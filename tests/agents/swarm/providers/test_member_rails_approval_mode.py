# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""user-mediated vs leader-mediated teammate permission rail assembly.

Task 2 (teammate-user-mediated-approval): ``team_approval_mode`` (Task 1 field
on ``TeamAgentSpec``) is plumbed through the real assembly path —
``assembly.py`` threads ``spec.team_approval_mode`` into
``build_member_deep_agent_spec``; ``config_specs`` bakes it into the
``TEAM_PERMISSION`` ``RailSpec.params`` alongside ``permissions_config``; the
``member_rails`` provider reads it from ``params`` (not ``context.config``).
When ``"user-mediated"`` the teammate ``TeamPermissionRail`` is built WITHOUT
``TeamApprovalOrchestrator`` / ``ToolPermissionHost`` so an ASK-level tool call
falls through to the base rail's ``self.interrupt()``
(``tool_security_rail.py:528``) and surfaces to the web user via
``chat.ask_user_question``. ``leader-mediated`` (opt-out) preserves the
orchestrator + host unchanged (现状逐字不变).

The tests drive the real plumbing so a break in any layer (assembly →
config_specs → provider) fails the suite: no value is stuffed into
``context.config``.
"""

from __future__ import annotations

import inspect
import re
from types import SimpleNamespace
from typing import Any

from openjiuwen.agent_teams.rails.team_context import inject_team_handles
from openjiuwen.agent_teams.schema.blueprint import LeaderSpec, TeamAgentSpec
from openjiuwen.agent_teams.schema.deep_agent_spec import DeepAgentSpec

from jiuwenswarm.agents.swarm import enrich_team_spec_for_swarm, registry
from jiuwenswarm.agents.swarm.config_specs import build_member_capability_specs
from jiuwenswarm.agents.swarm.context import SwarmBuildContext
from jiuwenswarm.agents.swarm.providers.member_rails import _build_team_permission_rail

# Config shape accepted by config_specs + PermissionEngine at construction
# (mirrors the tiered_policy shape used by config.yaml + the agent-core rail
# tests). The permissions section becomes the rail's permissions_config param.
_CONFIG: dict[str, Any] = {
    "agents": {"leader": {"skills": []}, "teammate": {"skills": []}},
    "permissions": {
        "enabled": True,
        "schema": "tiered_policy",
        "permission_mode": "normal",
        "defaults": {"*": "allow"},
        "rules": [],
        "approval_overrides": [],
    },
}


def _make_context() -> SwarmBuildContext:
    """Build a SwarmBuildContext with the live team handles the provider gate
    requires (backend + messager on ``BuildContext.extras``).

    ``team_approval_mode`` is NOT placed here — it rides on ``RailSpec.params``
    (serializable, survives cross-process), so the context only carries the
    runtime handles. user-mediated never touches the backend attributes (it
    returns before the orchestrator is built), but they must be present to
    clear the provider's backend/messager gate.
    """
    backend = SimpleNamespace(
        team_name="team",
        member_name="teammate",
        db=None,
        leader_member_name="leader",
    )
    messager = object()  # TeamMessageManager only stores it; no behavior needed.
    extras: dict[str, Any] = {}
    inject_team_handles(extras, team_backend=backend, messager=messager)
    return SwarmBuildContext(extras=extras)


def _team_permission_params(team_approval_mode: str) -> dict[str, Any]:
    """Drive the real config_specs plumbing: build the teammate capability
    specs with ``team_approval_mode`` and return the TEAM_PERMISSION rail's
    params dict (the exact payload the harness would hand the provider)."""
    rails, _ = build_member_capability_specs(
        _CONFIG,
        "team",
        "teammate",
        enable_permissions=True,
        team_approval_mode=team_approval_mode,
    )
    team_perm = next(rail for rail in rails if rail.type == registry.TEAM_PERMISSION)
    return dict(team_perm.params)


def test_user_mediated_rail_skips_orchestrator_host() -> None:
    """user-mediated: params carry team_approval_mode → no orchestrator/host →
    confirmation hook unset → ASK falls through to base ``self.interrupt()``
    (frontend user)."""
    rail = _build_team_permission_rail(
        params=_team_permission_params("user-mediated"),
        context=_make_context(),
    )
    assert rail is not None
    # Base rail does ``self._host = host or ToolPermissionHost()``, so the host
    # is never None — the load-bearing signal is an unset confirmation hook
    # (the exact check the ASK path makes at tool_security_rail.py:451).
    host = getattr(rail, "_host", None)
    assert host is not None
    assert host.request_permission_confirmation is None


def test_leader_mediated_rail_wires_orchestrator_host() -> None:
    """leader-mediated: orchestrator + host wired → ASK routes to the leader
    (现状逐字不变)."""
    rail = _build_team_permission_rail(
        params=_team_permission_params("leader-mediated"),
        context=_make_context(),
    )
    assert rail is not None
    host = getattr(rail, "_host", None)
    assert host is not None
    assert host.request_permission_confirmation is not None


def test_enrich_threads_spec_team_approval_mode_into_teammate_rail_params() -> None:
    """assembly.py threads ``spec.team_approval_mode`` through the whole
    enrich chain onto the teammate's TEAM_PERMISSION RailSpec.params. Catches
    a break in the assembly.py → config_specs plumbing layer."""
    spec = TeamAgentSpec(
        agents={"leader": DeepAgentSpec(), "teammate": DeepAgentSpec()},
        team_name="unit_team",
        leader=LeaderSpec(member_name="team_leader"),
        enable_permissions=True,
        team_approval_mode="user-mediated",
    )

    enrich_team_spec_for_swarm(spec, session_id="s", mode="team", channel_id="web")

    team_perm = next(
        rail
        for rail in (spec.agents["teammate"].rails or [])
        if rail.type == registry.TEAM_PERMISSION
    )
    assert team_perm.params["team_approval_mode"] == "user-mediated"


# ---------------------------------------------------------------------------
# Default-value guards (二轮评审 Important #2): the explicit-mode tests above
# all pass ``team_approval_mode`` explicitly, so flipping any *default* back to
# ``leader-mediated`` would leave the suite green while silently splitting
# behaviour. The guards below pin every layer's default to ``user-mediated``.
# ---------------------------------------------------------------------------


def test_config_specs_default_bakes_user_mediated_into_team_permission_params() -> None:
    """Default guard: ``build_member_capability_specs`` called WITHOUT the
    ``team_approval_mode`` kwarg must bake ``"user-mediated"`` (the function
    default) into the teammate ``TEAM_PERMISSION`` ``RailSpec.params``. Catches
    a silent flip of the ``config_specs`` function default back to
    ``leader-mediated``."""
    rails, _ = build_member_capability_specs(
        _CONFIG,
        "team",
        "teammate",
        enable_permissions=True,
        # 故意不传 team_approval_mode —— 钉 config_specs 函数默认值
    )
    team_perm = next(rail for rail in rails if rail.type == registry.TEAM_PERMISSION)
    assert team_perm.params["team_approval_mode"] == "user-mediated"


def test_team_permission_input_param_field_defaults_to_user_mediated() -> None:
    """Default guard: ``TeamPermissionInput.team_approval_mode`` ``param_field``
    default must be ``"user-mediated"``. This is the provider's final fallback
    when ``RailSpec.params`` omits the key, so flipping it back to
    ``leader-mediated`` would silently route teammate ASK to the leader."""
    from jiuwenswarm.agents.swarm.providers.member_rails import TeamPermissionInput

    field = TeamPermissionInput.model_fields["team_approval_mode"]
    assert field.default == "user-mediated"


def test_enrich_default_team_approval_mode_threads_user_mediated() -> None:
    """Default guard: ``TeamAgentSpec`` constructed WITHOUT ``team_approval_mode``
    (schema default ``"user-mediated"``) → ``assembly.py`` reads it via
    ``getattr(spec, "team_approval_mode", "user-mediated")`` → ``config_specs``
    bakes it → the teammate ``TEAM_PERMISSION`` ``RailSpec.params`` carry
    ``"user-mediated"``. Pins the full default chain (schema default + assembly
    plumbing + config_specs default).

    A source-level tripwire also pins the ``assembly.py`` getattr fallback
    literal to ``"user-mediated"`` so the defensive path (a spec object missing
    the field) cannot silently fall back to ``leader-mediated``.
    """
    from jiuwenswarm.agents.swarm import assembly as assembly_module

    # Source tripwire: assembly.py getattr fallback literal must be user-mediated.
    source = inspect.getsource(assembly_module)
    assert re.search(
        r'getattr\(\s*spec,\s*"team_approval_mode",\s*"user-mediated"\s*\)',
        source,
        re.DOTALL,
    ), "assembly.py must read spec.team_approval_mode with a 'user-mediated' fallback"

    spec = TeamAgentSpec(
        agents={"leader": DeepAgentSpec(), "teammate": DeepAgentSpec()},
        team_name="unit_team",
        leader=LeaderSpec(member_name="team_leader"),
        enable_permissions=True,
        # 故意不传 team_approval_mode —— 钉 schema default + assembly getattr fallback
    )

    enrich_team_spec_for_swarm(spec, session_id="s", mode="team", channel_id="web")

    team_perm = next(
        rail
        for rail in (spec.agents["teammate"].rails or [])
        if rail.type == registry.TEAM_PERMISSION
    )
    assert team_perm.params["team_approval_mode"] == "user-mediated"
