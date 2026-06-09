# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for the swarm provider-based team assembly framework.

These tests exercise the config-sourced assembly path end to end without ever
touching a real LLM, the network, or a live ``DeepAgent``:

* provider/rail-type registration is idempotent and lands in openjiuwen's
  registries,
* ``build_member_capability_specs`` emits the expected ``swarm.*`` rail/tool
  references per role (purely from a config dict),
* ``enrich_team_spec_for_swarm`` rewrites a real ``TeamAgentSpec`` in place,
  attaches a ``SwarmBuildContext``, and keeps the parent-free contract,
* the enriched spec serializes cleanly with the live build context excluded,
* individual providers degrade gracefully (return ``[]`` / ``None``) when their
  config gate is closed.
"""

from __future__ import annotations

import inspect
import json
import logging
import types
from pathlib import Path

import pytest

from openjiuwen.agent_teams.schema import deep_agent_spec as das
from openjiuwen.agent_teams.schema.blueprint import LeaderSpec, TeamAgentSpec
from openjiuwen.agent_teams.schema.deep_agent_spec import (
    BuiltinToolSpec,
    DeepAgentSpec,
    RailSpec,
)
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs
from openjiuwen.harness.prompts.builder import SystemPromptBuilder

from jiuwenswarm.agents.swarm import (
    SwarmBuildContext,
    enrich_team_spec_for_swarm,
    register_swarm_providers,
)
from openjiuwen.agent_teams.schema.deep_agent_spec import TeamModelConfig, WorkspaceSpec
from openjiuwen.agent_evolving.trajectory import InMemoryTrajectoryRegistry
from openjiuwen.core.foundation.llm import ModelClientConfig

from jiuwenswarm.agents.swarm import registry
from jiuwenswarm.agents.swarm.config_specs import (
    build_member_capability_specs,
    build_member_deep_agent_spec,
    build_member_subagent_specs,
)
from jiuwenswarm.agents.swarm.providers import (
    code_rails,
    evolution_rails,
    member_rails,
    runtime_tools,
    tools,
)
from jiuwenswarm.agents.harness.team.team_runtime_inheritance import (
    resolve_model_config,
)
from jiuwenswarm.common.config import get_config

logger = logging.getLogger(__name__)

# Rail provider names shared by both roles (no role-specific evolution rails).
# Sourced from the registry symbols so the test tracks renames automatically.
_COMMON_RAIL_NAMES: frozenset[str] = frozenset(
    {
        registry.RUNTIME_PROMPT,
        registry.TEAM_SKILL_STORAGE_POLICY,
        registry.TEAM_SHARED_SKILL_LINK_REFRESH,
        registry.RESPONSE_PROMPT,
        registry.SYS_OPERATION,
        registry.STREAM_EVENT,
        registry.TASK_PLANNING,
        registry.SECURITY,
        registry.HEARTBEAT,
        registry.AVATAR_PROMPT,
        registry.TEAM_WORKSPACE_REPORT_PATH,
        registry.CONTEXT_PROCESSOR,
        registry.PLUGIN_RAILS,
        registry.MEMBER_SKILL_TOOLKIT,
    }
)

_COMMON_TOOL_NAMES: frozenset[str] = frozenset(
    {
        registry.WEB_SEARCH,
        registry.WEB_FETCH,
        registry.WEB_PAID_SEARCH,
        registry.VISION,
        registry.AUDIO,
        # SKILL_TOOLKIT is no longer declared as a tool; the
        # MEMBER_SKILL_TOOLKIT rail is the sole registrar of skill tools.
        registry.USER_TODOS,
        registry.VIDEO,
        registry.IMAGE_GEN,
        registry.XIAOYI_PHONE,
        registry.CRON_TOOLS,
        registry.SEND_FILE,
    }
)


def _make_team_spec() -> TeamAgentSpec:
    """Build a minimal, valid two-role team spec for enrichment tests.

    Uses bare ``DeepAgentSpec`` members (no model / card) so the spec stays
    free of any live runtime dependency; enrichment only reads/writes rails,
    tools and ``build_context``.

    Returns:
        A ``TeamAgentSpec`` with ``leader`` and ``teammate`` members.
    """
    return TeamAgentSpec(
        agents={"leader": DeepAgentSpec(), "teammate": DeepAgentSpec()},
        team_name="unit_team",
        leader=LeaderSpec(member_name="team_leader"),
    )


def test_register_swarm_providers_is_idempotent() -> None:
    """Two consecutive registrations must not raise and stay consistent."""
    register_swarm_providers()
    rail_after_first = {
        k for k in das._RAIL_PROVIDER_REGISTRY if k.startswith("swarm.")
    }
    tool_after_first = {
        k for k in das._TOOL_PROVIDER_REGISTRY if k.startswith("swarm.")
    }

    register_swarm_providers()
    rail_after_second = {
        k for k in das._RAIL_PROVIDER_REGISTRY if k.startswith("swarm.")
    }
    tool_after_second = {
        k for k in das._TOOL_PROVIDER_REGISTRY if k.startswith("swarm.")
    }

    assert rail_after_first == rail_after_second
    assert tool_after_first == tool_after_second
    logger.info(
        "idempotent registration: %d rail providers, %d tool providers",
        len(rail_after_second),
        len(tool_after_second),
    )


def test_register_swarm_providers_populates_registries() -> None:
    """Registration installs every common ``swarm.*`` provider name."""
    register_swarm_providers()

    rail_providers = set(das._RAIL_PROVIDER_REGISTRY)
    tool_providers = set(das._TOOL_PROVIDER_REGISTRY)

    # The legacy class-type registry is gone; every rail (including the unified
    # class rails) is now provider-backed.
    for name in _COMMON_RAIL_NAMES:
        assert name in rail_providers, name
    for name in _COMMON_TOOL_NAMES:
        assert name in tool_providers, name

    # Role-specific evolution rails are provider-backed.
    assert registry.TEAM_SKILL_EVOLUTION in rail_providers
    assert registry.TEAM_SKILL_CREATE in rail_providers
    assert registry.MEMBER_SKILL_EVOLUTION in rail_providers


def test_runtime_prompt_rail_resolves_via_registry() -> None:
    """A ``RailSpec`` referencing a swarm provider builds a live rail.

    Proves the registration is wired through openjiuwen's resolution path (not
    just present as a dict key) using a fake per-member context.
    """
    register_swarm_providers()
    fake_ctx = SwarmBuildContext(language="cn", channel="web")

    rail = RailSpec(type=registry.RUNTIME_PROMPT).build(language="cn", context=fake_ctx)

    assert rail is not None
    assert type(rail).__name__ == "RuntimePromptRail"


@pytest.mark.asyncio
async def test_team_skill_storage_policy_rail_resolves_and_injects_paths(tmp_path: Path) -> None:
    """The team skill storage policy should inject concrete team/member paths."""
    register_swarm_providers()
    global_skills_dir = str(tmp_path / "agent" / "workspace" / "skills")
    team_ws_root = str(tmp_path / ".agent_teams" / "unit" / "team-workspace")
    team_skills_dir = str(tmp_path / ".agent_teams" / "unit" / "team-workspace" / "skills")
    member_workspace_root = str(
        tmp_path / ".agent_teams" / "unit" / "workspaces" / "member_workspace"
    )
    fake_ctx = SwarmBuildContext(
        language="cn",
        global_skills_dir=global_skills_dir,
        team_ws_root=team_ws_root,
        team_skills_dir=team_skills_dir,
        workspace=types.SimpleNamespace(root_path=member_workspace_root),
    )

    rail = RailSpec(type=registry.TEAM_SKILL_STORAGE_POLICY).build(
        language="cn",
        context=fake_ctx,
    )
    builder = SystemPromptBuilder(language="cn")
    rail.init(types.SimpleNamespace(system_prompt_builder=builder))

    await rail.before_model_call(AgentCallbackContext(agent=None, inputs=None, session=None))

    content = builder.build()
    assert f"{global_skills_dir}/<skill-name>/SKILL.md" in content
    assert team_ws_root in content
    assert team_skills_dir in content
    assert member_workspace_root in content
    assert "skill-creator" not in content


@pytest.mark.asyncio
async def test_team_shared_skill_link_refresh_rail_resolves_and_refreshes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shared skill link refresh rail should refresh after global skill writes."""
    register_swarm_providers()
    global_skills_dir = tmp_path / "agent" / "workspace" / "skills"
    skill_dir = global_skills_dir / "new-skill"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("---\ndescription: test\n---\n", encoding="utf-8")
    calls: list[tuple[str, str]] = []

    class _FakeTeamManager:
        def __init__(self, channel: str) -> None:
            self._channel = channel

        def refresh_team_shared_skill_links(self, session_id: str) -> bool:
            calls.append((self._channel, session_id))
            return True

    def _get_team_manager(channel: str) -> _FakeTeamManager:
        return _FakeTeamManager(channel)

    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.team.team_manager.get_team_manager",
        _get_team_manager,
    )
    fake_ctx = SwarmBuildContext(
        language="cn",
        session_id="session-1",
        channel="web",
        global_skills_dir=str(global_skills_dir),
    )

    rail = RailSpec(type=registry.TEAM_SHARED_SKILL_LINK_REFRESH).build(
        language="cn",
        context=fake_ctx,
    )

    await rail.after_tool_call(
        AgentCallbackContext(
            agent=None,
            inputs=ToolCallInputs(
                tool_name="write_file",
                tool_args={"file_path": str(skill_file)},
            ),
            session=None,
        )
    )

    assert calls == [("web", "session-1")]


def test_unknown_swarm_rail_type_raises() -> None:
    """An unregistered ``swarm.*`` rail type surfaces a clear ``ValueError``."""
    register_swarm_providers()
    fake_ctx = SwarmBuildContext(language="cn", channel="web")

    with pytest.raises(ValueError):
        RailSpec(type="swarm.__does_not_exist__").build(
            language="cn",
            context=fake_ctx,
        )


@pytest.mark.parametrize(
    ("role", "extra_rails"),
    [
        ("leader", {registry.TEAM_SKILL_EVOLUTION, registry.TEAM_SKILL_CREATE}),
        ("teammate", {registry.MEMBER_SKILL_EVOLUTION}),
    ],
)
def test_build_member_capability_specs_rail_names(
    role: str,
    extra_rails: set[str],
) -> None:
    """Each role gets the common rails plus its role-specific evolution rails."""
    config = {
        "agents": {
            "leader": {"skills": ["alpha"]},
            "teammate": {"skills": ["beta"]},
        }
    }

    rails_specs, _ = build_member_capability_specs(config, "team", role)
    rail_names = {spec.type for spec in rails_specs}

    assert _COMMON_RAIL_NAMES <= rail_names
    assert extra_rails <= rail_names
    # The common set has exactly 14 entries; the role adds only its evolution
    # rails on top.
    assert len(_COMMON_RAIL_NAMES) == 14
    assert rail_names == _COMMON_RAIL_NAMES | extra_rails
    # No DeepAgent is involved; every entry is a plain declarative RailSpec.
    assert all(isinstance(spec, RailSpec) for spec in rails_specs)
    logger.info("%s rails: %s", role, sorted(rail_names))


@pytest.mark.parametrize("role", ["leader", "teammate"])
def test_build_member_capability_specs_tool_names(role: str) -> None:
    """Both roles declare the common tool set (base / cron / send_file)."""
    config = {"agents": {"leader": {"skills": []}, "teammate": {"skills": []}}}

    _, tool_specs = build_member_capability_specs(config, "team", role)
    tool_names = {spec.type for spec in tool_specs}

    assert tool_names == _COMMON_TOOL_NAMES
    assert all(isinstance(spec, BuiltinToolSpec) for spec in tool_specs)


def test_member_skill_toolkit_carries_selected_skills() -> None:
    """The skill-toolkit rail forwards the role's cleaned skill selection."""
    config = {
        "agents": {
            "leader": {"skills": ["alpha", "  ", "beta"]},
            "teammate": {"skills": []},
        }
    }

    leader_rails, _ = build_member_capability_specs(config, "team", "leader")
    toolkit = next(
        spec for spec in leader_rails if spec.type == registry.MEMBER_SKILL_TOOLKIT
    )

    # Blank entries are stripped; order is preserved.
    assert toolkit.params == {"skills": ["alpha", "beta"]}


@pytest.mark.parametrize("role", ["leader", "teammate"])
def test_team_member_deep_agent_spec_enables_skill_discovery(role: str) -> None:
    """Chat-team members rely on the core default SkillUseRail."""
    base = DeepAgentSpec(enable_skill_discovery=False)

    spec = build_member_deep_agent_spec({}, "team", role, base)

    assert spec.enable_skill_discovery is True


@pytest.mark.parametrize("mode", ["code.team", "team.plan"])
def test_code_member_deep_agent_spec_keeps_explicit_skill_use_rail(mode: str) -> None:
    """Code profiles keep their explicit SkillUseRail provider path."""
    base = DeepAgentSpec(enable_skill_discovery=False)

    spec = build_member_deep_agent_spec({}, mode, "leader", base)
    rail_names = {rail.type for rail in (spec.rails or [])}

    assert spec.enable_skill_discovery is False
    assert registry.CODE_SKILL_USE in rail_names


def test_enrich_team_spec_for_swarm_has_no_deep_agent_param() -> None:
    """The enrichment seam must never accept a pre-built DeepAgent."""
    params = set(inspect.signature(enrich_team_spec_for_swarm).parameters)

    assert "deep_agent" not in params
    assert "deep_agent_spec" not in params
    # The exact public surface is the session/request descriptors only.
    assert params == {
        "spec",
        "session_id",
        "mode",
        "project_dir",
        "request_id",
        "channel_id",
        "request_metadata",
    }


def test_enrich_team_spec_for_swarm_rewrites_spec_in_place() -> None:
    """Enrichment rewrites member rails and attaches the build context."""
    spec = _make_team_spec()

    result = enrich_team_spec_for_swarm(
        spec,
        session_id="s",
        mode="team",
        channel_id="web",
    )

    # Mutates in place and returns nothing.
    assert result is None

    leader_rail_names = {rail.type for rail in (spec.agents["leader"].rails or [])}
    teammate_rail_names = {rail.type for rail in (spec.agents["teammate"].rails or [])}
    assert any(name.startswith("swarm.") for name in leader_rail_names)
    assert registry.TEAM_SKILL_EVOLUTION in leader_rail_names
    assert registry.MEMBER_SKILL_EVOLUTION in teammate_rail_names

    # Build context carries the per-team handles.
    assert isinstance(spec.build_context, SwarmBuildContext)
    assert spec.build_context.session_id == "s"
    assert spec.build_context.team_id == "unit_team"

    # The parent-free contract: openjiuwen removed the imperative customizer
    # hook entirely, so the field no longer exists on the spec.
    assert not hasattr(spec, "agent_customizer")


def test_enrich_team_spec_appends_after_existing_rails() -> None:
    """Provider rails are appended after a member's pre-existing rails."""
    spec = _make_team_spec()
    # Seed the leader with a non-swarm rail to prove ordering is preserved.
    spec.agents["leader"].rails = [RailSpec(type="skill_use")]

    enrich_team_spec_for_swarm(spec, session_id="s", mode="team", channel_id="web")

    leader_rail_types = [rail.type for rail in (spec.agents["leader"].rails or [])]
    assert leader_rail_types[0] == "skill_use"
    assert leader_rail_types.count("skill_use") == 1
    assert len(leader_rail_types) > 1


def test_enrich_skips_absent_roles_gracefully() -> None:
    """A team without a teammate role is enriched without error."""
    spec = TeamAgentSpec(
        agents={"leader": DeepAgentSpec()},
        team_name="solo_team",
        leader=LeaderSpec(member_name="team_leader"),
    )

    enrich_team_spec_for_swarm(spec, session_id="s", mode="team", channel_id="web")

    assert "teammate" not in spec.agents
    leader_rail_names = {rail.type for rail in (spec.agents["leader"].rails or [])}
    assert registry.TEAM_SKILL_EVOLUTION in leader_rail_names


def test_enriched_spec_serialization_round_trip() -> None:
    """The enriched spec dumps to JSON with the build context excluded."""
    spec = _make_team_spec()
    enrich_team_spec_for_swarm(spec, session_id="s", mode="team", channel_id="web")

    dumped = spec.model_dump_json()
    data = json.loads(dumped)

    # ``build_context`` (live carrier) is excluded; ``agent_customizer`` was
    # removed from the spec entirely (F_32) and must not reappear.
    assert "build_context" not in data
    assert "agent_customizer" not in data

    # Rails serialize as {type, params} declarative references.
    leader_rails = data["agents"]["leader"]["rails"]
    assert leader_rails, "expected leader rails in the dumped spec"
    for rail in leader_rails:
        assert set(rail.keys()) == {"type", "params"}
        assert isinstance(rail["type"], str) and rail["type"]
    # The member profile mixes swarm-owned (swarm.*) and openjiuwen-provided
    # (bare name) rails after the element unification.
    rail_types = {rail["type"] for rail in leader_rails}
    assert any(name.startswith("swarm.") for name in rail_types)
    assert any(not name.startswith("swarm.") for name in rail_types)


def test_send_file_returns_empty_without_request_id() -> None:
    """The send-file provider skips (returns []) when request_id is missing."""
    ctx = SwarmBuildContext(
        session_id="s",
        request_id=None,
        channel_id="web",
        config={},
    )

    assert runtime_tools.build_send_file_tools({}, ctx) == []


def test_send_file_returns_empty_without_channel_id() -> None:
    """The send-file provider skips (returns []) when channel_id is missing."""
    ctx = SwarmBuildContext(
        session_id="s",
        request_id="r",
        channel_id=None,
        config={},
    )

    assert runtime_tools.build_send_file_tools({}, ctx) == []


def test_send_file_gating_defaults_by_channel() -> None:
    """File sending defaults on for ``web`` and off for other channels."""
    assert runtime_tools._is_send_file_enabled(None, "web")
    assert not runtime_tools._is_send_file_enabled(None, "feishu")
    # Explicit config switch overrides the default.
    disabled = {"channels": {"web": {"send_file_allowed": False}}}
    assert not runtime_tools._is_send_file_enabled(disabled, "web")


def test_cron_tools_built(monkeypatch: pytest.MonkeyPatch) -> None:
    """The cron provider builds the member-scoped toolkit via CronRuntimeBridge."""

    class _FakeCronBridge:
        def build_tools(self, *, context, agent_id, language="cn"):
            return [
                types.SimpleNamespace(
                    card=types.SimpleNamespace(
                        name="cron_list_jobs", id=f"cron_{agent_id}"
                    )
                )
            ]

    monkeypatch.setattr(runtime_tools, "CronRuntimeBridge", _FakeCronBridge)
    ctx = SwarmBuildContext(member_card_id="m1", channel_id="web", session_id="s")

    built = runtime_tools.build_cron_tools({}, ctx)

    assert [tool.card.name for tool in built] == ["cron_list_jobs"]


def test_context_processor_returns_none_when_engine_disabled() -> None:
    """The context-processor provider returns None when the engine is off.

    The enabled flag is a config-derived attribute baked into ``params`` by
    config_specs; the provider gates on it directly.
    """
    ctx = SwarmBuildContext()

    assert (
        member_rails._build_context_processor({"context_engine_enabled": False}, ctx)
        is None
    )


def test_team_workspace_report_path_returns_none_without_root() -> None:
    """The report-path rail is skipped when no team workspace root is set."""
    ctx = SwarmBuildContext(team_ws_root=None)

    assert member_rails._build_team_workspace_report_path_rail({}, ctx) is None


# ---------------------------------------------------------------------------
# Multimodal / xiaoyi tool gating (config-sourced base tools)
# ---------------------------------------------------------------------------


def test_xiaoyi_phone_tools_gated_by_config() -> None:
    """xiaoyi phone tools are built only when the channel switch is on."""
    enabled = SwarmBuildContext(
        config={"channels": {"xiaoyi": {"phone_tools_enabled": True}}},
    )
    disabled = SwarmBuildContext(config={})

    assert len(tools._build_xiaoyi_phone_tools(enabled)) == 27
    assert tools._build_xiaoyi_phone_tools(disabled) == []


def test_video_tool_gated_by_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """The video tool is built only when models.video is configured."""
    ctx = SwarmBuildContext(config={})
    # Gate closed: empty config has no dedicated video model.
    assert tools._build_video_tools(ctx) == []

    # Gate open: dedicated key reported + VIDEO_API_KEY present.
    monkeypatch.setattr(tools, "apply_video_model_config_from_yaml", lambda cfg: None)
    monkeypatch.setattr(
        tools, "dedicated_multimodal_model_configured", lambda cfg, kind: True
    )
    monkeypatch.setenv("VIDEO_API_KEY", "k")
    built = tools._build_video_tools(ctx)
    assert [tool.card.name for tool in built] == ["video_understanding"]


def test_image_gen_tool_gated_by_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The image-generation tool is built only when IMAGE_GEN_API_KEY is set."""
    ctx = SwarmBuildContext(config={})
    monkeypatch.setattr(
        tools, "apply_image_gen_model_config_from_yaml", lambda cfg: None
    )
    monkeypatch.delenv("IMAGE_GEN_API_KEY", raising=False)
    assert tools._build_image_gen_tools(ctx) == []

    monkeypatch.setenv("IMAGE_GEN_API_KEY", "k")
    built = tools._build_image_gen_tools(ctx)
    assert [tool.card.name for tool in built] == ["generate_image"]


def test_vision_model_config_params_gating(monkeypatch: pytest.MonkeyPatch) -> None:
    """Vision config params are empty without a dedicated model, filled when complete."""
    assert tools.vision_model_config_params({}) == {}

    monkeypatch.setattr(
        tools, "dedicated_multimodal_model_configured", lambda cfg, kind: True
    )
    monkeypatch.setattr(tools, "apply_vision_model_config_from_yaml", lambda cfg: None)
    monkeypatch.setenv("VISION_API_KEY", "key")
    monkeypatch.setenv("VISION_BASE_URL", "https://vision.example")
    monkeypatch.setenv("VISION_MODEL", "vlm-1")

    params = tools.vision_model_config_params({})
    assert params["api_key"] == "key"
    assert params["base_url"] == "https://vision.example"
    assert params["model"] == "vlm-1"


def test_vision_audio_config_params_empty_when_unconfigured() -> None:
    """Vision/audio config params are empty when no dedicated model is configured.

    The vision/audio tools themselves are openjiuwen elements (``core.vision`` /
    ``core.audio``); swarm only fills their config, so an unconfigured member
    yields empty params (the core element then builds nothing).
    """
    assert tools.vision_model_config_params({}) == {}
    assert tools.audio_model_config_params({}) == {}
    assert tools.audio_dedicated_configured({}) is False


# ---------------------------------------------------------------------------
# Evolution hot-reload: full TeamWorkspaceInfo on the registered rail context
# ---------------------------------------------------------------------------


def test_team_skill_create_rail_registers_full_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The leader create rail registers a fully-populated ``TeamWorkspaceInfo``.

    An empty workspace info previously broke ``update_evolution_config`` rebuilds
    (``build_member_rails`` skips the leader branch without ``skills_dir``). This
    asserts the rail-mount context now carries root_dir / skills_dir / team_id /
    config / trajectory_registry from the build context.
    """
    register_swarm_providers()
    monkeypatch.setenv("SKILL_CREATE", "true")

    registry_obj = object()
    config = {"react": {"evolution": {"skill_create": True}}}
    ctx = SwarmBuildContext(
        language="cn",
        role="leader",
        member_card_id="t_leader",
        session_id="sess",
        channel="web",
        team_id="t",
        team_ws_root="/tmp/team-x",
        team_skills_dir="/tmp/team-x/skills",
        global_skills_dir="/tmp/global",
        trajectory_registry=registry_obj,
        config=config,
    )

    rail = evolution_rails.build_team_skill_create_rail({"skill_create": True}, ctx)
    assert rail is not None

    captured: dict[str, object] = {}

    class _RecorderTeamManager:
        def register_team_live_rail(self, session_id, agent, registered_rail) -> None:
            captured["live_rail"] = (session_id, registered_rail)

        def register_team_skill_create_rail(self, session_id, registered_rail) -> None:
            captured["create_rail"] = (session_id, registered_rail)

        def get_team_rail_context(self, session_id):
            return None

        def register_team_rail_context(self, session_id, context) -> None:
            captured["rail_context"] = context

    import jiuwenswarm.agents.harness.team.team_manager as tm_mod

    monkeypatch.setattr(
        tm_mod, "get_team_manager", lambda channel=None: _RecorderTeamManager()
    )

    fake_agent = types.SimpleNamespace(
        card=types.SimpleNamespace(name="leader", id="t_leader"),
    )
    rail.init(fake_agent)

    workspace = captured["rail_context"].team_workspace
    assert workspace.root_dir == "/tmp/team-x"
    assert workspace.skills_dir == "/tmp/team-x/skills"
    assert workspace.team_id == "t"
    assert workspace.config == config
    assert workspace.trajectory_registry is registry_obj


# ---------------------------------------------------------------------------
# code.team / team.plan declarative profile
# ---------------------------------------------------------------------------

_EXPECTED_CODE_RAIL_NAMES: frozenset[str] = frozenset(
    {
        registry.CODE_RUNTIME_PROMPT,
        registry.RESPONSE_PROMPT,
        registry.STREAM_EVENT,
        registry.SECURITY,
        registry.CODE_LSP,
        registry.CODE_PROJECT_MEMORY,
        registry.PERMISSION_INTERRUPT,
        registry.SYS_OPERATION,
        registry.CODE_CODING_MEMORY,
        registry.CODE_AGENT_MODE,
        registry.STRUCTURED_ASK_USER,
        registry.CONTEXT_PROCESSOR,
        registry.CODE_TASK_PLANNING,
        registry.CODE_AGENT_RAIL,
        registry.USER_HOOKS,
        registry.CODE_SKILL_USE,
        registry.CODE_WORKTREE,
        registry.CODE_CONFIRM_INTERRUPT,
        registry.MEMBER_SKILL_TOOLKIT,
        registry.TEAM_WORKSPACE_REPORT_PATH,
        registry.PLUGIN_RAILS,
    }
)


@pytest.mark.parametrize("mode", ["code.team", "team.plan"])
def test_code_capability_specs_rail_and_tool_names(mode: str) -> None:
    """Code modes emit the code rail/tool profile (not the chat-team common rails)."""
    register_swarm_providers()
    rails_specs, tool_specs = build_member_capability_specs({}, mode, "leader")
    rail_names = {spec.type for spec in rails_specs}
    tool_names = {spec.type for spec in tool_specs}

    assert _EXPECTED_CODE_RAIL_NAMES <= rail_names
    assert registry.TEAM_SKILL_EVOLUTION in rail_names
    # The chat-team common runtime prompt is replaced by the code variant.
    assert registry.RUNTIME_PROMPT not in rail_names
    assert tool_names == {
        registry.WEB_SEARCH,
        registry.WEB_FETCH,
        registry.WEB_PAID_SEARCH,
        registry.VISION,
        registry.AUDIO,
        # SKILL_TOOLKIT moved to the MEMBER_SKILL_TOOLKIT rail (see common set).
        registry.USER_TODOS,
        registry.VIDEO,
        registry.IMAGE_GEN,
        registry.XIAOYI_PHONE,
        registry.CODE_EXTRA_TOOLS,
        registry.CRON_TOOLS,
        registry.SEND_FILE,
    }


def test_code_subagent_specs_use_factory_names() -> None:
    """Code modes declare explore/plan (+ gated code) sub-agents via factory_name."""
    register_swarm_providers()
    config = {"react": {"subagents": {"code_agent": {"enabled": True}}}}

    subs = build_member_subagent_specs(config, "code.team", "leader")
    factory_names = [spec.factory_name for spec in subs]

    assert registry.EXPLORE_AGENT in factory_names
    assert registry.PLAN_AGENT in factory_names
    assert registry.CODE_AGENT in factory_names
    # Team mode has no code sub-agents.
    assert build_member_subagent_specs({}, "team", "leader") == []


def test_code_runtime_language_by_mode_and_role() -> None:
    """Only the team.plan leader uses the configured language; code is else English."""
    plan_leader = SwarmBuildContext(
        mode="team.plan",
        role="leader",
        config={"preferred_language": "zh"},
    )
    plan_teammate = SwarmBuildContext(mode="team.plan", role="teammate", config={})
    code_team = SwarmBuildContext(mode="code.team", role="leader", config={})

    assert code_rails.code_runtime_language(plan_leader) in {"cn", "zh"}
    assert code_rails.code_runtime_language(plan_teammate) == "en"
    assert code_rails.code_runtime_language(code_team) == "en"


def test_code_extra_tools_gated_by_config() -> None:
    """The code-exclusive tool provider only builds when ``acp_agents`` is configured."""
    register_swarm_providers()
    enabled = SwarmBuildContext(config={"acp_agents": {"demo": {}}})
    disabled = SwarmBuildContext(config={})

    assert tools.build_code_extra_tools({}, disabled) == []
    assert isinstance(tools.build_code_extra_tools({}, enabled), list)


def test_code_member_builds_declaratively_without_post_processing(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A code.team member builds through ``spec.build`` with zero post-processing.

    This is the crux of the "no customizer / no rail.init hack" contract: the
    code rails, sub-agents and system prompt materialize through openjiuwen's
    normal declarative build, and ``configure_code_team_member_agent`` is never
    invoked.
    """
    register_swarm_providers()
    config = get_config()
    model_client_config, _, _ = resolve_model_config(config)
    base = DeepAgentSpec(
        model=TeamModelConfig(
            model_client_config=ModelClientConfig(**model_client_config)
        ),
        workspace=WorkspaceSpec(root_path=str(tmp_path), language="en"),
    )
    spec = build_member_deep_agent_spec(config, "code.team", "leader", base)

    import jiuwenswarm.server.runtime.agent_adapter.interface_code as interface_code

    post_processing_calls: list[int] = []
    monkeypatch.setattr(
        interface_code,
        "configure_code_team_member_agent",
        lambda *args, **kwargs: post_processing_calls.append(1),
    )

    ctx = SwarmBuildContext(
        mode="code.team",
        role="leader",
        member_name="leader",
        member_card_id="t_leader",
        session_id="s",
        channel="web",
        team_id="t",
        project_dir=str(tmp_path),
        team_ws_root=str(tmp_path),
        team_skills_dir=str(tmp_path / "skills"),
        global_skills_dir=str(tmp_path / "global"),
        trajectory_registry=InMemoryTrajectoryRegistry(),
        config=config,
    )
    agent = spec.build(context=ctx)

    rails = list(getattr(agent, "_pending_rails", [])) + list(
        getattr(agent, "_registered_rails", [])
    )
    rail_types = {type(rail).__name__ for rail in rails}

    # Zero post-processing: the legacy code customizer is never invoked.
    assert post_processing_calls == []
    # Code-specific rails materialized via the normal declarative spec.build.
    for expected in (
        "CodeTaskPlanningRail",
        "CodeAgentModeRail",
        "StructuredAskUserRail",
        "WorktreeRail",
    ):
        assert expected in rail_types, (expected, sorted(rail_types))
    # The code system prompt is set declaratively on the spec.
    assert agent.deep_config.system_prompt
    # CodingMemoryRail is published for the code_agent sub-agent to reuse.
    assert ctx.extras.get(code_rails.CODING_MEMORY_EXTRAS_KEY) is not None
    logger.info("code member declarative build rail types: %s", sorted(rail_types))


# ---------------------------------------------------------------------------
# Serializable build-context seed (spawned / distributed member rebuild)
# ---------------------------------------------------------------------------


def test_swarm_build_context_seed_round_trip() -> None:
    """``to_seed`` exports the serializable fields; ``from_seed`` restores them."""
    base = SwarmBuildContext(
        session_id="s1",
        request_id="r1",
        channel_id="c1",
        channel="web",
        request_metadata={"mode": "code.team"},
        mode="code.team",
        project_dir="/tmp/proj",
        team_id="t1",
        team_ws_root="/tmp/ws",
        team_skills_dir="/tmp/ws/skills",
        global_skills_dir="/tmp/global",
        trajectory_registry=InMemoryTrajectoryRegistry(),
        config={"team": {}},
    )
    seed = base.to_seed()

    # Per-member and non-serializable fields stay out of the seed.
    for excluded in (
        "member_name",
        "role",
        "language",
        "workspace",
        "member_card_id",
        "config",
        "trajectory_registry",
    ):
        assert excluded not in seed

    registry_obj = InMemoryTrajectoryRegistry()
    restored = SwarmBuildContext.from_seed(
        seed,
        config={"k": "v"},
        trajectory_registry=registry_obj,
    )
    assert restored.session_id == "s1"
    assert restored.mode == "code.team"
    assert restored.project_dir == "/tmp/proj"
    assert restored.team_id == "t1"
    assert restored.team_ws_root == "/tmp/ws"
    assert restored.request_metadata == {"mode": "code.team"}
    # Non-serializable handles are sourced from the receiver, not the seed.
    assert restored.config == {"k": "v"}
    assert restored.trajectory_registry is registry_obj


def test_build_context_factory_rebuilds_and_shares_registry() -> None:
    """The registered factory rebuilds a context and shares per-team registries."""
    from openjiuwen.agent_teams.schema.build_context import build_context_from_seed

    register_swarm_providers()
    seed = {"session_id": "s", "team_id": "t", "mode": "team", "project_dir": "/p"}

    ctx = build_context_from_seed(seed)
    assert isinstance(ctx, SwarmBuildContext)
    assert ctx.mode == "team"
    assert ctx.project_dir == "/p"
    # config is sourced locally (this process's config.yaml), not from the seed.
    assert ctx.config is not None
    assert ctx.trajectory_registry is not None

    # Same (session, team) shares a registry; a different team is isolated.
    again = build_context_from_seed(seed)
    assert again.trajectory_registry is ctx.trajectory_registry
    other = build_context_from_seed(
        {"session_id": "s", "team_id": "t2", "mode": "team"}
    )
    assert other.trajectory_registry is not ctx.trajectory_registry


def test_enrich_sets_serializable_build_context_seed() -> None:
    """Enrichment leaves a serializable seed mirroring the live build context."""
    spec = _make_team_spec()
    enrich_team_spec_for_swarm(
        spec,
        session_id="s",
        mode="code.team",
        project_dir="/tmp/proj",
        channel_id="web",
    )
    assert spec.build_context_seed is not None
    assert spec.build_context_seed["mode"] == "code.team"
    assert spec.build_context_seed["project_dir"] == "/tmp/proj"
    assert spec.build_context_seed["team_id"] == spec.team_name
    # The seed equals what the live context exports.
    assert spec.build_context_seed == spec.build_context.to_seed()


def test_distributed_member_rebuild_reconstructs_build_context() -> None:
    """A serialized member spec rebuilds its provider context from the seed.

    Simulates the spawn / distributed boundary without ZMQ: ``model_dump`` drops
    the live ``build_context`` but carries ``build_context_seed``;
    ``materialize_build_context`` reconstructs the context from it via the
    registered factory.
    """
    register_swarm_providers()
    spec = _make_team_spec()
    enrich_team_spec_for_swarm(
        spec,
        session_id="s",
        mode="code.team",
        project_dir="/tmp/proj",
        channel_id="web",
    )

    payload = spec.model_dump(mode="json")
    # build_context (live) is excluded; the seed survives the round-trip.
    assert "build_context" not in payload
    assert payload["build_context_seed"]["mode"] == "code.team"

    rebuilt = TeamAgentSpec.model_validate(payload)
    assert rebuilt.build_context is None
    rebuilt.materialize_build_context()

    assert isinstance(rebuilt.build_context, SwarmBuildContext)
    assert rebuilt.build_context.mode == "code.team"
    assert rebuilt.build_context.project_dir == "/tmp/proj"
    assert rebuilt.build_context.config is not None
    assert rebuilt.build_context.trajectory_registry is not None
    # Idempotent: a second call does not replace the rebuilt context.
    existing = rebuilt.build_context
    rebuilt.materialize_build_context()
    assert rebuilt.build_context is existing


def test_rebuilt_member_spec_keeps_provider_declarations() -> None:
    """Provider rail / sub-agent declarations and the code prompt survive the round-trip.

    Complements the build-context reconstruction: the existing crown-jewel proves the
    declarative build itself, so here we prove the *serialized* code.team spec still
    carries its ``swarm.*`` rail / sub-agent references and code system prompt — so the
    post-round-trip build yields the same capabilities with no post-processing. (We do
    not build here: a second in-process ``spec.build`` would collide on the global tool
    registry, which never happens in production where each member builds in its own
    process.)
    """
    register_swarm_providers()
    spec = _make_team_spec()
    enrich_team_spec_for_swarm(
        spec,
        session_id="s",
        mode="code.team",
        project_dir="/tmp/proj",
        channel_id="web",
    )

    rebuilt = TeamAgentSpec.model_validate(spec.model_dump(mode="json"))
    teammate = rebuilt.agents["teammate"]

    rail_types = {rail.type for rail in teammate.rails}
    assert registry.CODE_TASK_PLANNING in rail_types
    assert registry.CODE_AGENT_MODE in rail_types
    assert registry.CODE_WORKTREE in rail_types
    # The code runtime prompt replaces the chat-team common runtime prompt.
    assert registry.CODE_RUNTIME_PROMPT in rail_types
    assert registry.RUNTIME_PROMPT not in rail_types
    # Sub-agents are declared by ``factory_name`` and survive serialization.
    leader_factory_names = {
        sub.factory_name for sub in rebuilt.agents["leader"].subagents
    }
    assert registry.EXPLORE_AGENT in leader_factory_names
    assert registry.PLAN_AGENT in leader_factory_names
    # The code system prompt is carried declaratively on the spec.
    assert teammate.system_prompt


def test_swarm_assembly_hint_from_seed_and_legacy() -> None:
    """The bootstrap envelope hint carries mode/project_dir; empty for legacy."""
    from jiuwenswarm.agents.harness.team import remote_member_bootstrap as rmb

    spec = _make_team_spec()
    enrich_team_spec_for_swarm(
        spec,
        session_id="s",
        mode="code.team",
        project_dir="/tmp/proj",
        channel_id="web",
    )
    leader = types.SimpleNamespace(spec=spec)
    assert rmb._swarm_assembly_hint(leader) == {
        "mode": "code.team",
        "project_dir": "/tmp/proj",
    }

    # Legacy spec (no seed, no live context) yields no hint -> teammate stays legacy.
    legacy = types.SimpleNamespace(spec=_make_team_spec())
    assert rmb._swarm_assembly_hint(legacy) == {}
    # Defensive: a team agent without a spec yields no hint.
    assert rmb._swarm_assembly_hint(types.SimpleNamespace()) == {}
