# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from abc import abstractmethod
from typing import List, Dict, Any, Tuple, Optional
import os
import uuid
import json

from pydantic import BaseModel, Field

from jiuwenswarm.extensions.dolores.server.runtime.context_engine import ModelContext, ContextWindow
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.context.session_memory_manager import group_completed_api_rounds
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.schema.messages import create_offload_message
from openjiuwen.core.foundation.llm import BaseMessage


_PROCESSOR_TYPE_ATTR: str = "__processor_type"
_OFFLOAD_MESSAGE_HANDLE: str = "\n[[OFFLOAD: handle={handle}, type={type}]]"
_OFFLOAD_MESSAGE_HANDLE_WITH_PATH: str = "\n[[OFFLOAD: handle={handle}, type={type}, path={path}]]"


class MetaContextProcessor(type):
    def __new__(msc, name, bases, attrs, **kwargs):
        attrs[_PROCESSOR_TYPE_ATTR] = name
        return super().__new__(msc, name, bases, attrs)


class ContextEvent(BaseModel):
    event_type: str = Field(...)
    messages_to_modify: List[int] = Field(default_factory=list)
    compact_summary: str = Field(default="")
    compression_usage: Dict[str, Any] | None = Field(default=None)


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
        self._compression_usage: Dict[str, Any] | None = None

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

    def _reset_compression_usage(self) -> None:
        self._compression_usage = None

    def _record_compression_usage(self, response: Any) -> None:
        usage = self._extract_usage_metadata(response)
        if usage is None:
            return
        self._compression_usage = self._merge_compression_usage(self._compression_usage, usage)

    def _current_compression_usage(self) -> Dict[str, Any] | None:
        return dict(self._compression_usage) if self._compression_usage else None

    @staticmethod
    def _extract_usage_metadata(response: Any) -> Dict[str, Any] | None:
        metadata = getattr(response, "usage_metadata", None)
        if metadata is None:
            return None
        if hasattr(metadata, "model_dump"):
            data = metadata.model_dump(mode="json")
        elif isinstance(metadata, dict):
            data = metadata
        else:
            return None
        if not isinstance(data, dict):
            return None
        return {
            "calls": 1,
            "input_tokens": int(data.get("input_tokens") or 0),
            "output_tokens": int(data.get("output_tokens") or 0),
            "total_tokens": int(data.get("total_tokens") or 0),
            "cache_tokens": int(data.get("cache_tokens") or 0),
            "input_cost": float(data.get("input_cost") or 0),
            "output_cost": float(data.get("output_cost") or 0),
            "total_cost": float(data.get("total_cost") or 0),
            "model_name": str(data.get("model_name") or ""),
            "details": [data],
        }

    @staticmethod
    def _merge_compression_usage(
            left: Dict[str, Any] | None,
            right: Dict[str, Any] | None,
    ) -> Dict[str, Any] | None:
        if left is None:
            return dict(right) if right else None
        if right is None:
            return dict(left)
        merged = dict(left)
        for key in (
            "calls",
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "cache_tokens",
        ):
            merged[key] = int(merged.get(key) or 0) + int(right.get(key) or 0)
        for key in ("input_cost", "output_cost", "total_cost"):
            merged[key] = float(merged.get(key) or 0) + float(right.get(key) or 0)
        if not merged.get("model_name"):
            merged["model_name"] = str(right.get("model_name") or "")
        merged["details"] = [
            *(merged.get("details") or []),
            *(right.get("details") or []),
        ]
        return merged

    async def offload_messages(
            self,
            role: str,
            content: str,
            messages: List[BaseMessage],
            *,
            context: ModelContext = None,
            offload_handle: str = None,
            offload_type: str = "filesystem",
            offload_path: str = None,
            **kwargs
    ) -> Optional[BaseMessage]:
        if not messages:
            return None

        if not offload_handle:
            offload_handle = uuid.uuid4().hex

        if context is not None:
            if offload_type == "in_memory":
                return self._offload_messages_to_memory(
                    role, content, messages, context, offload_handle, **kwargs
                )
            elif offload_type == "filesystem":
                session_id = context.session_id()
                workspace_dir = context.workspace_dir()
                # 生成 offload_path
                if not offload_path:
                    offload_path = self._generate_offload_path(workspace_dir, session_id, offload_handle)
                # 写入原始内容到文件，失败时 fallback 到 in_memory
                sys_operation = kwargs.get("sys_operation")
                write_success = await self._write_offload_to_file(
                    session_id=session_id,
                    offload_handle=offload_handle,
                    offload_path=offload_path,
                    messages=messages,
                    sys_operation=sys_operation,
                )
                if not write_success:
                    return None
                return await self._offload_messages_to_filesystem(
                    role, content, offload_handle, offload_path, session_id=session_id, **kwargs
                )
        return None

    @staticmethod
    def _generate_offload_path(workspace_dir: str, session_id: str, offload_handle: str) -> str:
        """生成 offload 文件路径"""
        if workspace_dir:
            return os.path.join(
                workspace_dir, "context", session_id + "_context", "offload", offload_handle + ".json"
            )
        return os.path.abspath(os.path.join("memory", "offloads", session_id, offload_handle + ".json"))

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

    @staticmethod
    async def _offload_messages_to_filesystem(
            role: str,
            content: str,
            offload_handle: str = None,
            offload_path: str = None,
            session_id: str = None,
            **kwargs
    ) -> Optional[BaseMessage]:
        if offload_path:
            content = content + _OFFLOAD_MESSAGE_HANDLE_WITH_PATH.format(
                handle=offload_handle, type="filesystem", path=offload_path
            )
        else:
            content = content + _OFFLOAD_MESSAGE_HANDLE.format(
                handle=offload_handle, type="filesystem"
            )

        # 如果有原始内容需要写入文件
        return create_offload_message(
            role=role,
            content=content,
            offload_handle=offload_handle,
            offload_type="filesystem",
            **kwargs
        )

    async def _write_offload_to_file(
            self,
            session_id: str,
            offload_handle: str,
            offload_path: str,
            messages: List[BaseMessage],
            sys_operation=None,
    ) -> bool:
        """
        使用 SysOperation 写入 offload 内容到文件系统。

        目录结构: {workspace_dir}/context/{session_id}_context/offload/{handle}.json
        如果 workspace_dir 未设置，使用绝对路径 offload_path。

        Returns:
            bool: True if file was written successfully, False if failed.
        """
        if offload_path:
            file_path = offload_path
        else:
            file_path = f"memory/offloads/{session_id}/{offload_handle}.json"

        message_data = {
            "offload_handle": offload_handle,
            "messages": [msg.model_dump() if hasattr(msg, "model_dump") else str(msg) for msg in messages],
        }
        content_json = json.dumps(message_data, ensure_ascii=False, indent=2)
        try:
            if sys_operation is None:
                if not os.path.isabs(file_path):
                    return False
                os.makedirs(os.path.dirname(file_path), exist_ok=True)
                with open(file_path, "w", encoding="utf-8") as file:
                    file.write(content_json)
                return True
            await sys_operation.fs().write_file(file_path, content_json)
            return True
        except Exception:
            return False

    @staticmethod
    def _api_round(messages: List[BaseMessage]) -> bool:
        if not messages:
            return False
        completed_rounds = group_completed_api_rounds(messages)
        if not completed_rounds:
            return False
        last_end = completed_rounds[-1][1]
        return last_end == len(messages)
