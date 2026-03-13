from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, ClassVar, List

from openjiuwen.core.foundation.tool import LocalFunction, Tool, ToolCard
from zoneinfo import ZoneInfo

from jiuwenclaw.gateway.cron.models import CronTargetChannel
from jiuwenclaw.gateway.cron.scheduler import CronSchedulerService, _cron_next_push_dt
from jiuwenclaw.gateway.cron.store import CronJobStore


class CronController:
    """High-level cron API used by WebChannel handlers. Singleton."""

    _instance: ClassVar[CronController | None] = None

    def __init__(self, *, store: CronJobStore, scheduler: CronSchedulerService) -> None:
        self._store = store
        self._scheduler = scheduler
        self._target_channel: CronTargetChannel = CronTargetChannel.WEB

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
        _ = _cron_next_push_dt(cron_expr, base)

    _DESCRIPTION_TIME_KEYWORDS = ("每天", "每周", "每月", "上午", "下午", "早上", "晚上", "凌晨")

    def _normalize_targets(self, raw: Any) -> str:
        """将 targets 规范为 CronTargetChannel 枚举值，非法则默认 web。"""
        s = str(raw or "").strip() or self._target_channel.value
        try:
            CronTargetChannel(s)
            return s
        except ValueError:
            return CronTargetChannel.WEB.value

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


    async def list_jobs(self) -> list[dict[str, Any]]:
        jobs = await self._store.list_jobs()
        return [j.to_dict() for j in jobs]

    async def get_job(self, job_id: str) -> dict[str, Any] | None:
        job = await self._store.get_job(job_id)
        return job.to_dict() if job else None

    async def create_job(self, params: dict[str, Any]) -> dict[str, Any]:
        name = str(params.get("name") or "").strip()
        cron_expr = str(params.get("cron_expr") or "").strip()
        timezone = str(params.get("timezone") or "Asia/Shanghai").strip() or "Asia/Shanghai"
        enabled = bool(params.get("enabled", True))
        description = str(params.get("description") or "")
        wake_offset_seconds = params.get("wake_offset_seconds", None)
        raw_targets = params.get("targets")
        targets = self._normalize_targets(raw_targets)

        self._validate_schedule(cron_expr=cron_expr, timezone=timezone)
        description = self._normalize_description(description, name)

        job = await self._store.create_job(
            name=name,
            cron_expr=cron_expr,
            timezone=timezone,
            enabled=enabled,
            wake_offset_seconds=int(wake_offset_seconds) if wake_offset_seconds is not None else None,
            description=description,
            targets=targets,
        )
        await self._scheduler.reload()
        return job.to_dict()

    async def update_job(self, job_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        patch = dict(patch or {})
        if "targets" in patch:
            patch["targets"] = self._normalize_targets(patch["targets"])
        existing = await self._store.get_job(job_id)
        if existing is None:
            raise KeyError("job not found")
        if "cron_expr" in patch or "timezone" in patch:
            cron_expr = str(patch.get("cron_expr") or existing.cron_expr).strip()
            timezone = str(patch.get("timezone") or existing.timezone).strip()
            self._validate_schedule(cron_expr=cron_expr, timezone=timezone)
        if "description" in patch:
            name = str(patch.get("name") or existing.name or "").strip()
            patch["description"] = self._normalize_description(str(patch.get("description") or ""), name)

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
            push_dt = _cron_next_push_dt(job.cron_expr, push_dt)
            wake_dt = push_dt - timedelta(seconds=max(0, int(job.wake_offset_seconds or 0)))
            out.append({"wake_at": wake_dt.isoformat(), "push_at": push_dt.isoformat()})
        return out

    async def run_now(self, job_id: str) -> str:
        run_id = await self._scheduler.trigger_run_now(job_id)
        return run_id

    async def _create_job_tool(
        self,
        name: str,
        cron_expr: str,
        timezone: str,
        targets: str,
        enabled: bool = True,
        description: str = "",
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
                id=f"cron_{name}_{self._target_channel}",
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
                    "创建定时任务。当用户说「每天/每周/每月某时间做某事」时使用。"
                    "cron_expr 为 5 段：分 时 日 月 周。例：每天 9 点 = '0 9 * * *'，每天 11 点 58 分 = '58 11 * * *'，每周一 9 点 = '0 9 * * 1'。"
                    "description 只填任务内容，不要包含时间、频率等，时间由 cron_expr 表达。"
                    "timezone 默认 Asia/Shanghai。"
                ),
                input_params={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "任务名称"},
                        "cron_expr": {
                            "type": "string",
                            "description": "Cron 表达式，如每天9点10分用 '10 9 * * *'",
                        },
                        "timezone": {
                            "type": "string",
                            "description": "时区，如 Asia/Shanghai",
                            "default": "Asia/Shanghai",
                        },
                        "targets": {
                            "type": "string",
                            "enum": [e.value for e in CronTargetChannel],
                            "description": "推送频道：web=网页, feishu=飞书",
                            "default": self._target_channel.value,
                        },
                         "enabled": {
                            "type": "boolean",
                            "description": "是否启用",
                            "default": True,
                        },
                        "description": {
                            "type": "string",
                            "description": "具体任务名称，到点执行时发给助手，不能为空。不要包含时间/频率，例如填「搜索美国总统年龄」而非「每天上午11点58分搜索美国总统年龄」",
                        },
                        "wake_offset_seconds": {
                            "type": "integer",
                            "description": "提前多少秒执行，默认 300",
                            "default": 300,
                        },
                    },
                    "required": ["name", "cron_expr", "timezone", "description"],
                },
                func=self._create_job_tool,
            ),
            make_tool(
                name="cron_update_job",
                description="Update an existing cron job. Pass job_id and a patch dict with fields to update (name, enabled, cron_expr, timezone, description, wake_offset_seconds, targets).",
                input_params={
                    "type": "object",
                    "properties": {
                        "job_id": {"type": "string", "description": "Job id to update"},
                        "patch": {
                            "type": "object",
                            "description": "Fields to update (name, enabled, cron_expr, timezone, description, wake_offset_seconds, targets)",
                            "properties": {
                                "targets": {
                                    "type": "string",
                                    "enum": [e.value for e in CronTargetChannel],
                                    "description": "推送频道：web/feishu",
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
                description="Preview next N scheduled run times for a job. Returns list of {wake_at, push_at} timestamps.",
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