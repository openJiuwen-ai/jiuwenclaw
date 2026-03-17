# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import uuid
from typing import Any

from openjiuwen.core.session import Config
from openjiuwen.core.session.internal.agent import AgentSession


class Session:
    """AgentGroup Session"""

    def __init__(self, session_id: str = None, envs: dict[str, Any] = None):
        if session_id is None:
            session_id = str(uuid.uuid4())
        self._session_id = session_id
        config = Config()
        if envs is not None:
            config.set_envs(envs)
        self._inner = AgentSession(session_id=session_id, config=config)

    def get_session_id(self) -> str:
        return self._session_id

    def get_env(self, key: str, default: Any) -> Any:
        return self._inner.config().get_env(key, default)


def create_agent_group_session(session_id: str = None, envs: dict[str, Any] = None) -> Session:
    """Create AgentGroup Session"""
    return Session(session_id=session_id, envs=envs)