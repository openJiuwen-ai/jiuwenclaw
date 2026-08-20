# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for SkillEvolutionRail hot-reload lifecycle in _get_current_agent_rails.

Covers the three reload states introduced for the evolution on/off toggle:
  * retire  — evolution.enabled true->false: the OLD object is staged for explicit
              unregister after configure succeeds, then its cached reference is
              cleared.
  * create  — evolution.enabled false->true: a fresh rail is built and the NEW object
              passed to rails_list so core loads it; the cached reference is updated.
  * retain  — evolution.enabled unchanged (on): the rail is updated in-place and NOT
              passed to rails_list (core leaves the registered instance untouched).

The non-evolution rails are stubbed out so the assertions focus on evolution only.
"""

# pylint: disable=protected-access

from typing import Any
from unittest.mock import MagicMock

import pytest

from jiuwenclaw.agentserver.deep_agent.interface_deep import JiuWenClawDeepAdapter
from jiuwenclaw.agentserver.deep_agent.rails.skill_credential_injection_rail import (
    SkillCredentialInjectionRail,
)


class _EvolutionRailReloadHarness(JiuWenClawDeepAdapter):
    """Minimal adapter exposing ``_get_current_agent_rails`` for evolution tests.

    Non-evolution rail builders are stubbed so they never enter ``rails_list``;
    this isolates the evolution reload logic under test.
    """

    _RAIL_ATTRS = (
        "_skill_rail",
        "_context_engineering_rail",
        "_memory_rail",
        "_lsp_rail",
        "_avatar_rail",
        "_permission_rail",
        "_progressive_tool_rail",
        "_disabled_tools_rail",
        "_skill_credential_injection_rail",
        "_pip_isolation_rail",
    )

    @classmethod
    def for_test(cls) -> "_EvolutionRailReloadHarness":
        """Build a harness without invoking the heavy base ``__init__``.

        Uses ``cls.__new__`` (mirroring ``_DeepAdapterReloadHarness`` in
        ``test_reload_agent_config.py``) so the parent initializer is skipped on
        purpose; only the attributes exercised by ``_get_current_agent_rails``
        are set up.
        """
        adapter = cls.__new__(cls)
        adapter._skill_evolution_rail = None
        for attr in cls._RAIL_ATTRS:
            setattr(adapter, attr, None)
        adapter._model = MagicMock()
        # Required by _resolve_evolution_model_name / _resolve_model_name on retain path.
        adapter._model_request_config = None
        adapter._default_model_name = None
        adapter._last_sync_env = {}
        adapter._config_cache = {}
        adapter._context_engineering_rail_mode = None
        adapter._context_engine_config_fp = None
        adapter._last_runtime_mode = "agent.plan"

        # Stub every other rail builder so they never contribute to rails_list.
        # _build_skill_evolution_rail is stubbed here too so retire/retain tests
        # (which do NOT monkeypatch it) can assert ``assert_not_called()`` on a
        # MagicMock rather than the real bound method; create tests override it.
        for builder in (
            "_build_skill_rail",
            "_build_progressive_tool_rail",
            "_build_disabled_tools_rail",
            "_build_skill_credential_injection_rail",
            "_build_skill_evolution_rail",
        ):
            setattr(adapter, builder, MagicMock(return_value=None))

        setattr(adapter, "_filesystem_rail_enabled_for_profile", MagicMock(return_value=True))
        setattr(adapter, "_update_permission_rail", MagicMock())
        setattr(adapter, "_skill_include_harness_fs_tools", MagicMock(return_value=False))
        setattr(adapter, "_skill_include_skill_body_tools", MagicMock(return_value=False))
        setattr(adapter, "_context_engine_config_fingerprint", MagicMock(return_value="fp"))
        return adapter

    async def get_current_agent_rails(
            self, config: dict[str, Any], config_base: dict[str, Any] | None = None
    ) -> list[Any]:
        """Expose the protected rail assembly entrypoint."""
        rails, _rails_to_unregister = await self._get_current_agent_rails(
            config, config_base
        )
        return rails


@pytest.fixture
def adapter() -> _EvolutionRailReloadHarness:
    return _EvolutionRailReloadHarness.for_test()


def _evolution_config(*, enabled: bool) -> dict[str, Any]:
    """Build a react-scoped config carrying only the evolution toggle."""
    return {
        "evolution": {"enabled": enabled},
        "model_name": "gpt-4",
    }


def _build_mock_that_caches(adapter: "_EvolutionRailReloadHarness", rail) -> MagicMock:
    """Mock _build_skill_evolution_rail to return ``rail`` AND cache it on the adapter.

    The real builder caches the freshly-built rail on ``self._skill_evolution_rail``
    (see ``_build_skill_evolution_rail``). The create branch relies on that side
    effect, so the mock must reproduce it.
    """

    def _build(_config):
        adapter._skill_evolution_rail = rail
        return rail

    return MagicMock(side_effect=_build)


@pytest.mark.asyncio
async def test_reload_creates_evolution_rail_when_newly_enabled(adapter, monkeypatch):
    """enabled false->true: a fresh rail is built, cached, and appended for loading."""
    built = MagicMock(name="new-evolution-rail")
    monkeypatch.setattr(adapter, "_build_skill_evolution_rail", _build_mock_that_caches(adapter, built))

    rails = await adapter.get_current_agent_rails(_evolution_config(enabled=True))

    assert built in rails
    # The freshly built rail is now the cached reference for subsequent reloads.
    assert adapter._skill_evolution_rail is built
    adapter._build_skill_evolution_rail.assert_called_once()


@pytest.mark.asyncio
async def test_reload_create_skipped_when_build_returns_none(adapter, monkeypatch):
    """enabled false->true but build fails: nothing is appended and no rail cached."""
    monkeypatch.setattr(adapter, "_build_skill_evolution_rail", MagicMock(return_value=None))

    rails = await adapter.get_current_agent_rails(_evolution_config(enabled=True))

    # No evolution object is staged for core to load.
    assert rails == []
    assert adapter._skill_evolution_rail is None


@pytest.mark.asyncio
async def test_reload_retires_evolution_rail_when_disabled(adapter, monkeypatch):
    """enabled true->false: stage the live rail for explicit unregister."""
    live = MagicMock(name="live-evolution-rail")
    adapter._skill_evolution_rail = live

    rails, rails_to_unregister = await adapter._get_current_agent_rails(
        _evolution_config(enabled=False)
    )

    assert live not in rails
    assert rails_to_unregister == [live]
    # Keep the cached reference until configure and unregister both succeed.
    assert adapter._skill_evolution_rail is live
    # No fresh build is attempted when retiring.
    adapter._build_skill_evolution_rail.assert_not_called()


@pytest.mark.asyncio
async def test_reload_retains_evolution_rail_when_unchanged_enabled(adapter, monkeypatch):
    """enabled unchanged (on): in-place update only; rail is NOT passed to rails_list."""
    live = MagicMock(name="retained-evolution-rail")
    adapter._skill_evolution_rail = live

    rails = await adapter.get_current_agent_rails(_evolution_config(enabled=True))

    # Retain path: the registered instance must be left untouched by core.
    assert live not in rails
    # In-place LLM update applied with the adapter's model + configured model name.
    live.update_llm.assert_called_once_with(adapter._model, "gpt-4")
    # Cached reference unchanged.
    assert adapter._skill_evolution_rail is live
    adapter._build_skill_evolution_rail.assert_not_called()


@pytest.mark.asyncio
async def test_reload_does_nothing_when_disabled_and_no_live_rail(adapter, monkeypatch):
    """enabled already off with no live rail: nothing is built, nothing appended."""
    monkeypatch.setattr(adapter, "_build_skill_evolution_rail", MagicMock(return_value=MagicMock()))

    rails = await adapter.get_current_agent_rails(_evolution_config(enabled=False))

    assert adapter._build_skill_evolution_rail.call_count == 0
    assert all(r is not adapter._skill_evolution_rail for r in rails)


@pytest.mark.asyncio
async def test_reload_appends_evolution_rail_only_once(adapter, monkeypatch):
    """Whichever evolution action fires, at most one evolution object enters rails_list."""
    cached = MagicMock(name="evolution-rail")
    monkeypatch.setattr(adapter, "_build_skill_evolution_rail", _build_mock_that_caches(adapter, cached))

    # create path -> exactly one entry.
    rails = await adapter.get_current_agent_rails(_evolution_config(enabled=True))
    assert sum(1 for r in rails if r is adapter._skill_evolution_rail) == 1

    # retire path -> old object is staged outside rails_list for explicit unregister.
    stale = adapter._skill_evolution_rail
    rails, rails_to_unregister = await adapter._get_current_agent_rails(
        _evolution_config(enabled=False)
    )
    assert stale not in rails
    assert rails_to_unregister == [stale]
    assert adapter._skill_evolution_rail is stale


@pytest.mark.asyncio
async def test_reload_create_passes_exactly_the_built_object(adapter, monkeypatch):
    """'create' hands core exactly the object returned by _build_skill_evolution_rail.

    No wrapping / cloning: the appended entry and the cached reference are the same
    object that the builder produced, so core can unregister any stale twin then load it.
    """
    built = MagicMock(name="built-by-builder")
    monkeypatch.setattr(adapter, "_build_skill_evolution_rail", _build_mock_that_caches(adapter, built))
    # create branch requires the cached reference to be None going in.
    assert adapter._skill_evolution_rail is None

    rails = await adapter.get_current_agent_rails(_evolution_config(enabled=True))

    appended = [r for r in rails if r is built]
    assert appended == [built]
    assert adapter._skill_evolution_rail is built
    adapter._build_skill_evolution_rail.assert_called_once()


@pytest.mark.asyncio
async def test_reload_keeps_skill_envs_when_yaml_overlay_is_empty(adapter):
    """Live rail credentials survive a config.yaml ``skill_envs: {}`` hot-reload."""
    live = SkillCredentialInjectionRail(
        skill_envs={"hwocr": {"HWOCR_AK": "ak", "HWOCR_PROJECT_ID": "p1"}}
    )
    adapter._skill_credential_injection_rail = live

    await adapter.get_current_agent_rails(
        {"skill_envs": {}, "evolution": {"enabled": False}},
        {"react": {"skill_envs": {}}},
    )

    assert live.get_skill_envs()["hwocr"]["HWOCR_AK"] == "ak"


@pytest.mark.asyncio
async def test_reload_applies_catalog_clear_for_skill_envs(adapter):
    live = SkillCredentialInjectionRail(skill_envs={"hwocr": {"HWOCR_AK": "ak"}})
    adapter._skill_credential_injection_rail = live
    cleared = {"hwocr": {"HWOCR_AK": ""}}

    await adapter.get_current_agent_rails(
        {"skill_envs": cleared, "evolution": {"enabled": False}},
        {"react": {"skill_envs": cleared}},
    )

    assert live.get_skill_envs()["hwocr"]["HWOCR_AK"] == ""
