"""Logical Celia session management."""

from __future__ import annotations

import asyncio

from .client import CeliaMcpClient


class CeliaSessionManager:
    def __init__(self, client: CeliaMcpClient) -> None:
        self.client = client
        self._open_sessions: set[str] = set()
        self._pending_opens: dict[str, asyncio.Task[str]] = {}
        client.add_restart_callback(self.clear)

    async def ensure_tool_session(self, user_id: str) -> str:
        session_id = f"tools-{user_id}"
        if session_id in self._open_sessions:
            return session_id
        pending = self._pending_opens.get(session_id)
        if pending is not None:
            return await pending

        async def _open() -> str:
            await self.client.call_tool(
                "memory_open",
                {"sessionId": session_id, "userId": user_id},
            )
            self._open_sessions.add(session_id)
            return session_id

        task = asyncio.create_task(_open(), name=f"celia-memory-open-{user_id}")
        self._pending_opens[session_id] = task
        try:
            return await task
        finally:
            if self._pending_opens.get(session_id) is task:
                self._pending_opens.pop(session_id, None)

    def clear(self) -> None:
        self._open_sessions.clear()
        for task in self._pending_opens.values():
            if not task.done():
                task.cancel()
        self._pending_opens.clear()

    def clear_user(self, user_id: str) -> None:
        self._open_sessions.discard(f"tools-{user_id}")
