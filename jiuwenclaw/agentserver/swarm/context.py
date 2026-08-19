# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Runtime build context for swarm provider-based team assembly."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openjiuwen.agent_teams.schema.build_context import BuildContext


@dataclass
class SwarmBuildContext(BuildContext):
    """BuildContext carrying jiuwenclaw runtime handles for swarm providers."""

    session_id: str = ""
    request_id: str | None = None
    channel_id: str | None = None
    channel: str = "default"
    request_metadata: dict[str, Any] | None = None
    mode: str = "team"
    project_dir: str | None = None
    team_id: str = ""
    # Bound ``modes.team`` template key (chat.send ``team_name``), before
    # session-scoped rename. Prefer this for roster / tip catalog lookups.
    team_template_id: str = ""
    # role / member_name → skills list hydrated from tip ENABLED_SKILLS (or yaml).
    agent_skills_by_key: dict[str, list[str]] | None = None
    team_ws_root: str | None = None
    team_skills_dir: str | None = None
    leader_skills_dir: str | None = None
    global_skills_dir: str | None = None
    trajectory_registry: Any = None
    config: dict[str, Any] | None = None

    def to_seed(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "request_id": self.request_id,
            "channel_id": self.channel_id,
            "channel": self.channel,
            "request_metadata": self.request_metadata,
            "mode": self.mode,
            "project_dir": self.project_dir,
            "team_id": self.team_id,
            "team_template_id": self.team_template_id,
            "agent_skills_by_key": self.agent_skills_by_key,
            "team_ws_root": self.team_ws_root,
            "team_skills_dir": self.team_skills_dir,
            "leader_skills_dir": self.leader_skills_dir,
            "global_skills_dir": self.global_skills_dir,
            # BuildContext.language — must survive spawn/deserialize so rails
            # and tools keep cn|en (defaulting the seed would pin everyone to cn).
            "language": self.language,
        }

    @classmethod
    def from_seed(
        cls,
        seed: dict[str, Any],
        *,
        config: dict[str, Any] | None,
        trajectory_registry: Any,
    ) -> SwarmBuildContext:
        skills_raw = seed.get("agent_skills_by_key")
        skills_map = skills_raw if isinstance(skills_raw, dict) else None
        language = str(seed.get("language") or "").strip() or "cn"
        if language not in {"cn", "en"}:
            language = "cn"
        return cls(
            session_id=seed.get("session_id", ""),
            request_id=seed.get("request_id"),
            channel_id=seed.get("channel_id"),
            channel=seed.get("channel") or "default",
            request_metadata=seed.get("request_metadata"),
            mode=seed.get("mode") or "team",
            project_dir=seed.get("project_dir"),
            team_id=seed.get("team_id", ""),
            team_template_id=seed.get("team_template_id", ""),
            agent_skills_by_key=skills_map,
            team_ws_root=seed.get("team_ws_root"),
            team_skills_dir=seed.get("team_skills_dir"),
            leader_skills_dir=seed.get("leader_skills_dir"),
            global_skills_dir=seed.get("global_skills_dir"),
            trajectory_registry=trajectory_registry,
            config=config,
            language=language,
        )


__all__ = ["SwarmBuildContext"]
