# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Prompt rail for reporting real team workspace artifact paths."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts import PromptSection
from openjiuwen.harness.rails.base import DeepAgentRail

logger = logging.getLogger(__name__)


class TeamWorkspaceReportPathRail(DeepAgentRail):
    """Inject guidance for reporting real shared-workspace artifact paths."""

    priority = 5

    def __init__(
        self,
        *,
        root_dir: str,
        team_id: str | None = None,
        language: str = "cn",
    ) -> None:
        super().__init__()
        self.system_prompt_builder = None
        self._root_dir = str(Path(root_dir))
        self._team_id = team_id or ""
        self._language = language
        self._swarm_context: Any | None = None

    def bind_swarm_context(self, context: Any) -> None:
        """Capture team build context for evolution hot-reload registration.

        This rail is present even when skill evolution is disabled, so its
        lifecycle provides the manager with a mount context that can later
        rebuild the role-specific evolution rails. It does not add any
        evolution capability or prompt content by itself.
        """
        self._swarm_context = context

    def init(self, agent) -> None:
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)
        self._register_swarm_context(agent)

    def uninit(self, agent) -> None:
        if self.system_prompt_builder is not None:
            self.system_prompt_builder.remove_section("team_workspace_report_paths")
        self.system_prompt_builder = None

    def _register_swarm_context(self, agent: Any) -> None:
        """Register a team mount context when this rail came from swarm assembly."""
        context = self._swarm_context
        if context is None:
            return

        session_id = str(getattr(context, "session_id", "") or "").strip()
        channel = str(getattr(context, "channel", "default") or "default").strip()
        team_id = str(getattr(context, "team_id", "") or "").strip()
        role = str(getattr(context, "role", "") or "").strip()
        if not session_id or not team_id or role not in {"leader", "teammate"}:
            return

        try:
            from jiuwenswarm.agents.harness.team.team_manager import (
                _make_team_rail_mount_context,
                get_team_manager,
            )

            config = getattr(context, "config", None)
            mount_context = _make_team_rail_mount_context(
                agent=agent,
                role=role,
                channel=channel,
                language=str(getattr(context, "language", None) or self._language),
                member_name=getattr(context, "member_name", None),
                root_dir=getattr(context, "team_ws_root", None) or self._root_dir,
                skills_dir=getattr(context, "team_skills_dir", None),
                team_id=team_id,
                config=config,
                trajectory_registry=getattr(context, "trajectory_registry", None),
            )
            team_manager = get_team_manager(channel)
            if role == "leader":
                if team_manager.get_team_rail_context(session_id) is None:
                    team_manager.register_team_rail_context(session_id, mount_context)
            else:
                register_member_context = getattr(
                    team_manager,
                    "register_team_member_rail_context",
                    None,
                )
                if callable(register_member_context):
                    register_member_context(session_id, mount_context)
        except Exception as exc:
            logger.warning(
                "[TeamWorkspaceReportPathRail] team mount context registration failed: %s",
                exc,
            )

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        _ = ctx
        if self.system_prompt_builder is None:
            return

        mount = f".team/{self._team_id}/" if self._team_id else ".team/"
        sample = str(Path(self._root_dir) / "prd-review-memo.md")
        content = (
            "# Team Workspace Artifact Paths\n\n"
            f"- Team workspace absolute root: `{self._root_dir}`\n"
            f"- Internal mount path: `{mount}`\n"
            "- Use the internal mount path only for tool read/write operations.\n"
            "- For every final deliverable file, call `send_file_to_user` with its real absolute filesystem path "
            "before marking the task complete or sending the completion message.\n"
            "- A file name or path in `send_message`, the final response, or a task-completion summary does not "
            "deliver the file and must not replace `send_file_to_user`.\n"
            "- After `send_file_to_user` succeeds, report the real absolute filesystem path under the team "
            "workspace absolute root, not the `.team/...` mount path.\n"
            "- If a generated artifact path contains `.team/<team>/team-workspace/`, remove that mount prefix and "
            "join the remaining file name under the team workspace absolute root before sending and reporting it.\n"
            f"- Example absolute path to pass to `send_file_to_user`: `{sample}`\n"
        )
        self.system_prompt_builder.add_section(
            PromptSection(
                name="team_workspace_report_paths",
                content={"cn": content, "en": content},
                priority=67,
            )
        )
