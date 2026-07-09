# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Headless task dispatch from desktop — assign work to an avatar without opening the main window."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import uuid
from typing import Any, Callable

logger = logging.getLogger("jiuwenavatar.channels.desktop.task_dispatch")

CHANNEL_ID = "__desktop_quick_assign__"
_DEFAULT_DISPATCH_TIMEOUT = 30.0


def _generate_session_id() -> str:
    ts = format(int(time.time() * 1000), "x")
    rand = uuid.uuid4().hex[:6]
    return f"sess_{ts}_{rand}"


async def _send_ws_request(
    ws: Any,
    *,
    method: str,
    params: dict[str, Any] | None = None,
    timeout: float = _DEFAULT_DISPATCH_TIMEOUT,
) -> dict[str, Any]:
    request_id = f"desktop-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
    await ws.send(
        json.dumps(
            {
                "type": "req",
                "id": request_id,
                "method": method,
                "params": params or {},
            },
            ensure_ascii=False,
        )
    )
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        remaining = max(0.1, deadline - asyncio.get_running_loop().time())
        raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        data = json.loads(raw)
        if data.get("type") == "res" and data.get("id") == request_id:
            return data
    raise TimeoutError(f"Timed out waiting for Gateway response: method={method}")


async def dispatch_task_to_avatar_async(
    *,
    host: str,
    port: int,
    avatar_id: str,
    prompt: str,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Create a session and fire ``chat.send`` for *avatar_id* via Gateway WebSocket."""
    import websockets

    trimmed_avatar = (avatar_id or "").strip()
    trimmed_prompt = (prompt or "").strip()
    if not trimmed_avatar:
        raise ValueError("avatar_id is required")
    if not trimmed_prompt:
        raise ValueError("prompt is required")

    sid = (session_id or "").strip() or _generate_session_id()
    ws_url = f"ws://{host}:{port}/ws"

    async with websockets.connect(ws_url, open_timeout=10.0) as ws:
        # First frame: connection.ack
        await asyncio.wait_for(ws.recv(), timeout=10.0)

        create_res = await _send_ws_request(
            ws,
            method="session.create",
            params={
                "session_id": sid,
                "avatar_id": trimmed_avatar,
                "channel_id": CHANNEL_ID,
            },
        )
        if not create_res.get("ok"):
            err = create_res.get("error") or "session.create failed"
            if "already exists" not in str(err).lower():
                raise RuntimeError(str(err))

        chat_res = await _send_ws_request(
            ws,
            method="chat.send",
            params={
                "session_id": sid,
                "avatar_id": trimmed_avatar,
                "content": trimmed_prompt,
                "query": trimmed_prompt,
                "mode": "agent.plan",
            },
            timeout=_DEFAULT_DISPATCH_TIMEOUT,
        )
        if not chat_res.get("ok"):
            raise RuntimeError(str(chat_res.get("error") or "chat.send failed"))

        payload = chat_res.get("payload") if isinstance(chat_res.get("payload"), dict) else {}
        return {
            "ok": True,
            "session_id": sid,
            "accepted": bool(payload.get("accepted", True)),
        }


def dispatch_task_to_avatar(
    *,
    host: str,
    port: int,
    avatar_id: str,
    prompt: str,
    on_complete: Callable[[dict[str, Any]], None] | None = None,
    on_error: Callable[[Exception], None] | None = None,
) -> None:
    """Fire-and-forget wrapper for desktop UI threads."""

    def _run() -> None:
        try:
            result = asyncio.run(
                dispatch_task_to_avatar_async(
                    host=host,
                    port=port,
                    avatar_id=avatar_id,
                    prompt=prompt,
                )
            )
            logger.info(
                "[desktop-dispatch] task accepted avatar=%s session=%s",
                avatar_id,
                result.get("session_id"),
            )
            if on_complete is not None:
                on_complete(result)
        except Exception as exc:
            logger.exception("[desktop-dispatch] failed avatar=%s: %s", avatar_id, exc)
            if on_error is not None:
                on_error(exc)

    threading.Thread(
        target=_run,
        daemon=True,
        name=f"desktop-dispatch-{avatar_id[:8]}",
    ).start()
