# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Message Envelope Module

Defines the lightweight message container for routing between agents.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class MessageEnvelope:
    """Message envelope for routing between agents.

    Attributes:
        message_id: Unique message identifier
        message: Message payload
        sender: Sender agent ID (optional)
        recipient: Recipient agent ID (optional, for P2P)
        topic_id: Topic ID (optional, for Pub-Sub)
        session_id: Session ID (optional)
        metadata: Additional metadata
    """
    message_id: str
    message: Any
    sender: Optional[str] = None
    recipient: Optional[str] = None
    topic_id: Optional[str] = None
    session_id: Optional[str] = None
    metadata: dict = field(default_factory=dict)

    def is_p2p(self) -> bool:
        """Check if this is a P2P message.

        Returns:
            True if recipient is specified
        """
        return self.recipient is not None

    def is_pubsub(self) -> bool:
        """Check if this is a Pub-Sub message.

        Returns:
            True if topic_id is specified
        """
        return self.topic_id is not None

    def __repr__(self) -> str:
        return (
            f"MessageEnvelope(message_id={self.message_id!r}, "
            f"sender={self.sender!r}, recipient={self.recipient!r}, "
            f"topic_id={self.topic_id!r}, session_id={self.session_id!r}, "
            f"message=<{type(self.message).__name__}>)"
        )
