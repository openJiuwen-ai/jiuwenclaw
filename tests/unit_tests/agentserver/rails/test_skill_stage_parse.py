# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from jiuwenswarm.agents.harness.common.rails.skill_stage_parse import (
    build_todos_from_skill_stages,
    extract_skill_markdown,
    is_top_level_skill_body,
    parse_skill_stage_headings,
)


PPTX_HEADINGS = """
# PPT 全流程

## 入口路由

## 阶段 1：需求澄清 & 环境检测

### 1.1 请求分类

## 阶段 2：内容设计

## 阶段 3：视觉设计

## 阶段 4：HTML 生成、修复与导出
"""


def test_parse_numbered_chinese_stage_headings() -> None:
    stages = parse_skill_stage_headings(PPTX_HEADINGS)
    assert [number for number, _ in stages] == [1, 2, 3, 4]
    assert stages[0][1] == "阶段 1：需求澄清 & 环境检测"
    assert stages[3][1] == "阶段 4：HTML 生成、修复与导出"


def test_parse_english_stage_headings() -> None:
    markdown = (
        "## Stage 1: News/Article Detection\n"
        "## Stage 2: Multi-Strategy Content Extraction\n"
        "## Stage 5: Entity Extraction (LLM)\n"
    )
    stages = parse_skill_stage_headings(markdown)
    assert [number for number, _ in stages] == [1, 2, 5]
    assert stages[0][1] == "Stage 1: News/Article Detection"


def test_ignore_unnumbered_stage_planning_heading() -> None:
    markdown = (
        "## 阶段规划\n\n"
        "| 阶段 | 完成条件 |\n"
        "| --- | --- |\n"
        "| 明确目标 | 完成 |\n"
    )
    assert parse_skill_stage_headings(markdown) == []


def test_build_todos_follows_parsed_count() -> None:
    items = build_todos_from_skill_stages(
        parse_skill_stage_headings(PPTX_HEADINGS)
    )
    assert [item["id"] for item in items] == [
        "skill_stage_1",
        "skill_stage_2",
        "skill_stage_3",
        "skill_stage_4",
    ]
    assert items[0]["status"] == "in_progress"
    assert items[1]["status"] == "pending"


def test_extract_skill_markdown_from_nested_data() -> None:
    result = {"success": True, "data": {"skill_content": PPTX_HEADINGS}}
    assert "阶段 1：" in extract_skill_markdown(result)


def test_top_level_skill_body_paths() -> None:
    assert is_top_level_skill_body("")
    assert is_top_level_skill_body("SKILL.md")
    assert is_top_level_skill_body("./SKILL.md")
    assert not is_top_level_skill_body("designer/SKILL.md")
