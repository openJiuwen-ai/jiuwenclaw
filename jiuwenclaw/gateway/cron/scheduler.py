from __future__ import annotations

import asyncio
import heapq
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

from jiuwenclaw.gateway.agent_client import AgentServerClient
from jiuwenclaw.gateway.cron.models import CronJob, CronRunState
from jiuwenclaw.gateway.cron.store import CronJobStore
from jiuwenclaw.gateway.message_handler import MessageHandler
from jiuwenclaw.e2a.gateway_normalize import e2a_from_agent_fields
from jiuwenclaw.schema.message import EventType, Message, ReqMethod

logger = logging.getLogger(__name__)


def _now_utc_ts() -> float:
    return time.time()


def _extract_text_from_agent_payload(payload: dict | None) -> str:
    if not isinstance(payload, dict):
        return ""
    # Common: {"content": {"output": "...", "result_type": "answer"}}
    content = payload.get("content")
    if isinstance(content, dict):
        out = content.get("output")
        if isinstance(out, str):
            return out
        if out is not None:
            return str(out)
        return str(content)
    if isinstance(content, str):
        return content
    # Fallbacks
    heartbeat = payload.get("heartbeat")
    if isinstance(heartbeat, str) and heartbeat:
        return heartbeat
    text = payload.get("text")
    if isinstance(text, str) and text:
        return text
    return ""


def _cron_next_push_dt(cron_expr: str, base_dt: datetime) -> datetime:
    # Lazy import so the rest of the system can still run without cron enabled.
    from croniter import croniter  # type: ignore

    it = croniter(cron_expr, base_dt)
    nxt = it.get_next(datetime)
    if not isinstance(nxt, datetime):
        raise RuntimeError("croniter returned invalid datetime")
    if nxt.tzinfo is None:
        # Keep tz-consistent; base_dt is tz-aware in our usage.
        return nxt.replace(tzinfo=base_dt.tzinfo)
    return nxt


@dataclass(frozen=True)
class _Event:
    at_ts: float
    seq: int
    kind: str  # wake|push|push_update
    job_id: str
    run_id: str


class CronSchedulerService:
    """Async scheduler that wakes agent and pushes results to channels."""

    def __init__(
        self,
        *,
        store: CronJobStore,
        agent_client: AgentServerClient,
        message_handler: MessageHandler,
        now_fn: Callable[[], float] = _now_utc_ts,
    ) -> None:
        self._store = store
        self._agent_client = agent_client
        self._message_handler = message_handler
        self._now_fn = now_fn

        self._running = False
        self._task: asyncio.Task | None = None
        self._reload_event = asyncio.Event()

        self._jobs: dict[str, CronJob] = {}
        self._events: list[tuple[float, int, _Event]] = []
        self._seq = 0
        self._runs: dict[str, CronRunState] = {}  # run_id -> state
        self._run_tasks: dict[str, asyncio.Task] = {}

    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        await self.reload()
        self._task = asyncio.create_task(self._loop(), name="cron-scheduler")
        logger.info("[Cron] scheduler started")

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        # best-effort cancel in-flight runs
        for t in list(self._run_tasks.values()):
            if not t.done():
                t.cancel()
        self._run_tasks.clear()
        logger.info("[Cron] scheduler stopped")

    async def reload(self) -> None:
        """Reload jobs from store and rebuild the event queue."""
        jobs = await self._store.list_jobs()
        self._jobs = {j.id: j for j in jobs}
        self._events.clear()
        self._seq = 0

        now = self._now_fn()
        for job in jobs:
            if not job.enabled:
                continue
            try:
                push_dt, wake_dt, run_id = self._compute_next_run(job, now_ts=now)
            except Exception as exc:  # noqa: BLE001
                if self._is_croniter_no_next_date(exc):
                    # 已过期的 one-shot：标记 expired 并停用，避免 UI 仍显示 enabled。
                    try:
                        job.enabled = False
                        job.expired = True
                        await self._store.update_job(job.id, {"enabled": False, "expired": True})
                    except Exception as update_exc:  # noqa: BLE001
                        logger.warning(
                            "[Cron] mark expired failed job=%s: %s",
                            job.id,
                            update_exc,
                        )
                else:
                    logger.warning("[Cron] compute next run failed job=%s: %s", job.id, exc)
                continue
            self._schedule_event(wake_dt, "wake", job.id, run_id)
            self._schedule_event(push_dt, "push", job.id, run_id)

        self._reload_event.set()

    async def trigger_run_now(self, job_id: str) -> str:
        job_id = str(job_id or "").strip()
        job = self._jobs.get(job_id) or await self._store.get_job(job_id)
        if job is None:
            raise KeyError("job not found")
        now = datetime.now(tz=ZoneInfo(job.timezone))
        push_dt = now
        wake_dt = now
        run_id = f"{job.id}:{int(push_dt.timestamp())}"
        self._schedule_event(wake_dt, "wake", job.id, run_id)
        self._schedule_event(push_dt, "push", job.id, run_id)
        self._reload_event.set()
        return run_id

    def _schedule_event(self, at_dt: datetime, kind: str, job_id: str, run_id: str) -> None:
        at_ts = float(at_dt.timestamp())
        self._seq += 1
        ev = _Event(at_ts=at_ts, seq=self._seq, kind=kind, job_id=job_id, run_id=run_id)
        heapq.heappush(self._events, (ev.at_ts, ev.seq, ev))
        # 若事件已在 1 秒内到期（如 push_update 补发），需唤醒主循环，否则会等到 timeout（可能 10 分钟）
        if at_ts <= self._now_fn() + 1.0:
            self._reload_event.set()

    def _compute_next_run(self, job: CronJob, *, now_ts: float) -> tuple[datetime, datetime, str]:
        tz = ZoneInfo(job.timezone)
        base = datetime.fromtimestamp(now_ts, tz=tz)
        push_dt = _cron_next_push_dt(job.cron_expr, base)
        wake_dt = push_dt - timedelta(seconds=max(0, int(job.wake_offset_seconds or 0)))
        run_id = f"{job.id}:{int(push_dt.timestamp())}"
        return push_dt, wake_dt, run_id

    @staticmethod
    def _is_croniter_no_next_date(exc: Exception) -> bool:
        """croniter 找不到下一次日期（通常为单次 year 固定为过去）时视为过期。"""
        return (
            exc.__class__.__name__ == "CroniterBadDateError"
            or "failed to find next date" in str(exc)
        )

    async def _loop(self) -> None:
        while self._running:
            try:
                if not self._events:
                    self._reload_event.clear()
                    await self._reload_event.wait()
                    continue

                now = self._now_fn()
                at_ts, _, ev = self._events[0]
                delay = max(0.0, at_ts - now)

                if delay > 0:
                    self._reload_event.clear()
                    try:
                        await asyncio.wait_for(self._reload_event.wait(), timeout=delay)
                        continue
                    except asyncio.TimeoutError:
                        pass

                # due
                heapq.heappop(self._events)
                await self._handle_event(ev)
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                logger.warning("[Cron] scheduler loop error: %s", exc, exc_info=True)
                await asyncio.sleep(0.5)

    async def _handle_event(self, ev: _Event) -> None:
        job = self._jobs.get(ev.job_id)
        if job is None:
            return
        if not job.enabled:
            return

        if ev.kind == "wake":
            await self._on_wake(job, ev.run_id)
        elif ev.kind == "push":
            await self._on_push(job, ev.run_id)
            # Schedule next occurrence after push is triggered
            try:
                push_dt, wake_dt, next_run_id = self._compute_next_run(job, now_ts=self._now_fn())
                self._schedule_event(wake_dt, "wake", job.id, next_run_id)
                self._schedule_event(push_dt, "push", job.id, next_run_id)
            except Exception as exc:  # noqa: BLE001
                if self._is_croniter_no_next_date(exc):
                    # 执行后无下一次：将任务标记为过期并停用。
                    try:
                        job.enabled = False
                        job.expired = True
                        await self._store.update_job(job.id, {"enabled": False, "expired": True})
                    except Exception as update_exc:  # noqa: BLE001
                        logger.warning(
                            "[Cron] mark expired after push failed job=%s: %s",
                            job.id,
                            update_exc,
                        )
                else:
                    logger.warning("[Cron] compute next run failed after push job=%s: %s", job.id, exc)
        elif ev.kind == "push_update":
            await self._on_push_update(job, ev.run_id)

    async def _on_wake(self, job: CronJob, run_id: str) -> None:
        state = self._runs.get(run_id)
        if state is None:
            tz = ZoneInfo(job.timezone)
            # Approx from run_id timestamp suffix
            try:
                push_ts = int(run_id.split(":")[-1])
            except Exception:
                push_ts = int(self._now_fn())
            push_dt = datetime.fromtimestamp(push_ts, tz=tz)
            wake_dt = push_dt - timedelta(seconds=max(0, int(job.wake_offset_seconds or 0)))
            state = CronRunState(
                run_id=run_id,
                job_id=job.id,
                wake_at_iso=wake_dt.isoformat(),
                push_at_iso=push_dt.isoformat(),
            )
            self._runs[run_id] = state

        if run_id in self._run_tasks and not self._run_tasks[run_id].done():
            return

        async def _run_agent() -> None:
            state.status = "running"
            state.started_at = self._now_fn()
            try:
                ts = format(int(time.time() * 1000), "x")
                envelope = e2a_from_agent_fields(
                    request_id=f"cron-{run_id}",
                    channel_id="__cron__",
                    session_id=f"cron_{ts}_{job.id}",
                    req_method=ReqMethod.CHAT_SEND,
                    params={
                        "content": job.description,
                        "query": job.description,
                        "cron": {
                            "job_id": job.id,
                            "job_name": job.name,
                            "run_id": run_id,
                            "push_at": state.push_at_iso,
                            "wake_at": state.wake_at_iso,
                        },
                    },
                    is_stream=False,
                    timestamp=self._now_fn(),
                    metadata={"cron": {"job_id": job.id, "run_id": run_id}},
                )
                resp = await self._agent_client.send_request(envelope)
                text = _extract_text_from_agent_payload(resp.payload)
                if not text:
                    text = "[cron] 任务完成，但未返回可展示文本"
                state.result_text = text
                state.status = "succeeded" if resp.ok else "failed"
            except asyncio.CancelledError:
                state.status = "failed"
                state.error = "cancelled"
                raise
            except Exception as exc:  # noqa: BLE001
                state.status = "failed"
                state.error = str(exc)
            finally:
                state.finished_at = self._now_fn()
                # if placeholder already sent, push update immediately
                if state.placeholder_sent and not state.pushed_final and state.result_text:
                    logger.info(
                        "[Cron] scheduling immediate push_update after agent finished "
                        "job=%s run_id=%s text_len=%d",
                        job.id,
                        run_id,
                        len(state.result_text or ""),
                    )
                    self._schedule_event(datetime.fromtimestamp(self._now_fn(), tz=ZoneInfo(job.timezone)), "push_update", job.id, run_id)
                # if push time already passed, also try to push update
                try:
                    push_dt = datetime.fromisoformat(state.push_at_iso)
                    if push_dt.timestamp() <= self._now_fn() and not state.pushed_final and state.result_text:
                        logger.info(
                            "[Cron] scheduling late push_update because push_at<=now "
                            "job=%s run_id=%s text_len=%d",
                            job.id,
                            run_id,
                            len(state.result_text or ""),
                        )
                        self._schedule_event(datetime.fromtimestamp(self._now_fn(), tz=ZoneInfo(job.timezone)), "push_update", job.id, run_id)
                except Exception:
                    pass

        task = asyncio.create_task(_run_agent(), name=f"cron-run-{job.id}")
        self._run_tasks[run_id] = task

    async def _on_push(self, job: CronJob, run_id: str) -> None:
        state = self._runs.get(run_id)
        if state is None:
            tz = ZoneInfo(job.timezone)
            try:
                push_ts = int(run_id.split(":")[-1])
            except Exception:
                push_ts = int(self._now_fn())
            push_dt = datetime.fromtimestamp(push_ts, tz=tz)
            wake_dt = push_dt - timedelta(seconds=max(0, int(job.wake_offset_seconds or 0)))
            state = CronRunState(
                run_id=run_id,
                job_id=job.id,
                wake_at_iso=wake_dt.isoformat(),
                push_at_iso=push_dt.isoformat(),
            )
            self._runs[run_id] = state

        if state.pushed_final:
            return

        if state.result_text:
            await self._push_to_targets(job, state, text=state.result_text, is_placeholder=False)
            state.pushed_final = True
            return

        # Not ready: send placeholder
        placeholder = f"[cron] {job.name} 正在执行中，结果稍后补发（push_at={state.push_at_iso}）"
        await self._push_to_targets(job, state, text=placeholder, is_placeholder=True)
        state.placeholder_sent = True

    async def _on_push_update(self, job: CronJob, run_id: str) -> None:
        state = self._runs.get(run_id)
        if state is None:
            logger.info("[Cron] push_update skipped: no state job=%s run_id=%s", job.id, run_id)
            return
        if state.pushed_final:
            logger.info("[Cron] push_update skipped: already pushed_final job=%s run_id=%s", job.id, run_id)
            return
        if not state.result_text:
            logger.info("[Cron] push_update skipped: empty result_text job=%s run_id=%s", job.id, run_id)
            return
        logger.info(
            "[Cron] push_update start job=%s run_id=%s text_len=%d",
            job.id,
            run_id,
            len(state.result_text or ""),
        )
        await self._push_to_targets(job, state, text=state.result_text, is_placeholder=False)
        state.pushed_final = True
        logger.info("[Cron] push_update done job=%s run_id=%s", job.id, run_id)

    async def _push_to_targets(self, job: CronJob, state: CronRunState, *, text: str, is_placeholder: bool) -> None:
        logger.info(
            "[Cron] push_to_targets job=%s run_id=%s channel=%s is_placeholder=%s text_len=%d status=%s",
            job.id,
            state.run_id,
            (job.targets or "").strip(),
            bool(is_placeholder),
            len(text or ""),
            state.status,
        )
        payload_extra = {
            "content": text,
            "cron": {
                "job_id": job.id,
                "job_name": job.name,
                "run_id": state.run_id,
                "push_at": state.push_at_iso,
                "wake_at": state.wake_at_iso,
                "is_placeholder": bool(is_placeholder),
                "status": state.status,
            },
        }
        channel_id = (job.targets or "").strip()
        if not channel_id:
            return

        # 企业飞书：优先用作业里绑定的 SessionMap session_id（feishu::chat_id::bot_id::...），
        # 避免多群共用 bot 时误用 config 中的 last_*（最近一条消息的会话）。
        metadata: dict | None = None
        msg_session_id: str | None = None
        routing_sid = str(getattr(job, "session_id", None) or "").strip()
        if channel_id.startswith("feishu_enterprise:") and routing_sid and "::" in routing_sid:
            parts = routing_sid.split("::")
            if len(parts) >= 3 and parts[0] == "feishu":
                chat_part = str(parts[1] or "").strip()
                if chat_part:
                    metadata = {"feishu_chat_id": chat_part}
                    if len(parts) >= 6:
                        open_part = str(parts[3] or "").strip()
                        if open_part:
                            metadata["feishu_open_id"] = open_part
                    msg_session_id = chat_part

        # 针对 feishu/xiaoyi/whatsapp：从 config.yaml 取最近一次可回发的平台身份，写入 metadata
        # 这样即使 cron 推送没有 session_id，也能让 Channel.send 正常路由到对应会话。
        if metadata is None:
            try:
                from jiuwenclaw.config import get_config_raw

                cfg = get_config_raw() or {}
                channels_cfg = cfg.get("channels") or {}
                ch_cfg = channels_cfg.get(channel_id) or {}
                if channel_id == "feishu":
                    last_chat_id = str(ch_cfg.get("last_chat_id") or "").strip()
                    last_open_id = str(ch_cfg.get("last_open_id") or "").strip()
                    if last_chat_id or last_open_id:
                        metadata = {
                            "feishu_chat_id": last_chat_id,
                            "feishu_open_id": last_open_id,
                        }
                elif channel_id.startswith("feishu_enterprise:"):
                    app_id = channel_id.split(":", 1)[1].strip()
                    enterprise_cfg = channels_cfg.get("feishu_enterprise") or {}
                    if isinstance(enterprise_cfg, dict) and app_id:
                        for _, bot_cfg in enterprise_cfg.items():
                            if not isinstance(bot_cfg, dict):
                                continue
                            bot_app_id = str(bot_cfg.get("app_id") or "").strip()
                            if bot_app_id != app_id:
                                continue
                            last_chat_id = str(bot_cfg.get("last_chat_id") or "").strip()
                            last_open_id = str(bot_cfg.get("last_open_id") or "").strip()
                            if last_chat_id or last_open_id:
                                metadata = {
                                    "feishu_chat_id": last_chat_id,
                                    "feishu_open_id": last_open_id,
                                }
                            break
                elif channel_id == "xiaoyi":
                    last_session_id = str(ch_cfg.get("last_session_id") or "").strip()
                    last_task_id = str(ch_cfg.get("last_task_id") or "").strip()
                    if last_session_id or last_task_id:
                        metadata = {
                            "xiaoyi_session_id": last_session_id,
                            "xiaoyi_task_id": last_task_id,
                        }
                elif channel_id == "whatsapp":
                    last_jid = str(ch_cfg.get("last_jid") or "").strip()
                    if last_jid:
                        metadata = {
                            "whatsapp_jid": last_jid,
                        }
                elif channel_id == "wecom":
                    last_chat_id = str(ch_cfg.get("last_chat_id") or "").strip()
                    if last_chat_id:
                        metadata = {
                            "wecom_chat_id": last_chat_id,
                        }
            except Exception:
                metadata = None

        msg = Message(
            id=f"cron-push-{state.run_id}-{channel_id}",
            type="event",
            channel_id=channel_id,
            session_id=msg_session_id,
            params={},
            timestamp=self._now_fn(),
            ok=True,
            payload=payload_extra,
            event_type=EventType.CHAT_FINAL,
            metadata=metadata,
        )
        await self._message_handler.publish_robot_messages(msg)
