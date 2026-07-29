# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""HeartbeatJobStore — 读写 heartbeat_jobs.json.

并发安全模式镜像 ``CronJobStore``:
  - ``asyncio.Lock``：同进程协程互斥；
  - ``portalocker`` 伴生 ``heartbeat_jobs.json.lock``：跨进程互斥。
  整个 read-modify-write 在双层锁内完成,避免 lost update。

store 层负责同步维护 ``status / enabled / next_run_at`` 三者状态机不变量
(方案 §2.5 / 接口设计 §1.3),禁止只改其中一个字段。

参考:``jiuwenswarm心跳任务重构方案设计.md`` §7.9、``接口设计方案.md`` §1.4。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any, TypeVar

import portalocker

from jiuwenswarm.common.utils import get_heartbeat_jobs_path
from jiuwenswarm.gateway.heartbeat.models import (
    DEFAULT_CONCURRENCY_POLICY,
    DEFAULT_MAX_RUNS,
    DEFAULT_SESSION_DELETED_POLICY,
    DEFAULT_TIMEZONE,
    HEARTBEAT_JOBS_VERSION,
    HEARTBEAT_TERMINAL_STATUSES,
    HeartbeatJob,
    HeartbeatRunState,
    HeartbeatSchedule,
    RUN_CANCELLED,
    RUN_FAILED,
    RUN_SKIPPED,
    RUN_SUCCEEDED,
    SOURCE_AGENT_TOOL,
    SOURCE_SCHEDULE_RECOVERY,
    STATUS_COMPLETED,
    STATUS_DISABLED,
    STATUS_EXPIRED,
    STATUS_RUNNING,
    STATUS_SCHEDULED,
    empty_heartbeat_jobs_doc,
    validate_metadata_source,
)

logger = logging.getLogger(__name__)

_T = TypeVar("_T")
_FILE_LOCK_TIMEOUT_SEC = 10.0


class HeartbeatJobStore:
    """Persist heartbeat jobs to ``heartbeat_jobs.json``."""

    def __init__(
        self,
        path: Path | None = None,
        *,
        file_lock_timeout: float = _FILE_LOCK_TIMEOUT_SEC,
    ) -> None:
        self._path = path or get_heartbeat_jobs_path()
        self._lock = asyncio.Lock()
        self._file_lock_timeout = float(file_lock_timeout)

    @property
    def path(self) -> Path:
        return self._path

    # ---- 双层锁(同进程 + 跨进程),镜像 CronJobStore ----

    def _call_under_file_lock(self, fn: Callable[[], _T]) -> _T:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self._path.with_suffix(self._path.suffix + ".lock")
        with portalocker.Lock(str(lock_path), timeout=self._file_lock_timeout):
            return fn()

    async def _run_locked(self, fn: Callable[[], _T]) -> _T:
        async with self._lock:
            return await asyncio.to_thread(self._call_under_file_lock, fn)

    # ---- 原子读写 ----

    def _read_json_unlocked(self) -> dict[str, Any]:
        path = self._path
        try:
            if not path.exists():
                return empty_heartbeat_jobs_doc()
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw) if raw.strip() else {}
            if not isinstance(data, dict):
                return empty_heartbeat_jobs_doc()
            if "version" not in data:
                data["version"] = HEARTBEAT_JOBS_VERSION
            if "jobs" not in data or not isinstance(data["jobs"], list):
                data["jobs"] = []
            return data
        except Exception:
            return empty_heartbeat_jobs_doc()

    def _write_json_unlocked(self, data: dict[str, Any]) -> None:
        path = self._path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        data["version"] = int(data.get("version") or HEARTBEAT_JOBS_VERSION)
        payload = json.dumps(data, ensure_ascii=False, indent=2)
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(path)

    # ---- 基础 CRUD ----

    async def list_jobs(self) -> list[HeartbeatJob]:
        def _body() -> list[HeartbeatJob]:
            data = self._read_json_unlocked()
            jobs_raw = data.get("jobs") or []
            if not isinstance(jobs_raw, list):
                return []
            jobs: list[HeartbeatJob] = []
            for item in jobs_raw:
                if not isinstance(item, dict):
                    continue
                try:
                    jobs.append(HeartbeatJob.from_dict(item))
                except Exception:
                    logger.warning(
                        "[HeartbeatStore] skip invalid job entry: %s",
                        item.get("id") if isinstance(item, dict) else item,
                    )
                    continue
            return jobs

        jobs = await self._run_locked(_body)
        jobs.sort(
            key=lambda j: (j.updated_at or 0.0, j.created_at or 0.0),
            reverse=True,
        )
        return jobs

    async def get_job(self, job_id: str) -> HeartbeatJob | None:
        job_id = str(job_id or "").strip()
        if not job_id:
            return None
        for job in await self.list_jobs():
            if job.id == job_id:
                return job
        return None

    async def _upsert_job(self, job: HeartbeatJob) -> None:
        def _body() -> None:
            data = self._read_json_unlocked()
            jobs_raw = data.get("jobs") or []
            if not isinstance(jobs_raw, list):
                jobs_raw = []
            out: list[dict[str, Any]] = []
            found = False
            for item in jobs_raw:
                if not isinstance(item, dict):
                    continue
                if str(item.get("id") or "").strip() == job.id:
                    out.append(job.to_dict())
                    found = True
                else:
                    out.append(item)
            if not found:
                out.append(job.to_dict())
            data["version"] = int(data.get("version") or HEARTBEAT_JOBS_VERSION)
            data["jobs"] = out
            self._write_json_unlocked(data)

        await self._run_locked(_body)

    async def create_job(
        self,
        *,
        job_id: str | None = None,
        name: str,
        channel_id: str,
        session_id: str,
        prompt: str,
        schedule: HeartbeatSchedule,
        timezone: str = DEFAULT_TIMEZONE,
        enabled: bool = True,
        concurrency_policy: str = DEFAULT_CONCURRENCY_POLICY,
        session_deleted_policy: str = DEFAULT_SESSION_DELETED_POLICY,
        max_runs: int | None = DEFAULT_MAX_RUNS,
        delete_after_run: bool = False,
        source: str = SOURCE_AGENT_TOOL,
        metadata: dict[str, Any] | None = None,
        now: float | None = None,
    ) -> HeartbeatJob:
        """创建心跳任务。

        controller 负责按入口强制写入合法 ``source``(枚举校验);store 层兜底校验。
        """
        ts = float(now) if now is not None else time.time()
        # source 校验:controller 应已校验,此处再校一遍防绕过。
        src = validate_metadata_source(source)

        meta = dict(metadata or {})
        meta["source"] = src

        job_id_clean = str(job_id or "").strip() or f"{_id_prefix()}{uuid.uuid4().hex}"

        # enabled=false 创建 → 直接 disabled 终态;否则 scheduled。
        if enabled:
            status = STATUS_SCHEDULED
        else:
            status = STATUS_DISABLED

        job = HeartbeatJob(
            id=job_id_clean,
            name=str(name or "").strip(),
            enabled=bool(enabled),
            channel_id=str(channel_id or "").strip(),
            session_id=str(session_id or "").strip(),
            prompt=str(prompt or "").strip(),
            schedule=schedule,
            timezone=str(timezone or DEFAULT_TIMEZONE).strip() or DEFAULT_TIMEZONE,
            status=status,
            concurrency_policy=str(concurrency_policy or DEFAULT_CONCURRENCY_POLICY),
            session_deleted_policy=str(
                session_deleted_policy or DEFAULT_SESSION_DELETED_POLICY
            ),
            max_runs=max_runs,
            delete_after_run=bool(delete_after_run),
            created_at=ts,
            updated_at=ts,
            next_run_at=None,  # 由 controller/scheduler 调 _compute_next_run 后回填
            last_run_at=None,
            run_count=0,
            metadata=meta,
            run_state=HeartbeatRunState(),
        )
        # 不变量:disabled 终态要求 next_run_at=None(已置);scheduled 允许 next_run_at
        # 暂时为 None(刚创建,尚未算 next run)。create 不在此处计算 next_run_at,
        # 由 controller 调用 scheduler 的 _compute_next_run 回填,避免 store 依赖调度器。
        job.check_invariants()
        await self._upsert_job(job)
        return job

    async def update_job(self, job_id: str, patch: dict[str, Any]) -> HeartbeatJob:
        """patch-only 更新;维护状态机不变量。

        关键规则(方案 §9.1 update / 接口设计 §2.4):
          - 修改 schedule 后重算 next_run_at(由 controller 注入,store 接受 next_run_at 字段)。
          - patch.enabled=false → status=disabled, next_run_at=None。
          - patch.enabled=true 且原为终态 → 重新激活: status=scheduled, next_run_at 由调用方重算注入。
          - 禁止 patch mode/model/approval/sandbox/worktree(controller 层已拦,store 层忽略这些键)。
        """
        job_id = str(job_id or "").strip()
        if not job_id:
            raise ValueError("id is required")
        patch = dict(patch or {})
        existing = await self.get_job(job_id)
        if existing is None:
            raise KeyError("job not found")

        updated = existing

        if "name" in patch:
            updated = replace(updated, name=str(patch.get("name") or "").strip())
        if "prompt" in patch:
            updated = replace(updated, prompt=str(patch.get("prompt") or "").strip())
        if "channel_id" in patch:
            updated = replace(
                updated, channel_id=str(patch.get("channel_id") or "").strip()
            )
        if "session_id" in patch:
            updated = replace(
                updated, session_id=str(patch.get("session_id") or "").strip()
            )
        if "timezone" in patch:
            # 顶层时区变更:仅做规范化,不影响 schedule 内已存时区
            from jiuwenswarm.gateway.heartbeat.models import _validate_timezone

            updated = replace(
                updated,
                timezone=_validate_timezone(str(patch.get("timezone") or "").strip()),
            )
        if "concurrency_policy" in patch:
            from jiuwenswarm.gateway.heartbeat.models import HEARTBEAT_CONCURRENCY_POLICIES

            cp = str(patch.get("concurrency_policy") or DEFAULT_CONCURRENCY_POLICY).strip()
            if cp not in HEARTBEAT_CONCURRENCY_POLICIES:
                raise ValueError(f"invalid concurrency_policy {cp!r}")
            updated = replace(updated, concurrency_policy=cp)
        if "session_deleted_policy" in patch:
            from jiuwenswarm.gateway.heartbeat.models import (
                HEARTBEAT_SESSION_DELETED_POLICIES,
            )

            sdp = str(
                patch.get("session_deleted_policy") or DEFAULT_SESSION_DELETED_POLICY
            ).strip()
            if sdp not in HEARTBEAT_SESSION_DELETED_POLICIES:
                raise ValueError(f"invalid session_deleted_policy {sdp!r}")
            updated = replace(updated, session_deleted_policy=sdp)
        if "max_runs" in patch:
            raw_mr = patch.get("max_runs")
            if raw_mr is None:
                updated = replace(updated, max_runs=None)
            else:
                try:
                    mr = int(raw_mr)
                except Exception as exc:  # noqa: BLE001
                    raise ValueError("max_runs must be int or null") from exc
                if mr < 1:
                    raise ValueError("max_runs must be at least 1")
                updated = replace(updated, max_runs=mr)
        if "delete_after_run" in patch:
            updated = replace(updated, delete_after_run=bool(patch.get("delete_after_run")))
        if "schedule" in patch:
            new_schedule = HeartbeatSchedule.from_dict(
                patch.get("schedule") or {},
                default_timezone=updated.timezone,
            )
            updated = replace(updated, schedule=new_schedule)
        if "metadata" in patch:
            meta_patch = patch.get("metadata")
            if isinstance(meta_patch, dict):
                merged = dict(updated.metadata or {})
                merged.update(meta_patch)
                # 若 patch 含 source,校验枚举
                if "source" in merged:
                    merged["source"] = validate_metadata_source(merged.get("source"))
                updated = replace(updated, metadata=merged)

        # ---- enabled / status / next_run_at 联动 ----
        if "enabled" in patch:
            enabled_val = bool(patch.get("enabled"))
            updated = replace(updated, enabled=enabled_val)
            if not enabled_val:
                # 停用 → disabled 终态,next_run_at 清空。
                updated = replace(updated, status=STATUS_DISABLED, next_run_at=None)
            else:
                # 重新激活:原终态 → scheduled;原 scheduled 保持。
                if existing.status in HEARTBEAT_TERMINAL_STATUSES:
                    updated = replace(updated, status=STATUS_SCHEDULED)
                # next_run_at 是否重算由调用方通过单独的 reschedule 注入;
                # 若 patch 显式带了 next_run_at,尊重它。
                if "next_run_at" not in patch:
                    # 重新激活但未给 next_run_at → 暂置 None,scheduler 下个 tick 补算。
                    updated = replace(updated, next_run_at=None)

        if "next_run_at" in patch:
            nra_raw = patch.get("next_run_at")
            updated = replace(
                updated,
                next_run_at=float(nra_raw) if isinstance(nra_raw, (int, float)) else None,
            )

        updated.updated_at = time.time()
        # 落库前校验不变量。
        updated.check_invariants()
        await self._upsert_job(updated)
        return updated

    async def delete_job(self, job_id: str) -> bool:
        """物理删除 job 记录。与终态的「保留记录」不同:delete 是真删除。"""
        job_id = str(job_id or "").strip()
        if not job_id:
            return False

        def _body() -> bool:
            data = self._read_json_unlocked()
            jobs_raw = data.get("jobs") or []
            if not isinstance(jobs_raw, list):
                jobs_raw = []
            kept: list[dict[str, Any]] = []
            deleted = False
            for item in jobs_raw:
                if not isinstance(item, dict):
                    continue
                if str(item.get("id") or "").strip() == job_id:
                    deleted = True
                    continue
                kept.append(item)
            data["version"] = int(data.get("version") or HEARTBEAT_JOBS_VERSION)
            data["jobs"] = kept
            if deleted:
                self._write_json_unlocked(data)
            return deleted

        return await self._run_locked(_body)

    async def toggle_job(self, job_id: str, enabled: bool) -> HeartbeatJob:
        """启停;联动 status / next_run_at(语义同 update enabled)。"""
        return await self.update_job(job_id, {"enabled": bool(enabled)})

    # ---- 状态机写方法(方案 §7.9 / 接口设计 §1.4) ----

    async def mark_running(self, job_id: str, run_id: str, now: float) -> HeartbeatJob:
        job = await self.get_job(job_id)
        if job is None:
            raise KeyError("job not found")
        job = replace(
            job,
            status=STATUS_RUNNING,
            enabled=True,
            run_state=replace(
                job.run_state,
                current_run_id=run_id,
                current_run_started_at=float(now),
            ),
            updated_at=float(now),
        )
        job.check_invariants()
        await self._upsert_job(job)
        return job

    async def mark_succeeded(
        self, job_id: str, run_id: str, now: float
    ) -> HeartbeatJob:
        job = await self.get_job(job_id)
        if job is None:
            raise KeyError("job not found")
        job = replace(
            job,
            status=STATUS_SCHEDULED,
            enabled=True,
            last_run_at=float(now),
            run_count=int(job.run_count) + 1,
            run_state=replace(
                job.run_state,
                current_run_id=None,
                current_run_started_at=None,
                last_run_status=RUN_SUCCEEDED,
                last_error=None,
            ),
            updated_at=float(now),
        )
        job.check_invariants()
        await self._upsert_job(job)
        return job

    async def mark_failed(
        self, job_id: str, run_id: str, now: float, error: str
    ) -> HeartbeatJob:
        job = await self.get_job(job_id)
        if job is None:
            raise KeyError("job not found")
        job = replace(
            job,
            status=STATUS_SCHEDULED,
            enabled=True,
            last_run_at=float(now),
            run_count=int(job.run_count) + 1,
            run_state=replace(
                job.run_state,
                current_run_id=None,
                current_run_started_at=None,
                last_run_status=RUN_FAILED,
                last_error=str(error)[:1000],
            ),
            updated_at=float(now),
        )
        job.check_invariants()
        await self._upsert_job(job)
        return job

    async def record_skipped(
        self, job_id: str, now: float, reason: str
    ) -> HeartbeatJob:
        job = await self.get_job(job_id)
        if job is None:
            raise KeyError("job not found")
        job = replace(
            job,
            last_run_at=float(now),
            run_state=replace(
                job.run_state,
                last_run_status=RUN_SKIPPED,
                last_error=str(reason)[:1000],
                skipped_count=int(job.run_state.skipped_count) + 1,
            ),
            updated_at=float(now),
        )
        await self._upsert_job(job)
        return job

    async def mark_cancelled(
        self, job_id: str, run_id: str | None, now: float
    ) -> HeartbeatJob:
        job = await self.get_job(job_id)
        if job is None:
            raise KeyError("job not found")
        job = replace(
            job,
            status=STATUS_SCHEDULED,
            run_state=replace(
                job.run_state,
                current_run_id=None,
                current_run_started_at=None,
                last_run_status=RUN_CANCELLED,
            ),
            updated_at=float(now),
        )
        job.check_invariants()
        await self._upsert_job(job)
        return job

    async def mark_queued(self, job_id: str, run_id: str) -> HeartbeatJob:
        job = await self.get_job(job_id)
        if job is None:
            raise KeyError("job not found")
        job = replace(
            job,
            run_state=replace(job.run_state, queued_run_id=run_id),
            updated_at=time.time(),
        )
        await self._upsert_job(job)
        return job

    async def clear_queued(self, job_id: str) -> HeartbeatJob:
        job = await self.get_job(job_id)
        if job is None:
            raise KeyError("job not found")
        job = replace(
            job,
            run_state=replace(job.run_state, queued_run_id=None),
            updated_at=time.time(),
        )
        await self._upsert_job(job)
        return job

    async def reschedule(self, job_id: str, next_run_at: float | None) -> HeartbeatJob:
        """更新下次触发时间。next_run_at=None 时若处于终态则保持,否则置 scheduled。"""
        job = await self.get_job(job_id)
        if job is None:
            raise KeyError("job not found")
        if job.status in HEARTBEAT_TERMINAL_STATUSES:
            # 终态不接受 reschedule;保持不变。
            return job
        job = replace(job, next_run_at=next_run_at, updated_at=time.time())
        job.check_invariants()
        await self._upsert_job(job)
        return job

    async def mark_completed(
        self,
        job_id: str,
        run_id: str | None,
        now: float,
        reason: str = "stop_condition_reached",
    ) -> HeartbeatJob:
        """once / delete_after_run / max_runs 达成 → 标记 completed,保留记录,停用。"""
        job = await self.get_job(job_id)
        if job is None:
            raise KeyError("job not found")
        run_count = int(job.run_count) + 1
        job = replace(
            job,
            status=STATUS_COMPLETED,
            enabled=False,
            next_run_at=None,
            last_run_at=float(now),
            run_count=run_count,
            run_state=replace(
                job.run_state,
                current_run_id=None,
                current_run_started_at=None,
                last_run_status=RUN_SUCCEEDED,
                last_error=None,
                queued_run_id=None,
            ),
            updated_at=float(now),
        )
        job.check_invariants()
        await self._upsert_job(job)
        return job

    async def mark_expired(self, job_id: str, now: float) -> HeartbeatJob:
        job = await self.get_job(job_id)
        if job is None:
            raise KeyError("job not found")
        job = replace(
            job,
            status=STATUS_EXPIRED,
            enabled=False,
            next_run_at=None,
            updated_at=float(now),
        )
        job.check_invariants()
        await self._upsert_job(job)
        return job

    async def disable(self, job_id: str, now: float) -> HeartbeatJob:
        """session_deleted_policy=disable 或手动停用。"""
        job = await self.get_job(job_id)
        if job is None:
            raise KeyError("job not found")
        job = replace(
            job,
            status=STATUS_DISABLED,
            enabled=False,
            next_run_at=None,
            updated_at=float(now),
        )
        job.check_invariants()
        await self._upsert_job(job)
        return job

    async def complete_for_session_deleted(self, job_id: str, now: float) -> HeartbeatJob:
        """session_deleted_policy=completed 时调用。"""
        job = await self.get_job(job_id)
        if job is None:
            raise KeyError("job not found")
        job = replace(
            job,
            status=STATUS_COMPLETED,
            enabled=False,
            next_run_at=None,
            run_state=replace(
                job.run_state,
                current_run_id=None,
                current_run_started_at=None,
                last_run_status=RUN_CANCELLED,
                last_error="session_deleted",
                queued_run_id=None,
            ),
            updated_at=float(now),
        )
        job.check_invariants()
        await self._upsert_job(job)
        return job

    # ---- 查询辅助 ----

    async def get_active_run(self, job_id: str) -> str | None:
        """返回当前运行中的 run_id(若有),否则 None。"""
        job = await self.get_job(job_id)
        if job is None:
            return None
        return job.run_state.current_run_id

    async def list_jobs_by_session(self, session_id: str) -> list[HeartbeatJob]:
        session_id = str(session_id or "").strip()
        if not session_id:
            return []
        return [j for j in await self.list_jobs() if j.session_id == session_id]

    async def count_active_jobs_for_session(self, session_id: str) -> int:
        session_id = str(session_id or "").strip()
        if not session_id:
            return 0
        return sum(
            1
            for j in await self.list_jobs()
            if j.session_id == session_id and j.status == STATUS_SCHEDULED and j.enabled
        )

    async def count_active_jobs_global(self) -> int:
        return sum(
            1
            for j in await self.list_jobs()
            if j.status == STATUS_SCHEDULED and j.enabled
        )

    # ---- 运行状态查询(source 兜底) ----

    async def reload_mtime(self) -> float:
        """供 scheduler 做 mtime 变化检测(与 Cron 一致)。"""
        try:
            return self._path.stat().st_mtime
        except Exception:
            return 0.0


def _id_prefix() -> str:
    from jiuwenswarm.gateway.heartbeat.models import HEARTBEAT_ID_PREFIX

    return HEARTBEAT_ID_PREFIX
