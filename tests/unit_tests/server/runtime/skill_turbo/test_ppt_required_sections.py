# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json

from jiuwenswarm.server.runtime.skill_turbo.skill_codes.ppt.intent_classify import (
    _parse_slots_from_llm_response,
)
from jiuwenswarm.server.runtime.skill_turbo.skill_codes.ppt.ppt_common import (
    PptCommon,
)


def _required_sections() -> list[dict[str, str]]:
    return [
        {"title": "标题页", "page_type": "cover"},
        {"title": "目录", "page_type": "agenda"},
        {"title": "市场概况", "page_type": "content"},
        {"title": "竞争格局", "page_type": "content"},
        {"title": "技术趋势", "page_type": "content"},
        {"title": "展望结论", "page_type": "ending"},
    ]


def test_required_sections_override_conflicting_total_page_count() -> None:
    inputs = {
        "page_count": 1,
        "requested_total_pages": 3,
        "structural_page_request": "none",
        "required_sections": _required_sections(),
    }

    PptCommon.resolve_required_section_budget(inputs)

    assert inputs["page_count"] == 3
    assert inputs["structural_page_request"] == "agenda"
    assert inputs["structural_page_count"] == 1
    assert inputs["resolved_total_pages"] == 6
    assert inputs["required_agenda_item_count"] == 4
    assert inputs["page_count_resolution"] == "required_sections_override"


def test_required_sections_do_not_shrink_larger_page_budget() -> None:
    inputs = {
        "page_count": 7,
        "requested_total_pages": 10,
        "structural_page_request": "none",
        "required_sections": _required_sections(),
    }

    PptCommon.resolve_required_section_budget(inputs)

    assert inputs["page_count"] == 7
    assert inputs["resolved_total_pages"] == 10
    assert inputs["page_count_resolution"] == "required_sections_fit"


def test_intent_parser_preserves_structured_required_sections() -> None:
    response = json.dumps({
        "doc_paths": [],
        "slots": {
            "topic": "2025年新能源汽车市场趋势",
            "page_count": 1,
            "audience": "普通受众",
            "presentation_purpose": "教学分享",
            "style_id": "custom",
            "pack_dir": "",
            "requested_total_pages": 3,
            "required_sections": _required_sections(),
        },
    }, ensure_ascii=False)

    slots = _parse_slots_from_llm_response(response)

    assert slots["requested_total_pages"] == 3
    assert slots["required_sections"] == _required_sections()
