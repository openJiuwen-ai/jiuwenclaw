# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team members must declare plan-parity catalog tool specs."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from openjiuwen.core.runner.runner import Runner

from jiuwenclaw.agentserver.swarm.config_specs import (
    _build_catalog_tool_specs,
    build_member_capability_specs,
)
from jiuwenclaw.agentserver.swarm.providers import catalog_tools
from jiuwenclaw.agentserver.swarm.providers.catalog_tools import _build_jiuwen_web_search
from jiuwenclaw.agentserver.swarm.registry import (
    CORE_AUDIO,
    CORE_VISION,
    CORE_WEB_FETCH,
    JIUWEN_WEB_FETCH,
    JIUWEN_WEB_SEARCH,
    PLATFORM_CATALOG_TOOLS,
    PLATFORM_MEMBER_RAILS,
)


def test_web_search_reuses_registered_tool_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = SimpleNamespace(member_card_id="office", language="cn")
    registered = _build_jiuwen_web_search({}, context)[0]
    monkeypatch.setattr(
        Runner.resource_mgr,
        "get_tool",
        lambda tool_id: registered if tool_id == registered.card.id else None,
    )

    rebuilt = _build_jiuwen_web_search({}, context)[0]

    assert rebuilt is registered


def test_web_fetch_reuses_registered_tool_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builder = getattr(catalog_tools, "_build_jiuwen_web_fetch", None)
    assert builder is not None
    context = SimpleNamespace(member_card_id="office", language="cn")
    registered = builder({}, context)[0]
    monkeypatch.setattr(
        Runner.resource_mgr,
        "get_tool",
        lambda tool_id: registered if tool_id == registered.card.id else None,
    )

    rebuilt = builder({}, context)[0]

    assert rebuilt is registered


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
    assert JIUWEN_WEB_SEARCH in types
    assert JIUWEN_WEB_FETCH in types
    assert CORE_WEB_FETCH not in types
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
    assert any(t.type == JIUWEN_WEB_SEARCH for t in tools)
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
    # JIUWEN_WEB_SEARCH + JIUWEN_WEB_FETCH + PLATFORM_CATALOG_TOOLS = 3
    # (CORE_WEB_PAID_SEARCH removed; paid chain is inside JIUWEN_WEB_SEARCH)
    assert len(tools) >= 3
