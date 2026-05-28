from __future__ import annotations

import json
import logging
import os
from typing import Any

from jiuwenclaw.channel.vibeskill_session import (
    VibeSkillSession,
    VibeSkillSessionState,
)
from jiuwenclaw.dcs import DcsClusterClient, DcsClusterConfig, load_config_from_env

logger = logging.getLogger(__name__)
_SANDBOX_DCS_TTL_SECONDS_ENV = "SANDBOX_DCS_TTL_SECONDS"

# Backward-compatible alias for tests / future imports.
VibeSkillSessionDcsConfig = DcsClusterConfig


class VibeSkillSessionDcsStore:
    """Persist VibeSkill session metadata to Huawei DCS (Redis Cluster).

    职责仅为 DCS 远端 I/O：序列化 / Key 拼接 / Redis 命令 / TTL 续期。
    业务语义（缓存、状态机）在 ``VibeSkillSessionStore`` 中处理。
    """

    def __init__(self, config: DcsClusterConfig) -> None:
        self._config = config
        # Session keys default to no expire unless SANDBOX_DCS_TTL_SECONDS is explicitly set.
        raw = os.environ.get(_SANDBOX_DCS_TTL_SECONDS_ENV, "").strip()
        self._ttl_seconds = max(0, int(raw)) if raw else 0
        self._dcs = DcsClusterClient(config)

    @classmethod
    def from_env(cls) -> VibeSkillSessionDcsStore | None:
        config = load_config_from_env(required=False)
        if config is None:
            return None
        return cls(config)

    async def close(self) -> None:
        await self._dcs.close()

    @property
    def host(self) -> str:
        return self._dcs.host

    @staticmethod
    def _session_key(session_id: str) -> str:
        return f"jiuwen:vibeskillSession:{session_id}"

    @staticmethod
    def _serialize(session: VibeSkillSession) -> str:
        state_value = (
            session.state.value
            if isinstance(session.state, VibeSkillSessionState)
            else str(session.state)
        )
        payload: dict[str, Any] = {
            "session_id": session.session_id,
            "state": state_value,
            "mode": session.mode,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "metadata": dict(session.metadata or {}),
        }
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _deserialize(raw: str) -> VibeSkillSession | None:
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        session_id = str(
            data.get("session_id") or data.get("internal_id") or ""
        ).strip()
        if not session_id:
            return None
        try:
            state = VibeSkillSessionState(str(data.get("state") or "idle"))
        except ValueError:
            state = VibeSkillSessionState.IDLE
        mode = str(data.get("mode") or "SkillCreate")
        metadata = data.get("metadata") or {}
        if not isinstance(metadata, dict):
            metadata = {}
        try:
            created_at = float(data.get("created_at") or 0.0)
        except (TypeError, ValueError):
            created_at = 0.0
        try:
            updated_at = float(data.get("updated_at") or 0.0)
        except (TypeError, ValueError):
            updated_at = 0.0
        return VibeSkillSession(
            session_id=session_id,
            state=state,
            created_at=created_at,
            updated_at=updated_at,
            metadata=metadata,
            mode=mode,
        )

    async def save_session(self, session: VibeSkillSession) -> None:
        """覆盖写整个 session。失败时透传异常（fail-fast）。"""
        session_key = self._session_key(session.session_id)
        await self._dcs.set_with_ttl(
            session_key,
            self._serialize(session),
            ttl_seconds=self._ttl_seconds,
        )
        logger.debug(
            "[VibeSkillSessionDcsStore] saved session: session_id=%s state=%s",
            session.session_id,
            session.state.value
            if isinstance(session.state, VibeSkillSessionState)
            else session.state,
        )

    async def load_session(self, session_id: str) -> VibeSkillSession | None:
        sid = str(session_id or "").strip()
        if not sid:
            return None
        raw = await self._dcs.get(self._session_key(sid))
        if raw is None:
            return None
        return self._deserialize(str(raw))

    async def delete_session(self, session_id: str) -> None:
        sid = str(session_id or "").strip()
        if not sid:
            return
        await self._dcs.delete(self._session_key(sid))
