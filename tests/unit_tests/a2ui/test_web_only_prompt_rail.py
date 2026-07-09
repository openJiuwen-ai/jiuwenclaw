# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from types import SimpleNamespace

import pytest
from openjiuwen.core.single_agent.rail.base import InvokeInputs

from jiuwenavatar.agents.harness.common.prompt.prompt_builder import LocalSectionName
from jiuwenavatar.agents.harness.common.rails.response_prompt_rail import ResponsePromptRail
from jiuwenavatar.server.runtime.a2ui.config import A2UIConfig


class _FakePromptBuilder:
    def __init__(self) -> None:
        self.language = "en"
        self.sections = {}

    def add_section(self, section) -> None:
        self.sections[section.name] = section

    def remove_section(self, name: str) -> None:
        self.sections.pop(name, None)


@pytest.mark.asyncio
async def test_response_prompt_rail_does_not_inject_a2ui_for_non_web_channel(monkeypatch):
    """Non-Web model calls should not receive A2UI prompt instructions."""
    monkeypatch.setattr(
        "jiuwenavatar.server.runtime.a2ui.config.get_current_a2ui_config",
        lambda: A2UIConfig(enabled=True),
    )
    rail = ResponsePromptRail()
    rail.system_prompt_builder = _FakePromptBuilder()

    await rail.before_model_call(SimpleNamespace(inputs={"channel": "feishu"}))

    assert "response" in rail.system_prompt_builder.sections
    assert LocalSectionName.A2UI not in rail.system_prompt_builder.sections


@pytest.mark.asyncio
async def test_response_prompt_rail_keeps_a2ui_for_web_channel(monkeypatch):
    """Web model calls should keep existing A2UI prompt behavior."""
    monkeypatch.setattr(
        "jiuwenavatar.server.runtime.a2ui.config.get_current_a2ui_config",
        lambda: A2UIConfig(enabled=True),
    )
    rail = ResponsePromptRail()
    rail.system_prompt_builder = _FakePromptBuilder()

    await rail.before_model_call(SimpleNamespace(inputs={"channel": "web"}))

    assert "response" in rail.system_prompt_builder.sections
    assert LocalSectionName.A2UI in rail.system_prompt_builder.sections


@pytest.mark.asyncio
async def test_response_prompt_rail_keeps_web_channel_from_invoke_context(monkeypatch):
    """Model-call inputs drop channel, so Web channel must survive via invoke context."""
    monkeypatch.setattr(
        "jiuwenavatar.server.runtime.a2ui.config.get_current_a2ui_config",
        lambda: A2UIConfig(enabled=True),
    )
    rail = ResponsePromptRail()
    rail.system_prompt_builder = _FakePromptBuilder()
    extra = {}

    await rail.before_invoke(
        SimpleNamespace(
            inputs=InvokeInputs(query="generate an A2UI form", conversation_id="web_session_1"),
            extra=extra,
        )
    )
    await rail.before_model_call(SimpleNamespace(inputs=SimpleNamespace(), extra=extra))

    assert LocalSectionName.A2UI in rail.system_prompt_builder.sections


@pytest.mark.asyncio
async def test_response_prompt_rail_keeps_non_web_bypass_from_invoke_context(monkeypatch):
    """Non-Web channel inferred from conversation id should still bypass A2UI."""
    monkeypatch.setattr(
        "jiuwenavatar.server.runtime.a2ui.config.get_current_a2ui_config",
        lambda: A2UIConfig(enabled=True),
    )
    rail = ResponsePromptRail()
    rail.system_prompt_builder = _FakePromptBuilder()
    extra = {}

    await rail.before_invoke(
        SimpleNamespace(
            inputs=InvokeInputs(query="generate an A2UI form", conversation_id="feishu_session_1"),
            extra=extra,
        )
    )
    await rail.before_model_call(SimpleNamespace(inputs=SimpleNamespace(), extra=extra))

    assert LocalSectionName.A2UI not in rail.system_prompt_builder.sections
