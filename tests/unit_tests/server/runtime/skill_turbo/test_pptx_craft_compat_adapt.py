# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""SkillTurbo × 现行 pptx-craft 普通分支兼容适配回归（内嵌 fixture，无本机绝对路径）。"""

from __future__ import annotations

import json

import pytest

from jiuwenswarm.server.runtime.skill_turbo.skill_codes.ppt.ppt_page_gen import (
    _build_content_template_fill_prompt,
    _extract_designer_section,
    _uses_content_template_fill,
    _uses_structural_template_fill,
    _validate_custom_content_template_fill_output,
)
from jiuwenswarm.server.runtime.skill_turbo.skill_turbo_tools import skill_turbo

# 模拟现行 designer.md 关键结构：预算为加粗正文；关键原则后接「禁止事项」（无质量控制清单）。
_DESIGNER_MD_FIXTURE = """
## 角色定位

无关前言。

**E. 页面内容预算契约（生成 HTML 前必须完成）**

预算片段 MARKER_BUDGET_CONTRACT

#### 3.2 版面计算

版面计算片段仍属预算段。

### 阶段 4：交付

交付片段不应进入预算抽取 MARKER_STAGE4_LEAK

## 弹性布局模式

弹性布局片段 MARKER_FLEX

## HTML 代码规范

代码规范。

## 页面布局规范

布局规范片段 MARKER_LAYOUT

## 视觉设计规范

视觉规范片段 MARKER_VISUAL

## 图表与数据可视化

图表规范。

## 图片使用规范

图片规范。

## 关键原则

### 防溢出核心策略

关键原则片段 MARKER_CRITICAL

### 防空白核心策略

防空白说明。

## 禁止事项

禁止事项片段 MARKER_FORBIDDEN_LEAK

## 输出要求

输出说明。
""".strip()


def _minimal_fill_prompt(*, style_id: str, outline_page: str = "- type: content\n  title: 测试页", **kwargs) -> str:
    return _build_content_template_fill_prompt(
        page_number=3,
        style_id=style_id,
        style_text="style stub",
        outline_page=outline_page,
        research_page="research stub",
        outline_full="OUTLINE_FULL_MARKER 不应出现在填槽 prompt",
        seed_html="<html><main>{{PAGE_CONTENT}}</main></html>",
        **kwargs,
    )


def test_extract_designer_section_matches_current_anchors():
    extracted = _extract_designer_section(_DESIGNER_MD_FIXTURE)

    assert "MARKER_BUDGET_CONTRACT" in extracted
    assert "MARKER_CRITICAL" in extracted
    assert "MARKER_FORBIDDEN_LEAK" not in extracted
    assert "MARKER_STAGE4_LEAK" not in extracted
    assert "## 禁止事项" not in extracted


def test_extract_designer_section_content_fill_uses_density_checklist_not_long_chapters():
    extracted = _extract_designer_section(
        _DESIGNER_MD_FIXTURE,
        include_charts=True,
        for_content_template_fill=True,
    )

    assert "PAGE_CONTENT 密度硬约束" in extracted
    assert "CHART_SCAFFOLD" in extracted
    assert "MARKER_BUDGET_CONTRACT" not in extracted
    assert "MARKER_FLEX" not in extracted
    assert "MARKER_LAYOUT" not in extracted
    assert "MARKER_VISUAL" not in extracted
    assert "MARKER_CRITICAL" not in extracted
    assert "## 图表与数据可视化" in extracted


def test_content_fill_prompt_omits_outline_full_and_long_designer_chapters():
    prompt = _minimal_fill_prompt(
        style_id="business-classic",
        outline_page="**类型**：trend\n**标题**：趋势页",
        designer_md_text=_DESIGNER_MD_FIXTURE,
    )

    assert "OUTLINE_FULL_MARKER" not in prompt
    assert "大纲全文" not in prompt
    assert "PAGE_CONTENT 密度硬约束" in prompt
    assert "MARKER_FLEX" not in prompt
    assert "MARKER_LAYOUT" not in prompt
    assert "MARKER_VISUAL" not in prompt
    assert "MARKER_CRITICAL" not in prompt
    assert "## 图表与数据可视化" in prompt  # trend 为图表候选页


def test_content_fill_prompt_non_chart_page_skips_chart_section():
    prompt = _minimal_fill_prompt(
        style_id="business-classic",
        outline_page="**类型**：case\n**标题**：案例页",
        designer_md_text=_DESIGNER_MD_FIXTURE,
    )
    assert "PAGE_CONTENT 密度硬约束" in prompt
    assert "## 图表与数据可视化" not in prompt
    assert "OUTLINE_FULL_MARKER" not in prompt


def test_content_fill_prompt_custom_vs_preset_page_content_rules():
    custom_prompt = _minimal_fill_prompt(style_id="custom")
    preset_prompt = _minimal_fill_prompt(style_id="business-classic")

    assert "一个且仅一个首层根容器" not in custom_prompt
    assert "至少两个直接子块" in custom_prompt
    assert "THEME_CSS_VARIABLES" in custom_prompt
    assert "只允许替换 3 类占位符" not in custom_prompt
    assert "只替换三处占位符" not in custom_prompt
    assert "一个且仅一个首层根容器" in preset_prompt
    assert "至少两个直接子块" not in preset_prompt
    assert "只允许替换 3 类占位符" in preset_prompt


_RESEARCH_OUTLINE = """**类型**：content
**标题**：测试内容页
**研究需求**：✅
**页研究查询**：foo
"""


def test_uses_content_template_fill_allows_custom():
    assert _uses_content_template_fill("custom", "content", _RESEARCH_OUTLINE) is True
    assert _uses_content_template_fill("business-classic", "content", _RESEARCH_OUTLINE) is True
    assert _uses_structural_template_fill("custom", "cover") is True
    assert _uses_content_template_fill("custom", "cover", _RESEARCH_OUTLINE) is False


def test_validate_custom_content_fill_theme_and_placeholders():
    # _is_valid_html 要求长度 >= 200；_validate_slide_dom 要求 class="...ppt-slide"
    pad = "<!-- pad -->" * 20
    seed = (
        '<!DOCTYPE html><html><head><style id="theme-contract">'
        "{{THEME_CSS_VARIABLES}}</style></head><body>"
        f"{pad}"
        '<div class="ppt-slide"><div class="content-safe">'
        "<header><h1>{{PAGE_TITLE}}</h1></header>"
        '<main class="page-main flex-1">{{PAGE_CONTENT}}</main>'
        "<footer><p>{{PAGE_FOOTER}}</p></footer>"
        "</div></div></body></html>"
    )
    filled_ok = seed.replace("{{THEME_CSS_VARIABLES}}", "--color-text:#111;")
    filled_ok = filled_ok.replace("{{PAGE_TITLE}}", "标题")
    filled_ok = filled_ok.replace(
        "{{PAGE_CONTENT}}",
        '<section class="flex-shrink-0">a</section>'
        '<div class="flex-1 min-h-0">b</div>',
    )
    filled_ok = filled_ok.replace("{{PAGE_FOOTER}}", "来源")
    ok, reason = _validate_custom_content_template_fill_output(seed, filled_ok)
    assert ok, reason

    filled_theme_left = filled_ok.replace("--color-text:#111;", "{{THEME_CSS_VARIABLES}}")
    ok2, reason2 = _validate_custom_content_template_fill_output(seed, filled_theme_left)
    assert not ok2
    assert reason2 == "unfilled_placeholders"


@pytest.mark.asyncio
async def test_skill_turbo_template_path_bypass_hard_fails():
    raw = await skill_turbo.invoke(
        {"query": r"用模板目录：C:\templates\demo-pack 生成一份产品发布会 PPT"}
    )
    result = json.loads(raw) if isinstance(raw, str) else raw

    assert result.get("success") is False
    error = str(result.get("error") or "")
    assert "自定义模板" in error or "模板包" in error
    assert "skill_tool" in error
