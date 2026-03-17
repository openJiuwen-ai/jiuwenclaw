# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.


from abc import (
    ABC,
    abstractmethod,
)
from dataclasses import dataclass
from typing import (
    Any,
    Dict,
    List,
    Optional,
)

from openjiuwen.core.common.logging import graph_logger, LogEventType


@dataclass
class PendingNode:
    node_name: str
    status: str
    exception: list[Exception] = None


@dataclass
class GraphState:
    ns: str
    step: int
    channel_values: Dict[str, Any]
    pending_buffer: List["Message"]
    pending_node: Dict[str, PendingNode]
    node_version: Dict[str, int]


class Store(ABC):
    @abstractmethod
    async def get(self, session_id: str, ns: str) -> Optional[GraphState]:
        ...

    @abstractmethod
    async def save(self, session_id: str, ns: str, state: GraphState) -> None:
        ...

    @abstractmethod
    async def delete(self, session_id: str, ns: Optional[str] = None) -> None:
        ...


def create_state(
        ns: str,
        step: int,
        channel_snapshot: Dict[str, Any],
        *,
        pending_buffer: Optional[List["Message"]] = None,
        pending_node: Optional[Dict[str, PendingNode]] = None,
        node_version: Dict[str, int] = None

) -> GraphState:
    return GraphState(
        ns=ns,
        step=step,
        channel_values=channel_snapshot,
        pending_buffer=pending_buffer or [],
        pending_node=pending_node or {},
        node_version=node_version or {},
    )


class GraphStore(Store):
    def __init__(self, saver: Store):
        self._saver = saver

    async def get(self, session_id: str, ns: str) -> Optional[GraphState]:
        """Get graph state from storage."""
        try:
            state = await self._saver.get(session_id, ns)
            if state is None:
                graph_logger.debug(
                    "Not found graph state for session",
                    event_type=LogEventType.GRAPH_STORE_GET,
                    session_id=session_id,
                    graph_id=ns
                )
            return state
        except Exception as e:
            graph_logger.error(
                "Failed to get graph state",
                event_type=LogEventType.GRAPH_STORE_GET,
                session_id=session_id,
                exception=e,
                graph_id=ns
            )
            raise

    async def save(self, session_id: str, ns: str, state: GraphState) -> None:
        """Save graph state to storage."""
        graph_logger.debug(
            f"Begin to save graph state of super-step[{state.step}]",
            event_type=LogEventType.GRAPH_STORE_SAVE,
            session_id=session_id,
            graph_id=ns
        )
        try:
            await self._saver.save(session_id, ns, state)
            graph_logger.debug(
                f"Succeed to save graph state of super-step[{state.step}]",
                event_type=LogEventType.GRAPH_STORE_SAVE,
                session_id=session_id,
                graph_id=ns
            )
        except Exception as e:
            graph_logger.error(
                f"Succeed to save graph state of super-step[{state.step}]",
                event_type=LogEventType.GRAPH_STORE_SAVE,
                session_id=session_id,
                exception=e,
                graph_id=ns
            )
            raise

    async def delete(self, session_id: str, ns: Optional[str] = None) -> None:
        """Delete graph state from storage."""
        graph_logger.debug(
            f"Begin to delete {ns if ns else 'all'} graph states for session",
            event_type=LogEventType.GRAPH_STORE_DELETE,
            session_id=session_id,
            graph_id=ns,
        )
        try:
            await self._saver.delete(session_id, ns)
            graph_logger.debug(
                f"Succeed to delete {ns if ns else 'all'} graph states for session",
                event_type=LogEventType.GRAPH_STORE_DELETE,
                session_id=session_id,
                graph_id=ns,
            )
        except Exception as e:
            graph_logger.debug(
                f"Failed delete {ns if ns else 'all'} graph states for session",
                event_type=LogEventType.GRAPH_STORE_DELETE,
                session_id=session_id,
                exception=e,
                graph_id=ns,
            )
            raise
