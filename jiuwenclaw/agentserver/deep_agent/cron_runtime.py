from __future__ import annotations

import time
from copy import deepcopy
from typing import Any

from openjiuwen.harness.tools.cron import CronToolBackend, CronToolContext, create_cron_tools

from jiuwenclaw.gateway.cron import CronController, CronJobStore, CronSchedulerService, CronTargetChannel
from jiuwenclaw.gateway.message_handler import MessageHandler
from jiuwenclaw.schema.message import Message, ReqMethod
from jiuwenclaw.utils import logger


class _ControllerCronBackend(CronToolBackend):
    """Adapt the legacy gateway CronController to the DeepAgents cron backend interface."""

    def __init__(self, controller: CronController, message_handler: MessageHandler | None = None) -> None:
        self._controller = controller
        self._message_handler = message_handler

    async def list_jobs(self, *, include_disabled: bool = True) -> list[dict[str, Any]]:
        jobs = await self._controller.list_jobs()
        rows = [self._to_backend_job(job) for job in jobs]
        if include_disabled:
            return rows
        return [job for job in rows if job.get("enabled", True)]

    async def get_job(self, job_id: str) -> dict[str, Any] | None:
        job = await self._controller.get_job(job_id)
        if job is None:
            return None
        return self._to_backend_job(job)

    async def create_job(
        self,
        params: dict[str, Any],
        *,
        context: CronToolContext | None = None,
    ) -> dict[str, Any]:
        payload = self._to_legacy_create_params(params, context=context)
        job = await self._controller.create_job(payload)
        return self._to_backend_job(job)

    async def update_job(
        self,
        job_id: str,
        patch: dict[str, Any],
        *,
        context: CronToolContext | None = None,
    ) -> dict[str, Any]:
        payload = self._to_legacy_patch(patch, context=context)
        job = await self._controller.update_job(job_id, payload)
        return self._to_backend_job(job)

    async def delete_job(self, job_id: str) -> bool:
        return await self._controller.delete_job(job_id)

    async def toggle_job(self, job_id: str, enabled: bool) -> dict[str, Any]:
        job = await self._controller.toggle_job(job_id, enabled)
        return self._to_backend_job(job)

    async def preview_job(self, job_id: str, count: int = 5) -> list[dict[str, Any]]:
        return await self._controller.preview_job(job_id, count)

    async def run_now(self, job_id: str) -> str:
        return await self._controller.run_now(job_id)

    async def status(self) -> dict[str, Any]:
        scheduler = getattr(self._controller, "_scheduler", None)
        jobs = await self._controller.list_jobs()
        runs = self._serialize_runs()
        return {
            "running": bool(scheduler and getattr(scheduler, "is_running", lambda: False)()),
            "job_count": len(jobs),
            "run_count": len(runs),
        }

    async def get_runs(self, job_id: str, limit: int = 20) -> list[dict[str, Any]]:
        rows = [row for row in self._serialize_runs() if not job_id or row.get("job_id") == job_id]
        return rows[: max(1, min(int(limit), 100))]

    async def wake(
        self,
        text: str,
        *,
        context: CronToolContext | None = None,
        mode: str | None = None,
    ) -> dict[str, Any]:
        if not text.strip():
            raise ValueError("text is required")
        if context is None or not (context.channel_id or "").strip():
            raise ValueError("wake requires an active session context")
        if self._message_handler is None:
            raise RuntimeError("cron wake is unavailable before message handler startup")

        msg = Message(
            id=f"cron-wake-{int(time.time() * 1000)}",
            type="req",
            channel_id=context.channel_id,
            session_id=context.session_id,
            params={
                "query": text,
                "content": text,
                "mode": (mode or context.mode or "agent"),
            },
            timestamp=time.time(),
            ok=True,
            req_method=ReqMethod.CHAT_SEND,
            metadata=deepcopy(context.metadata) if isinstance(context.metadata, dict) else None,
        )
        await self._message_handler.publish_user_messages(msg)
        return {"queued": True}

    def _serialize_runs(self) -> list[dict[str, Any]]:
        scheduler = getattr(self._controller, "_scheduler", None)
        run_map = getattr(scheduler, "_runs", {}) if scheduler is not None else {}
        if not isinstance(run_map, dict):
            return []
        rows: list[dict[str, Any]] = []
        for state in run_map.values():
            if hasattr(state, "to_dict"):
                try:
                    rows.append(state.to_dict())
                    continue
                except Exception:
                    pass
            if hasattr(state, "__dict__"):
                rows.append({k: v for k, v in vars(state).items() if not k.startswith("_")})
        rows.sort(
            key=lambda item: item.get("started_at") or item.get("finished_at") or 0.0,
            reverse=True,
        )
        return rows

    @staticmethod
    def _to_backend_job(job: dict[str, Any]) -> dict[str, Any]:
        row = dict(job)
        row.setdefault(
            "schedule",
            {
                "kind": "cron",
                "expr": str(row.get("cron_expr") or "").strip(),
                "tz": str(row.get("timezone") or "Asia/Shanghai").strip() or "Asia/Shanghai",
            },
        )
        row.setdefault(
            "payload",
            {
                "kind": "agentTurn",
                "message": str(row.get("description") or "").strip(),
            },
        )
        row.setdefault(
            "delivery",
            {
                "mode": "announce",
                "channel": str(row.get("targets") or CronTargetChannel.WEB.value).strip() or CronTargetChannel.WEB.value,
            },
        )
        row.setdefault("session_target", "isolated")
        row.setdefault("compat_mode", "legacy")
        return row

    @staticmethod
    def _to_legacy_create_params(
        params: dict[str, Any],
        *,
        context: CronToolContext | None,
    ) -> dict[str, Any]:
        payload = dict(params or {})
        out = _extract_legacy_params(payload, context=context, require_schedule=True)
        return out

    @staticmethod
    def _to_legacy_patch(
        patch: dict[str, Any],
        *,
        context: CronToolContext | None,
    ) -> dict[str, Any]:
        payload = dict(patch or {})
        return _extract_legacy_params(payload, context=context, require_schedule=False)


def _extract_legacy_params(
    payload: dict[str, Any],
    *,
    context: CronToolContext | None,
    require_schedule: bool,
) -> dict[str, Any]:
    data = dict(payload or {})
    if "schedule" in data or "payload" in data or "delivery" in data:
        schedule = data.get("schedule") if isinstance(data.get("schedule"), dict) else {}
        kind = str(schedule.get("kind") or "cron").strip().lower()
        if kind and kind != "cron":
            raise ValueError("Only cron schedule is supported by the current gateway bridge")

        cron_expr = str(
            schedule.get("expr")
            or schedule.get("cron")
            or data.get("cron_expr")
            or ""
        ).strip()
        timezone = str(
            schedule.get("tz")
            or schedule.get("timezone")
            or data.get("timezone")
            or "Asia/Shanghai"
        ).strip() or "Asia/Shanghai"

        payload_block = data.get("payload") if isinstance(data.get("payload"), dict) else {}
        payload_kind = str(payload_block.get("kind") or "agentTurn").strip()
        if payload_kind and payload_kind != "agentTurn":
            raise ValueError("Only agentTurn cron jobs are supported by the current gateway bridge")
        description = str(
            payload_block.get("message")
            or data.get("description")
            or ""
        )

        delivery = data.get("delivery") if isinstance(data.get("delivery"), dict) else {}
        targets = str(
            delivery.get("channel")
            or data.get("targets")
            or (context.channel_id if context else "")
            or CronTargetChannel.WEB.value
        ).strip() or CronTargetChannel.WEB.value

        out: dict[str, Any] = {}
        if cron_expr or require_schedule:
            out["cron_expr"] = cron_expr
        if timezone or require_schedule:
            out["timezone"] = timezone
        if description:
            out["description"] = description
        if targets:
            out["targets"] = targets
        if "name" in data:
            out["name"] = str(data.get("name") or "").strip()
        if "enabled" in data:
            out["enabled"] = bool(data.get("enabled"))
        if "wake_offset_seconds" in data:
            out["wake_offset_seconds"] = data.get("wake_offset_seconds")
        return out

    return data


class CronRuntimeBridge:
    """Resolve the host cron backend for DeepAgents while keeping gateway diffs minimal."""

    def __init__(self) -> None:
        self._backend_override: CronToolBackend | None = None
        self._resolved_backend: CronToolBackend | None = None

    def set_backend(self, backend: CronToolBackend | None) -> None:
        self._backend_override = backend
        self._resolved_backend = backend

    def get_backend(self) -> CronToolBackend | None:
        if self._backend_override is not None:
            return self._backend_override
        if self._resolved_backend is not None:
            return self._resolved_backend

        controller = self._resolve_controller()
        if controller is None:
            return None

        message_handler = None
        try:
            message_handler = MessageHandler.get_instance()
        except RuntimeError:
            message_handler = None

        backend = _ControllerCronBackend(controller, message_handler=message_handler)
        self._resolved_backend = backend
        return backend

    def build_tools(self, *, context: Any) -> list[Any]:
        backend = self.get_backend()
        if backend is None:
            logger.warning("[CronRuntimeBridge] cron backend is not ready, skip builtin cron tools")
            return []
        return create_cron_tools(
            backend,
            context=context,
            target_channels=[channel.value for channel in CronTargetChannel],
            default_target_channel=CronTargetChannel.WEB.value,
        )

    @staticmethod
    def _resolve_controller() -> CronController | None:
        try:
            return CronController.get_instance()
        except RuntimeError:
            pass

        try:
            message_handler = MessageHandler.get_instance()
        except RuntimeError:
            return None

        agent_client = getattr(message_handler, "_agent_client", None)
        if agent_client is None:
            logger.warning("[CronRuntimeBridge] message handler is missing agent client")
            return None

        store = CronJobStore()
        scheduler = CronSchedulerService(
            store=store,
            agent_client=agent_client,
            message_handler=message_handler,
        )
        return CronController.get_instance(store=store, scheduler=scheduler)
