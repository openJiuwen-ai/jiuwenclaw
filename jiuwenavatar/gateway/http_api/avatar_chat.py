# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""HTTP API for invoking digital avatars via POST /avatar/chat.

External clients can call::

    POST /avatar/chat
    {
      "avatar_id": "committerXXX",
      "params": {"query": "帮我检视xxxxxx"}
    }

Also accepts the typo key ``atavar_id`` for compatibility.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from aiohttp import web

from jiuwenavatar.common.e2a.gateway_normalize import e2a_from_agent_fields
from jiuwenavatar.common.enterprise import make_service_id, merge_routing
from jiuwenavatar.common.schema.message import ReqMethod
from jiuwenavatar.gateway.trigger.engine import TriggerEngine

logger = logging.getLogger(__name__)


def _json_dumps(obj: Any) -> str:
    """Serialize JSON with readable non-ASCII (Chinese) characters."""
    return json.dumps(obj, ensure_ascii=False)


def _dispatch_timeout() -> float:
    """Timeout (seconds) for avatar HTTP chat; defaults to trigger dispatch timeout."""
    default = 1800.0
    raw = (
        os.getenv("JIUWENAVATAR_AVATAR_CHAT_TIMEOUT")
        or os.getenv("JIUWENAVATAR_TRIGGER_DISPATCH_TIMEOUT")
    )
    if not raw:
        return default
    try:
        value = float(raw)
        return value if value > 0 else default
    except (TypeError, ValueError):
        return default


def _resolve_avatar_route(avatar_id: str) -> dict[str, str]:
    """Resolve cloud routing fields for an Avatar, keeping standalone fallback."""
    try:
        from jiuwenavatar.server.runtime.persona import PersonaManager

        avatar = PersonaManager.get_instance().get_avatar(avatar_id) or {}
    except Exception:  # noqa: BLE001
        avatar = {}

    group_id = str(avatar.get("group_id") or "").strip()
    owner_user_id = str(avatar.get("owner_user_id") or "").strip()
    service_id = str(avatar.get("service_id") or "").strip()
    if not service_id and avatar_id:
        service_id = make_service_id(group_id or "default", avatar_id)
    agent_id = str(avatar.get("agent_id") or owner_user_id or "").strip()
    return {
        "service_id": service_id,
        "agent_id": agent_id,
        "group_id": group_id,
        "owner_user_id": owner_user_id,
    }


def _resolve_avatar_id(raw_id: str) -> str:
    """Resolve caller-provided avatar key to a concrete avatar id.

    Accepts exact avatar id, or looks up by avatar display name when PersonaManager
    is available in-process (AgentServer). Falls back to the raw value.
    """
    key = (raw_id or "").strip()
    if not key:
        return ""
    try:
        from jiuwenavatar.server.runtime.persona import PersonaManager

        manager = PersonaManager.get_instance()
        manager.ensure_loaded()
        if manager.get_avatar(key):
            return key
        # Prefer exact name match, then case-insensitive.
        exact: str | None = None
        fuzzy: str | None = None
        for item in manager.list_avatars():
            name = str(item.get("name") or "").strip()
            avatar_id = str(item.get("id") or "").strip()
            if not avatar_id:
                continue
            if name == key:
                exact = avatar_id
                break
            if fuzzy is None and name.lower() == key.lower():
                fuzzy = avatar_id
        return exact or fuzzy or key
    except Exception:  # noqa: BLE001
        return key


def _extract_query(params: dict[str, Any]) -> str:
    """Pick the user query text from params (query / content / prompt / text)."""
    for key in ("query", "content", "prompt", "text", "message"):
        value = params.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _normalize_final_reply(content: str) -> str:
    """Collapse interrupt/tool-approval noise into a short human-readable reply."""
    text = (content or "").strip()
    if not text:
        return ""
    # Tool-approval interrupts often arrive as a Python-repr blob; surface a clear hint.
    if "result_type" in text and "interrupt" in text and "ToolCallInterruptRequest" in text:
        tool_names: list[str] = []
        marker = "tool_name='"
        start = 0
        while True:
            idx = text.find(marker, start)
            if idx < 0:
                break
            end = text.find("'", idx + len(marker))
            if end < 0:
                break
            name = text[idx + len(marker) : end].strip()
            if name and name not in tool_names:
                tool_names.append(name)
            start = end + 1
        if tool_names:
            joined = "、".join(tool_names)
            return f"需要授权后才能继续：工具 {joined} 等待确认（HTTP 接口无法完成交互授权，请在 Web 端批准或调整安全策略后重试）。"
        return "需要授权后才能继续（HTTP 接口无法完成交互授权，请在 Web 端批准或调整安全策略后重试）。"
    return text


class AvatarChatService:
    """Dispatch non-streaming chat.send to AgentServer for a given avatar_id."""

    def __init__(self, agent_client: Any) -> None:
        self._agent_client = agent_client

    async def chat(
        self,
        *,
        avatar_id: str,
        params: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        avatar_id = (avatar_id or "").strip()
        if not avatar_id:
            return {"error": "avatar_id is required", "status": 400}
        avatar_id = _resolve_avatar_id(avatar_id)

        params = dict(params or {})
        query = _extract_query(params)
        if not query:
            return {
                "error": "params.query (or content/prompt) is required",
                "status": 400,
            }

        if self._agent_client is None:
            return {"error": "agent client not configured", "status": 503}

        ts = format(int(time.time() * 1000), "x")
        run_id = f"http-avatar-{avatar_id}-{ts}"
        resolved_session = (session_id or "").strip() or f"http_avatar_{ts}_{avatar_id}"
        route = _resolve_avatar_route(avatar_id)
        mode = str(params.get("mode") or "agent").strip() or "agent"

        chat_params: dict[str, Any] = {
            "avatar_id": avatar_id,
            "content": query,
            "query": query,
            "mode": mode,
        }
        # Forward extra params except reserved keys / query aliases.
        for key, value in params.items():
            if key in {"query", "content", "prompt", "text", "message", "mode"}:
                continue
            chat_params[key] = value

        envelope = e2a_from_agent_fields(
            request_id=run_id,
            channel_id="__http_avatar__",
            session_id=resolved_session,
            req_method=ReqMethod.CHAT_SEND,
            params=merge_routing(
                chat_params,
                service_id=route.get("service_id", ""),
                agent_id=route.get("agent_id", ""),
                avatar_id=avatar_id,
                group_id=route.get("group_id", ""),
                user_id=route.get("owner_user_id", ""),
            ),
            is_stream=False,
            timestamp=time.time(),
            metadata={"http_avatar": {"avatar_id": avatar_id, "run_id": run_id}},
        )

        try:
            resp = await self._agent_client.send_request(envelope, timeout=_dispatch_timeout())
        except Exception as exc:  # noqa: BLE001
            logger.exception("[AvatarHTTP] chat failed avatar_id=%s", avatar_id)
            return {"error": str(exc), "status": 502}

        payload = getattr(resp, "payload", None)
        content = TriggerEngine._extract_text(payload)
        content = _normalize_final_reply(content)
        ok = bool(getattr(resp, "ok", False))
        # External HTTP clients only need the final answer text.
        if ok:
            return {"content": content, "status": 200}
        return {
            "error": content or "agent returned not-ok",
            "status": 502,
        }


def build_avatar_http_app(
    agent_client: Any,
    *,
    include_webhook: bool = False,
    webhook_handler: Any | None = None,
) -> web.Application:
    """Build aiohttp app exposing POST /avatar/chat (and optional webhook routes)."""
    service = AvatarChatService(agent_client)
    app = web.Application()

    async def _health(_request: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "avatar_http": True}, dumps=_json_dumps)

    async def _avatar_chat(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return web.json_response(
                {"error": "invalid JSON body"},
                status=400,
                dumps=_json_dumps,
            )

        if not isinstance(body, dict):
            return web.json_response(
                {"error": "body must be a JSON object"},
                status=400,
                dumps=_json_dumps,
            )

        # Accept typo "atavar_id" from callers.
        avatar_id = str(
            body.get("avatar_id")
            or body.get("atavar_id")
            or ""
        ).strip()
        params = body.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return web.json_response(
                {"error": "params must be an object"},
                status=400,
                dumps=_json_dumps,
            )

        session_id = body.get("session_id")
        session_id_str = str(session_id).strip() if session_id is not None else None

        result = await service.chat(
            avatar_id=avatar_id,
            params=params,
            session_id=session_id_str,
        )
        status = int(result.pop("status", 200))
        return web.json_response(result, status=status, dumps=_json_dumps)

    app.router.add_get("/avatar/health", _health)
    app.router.add_post("/avatar/chat", _avatar_chat)

    if include_webhook and webhook_handler is not None:
        app.router.add_post("/webhook/{path:.*}", webhook_handler)

        async def _webhook_health(_request: web.Request) -> web.Response:
            return web.json_response({"status": "ok", "webhook": True}, dumps=_json_dumps)

        app.router.add_get("/webhook/health", _webhook_health)

    return app
