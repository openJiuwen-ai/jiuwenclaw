# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Event Trigger — 事件触发器，监听外部事件（如 Git 事件、IM 事件等）."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from jiuwenavatar.gateway.trigger.base import ITrigger, TriggerCallback
from jiuwenavatar.gateway.trigger.models import TriggerConfig

logger = logging.getLogger(__name__)


class EventTrigger(ITrigger):
    """Event trigger that fires when a matching event is received.

    Events are pushed into the TriggerEngine via `emit_event()`.
    The engine matches event_source + event_type against registered triggers.
    """

    def __init__(self, config: TriggerConfig, callback: TriggerCallback) -> None:
        super().__init__(config, callback)
        self._registered = False

    async def start(self) -> None:
        event_source = self._config.event_source
        event_type = self._config.event_type
        if not event_source or not event_type:
            logger.error("EventTrigger %s: event_source and event_type are required", self.trigger_id)
            return
        self._registered = True
        logger.info(
            "EventTrigger %s registered for %s/%s",
            self.trigger_id, event_source, event_type,
        )

    async def stop(self) -> None:
        self._registered = False
        logger.info("EventTrigger %s unregistered", self.trigger_id)

    def is_running(self) -> bool:
        return self._registered

    def matches_event(self, source: str, event_type: str) -> bool:
        """Check if this trigger matches the given event."""
        if not self._registered or not self._config.enabled:
            return False
        return (
            self._config.event_source == source
            and self._config.event_type == event_type
        )

    async def handle_event(self, event_data: dict[str, Any]) -> None:
        """Handle a matching event and fire the trigger."""
        prompt = self._config.trigger_prompt
        if event_data:
            prompt += f"\n\n事件数据:\n```json\n{json.dumps(event_data, indent=2, ensure_ascii=False)[:2000]}\n```"

        await self.fire(prompt)
        logger.info(
            "EventTrigger %s fired from event %s/%s",
            self.trigger_id, self._config.event_source, self._config.event_type,
        )
