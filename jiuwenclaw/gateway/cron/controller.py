from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, ClassVar, List

from openjiuwen.core.foundation.tool import LocalFunction, Tool, ToolCard
from zoneinfo import ZoneInfo

from jiuwenclaw.gateway.cron.models import (
    CronTargetChannel,
    is_valid_target_channel_id,
    normalize_target_channel_id,
    upgrade_bare_feishu_target_for_multi_bot_config,
)
from jiuwenclaw.gateway.cron.enterprise_gate import (
    coerce_routing_id,
    enterprise_cron_enabled,
    extract_routing_triple,
    is_enterprise_edition,
    job_matches_routing,
    routing_triple_complete,
    strip_sticky_identity_fields,
)
from jiuwenclaw.gateway.cron.cron_expr import clamp_wake_offset_for_delay_seconds
from jiuwenclaw.gateway.cron.scheduler import CronSchedulerService, _cron_next_push_dt
from jiuwenclaw.gateway.cron.store_base import CronJobStoreBackend
from jiuwenclaw.utils import logger


class CronController:
    """High-level cron API used by WebChannel handlers. Singleton."""

    _instance: ClassVar[CronController | None] = None

    def __init__(self, *, store: CronJobStoreBackend, scheduler: CronSchedulerService) -> None:
        self._store = store
        self._scheduler = scheduler
        self._target_channel: CronTargetChannel | None = None

    def set_target_channel(self, channel: CronTargetChannel) -> None:
        self._target_channel = channel

    @classmethod
    def get_instance(
        cls,
        *,
        store: CronJobStoreBackend | None = None,
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
        _ = _cron_next_push_dt(cron_expr, base)

    @staticmethod
    def _clamp_wake_offset_for_upcoming_run(
        wake_offset_seconds: Any,
        *,
        cron_expr: str,
        timezone: str,
    ) -> Any:
        """企业创建兜底：wake_offset 不得超过距下次 push 的剩余秒数。"""
        try:
            tz = ZoneInfo(timezone)
            now = datetime.now(tz=tz)
            push_dt = _cron_next_push_dt(cron_expr, now)
            delay = float(push_dt.timestamp() - now.timestamp())
            if delay <= 0:
                return wake_offset_seconds
            clamped = clamp_wake_offset_for_delay_seconds(wake_offset_seconds, delay)
            if wake_offset_seconds is not None:
                try:
                    before = int(wake_offset_seconds)
                except (TypeError, ValueError):
                    before = wake_offset_seconds
            else:
                before = None
            if before != clamped:
                logger.info(
                    "[Cron] clamp wake_offset for upcoming run delay≈%.1fs: %s -> %s",
                    delay,
                    before,
                    clamped,
                )
            return clamped
        except Exception as exc:  # noqa: BLE001
            logger.debug("[Cron] skip wake_offset clamp: %s", exc)
            return wake_offset_seconds

    _DESCRIPTION_TIME_KEYWORDS = ("每天", "每周", "每月", "上午", "下午", "早上", "晚上", "凌晨")

    def _normalize_targets(self, raw: Any) -> str:
        """将 targets 规范为 CronTargetChannel 枚举值。"""
        raw_s = str(raw or "").strip()
        if self._target_channel is None and not raw_s:
            raise ValueError("targets is required when target_channel is not set")
        if not raw_s:
            return upgrade_bare_feishu_target_for_multi_bot_config(
                normalize_target_channel_id(self._target_channel.value),
            )
        if not is_valid_target_channel_id(raw_s):
            raise ValueError("targets must be one of web/feishu/whatsapp/wecom/xiaoyi or feishu:<app_id>")
        return upgrade_bare_feishu_target_for_multi_bot_config(normalize_target_channel_id(raw_s))

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
    def _routing_session_id_for_targets(targets: str, raw: Any) -> str | None:
        """Persist delivery session for web and feishu:<app_id> targets."""
        targets_s = str(targets or "").strip()
        if targets_s == "web":
            if isinstance(raw, str):
                s = raw.strip()
                return s or None
            return None
        if not targets_s.startswith("feishu:"):
            return None
        if not isinstance(raw, str):
            return None
        s = raw.strip()
        if not s or "::" not in s:
            return None
        parts = s.split("::")
        if len(parts) < 3 or parts[0] != "feishu":
            return None
        return s

    @staticmethod
    def _parse_list_filters(params: dict[str, Any] | None) -> dict[str, Any]:
        params = dict(params or {})
        g, b, u = extract_routing_triple(params)
        out: dict[str, Any] = {}
        if g:
            out["group_id"] = g
        if b:
            out["bot_id"] = b
        if u:
            out["user_id"] = u
        match = str(params.get("match") or "and").strip().lower() or "and"
        out["match"] = match if match in ("and", "or") else "and"
        out["include_unbound"] = bool(params.get("include_unbound", False))
        return out

    @staticmethod
    def _require_enterprise_routing(params: dict[str, Any] | None) -> tuple[str, str, str]:
        """企业就绪时要求三元组齐全；未就绪企业版写路径拒绝。"""
        if is_enterprise_edition() and not enterprise_cron_enabled():
            raise PermissionError(
                "enterprise cron is not ready: jiuwenclaw_id not bound "
                "(refuse write path; will not fall back to file/Redis)"
            )
        g, b, u = extract_routing_triple(params or {})
        if enterprise_cron_enabled():
            if not g or not b or not u:
                raise ValueError(
                    "enterprise cron requires group_id, bot_id and user_id"
                )
            return g, b, u
        return g or "", b or "", u or ""

    @staticmethod
    def _assert_job_owned(
        job: Any,
        *,
        group_id: str | None,
        bot_id: str | None,
        user_id: str | None,
    ) -> None:
        """企业强制隔离：跨租户按 NOT_FOUND 处理，防枚举。"""
        if not enterprise_cron_enabled():
            return
        if not routing_triple_complete(group_id, bot_id, user_id):
            raise KeyError("job not found")
        if not job_matches_routing(
            job,
            group_id=group_id,
            bot_id=bot_id,
            user_id=user_id,
            match="and",
            include_unbound=False,
        ):
            raise KeyError("job not found")

    async def list_jobs(self, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        parsed = self._parse_list_filters(filters)
        if enterprise_cron_enabled():
            if not routing_triple_complete(
                parsed.get("group_id"),
                parsed.get("bot_id"),
                parsed.get("user_id"),
            ):
                raise ValueError(
                    "enterprise cron list requires group_id, bot_id and user_id"
                )
            # 强制 AND 隔离；请求更宽（缺维）已拒绝；更严子集允许
            store_filters = {
                "group_id": parsed["group_id"],
                "bot_id": parsed["bot_id"],
                "user_id": parsed["user_id"],
            }
            jobs = await self._store.list_jobs(filters=store_filters)
            return [j.to_dict() for j in jobs]

        # 非企业：file/Redis 全量；不向非企业 store 传 filters
        jobs = await self._store.list_jobs()
        return [j.to_dict() for j in jobs]

    async def get_job(
        self,
        job_id: str,
        *,
        group_id: str | None = None,
        bot_id: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any] | None:
        job = await self._store.get_job(job_id)
        if job is None:
            return None
        try:
            self._assert_job_owned(job, group_id=group_id, bot_id=bot_id, user_id=user_id)
        except KeyError:
            return None
        return job.to_dict()

    async def create_job(self, params: dict[str, Any]) -> dict[str, Any]:
        params = dict(params or {})
        name = str(params.get("name") or "").strip()
        cron_expr = str(params.get("cron_expr") or "").strip()
        timezone = str(params.get("timezone") or "Asia/Shanghai").strip() or "Asia/Shanghai"
        enabled = bool(params.get("enabled", True))
        description = str(params.get("description") or "")
        wake_offset_seconds = params.get("wake_offset_seconds", None)
        raw_targets = params.get("targets")
        mode = params.get("mode")
        targets = self._normalize_targets(raw_targets)

        self._validate_schedule(cron_expr=cron_expr, timezone=timezone)
        description = self._normalize_description(description, name)

        # 企业：按距下次触发的剩余秒数收敛 wake_offset（兜底 Agent/harness 默认 300）
        if is_enterprise_edition():
            wake_offset_seconds = self._clamp_wake_offset_for_upcoming_run(
                wake_offset_seconds,
                cron_expr=cron_expr,
                timezone=timezone,
            )

        routing_sid = self._routing_session_id_for_targets(targets, params.get("session_id"))
        chat_type = params.get("chat_type")
        delete_after_run = params.get("delete_after_run")

        g, b, u = self._require_enterprise_routing(params)
        group_id = g or coerce_routing_id(params.get("group_id"))
        bot_id = b or coerce_routing_id(params.get("bot_id"))
        user_id = u or coerce_routing_id(params.get("user_id"))

        create_kwargs: dict[str, Any] = {
            "job_id": str(params.get("id") or "").strip() or None,
            "name": name,
            "cron_expr": cron_expr,
            "timezone": timezone,
            "enabled": enabled,
            "wake_offset_seconds": int(wake_offset_seconds) if wake_offset_seconds is not None else None,
            "description": description,
            "targets": targets,
            "session_id": routing_sid,
            "chat_type": chat_type,
            "mode": mode,
            "delete_after_run": delete_after_run,
        }
        # 三元组仅企业 DB store 落库；非企业 file/Redis 保持原 create 签名
        if enterprise_cron_enabled():
            create_kwargs["group_id"] = group_id
            create_kwargs["bot_id"] = bot_id
            create_kwargs["user_id"] = user_id

        job = await self._store.create_job(**create_kwargs)
        await self._scheduler.reload()
        return job.to_dict()

    async def update_job(
        self,
        job_id: str,
        patch: dict[str, Any],
        *,
        group_id: str | None = None,
        bot_id: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        if is_enterprise_edition() and not enterprise_cron_enabled():
            raise PermissionError(
                "enterprise cron is not ready: jiuwenclaw_id not bound"
            )
        patch = strip_sticky_identity_fields(dict(patch or {}))
        if "targets" in patch:
            patch["targets"] = self._normalize_targets(patch["targets"])
        existing = await self._store.get_job(job_id)
        if existing is None:
            raise KeyError("job not found")
        self._assert_job_owned(existing, group_id=group_id, bot_id=bot_id, user_id=user_id)
        if "cron_expr" in patch or "timezone" in patch:
            cron_expr = str(patch.get("cron_expr") or existing.cron_expr).strip()
            timezone = str(patch.get("timezone") or existing.timezone).strip()
            self._validate_schedule(cron_expr=cron_expr, timezone=timezone)
        if "description" in patch:
            name = str(patch.get("name") or existing.name or "").strip()
            patch["description"] = self._normalize_description(str(patch.get("description") or ""), name)

        final_targets = str(patch.get("targets") or existing.targets).strip()
        if "session_id" in patch:
            patch["session_id"] = self._routing_session_id_for_targets(
                final_targets, patch.get("session_id")
            )

        job = await self._store.update_job(job_id, patch)
        await self._scheduler.reload()
        return job.to_dict()

    async def delete_job(
        self,
        job_id: str,
        *,
        group_id: str | None = None,
        bot_id: str | None = None,
        user_id: str | None = None,
        skip_ownership: bool = False,
    ) -> bool:
        if is_enterprise_edition() and not enterprise_cron_enabled():
            raise PermissionError(
                "enterprise cron is not ready: jiuwenclaw_id not bound"
            )
        existing = await self._store.get_job(job_id)
        if existing is None:
            return False
        if not skip_ownership:
            self._assert_job_owned(existing, group_id=group_id, bot_id=bot_id, user_id=user_id)
        deleted = await self._store.delete_job(job_id)
        if deleted:
            await self._scheduler.reload()
        return deleted

    async def toggle_job(
        self,
        job_id: str,
        enabled: bool,
        *,
        group_id: str | None = None,
        bot_id: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        return await self.update_job(
            job_id,
            {"enabled": bool(enabled)},
            group_id=group_id,
            bot_id=bot_id,
            user_id=user_id,
        )

    async def preview_job(
        self,
        job_id: str,
        count: int = 5,
        *,
        group_id: str | None = None,
        bot_id: str | None = None,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        job = await self._store.get_job(job_id)
        if job is None:
            raise KeyError("job not found")
        self._assert_job_owned(job, group_id=group_id, bot_id=bot_id, user_id=user_id)
        count = max(1, min(int(count), 50))

        tz = ZoneInfo(job.timezone)
        base = datetime.now(tz=tz)
        out: list[dict[str, Any]] = []
        push_dt = base
        for _ in range(count):
            try:
                push_dt = _cron_next_push_dt(job.cron_expr, push_dt)
            except Exception as exc:  # noqa: BLE001
                # croniter 对“单次且已到/已用完”的 7 段表达式会抛错。
                # 预览场景希望返回已有结果（通常为 1 条），而不是让工具调用失败。
                _msg = str(exc)
                if "CroniterBadDateError" in _msg or "failed to find next date" in _msg:
                    break
                raise
            wake_dt = push_dt - timedelta(seconds=max(0, int(job.wake_offset_seconds or 0)))
            out.append({"wake_at": wake_dt.isoformat(), "push_at": push_dt.isoformat()})
        return out

    async def run_now(
        self,
        job_id: str,
        *,
        group_id: str | None = None,
        bot_id: str | None = None,
        user_id: str | None = None,
    ) -> str:
        job = await self._store.get_job(job_id)
        if job is None:
            raise KeyError("job not found")
        self._assert_job_owned(job, group_id=group_id, bot_id=bot_id, user_id=user_id)
        run_id = await self._scheduler.trigger_run_now(job_id)
        return run_id

    async def _create_job_tool(
        self,
        name: str,
        cron_expr: str,
        timezone: str,
        description: str,
        targets: str = "",
        enabled: bool = True,
        wake_offset_seconds: int | None = None,
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
                description="List all cron jobs. Returns a list of job objects with id, name, cron_expr, timezone, enabled, etc.",
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
                    "cron_expr:\n"
                    "- Recurring (5 fields): minute hour day month day-of-week.\n"
                    "  Example: daily 9:00 = '0 9 * * *', every Monday 9:00 = '0 9 * * 1'.\n"
                    "- Relative time (e.g. \"in X minutes\"): take now in the given timezone, "
                    "compute run_at = now + X minutes, then encode run_at as 7-field cron "
                    "with a fixed year (minute hour day month day-of-week second year). "
                    "Example: run_at (Mar 19, 2026 10:07:00 local) -> '0 7 10 19 3 * 2026'.\n"
                    "- One-shot (runs only once): must use 7 fields with a fixed year: "
                    "minute hour day month day-of-week second year. "
                    "Example: 2026-03-28 17:00 (local) -> '0 17 28 3 * 0 2026'.\n"
                    "Warning: if you use a 5-field expression with fixed day/month "
                    "but year semantics implicitly '*', it will repeat every year; "
                    "for a real one-shot, use the 7-field form with a fixed year.\n"
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
                                "Cron expression. "
                                "Recurring jobs use 5 fields: minute hour dom month day-of-week. "
                                "One-shot jobs must use 7 fields: minute hour dom month "
                                "day-of-week second year (fixed year). "
                                "For relative time, treat it as one-shot: compute run_at = now + X minutes, "
                                "then encode it as a 7-field expression with a fixed year. "
                                "Example: 2026-03-28 17:00 (local) -> '0 17 28 3 * 0 2026'."
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
                            "description": "Delivery channel: web, feishu, whatsapp, wecom, xiaoyi. "
                                           "If omitted, use the current request source channel.",
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
                            "description": "Seconds to wake before push. Default 60",
                            "default": 60,
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
                    "(name, enabled, cron_expr, timezone, description, wake_offset_seconds, targets)."
                ),
                input_params={
                    "type": "object",
                    "properties": {
                        "job_id": {"type": "string", "description": "Job id to update"},
                        "patch": {
                            "type": "object",
                            "description": (
                                "Fields to update (name, enabled, cron_expr, timezone, "
                                "description, wake_offset_seconds, targets)"
                            ),
                            "properties": {
                                "targets": {
                                    "type": "string",
                                    "enum": [e.value for e in CronTargetChannel],
                                    "description": "推送频道：web/feishu/whatsapp",
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
