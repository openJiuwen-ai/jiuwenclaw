# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""PptCommon 单元测试。"""

from __future__ import annotations

import pytest

from jiuwenclaw.agentserver.skill_turbo.skill_codes.ppt.ppt_common import PptCommon


@pytest.mark.unit
def test_collect_user_text_extracts_officeclaw_query() -> None:
    wrapped = (
        '你收到一条消息：\n'
        '{"source": "officeclaw", "content": "华为风格政府工作报告PPT", "type": "user input"}'
    )
    assert PptCommon.collect_user_text({"query": wrapped}) == "华为风格政府工作报告PPT"


@pytest.mark.unit
def test_resolve_total_pages_prefers_outline_max_over_page_count_plus_two() -> None:
    outline_text = (
        "# 大纲：测试\n\n## 页面规划\n"
        "### P1:\n- **类型**：cover\n"
        "### P2:\n- **类型**：agenda\n"
        "### P13:\n- **类型**：ending\n"
    )
    total = PptCommon.resolve_total_pages(
        page_count=10,
        total_pages=None,
        outline_text=outline_text,
        default_structural_pages=2,
    )
    assert total == 13


@pytest.mark.unit
def test_parse_json_payload_supports_markdown_fence() -> None:
    raw = '```json\n{"topic":"测试主题","page_count":6}\n```'
    payload = PptCommon.parse_json_payload(raw)
    assert isinstance(payload, dict)
    assert payload["topic"] == "测试主题"
    assert payload["page_count"] == 6


@pytest.mark.unit
def test_normalize_structural_page_request_maps_auto_and_combos() -> None:
    assert PptCommon.normalize_structural_page_request("auto") == "section"
    assert PptCommon.normalize_structural_page_request("agenda") == "agenda"
    assert PptCommon.normalize_structural_page_request("agenda+section") == "agenda+section"
    assert PptCommon.normalize_structural_page_request("AGENDA, chapter") == "agenda+chapter"
    assert PptCommon.normalize_structural_page_request("") == "none"
    assert PptCommon.normalize_structural_page_request(None) == "none"


@pytest.mark.unit
def test_resolve_structural_page_plan_agenda_only_is_one_toc() -> None:
    plan = PptCommon.resolve_structural_page_plan("agenda", None, page_count=6)
    assert plan.need_agenda is True
    assert plan.agenda_count == 1
    assert plan.divider_count == 0
    assert plan.total_middle == 1


@pytest.mark.unit
def test_resolve_structural_page_plan_section_default_uses_ceil() -> None:
    plan = PptCommon.resolve_structural_page_plan("section", None, page_count=10)
    assert plan.need_agenda is False
    assert plan.divider_type == "section"
    assert plan.divider_count == 3
    assert plan.divider_count_mode == "default"


@pytest.mark.unit
def test_resolve_structural_page_plan_agenda_plus_section() -> None:
    plan = PptCommon.resolve_structural_page_plan("agenda+section", None, page_count=10)
    assert plan.agenda_count == 1
    assert plan.divider_type == "section"
    assert plan.divider_count == 3
    assert plan.total_middle == 4
