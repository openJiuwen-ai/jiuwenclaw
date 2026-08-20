# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""identity 拆层（identity + jiuwenswarm.conventions）测试。

单脚本自包含，不依赖外部 golden 文件：
- 渲染结构：build_agent_identity_prompt 把 identity + conventions 按 priority 拼装，
  identity 在前（section 内容打桩为占位串，只锁拼装结构，不锁文案）
- 拆分结构：identity 只剩人设、conventions 承接规则且 priority 紧跟
- 专家场景预演：identity 被同名 section 替换时 conventions 不受影响
"""

from pathlib import Path

import pytest

from openjiuwen.harness.prompts import PromptSection, SystemPromptBuilder

import jiuwenswarm.agents.harness.common.prompt.prompt_builder as pb


@pytest.fixture(autouse=True)
def fixed_dirs(monkeypatch: pytest.MonkeyPatch) -> None:
    """固定路径与 shell 环境探测，保证渲染可复现。"""
    monkeypatch.setattr(pb, "_get_config_dir", lambda: Path("<CONFIG_DIR>"))
    monkeypatch.setattr(pb, "get_agent_workspace_dir", lambda: Path("<AGENT_WORKSPACE_DIR>"))
    monkeypatch.setattr(pb, "get_agent_memory_dir", lambda: Path("<MEMORY_DIR>"))
    monkeypatch.setattr(pb, "get_agent_skills_dir", lambda: Path("<SKILLS_DIR>"))
    monkeypatch.setattr(pb, "get_deepagent_todo_dir", lambda: Path("<TODO_DIR>"))
    monkeypatch.setattr(
        pb, "build_shell_environment_prompt", lambda language, os_type: "<SHELL_ENV>"
    )


def test_render_composes_identity_then_conventions(
        monkeypatch: pytest.MonkeyPatch,
) -> None:
    """渲染结构：identity(10) 在前、conventions(15) 在后，两段都进入输出。

    section 内容打桩为占位串——本用例只锁「拼装结构」；文案正确性由
    test_identity_section_contains_persona_only /
    test_conventions_section_keeps_rules 的锚点断言把守。
    """
    monkeypatch.setattr(
        pb,
        "_identity_prompt",
        lambda language: PromptSection(
            name="identity",
            content={language: "<PERSONA>"},
            priority=pb.PromptPriority.IDENTITY,
        ),
    )
    monkeypatch.setattr(
        pb,
        "_conventions_prompt",
        lambda language: PromptSection(
            name="jiuwenswarm.conventions",
            content={language: "<RULES>"},
            priority=pb.PromptPriority.CONVENTIONS,
        ),
    )
    prompt = pb.build_agent_identity_prompt(language="zh")
    assert "<PERSONA>" in prompt
    assert "<RULES>" in prompt
    assert prompt.index("<PERSONA>") < prompt.index("<RULES>")


def test_identity_section_contains_persona_only() -> None:
    section = pb._identity_prompt("cn")
    assert section.name == "identity"
    assert section.priority == pb.PromptPriority.IDENTITY == 10
    text = section.content["cn"]
    assert "你是一个私人智能体" in text
    assert "JiuwenSwarm 内部数据" not in text
    assert "任务执行准则" not in text


def test_conventions_section_keeps_rules() -> None:
    section = pb._conventions_prompt("cn")
    assert section.name == "jiuwenswarm.conventions"
    assert section.priority == pb.PromptPriority.CONVENTIONS == 15
    text = section.content["cn"]
    for anchor in (
            "# JiuwenSwarm 内部数据",
            "## 目录理解规则",
            "## 运行环境",
            "## 任务执行准则",
            "## 输出文件放置规范",
            "## 文件发送",
            "## 网页文件下载协作",
            "## 技能工具适配",
    ):
        assert anchor in text, f"conventions 缺少锚点 {anchor}"


def test_expert_identity_replace_keeps_conventions() -> None:
    """预演专家覆盖：同名 identity section 替换后，conventions 原样保留。"""
    builder = SystemPromptBuilder(language="cn")
    builder.add_section(pb._identity_prompt("cn"))
    builder.add_section(pb._conventions_prompt("cn"))

    builder.add_section(
        PromptSection(
            name="identity",
            content={"cn": "你是安全评审专家老沈。"},
            priority=pb.PromptPriority.IDENTITY,
        )
    )
    prompt = builder.build()
    assert "你是安全评审专家老沈。" in prompt
    assert "你是一个私人智能体" not in prompt
    assert "# JiuwenSwarm 内部数据" in prompt
    # 渲染顺序不变：identity（10）在 conventions（15）之前
    assert prompt.index("你是安全评审专家老沈。") < prompt.index("# JiuwenSwarm 内部数据")
