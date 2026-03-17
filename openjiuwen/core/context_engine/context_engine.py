# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import List, Dict, Optional, Tuple, Any
import functools

from pydantic import BaseModel

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import context_engine_logger, LogEventType
from openjiuwen.core.foundation.llm import BaseMessage
from openjiuwen.core.session.agent import Session
from openjiuwen.core.context_engine.base import ModelContext
from openjiuwen.core.context_engine.schema.config import ContextEngineConfig
from openjiuwen.core.context_engine.context.context import SessionModelContext
from openjiuwen.core.context_engine.token.base import TokenCounter
from openjiuwen.core.context_engine.processor.base import ContextProcessor
from openjiuwen.core.runner.callback import trigger, lazy_callback_framework as _fw
from openjiuwen.core.runner.callback.events import ContextEvents


class ContextEngine:
    """
    Manages the lifecycle and processing of conversational context.

    ContextEngine acts as the central entry-point for:
    1. Registering and configuring message processors.
    2. Creating isolated ModelContext instances tied to a session.
    3. Applying processor chains to enforce window limits, compression, etc.

    Parameters
    ----------
    config : ContextEngineConfig, optional
        Global engine settings (message/token limits, processor defaults).
        If omitted, a default configuration is used.
    """

    _PROCESSOR_MAP: Dict[str, type[ContextProcessor]] = dict()

    def __init__(self,
                 config: ContextEngineConfig = None,
                 ):
        self._config = config or ContextEngineConfig()
        self._context_pool: Dict[str, ModelContext] = dict()

    @_fw.emit_after(ContextEvents.CONTEXT_RETRIEVED, result_key="context")
    async def create_context(
            self,
            context_id: str = "default_context_id",
            session: Session = None,
            *,
            processors: List[Tuple[str, BaseModel]] = None,
            history_messages: List[BaseMessage] = None,
            token_counter: TokenCounter = None,
    ) -> ModelContext:
        """
        Create or retrieve a ModelContext for the given session & context ID.

        Token counting: if `token_counter` is None, defaults to `TiktokenCounter`.

        Message seeding:
        - if `history_messages` is provided, it is used as-is;
        - else if `mem_scope_id` is given, the engine attempts to restore
          previous messages from long-term memory under that scope;
        - otherwise an empty message list is adopted.

        Args:
            context_id: Unique identifier for this context within the session.
            session: Session object supplying session_id; if None, a default
                     session ID is used.
            history_messages: Initial message list.
            token_counter: Strategy for counting tokens; defaults to
                           TiktokenCounter if not provided.

        Returns:
            ModelContext: The newly created or cached context instance.
        """
        context_id = self._process_context_id(context_id)
        session_id = session.get_session_id() if session else "default_session_id"
        full_context_id = f"{session_id}_{context_id}"
        if full_context_id in self._context_pool:
            context = self._context_pool.get(full_context_id)
            self._load_state_from_session(context, session, history_messages)
            return context

        processor_instances = [
            self._create_processor(processor_type, processor_config)
            for processor_type, processor_config in (processors or [])
        ]

        context = SessionModelContext(
            context_id,
            session_id,
            self._config,
            history_messages=history_messages or [],
            processors=processor_instances,
            token_counter=token_counter,
        )
        self._load_state_from_session(context, session, history_messages)
        self._context_pool[full_context_id] = context
        return context

    def get_context(
            self,
            context_id: str = "default_context_id",
            session_id: str = "default_session_id"
    ) -> Optional[ModelContext]:
        """
        Retrieve an existing ModelContext from the pool.

        Args:
            context_id: Context identifier within the session.
            session_id: Session identifier.

        Returns:
            ModelContext instance if found, otherwise None.
        """
        context_id = self._process_context_id(context_id)
        full_context_id = f"{session_id}_{context_id}"
        return self._context_pool.get(full_context_id, None)

    async def clear_context(
            self,
            context_id: str = None,
            session_id: str = None
    ):
        """
        Remove contexts from the internal pool.

        Behavior depends on the arguments provided:
        1. Neither argument supplied  -> delete the all context.
        2. Only `session_id` supplied -> delete every context belonging to that session.
        3. Both arguments supplied    -> delete the single context identified..

        Parameters
        ----------
        context_id : str, optional
            Logical context identifier.  When provided, `session_id` must also
            be supplied and only the exact matching context is removed.
        session_id : str, optional
            Session identifier used to scope the deletion.  If omitted, the
            operation targets all contexts.

        Warnings
        --------
        Logs a warning when the requested session or context cannot be found.
        """
        if session_id is None:
            cleared_count = len(self._context_pool)
            self._context_pool.clear()
            await trigger(ContextEvents.CONTEXT_CLEARED,
                       context_id=context_id, session_id=session_id,
                       cleared_count=cleared_count)
            return

        if context_id is None:
            delete_context_list = [
                context.context_id() for _, context in self._context_pool.items()
                if context.session_id() == session_id
            ]

            if not delete_context_list:
                context_engine_logger.warning(
                    "Delete context failed, session does not exist",
                    event_type=LogEventType.CONTEXT_CLEAR,
                    metadata={"session_id": session_id}
                )
                return

            for context_id in delete_context_list:
                full_context_id = f"{session_id}_{context_id}"
                del self._context_pool[full_context_id]
            await trigger(ContextEvents.CONTEXT_CLEARED,
                       context_id=context_id, session_id=session_id,
                       cleared_count=len(delete_context_list))
            return

        context_id = self._process_context_id(context_id)
        full_context_id = f"{session_id}_{context_id}"
        if full_context_id not in self._context_pool:
            context_engine_logger.warning(
                "Delete context failed, context does not exist",
                event_type=LogEventType.CONTEXT_CLEAR,
                metadata={"session_id": session_id}
            )
            return

        del self._context_pool[full_context_id]
        await trigger(ContextEvents.CONTEXT_CLEARED,
                   context_id=context_id, session_id=session_id)

    @_fw.emit_after(ContextEvents.CONTEXT_OFFLOADED, result_key="result")
    async def save_contexts(self,
                            session: Session,
                            context_ids: List[str] = None
                            ):
        """
        Batch-persist multiple contexts and their runtime states.

        Each context's messages, sliding-window position, token count and statistics
        are saved locally.

        Args:
            context_ids: List of target context identifiers to save.
            session: Session object;
        """
        if not session:
            context_engine_logger.warning(
                "Save context failed, session cannot be None",
                event_type=LogEventType.CONTEXT_SAVE,
            )
            return
        session_id = session.get_session_id()
        states = dict()
        if context_ids is None:
            context_ids = [
                context.context_id() for context_id, context in self._context_pool.items()
                if context.session_id() == session_id
            ]

        for context_id in context_ids:
            context_id = self._process_context_id(context_id)
            full_context_id = f"{session_id}_{context_id}"
            context = self._context_pool.get(full_context_id)
            if context is None or not hasattr(context, "save_state"):
                continue
            context_state = context.save_state()
            states[context_id] = context_state
        self._save_state_to_session(session, states)
        return states

    @classmethod
    def register_processor(cls, processor_class=None):
        """
        Class-method decorator for plugging a new ContextProcessor into the engine.

        Usage
        -----
        @register_processor(MyProcessorConfig)
        class MyProcessor(ContextProcessor):
            ...

        The decorator performs two book-keeping actions:
        1. Maps `processor_class.processor_type()` -> `processor_class`
           so the engine can instantiate the processor at runtime.
        2. Maps `processor_class.processor_type()` -> `config`
           so the engine can validate/convert the user-supplied config dict.

        Parameters
        ----------
        config : subclass of ContextProcessorConfig
            Configuration schema that belongs to the processor being decorated.
        processor_class : subclass of ContextProcessor, optional
            When used as a **parameter-less** decorator this argument is None;
            the inner function receives the real class object.

        Returns
        -------
        callable
            A decorator that accepts the processor class and returns it unchanged
            after registration (allowing normal class-definition syntax).
        """
        @functools.wraps(processor_class)
        def register_processor_class(processor_class: type[ContextProcessor]):
            cls._PROCESSOR_MAP[processor_class.processor_type()] = processor_class
            return processor_class
        return register_processor_class

    def _create_processor(self, processor_type: str, config: BaseModel):
        processor_class = self._PROCESSOR_MAP.get(processor_type)
        if not processor_class:
            raise build_error(
                StatusCode.CONTEXT_EXECUTION_ERROR,
                error_msg=f"cannot find processor type '{processor_type}'"
            )

        try:
            processor = processor_class(config)
        except Exception as e:
            raise build_error(
                StatusCode.CONTEXT_EXECUTION_ERROR,
                error_msg=f"init processor type '{processor_type}' failed",
                cause=e
            ) from e

        return processor

    @staticmethod
    def _load_state_from_session(
            context: ModelContext,
            session: Session,
            history_messages: List[BaseMessage] = None
    ):
        if not session:
            return
        states = None
        if hasattr(session, "get_state"):
            states = session.get_state("context")
        elif hasattr(session, "_inner"):
            states = getattr(session, "_inner").get_state("context") if session else None

        if states is None:
            return

        if not hasattr(context, "load_state"):
            return

        if history_messages is not None:
            context_id = context.context_id()
            states[context_id]['messages'] = history_messages

        context.load_state(states)

    @staticmethod
    def _save_state_to_session(
            session,
            states: dict
    ):
        if not session:
            return
        if hasattr(session, "update_state"):
            session.update_state({"context": None})
            session.update_state({"context": states})
        elif hasattr(session, "_inner"):
            getattr(session, "_inner").update_state({"context": None})
            getattr(session, "_inner").update_state({"context": states})

    @staticmethod
    def _process_context_id(context_id: str) -> str:
        return context_id.replace(".", "_")
