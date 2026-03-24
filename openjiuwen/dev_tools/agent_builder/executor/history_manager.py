# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

from openjiuwen.core.common.logging import LogManager
from openjiuwen.dev_tools.agent_builder.utils.constants import DEFAULT_MAX_HISTORY_SIZE

logger = LogManager.get_logger("agent_builder")


@dataclass
class DialogueMessage:
    """Dialog message

    Attributes:
        content: Message content
        role: Message role ('user' or 'assistant')
        timestamp: Message timestamp
    """

    content: str
    role: str
    timestamp: datetime

    def to_dict(self) -> Dict[str, str]:
        """Convert to dict format, only exposing role and content."""
        return {
            "role": self.role,
            "content": self.content,
        }


class HistoryCache:
    """Dialog history cache (formerly DialogueHistoryCache)"""

    def __init__(self, max_history_size: int = DEFAULT_MAX_HISTORY_SIZE) -> None:
        self._history: List[DialogueMessage] = []
        self.max_history_size: int = max_history_size

    def get_history(self) -> List[DialogueMessage]:
        return self._history.copy()

    def get_messages(self, num: int) -> List[Dict[str, Any]]:
        if num <= 0:
            num = self.max_history_size

        messages = (
            self._history[-num:]
            if len(self._history) > num
            else self._history
        )

        return [msg.to_dict() for msg in messages]

    def add_message(self, message: DialogueMessage) -> None:
        self._history.append(message)
        if len(self._history) > self.max_history_size:
            removed = self._history.pop(0)
            logger.debug(
                "History full, removed oldest message",
                removed_role=removed.role,
                current_size=len(self._history),
            )

    def clear(self) -> None:
        self._history.clear()
        logger.debug("Dialog history cleared")


class HistoryManager:
    """Session history manager (formerly ContextManager)

    Manages session dialog history, provides message add, query and clear functions.
    """

    def __init__(self, max_history_size: int = DEFAULT_MAX_HISTORY_SIZE) -> None:
        self._dialogue_history: HistoryCache = HistoryCache(max_history_size)

    @property
    def dialogue_history(self) -> HistoryCache:
        return self._dialogue_history

    def get_latest_k_messages(self, k: int) -> List[Dict[str, Any]]:
        return self._dialogue_history.get_messages(k)

    def get_history(self) -> List[Dict[str, Any]]:
        return self._dialogue_history.get_messages(-1)

    def add_message(
        self,
        content: str,
        role: str,
        timestamp: Optional[datetime] = None,
    ) -> None:
        message = DialogueMessage(
            content=content,
            role=role,
            timestamp=timestamp or datetime.now(timezone.utc),
        )
        self._dialogue_history.add_message(message)

    def add_assistant_message(self, content: str) -> None:
        self.add_message(content=content, role="assistant")

    def add_user_message(self, content: str) -> None:
        self.add_message(content=content, role="user")

    def clear(self) -> None:
        self._dialogue_history.clear()
        logger.debug("Session history cleared")
