# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team members must declare plan-parity catalog tool specs."""

from __future__ import annotations

import pytest

from jiuwenclaw.agentserver.swarm.config_specs import (
    _build_catalog_tool_specs,
    build_member_capability_specs,
)
from jiuwenclaw.agentserver.swarm.registry import (
    CORE_AUDIO,
    CORE_VISION,
    CORE_WEB_FETCH,
    CORE_WEB_PAID_SEARCH,
    CORE_WEB_SEARCH,
    PLATFORM_CATALOG_TOOLS,
    PLATFORM_MEMBER_RAILS,
)


def test_catalog_tool_specs_always_include_web_and_platform_extras(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.swarm.providers.catalog_tools.vision_tool_params",
        lambda config: {"vision_model_config": {}},
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.swarm.providers.catalog_tools.audio_tool_params",
        lambda config: {"dedicated": False, "audio_model_config": {}},
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.swarm.providers.catalog_tools.platform_catalog_tool_params",
        lambda config: {
            "enable_video": False,
            "enable_image_gen": False,
            "enable_deepresearch": True,
        },
    )

    specs = _build_catalog_tool_specs({})
    types = [s.type for s in specs]
    assert CORE_WEB_SEARCH in types
    assert CORE_WEB_FETCH in types
    assert CORE_WEB_PAID_SEARCH in types
    assert PLATFORM_CATALOG_TOOLS in types
    assert CORE_VISION not in types
    assert CORE_AUDIO not in types


def test_catalog_tool_specs_include_vision_audio_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.swarm.providers.catalog_tools.vision_tool_params",
        lambda config: {"vision_model_config": {"api_key": "k", "base_url": "u", "model": "m"}},
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.swarm.providers.catalog_tools.audio_tool_params",
        lambda config: {"dedicated": True, "audio_model_config": {"api_key": "k", "base_url": "u"}},
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.swarm.providers.catalog_tools.platform_catalog_tool_params",
        lambda config: {"enable_video": True, "enable_image_gen": True, "enable_deepresearch": True},
    )

    types = [s.type for s in _build_catalog_tool_specs({})]
    assert CORE_VISION in types
    assert CORE_AUDIO in types


def test_build_member_capability_specs_not_empty_for_team_plan() -> None:
    rails, tools = build_member_capability_specs(
        {},
        "team.plan",
        "leader",
        enable_permissions=True,
        leader_member_name="office",
    )
    assert any(r.type == PLATFORM_MEMBER_RAILS for r in rails)
    assert any(t.type == CORE_WEB_SEARCH for t in tools)
    assert any(t.type == PLATFORM_CATALOG_TOOLS for t in tools)


def test_build_member_capability_specs_for_team_mode() -> None:
    rails, tools = build_member_capability_specs(
        {},
        "team",
        "teammate",
        enable_permissions=False,
    )
    assert len(rails) == 1
    assert rails[0].params.get("enable_permissions") is False
    assert len(tools) >= 4
