# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import glob
import os
from typing import List, Optional, Union, Dict

from openjiuwen.core.foundation.llm import BaseMessage
from openjiuwen.core.common.logging import logger


class ContextMessageBuffer:
    def __init__(self, history_messages: List[BaseMessage], max_buffer_size: Optional[int] = None):
        self._max_buffer_size = max_buffer_size
        self.rebulid(history_messages)

    def size(self) -> int:
        if self._max_buffer_size is not None:
            return min(len(self._context_messages), self._max_buffer_size)
        return len(self._context_messages)

    def add_back(self, messages: Union[BaseMessage, List[BaseMessage]]):
        if isinstance(messages, BaseMessage):
            self._context_messages.append(messages)
        else:
            for msg in messages:
                self._context_messages.append(msg)
        self._if_need_resize()

    def get_back(self, size: Optional[int] = None, with_history: bool = True) -> List[BaseMessage]:
        context_messages = (
            self._context_messages[:]
            if self._max_buffer_size is None
            else self._context_messages[max(0, len(self._context_messages) - self._max_buffer_size):]
        )
        if size is None:
            return context_messages \
                if with_history \
                else context_messages[self._history_messages_size:]
        total_size = len(context_messages)
        context_size = total_size - self._history_messages_size
        size = min(size, context_size) if not with_history else min(size, total_size)
        return context_messages[total_size - size:]

    def pop_back(self, size: Optional[int] = None, with_history: bool = True) -> List[BaseMessage]:
        popped_messages = self.get_back(size, with_history)
        popped_size = len(popped_messages)
        context_size = len(self._context_messages) - self._history_messages_size

        if with_history and popped_size > context_size:
            self._history_messages_size = max(0, self._history_messages_size - (popped_size - context_size))

        self._context_messages = self._context_messages[:len(self._context_messages) - popped_size]
        return popped_messages

    def set_messages(self, messages: List[BaseMessage], with_history: bool = True):
        if with_history:
            self._context_messages = messages
            self._history_messages_size = 0
            return
        history_messages = self._context_messages[:self._history_messages_size]
        self._context_messages = history_messages + messages

    def rebulid(self, history_messages: List[BaseMessage]):
        if self._max_buffer_size is not None:
            self._context_messages = history_messages[-self._max_buffer_size:]
            self._history_messages_size = min(len(self._context_messages), self._max_buffer_size)
        else:
            self._context_messages = history_messages.copy()
            self._history_messages_size = len(self._context_messages)

    def _if_need_resize(self):
        if self._max_buffer_size is None:
            return
        if len(self._context_messages) <= self._max_buffer_size * 2:
            return
        self._context_messages = self._context_messages[self._max_buffer_size:]
        if self._history_messages_size == 0 or self._max_buffer_size > self._history_messages_size:
            self._history_messages_size = 0
            return
        self._history_messages_size = self._history_messages_size - self._max_buffer_size


class OffloadMessageBuffer:
    def __init__(
            self,
            init_messages: Dict[str, List[BaseMessage]] = None,
    ):
        self._in_memory_offload_messages: Dict[str, List[BaseMessage]] = init_messages or dict()
        self._sys_operation = None
        self._workspace_dir = None
        self._session_id = None

    def set_sys_operation(self, sys_operation):
        """Set sys_operation for filesystem-based offload reload."""
        self._sys_operation = sys_operation

    def set_workspace_info(self, workspace_dir: str, session_id: str):
        """Set workspace info for filesystem-based offload reload."""
        self._workspace_dir = workspace_dir
        self._session_id = session_id

    def offload(
            self,
            offload_handle: str,
            offload_type: str,
            messages: List[BaseMessage],
    ):
        if offload_type == "in_memory":
            self._in_memory_offload_messages[offload_handle] = messages

    async def reload(
            self,
            offload_handle: str,
            offload_type: str
    ) -> List[BaseMessage]:
        if offload_type == "in_memory":
            return self._in_memory_offload_messages.get(offload_handle, [])
        if offload_type == "filesystem":
            return await self._reload_from_filesystem(offload_handle)
        return []

    async def _reload_from_filesystem(self, offload_handle: str) -> List[BaseMessage]:
        """Reload messages from filesystem storage."""
        if self._sys_operation is None:
            return []
        # 重建完整文件路径
        for offload_path in self._filesystem_reload_paths(offload_handle):
            try:
                result = await self._sys_operation.fs().read_file(offload_path)
                if result.code == 0 and result.data:
                    import json
                    payload = json.loads(result.data.content)
                    messages_data = payload.get("messages", [])
                    messages = []
                    for msg_data in messages_data:
                        try:
                            messages.append(BaseMessage.model_validate(msg_data))
                        except Exception as e:
                            logger.warning(f"Failed to validate message: {e}")
                    return messages
            except Exception as e:
                logger.warning(f"Failed to reload messages from filesystem: {e}")
        return []

    def _filesystem_reload_paths(self, offload_handle: str) -> List[str]:
        """Return candidate filesystem paths for an offload handle."""
        if not (self._workspace_dir and self._session_id):
            return [offload_handle]

        offload_dir = os.path.join(
            self._workspace_dir, "context", self._session_id + "_context", "offload"
        )
        exact_path = os.path.join(offload_dir, offload_handle + ".json")
        paths = [exact_path]
        prefixed_paths = sorted(glob.glob(os.path.join(offload_dir, f"*_{offload_handle}.json")))
        paths.extend(path for path in prefixed_paths if path not in paths)
        return paths

    def clear(
            self,
            offload_handle: str,
            offload_type: str
    ):
        if offload_type == "in_memory":
            self._in_memory_offload_messages.pop(offload_handle, None)
        return

    def get_all(
            self
    ):
        return self._in_memory_offload_messages
