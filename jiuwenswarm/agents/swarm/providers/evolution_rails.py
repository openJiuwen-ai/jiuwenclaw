# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Skill-evolution rail providers for swarm provider-based team assembly.

This module ports the leader-only ``TeamSkillEvolutionRail`` /
``TeamSkillCreateRail`` and the teammate-only ``SkillEvolutionRail`` branches of
the legacy ``team_manager`` customizer into config-sourced rail providers.

In the legacy flow the customizer built each evolution rail and then imperatively
registered it with the per-channel :class:`TeamManager` (live-rail bookkeeping,
approval/sync targets, hot-reload contexts). Providers, however, only return rail
instances and never see the live agent. To preserve that bookkeeping without a
customizer, each evolution rail is wrapped in a thin subclass that overrides
``init(self, agent)`` to perform the same ``TeamManager`` registration once
openjiuwen attaches the rail to its agent. The build-time context handles are
captured on the instance at construction time by the provider factory.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

import portalocker

from openjiuwen.agent_teams.harness.manifest import (
    ConstructionInput,
    context_field,
    ElementKind,
    harness_element,
    param_field,
)
from openjiuwen.harness.rails import (
    EvolutionInterruptRail,
    SkillEvolutionRail,
    TeamSkillCreateRail,
    TeamSkillEvolutionRail,
)
from openjiuwen.harness.rails.evolution import EvolutionReviewRuntime

from jiuwenswarm.agents.swarm.context import SwarmBuildContext
from jiuwenswarm.agents.harness.team.team_runtime_inheritance import get_team_evolution_skills_dirs
from jiuwenswarm.common.config import get_skill_evolution_enabled
from jiuwenswarm.server.runtime.skill import load_execution_disabled_skills

logger = logging.getLogger(__name__)

# Provider name constants; namespaced under the shared "swarm." prefix.
TEAM_SKILL_EVOLUTION = "swarm.team_skill_evolution"
TEAM_SKILL_CREATE = "swarm.team_skill_create"
MEMBER_SKILL_EVOLUTION = "swarm.member_skill_evolution"
EVOLUTION_INTERRUPT = "swarm.evolution_interrupt"

_SAFE_SKILL_NAME = re.compile(r"^[A-Za-z0-9_-]+$")


def _member_skills_dir_from_context(ctx: Any) -> str | None:
    """Resolve the current member's private Skill directory."""
    workspace = getattr(ctx, "workspace", None)
    if workspace is not None:
        get_node_path = getattr(workspace, "get_node_path", None)
        if callable(get_node_path):
            value = get_node_path("skills")
            if value:
                return str(value)
        root_path = getattr(workspace, "root_path", None)
        if root_path:
            return str(Path(root_path) / "skills")

    team_id = str(getattr(ctx, "team_id", "") or "").strip()
    member_name = str(getattr(ctx, "member_name", "") or "").strip()
    if team_id and member_name:
        from openjiuwen.agent_teams.workspace_layout import team_member_workspace_path

        return str(team_member_workspace_path(team_id, member_name) / "skills")
    return None


def ensure_member_skill_copy(
    *,
    member_skills_dir: str | Path,
    global_skills_dir: str | Path,
    skill_name: str,
) -> Path:
    """Materialize one global Skill as a member-owned copy before mutation.

    Member workspaces historically contain links into the global Skill store.
    Evolution must never follow such a link when its intended scope is one
    member.  This helper atomically replaces that link (or fills a missing
    entry) with a real directory while preserving an existing private copy.
    """
    normalized_name = str(skill_name or "").strip()
    if not normalized_name or _SAFE_SKILL_NAME.fullmatch(normalized_name) is None:
        raise ValueError(f"unsafe Skill name: {skill_name!r}")

    member_root = Path(member_skills_dir).expanduser()
    global_root = Path(global_skills_dir).expanduser().resolve()
    source = global_root / normalized_name
    if not source.is_dir() or not (source / "SKILL.md").is_file():
        raise FileNotFoundError(f"global Skill '{normalized_name}' does not exist: {source}")

    member_root.mkdir(parents=True, exist_ok=True)
    destination = member_root / normalized_name
    lock_path = member_root / ".member-skill-copy.lock"
    with portalocker.Lock(str(lock_path), timeout=30):
        if destination.is_dir():
            try:
                if destination.resolve() != source.resolve():
                    return destination
            except OSError:
                pass

        temporary = member_root / f".{normalized_name}.copy-{uuid.uuid4().hex}"
        try:
            shutil.copytree(
                source,
                temporary,
                symlinks=False,
                copy_function=shutil.copy2,
                dirs_exist_ok=False,
            )
            if os.path.lexists(destination):
                from jiuwenswarm.agents.harness.team.team_skill_links import (
                    remove_skill_dir_link,
                )

                remove_skill_dir_link(destination)
            if os.path.lexists(destination):
                # A real directory appeared while the copy was prepared. It is
                # already member-owned, so preserve it rather than overwriting.
                return destination
            os.replace(temporary, destination)
            return destination
        finally:
            if temporary.exists():
                shutil.rmtree(temporary, ignore_errors=True)


def _build_team_workspace_info(
    *,
    team_ws_root: str | None,
    team_skills_dir: str | None,
    team_id: str,
    config: dict[str, Any] | None,
    trajectory_registry: Any,
) -> Any:
    """Build a fully-populated ``TeamWorkspaceInfo`` for rail-context registration.

    The hot-reload path (``update_evolution_config`` →
    ``_build_and_mount_member_rails_for_context`` → ``build_member_rails``)
    rebuilds the leader evolution / create rails from the stored
    ``TeamRailMountContext.team_workspace``; populating every field keeps that
    rebuild working for provider-mounted teams (the legacy customizer passed the
    same fields, the swarm path previously passed an empty object).

    Args:
        team_ws_root: Team shared workspace root path.
        team_skills_dir: Team shared skills directory (rebuild gate + sync source).
        team_id: Team name.
        config: The resolved ``config.yaml`` mapping.
        trajectory_registry: Per-team in-memory trajectory registry.

    Returns:
        A populated ``TeamWorkspaceInfo``.
    """
    from jiuwenswarm.agents.harness.team.team_runtime_inheritance import (
        TeamWorkspaceInfo,
    )

    return TeamWorkspaceInfo(
        root_dir=team_ws_root,
        skills_dir=team_skills_dir,
        team_id=team_id,
        config=config,
        trajectory_registry=trajectory_registry,
    )


def _register_team_rail_context(
    channel: str,
    session_id: str,
    agent: Any,
    team_workspace: Any,
) -> None:
    """Register the leader rail-mount context for hot config reloads.

    Mirrors the legacy customizer's ``register_team_rail_context`` call. Imported
    lazily so this provider module never imports ``team_manager`` at module load
    (the swarm assembly entry point is imported from team code paths).

    Args:
        channel: Resolved channel key used to locate the team manager.
        session_id: Active session id owning the rail.
        agent: The live member agent the rail was attached to.
        team_workspace: Fully-populated ``TeamWorkspaceInfo`` so hot-reload can
            rebuild the leader rails.
    """
    from jiuwenswarm.agents.harness.team.team_manager import (
        _make_team_rail_mount_context,
        get_team_manager,
    )
    from jiuwenswarm.agents.harness.team.team_runtime_inheritance import (
        get_default_model_name,
    )

    team_manager = get_team_manager(channel)
    context = team_manager.get_team_rail_context(session_id)
    if context is not None:
        return
    team_manager.register_team_rail_context(
        session_id,
        _make_team_rail_mount_context(
            agent=agent,
            role="leader",
            channel=channel,
            model_name=get_default_model_name(),
            root_dir=team_workspace.root_dir,
            skills_dir=team_workspace.skills_dir,
            team_id=team_workspace.team_id,
            config=team_workspace.config,
            trajectory_registry=team_workspace.trajectory_registry,
        ),
    )


class SwarmTeamSkillEvolutionRail(TeamSkillEvolutionRail):
    """``TeamSkillEvolutionRail`` that self-registers with the team manager.

    On ``init`` it records itself as a live rail, as the session's team skill
    rail, and registers the workspace-to-global skill sync target, reproducing
    the leader-side registration the legacy customizer performed.
    """

    def bind_swarm_context(
        self,
        *,
        channel: str,
        session_id: str,
        team_ws_root: str | None,
        team_skills_dir: str,
        team_id: str,
        config: dict[str, Any] | None,
        trajectory_registry: Any,
    ) -> None:
        """Capture the build-time handles needed for team-manager registration.

        Args:
            channel: Resolved channel key used to locate the team manager.
            session_id: Active session id owning the rail.
            team_ws_root: Team shared workspace root path.
            team_skills_dir: Team shared skills directory.
            team_id: Team name.
            config: The resolved ``config.yaml`` mapping.
            trajectory_registry: Per-team in-memory trajectory registry.
        """
        self._swarm_channel = channel
        self._swarm_session_id = session_id
        self._swarm_team_skills_dir = team_skills_dir
        self._swarm_team_workspace = _build_team_workspace_info(
            team_ws_root=team_ws_root,
            team_skills_dir=team_skills_dir,
            team_id=team_id,
            config=config,
            trajectory_registry=trajectory_registry,
        )

    def bind_review_feedback_skill_rail(self, rail: SkillEvolutionRail) -> None:
        """Bind the global regular-Skill sidecar used at team aggregation."""

        self._swarm_review_feedback_skill_rail = rail

    def init(self, agent: Any) -> None:
        """Attach to *agent* and register with the per-channel team manager.

        Args:
            agent: The live member agent this rail is mounted on.
        """
        super().init(agent)
        try:
            from jiuwenswarm.agents.harness.team.team_manager import get_team_manager

            team_manager = get_team_manager(self._swarm_channel)
            team_manager.register_team_live_rail(self._swarm_session_id, agent, self)
            team_manager.register_team_skill_rail(self._swarm_session_id, self)
            review_feedback_rail = getattr(self, "_swarm_review_feedback_skill_rail", None)
            if review_feedback_rail is not None:
                team_manager.register_review_feedback_skill_rail(
                    self._swarm_session_id,
                    review_feedback_rail,
                )
            if team_manager.consume_team_evolution_watcher_deferred(self._swarm_session_id):
                from jiuwenswarm.server.runtime.agent_adapter.team_helpers import (
                    ensure_team_evolution_watcher,
                )

                ensure_team_evolution_watcher(
                    self._swarm_channel,
                    self._swarm_session_id,
                    source="rail_registered",
                )
            _register_team_rail_context(
                self._swarm_channel,
                self._swarm_session_id,
                agent,
                self._swarm_team_workspace,
            )
            logger.info(
                "[swarm.team_skill_evolution] registered live rail "
                "(session=%s, skills_dir=%s)",
                self._swarm_session_id,
                self._swarm_team_skills_dir,
            )
        except Exception as exc:
            logger.warning(
                "[swarm.team_skill_evolution] team manager registration failed: %s", exc
            )


class SwarmTeamSkillCreateRail(TeamSkillCreateRail):
    """``TeamSkillCreateRail`` that self-registers with the team manager."""

    def bind_swarm_context(
        self,
        *,
        channel: str,
        session_id: str,
        team_ws_root: str | None,
        team_skills_dir: str,
        team_id: str,
        config: dict[str, Any] | None,
        trajectory_registry: Any,
    ) -> None:
        """Capture the build-time handles needed for team-manager registration.

        Args:
            channel: Resolved channel key used to locate the team manager.
            session_id: Active session id owning the rail.
            team_ws_root: Team shared workspace root path.
            team_skills_dir: Team shared skills directory.
            team_id: Team name.
            config: The resolved ``config.yaml`` mapping.
            trajectory_registry: Per-team in-memory trajectory registry.
        """
        self._swarm_channel = channel
        self._swarm_session_id = session_id
        self._swarm_team_workspace = _build_team_workspace_info(
            team_ws_root=team_ws_root,
            team_skills_dir=team_skills_dir,
            team_id=team_id,
            config=config,
            trajectory_registry=trajectory_registry,
        )

    def init(self, agent: Any) -> None:
        """Attach to *agent* and register with the per-channel team manager.

        Args:
            agent: The live member agent this rail is mounted on.
        """
        super().init(agent)
        try:
            from jiuwenswarm.agents.harness.team.team_manager import get_team_manager

            team_manager = get_team_manager(self._swarm_channel)
            team_manager.register_team_live_rail(self._swarm_session_id, agent, self)
            team_manager.register_team_skill_create_rail(self._swarm_session_id, self)
            _register_team_rail_context(
                self._swarm_channel,
                self._swarm_session_id,
                agent,
                self._swarm_team_workspace,
            )
            logger.info(
                "[swarm.team_skill_create] registered live rail (session=%s)",
                self._swarm_session_id,
            )
        except Exception as exc:
            logger.warning(
                "[swarm.team_skill_create] team manager registration failed: %s", exc
            )


class SwarmMemberSkillEvolutionRail(SkillEvolutionRail):
    """``SkillEvolutionRail`` that self-registers as a teammate evolution rail.

    The teammate evolution rail has no approval interrupt, but it is tracked as
    a live rail so a disabled hot-reload can unregister it from the mounted
    teammate agent as well as clear the manager registry.
    """

    def bind_swarm_context(
        self,
        *,
        channel: str,
        session_id: str,
        team_ws_root: str | None = None,
        team_skills_dir: str | None = None,
        team_id: str = "",
        config: dict[str, Any] | None = None,
        trajectory_registry: Any = None,
        language: str = "cn",
        member_name: str = "",
        member_skills_dir: str | None = None,
        global_skills_dir: str | None = None,
    ) -> None:
        """Capture the build-time handles needed for team-manager registration.

        Args:
            channel: Resolved channel key used to locate the team manager.
            session_id: Active session id owning the rail.
        """
        self._swarm_channel = channel
        self._swarm_session_id = session_id
        self._swarm_team_workspace = _build_team_workspace_info(
            team_ws_root=team_ws_root,
            team_skills_dir=team_skills_dir,
            team_id=team_id,
            config=config,
            trajectory_registry=trajectory_registry,
        )
        self._swarm_member_info = (team_id, language)
        self._swarm_member_name = member_name
        self._swarm_member_skills_dir = member_skills_dir
        self._swarm_global_skills_dir = global_skills_dir

    async def _handle_evolution_from_signals(
        self,
        *,
        skill_name: str,
        **kwargs: Any,
    ) -> Any:
        """Copy-on-write the target Skill, then use the standard evolution path."""
        if not self._swarm_member_skills_dir or not self._swarm_global_skills_dir:
            raise RuntimeError("member Skill copy-on-write scope is not configured")
        ensure_member_skill_copy(
            member_skills_dir=self._swarm_member_skills_dir,
            global_skills_dir=self._swarm_global_skills_dir,
            skill_name=skill_name,
        )
        return await super()._handle_evolution_from_signals(
            skill_name=skill_name,
            **kwargs,
        )

    async def _evolve_skill_with_sharing(
        self,
        *,
        skill_name: str,
        **kwargs: Any,
    ) -> bool:
        """Detach the member copy before shared records can be auto-saved."""
        if not self._swarm_member_skills_dir or not self._swarm_global_skills_dir:
            raise RuntimeError("member Skill copy-on-write scope is not configured")
        ensure_member_skill_copy(
            member_skills_dir=self._swarm_member_skills_dir,
            global_skills_dir=self._swarm_global_skills_dir,
            skill_name=skill_name,
        )
        return await super()._evolve_skill_with_sharing(
            skill_name=skill_name,
            **kwargs,
        )

    def init(self, agent: Any) -> None:
        """Attach to *agent* and register with the per-channel team manager.

        Args:
            agent: The live member agent this rail is mounted on.
        """
        super().init(agent)
        try:
            from jiuwenswarm.agents.harness.team.team_manager import get_team_manager

            team_manager = get_team_manager(self._swarm_channel)
            team_manager.register_team_live_rail(self._swarm_session_id, agent, self)
            team_manager.register_team_member_skill_evolution_rail(
                self._swarm_session_id,
                self,
            )
            register_member_context = getattr(
                team_manager, "register_team_member_rail_context", None
            )
            if callable(register_member_context):
                from jiuwenswarm.agents.harness.team.team_manager import (
                    _make_team_rail_mount_context,
                )

                team_id, language = self._swarm_member_info
                register_member_context(
                    self._swarm_session_id,
                    _make_team_rail_mount_context(
                        agent=agent,
                        role="teammate",
                        channel=self._swarm_channel,
                        language=language,
                        model_name="gpt-4",
                        root_dir=self._swarm_team_workspace.root_dir,
                        skills_dir=self._swarm_team_workspace.skills_dir,
                        team_id=team_id,
                        config=self._swarm_team_workspace.config,
                        trajectory_registry=self._swarm_team_workspace.trajectory_registry,
                    ),
                )
            logger.info(
                "[swarm.member_skill_evolution] registered member evolution rail "
                "(session=%s, member=%s)",
                self._swarm_session_id,
                self._swarm_member_name,
            )
        except Exception as exc:
            logger.warning(
                "[swarm.member_skill_evolution] team manager registration failed: %s",
                exc,
            )


def _build_evolution_llm_from(model_config: dict[str, Any]) -> tuple[Any, str]:
    """Build the evolution LLM Model from the serializable model-config param.

    Mirrors ``team_runtime_inheritance.build_evolution_llm`` but takes the already
    resolved (serializable) model config baked into ``params`` at spec-build time,
    so the live LLM handle is constructed here at build time (never in the schema).
    """
    from openjiuwen.core.foundation.llm import (
        Model,
        ModelClientConfig,
        ModelRequestConfig,
    )
    from jiuwenswarm.common.reasoning_injector import build_reasoning_model_request_kwargs

    model_client_config = model_config.get("model_client_config") or {}
    model_config_obj = model_config.get("model_config_obj") or {}
    model_name = model_config.get("model_name") or "gpt-4"
    request_config = ModelRequestConfig(
        **build_reasoning_model_request_kwargs(
            model_client_config=model_client_config,
            model_config_obj=model_config_obj,
            model_name=model_name,
        )
    )
    client_config = ModelClientConfig(**model_client_config)
    return Model(
        model_client_config=client_config, model_config=request_config
    ), model_name


def _build_evolution_approval_stack(
    rail: SkillEvolutionRail,
    *,
    review_runtime: EvolutionReviewRuntime,
    auto_save: bool,
    language: str,
) -> list[Any]:
    """Return the approval interrupt plus evolution rail in registration order."""
    return [
        EvolutionInterruptRail(
            review_runtime=review_runtime,
            submission_service=rail.approval_submission_service,
            auto_save=auto_save,
            language=language,
        ),
        rail,
    ]


harness_element(
    kind=ElementKind.RAIL,
    name=EVOLUTION_INTERRUPT,
    description="Active Skill evolution mutation approval interrupt rail.",
    builder=EvolutionInterruptRail,
)


class TeamSkillEvolutionInput(ConstructionInput):
    """Construction inputs for the leader team skill-evolution rail."""

    evolution_model_config: dict[str, Any] = param_field(
        default_factory=dict,
        description="Serializable evolution model config (LLM built at build time).",
    )
    auto_save: bool = param_field(
        default=False, description="Evolution auto-save approval flag."
    )
    team_skills_dir: str | None = context_field(
        attr="team_skills_dir", description="Team shared skills directory."
    )
    global_skills_dir: str | None = context_field(
        attr="global_skills_dir", description="Global regular skills directory."
    )
    language: str = context_field(
        attr="language", default="cn", description="Member language code."
    )
    role: str | None = context_field(attr="role", description="Team role value.")
    team_id: str = context_field(attr="team_id", default="", description="Team name.")
    trajectory_registry: Any = context_field(
        attr="trajectory_registry", description="Per-team trajectory registry."
    )
    channel: str = context_field(
        attr="channel", default="default", description="Resolved channel key."
    )
    session_id: str | None = context_field(
        attr="session_id", description="Active session id."
    )
    team_ws_root: str | None = context_field(
        attr="team_ws_root", description="Team shared workspace root."
    )


@harness_element(
    kind=ElementKind.RAIL,
    name=TEAM_SKILL_EVOLUTION,
    description="Leader-only team skill evolution rail (builds the evolution LLM and "
    "binds the per-team trajectory registry); skipped for non-leaders.",
    input_model=TeamSkillEvolutionInput,
)
def build_team_skill_evolution_rail(
    params: dict[str, Any],
    ctx: SwarmBuildContext,
) -> list[Any]:
    """Build the leader-only team skill evolution rail from the config source.

    Mirrors the leader branch of the legacy ``build_member_rails``: builds the
    evolution LLM, applies canonical auto-save behavior, binds the per-team trajectory registry
    when a team id is present, and wires the team-manager registration via the
    rail subclass ``init``.

    Args:
        params: Provider params (unused; kept for the provider contract).
        ctx: The per-member build context.

    Returns:
        ``[EvolutionInterruptRail, SwarmTeamSkillEvolutionRail]`` for the leader,
        or ``[]`` when the member is not the leader or no team skills directory
        is configured.
    """
    # Cheap gate before resolving (avoids building the evolution LLM for non-leaders).
    if (
        not get_skill_evolution_enabled(ctx.config)
        or ctx.role != "leader"
        or not ctx.team_skills_dir
    ):
        return []

    try:
        inp = TeamSkillEvolutionInput.resolve(params, ctx)
        Path(inp.team_skills_dir).mkdir(parents=True, exist_ok=True)
        llm_model, actual_model_name = _build_evolution_llm_from(
            inp.evolution_model_config
        )
        bound_registry = inp.trajectory_registry if inp.team_id else None
        review_runtime = EvolutionReviewRuntime()
        rail = SwarmTeamSkillEvolutionRail(
            get_team_evolution_skills_dirs(inp.team_skills_dir, ctx.global_skills_dir),
            llm=llm_model,
            model=actual_model_name,
            review_runtime=review_runtime,
            language=inp.language,
            signal_trigger=False,
            review_trigger=True,
            trajectory_source=bound_registry,
            trajectory_sink=bound_registry,
            member_role=inp.role,
            auto_save=inp.auto_save,
            team_id=inp.team_id,
            disabled_skills=load_execution_disabled_skills(),
        )
        if inp.global_skills_dir:
            review_feedback_rail = SkillEvolutionRail(
                inp.global_skills_dir,
                llm=llm_model,
                model=actual_model_name,
                review_runtime=EvolutionReviewRuntime(),
                language=inp.language,
                signal_trigger=False,
                review_trigger=False,
                auto_save=inp.auto_save,
                disabled_skills=load_execution_disabled_skills(),
            )
            rail.bind_review_feedback_skill_rail(review_feedback_rail)
        else:
            logger.warning(
                "[swarm.team_skill_evolution] review-feedback sidecar skipped: "
                "global_skills_dir is unavailable"
            )
        rail.bind_swarm_context(
            channel=inp.channel,
            session_id=inp.session_id,
            team_ws_root=inp.team_ws_root,
            team_skills_dir=inp.team_skills_dir,
            team_id=inp.team_id,
            config=ctx.config,
            trajectory_registry=inp.trajectory_registry,
        )
        logger.info(
            "[swarm.team_skill_evolution] built: skills_dir=%s, model=%s, "
            "auto_save=%s",
            inp.team_skills_dir,
            actual_model_name,
            inp.auto_save,
        )
        return _build_evolution_approval_stack(
            rail,
            review_runtime=review_runtime,
            auto_save=inp.auto_save,
            language=inp.language,
        )
    except Exception as exc:
        logger.warning(
            "[swarm.team_skill_evolution] build failed: %s", exc, exc_info=True
        )
        return []


class TeamSkillCreateInput(ConstructionInput):
    """Construction inputs for the leader team skill-create rail."""

    team_skills_dir: str | None = context_field(
        attr="team_skills_dir", description="Team shared skills directory."
    )
    language: str = context_field(
        attr="language", default="cn", description="Member language code."
    )
    channel: str = context_field(
        attr="channel", default="default", description="Resolved channel key."
    )
    session_id: str | None = context_field(
        attr="session_id", description="Active session id."
    )
    team_ws_root: str | None = context_field(
        attr="team_ws_root", description="Team shared workspace root."
    )
    team_id: str = context_field(attr="team_id", default="", description="Team name.")
    trajectory_registry: Any = context_field(
        attr="trajectory_registry", description="Per-team trajectory registry."
    )


@harness_element(
    kind=ElementKind.RAIL,
    name=TEAM_SKILL_CREATE,
    description="Leader-only team skill creation rail (gated by skill_evolution and "
    "a configured team skills directory); skipped otherwise.",
    input_model=TeamSkillCreateInput,
)
def build_team_skill_create_rail(
    params: dict[str, Any],
    ctx: SwarmBuildContext,
) -> SwarmTeamSkillCreateRail | None:
    """Build the leader-only team skill creation rail from the config source.

    Mirrors the leader branch of the legacy ``build_member_rails``: gated on
    the canonical ``react.evolution.skill_evolution`` switch and a configured
    team skills directory.

    Args:
        params: Provider params (unused; kept for the provider contract).
        ctx: The per-member build context.

    Returns:
        A ``SwarmTeamSkillCreateRail`` for the leader, or ``None`` when the
        member is not the leader, no team skills directory is configured, or
        skill creation is disabled.
    """
    if (
        not get_skill_evolution_enabled(ctx.config)
        or ctx.role != "leader"
        or not ctx.team_skills_dir
    ):
        return None

    inp = TeamSkillCreateInput.resolve(params, ctx)
    try:
        rail = SwarmTeamSkillCreateRail(
            inp.team_skills_dir,
            language=inp.language,
            auto_trigger=True,
        )
        rail.bind_swarm_context(
            channel=inp.channel,
            session_id=inp.session_id,
            team_ws_root=inp.team_ws_root,
            team_skills_dir=inp.team_skills_dir,
            team_id=inp.team_id,
            config=ctx.config,
            trajectory_registry=inp.trajectory_registry,
        )
        logger.info(
            "[swarm.team_skill_create] built: skills_dir=%s", inp.team_skills_dir
        )
        return rail
    except Exception as exc:
        logger.warning("[swarm.team_skill_create] build failed: %s", exc, exc_info=True)
        return None


class MemberSkillEvolutionInput(ConstructionInput):
    """Construction inputs for the teammate member skill-evolution rail."""

    evolution_model_config: dict[str, Any] = param_field(
        default_factory=dict,
        description="Serializable evolution model config (LLM built at build time).",
    )
    team_skills_dir: str | None = context_field(
        attr="team_skills_dir", description="Team shared skills directory."
    )
    member_skills_dir: str | None = context_field(
        resolver=_member_skills_dir_from_context,
        description="Member-private skills directory.",
    )
    global_skills_dir: str | None = context_field(
        attr="global_skills_dir", description="Global fallback skills directory."
    )
    language: str = context_field(
        attr="language", default="cn", description="Member language code."
    )
    trajectory_registry: Any = context_field(
        attr="trajectory_registry", description="Per-team trajectory registry."
    )
    team_id: str = context_field(attr="team_id", default="", description="Team name.")
    channel: str = context_field(
        attr="channel", default="default", description="Resolved channel key."
    )
    session_id: str | None = context_field(
        attr="session_id", description="Active session id."
    )
    member_name: str | None = context_field(
        attr="member_name", description="Current team member name."
    )


@harness_element(
    kind=ElementKind.RAIL,
    name=MEMBER_SKILL_EVOLUTION,
    description="Teammate-only member skill evolution rail (auto-save, "
    "disabled skills, conditional team trajectory sink).",
    input_model=MemberSkillEvolutionInput,
)
def build_member_skill_evolution_rail(
    params: dict[str, Any],
    ctx: SwarmBuildContext,
) -> list[Any]:
    """Build the teammate-only member skill evolution rail from the config source.

    Replicates ``build_skill_evolution_rail`` (``auto_save=True``,
    disabled skills, conditional team trajectory sink with
    ``member_role="teammate"``) but constructs the swarm subclass so the rail can
    self-register with the team manager from its ``init``.

    Args:
        params: Provider params (unused; kept for the provider contract).
        ctx: The per-member build context.

    Returns:
        ``[SwarmMemberSkillEvolutionRail]`` for non-leader members, or ``[]`` when
        the member is the leader or no team skills directory is configured.
    """
    # Cheap gate before resolving (avoids building the evolution LLM for the leader).
    if (
        not get_skill_evolution_enabled(ctx.config)
        or ctx.role == "leader"
        or not ctx.team_skills_dir
    ):
        return []

    try:
        inp = MemberSkillEvolutionInput.resolve(params, ctx)
        if not inp.member_skills_dir or not inp.global_skills_dir or not inp.member_name:
            logger.warning(
                "[swarm.member_skill_evolution] skipped: member/global Skill scope is incomplete"
            )
            return []
        Path(inp.member_skills_dir).mkdir(parents=True, exist_ok=True)
        llm_model, actual_model_name = _build_evolution_llm_from(
            inp.evolution_model_config
        )
        review_runtime = EvolutionReviewRuntime()
        rail = SwarmMemberSkillEvolutionRail(
            [inp.member_skills_dir, inp.team_skills_dir, inp.global_skills_dir],
            llm=llm_model,
            model=actual_model_name,
            review_runtime=review_runtime,
            language=inp.language,
            signal_trigger=True,
            auto_save=True,
            review_trigger=False,
            disabled_skills=load_execution_disabled_skills(),
        )
        has_team_trajectory_sink = inp.trajectory_registry is not None and bool(inp.team_id)
        if has_team_trajectory_sink:
            rail.set_trajectory_sink(
                inp.trajectory_registry,
                team_id=inp.team_id,
                member_role="teammate",
            )
        rail.bind_swarm_context(
            channel=inp.channel,
            session_id=inp.session_id,
            team_ws_root=ctx.team_ws_root,
            team_skills_dir=inp.team_skills_dir,
            team_id=inp.team_id,
            config=ctx.config,
            trajectory_registry=inp.trajectory_registry,
            member_name=inp.member_name,
            member_skills_dir=inp.member_skills_dir,
            global_skills_dir=inp.global_skills_dir,
            language=inp.language,
        )
        logger.info(
            "[swarm.member_skill_evolution] built: model=%s, auto_save=%s, "
            "team_trajectory_sink=%s",
            actual_model_name,
            True,
            has_team_trajectory_sink,
        )
        return [rail]
    except Exception as exc:
        logger.warning(
            "[swarm.member_skill_evolution] build failed: %s", exc, exc_info=True
        )
        return []


__all__ = [
    "EVOLUTION_INTERRUPT",
    "TEAM_SKILL_EVOLUTION",
    "TEAM_SKILL_CREATE",
    "MEMBER_SKILL_EVOLUTION",
    "build_team_skill_evolution_rail",
    "build_team_skill_create_rail",
    "build_member_skill_evolution_rail",
    "ensure_member_skill_copy",
]
