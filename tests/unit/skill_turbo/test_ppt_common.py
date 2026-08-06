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
