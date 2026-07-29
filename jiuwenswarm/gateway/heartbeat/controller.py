# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""HeartbeatController — Web/RPC 与 Agent Tool 统一业务入口.

职责(方案 §9 / 接口设计 §3):
  - Web/RPC 业务 API: create/update/delete/toggle/list/get/preview/run_now/cancel。
  - Agent Tool (``heartbeat_*_job``): 语义化参数,自动继承当前 channel_id/session_id。
  - source 审计:controller 创建/更新时强制写入并校验 metadata.source 枚举。
  - 资源限制校验: min_interval / max_active_jobs_per_session / max_active_jobs_global。
  - 禁止字段拦截: mode/model/approval/sandbox/worktree 不可传入、不可修改。

Agent Tool 与 Web/RPC 的分工(方案 §9.3):
  - Agent Tool 自动继承当前 channel_id/session_id,默认仅当前 session 可见。
  - Web/RPC 可显式传 channel_id/session_id,可迁移会话;两者都不可改运行配置。

参考:``jiuwenswarm心跳任务重构方案设计.md`` §9、``接口设计方案.md`` §3。
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar, List

from openjiuwen.core.foundation.tool import LocalFunction, Tool, ToolCard

from jiuwenswarm.gateway.heartbeat.models import (
    DEFAULT_CONCURRENCY_POLICY,
    DEFAULT_MAX_RUNS,
    DEFAULT_SESSION_DELETED_POLICY,
    DEFAULT_TIMEZONE,
    HEARTBEAT_CONCURRENCY_POLICIES,
    HEARTBEAT_NAME_MAX_LENGTH,
    HEARTBEAT_PROMPT_MAX_LENGTH,
    HEARTBEAT_SCHEDULE_TYPES,
    HEARTBEAT_SESSION_DELETED_POLICIES,
    HEARTBEAT_SOURCES,
    HEARTBEAT_STATUSES,
    HeartbeatJob,
    HeartbeatSchedule,
    MIN_INTERVAL_SECONDS,
    SOURCE_AGENT_TOOL,
    SOURCE_SCHEDULE_RECOVERY,
    SOURCE_TUI_RPC,
    SOURCE_WEB_RPC,
    validate_metadata_source,
)
from jiuwenswarm.gateway.heartbeat.scheduler import HeartbeatSchedulerService
from jiuwenswarm.gateway.heartbeat.store import HeartbeatJobStore

logger = logging.getLogger(__name__)

# 禁止传入/修改的运行配置字段(方案铁律)。
_FORBIDDEN_FIELDS: frozenset[str] = frozenset(
    {"mode", "model", "model_name", "approval", "sandbox", "worktree", "work_mode"}
)

# 资源限制默认值(可被 config 覆盖;接口设计 §4.2)。
_DEFAULT_LIMITS: dict[str, Any] = {
    "min_interval_seconds": MIN_INTERVAL_SECONDS,
    "max_active_jobs_per_session": 5,
    "max_active_jobs_global": 100,
    "default_max_runs": DEFAULT_MAX_RUNS,
    "default_concurrency_policy": DEFAULT_CONCURRENCY_POLICY,
    "default_session_deleted_policy": DEFAULT_SESSION_DELETED_POLICY,
}


class HeartbeatController:
    """High-level heartbeat API used by WebChannel handlers + Agent tools. Singleton."""

    _instance: ClassVar[HeartbeatController | None] = None

    def __init__(
        self,
        *,
        store: HeartbeatJobStore,
        scheduler: HeartbeatSchedulerService,
        limits: dict[str, Any] | None = None,
    ) -> None:
        self._store = store
        self._scheduler = scheduler
        self._limits = {**_DEFAULT_LIMITS, **(limits or {})}

    @classmethod
    def get_instance(
        cls,
        *,
        store: HeartbeatJobStore | None = None,
        scheduler: HeartbeatSchedulerService | None = None,
        limits: dict[str, Any] | None = None,
    ) -> HeartbeatController:
        if cls._instance is not None:
            return cls._instance
        if store is None or scheduler is None:
            raise RuntimeError(
                "HeartbeatController not initialized. Call get_instance(store=..., scheduler=...) first."
            )
        cls._instance = cls(store=store, scheduler=scheduler, limits=limits)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        cls._instance = None

    def set_limits(self, limits: dict[str, Any]) -> None:
        self._limits = {**self._limits, **(limits or {})}

    @property
    def limits(self) -> dict[str, Any]:
        return dict(self._limits)

    # ---- 校验辅助 ----

    @staticmethod
    def _check_forbidden(params: dict[str, Any], *, where: str) -> None:
        """禁止字段拦截:mode/model/approval/sandbox/worktree 不可传入。"""
        bad = [k for k in params if k in _FORBIDDEN_FIELDS]
        if bad:
            raise ValueError(
                f"{where}: forbidden runtime config fields {bad}; "
                "heartbeat jobs must not change agent mode/model/approval/sandbox/worktree"
            )

    def _check_resource_limits(
        self, *, session_id: str, schedule: HeartbeatSchedule, exclude_job_id: str | None = None
    ) -> None:
        """资源限制校验(接口设计 §4.2)。"""
        min_iv = int(self._limits.get("min_interval_seconds", MIN_INTERVAL_SECONDS))
        if schedule.type == "interval":
            iv = schedule.interval_seconds or 0
            if iv < min_iv:
                raise ValueError(
                    f"interval_seconds must be at least {min_iv}"
                )
        max_per_session = int(self._limits.get("max_active_jobs_per_session", 5))
        cur_session = await_or_zero(self._store.count_active_jobs_for_session(session_id))
        # 若是更新已有 job,且该 job 仍活跃,不计入新增
        if exclude_job_id:
            existing = self._scheduler_sync_get(exclude_job_id)
            if existing is not None and existing.session_id == session_id and existing.status == "scheduled":
                cur_session = max(0, cur_session - 1)
        if cur_session >= max_per_session:
            raise ValueError(
                f"max_active_jobs_per_session ({max_per_session}) exceeded for session {session_id}"
            )
        max_global = int(self._limits.get("max_active_jobs_global", 100))
        cur_global = await_or_zero(self._store.count_active_jobs_global())
        if cur_global >= max_global:
            raise ValueError(f"max_active_jobs_global ({max_global}) exceeded")

    def _scheduler_sync_get(self, job_id: str) -> HeartbeatJob | None:
        """同步拿 job(controller 内不便 await,走 store 同步路径;实际用 get_job)。"""
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # 在运行的事件循环里:用 store 的同步读(不经锁,够用于只读校验)
                return None
        except RuntimeError:
            pass
        return None

    # ---- Web/RPC 业务 API ----

    async def list_jobs(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = dict(params or {})
        self._check_forbidden(params, where="list_jobs")
        jobs = await self._store.list_jobs()
        # 过滤
        session_id = str(params.get("session_id") or "").strip()
        channel_id = str(params.get("channel_id") or "").strip()
        status = str(params.get("status") or "").strip()
        scope = str(params.get("scope") or "current").strip()
        # Agent Tool 默认 current session;Web/RPC 可传 session_id 过滤。
        # 若未给 session_id 且 scope=all_visible,返回全部(需上层权限校验)。
        out = []
        for j in jobs:
            if session_id and j.session_id != session_id:
                continue
            if channel_id and j.channel_id != channel_id:
                continue
            if status and j.status != status:
                continue
            out.append(j.to_dict())
        return {"jobs": out}

    async def get_job(self, job_id: str) -> dict[str, Any] | None:
        job = await self._store.get_job(str(job_id or "").strip())
        return job.to_dict() if job is not None else None

    async def create_job(self, params: dict[str, Any]) -> dict[str, Any]:
        """Web/RPC create;Agent Tool 经 create_job_for_session 注入 channel_id/session_id。"""
        params = dict(params or {})
        self._check_forbidden(params, where="create_job")

        name = str(params.get("name") or "").strip()
        channel_id = str(params.get("channel_id") or "").strip()
        session_id = str(params.get("session_id") or "").strip()
        prompt = str(params.get("prompt") or "").strip()
        if not name:
            raise ValueError("name is required")
        if len(name) > HEARTBEAT_NAME_MAX_LENGTH:
            raise ValueError(f"name must be at most {HEARTBEAT_NAME_MAX_LENGTH} characters")
        if not channel_id:
            raise ValueError("channel_id is required")
        if not session_id:
            raise ValueError("session_id is required")
        if not prompt:
            raise ValueError("prompt is required")
        if len(prompt) > HEARTBEAT_PROMPT_MAX_LENGTH:
            raise ValueError(f"prompt must be at most {HEARTBEAT_PROMPT_MAX_LENGTH} characters")

        schedule = HeartbeatSchedule.from_dict(
            params.get("schedule") or {},
            default_timezone=str(params.get("timezone") or DEFAULT_TIMEZONE),
        )

        source = str(params.get("source") or SOURCE_WEB_RPC).strip()
        source = validate_metadata_source(source)  # controller 强制校验枚举

        max_runs_raw = params.get("max_runs", self._limits.get("default_max_runs", DEFAULT_MAX_RUNS))
        max_runs = None if max_runs_raw is None else int(max_runs_raw)

        concurrency_policy = str(
            params.get("concurrency_policy")
            or self._limits.get("default_concurrency_policy", DEFAULT_CONCURRENCY_POLICY)
        )
        session_deleted_policy = str(
            params.get("session_deleted_policy")
            or self._limits.get("default_session_deleted_policy", DEFAULT_SESSION_DELETED_POLICY)
        )

        # 资源限制
        self._check_resource_limits_sync(session_id=session_id, schedule=schedule)
        await self._check_resource_limits_async(
            session_id=session_id, schedule=schedule, exclude_job_id=None
        )

        job = await self._store.create_job(
            job_id=str(params.get("id") or "").strip() or None,
            name=name,
            channel_id=channel_id,
            session_id=session_id,
            prompt=prompt,
            schedule=schedule,
            timezone=str(params.get("timezone") or DEFAULT_TIMEZONE),
            enabled=bool(params.get("enabled", True)),
            concurrency_policy=concurrency_policy,
            session_deleted_policy=session_deleted_policy,
            max_runs=max_runs,
            delete_after_run=bool(params.get("delete_after_run", False)),
            source=source,
        )
        # 算 next_run_at 并回填
        import time as _time

        now = _time.time()
        next_run_at = self._scheduler._compute_next_run(job, now)
        if next_run_at is not None and job.enabled:
            job = await self._store.update_job(job.id, {"next_run_at": next_run_at})
        await self._scheduler.reload()
        return job.to_dict()

    async def create_job_for_session(
        self,
        params: dict[str, Any],
        *,
        channel_id: str,
        session_id: str,
    ) -> dict[str, Any]:
        """Agent Tool 入口:自动继承当前 channel_id/session_id。

        Agent Tool 不暴露 channel_id/session_id/mode 等参数;由调用层注入。
        metadata.source 默认 agent_tool。
        """
        params = dict(params or {})
        params.setdefault("channel_id", channel_id)
        params.setdefault("session_id", session_id)
        params.setdefault("source", SOURCE_AGENT_TOOL)
        return await self.create_job(params)

    async def update_job(self, job_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        patch = dict(patch or {})
        self._check_forbidden(patch, where="update_job")
        existing = await self._store.get_job(str(job_id or "").strip())
        if existing is None:
            raise KeyError("job not found")

        # schedule 变更要校验
        if "schedule" in patch:
            new_sched = HeartbeatSchedule.from_dict(
                patch["schedule"], default_timezone=existing.timezone
            )
            self._check_resource_limits_sync(
                session_id=existing.session_id, schedule=new_sched
            )

        # 若重新激活(enabled=true 且原终态),需重算 next_run_at
        now = __import__("time").time()
        if patch.get("enabled") is True and existing.status in {"completed", "expired", "disabled"}:
            # 先应用 patch 拿到新 schedule,再算 next_run_at
            merged_sched = HeartbeatSchedule.from_dict(
                patch.get("schedule", existing.schedule.to_dict()),
                default_timezone=str(patch.get("timezone") or existing.timezone),
            )
            next_run_at = self._scheduler._compute_next_run(
                __import__("dataclasses").replace(existing, schedule=merged_sched), now
            )
            patch.setdefault("next_run_at", next_run_at)

        updated = await self._store.update_job(str(job_id), patch)
        await self._scheduler.reload()
        return updated.to_dict()

    async def delete_job(self, job_id: str) -> dict[str, Any]:
        job_id = str(job_id or "").strip()
        # 若有活跃 run,先取消(ghost 清理)
        existing = await self._store.get_job(job_id)
        if existing is not None and existing.run_state.current_run_id:
            try:
                await self._scheduler.cancel_run(job_id, pause_schedule=False)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[HeartbeatController] cancel run on delete failed: %s", exc)
        deleted = await self._store.delete_job(job_id)
        await self._scheduler.reload()
        return {"deleted": bool(deleted)}

    async def toggle_job(self, job_id: str, enabled: bool) -> dict[str, Any]:
        return await self.update_job(job_id, {"enabled": bool(enabled)})

    async def preview_job(self, job_id: str, count: int = 5) -> dict[str, Any]:
        job = await self._store.get_job(str(job_id or "").strip())
        if job is None:
            raise KeyError("job not found")
        nxt = self._scheduler.preview_next_runs(job, count=count)
        return {"next": nxt}

    async def run_now(self, job_id: str, *, reschedule: bool = False) -> dict[str, Any]:
        result = await self._scheduler.trigger_run_now(
            str(job_id or "").strip(), reschedule=reschedule
        )
        return result

    async def cancel_run(self, job_id: str, *, pause_schedule: bool = False) -> dict[str, Any]:
        return await self._scheduler.cancel_run(
            str(job_id or "").strip(), pause_schedule=pause_schedule
        )

    # ---- 资源限制同步校验(min_interval 不需 await) ----

    def _check_resource_limits_sync(
        self, *, session_id: str, schedule: HeartbeatSchedule
    ) -> None:
        min_iv = int(self._limits.get("min_interval_seconds", MIN_INTERVAL_SECONDS))
        if schedule.type == "interval":
            iv = schedule.interval_seconds or 0
            if iv < min_iv:
                raise ValueError(f"interval_seconds must be at least {min_iv}")

    async def _check_resource_limits_async(
        self, *, session_id: str, schedule: HeartbeatSchedule, exclude_job_id: str | None = None
    ) -> None:
        min_iv = int(self._limits.get("min_interval_seconds", MIN_INTERVAL_SECONDS))
        if schedule.type == "interval" and (schedule.interval_seconds or 0) < min_iv:
            raise ValueError(f"interval_seconds must be at least {min_iv}")
        max_per_session = int(self._limits.get("max_active_jobs_per_session", 5))
        cur_session = await self._store.count_active_jobs_for_session(session_id)
        if exclude_job_id:
            existing = await self._store.get_job(exclude_job_id)
            if (
                existing is not None
                and existing.session_id == session_id
                and existing.status == "scheduled"
            ):
                cur_session = max(0, cur_session - 1)
        if cur_session >= max_per_session:
            raise ValueError(
                f"max_active_jobs_per_session ({max_per_session}) exceeded"
            )
        max_global = int(self._limits.get("max_active_jobs_global", 100))
        cur_global = await self._store.count_active_jobs_global()
        if cur_global >= max_global:
            raise ValueError(f"max_active_jobs_global ({max_global}) exceeded")

    # ---- meta(接口设计 §2.9) ----

    def get_meta(self) -> dict[str, Any]:
        return {
            "limits": dict(self._limits),
            "schedule_types": list(HEARTBEAT_SCHEDULE_TYPES),
            "concurrency_policies": list(HEARTBEAT_CONCURRENCY_POLICIES),
            "session_deleted_policies": list(HEARTBEAT_SESSION_DELETED_POLICIES),
            "statuses": list(HEARTBEAT_STATUSES),
            "sources": list(HEARTBEAT_SOURCES),
        }

    # ---- Agent Tool (方案 §9.2 / 接口设计 §3) ----

    def get_tools(self) -> List[Tool]:
        """返回 heartbeat_*_job 工具组,供 openJiuwen Runner 注册。

        工具说明含结构化决策树(何时用 heartbeat vs cron)与停止义务。
        """

        def make_tool(
            name: str,
            description: str,
            input_params: dict,
            func,
        ) -> Tool:
            card = ToolCard(
                name=name,
                description=description,
                input_params=input_params,
            )
            return LocalFunction(card=card, func=func)

        tools: list[Tool] = []

        tools.append(
            make_tool(
                name="heartbeat_list_jobs",
                description=(
                    "List heartbeat follow-up jobs. By default returns only jobs "
                    "bound to the current session. A heartbeat job is one that makes "
                    "the assistant return to the current conversation later/repeatedly "
                    "to continue an existing task. For standalone scheduled tasks "
                    "(daily reports, periodic notifications) use cron_list_jobs instead."
                ),
                input_params={
                    "type": "object",
                    "properties": {
                        "scope": {
                            "type": "string",
                            "enum": ["current", "all_visible"],
                            "description": "current: only current session (default). all_visible: needs permission.",
                        }
                    },
                },
                func=self._tool_list_jobs,
            )
        )

        tools.append(
            make_tool(
                name="heartbeat_get_job",
                description="Get a single heartbeat job by id.",
                input_params={
                    "type": "object",
                    "properties": {"job_id": {"type": "string"}},
                    "required": ["job_id"],
                },
                func=self._tool_get_job,
            )
        )

        tools.append(
            make_tool(
                name="heartbeat_create_job",
                description=_CREATE_JOB_DESCRIPTION,
                input_params=_CREATE_JOB_SCHEMA,
                func=self._tool_create_job,
            )
        )

        tools.append(
            make_tool(
                name="heartbeat_update_job",
                description=(
                    "Update a heartbeat job. Only patches provided fields. "
                    "session_id/channel_id cannot be changed via this tool; "
                    "mode/model/approval/sandbox/worktree are never patchable. "
                    "Set enabled=false (or heartbeat_cancel_run pause_schedule=true) "
                    "to actually stop a completed task."
                ),
                input_params={
                    "type": "object",
                    "properties": {
                        "job_id": {"type": "string"},
                        "patch": {
                            "type": "object",
                            "properties": {
                                "prompt": {"type": "string"},
                                "schedule": _SCHEDULE_SCHEMA,
                                "max_runs": {"type": "integer"},
                                "concurrency_policy": {
                                    "type": "string",
                                    "enum": list(HEARTBEAT_CONCURRENCY_POLICIES),
                                },
                                "enabled": {"type": "boolean"},
                                "delete_after_run": {"type": "boolean"},
                            },
                        },
                    },
                    "required": ["job_id", "patch"],
                },
                func=self._tool_update_job,
            )
        )

        tools.append(
            make_tool(
                name="heartbeat_delete_job",
                description="Delete a heartbeat job permanently.",
                input_params={
                    "type": "object",
                    "properties": {"job_id": {"type": "string"}},
                    "required": ["job_id"],
                },
                func=self._tool_delete_job,
            )
        )

        tools.append(
            make_tool(
                name="heartbeat_toggle_job",
                description="Enable or disable a heartbeat job. Disabling keeps the record; re-enabling reactivates.",
                input_params={
                    "type": "object",
                    "properties": {
                        "job_id": {"type": "string"},
                        "enabled": {"type": "boolean"},
                    },
                    "required": ["job_id", "enabled"],
                },
                func=self._tool_toggle_job,
            )
        )

        tools.append(
            make_tool(
                name="heartbeat_preview_job",
                description="Preview upcoming run times for a heartbeat job.",
                input_params={
                    "type": "object",
                    "properties": {
                        "job_id": {"type": "string"},
                        "count": {"type": "integer", "default": 5},
                    },
                    "required": ["job_id"],
                },
                func=self._tool_preview_job,
            )
        )

        tools.append(
            make_tool(
                name="heartbeat_run_now",
                description="Trigger one immediate heartbeat run to the bound session.",
                input_params={
                    "type": "object",
                    "properties": {
                        "job_id": {"type": "string"},
                        "reschedule": {"type": "boolean", "default": False},
                    },
                    "required": ["job_id"],
                },
                func=self._tool_run_now,
            )
        )

        tools.append(
            make_tool(
                name="heartbeat_cancel_run",
                description=(
                    "Cancel the currently running heartbeat trigger. "
                    "Use pause_schedule=true to also stop future scheduling "
                    "when the task is complete."
                ),
                input_params={
                    "type": "object",
                    "properties": {
                        "job_id": {"type": "string"},
                        "pause_schedule": {"type": "boolean", "default": False},
                    },
                    "required": ["job_id"],
                },
                func=self._tool_cancel_run,
            )
        )

        return tools

    # ---- Tool 回调(由 Agent Runner 注入 channel_id/session_id 上下文) ----
    # 当前 session 的 channel_id/session_id 通过 _current_session_ctx 读取;
    # 该上下文由 app_gateway / AgentServer 在工具执行时注入。

    _session_ctx: ClassVar[dict[str, str]] = {}

    @classmethod
    def set_session_ctx(cls, *, channel_id: str, session_id: str) -> None:
        cls._session_ctx = {"channel_id": channel_id, "session_id": session_id}

    @classmethod
    def get_session_ctx(cls) -> dict[str, str]:
        return dict(cls._session_ctx)

    async def _tool_list_jobs(self, **kwargs: Any) -> dict[str, Any]:
        return await self.list_jobs(kwargs)

    async def _tool_get_job(self, job_id: str, **kwargs: Any) -> dict[str, Any]:
        job = await self.get_job(job_id)
        return job or {"error": "not found"}

    async def _tool_create_job(self, **kwargs: Any) -> dict[str, Any]:
        ctx = self.get_session_ctx()
        if not ctx.get("session_id"):
            raise ValueError(
                "no current session; heartbeat_create_job must bind to a session"
            )
        return await self.create_job_for_session(
            kwargs, channel_id=ctx.get("channel_id", "web"), session_id=ctx["session_id"]
        )

    async def _tool_update_job(self, job_id: str, patch: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        return await self.update_job(job_id, patch)

    async def _tool_delete_job(self, job_id: str, **kwargs: Any) -> dict[str, Any]:
        return await self.delete_job(job_id)

    async def _tool_toggle_job(self, job_id: str, enabled: bool, **kwargs: Any) -> dict[str, Any]:
        return await self.toggle_job(job_id, enabled)

    async def _tool_preview_job(self, job_id: str, count: int = 5, **kwargs: Any) -> dict[str, Any]:
        return await self.preview_job(job_id, count=count)

    async def _tool_run_now(self, job_id: str, reschedule: bool = False, **kwargs: Any) -> dict[str, Any]:
        return await self.run_now(job_id, reschedule=reschedule)

    async def _tool_cancel_run(self, job_id: str, pause_schedule: bool = False, **kwargs: Any) -> dict[str, Any]:
        return await self.cancel_run(job_id, pause_schedule=pause_schedule)


# ---------------------------------------------------------------------------
# Tool schema 常量(对齐方案 §9.2 / 接口设计 §3.2)
# ---------------------------------------------------------------------------

_SCHEDULE_SCHEMA: dict = {
    "type": "object",
    "description": "When to return to the current session: interval/cron/once.",
    "properties": {
        "type": {"type": "string", "enum": list(HEARTBEAT_SCHEDULE_TYPES)},
        "interval_seconds": {"type": "integer", "description": "interval mode, min 60"},
        "cron_expr": {"type": "string", "description": "cron mode, 5 fields"},
        "timezone": {"type": "string", "description": "cron mode, default Asia/Shanghai"},
        "run_at": {"type": "number", "description": "once mode, Unix timestamp"},
    },
    "required": ["type"],
}

_CREATE_JOB_SCHEMA: dict = {
    "type": "object",
    "description": (
        "Create a heartbeat follow-up job bound to the current conversation/session. "
        "Use this only when the user wants the assistant to return to the current "
        "session later or repeatedly and continue the existing task with the existing "
        "conversation context and original session configuration. A heartbeat job must "
        "not change agent mode, model, approval policy, sandbox, worktree, or other "
        "session runtime settings; future runs execute directly in the original session. "
        "Do not use this for standalone scheduled tasks such as daily reports, periodic "
        "channel notifications, or jobs that should start independently from a saved "
        "prompt; use cron_create_job for those. If a future heartbeat run determines the "
        "task is complete, the assistant must actually stop the schedule by calling "
        "heartbeat_update_job(enabled=false) or heartbeat_cancel_run(pause_schedule=true), "
        "not merely state that it has stopped."
    ),
    "properties": {
        "name": {"type": "string", "description": "Heartbeat job name describing what to follow up in current session."},
        "prompt": {
            "type": "string",
            "description": (
                "Follow-up prompt delivered to the current session each run. Must include "
                "stop obligation: when the task is complete, call heartbeat_update_job"
                "(enabled=false) or heartbeat_cancel_run(pause_schedule=true)."
            ),
        },
        "schedule": _SCHEDULE_SCHEMA,
        "max_runs": {"type": "integer", "description": "Max trigger count; omit for system default."},
        "delete_after_run": {"type": "boolean", "description": "Auto-complete after one run."},
        "concurrency_policy": {
            "type": "string",
            "enum": list(HEARTBEAT_CONCURRENCY_POLICIES),
        },
        "enabled": {"type": "boolean", "default": True},
    },
    "required": ["name", "prompt", "schedule"],
}

_CREATE_JOB_DESCRIPTION: str = _CREATE_JOB_SCHEMA["description"]


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def await_or_zero(coro) -> int:
    """同步上下文里安全读取 count(返回 0);实际异步路径走 _check_resource_limits_async。"""
    # controller 的 create_job 是 async,本不应走到这;留作兜底,返回 0 表示无法判定。
    return 0
