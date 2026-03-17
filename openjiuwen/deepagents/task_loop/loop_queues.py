# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025.
# All rights reserved.
"""Dual-queue buffer for steer/follow_up messages.

Bridges EventHandler -> Executor/Loop by providing two
async-safe queues:
- steering: drained by the executor before each
  inner invoke.
- follow_up: drained by outer task loop after
  each iteration completes.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import List


@dataclass
class LoopQueues:
    """Buffer between EventHandler and Executor/Loop.

    Attributes:
        steering: Queue for steer messages, drained
            by the executor before each invoke.
        follow_up: Queue for follow-up messages,
            drained by the outer task loop.
    """

    steering: asyncio.Queue = field(
        default_factory=asyncio.Queue
    )
    follow_up: asyncio.Queue = field(
        default_factory=asyncio.Queue
    )

    def push_steer(self, msg: str) -> None:
        """Push a steering message.

        Args:
            msg: Steering instruction text.
        """
        self.steering.put_nowait(msg)

    def push_follow_up(self, msg: str) -> None:
        """Push a follow-up message.

        Args:
            msg: Follow-up content text.
        """
        self.follow_up.put_nowait(msg)

    def has_follow_up(self) -> bool:
        """Return whether follow-up messages are pending."""
        return not self.follow_up.empty()

    def drain_steering(self) -> List[str]:
        """Drain all pending steering messages.

        Returns:
            List of steering message strings.
        """
        msgs: List[str] = []
        while not self.steering.empty():
            try:
                msgs.append(self.steering.get_nowait())
            except asyncio.QueueEmpty:
                break
        return msgs

    def drain_follow_up(self) -> List[str]:
        """Drain all pending follow-up messages.

        Returns:
            List of follow-up message strings.
        """
        msgs: List[str] = []
        while not self.follow_up.empty():
            try:
                msgs.append(self.follow_up.get_nowait())
            except asyncio.QueueEmpty:
                break
        return msgs


__all__ = ["LoopQueues"]
