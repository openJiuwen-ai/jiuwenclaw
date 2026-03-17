# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import List, Optional, Union, Dict

from openjiuwen.core.foundation.llm import BaseMessage


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

    def offload(
            self,
            offload_handle: str,
            offload_type: str,
            messages: List[BaseMessage],
    ):
        if offload_type == "in_memory":
            self._in_memory_offload_messages[offload_handle] = messages

    def reload(
            self,
            offload_handle: str,
            offload_type: str
    ) -> List[BaseMessage]:
        if offload_type == "in_memory":
            return self._in_memory_offload_messages.get(offload_handle, [])
        return []

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
