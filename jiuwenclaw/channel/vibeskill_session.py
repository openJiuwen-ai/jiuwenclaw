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
    RETRY = "retry"


@dataclass
class VibeSkillSession:
    """VibeSkill Session 状态和元数据。"""

    external_id: str | None = None
    internal_id: str = ""
    state: VibeSkillSessionState = VibeSkillSessionState.IDLE
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)


class VibeSkillSessionStore:
    """VibeSkill Session 状态管理器。

    统一管理 VibeSkill 的 session 状态和内外 ID 映射。
    由 VibeSkillChannel 持有实例，不使用单例模式。

    Session 状态转换：
        idle ──(message.send)──► busy
        busy ──(chat.final/cancel)──► idle
        busy ──(error)──► retry
        retry ──(message.send)──► busy
    """

    def __init__(self) -> None:
        self._sessions: dict[str, VibeSkillSession] = {}  # internal_id -> session
        self._external_to_internal: dict[str, str] = {}  # external_id -> internal_id
        self._lock = asyncio.Lock()

    async def get_or_create(
        self,
        external_id: str | None,
        internal_id: str | None = None,
    ) -> VibeSkillSession:
        """获取或创建 session。

        优先用 internal_id 查找，其次用 external_id 查找，都找不到则创建新的。
        """
        async with self._lock:
            # 优先用 internal_id 查找
            if internal_id and internal_id in self._sessions:
                return self._sessions[internal_id]

            # 其次用 external_id 查找
            if external_id:
                existing_internal = self._external_to_internal.get(external_id)
                if existing_internal and existing_internal in self._sessions:
                    return self._sessions[existing_internal]
                # external_id 可能是 internal_id（当 session 通过 external_id=None 创建时）
                if external_id in self._sessions:
                    return self._sessions[external_id]

            # 创建新 session
            sid = internal_id or f"vibeskill_{secrets.token_hex(6)}"
            session = VibeSkillSession(external_id=external_id, internal_id=sid)
            self._sessions[sid] = session
            if external_id:
                self._external_to_internal[external_id] = sid
            return session

    async def set_state(self, internal_id: str, state: VibeSkillSessionState) -> None:
        """更新 session 状态。"""
        async with self._lock:
            if internal_id in self._sessions:
                self._sessions[internal_id].state = state
                self._sessions[internal_id].updated_at = time.time()

    async def set_metadata(self, internal_id: str, metadata: dict[str, Any]) -> None:
        """更新 session 元数据。"""
        async with self._lock:
            if internal_id in self._sessions:
                self._sessions[internal_id].metadata.update(metadata)
                self._sessions[internal_id].updated_at = time.time()

    async def get_state(self, internal_id: str) -> VibeSkillSessionState:
        """获取 session 状态。"""
        session = self._sessions.get(internal_id)
        return session.state if session else VibeSkillSessionState.IDLE

    async def resolve_internal(self, external_id: str) -> str | None:
        """外部 ID → 内部 ID。"""
        internal_id = self._external_to_internal.get(external_id)
        if internal_id and internal_id in self._sessions:
            return internal_id
        return None

    async def resolve_external(self, internal_id: str) -> str | None:
        """内部 ID → 外部 ID。"""
        session = self._sessions.get(internal_id)
        return session.external_id if session else None

    async def bind_external(self, internal_id: str, external_id: str) -> None:
        """绑定外部 ID 到已有 session。"""
        async with self._lock:
            if internal_id not in self._sessions:
                return

            old_external = self._sessions[internal_id].external_id
            if old_external and old_external in self._external_to_internal:
                del self._external_to_internal[old_external]

            self._sessions[internal_id].external_id = external_id
            self._sessions[internal_id].updated_at = time.time()
            self._external_to_internal[external_id] = internal_id

    async def get_session(self, internal_id: str) -> VibeSkillSession | None:
        """获取 session 对象。"""
        return self._sessions.get(internal_id)

    async def list_sessions(self) -> list[VibeSkillSession]:
        """列出所有 session。"""
        return list(self._sessions.values())

    async def delete_session(self, internal_id: str) -> bool:
        """删除 session。"""
        async with self._lock:
            if internal_id not in self._sessions:
                return False
            session = self._sessions.pop(internal_id)
            if session.external_id and session.external_id in self._external_to_internal:
                del self._external_to_internal[session.external_id]
            return True
