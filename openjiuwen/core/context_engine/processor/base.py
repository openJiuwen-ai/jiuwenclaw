# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from abc import abstractmethod
from typing import List, Dict, Any, Tuple, Optional
import uuid

from pydantic import BaseModel, Field

from openjiuwen.core.context_engine import ModelContext, ContextWindow
from openjiuwen.core.context_engine.schema.messages import create_offload_message
from openjiuwen.core.foundation.llm import BaseMessage


_PROCESSOR_TYPE_ATTR: str = "__processor_type"
_OFFLOAD_MESSAGE_HANDLE: str = "[[OFFLOAD: handle={handle}, type={type}]]"


class MetaContextProcessor(type):
    def __new__(msc, name, bases, attrs, **kwargs):
        attrs[_PROCESSOR_TYPE_ATTR] = name
        return super().__new__(msc, name, bases, attrs)


class ContextEvent(BaseModel):
    event_type: str = Field(...)
    messages_to_modify: List[int] = Field(default_factory=list)


class ContextProcessor(metaclass=MetaContextProcessor):
    """
    Abstract base class for all context-processing plug-ins.

    A context processor can intervene at two life-cycle points:
    1. When new messages are about to be added (`on_add_messages`)
    2. When the context window is being materialized (`on_get_context_window`)

    Each processor decides *whether* to intervene via the corresponding
    `trigger_*` coroutine and, if so, *how* to intervene in the paired
    `on_*` coroutine.  Implementations must be stateless or provide
    `save_state`/`load_state` so that the owning context manager can
    checkpoint and restore them across sessions.

    The processor is configured once at construction time and is
    re-entrant for concurrent contexts.
    """

    def __init__(self, config: BaseModel):
        """
        Store the processor-specific configuration.

        Parameters
        ----------
        config : pydantic BaseModel
            Validated configuration object produced from the
            processor's own *Config schema.
        """
        self._config = config

    # ------------------------------------------------------------------
    # Processing hooks
    # ------------------------------------------------------------------
    async def on_add_messages(
        self,
        context: ModelContext,
        messages_to_add: List[BaseMessage],
        **kwargs: Any,
    ) -> Tuple[ContextEvent | None, List[BaseMessage]]:
        """
        Transform or filter the **incoming** message batch.

        Called only when `trigger_add_messages` returned *True*.
        The returned list is passed to the next processor; an empty list
        cancels the insertion entirely.

        Default implementation is a no-op pass-through.
        """
        return None, messages_to_add

    async def on_get_context_window(
        self,
        context: ModelContext,
        context_window: ContextWindow,
        **kwargs: Any,
    ) -> Tuple[ContextEvent | None, ContextWindow]:
        """
        Mutate the **outgoing** context window (e.g. compress, reorder).

        Called only when `trigger_get_context_window` returned *True*.
        The returned object is forwarded to the next processor or the
        caller; returning *None* is forbidden.

        Default implementation is a no-op pass-through.
        """
        return None, context_window

    # ------------------------------------------------------------------
    # Trigger hooks
    # ------------------------------------------------------------------
    async def trigger_add_messages(
        self,
        context: ModelContext,
        messages_to_add: List[BaseMessage],
        **kwargs: Any,
    ) -> bool:
        """
        Return *True* if this processor wants to intervene **before**
        the messages are appended to the context.

        Executed for **every** add operation; must be cheap.
        Default: always *False*.
        """
        return False

    async def trigger_get_context_window(
        self,
        context: ModelContext,
        context_window: ContextWindow,
        **kwargs: Any,
    ) -> bool:
        """
        Return *True* if this processor wants to intervene **before**
        the context window is returned to the caller.

        Executed for **every** get operation; must be cheap.
        Default: always *False*.
        """
        return False

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------
    @abstractmethod
    def load_state(self, state: Dict[str, Any]) -> None:
        """
        Restore internal state from a dictionary produced by
        `save_state`.  Called during context manager initialisation
        when a previous checkpoint exists.
        """

    @abstractmethod
    def save_state(self) -> Dict[str, Any]:
        """
        Export internal state to a serialisable dictionary.
        The returned object must be JSON-compatible and sufficient
        to recreate an identical processor state via `load_state`.
        """

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------
    @classmethod
    def processor_type(cls) -> str:
        """
        Return the registered processor type string (set by the
        meta-class).  Empty string if not registered.
        """
        return getattr(cls, _PROCESSOR_TYPE_ATTR, "")

    @property
    def config(self) -> BaseModel:
        """
        Read-only access to the validated configuration object
        supplied at construction time.
        """
        return self._config

    async def offload_messages(
            self,
            role: str,
            content: str,
            messages: List[BaseMessage],
            *,
            context: ModelContext = None,
            offload_handle: str = None,
            offload_type: str = "in_memory",
            **kwargs
    ) -> Optional[BaseMessage]:
        if not messages:
            return None

        if not offload_handle:
            offload_handle = uuid.uuid4().hex

        if context is not None and offload_type == "in_memory":
            return self._offload_messages_to_memory(
                role, content, messages, context, offload_handle, **kwargs
            )
        return None

    @staticmethod
    def _offload_messages_to_memory(
            role: str,
            content: str,
            messages: List[BaseMessage],
            context: ModelContext,
            offload_handle: str = None,
            **kwargs
    ) -> Optional[BaseMessage]:
        content = content + _OFFLOAD_MESSAGE_HANDLE.format(handle=offload_handle, type="in_memory")
        if hasattr(context, "offload_messages"):
            context.offload_messages(offload_handle, messages)
            offload_handle = offload_handle if offload_handle else uuid.uuid4().hex
            return create_offload_message(
                role=role,
                content=content,
                offload_handle=offload_handle,
                offload_type="in_memory",
                **kwargs
            )
        return None
