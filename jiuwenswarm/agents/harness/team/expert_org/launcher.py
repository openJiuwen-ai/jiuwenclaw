# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Host ExpertTeamLauncher: base Spec + enrich + activate (+ stop rollback)."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import asdict, dataclass
from typing import Any

logger = logging.getLogger(__name__)

_SAFE_ID = re.compile(r"[^a-zA-Z0-9_-]+")


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


def _safe_id_part(value: str, *, fallback: str = "x") -> str:
    cleaned = _SAFE_ID.sub("-", str(value or "").strip()).strip("-_")
    return cleaned or fallback


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


class JiuwenExpertTeamLauncher:
    """Build an independent Team from an AgentGroup package and activate it.

    Flow:
      validate package → allocate team_id → base Spec → enrich_team_spec_for_swarm
      → TeamRuntimeManager.activate → best-effort pause → LaunchedExpertTeam

    On failure after a team_id is allocated, ``stop`` is called for rollback.
    """

    def __init__(
        self,
        *,
        runtime_manager: Any | None = None,
        sequence_start: int = 1,
    ) -> None:
        self._runtime_manager = runtime_manager
        self._seq = max(1, int(sequence_start))
        self._seq_lock = asyncio.Lock()

    def _get_runtime(self) -> Any:
        if self._runtime_manager is not None:
            return self._runtime_manager
        from openjiuwen.core.runner.runner import GLOBAL_RUNNER
        from jiuwenswarm.agents.harness.team.team_manager import (
            _runner_team_runtime_manager,
        )

        return _runner_team_runtime_manager(GLOBAL_RUNNER)

    @staticmethod
    def _validate_agent_group(agent_group_name: str) -> None:
        from jiuwenswarm.server.runtime.extension_package_manager import (
            resolve_agent_group_dir,
        )

        resolve_agent_group_dir(agent_group_name)

    async def _allocate_team_id(
        self, *, organization_id: str, agent_group_name: str
    ) -> str:
        async with self._seq_lock:
            seq = self._seq
            self._seq += 1
        org = _safe_id_part(organization_id, fallback="org")
        group = _safe_id_part(agent_group_name, fallback="group")
        return f"org-{org}-{group}-{seq}"

    async def _build_enriched_spec(
        self,
        *,
        team_id: str,
        session_id: str,
        agent_group_name: str,
        display_name: str | None,
        channel_id: str | None = None,
    ) -> Any:
        """Base Team Spec from config, then AgentGroup overlay via assembly."""
        from jiuwenswarm.agents.harness.team.team_manager import TeamManager
        from jiuwenswarm.agents.swarm.assembly import enrich_team_spec_for_swarm

        spec = TeamManager._load_team_spec(session_id)
        updates: dict[str, Any] = {"team_name": team_id, "lifecycle": "persistent"}
        metadata = dict(getattr(spec, "metadata", None) or {})
        metadata["agent_group_name"] = agent_group_name
        metadata["expert_team"] = True
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
        )
        return spec

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

        self._validate_agent_group(group_name)
        team_id = await self._allocate_team_id(
            organization_id=organization_id,
            agent_group_name=group_name,
        )
        runtime = self._get_runtime()
        activated = False
        try:
            spec = await self._build_enriched_spec(
                team_id=team_id,
                session_id=session_id,
                agent_group_name=group_name,
                display_name=display_name,
                channel_id=channel_id,
            )
            activation = await runtime.activate(spec, session_id)
            activated = True
            agent = getattr(activation, "agent", None)
            if agent is None:
                raise ValueError(f"activate returned no agent for team: {team_id}")

            await self._share_team_database(
                agent=agent,
                session_id=session_id,
                team_id=team_id,
                share_db_from_team_id=share_db_from_team_id,
            )

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
            if activated or team_id:
                await self.stop(team_id=team_id, session_id=session_id)
            raise

    async def _share_team_database(
        self,
        *,
        agent: Any,
        session_id: str,
        team_id: str,
        share_db_from_team_id: str | None,
    ) -> None:
        """Reuse an existing session TeamDatabase so org invite can bind the team."""
        backend = getattr(agent, "team_backend", None)
        if backend is None:
            return
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
                        donor_id = entry.team_name
                        break
        if donor_backend is None or getattr(donor_backend, "db", None) is None:
            logger.warning(
                "[ExpertTeamLauncher] no shared TeamDatabase found for team=%s session=%s",
                team_id,
                session_id,
            )
            return
        backend.db = donor_backend.db
        logger.info(
            "[ExpertTeamLauncher] team %s adopted shared db from %s",
            team_id,
            donor_id or "session-peer",
        )

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
