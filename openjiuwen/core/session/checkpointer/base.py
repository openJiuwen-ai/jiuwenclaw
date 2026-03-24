# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from abc import (
    ABC,
    abstractmethod,
)

from openjiuwen.core.graph.store import Store
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
from openjiuwen.core.session.session import BaseSession


class Checkpointer(ABC):
    @staticmethod
    def get_thread_id(session: BaseSession) -> str:
        return ":".join([session.session_id(), session.workflow_id()])

    @abstractmethod
    async def pre_workflow_execute(self, session: BaseSession, inputs: InteractiveInput):
        ...

    @abstractmethod
    async def post_workflow_execute(self, session: BaseSession, result, exception):
        ...

    @abstractmethod
    async def pre_agent_execute(self, session: BaseSession, inputs):
        ...

    @abstractmethod
    async def pre_agent_team_execute(self, session: BaseSession, inputs):
        ...

    @abstractmethod
    async def interrupt_agent_execute(self, session: BaseSession):
        ...

    @abstractmethod
    async def post_agent_execute(self, session: BaseSession):
        ...

    @abstractmethod
    async def post_agent_team_execute(self, session: BaseSession):
        ...

    @abstractmethod
    async def session_exists(self, session_id: str) -> bool:
        ...

    @abstractmethod
    async def release(self, session_id: str):
        ...

    @abstractmethod
    def graph_store(self) -> Store:
        ...


class Storage(ABC):
    @abstractmethod
    async def save(self, session: BaseSession):
        ...

    @abstractmethod
    async def recover(self, session: BaseSession, inputs: InteractiveInput = None):
        ...

    @abstractmethod
    async def clear(self, session_id: str):
        ...

    @abstractmethod
    async def exists(self, session: BaseSession) -> bool:
        ...


# Key namespace constants
# Namespace for agent state under session
SESSION_NAMESPACE_AGENT = "agent"
# Namespace for agent team state under session
SESSION_NAMESPACE_AGENT_TEAM = "agent-team"
# Namespace for workflow state under session (workflow's own state)
SESSION_NAMESPACE_WORKFLOW = "workflow"
# Namespace for graph state under workflow (separated from workflow's own state)
WORKFLOW_NAMESPACE_GRAPH = "workflow-graph"


def build_key(*parts: str) -> str:
    """
    Build key by joining parts with colon separator.

    Args:
        *parts: Variable number of string parts to join into a key.

    Returns:
        Key string with parts joined by ':'.
    """
    return ":".join(parts)


def build_key_with_namespace(
        session_id: str,
        namespace: str,
        entity_id: str,
        *suffixes: str
) -> str:
    """
    Build key with namespace structure: session:namespace:entity_id:suffixes.

    Args:
        session_id: Session identifier.
        namespace: Namespace (e.g., 'agent', 'workflow').
        entity_id: Entity identifier (e.g., agent_id, workflow_id).
        *suffixes: Additional key suffixes.

    Returns:
        Key string.
    """
    parts = [session_id, namespace, entity_id] + list(suffixes)
    return build_key(*parts)
