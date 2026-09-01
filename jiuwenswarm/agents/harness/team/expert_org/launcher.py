# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Host ExpertTeamLauncher: base Spec + enrich + activate (+ stop rollback)."""

from __future__ import annotations

import logging
import uuid
from dataclasses import asdict, dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LaunchedExpertTeam:
    """Result of launching one expert Team (duck-typed for agent-core Protocol)."""

    team_id: str
    leader_id: str
    capabilities: tuple[str, ...] = ()
    agent_group_name: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["capabilities"] = list(self.capabilities)
        return data


def _leader_id_from_agent(agent: Any, team_id: str) -> str:
    backend = getattr(agent, "team_backend", None)
    for attr in ("leader_member_name", "member_name"):
        value = getattr(backend, attr, None) if backend is not None else None
        if isinstance(value, str) and value.strip():
            return value.strip()
    member_name = getattr(agent, "member_name", None)
    if isinstance(member_name, str) and member_name.strip():
        return member_name.strip()
    return f"leader-{team_id}"


def _capabilities_from_agent(agent: Any) -> tuple[str, ...]:
    spec = getattr(agent, "spec", None)
    metadata = getattr(spec, "metadata", None)
    if not isinstance(metadata, dict):
        return ()
    raw = metadata.get("capabilities", [])
    if not isinstance(raw, list):
        return ()
    names: list[str] = []
    seen: set[str] = set()
    for item in raw:
        name = str(item or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return tuple(names)


def _align_spec_storage(spec: Any, donor_db: Any) -> None:
    """Point spec.storage at the donor DB so build uses the same TeamDatabase instance."""
    config = getattr(donor_db, "config", None)
    if config is None:
        raise ValueError("donor TeamDatabase has no config")

    from openjiuwen.agent_teams.schema.blueprint import StorageSpec

    db_type = str(getattr(config, "db_type", "") or "sqlite").strip() or "sqlite"
    params: dict[str, Any] = {
        "connection_string": str(getattr(config, "connection_string", "") or ""),
    }
    for key in ("db_timeout", "db_enable_wal"):
        if hasattr(config, key):
            params[key] = getattr(config, key)

    spec.storage = StorageSpec(type=db_type, params=params)


def _team_build_labels(
    *,
    team_id: str,
    agent_group_name: str,
    instruction: str,
    display_name: str | None,
    spec: Any,
) -> tuple[str, str, str, str]:
    team_display = str(display_name or "").strip() or agent_group_name or team_id
    team_desc = instruction or team_display
    leader = getattr(spec, "leader", None)
    leader_display = str(getattr(leader, "display_name", "") or "").strip() or "leader"
    leader_desc = str(getattr(leader, "desc", "") or "").strip()
    return team_display, team_desc, leader_display, leader_desc


class JiuwenExpertTeamLauncher:
    """Build an independent Team from an AgentGroup package and activate it.

    Flow:
      validate package → resolve donor DB → allocate team_id → enriched Spec
      → activate → build_team on shared DB → verify DB refs → pause

    On failure after a team_id is allocated, ``stop`` is called for rollback.
    """

    def __init__(self, *, runtime_manager: Any | None = None) -> None:
        self._runtime_manager = runtime_manager

    def _get_runtime(self) -> Any:
        if self._runtime_manager is not None:
            return self._runtime_manager
        from openjiuwen.core.runner.runner import GLOBAL_RUNNER
        from jiuwenswarm.agents.harness.team.team_manager import (
            _runner_team_runtime_manager,
        )

        return _runner_team_runtime_manager(GLOBAL_RUNNER)

    async def _allocate_team_id(self) -> str:
        return f"org-expert-{uuid.uuid4().hex[:12]}"

    async def _resolve_donor_backend(
        self,
        *,
        session_id: str,
        team_id: str,
        share_db_from_team_id: str | None,
    ) -> Any | None:
        runtime = self._get_runtime()
        donor_id = str(share_db_from_team_id or "").strip()
        donor_backend = None
        if donor_id:
            entry = await runtime.pool.get(donor_id)
            if entry is not None:
                donor_backend = getattr(entry.agent, "team_backend", None)
        if donor_backend is None or getattr(donor_backend, "db", None) is None:
            teams_for_session = getattr(runtime.pool, "teams_for_session", None)
            if callable(teams_for_session):
                for entry in await teams_for_session(session_id):
                    if getattr(entry, "team_name", None) == team_id:
                        continue
                    candidate = getattr(entry.agent, "team_backend", None)
                    if candidate is not None and getattr(candidate, "db", None) is not None:
                        donor_backend = candidate
                        break
        return donor_backend

    async def _build_enriched_spec(
        self,
        *,
        team_id: str,
        session_id: str,
        agent_group_name: str,
        agent_group_package: Any,
        display_name: str | None,
        channel_id: str | None = None,
        shared_db: Any | None = None,
    ) -> Any:
        """Base Team Spec from config, then AgentGroup overlay via assembly."""
        from jiuwenswarm.agents.harness.team.team_manager import TeamManager
        from jiuwenswarm.agents.swarm.assembly import enrich_team_spec_for_swarm

        spec = TeamManager._load_team_spec(session_id)
        updates: dict[str, Any] = {"team_name": team_id, "lifecycle": "persistent"}
        metadata = dict(getattr(spec, "metadata", None) or {})
        metadata["agent_group_name"] = agent_group_name
        metadata["expert_team"] = True
        metadata["capabilities"] = list(agent_group_package.capabilities)
        if display_name:
            metadata["display_name"] = display_name
        updates["metadata"] = metadata
        spec = spec.model_copy(update=updates)

        enrich_team_spec_for_swarm(
            spec,
            session_id=session_id,
            mode="team",
            channel_id=channel_id,
            request_metadata={"mode": "team", "agent_group_name": agent_group_name},
            agent_group_name=agent_group_name,
            agent_group_package=agent_group_package,
        )
        if shared_db is not None:
            _align_spec_storage(spec, shared_db)
        return spec

    async def _materialize_expert_team_in_db(
        self,
        agent: Any,
        *,
        team_id: str,
        agent_group_name: str,
        instruction: str,
        display_name: str | None,
        spec: Any,
    ) -> None:
        backend = getattr(agent, "team_backend", None)
        if backend is None:
            raise ValueError(f"activate returned no team_backend for team: {team_id}")

        build_team = getattr(backend, "build_team", None)
        if not callable(build_team):
            raise ValueError(f"expert team backend has no build_team for team: {team_id}")

        team_display, team_desc, leader_display, leader_desc = _team_build_labels(
            team_id=team_id,
            agent_group_name=agent_group_name,
            instruction=instruction,
            display_name=display_name,
            spec=spec,
        )
        await build_team(
            display_name=team_display,
            desc=team_desc,
            leader_display_name=leader_display,
            leader_desc=leader_desc,
        )

    @staticmethod
    def _verify_shared_database(agent: Any, donor_backend: Any) -> None:
        backend = getattr(agent, "team_backend", None)
        if backend is None:
            raise ValueError("expert team has no team_backend")

        donor_db = getattr(donor_backend, "db", None)
        if donor_db is None:
            raise ValueError("donor team has no TeamDatabase")

        if backend.db is not donor_db:
            raise ValueError(
                "expert team must use the owner's shared TeamDatabase instance"
            )

        task_manager = getattr(backend, "task_manager", None)
        if task_manager is not None and getattr(task_manager, "db", None) is not donor_db:
            raise ValueError(
                "expert team task_manager must use the shared TeamDatabase instance"
            )

        message_manager = getattr(backend, "message_manager", None)
        if message_manager is not None and getattr(message_manager, "db", None) is not donor_db:
            raise ValueError(
                "expert team message_manager must use the shared TeamDatabase instance"
            )

    async def launch(
        self,
        *,
        organization_id: str,
        agent_group_name: str,
        session_id: str,
        display_name: str | None = None,
        channel_id: str | None = None,
        share_db_from_team_id: str | None = None,
    ) -> LaunchedExpertTeam:
        group_name = str(agent_group_name or "").strip()
        if not group_name:
            raise ValueError("agent_group_name is required")
        if not str(organization_id or "").strip():
            raise ValueError("organization_id is required")
        if not str(session_id or "").strip():
            raise ValueError("session_id is required")

        from jiuwenswarm.agents.swarm.agent_group import (
            load_agent_group_package_bundle,
        )
        from jiuwenswarm.server.runtime.extension_package_manager import (
            resolve_agent_group_dir,
        )

        agent_group_package = load_agent_group_package_bundle(
            resolve_agent_group_dir(group_name)
        )
        team_id = await self._allocate_team_id()
        runtime = self._get_runtime()
        donor_backend = await self._resolve_donor_backend(
            session_id=session_id,
            team_id=team_id,
            share_db_from_team_id=share_db_from_team_id,
        )
        shared_db = getattr(donor_backend, "db", None) if donor_backend is not None else None

        activation_attempted = False
        try:
            spec = await self._build_enriched_spec(
                team_id=team_id,
                session_id=session_id,
                agent_group_name=group_name,
                agent_group_package=agent_group_package,
                display_name=display_name,
                channel_id=channel_id,
                shared_db=shared_db,
            )
            activation_attempted = True
            activation = await runtime.activate(spec, session_id)
            agent = getattr(activation, "agent", None)
            if agent is None:
                raise ValueError(f"activate returned no agent for team: {team_id}")

            if donor_backend is not None:
                await self._materialize_expert_team_in_db(
                    agent,
                    team_id=team_id,
                    agent_group_name=group_name,
                    instruction=agent_group_package.instruction,
                    display_name=display_name,
                    spec=spec,
                )
                self._verify_shared_database(agent, donor_backend)

            # Design: expert Team should sit PAUSED without an idle LLM warm-up.
            pause = getattr(runtime, "pause", None)
            if callable(pause):
                try:
                    await pause(team_name=team_id, session_id=session_id)
                except Exception as exc:  # pragma: no cover - best effort
                    logger.warning(
                        "[ExpertTeamLauncher] pause after activate failed team=%s: %s",
                        team_id,
                        exc,
                    )

            return LaunchedExpertTeam(
                team_id=team_id,
                leader_id=_leader_id_from_agent(agent, team_id),
                capabilities=_capabilities_from_agent(agent),
                agent_group_name=group_name,
            )
        except Exception:
            if activation_attempted:
                await self.stop(team_id=team_id, session_id=session_id)
            raise

    async def stop(self, *, team_id: str, session_id: str) -> None:
        name = str(team_id or "").strip()
        if not name:
            return
        runtime = self._get_runtime()
        stop_team = getattr(runtime, "stop_team", None)
        if not callable(stop_team):
            return
        try:
            await stop_team(team_name=name, session_id=session_id)
        except Exception as exc:
            logger.warning(
                "[ExpertTeamLauncher] stop_team failed team=%s session=%s: %s",
                name,
                session_id,
                exc,
            )


__all__ = [
    "JiuwenExpertTeamLauncher",
    "LaunchedExpertTeam",
]
