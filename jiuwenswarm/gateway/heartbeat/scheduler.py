# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""HeartbeatSchedulerService — 心跳任务后台调度.

职责(方案 §7.3):
  - ``_loop`` 周期扫描 due jobs,调用 ``_tick_once``。
  - ``_handle_due_job``:session 校验 + 并发策略 + dispatch。
  - ``_dispatch_job``:构造 ``CHAT_SEND`` 投递回原 session,带 automation metadata。
  - ``_finish_run``:更新 last_run_at/run_count/next_run_at;达成停止条件则 completed。
  - ``_compute_next_run``:interval/cron/once 下一次触发。
  - ``_apply_concurrency_policy``:skip/queue/replace。
  - ``_handle_missing_session``:session 删除/不可恢复按 session_deleted_policy 处理。
  - ``reload``/``_check_store_changed``:外部编辑 heartbeat_jobs.json 后刷新。
  - ghost task 清理:job 被删除后取消当前 run。

关键约束(方案 §6):
  - 不创建 heartbeat_* 临时会话;不读 HEARTBEAT.md;不用 __heartbeat__ channel。
  - CHAT_SEND 投递回原 session_id;不传 params.mode(web/tui 由原 session 配置决定)。
  - 不补跑系统离线期间错过的历史触发(next_run_at 远早于 now 时基于 now 重算)。
  - 自动触发消息必须带 metadata.automation。

参考:``jiuwenswarm心跳任务重构方案设计.md`` §6/§7、``接口设计方案.md`` §5。
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from jiuwenswarm.gateway.heartbeat.models import (
    HEARTBEAT_TERMINAL_STATUSES,
    HeartbeatJob,
    RUN_FAILED,
    SOURCE_AGENT_TOOL,
    SOURCE_SCHEDULE_RECOVERY,
    SCHEDULE_CRON,
    SCHEDULE_INTERVAL,
    SCHEDULE_ONCE,
    SESSION_DELETED_COMPLETED,
    SESSION_DELETED_DISABLE,
    STATUS_COMPLETED,
    STATUS_SCHEDULED,
    HeartbeatRunState,
)
from jiuwenswarm.gateway.heartbeat.session_resolver import HeartbeatSessionResolver

if TYPE_CHECKING:
    from jiuwenswarm.gateway.message_handler import MessageHandler
    from jiuwenswarm.gateway.heartbeat.store import HeartbeatJobStore

logger = logging.getLogger(__name__)


def _now_ts() -> float:
    return time.time()


class HeartbeatSchedulerService:
    """后台调度心跳任务,到点投递 follow-up prompt 回原 session。"""

    def __init__(
        self,
        *,
        store: "HeartbeatJobStore",
        message_handler: "MessageHandler",
        session_resolver: HeartbeatSessionResolver | None = None,
        now_fn: Callable[[], float] = _now_ts,
        poll_interval_seconds: float = 5.0,
    ) -> None:
        self._store = store
        self._message_handler = message_handler
        self._session_resolver = session_resolver or HeartbeatSessionResolver(scheduler=self)
        # 确保 resolver 能回调本 scheduler
        self._session_resolver.set_scheduler(self)
        self._now_fn = now_fn
        self._poll_interval = float(poll_interval_seconds)

        self._running = False
        self._task: asyncio.Task | None = None
        self._reload_event = asyncio.Event()
        self._last_store_mtime: float = 0.0

        # 内存中的活跃 run:run_id -> (job_id, started_at)
        self._active_runs: dict[str, tuple[str, float]] = {}

    # ---- 生命周期 ----

    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        await self.reload()
        self._task = asyncio.create_task(self._loop(), name="heartbeat-scheduler-loop")

    async def stop(self) -> None:
        self._running = False
        self._reload_event.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    # ---- store mtime 检测(镜像 CronSchedulerService) ----

    def _get_store_mtime(self) -> float:
        try:
            return self._store.path.stat().st_mtime
        except OSError:
            return 0.0

    def _sync_store_mtime(self) -> None:
        self._last_store_mtime = self._get_store_mtime()

    async def _check_store_changed(self) -> bool:
        mtime = self._get_store_mtime()
        if mtime != self._last_store_mtime and (mtime or self._last_store_mtime):
            logger.info(
                "[HeartbeatScheduler] store file changed (mtime %.3f -> %.3f), reloading",
                self._last_store_mtime,
                mtime,
            )
            await self.reload()
            return True
        return False

    async def reload(self) -> None:
        """重新加载 store;清理已删除 job 的 ghost run(方案 §7 ghost 清理)。"""
        await self._store.list_jobs()  # 触发读盘 + 缓存刷新
        # 清理 store 中已不存在的 job 的活跃 run
        all_jobs = await self._store.list_jobs()
        live_job_ids = {j.id for j in all_jobs}
        ghost_run_ids = [
            rid
            for rid, (jid, _) in self._active_runs.items()
            if jid not in live_job_ids
        ]
        for rid in ghost_run_ids:
            self._active_runs.pop(rid, None)
            logger.info(
                "[HeartbeatScheduler] cleared ghost run: run_id=%s (job no longer in store)",
                rid,
            )
        self._sync_store_mtime()
        self._reload_event.set()

    # ---- 主循环 ----

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._tick_once()
                self._reload_event.clear()
                try:
                    await asyncio.wait_for(
                        self._reload_event.wait(),
                        timeout=self._poll_interval,
                    )
                except asyncio.TimeoutError:
                    await self._check_store_changed()
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                logger.warning("[HeartbeatScheduler] loop error: %s", exc, exc_info=True)
                await asyncio.sleep(0.5)

    async def _tick_once(self) -> None:
        """扫描 due jobs 并逐个执行调度判断(方案 §7.4)。"""
        jobs = await self._store.list_jobs()
        now = self._now_fn()
        for job in jobs:
            try:
                await self._handle_job_tick(job, now)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[HeartbeatScheduler] handle job tick failed job=%s: %s",
                    job.id,
                    exc,
                    exc_info=True,
                )

    async def _handle_job_tick(self, job: HeartbeatJob, now: float) -> None:
        """单 job 的调度判断:可调度性 + due + session + 并发 + dispatch。"""
        if not job.enabled:
            return
        if job.status != STATUS_SCHEDULED:
            logger.warning(
                "[HeartbeatScheduler] skip inconsistent job %s: status=%s enabled=%s",
                job.id,
                job.status,
                job.enabled,
            )
            return
        if job.next_run_at is None or job.next_run_at > now:
            return
        await self._handle_due_job(job, now)

    # ---- due job 处理(方案 §7.5) ----

    async def _handle_due_job(self, job: HeartbeatJob, now: float) -> None:
        session = self._session_resolver.resolve(job.channel_id, job.session_id)
        if session is None:
            await self._handle_missing_session(job, now)
            return
        run_id = self._new_run_id(job, now)
        decision = await self._apply_concurrency_policy(job, run_id)
        if decision == "skip":
            await self._store.record_skipped(job.id, now, reason="previous_run_active")
            # 跳过本轮,基于 now 重算下次触发(不补跑历史积压)
            await self._store.reschedule(job.id, self._compute_next_run(job, now))
            return
        if decision == "queued":
            await self._store.reschedule(job.id, self._compute_next_run(job, now))
            return
        # decision == "run"
        await self._dispatch_job(job, run_id, now)

    async def _handle_missing_session(self, job: HeartbeatJob, now: float) -> None:
        """session 删除/归档/不可恢复(方案 §5.2)。"""
        policy = job.session_deleted_policy
        if policy == SESSION_DELETED_COMPLETED:
            await self._store.complete_for_session_deleted(job.id, now)
        else:
            # 默认 disable
            await self._store.disable(job.id, now)
        logger.info(
            "[HeartbeatScheduler] session missing for job=%s, applied policy=%s",
            job.id,
            policy,
        )

    # ---- 投递回原 session(方案 §7.6) ----

    def _new_run_id(self, job: HeartbeatJob, now: float) -> str:
        return f"{job.id}_run_{int(now)}_{secrets.token_hex(3)}"

    def _build_message(self, job: HeartbeatJob, run_id: str, now: float) -> Any:
        """构造投递回原 session 的 CHAT_SEND Message(方案 §7.6)。"""
        from jiuwenswarm.common.e2a.gateway_normalize import e2a_from_agent_fields
        from jiuwenswarm.common.schema.message import Message, ReqMethod

        # metadata.source 审计:scheduler 只消费,缺失/非法记 warning 后兜底。
        source = job.source
        if source not in {SOURCE_AGENT_TOOL, "web_rpc", "tui_rpc", SOURCE_SCHEDULE_RECOVERY}:
            logger.warning(
                "[HeartbeatScheduler] job %s missing or invalid source=%r, "
                "falling back to schedule_recovery",
                job.id,
                source,
            )
            source = SOURCE_SCHEDULE_RECOVERY

        return Message(
            id=f"heartbeat-job-{job.id}-{run_id}",
            type="req",
            channel_id=job.channel_id,
            session_id=job.session_id,
            req_method=ReqMethod.CHAT_SEND,
            timestamp=now,
            ok=True,
            params={
                "query": job.prompt,
                "content": job.prompt,
                # 不设置 params.mode:web/tui 由原 session 当前配置决定。
                "run": {
                    "kind": "heartbeat_job",
                    "context": {
                        "job_id": job.id,
                        "run_id": run_id,
                        "triggered_at": now,
                    },
                },
            },
            metadata={
                "automation": {
                    "kind": "heartbeat",
                    "job_id": job.id,
                    "run_id": run_id,
                    "triggered_at": now,
                    "source": source,
                    "trigger": "scheduler",
                }
            },
        )

    async def _dispatch_job(self, job: HeartbeatJob, run_id: str, now: float) -> None:
        """构造 CHAT_SEND 并投递回原 session(方案 §7.6)。

        第一版语义:投递成功即完成(_finish_run)。后续若 AgentServer 有异步 run
        完成回调,可升级为 run 完成后再完成。
        """
        msg = self._build_message(job, run_id, now)
        await self._store.mark_running(job.id, run_id, now)
        self._active_runs[run_id] = (job.id, now)
        try:
            await self._message_handler.publish_user_messages(msg)
        except Exception as exc:
            logger.warning(
                "[HeartbeatScheduler] dispatch failed job=%s run_id=%s: %s",
                job.id,
                run_id,
                exc,
            )
            await self._store.mark_failed(job.id, run_id, now, error=str(exc))
            self._active_runs.pop(run_id, None)
            # 失败也基于 now 重算下次触发,避免补跑风暴
            await self._store.reschedule(job.id, self._compute_next_run(job, now))
            return
        await self._finish_run(job, run_id, now, {"accepted": True, "finished_at": now})

    # ---- 下一次触发计算(方案 §7.7) ----

    def _compute_next_run(self, job: HeartbeatJob, base_ts: float) -> float | None:
        """interval/cron/once 下一次触发;基于 now 重算,不补跑历史。"""
        stype = job.schedule.type
        if stype == SCHEDULE_ONCE:
            return None
        if stype == SCHEDULE_INTERVAL:
            iv = job.schedule.interval_seconds or 60
            return base_ts + max(60, int(iv))
        if stype == SCHEDULE_CRON:
            return self._next_cron_ts(job, base_ts)
        raise ValueError(f"unsupported schedule type: {stype}")

    def _next_cron_ts(self, job: HeartbeatJob, base_ts: float) -> float | None:
        """复用 Cron 的 _cron_next_push_dt 计算 cron 下一次触发。"""
        from jiuwenswarm.gateway.cron.scheduler import _cron_next_push_dt

        tz_name = job.schedule.timezone or job.timezone or "Asia/Shanghai"
        tz = ZoneInfo(tz_name)
        base_dt = datetime.fromtimestamp(base_ts, tz=tz)
        try:
            nxt = _cron_next_push_dt(job.schedule.cron_expr or "", base_dt)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[HeartbeatScheduler] cron next run failed job=%s expr=%r: %s",
                job.id,
                job.schedule.cron_expr,
                exc,
            )
            return None
        return nxt.timestamp()

    # ---- 并发控制(方案 §7.8) ----

    async def _apply_concurrency_policy(self, job: HeartbeatJob, run_id: str) -> str:
        """返回 run/skip/queued。"""
        active = await self._store.get_active_run(job.id)
        if active is None:
            return "run"
        policy = job.concurrency_policy
        if policy == "skip":
            return "skip"
        if policy == "queue":
            await self._store.mark_queued(job.id, run_id)
            return "queued"
        if policy == "replace":
            # 第一版:replace 不保证精确取消 Agent run,降级为 skip。
            # 真正中断需要 _send_cancel_to_session 且能精确取消对应 run。
            logger.warning(
                "[HeartbeatScheduler] replace policy not fully supported for job=%s, "
                "downgrading to skip",
                job.id,
            )
            return "skip"
        return "skip"

    # ---- 完成与停止条件(方案 §7.10) ----

    async def _finish_run(
        self, job: HeartbeatJob, run_id: str, now: float, result: dict[str, Any]
    ) -> None:
        """投递成功后更新状态;达成停止条件则 completed。"""
        run_count = int(job.run_count) + 1
        should_stop = (
            bool(job.delete_after_run)
            or job.schedule.type == SCHEDULE_ONCE
            or (job.max_runs is not None and run_count >= int(job.max_runs))
        )
        if should_stop:
            await self._store.mark_completed(
                job.id, run_id, now, reason="stop_condition_reached"
            )
            self._active_runs.pop(run_id, None)
            return
        next_run_at = self._compute_next_run(job, now)
        await self._store.mark_succeeded(job.id, run_id, now)
        await self._store.reschedule(job.id, next_run_at)
        self._active_runs.pop(run_id, None)

    # ---- 取消执行(方案 §7.11) ----

    async def cancel_run(self, job_id: str, *, pause_schedule: bool = False) -> dict[str, Any]:
        """中断当前心跳触发的运行;可选同时停用后续调度。"""
        job = await self._store.get_job(job_id)
        if job is None:
            raise KeyError("job not found")
        run_id = job.run_state.current_run_id
        now = self._now_fn()
        cancelled_run_id: str | None = None
        if run_id:
            await self._send_cancel_to_session(job, run_id)
            await self._store.mark_cancelled(job.id, run_id, now)
            self._active_runs.pop(run_id, None)
            cancelled_run_id = run_id
        if pause_schedule:
            await self._store.disable(job.id, now)
        return {
            "job_id": job.id,
            "cancelled_run_id": cancelled_run_id,
            "paused": bool(pause_schedule),
        }

    async def _send_cancel_to_session(self, job: HeartbeatJob, run_id: str) -> None:
        """fire-and-forget 向原 session 投递 CHAT_CANCEL(第一版,不保证精确取消)。"""
        try:
            from jiuwenswarm.common.e2a.gateway_normalize import e2a_from_agent_fields
            from jiuwenswarm.common.schema.message import Message, ReqMethod

            now = self._now_fn()
            msg = Message(
                id=f"heartbeat-cancel-{job.id}-{run_id}",
                type="req",
                channel_id=job.channel_id,
                session_id=job.session_id,
                req_method=ReqMethod.CHAT_CANCEL,
                timestamp=now,
                ok=True,
                params={
                    "heartbeat": {
                        "job_id": job.id,
                        "run_id": run_id,
                    }
                },
                metadata={
                    "automation": {
                        "kind": "heartbeat",
                        "job_id": job.id,
                        "run_id": run_id,
                        "triggered_at": now,
                        "source": job.source,
                        "trigger": "cancel",
                    }
                },
            )
            await self._message_handler.publish_user_messages(msg)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[HeartbeatScheduler] send cancel to session failed job=%s run_id=%s: %s",
                job.id,
                run_id,
                exc,
            )

    # ---- trigger_run_now(controller 调用) ----

    async def trigger_run_now(
        self, job_id: str, *, reschedule: bool = False
    ) -> dict[str, Any]:
        """立即执行一次指定心跳任务(方案 §9.1 run_now)。"""
        job = await self._store.get_job(job_id)
        if job is None:
            raise KeyError("job not found")
        now = self._now_fn()
        run_id = self._new_run_id(job, now)
        # run_now 受并发策略约束
        decision = await self._apply_concurrency_policy(job, run_id)
        if decision == "skip":
            return {
                "accepted": False,
                "run_id": run_id,
                "session_id": job.session_id,
                "reason": "previous_run_active",
            }
        # queue:第一版直接降级为 run(queue 需要补跑队列消费,第一版简化)
        await self._dispatch_job(job, run_id, now)
        if reschedule:
            await self._store.reschedule(job.id, self._compute_next_run(job, now))
        return {
            "accepted": True,
            "run_id": run_id,
            "session_id": job.session_id,
        }

    # ---- 会话删除回调(session_resolver 转发) ----

    async def on_session_deleted(self, session_id: str) -> None:
        """session 被删除/归档时,按各 job 的 session_deleted_policy 处理。"""
        now = self._now_fn()
        jobs = await self._store.list_jobs_by_session(session_id)
        for job in jobs:
            if job.status in HEARTBEAT_TERMINAL_STATUSES:
                continue
            if job.session_deleted_policy == SESSION_DELETED_COMPLETED:
                await self._store.complete_for_session_deleted(job.id, now)
            else:
                await self._store.disable(job.id, now)
            logger.info(
                "[HeartbeatScheduler] session %s deleted, applied policy=%s to job=%s",
                session_id,
                job.session_deleted_policy,
                job.id,
            )

    # ---- preview(controller 调用) ----

    def preview_next_runs(self, job: HeartbeatJob, count: int = 5) -> list[dict[str, Any]]:
        """预览未来 N 次触发时间(方案 §9.1 preview)。"""
        count = max(1, min(int(count), 50))
        out: list[dict[str, Any]] = []
        now = self._now_fn()
        base = now
        stype = job.schedule.type
        if stype == SCHEDULE_ONCE:
            ra = job.schedule.run_at
            if ra is not None and ra >= now:
                out.append(self._format_preview(ra))
            return out
        for _ in range(count):
            if stype == SCHEDULE_INTERVAL:
                iv = job.schedule.interval_seconds or 60
                nxt = base + max(60, int(iv))
            elif stype == SCHEDULE_CRON:
                nxt_ts = self._next_cron_ts(job, base)
                if nxt_ts is None:
                    break
                nxt = nxt_ts
            else:
                break
            out.append(self._format_preview(nxt))
            base = nxt
        return out

    @staticmethod
    def _format_preview(ts: float) -> dict[str, Any]:
        from datetime import datetime as _dt

        try:
            iso = _dt.fromtimestamp(ts, tz=ZoneInfo("Asia/Shanghai")).isoformat()
        except Exception:
            iso = ""
        return {"run_at": ts, "iso": iso}
