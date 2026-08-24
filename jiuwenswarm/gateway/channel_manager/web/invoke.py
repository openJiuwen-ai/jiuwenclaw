# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.

"""Unique WebChannel inbound dispatch (WS and HTTP share this pipeline)."""

from __future__ import annotations

import inspect
import logging
from typing import Any

from jiuwenswarm.common.schema.message import Message

logger = logging.getLogger(__name__)


async def dispatch_web_request(
    channel: Any,
    *,
    method: str,
    params: dict[str, Any],
    request_id: str,
    outbound: Any,
    session_id: str,
    user_message: Message,
) -> None:
    """Run HANDLER_BEFORE → on_message (or bus) → local handler / METHOD_NOT_FOUND.

    ``outbound`` is the reply target: a real WebSocket peer or request-scoped
    HTTP Outbound (``HttpJsonOutbound`` / ``HttpSseOutbound``).
    """
    # Lazy import: avoid import cycle with web_connect (which calls this module).
    from jiuwenswarm.gateway.channel_manager.web.web_rpc_host import (
        HANDLER_BEFORE_CALLBACK_METHODS as _HANDLER_BEFORE_CALLBACK_METHODS,
        MethodHandlerInvocation as _MethodHandlerInvocation,
    )

    handler = channel.rpc.method_handlers.get(method)
    handler_already_called = False
    if method in _HANDLER_BEFORE_CALLBACK_METHODS and handler is not None:
        handler_already_called = await channel.rpc.invoke_method_handler(
            _MethodHandlerInvocation(
                outbound, method, request_id, params, session_id, handler,
            ),
        )
        if not handler_already_called:
            return

    handled_by_callback = False
    on_message_cb = channel.rpc.on_message_cb
    if on_message_cb is not None:
        result = on_message_cb(user_message)
        if inspect.isawaitable(result):
            result = await result
        handled_by_callback = bool(result)
    else:
        bus = getattr(channel, "bus", None)
        if bus is not None and hasattr(bus, "publish_user_messages"):
            await bus.publish_user_messages(user_message)

    if handled_by_callback:
        return
    if handler_already_called:
        return

    if handler is not None:
        await channel.rpc.invoke_method_handler(
            _MethodHandlerInvocation(
                outbound, method, request_id, params, session_id, handler,
            ),
        )
    else:
        await channel.send_response(
            outbound,
            request_id,
            ok=False,
            error=f"unknown method: {method}",
            code="METHOD_NOT_FOUND",
        )
