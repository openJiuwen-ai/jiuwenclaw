# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for SkillProtocolPromptRail prompt content."""

from __future__ import annotations

from jiuwenclaw.agentserver.deep_agent.rails.skill_prompt_rail import (
    _build_skill_protocol_section_text,
)


def test_skill_protocol_cn_contains_todo_before_execution():
    text = _build_skill_protocol_section_text("cn")
    assert "在执行skill步骤前" in text
    assert "todo" in text.lower()


def test_skill_protocol_en_contains_todo_before_execution():
    text = _build_skill_protocol_section_text("en")
    assert "before executing the skill steps" in text
    assert "todo" in text.lower()
