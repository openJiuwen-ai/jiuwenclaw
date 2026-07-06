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
def test_parse_json_payload_supports_markdown_fence() -> None:
    raw = '```json\n{"topic":"测试主题","page_count":6}\n```'
    payload = PptCommon.parse_json_payload(raw)
    assert isinstance(payload, dict)
    assert payload["topic"] == "测试主题"
    assert payload["page_count"] == 6
