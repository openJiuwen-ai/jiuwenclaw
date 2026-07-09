# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Trigger base — 触发器抽象基类."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable, Coroutine

from jiuwenavatar.gateway.trigger.models import TriggerConfig

# Callback type: when a trigger fires, call this with (trigger_config, prompt)
TriggerCallback = Callable[[TriggerConfig, str], Coroutine[Any, Any, None]]


class ITrigger(ABC):
    """Abstract base class for all trigger types.

    Each trigger type (Cron, Heartbeat, Webhook, Event) implements this interface.
    """

    def __init__(self, config: TriggerConfig, callback: TriggerCallback) -> None:
        self._config = config
        self._callback = callback

    @property
    def config(self) -> TriggerConfig:
        return self._config

    @property
    def trigger_id(self) -> str:
        return self._config.id

    @abstractmethod
    async def start(self) -> None:
        """Start the trigger (begin listening / scheduling)."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Stop the trigger."""
        ...

    @abstractmethod
    def is_running(self) -> bool:
        """Check if the trigger is currently active."""
        ...

    async def fire(self, prompt: str | None = None) -> None:
        """Fire the trigger — invoke the callback with the configured prompt."""
        effective_prompt = prompt or self._config.trigger_prompt
        await self._callback(self._config, effective_prompt)
