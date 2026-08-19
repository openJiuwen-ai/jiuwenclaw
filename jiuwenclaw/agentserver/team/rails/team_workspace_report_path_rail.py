# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Prompt rail for reporting real team workspace artifact paths."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts import PromptSection
from openjiuwen.harness.rails.base import DeepAgentRail


class TeamWorkspaceReportPathRail(DeepAgentRail):
    """Inject guidance for shared-workspace mount vs absolute paths.

    Mounted for every team member so write/read paths stay consistent.
    ``send_file_to_user`` delivery guidance is Leader-only.
    """

    priority = 5

    def __init__(
        self,
        *,
        root_dir: str,
        team_id: str | None = None,
        language: str = "cn",
        enable_send_file_guidance: bool = False,
        send_file_rail: Any | None = None,
    ) -> None:
        super().__init__()
        self.system_prompt_builder = None
        self._root_dir = str(Path(root_dir))
        self._team_id = team_id or ""
        self._language = language
        self._enable_send_file_guidance = bool(enable_send_file_guidance)
        self._send_file_rail = send_file_rail

    def init(self, agent) -> None:
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)

    def uninit(self, agent) -> None:
        if self.system_prompt_builder is not None:
            self.system_prompt_builder.remove_section("team_workspace_report_paths")
        self.system_prompt_builder = None

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        _ = ctx
        if self.system_prompt_builder is None:
            return

        mount = ".team/"
        sample_rel = f"{mount}prd-review-memo.md"
        sample_abs = str(Path(self._root_dir) / "prd-review-memo.md")
        wrong_abs = str(Path(self._root_dir) / ".team" / "prd-review-memo.md")
        content = (
            "# Team Workspace Artifact Paths\n\n"
            f"- Team workspace absolute root (shared disk root): `{self._root_dir}`\n"
            f"- Internal mount path (for tool read/write only): `{mount}`\n"
            "- Flat mount: `{member_cwd}/.team` points at the shared root above. "
            f"Write shared files as `{mount}...` (example `{sample_rel}`).\n"
            f"- Do NOT invent nested mounts like `.team/<team_id>/...` — that layout is obsolete.\n"
            "- Do NOT invent private roots outside the mount; topic subfolders "
            f"belong under the mount (e.g. `{mount}debate/position.md`).\n"
            "- Absolute path mapping: `{mount}foo.md` on disk is "
            f"`{{shared_root}}/foo.md` (example `{sample_abs}`).\n"
            f"- NEVER join the absolute root with the mount "
            f"(wrong: `{wrong_abs}`).\n"
            "- Use the internal mount path only for tool read/write operations; "
            "when telling teammates where a file is, prefer the mount-relative path "
            f"(`{mount}...`) or the real absolute path under the shared root.\n"
            "- Member intermediate files stay in the team workspace for the team to "
            "find; do not treat them as user-facing deliveries.\n"
        )
        if self._enable_send_file_guidance and bool(
            self._send_file_rail is not None
            and getattr(self._send_file_rail, "_registered", False)
        ):
            content += (
                "- For every **Leader final** deliverable file, call `send_file_to_user` with "
                "its real absolute filesystem path under the shared root "
                f"(example `{sample_abs}`) before marking the task complete.\n"
                "- A file name or path in `send_message`, the final response, or a "
                "task-completion summary does not deliver the file and must not replace "
                "`send_file_to_user`.\n"
                "- After `send_file_to_user` succeeds, report that same absolute path "
                f"under `{self._root_dir}`, not a `.team/...` mount path.\n"
            )
        self.system_prompt_builder.add_section(
            PromptSection(
                name="team_workspace_report_paths",
                content={"cn": content, "en": content},
                priority=67,
            )
        )
