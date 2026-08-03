from __future__ import annotations

from typing import Any

from openjiuwen.core.foundation.tool import LocalFunction, Tool, ToolCard

from jiuwenswarm.server.gateway_push import (
    GatewayPushTransport,
    WebSocketGatewayPushTransport,
)


HEARTBEAT_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "heartbeat_list_jobs",
        "heartbeat_get_job",
        "heartbeat_create_job",
        "heartbeat_update_job",
        "heartbeat_delete_job",
        "heartbeat_toggle_job",
        "heartbeat_preview_job",
        "heartbeat_run_now",
        "heartbeat_cancel_run",
    }
)


class HeartbeatRuntimeBridge:
    """Agent-side heartbeat tools forwarding authoritative mutations to Gateway."""

    def __init__(self, gateway_push: GatewayPushTransport | None = None) -> None:
        self._gateway_push = gateway_push or WebSocketGatewayPushTransport()

    async def _send(
        self, context: Any, action: str, data: dict[str, Any]
    ) -> dict[str, Any]:
        from jiuwenswarm.common.e2a.constants import E2A_RESPONSE_KIND_HEARTBEAT

        metadata = context.metadata if isinstance(context.metadata, dict) else {}
        request_id = str(metadata.get("request_id") or "").strip()
        channel_id = str(context.channel_id or "web").strip() or "web"
        session_id = str(context.session_id or "").strip()
        if not request_id or not session_id:
            raise ValueError("heartbeat tools require an active session context")
        message = {
            "request_id": request_id,
            "channel_id": channel_id,
            "session_id": session_id,
            "response_kind": E2A_RESPONSE_KIND_HEARTBEAT,
            "body": {
                "action": action,
                "status": "ok",
                "data": dict(data or {}),
                "message": "",
            },
        }
        request = getattr(self._gateway_push, "request", None)
        if not callable(request):
            raise RuntimeError(
                "heartbeat transport does not support authoritative responses"
            )
        response = await request(message, timeout_seconds=15.0)
        if not isinstance(response, dict):
            raise RuntimeError("invalid heartbeat response from gateway")
        if response.get("ok") is not True:
            code = str(response.get("code") or "INTERNAL_ERROR")
            error = str(response.get("error") or "heartbeat operation failed")
            raise RuntimeError(f"{code}: {error}")
        data_out = response.get("data")
        return dict(data_out) if isinstance(data_out, dict) else {"result": data_out}

    def build_tools(self, *, context: Any) -> list[Tool]:
        def tool(name: str, description: str, schema: dict[str, Any], func: Any) -> Tool:
            return LocalFunction(
                card=ToolCard(name=name, description=description, input_params=schema),
                func=func,
            )

        async def list_jobs(scope: str = "current", **_: Any) -> dict[str, Any]:
            return await self._send(context, "list", {"scope": scope})

        async def get_job(job_id: str, **_: Any) -> dict[str, Any]:
            return await self._send(context, "get", {"job_id": job_id})

        async def create_job(**kwargs: Any) -> dict[str, Any]:
            return await self._send(context, "create", kwargs)

        async def update_job(job_id: str, patch: dict[str, Any], **_: Any) -> dict[str, Any]:
            return await self._send(context, "update", {"job_id": job_id, "patch": patch})

        async def delete_job(job_id: str, **_: Any) -> dict[str, Any]:
            return await self._send(context, "delete", {"job_id": job_id})

        async def toggle_job(job_id: str, enabled: bool, **_: Any) -> dict[str, Any]:
            return await self._send(
                context, "toggle", {"job_id": job_id, "enabled": enabled}
            )

        async def preview_job(job_id: str, count: int = 5, **_: Any) -> dict[str, Any]:
            return await self._send(
                context, "preview", {"job_id": job_id, "count": count}
            )

        async def run_now(
            job_id: str, reschedule: bool = False, **_: Any
        ) -> dict[str, Any]:
            return await self._send(
                context,
                "run_now",
                {"job_id": job_id, "reschedule": reschedule},
            )

        async def cancel_run(
            job_id: str, pause_schedule: bool = False, **_: Any
        ) -> dict[str, Any]:
            return await self._send(
                context,
                "cancel",
                {"job_id": job_id, "pause_schedule": pause_schedule},
            )

        schedule = {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["interval", "cron", "once"]},
                "interval_seconds": {"type": "integer"},
                "cron_expr": {"type": "string"},
                "timezone": {"type": "string"},
                "run_at": {"type": "number"},
            },
            "required": ["type"],
        }
        job_id_schema = {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
        }
        return [
            tool(
                "heartbeat_list_jobs",
                "List heartbeat follow-up jobs for the current session.",
                {"type": "object", "properties": {"scope": {"type": "string", "enum": ["current", "all_visible"]}}},
                list_jobs,
            ),
            tool("heartbeat_get_job", "Get a heartbeat job in the current session.", job_id_schema, get_job),
            tool(
                "heartbeat_create_job",
                "Create a heartbeat follow-up job bound to the current conversation/session. "
                "Use it only to return later to continue the existing task with the original "
                "conversation and runtime configuration. For standalone daily reports, "
                "periodic notifications, or independent saved-prompt tasks, use "
                "cron_create_job instead. When the followed task is complete, actually stop "
                "the schedule with heartbeat_update_job(enabled=false) or "
                "heartbeat_cancel_run(pause_schedule=true).",
                {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "prompt": {
                            "type": "string",
                            "description": (
                                "Follow-up prompt for the current session. It should require "
                                "the future run to stop the schedule when work is complete."
                            ),
                        },
                        "schedule": schedule,
                        "max_runs": {"type": "integer"},
                        "delete_after_run": {"type": "boolean"},
                        "concurrency_policy": {"type": "string", "enum": ["skip", "queue", "replace"]},
                        "enabled": {"type": "boolean"},
                    },
                    "required": ["name", "prompt", "schedule"],
                },
                create_job,
            ),
            tool(
                "heartbeat_update_job",
                "Update a heartbeat job in the current session.",
                {"type": "object", "properties": {"job_id": {"type": "string"}, "patch": {"type": "object"}}, "required": ["job_id", "patch"]},
                update_job,
            ),
            tool("heartbeat_delete_job", "Delete a heartbeat job in the current session.", job_id_schema, delete_job),
            tool(
                "heartbeat_toggle_job",
                "Enable or disable a heartbeat job.",
                {"type": "object", "properties": {"job_id": {"type": "string"}, "enabled": {"type": "boolean"}}, "required": ["job_id", "enabled"]},
                toggle_job,
            ),
            tool(
                "heartbeat_preview_job",
                "Preview future heartbeat trigger times.",
                {"type": "object", "properties": {"job_id": {"type": "string"}, "count": {"type": "integer"}}, "required": ["job_id"]},
                preview_job,
            ),
            tool(
                "heartbeat_run_now",
                "Run a heartbeat job now; reschedule=false preserves its schedule.",
                {"type": "object", "properties": {"job_id": {"type": "string"}, "reschedule": {"type": "boolean"}}, "required": ["job_id"]},
                run_now,
            ),
            tool(
                "heartbeat_cancel_run",
                "Cancel only the current heartbeat run; optionally pause future scheduling.",
                {"type": "object", "properties": {"job_id": {"type": "string"}, "pause_schedule": {"type": "boolean"}}, "required": ["job_id"]},
                cancel_run,
            ),
        ]
