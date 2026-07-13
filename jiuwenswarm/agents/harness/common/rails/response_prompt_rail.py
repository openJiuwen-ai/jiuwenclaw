# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Inject the response/message-format section before each model call."""

from __future__ import annotations

import logging

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts import PromptSection
from openjiuwen.harness.rails.base import DeepAgentRail

from jiuwenswarm.common.context_keys import JIUWENSWARM_CHANNEL_CONTEXT_KEY
from jiuwenswarm.agents.harness.common.prompt.prompt_builder import (
    LocalSectionName,
    PromptPriority,
    _response_prompt,
)

logger = logging.getLogger(__name__)

SKIP_A2UI_PROMPT_CONTEXT_KEY = "skip_a2ui"


class ResponsePromptRail(DeepAgentRail):
    """Inject the response section as an independent prompt section."""

    priority = 5

    def __init__(self) -> None:
        super().__init__()
        self.system_prompt_builder = None
        self._runtime_channel: str | None = None

    def init(self, agent) -> None:
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)

    def uninit(self, agent) -> None:
        if self.system_prompt_builder is not None:
            self.system_prompt_builder.remove_section("response")
            self.system_prompt_builder.remove_section(LocalSectionName.A2UI)
        self.system_prompt_builder = None
        self._runtime_channel = None

    def set_channel(self, channel: str | None) -> None:
        value = str(channel or "").strip()
        self._runtime_channel = value or None

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        channel = self._resolve_channel(ctx)
        extra = getattr(ctx, "extra", None)
        if isinstance(extra, dict) and channel:
            extra[JIUWENSWARM_CHANNEL_CONTEXT_KEY] = channel
        if isinstance(extra, dict) and self._should_skip_a2ui(ctx):
            extra[SKIP_A2UI_PROMPT_CONTEXT_KEY] = True

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        if self.system_prompt_builder is None:
            return

        language = self.system_prompt_builder.language or "cn"
        self.system_prompt_builder.add_section(_response_prompt(language))
        self._sync_a2ui_prompt_section(
            self._resolve_channel(ctx),
            skip_a2ui=self._should_skip_a2ui(ctx),
        )

    def _should_skip_a2ui(self, ctx: AgentCallbackContext) -> bool:
        inputs = getattr(ctx, "inputs", None)
        if isinstance(inputs, dict) and inputs.get(SKIP_A2UI_PROMPT_CONTEXT_KEY) is True:
            return True
        if getattr(inputs, SKIP_A2UI_PROMPT_CONTEXT_KEY, False) is True:
            return True

        extra = getattr(ctx, "extra", None)
        return isinstance(extra, dict) and extra.get(SKIP_A2UI_PROMPT_CONTEXT_KEY) is True

    def _resolve_channel(self, ctx: AgentCallbackContext) -> str | None:
        """Read the request channel from callback inputs when available."""
        inputs = getattr(ctx, "inputs", None)
        if isinstance(inputs, dict):
            value = inputs.get("channel")
            if value is not None:
                return str(value)
        else:
            value = getattr(inputs, "channel", None)
            if value is not None:
                return str(value)

        extra = getattr(ctx, "extra", None)
        if isinstance(extra, dict):
            value = extra.get(JIUWENSWARM_CHANNEL_CONTEXT_KEY)
            if value is not None:
                return str(value)

        if self._runtime_channel is not None:
            return self._runtime_channel

        conversation_id = (
            inputs.get("conversation_id")
            if isinstance(inputs, dict)
            else getattr(inputs, "conversation_id", None)
        )
        if isinstance(conversation_id, str) and "_" in conversation_id:
            channel = conversation_id.split("_", 1)[0]
            if channel == "sess":
                return "web"
            if channel:
                return channel

        return None

    def _sync_a2ui_prompt_section(self, channel: str | None, *, skip_a2ui: bool = False) -> None:
        """Inject or remove the A2UI prompt section from runtime config."""
        if self.system_prompt_builder is None:
            return

        try:
            from jiuwenswarm.server.runtime.a2ui.integration import is_a2ui_channel

            if skip_a2ui:
                self.system_prompt_builder.remove_section(LocalSectionName.A2UI)
                return

            if not is_a2ui_channel(channel):
                self.system_prompt_builder.remove_section(LocalSectionName.A2UI)
                return

            from jiuwenswarm.server.runtime.a2ui.config import get_current_a2ui_config
            from jiuwenswarm.server.runtime.a2ui.runtime.prompt import build_a2ui_prompt_section

            if not get_current_a2ui_config().enabled:
                self.system_prompt_builder.remove_section(LocalSectionName.A2UI)
                return

            self.system_prompt_builder.add_section(
                PromptSection(
                    name=LocalSectionName.A2UI,
                    content={
                        "cn": build_a2ui_prompt_section("cn"),
                        "en": build_a2ui_prompt_section("en"),
                    },
                    priority=PromptPriority.A2UI,
                )
            )
        except Exception:
            logger.exception("Failed to sync A2UI prompt section")
            self.system_prompt_builder.remove_section(LocalSectionName.A2UI)
