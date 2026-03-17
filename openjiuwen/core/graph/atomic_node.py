# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from abc import ABC, abstractmethod
from typing import Any, Optional

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.session import BaseSession
from openjiuwen.core.session import CommitState


def _validate_session_and_state(session: Optional[BaseSession]) -> None:
    if session is None:
        raise build_error(StatusCode.GRAPH_STATE_COMMIT_ERROR, reason="session is None")
    
    if not isinstance(session, BaseSession):
        raise build_error(StatusCode.GRAPH_STATE_COMMIT_ERROR, reason="session is not base session")
    
    state = session.state()
    if not isinstance(state, CommitState):
        raise build_error(StatusCode.GRAPH_STATE_COMMIT_ERROR, reason="session is not support commit state")


class AtomicNode(ABC):
    def atomic_invoke(self, **kwargs) -> Any:
        session = kwargs.get("session", None)
        _validate_session_and_state(session)
        result = self._atomic_invoke(**kwargs)
        session.state().commit_cmp()
        return result

    @abstractmethod
    def _atomic_invoke(self, **kwargs) -> Any:
        pass


class AsyncAtomicNode(ABC):
    async def atomic_invoke(self, **kwargs) -> Any:
        session = kwargs.get("session", None)
        _validate_session_and_state(session)
        result = await self._atomic_invoke(**kwargs)
        session.state().commit_cmp()
        return result

    @abstractmethod
    async def _atomic_invoke(self, **kwargs) -> Any:
        pass
