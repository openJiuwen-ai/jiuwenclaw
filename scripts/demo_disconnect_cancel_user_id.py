"""Show that disconnect cancellation keeps the WebSocket user ID."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from jiuwenswarm.gateway.message_handler.message_handler import MessageHandler


class DemoAgentClient:
    last_request: Any = None

    @classmethod
    async def send_request(cls, request: Any) -> SimpleNamespace:
        cls.last_request = request
        return SimpleNamespace(
            request_id=request.request_id,
            channel_id=request.channel,
            ok=True,
            payload={"event_type": "chat.interrupt_result", "success": True},
            metadata=None,
        )

    @staticmethod
    async def send_request_stream(request: Any):
        if False:
            yield request


class DemoMessageHandler(MessageHandler):
    pass


async def main() -> None:
    handler = DemoMessageHandler(DemoAgentClient())
    cleaned = await handler.cancel_agent_sessions_on_disconnect(
        [("tui", "demo-session")],
        user_id="demo-user",
    )

    request = DemoAgentClient.last_request
    assert cleaned is True
    assert request is not None
    assert request.user_id == "demo-user"
    print(
        "disconnect cancel user_id preserved:",
        request.user_id,
    )


if __name__ == "__main__":
    asyncio.run(main())
