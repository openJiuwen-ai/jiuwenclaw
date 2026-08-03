# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
# pylint: disable=protected-access

"""deep_research.py 正则回退解析 & json_utils.py 报错增强 单元测试。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_PKG_ROOT = Path(__file__).resolve().parents[3]


def _load_module(module_name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


# 加载依赖链
_load_module(
    "jiuwenclaw.agentserver.skill_turbo.plan_node",
    _PKG_ROOT / "jiuwenclaw/agentserver/skill_turbo/plan_node.py",
)
_load_module(
    "jiuwenclaw.agentserver.skill_turbo.skill_codes.ppt.ppt_common",
    _PKG_ROOT / "jiuwenclaw/agentserver/skill_turbo/skill_codes/ppt/ppt_common.py",
)
dr = _load_module(
    "jiuwenclaw.agentserver.skill_turbo.skill_codes.ppt.deep_research",
    _PKG_ROOT / "jiuwenclaw/agentserver/skill_turbo/skill_codes/ppt/deep_research.py",
)
ju = _load_module(
    "jiuwenclaw.agentserver.skill_turbo.json_utils",
    _PKG_ROOT / "jiuwenclaw/agentserver/skill_turbo/json_utils.py",
)


# ──────────────────────────────────────────────
# 测试用大纲数据（模拟真实 P4.3 输出格式）
# ──────────────────────────────────────────────

_OUTLINE_WITH_BOLD_MARKERS = """# 大纲：华为小艺Claw产品完整功能

## 页面规划

### P1:
**类型**：cover
**研究需求**：❌
**标题**：华为小艺Claw产品完整功能解析
**内容概要**：封面页
**研究查询**：-
**数据需求**：-

### P2:
**类型**：chapter
**研究需求**：✅
**标题**：小艺Claw定位为鸿蒙系统级AI智能体，实现从"对话助手"到"执行者"的跨越
**内容概要**：阐述小艺Claw的产品定义。
**研究查询**：
- 小艺Claw产品定位与OpenClaw关系
- 小艺Claw与传统AI助手差异对比
**数据需求**：产品定位关键词、核心特性列表（4项）、与传统助手对比维度

### P3:
**类型**：data
**研究需求**：✅
**标题**：小艺Claw集成2100+系统能力
**内容概要**：能力边界数据。
**研究查询**：
- 小艺Claw系统能力Skills数量
- 生态Skills与鸿蒙智能体规模
**数据需求**：系统能力数量（2100+）、系统级数据数量（200+）、生态Skills数量（500+）

### P4:
**类型**：ending
**研究需求**：❌
**标题**：总结
**内容概要**：结束页
**研究查询**：-
**数据需求**：-
"""


# ──────────────────────────────────────────────
# _parse_outline_pages_fallback 测试
# ──────────────────────────────────────────────


class TestParseOutlinePagesFallback:
    """测试正则回退解析大纲。"""

    @pytest.mark.unit
    def test_skips_non_research_pages(self):
        """❌ 页面应被跳过，只返回 ✅ 页面。"""
        # P1(❌), P2(✅), P3(✅), P4(❌) → 只返回 P2, P3
        node = dr.PrepareNode.__new__(dr.PrepareNode)
        pages = node._parse_outline_pages_fallback(_OUTLINE_WITH_BOLD_MARKERS)
        page_nums = [p["page_number"] for p in pages]
        assert page_nums == [2, 3]

    @pytest.mark.unit
    def test_extracts_title_with_quotes(self):
        """标题中含双引号时应正确提取。"""
        node = dr.PrepareNode.__new__(dr.PrepareNode)
        pages = node._parse_outline_pages_fallback(_OUTLINE_WITH_BOLD_MARKERS)
        p2 = next(p for p in pages if p["page_number"] == 2)
        assert "鸿蒙系统级AI智能体" in p2["title"]
        assert "对话助手" in p2["title"]
        assert "执行者" in p2["title"]

    @pytest.mark.unit
    def test_extracts_page_type(self):
        """page_type 应从 **类型**：xxx 提取。"""
        node = dr.PrepareNode.__new__(dr.PrepareNode)
        pages = node._parse_outline_pages_fallback(_OUTLINE_WITH_BOLD_MARKERS)
        p2 = next(p for p in pages if p["page_number"] == 2)
        assert p2["page_type"] == "chapter"
        p3 = next(p for p in pages if p["page_number"] == 3)
        assert p3["page_type"] == "data"

    @pytest.mark.unit
    def test_extracts_research_queries_multi_line(self):
        """研究查询为多行 - 列表时，应提取为字符串列表。"""
        node = dr.PrepareNode.__new__(dr.PrepareNode)
        pages = node._parse_outline_pages_fallback(_OUTLINE_WITH_BOLD_MARKERS)
        p2 = next(p for p in pages if p["page_number"] == 2)
        assert p2["research_queries"] == [
            "小艺Claw产品定位与OpenClaw关系",
            "小艺Claw与传统AI助手差异对比",
        ]

    @pytest.mark.unit
    def test_extracts_data_needs_split(self):
        """数据需求为单行逗号分隔时，应拆分为列表。"""
        node = dr.PrepareNode.__new__(dr.PrepareNode)
        pages = node._parse_outline_pages_fallback(_OUTLINE_WITH_BOLD_MARKERS)
        p2 = next(p for p in pages if p["page_number"] == 2)
        assert p2["data_needs"] == [
            "产品定位关键词",
            "核心特性列表（4项）",
            "与传统助手对比维度",
        ]

    @pytest.mark.unit
    def test_data_needs_with_mixed_separators(self):
        """数据需求混用中文、英文逗号时也能拆分。"""
        outline = """# 大纲：测试

## 页面规划

### P1:
**类型**：data
**研究需求**：✅
**标题**：测试页
**内容概要**：测试
**研究查询**：
- query1
**数据需求**：指标A、指标B,指标C，指标D
"""
        node = dr.PrepareNode.__new__(dr.PrepareNode)
        pages = node._parse_outline_pages_fallback(outline)
        assert pages[0]["data_needs"] == ["指标A", "指标B", "指标C", "指标D"]


# ──────────────────────────────────────────────
# _extract_multi_line_list 测试
# ──────────────────────────────────────────────


class TestExtractMultiLineList:
    """测试多行列表提取方法。"""

    @pytest.mark.unit
    def test_multi_line_dash_list(self):
        """标准多行 - 列表。"""
        section = (
            "**研究查询**：\n"
            "- query1\n"
            "- query2\n"
            "- query3\n"
            "**数据需求**：need1、need2"
        )
        result = dr.PrepareNode._extract_multi_line_list(section, "研究查询")
        assert result == ["query1", "query2", "query3"]

    @pytest.mark.unit
    def test_single_line_value_fallback(self):
        """无 - 列表时，单行值按分隔符拆分。"""
        section = "**研究查询**：query1、query2、query3\n**数据需求**：need1"
        result = dr.PrepareNode._extract_multi_line_list(section, "研究查询")
        assert result == ["query1", "query2", "query3"]

    @pytest.mark.unit
    def test_field_not_found(self):
        """字段不存在时返回空列表。"""
        section = "**类型**：data\n**标题**：测试"
        result = dr.PrepareNode._extract_multi_line_list(section, "研究查询")
        assert result == []

    @pytest.mark.unit
    def test_placeholder_dash_skipped(self):
        """占位符 - 不应被提取。"""
        section = "**研究查询**：-\n**数据需求**：need1"
        result = dr.PrepareNode._extract_multi_line_list(section, "研究查询")
        assert result == []

    @pytest.mark.unit
    def test_stops_at_next_field(self):
        """多行列表应在下一个 ** 字段处停止。"""
        section = (
            "**研究查询**：\n"
            "- real_query_1\n"
            "- real_query_2\n"
            "**内容概要**：这些不应被当作 query\n"
            "- not_a_query"
        )
        result = dr.PrepareNode._extract_multi_line_list(section, "研究查询")
        assert result == ["real_query_1", "real_query_2"]


# ──────────────────────────────────────────────
# extract_llm_json 报错增强测试
# ──────────────────────────────────────────────


class TestExtractLlmJsonErrorEnhanced:
    """测试 extract_llm_json 报错信息包含具体原因。"""

    @pytest.mark.unit
    def test_valid_json_returns_result(self):
        """合法 JSON 应正常返回。"""
        result = ju.extract_llm_json('{"key": "value"}', expected_type=dict)
        assert result == {"key": "value"}

    @pytest.mark.unit
    def test_unescaped_quotes_error_contains_detail(self):
        """未转义引号时，报错应包含具体错误原因和位置。"""
        # 模拟 GLM-5 输出的含未转义引号的 JSON
        bad_json = '{"title": "从"对话助手"到"执行者"的跨越"}'
        with pytest.raises(ValueError) as exc_info:
            ju.extract_llm_json(bad_json, expected_type=dict)
        err_msg = str(exc_info.value)
        # 报错应包含 JSONDecodeError 的 msg（如 "Expecting" 等）
        assert "Expecting" in err_msg or "Invalid" in err_msg
        # 报错应包含行号或列号
        assert "行" in err_msg or "列" in err_msg
        # 报错应包含出错位置附近的上下文
        assert "出错位置附近" in err_msg

    @pytest.mark.unit
    def test_error_contains_context_around_position(self):
        """报错上下文应包含出错位置附近的原始文本。"""
        bad_json = '[{"page_number": 2, "title": "从"对话助手"到"执行者""}]'
        with pytest.raises(ValueError) as exc_info:
            ju.extract_llm_json(bad_json, expected_type=list)
        err_msg = str(exc_info.value)
        # 上下文中应能看到出错的文本片段
        assert "对话助手" in err_msg

    @pytest.mark.unit
    def test_valid_json_array_returns_list(self):
        """合法 JSON 数组应正常返回列表。"""
        result = ju.extract_llm_json('[1, 2, 3]', expected_type=list)
        assert result == [1, 2, 3]

    @pytest.mark.unit
    def test_code_block_extraction_still_works(self):
        """```json 代码块包裹的 JSON 仍能提取。"""
        raw = '```json\n{"key": "value"}\n```'
        result = ju.extract_llm_json(raw, expected_type=dict)
        assert result == {"key": "value"}

    @pytest.mark.unit
    def test_type_mismatch_error(self):
        """解析成功但类型不匹配时仍应抛 ValueError。"""
        with pytest.raises(ValueError) as exc_info:
            ju.extract_llm_json('{"key": "value"}', expected_type=list)
        # 类型不匹配时，first_error 为 None，走默认报错
        assert "list" in str(exc_info.value)
