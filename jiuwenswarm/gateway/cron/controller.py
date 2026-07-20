from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, ClassVar, List
from zoneinfo import ZoneInfo

from openjiuwen.core.foundation.tool import LocalFunction, Tool, ToolCard

from jiuwenswarm.gateway.cron.cron_expr import normalize_cron_expr, iso_to_five_field_cron, validate_cron_expression
from jiuwenswarm.gateway.cron.models import (
    CRON_JOB_DEFAULT_MODE,
    CronRunState,
    CronTargetChannel,
    cron_job_metadata,
    cron_job_modes_for_tools,
    is_team_cron_mode,
    is_valid_target_channel_id,
    normalize_cron_job_mode,
    normalize_target_channel_id,
    normalize_required_device_intents,
)
from jiuwenswarm.gateway.cron.scheduler import CronSchedulerService, _cron_next_push_dt
from jiuwenswarm.gateway.cron.store import CronJobStore


class CronController:
    """High-level cron API used by WebChannel handlers. Singleton."""

    _instance: ClassVar[CronController | None] = None

    def __init__(self, *, store: CronJobStore, scheduler: CronSchedulerService) -> None:
        self._store = store
        self._scheduler = scheduler
        self._target_channel: CronTargetChannel | None = None

    def set_target_channel(self, channel: CronTargetChannel) -> None:
        self._target_channel = channel

    @classmethod
    def get_instance(
        cls,
        *,
        store: CronJobStore | None = None,
        scheduler: CronSchedulerService | None = None,
    ) -> CronController:
        """Return the singleton instance.

        On first call, store and scheduler are required to create the instance.
        On subsequent calls, both can be omitted to get the existing instance.

        Args:
            store: Required only on first call.
            scheduler: Required only on first call.

        Returns:
            The singleton CronController.

        Raises:
            RuntimeError: If instance not yet initialized and store/scheduler not provided.
        """
        if cls._instance is not None:
            return cls._instance
        if store is None or scheduler is None:
            raise RuntimeError(
                "CronController not initialized. Call get_instance(store=..., scheduler=...) first."
            )
        cls._instance = cls(store=store, scheduler=scheduler)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton. For testing only."""
        cls._instance = None

    @staticmethod
    def _validate_schedule(*, cron_expr: str, timezone: str) -> None:
        tz = ZoneInfo(timezone)
        base = datetime.now(tz=tz)
        # All jobs use 5-field standard cron, which always has a future
        # match (year is implicit '*'). One-shot semantics are achieved
        # via delete_after_run=True, not via a fixed-year cron expression.
        _ = _cron_next_push_dt(cron_expr, base)

    _DESCRIPTION_TIME_KEYWORDS = ("每天", "每周", "每月", "上午", "下午", "早上", "晚上", "凌晨")

    def _normalize_targets(self, raw: Any) -> str:
        """将 targets 规范为 CronTargetChannel 枚举值。"""
        raw_s = str(raw or "").strip()
        if self._target_channel is None and not raw_s:
            raise ValueError("targets is required when target_channel is not set")
        if not raw_s:
            return normalize_target_channel_id(self._target_channel.value)
        if not is_valid_target_channel_id(raw_s):
            raise ValueError(
                "targets must be one of tui/web/feishu/dingtalk/whatsapp/wecom/xiaoyi/wechat"
                " or feishu_enterprise:<app_id>"
            )
        return normalize_target_channel_id(raw_s)

    @classmethod
    def _normalize_description(cls, description: str, name: str) -> str:
        """若 description 含时间/频率用语且 name 为纯任务，则只保留任务内容（用 name）。"""
        description = (description or "").strip()
        name = (name or "").strip()
        if not name:
            return description
        if not any(kw in description for kw in cls._DESCRIPTION_TIME_KEYWORDS):
            return description
        if name in description or description.endswith(name):
            return name
        return description

    @staticmethod
    def _routing_session_id(targets: str, raw: Any) -> str | None:
        """Accept session_id for all channels; feishu_enterprise requires SessionMap format."""
        targets_s = str(targets or "").strip()
        raw_s = str(raw or "").strip() if isinstance(raw, str) else ""
        if not raw_s:
            return None
        if targets_s.startswith("feishu_enterprise:"):
            if "::" not in raw_s:
                return None
            parts = raw_s.split("::")
            if len(parts) < 3 or parts[0] != "feishu":
                return None
            return raw_s
        return raw_s

    async def list_jobs(self) -> list[dict[str, Any]]:
        jobs = await self._store.list_jobs()
        return [j.to_dict() for j in jobs]

    async def get_job(self, job_id: str) -> dict[str, Any] | None:
        job = await self._store.get_job(job_id)
        return job.to_dict() if job else None

    @staticmethod
    def job_metadata() -> dict[str, Any]:
        return cron_job_metadata()

    async def create_job(self, params: dict[str, Any]) -> dict[str, Any]:
        name = str(params.get("name") or "").strip()
        cron_expr = normalize_cron_expr(str(params.get("cron_expr") or "").strip())
        timezone = str(params.get("timezone") or "Asia/Shanghai").strip() or "Asia/Shanghai"
        enabled = bool(params.get("enabled", True))
        description = str(params.get("description") or "")
        wake_offset_seconds = params.get("wake_offset_seconds", None)
        raw_targets = params.get("targets")
        mode = params.get("mode")
        if mode is not None and str(mode).strip():
            mode = normalize_cron_job_mode(mode)
        else:
            mode = None
        targets = self._normalize_targets(raw_targets)

        self._validate_schedule(cron_expr=cron_expr, timezone=timezone)
        description = self._normalize_description(description, name)

        routing_sid = self._routing_session_id(targets, params.get("session_id"))
        chat_type = params.get("chat_type")
        delete_after_run = params.get("delete_after_run")
        timeout_seconds = params.get("timeout_seconds")
        required_device_intents = normalize_required_device_intents(
            params.get("required_device_intents")
        )
        xiaoyi_push_id = str(params.get("xiaoyi_push_id") or "").strip() or None
        if required_device_intents and not xiaoyi_push_id:
            raise ValueError("xiaoyi_push_id is required for device cron jobs")
        effective_mode = mode or CRON_JOB_DEFAULT_MODE
        if required_device_intents and is_team_cron_mode(effective_mode):
            raise ValueError("Xiaoyi device cron jobs do not support team mode")
        job = await self._store.create_job(
            job_id=str(params.get("id") or "").strip() or None,
            name=name,
            cron_expr=cron_expr,
            timezone=timezone,
            enabled=enabled,
            wake_offset_seconds=int(wake_offset_seconds) if wake_offset_seconds is not None else None,
            description=description,
            targets=targets,
            session_id=routing_sid,
            chat_type=chat_type,
            mode=mode,
            delete_after_run=delete_after_run,
            timeout_seconds=timeout_seconds,
            required_device_intents=required_device_intents,
            xiaoyi_push_id=xiaoyi_push_id,
        )
        await self._scheduler.reload()
        return job.to_dict()

    async def update_job(self, job_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        patch = dict(patch or {})
        if "mode" in patch:
            patch["mode"] = normalize_cron_job_mode(patch.get("mode"))
        if "targets" in patch:
            patch["targets"] = self._normalize_targets(patch["targets"])
        existing = await self._store.get_job(job_id)
        if existing is None:
            raise KeyError("job not found")
        immutable_device_fields = {
            "description",
            "required_device_intents",
            "xiaoyi_push_id",
        }
        if existing.required_device_intents and immutable_device_fields.intersection(
            patch
        ):
            raise ValueError(
                "Device cron task content cannot be updated; delete and recreate it"
            )
        if "cron_expr" in patch:
            patch["cron_expr"] = normalize_cron_expr(str(patch["cron_expr"]).strip())
        if "cron_expr" in patch or "timezone" in patch:
            cron_expr = str(patch.get("cron_expr") or existing.cron_expr).strip()
            timezone = str(patch.get("timezone") or existing.timezone).strip()
            self._validate_schedule(cron_expr=cron_expr, timezone=timezone)
        if "description" in patch:
            name = str(patch.get("name") or existing.name or "").strip()
            patch["description"] = self._normalize_description(str(patch.get("description") or ""), name)

        final_targets = str(patch.get("targets") or existing.targets).strip()
        if "session_id" in patch:
            patch["session_id"] = self._routing_session_id(
                final_targets, patch.get("session_id")
            )
        elif "targets" in patch:
            patch["session_id"] = self._routing_session_id(
                final_targets, existing.session_id
            )

        job = await self._store.update_job(job_id, patch)
        await self._scheduler.reload()
        return job.to_dict()

    async def delete_job(self, job_id: str) -> bool:
        deleted = await self._store.delete_job(job_id)
        if deleted:
            await self._scheduler.reload()
        return deleted

    async def toggle_job(self, job_id: str, enabled: bool) -> dict[str, Any]:
        job = await self._store.update_job(job_id, {"enabled": bool(enabled)})
        await self._scheduler.reload()
        return job.to_dict()

    async def preview_job(self, job_id: str, count: int = 5) -> list[dict[str, Any]]:
        job = await self._store.get_job(job_id)
        if job is None:
            raise KeyError("job not found")
        count = max(1, min(int(count), 50))

        tz = ZoneInfo(job.timezone)
        base = datetime.now(tz=tz)
        out: list[dict[str, Any]] = []
        push_dt = base
        for _ in range(count):
            try:
                push_dt = _cron_next_push_dt(job.cron_expr, push_dt)
            except Exception as exc:  # noqa: BLE001
                _msg = str(exc)
                if "CroniterBadDateError" in _msg or "failed to find next date" in _msg:
                    break
                raise
            if out and push_dt.isoformat() == out[-1]["push_at"]:
                break
            wake_dt = push_dt - timedelta(seconds=max(0, int(job.wake_offset_seconds or 0)))
            out.append({"wake_at": wake_dt.isoformat(), "push_at": push_dt.isoformat()})
        return out

    async def run_now(self, job_id: str) -> str:
        run_id = await self._scheduler.trigger_run_now(job_id)
        return run_id

    # ── A2A CronQuery protocol methods ────────────────────────────────

    def _compute_next_run_ms(self, job_id: str) -> int:
        """Compute next scheduled run time (ms) for a job from scheduler events.

        Scans ``scheduler._events`` heap for the earliest ``wake`` event
        matching ``job_id``. Returns 0 if not found or scheduler unavailable.
        """
        events = getattr(self._scheduler, "_events", None)
        if not events:
            return 0
        min_ts: float | None = None
        for at_ts, _seq, ev in events:
            if ev.kind == "wake" and ev.job_id == job_id:
                if min_ts is None or at_ts < min_ts:
                    min_ts = at_ts
        if min_ts is not None:
            return int(float(min_ts) * 1000)
        return 0

    def _compute_last_run_ms(self, job_id: str) -> int:
        """Compute last run start time (ms) for a job from scheduler runs.

        Scans ``scheduler._runs`` dict for the most recent run matching
        ``job_id`` (by ``started_at``). Returns 0 if not found.
        """
        runs = getattr(self._scheduler, "_runs", None)
        if not runs:
            return 0
        max_started: float | None = None
        for state in runs.values():
            if state.job_id != job_id:
                continue
            if isinstance(state.started_at, (int, float)):
                if max_started is None or state.started_at > max_started:
                    max_started = state.started_at
        if max_started is not None:
            return int(float(max_started) * 1000)
        return 0

    def _compute_job_run_stats(self, job_id: str) -> dict[str, Any]:
        """Compute aggregated run statistics for a job from scheduler runs.

        Returns a dict with:
            last_run_status: str (ok/error/skipped/running) — status of the most recent run
            last_duration_ms: int — duration of the most recent run
            last_delivery_status: str — delivery status of the most recent run
            consecutive_errors: int — count of consecutive failed runs
            consecutive_skipped: int — count of consecutive skipped runs
        """
        runs = getattr(self._scheduler, "_runs", None)
        if not runs:
            return {
                "last_run_status": "skipped",
                "last_duration_ms": 0,
                "last_delivery_status": "unknown",
                "consecutive_errors": 0,
                "consecutive_skipped": 0,
            }

        # Collect all runs for this job, sorted by started_at descending
        job_runs: list[CronRunState] = []
        for state in runs.values():
            if state.job_id == job_id:
                job_runs.append(state)
        if not job_runs:
            return {
                "last_run_status": "skipped",
                "last_duration_ms": 0,
                "last_delivery_status": "unknown",
                "consecutive_errors": 0,
                "consecutive_skipped": 0,
            }

        # Sort by started_at descending (most recent first)
        job_runs.sort(
            key=lambda s: s.started_at or 0,
            reverse=True,
        )

        latest = job_runs[0]
        # Map internal status to A2A display
        status_map = {
            "succeeded": "ok",
            "failed": "error",
            "running": "running",
            "pending": "skipped",
        }
        last_run_status = status_map.get(latest.status, "skipped")

        # Duration
        started_ms = int(latest.started_at * 1000) if isinstance(latest.started_at, (int, float)) else 0
        finished_ms = int(latest.finished_at * 1000) if isinstance(latest.finished_at, (int, float)) else 0
        last_duration_ms = max(0, finished_ms - started_ms) if started_ms and finished_ms else 0

        # Delivery status from the latest run
        last_delivery_status = latest.delivery_status or "unknown"

        # Count consecutive errors and skipped from the most recent runs
        consecutive_errors = 0
        consecutive_skipped = 0
        for s in job_runs:
            if s.status == "failed":
                consecutive_errors += 1
            elif s.status == "pending":
                consecutive_skipped += 1
            else:
                break  # stop counting at first non-error/skipped

        return {
            "last_run_status": last_run_status,
            "last_duration_ms": last_duration_ms,
            "last_delivery_status": last_delivery_status,
            "consecutive_errors": consecutive_errors,
            "consecutive_skipped": consecutive_skipped,
        }

    async def status(self) -> dict[str, Any]:
        """Return cron service overall status for A2A ``status`` action."""
        jobs = await self._store.list_jobs()
        store_path = str(self._store.path)
        # nextWakeAtMs: compute from scheduler events if available
        next_wake_ms = 0
        events = getattr(self._scheduler, "_events", None)
        if events:
            # _events is a heap of (at_ts, seq, ev); find earliest wake event
            min_ts = None
            for at_ts, _seq, ev in events:
                if ev.kind == "wake":
                    if min_ts is None or at_ts < min_ts:
                        min_ts = at_ts
            if min_ts is not None:
                next_wake_ms = int(float(min_ts) * 1000)
        return {
            "enabled": True,
            "storePath": store_path,
            "jobs": len(jobs),
            "nextWakeAtMs": next_wake_ms,
        }

    async def runs(self, job_id: str, limit: int = 10) -> dict[str, Any]:
        """Return run history for a job (A2A ``runs`` action).

        The scheduler ``_runs`` dict only tracks in-flight runs (not persisted).
        We return whatever is available; absent persistent history, entries will
        be sparse. This is a best-effort implementation.
        """
        job_id = str(job_id or "").strip()
        if not job_id:
            raise ValueError("jobId is required")
        job = await self._store.get_job(job_id)
        if job is None:
            raise KeyError("job not found")

        limit = max(1, min(int(limit), 100))
        entries: list[dict[str, Any]] = []
        # Compute next run time for this job once (shared across all entries)
        next_run_ms = self._compute_next_run_ms(job_id)
        runs = getattr(self._scheduler, "_runs", None)
        if runs:
            for state in runs.values():
                if state.job_id == job_id and state.status in ("succeeded", "failed", "running"):
                    entries.append(state.to_a2a_run_entry(for_runs=True, next_run_at_ms=next_run_ms))
        # Sort by ts descending and limit
        entries.sort(key=lambda e: e.get("ts", 0), reverse=True)
        entries = entries[:limit]
        total = len(entries)
        return {
            "entries": entries,
            "total": total,
            "offset": 0,
            "limit": limit,
            "hasMore": False,
            "nextOffset": None,
        }

    async def query_time_list(self) -> list[dict[str, list[dict[str, Any]]]]:
        """Return run history grouped by date (A2A ``queryTimeList``).

        Per protocol doc §2.8: queryTimeList queries **historical** execution
        records only (not upcoming schedule). ``ans`` is an array of single-key
        objects: ``[{"2026-06-01": [entry, ...]}, {"2026-06-02": [entry, ...]}]``.
        Each entry has action="finished", status=ok|error|skipped, etc.

        Data source: ``scheduler._runs`` dict (in-memory run history).
        Future scheduled events (from ``_events`` heap) are NOT included —
        they are not "finished" executions.
        """
        from datetime import datetime, timezone

        all_entries: list[dict[str, Any]] = []

        # Cache next run times per job_id to avoid repeated heap scans.
        next_run_cache: dict[str, int] = {}

        # Collect historical runs from scheduler._runs.
        # Include all statuses: succeeded, failed, running, pending.
        # - succeeded/failed: completed runs → status "ok"/"error"
        # - running: in-flight run → status "running"
        # - pending: wake triggered but agent not started yet → status "skipped"
        runs = getattr(self._scheduler, "_runs", None)
        if runs:
            for state in runs.values():
                jid = state.job_id
                if jid not in next_run_cache:
                    next_run_cache[jid] = self._compute_next_run_ms(jid)
                all_entries.append(
                    state.to_a2a_run_entry(next_run_at_ms=next_run_cache[jid])
                )

        # Group by date (YYYY-MM-DD) based on ts
        by_date: dict[str, list[dict[str, Any]]] = {}
        for entry in all_entries:
            ts = entry.get("ts", 0)
            if ts > 0:
                dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
                date_key = dt.strftime("%Y-%m-%d")
            else:
                date_key = "unknown"
            if entry.get("status") != "running":
                by_date.setdefault(date_key, []).append(entry)

        # Sort entries within each date by ts ascending
        for entries in by_date.values():
            entries.sort(key=lambda e: e.get("ts", 0))

        # Per protocol doc: ans is an array of single-key objects
        # e.g. [{"2026-06-01": [...]}, {"2026-06-02": [...]}]
        ans: list[dict[str, list[dict[str, Any]]]] = [
            {date_key: entries} for date_key, entries in by_date.items()
        ]
        return ans

    async def list_jobs_a2a(self, include_disabled: bool = True) -> dict[str, Any]:
        """Return jobs list in A2A ``list`` ans format."""
        jobs = await self._store.list_jobs()
        if not include_disabled:
            jobs = [j for j in jobs if j.enabled]
        a2a_jobs = [
            j.to_a2a_job(
                next_run_at_ms=self._compute_next_run_ms(j.id),
                last_run_at_ms=self._compute_last_run_ms(j.id),
                job_run_stats=self._compute_job_run_stats(j.id),
            )
            for j in jobs
        ]
        total = len(a2a_jobs)
        return {
            "jobs": a2a_jobs,
            "total": total,
            "offset": 0,
            "limit": total,
            "hasMore": False,
            "nextOffset": None,
            "deliveryPreviews": {},
        }

    async def create_job_a2a(self, job_params: dict[str, Any], session_id: str | None = None) -> dict[str, Any]:
        """Create job from A2A ``add`` action params and return A2A ans format.

        Supports both frontend nested format ({job: {...}}) and Xiaoyi device
        flat format (job fields directly in params).
        """
        # Extract A2A nested fields and map to internal flat params
        params: dict[str, Any] = {}
        params["name"] = str(job_params.get("name") or "").strip()
        params["enabled"] = bool(job_params.get("enabled", True))
        if job_params.get("deleteAfterRun") is not None:
            params["delete_after_run"] = bool(job_params.get("deleteAfterRun"))
        if job_params.get("wakeOffsetSeconds") is not None:
            try:
                params["wake_offset_seconds"] = int(job_params.get("wakeOffsetSeconds"))
            except (TypeError, ValueError):
                pass
        if job_params.get("mode") is not None:
            mode_val = str(job_params.get("mode") or "").strip()
            if mode_val:
                params["mode"] = mode_val
        # 设备可能用 wakeMode ("now"/"skip") 代替 wakeOffsetSeconds
        wake_mode = str(job_params.get("wakeMode") or "").strip().lower()
        if wake_mode == "now" and "wake_offset_seconds" not in params:
            params["wake_offset_seconds"] = 0

        schedule = job_params.get("schedule") or {}
        if isinstance(schedule, dict):
            schedule_kind = str(schedule.get("kind") or "").strip().lower()
            timezone = str(schedule.get("tz") or "Asia/Shanghai").strip() or "Asia/Shanghai"
            params["timezone"] = timezone
            if schedule_kind == "at":
                # One-shot task: convert ISO datetime to 5-field cron.
                # Device sends {"kind": "at", "at": "2026-07-16T16:00:00+08:00", "expr": ""}.
                # The year is dropped (5-field cron has no year); one-shot
                # semantics rely on delete_after_run=True below.
                at_iso = str(schedule.get("at") or "").strip()
                if not at_iso:
                    raise ValueError("schedule.kind='at' requires non-empty 'at' field")
                params["cron_expr"] = iso_to_five_field_cron(at_iso, timezone=timezone)
                # One-shot tasks should be auto-deleted after execution.
                params["delete_after_run"] = True
            else:
                # Standard cron task.
                params["cron_expr"] = str(schedule.get("expr") or "").strip()

        # description: 优先从 payload.message 获取，fallback 到顶层 description
        payload = job_params.get("payload") or {}
        desc = ""
        if isinstance(payload, dict):
            desc = str(payload.get("message") or "").strip()
        if not desc:
            desc = str(job_params.get("description") or "").strip()
        params["description"] = desc

        delivery = job_params.get("delivery") or {}
        if isinstance(delivery, dict):
            channel = str(delivery.get("channel") or "").strip()
            # 设备可能发送 "xiaoyi-channel"，映射为内部枚举值 "xiaoyi"
            if channel.endswith("-channel"):
                channel = channel[:-len("-channel")]
            if channel:
                params["targets"] = channel

        if session_id:
            params["session_id"] = session_id

        job = await self.create_job(params)
        # Convert to A2A format
        jobs_list = await self._store.list_jobs()
        created = None
        for j in jobs_list:
            if j.id == job.get("id"):
                created = j
                break
        if created:
            return created.to_a2a_job(
                include_description=True,
                next_run_at_ms=self._compute_next_run_ms(created.id),
                last_run_at_ms=self._compute_last_run_ms(created.id),
            )
        # Fallback: construct minimal A2A shape from to_dict result
        return job

    async def update_job_a2a(self, job_id: str, patch_params: dict[str, Any]) -> dict[str, Any]:
        """Update job from A2A ``update`` action and return A2A ans format."""
        patch: dict[str, Any] = {}
        # Map A2A nested patch fields to internal flat patch
        if "enabled" in patch_params:
            patch["enabled"] = bool(patch_params["enabled"])
        if "name" in patch_params:
            patch["name"] = str(patch_params["name"]).strip()
        if "wakeOffsetSeconds" in patch_params:
            try:
                patch["wake_offset_seconds"] = int(patch_params["wakeOffsetSeconds"])
            except (TypeError, ValueError):
                pass
        if "mode" in patch_params:
            mode_val = str(patch_params.get("mode") or "").strip()
            if mode_val:
                patch["mode"] = mode_val
        schedule = patch_params.get("schedule")
        if isinstance(schedule, dict):
            schedule_kind = str(schedule.get("kind") or "").strip().lower()
            timezone = str(schedule.get("tz") or "").strip()
            if timezone:
                patch["timezone"] = timezone
            if schedule_kind == "at":
                # One-shot task: convert ISO datetime to 5-field cron.
                at_iso = str(schedule.get("at") or "").strip()
                if not at_iso:
                    raise ValueError("schedule.kind='at' requires non-empty 'at' field")
                # Use timezone from schedule if present, else fall back to existing job tz
                tz_for_conversion = timezone or "Asia/Shanghai"
                patch["cron_expr"] = iso_to_five_field_cron(at_iso, timezone=tz_for_conversion)
                # One-shot tasks should be auto-deleted after execution.
                patch["delete_after_run"] = True
            elif schedule.get("expr"):
                patch["cron_expr"] = str(schedule["expr"]).strip()
        payload = patch_params.get("payload")
        if isinstance(payload, dict) and payload.get("message") is not None:
            patch["description"] = str(payload["message"]).strip()
        delivery = patch_params.get("delivery")
        if isinstance(delivery, dict) and delivery.get("channel"):
            patch["targets"] = str(delivery["channel"]).strip()

        job = await self.update_job(job_id, patch)
        # Convert to A2A format (include description per add/update protocol)
        stored = await self._store.get_job(job_id)
        if stored:
            return stored.to_a2a_job(
                include_description=True,
                next_run_at_ms=self._compute_next_run_ms(stored.id),
                last_run_at_ms=self._compute_last_run_ms(stored.id),
            )
        return job

    async def delete_job_a2a(self, job_id: str) -> dict[str, Any]:
        """Delete job and return A2A ``remove`` ans format."""
        deleted = await self.delete_job(job_id)
        return {
            "ok": True,
            "removed": bool(deleted),
        }

    async def run_now_a2a(self, job_id: str) -> dict[str, Any]:
        """Trigger immediate run and return A2A ``run`` ans format."""
        run_id = await self.run_now(job_id)
        return {
            "ok": True,
            "runId": run_id,
            "enqueued": True,
        }

    async def _create_job_tool(
        self,
        name: str,
        cron_expr: str,
        timezone: str,
        description: str,
        targets: str = "",
        enabled: bool = True,
        wake_offset_seconds: int | None = None,
        mode: str | None = None,
        timeout_seconds: int | None = None,
        required_device_intents: list[str] | None = None,
        delete_after_run: bool | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "name": name,
            "cron_expr": cron_expr,
            "timezone": timezone,
            "targets": targets,
            "enabled": enabled,
            "description": description,
        }
        if wake_offset_seconds is not None:
            params["wake_offset_seconds"] = wake_offset_seconds
        if mode is not None and str(mode).strip():
            params["mode"] = mode
        if timeout_seconds is not None:
            params["timeout_seconds"] = timeout_seconds
        if required_device_intents is not None:
            params["required_device_intents"] = required_device_intents
        if delete_after_run is not None:
            params["delete_after_run"] = delete_after_run
        return await self.create_job(params)

    async def _update_job_tool(
        self, job_id: str, patch: dict[str, Any]
    ) -> dict[str, Any]:
        return await self.update_job(job_id, patch)

    async def _preview_job_tool(
        self, job_id: str, count: int = 5
    ) -> list[dict[str, Any]]:
        return await self.preview_job(job_id, count)

    def get_tools(self) -> List[Tool]:
        """Return cron job tools for registration in the openJiuwen Runner.
        Tools to be returned:
            list_jobs
            get_job
            create_job
            update_job
            delete_job
            toggle_job
            preview_job

        Usage:
            toolkit = CronController(xxxxxx)
            tools = toolkit.get_tools()
            Runner.resource_mgr.add_tool(tools)
            for t in tools:
                agent.ability_manager.add(t.card)

        Returns:
            List of Tool instances (LocalFunction) ready for Runner/agent registration.
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

        return [
            make_tool(
                name="cron_list_jobs",
                description=(
                    "List all cron jobs. Returns a list of job objects with"
                    " id, name, cron_expr, timezone, enabled, etc."
                ),
                input_params={"type": "object", "properties": {}},
                func=self.list_jobs,
            ),
            make_tool(
                name="cron_get_job",
                description="Get a single cron job by id. Returns job details or None if not found.",
                input_params={
                    "type": "object",
                    "properties": {
                        "job_id": {
                            "type": "string",
                            "description": "The job id to look up",
                        }
                    },
                    "required": ["job_id"],
                },
                func=self.get_job,
            ),
            make_tool(
                name="cron_create_job",
                description=(
                    "Create a scheduled cron job.\n"
                    "cron_expr is always 5-field standard cron: "
                    "minute hour day month day-of-week.\n"
                    "  Example: daily 9:00 = '0 9 * * *', every Monday 9:00 = '0 9 * * 1'.\n"
                    "For a one-shot / relative-time task (e.g. \"in X minutes\"), "
                    "compute run_at = now + X minutes in the given timezone, "
                    "encode it as a 5-field cron (minute hour day month day-of-week), "
                    "and set delete_after_run=true so the task fires once then stops.\n"
                    "  Example: run_at = Mar 19, 2026 10:07 local -> '7 10 19 3 *' with "
                    "delete_after_run=true.\n"
                    "description should contain task content only (no time/frequency). "
                    "timezone defaults to Asia/Shanghai."
                ),
                input_params={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Job name"},
                        "cron_expr": {
                            "type": "string",
                            "description": (
                                "Cron expression (5 fields): "
                                "minute hour day month day-of-week. "
                                "For one-shot tasks, encode the target time as a 5-field "
                                "cron and set delete_after_run=true. "
                                "Example: 2026-03-28 17:00 local -> '0 17 28 3 *' "
                                "with delete_after_run=true."
                            ),
                        },
                        "timezone": {
                            "type": "string",
                            "description": "Time zone (IANA), e.g. Asia/Shanghai",
                            "default": "Asia/Shanghai",
                        },
                        "targets": {
                            "type": "string",
                            "enum": [e.value for e in CronTargetChannel],
                            "description": (
                                "Delivery channel: tui, web, feishu, dingtalk, "
                                "whatsapp, wecom, xiaoyi, wechat. "
                                "If omitted, use the current request source channel."
                            ),
                        },
                        "enabled": {
                            "type": "boolean",
                            "description": "Whether the job is enabled",
                            "default": True,
                        },
                        "description": {
                            "type": "string",
                            "description": (
                                "Task payload text sent to the assistant at run time. "
                                "Do not include time or frequency."
                            ),
                        },
                        "wake_offset_seconds": {
                            "type": "integer",
                            "description": "Seconds to wake before push. Default 300",
                            "default": 300,
                        },
                        "mode": {
                            "type": "string",
                            "enum": cron_job_modes_for_tools(),
                            "description": (
                                "Agent runtime mode when the job runs. "
                                "Default agent.fast. Use team for multi-agent team execution."
                            ),
                        },
                        "timeout_seconds": {
                            "type": "integer",
                            "description": (
                                "Execution timeout in seconds (60-259200). "
                                "Default 600 for normal modes and 1200 for team modes."
                            ),
                        },
                        "delete_after_run": {
                            "type": "boolean",
                            "description": (
                                "If true, the task fires once then is marked "
                                "expired/disabled (one-shot semantics). Use this "
                                "with a 5-field cron encoding a specific "
                                "month/day/hour/minute for one-shot tasks."
                            ),
                            "default": False,
                        },
                        "required_device_intents": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Device intent names needed when this cron runs. "
                                "For Xiaoyi device cron jobs, call check_plugin_privilege "
                                "for each intent before creating the job."
                            ),
                        },
                    },
                    "required": ["name", "cron_expr", "timezone", "description"],
                },
                func=self._create_job_tool,
            ),
            make_tool(
                name="cron_update_job",
                description=(
                    "Update an existing cron job. Pass job_id and a patch dict with fields to update "
                    "(name, enabled, cron_expr, timezone, description, wake_offset_seconds, targets, mode)."
                ),
                input_params={
                    "type": "object",
                    "properties": {
                        "job_id": {"type": "string", "description": "Job id to update"},
                        "patch": {
                            "type": "object",
                            "description": (
                                "Fields to update (name, enabled, cron_expr, timezone, "
                                "description, wake_offset_seconds, targets, mode)"
                            ),
                            "properties": {
                                "targets": {
                                    "type": "string",
                                    "enum": [e.value for e in CronTargetChannel],
                                    "description": (
                                        "推送频道：web/tui/feishu/dingtalk/whatsapp/wecom/xiaoyi/wechat"
                                    ),
                                },
                                "mode": {
                                    "type": "string",
                                    "enum": cron_job_modes_for_tools(),
                                    "description": "Agent runtime mode (agent, team, agent.plan, ...)",
                                },
                            },
                        },
                    },
                    "required": ["job_id", "patch"],
                },
                func=self._update_job_tool,
            ),
            make_tool(
                name="cron_delete_job",
                description="Delete a cron job by id. Returns True if deleted, False if not found.",
                input_params={
                    "type": "object",
                    "properties": {
                        "job_id": {"type": "string", "description": "Job id to delete"},
                    },
                    "required": ["job_id"],
                },
                func=self.delete_job,
            ),
            make_tool(
                name="cron_toggle_job",
                description="Enable or disable a cron job. Pass job_id and enabled (true/false).",
                input_params={
                    "type": "object",
                    "properties": {
                        "job_id": {"type": "string", "description": "Job id"},
                        "enabled": {
                            "type": "boolean",
                            "description": "Whether to enable the job",
                        },
                    },
                    "required": ["job_id", "enabled"],
                },
                func=self.toggle_job,
            ),
            make_tool(
                name="cron_preview_job",
                description=(
                    "Preview next N scheduled run times for a job. "
                    "Returns list of {wake_at, push_at} timestamps."
                ),
                input_params={
                    "type": "object",
                    "properties": {
                        "job_id": {"type": "string", "description": "Job id"},
                        "count": {
                            "type": "integer",
                            "description": "Number of runs to preview (1-50, default 5)",
                            "default": 5,
                        },
                    },
                    "required": ["job_id"],
                },
                func=self._preview_job_tool,
            ),
        ]


