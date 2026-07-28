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
    # online 加速后：todo 仅约束 skill_tool 标准流程（turbo 走系统 task 进度）
    assert "在执行 **skill_tool 标准流程** 的步骤前" in text
    assert "todo" in text.lower()


def test_skill_protocol_en_contains_todo_before_execution():
    text = _build_skill_protocol_section_text("en")
    assert "before executing the skill steps" in text
    assert "todo" in text.lower()


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
    """加速通道硬约束：第一个工具调用必须是 skill_turbo_tool。"""
    text = _build_skill_protocol_section_text("cn")
    assert "第一个工具调用必须是" in text
    assert "skill_turbo_tool" in text
    assert "禁止" in text and "web_search" in text and "fetch_webpage" in text
    assert "仍须立即调用" in text


def test_skill_protocol_en_requires_acceleration_first():
    """Acceleration channel hard constraint: FIRST tool call MUST be skill_turbo_tool."""
    text = _build_skill_protocol_section_text("en")
    assert "FIRST tool call MUST be" in text
    assert "skill_turbo_tool" in text
    assert "FORBIDDEN" in text and "web_search" in text and "fetch_webpage" in text
    assert "you MUST still call" in text
