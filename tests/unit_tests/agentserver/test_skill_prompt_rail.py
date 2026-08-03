# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for SkillProtocolPromptRail prompt content."""

from __future__ import annotations

import asyncio

from jiuwenclaw.agentserver.deep_agent.rails.skill_prompt_rail import (
    SkillProtocolPromptRail,
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


def test_skill_protocol_cn_allows_tool_owned_stage_messages():
    text = _build_skill_protocol_section_text("cn")
    assert "阶段状态和阶段消息由工具事件唯一生成" in text
    assert "禁止自行输出" in text


def test_skill_protocol_en_allows_tool_owned_stage_messages():
    text = _build_skill_protocol_section_text("en")
    assert "stage status and stage messages are emitted exclusively by tool events" in text
    assert "must not declare" in text


class _FakeBuilder:
    def __init__(self, language: str = "cn") -> None:
        self.language = language
        self._sections: dict[str, object] = {}

    def add_section(self, section) -> None:
        self._sections[section.name] = section

    def get_section(self, name: str):
        return self._sections.get(name)

    def remove_section(self, name: str) -> None:
        self._sections.pop(name, None)


class _FakeAgent:
    def __init__(self, builder) -> None:
        self.system_prompt_builder = builder


class _FakeCtx:
    def __init__(self, agent) -> None:
        self.agent = agent


def test_before_model_call_writes_to_current_builder_after_hot_reload():
    """热重载替换 agent.system_prompt_builder 后，section 必须落到新 builder 上。

    回归 _hot_reload_system_prompt 新建 builder、保留型 rail 不重新 init() 导致的旧引用失效。
    """
    old_builder = _FakeBuilder()
    agent = _FakeAgent(old_builder)

    rail = SkillProtocolPromptRail()
    rail.init(agent)
    assert rail.system_prompt_builder is old_builder  # init 时缓存旧 builder

    # 模拟热重载：agent 换上全新 builder
    new_builder = _FakeBuilder(language="cn")
    agent.system_prompt_builder = new_builder

    asyncio.run(rail.before_model_call(_FakeCtx(agent)))

    assert new_builder.get_section("skill_protocol") is not None
    assert old_builder.get_section("skill_protocol") is None
    assert rail.system_prompt_builder is new_builder  # 缓存被刷新为新 builder


def test_before_model_call_falls_back_to_cached_builder_without_ctx_agent():
    """ctx.agent 缺失时回退到 init() 缓存的 builder，保持兼容。"""
    builder = _FakeBuilder()
    rail = SkillProtocolPromptRail()
    rail.init(_FakeAgent(builder))

    asyncio.run(rail.before_model_call(_FakeCtx(agent=None)))

    assert builder.get_section("skill_protocol") is not None


def test_skill_protocol_cn_requires_cancelled_on_abandon():
    text = _build_skill_protocol_section_text("cn")
    assert "todo_modify" in text
    assert "cancelled" in text
    assert "不生成 PPT" in text


def test_skill_protocol_en_requires_cancelled_on_abandon():
    text = _build_skill_protocol_section_text("en")
    assert "todo_modify" in text
    assert "cancelled" in text
    assert "not to generate the PPT" in text


def test_skill_protocol_cn_requires_acceleration_first():
    """加速通道默认约束：第一个工具调用必须是 skill_acceleration_exec（含唯一例外）。"""
    text = _build_skill_protocol_section_text("cn")
    assert "默认优先" in text
    assert "第一个工具调用必须是" in text
    assert "仍须立即调用" in text
    # 唯一例外：仅限 skill_acceleration_exec 不会自行处理的操作
    assert "唯一例外" in text
    assert "不会自行处理" in text
    assert "不构成" in text
    assert "技能类" in text
    # 误触恢复：加载 pptx-craft 正文不属于例外，仍须走加速通道
    assert "pptx-craft 正文" in text


def test_skill_protocol_en_requires_acceleration_first():
    """Acceleration default constraint: FIRST tool call MUST be skill_acceleration_exec (with only exception)."""
    text = _build_skill_protocol_section_text("en")
    assert "Default priority" in text
    assert "FIRST tool call MUST be" in text
    assert "you MUST still call" in text
    # Only exception: only for actions skill_acceleration_exec does NOT handle internally
    assert "Only exception" in text
    assert "does not handle internally" in text
    assert "constitute an exception" in text
    assert "skill-type" in text
    # 误触恢复：加载 pptx-craft body 不属于例外，仍须走加速通道
    assert "pptx-craft body" in text
