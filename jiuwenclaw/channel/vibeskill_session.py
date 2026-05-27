from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

VIBESKILL_CHANNEL_ID = "vibeskill"
_VIBESKILL_ORIGINAL_SESSION_ID_KEY = "vibeskill_original_session_id"


class VibeSkillSessionState(str, Enum):
    """VibeSkill Session 状态。"""

    IDLE = "idle"
    BUSY = "busy"
    COMPLETED = "completed"
    RETRY = "retry"


@dataclass
class VibeSkillSession:
    """VibeSkill Session 状态和元数据。"""

    session_id: str = ""
    state: VibeSkillSessionState = VibeSkillSessionState.IDLE
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)
    mode: str = "SkillCreate"  # "SkillCreate" or "Standard"


class VibeSkillSessionStore:
    """VibeSkill Session 状态管理器。

    统一管理 VibeSkill 的 session 状态。
    由 VibeSkillChannel 持有实例，不使用单例模式。

    Session 状态转换：
        idle ──(message.send)──► busy
        busy ──(skilldev.agent_completed)──► idle
        busy ──(skilldev.completed)──► completed
        completed ──(message.send)──► busy
        busy ──(chat.final/cancel)──► idle
        busy ──(skilldev.error)──► idle
        retry ──(message.send)──► busy
    """

    def __init__(self) -> None:
        self._sessions: dict[str, VibeSkillSession] = {}
        self._lock = asyncio.Lock()

    async def get_or_create(
        self,
        session_id: str | None = None,
        mode: str = "SkillCreate",
    ) -> VibeSkillSession:
        """获取或创建 session。

        优先按 session_id 查找，都找不到则创建新的。
        """
        async with self._lock:
            if session_id and session_id in self._sessions:
                return self._sessions[session_id]

            sid = session_id or f"vibeskill_{secrets.token_hex(6)}"
            session = VibeSkillSession(session_id=sid, mode=mode)
            self._sessions[sid] = session
            return session

    async def set_state(self, session_id: str, state: VibeSkillSessionState) -> None:
        """更新 session 状态。"""
        async with self._lock:
            if session_id in self._sessions:
                self._sessions[session_id].state = state
                self._sessions[session_id].updated_at = time.time()

    async def set_metadata(self, session_id: str, metadata: dict[str, Any]) -> None:
        """更新 session 元数据。"""
        async with self._lock:
            if session_id in self._sessions:
                self._sessions[session_id].metadata.update(metadata)
                self._sessions[session_id].updated_at = time.time()

    async def get_state(self, session_id: str) -> VibeSkillSessionState:
        """获取 session 状态。"""
        session = self._sessions.get(session_id)
        return session.state if session else VibeSkillSessionState.IDLE

    async def resolve_session(self, session_id: str) -> VibeSkillSession | None:
        """通过 session ID 解析已有 session；不存在则返回 None。"""
        sid = str(session_id or "").strip()
        if not sid:
            return None
        return self._sessions.get(sid)

    async def get_session(self, session_id: str) -> VibeSkillSession | None:
        """获取 session 对象。"""
        return self._sessions.get(session_id)

    def get_user_id(self, session_id: str) -> str | None:
        """Return the routing user_id for a VibeSkill session."""
        session = self._sessions.get(session_id)
        if not session:
            return None
        user_id = str((session.metadata or {}).get("user_id") or "").strip()
        return user_id or None

    async def list_sessions(self) -> list[VibeSkillSession]:
        """列出所有 session。"""
        return list(self._sessions.values())

    async def delete_session(self, session_id: str) -> bool:
        """删除 session。"""
        async with self._lock:
            if session_id not in self._sessions:
                return False
            self._sessions.pop(session_id)
            return True
