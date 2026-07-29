# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Inject team skill storage policy into member prompts."""

from __future__ import annotations

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts import PromptSection
from openjiuwen.harness.prompts.prompt_attachment_manager import PromptAttachmentKind
from openjiuwen.harness.rails.base import DeepAgentRail

from jiuwenswarm.common.utils import logger


class TeamSkillStoragePolicyRail(DeepAgentRail):
    """Tell team members where skill authoring outputs must be stored.

    The policy text is identical for every member of a team and stays in the
    system prompt, so the whole team shares one cacheable prompt prefix. The
    member workspace path is the one per-member value here, so it is delivered
    as a prompt attachment instead of being inlined into that prefix.

    Both lanes carry bilingual content: the system prompt section renders
    through the builder's language, the attachment through ``language`` passed
    at construction time.
    """

    priority = 5
    SECTION_NAME = "team_skill_storage_policy"
    SECTION_PRIORITY = 39
    MEMBER_WORKSPACE_SECTION_NAME = "team_skill_storage_member_workspace"
    ATTACHMENT_SOURCE = "jiuwenswarm.team_skill_storage_policy_rail"

    def __init__(
        self,
        *,
        global_skills_dir: str,
        team_workspace_root: str | None = None,
        team_skills_dir: str | None = None,
        member_workspace_root: str | None = None,
        language: str = "cn",
    ) -> None:
        super().__init__()
        self.system_prompt_builder = None
        self.attachment_manager = None
        self._global_skills_dir = global_skills_dir
        self._team_workspace_root = team_workspace_root
        self._team_skills_dir = team_skills_dir
        self._member_workspace_root = member_workspace_root
        self._language = language

    def init(self, agent) -> None:
        """Capture the prompt builder and attachment manager owned by the member."""
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)
        self.attachment_manager = getattr(agent, "prompt_attachment_manager", None)

    def uninit(self, agent) -> None:
        """Remove the injected policy section."""
        _ = agent
        if self.system_prompt_builder is not None:
            self.system_prompt_builder.remove_section(self.SECTION_NAME)
        self.system_prompt_builder = None
        self.attachment_manager = None

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Inject the storage policy before each model call."""
        if self.system_prompt_builder is None:
            return

        self.system_prompt_builder.add_section(
            PromptSection(
                name=self.SECTION_NAME,
                content={
                    "cn": self._build_cn_content(),
                    "en": self._build_en_content(),
                },
                priority=self.SECTION_PRIORITY,
            )
        )
        await self._sync_member_workspace_attachment(ctx)

    def _build_cn_content(self) -> str:
        """Build the Chinese prompt section."""
        forbidden_paths = self._format_forbidden_paths("cn")
        return (
            "# Team Skill 存储规则\n\n"
            "在 team 模式中，凡是创建、转换、修改或优化 Skill / Swarm Skill / Team Skill，"
            "最终 skill 源目录必须写入全局公共技能目录。\n\n"
            f"- 全局公共技能目录：`{self._global_skills_dir}`\n"
            f"- 新 skill 的入口文件必须是：`{self._global_skills_dir}/<skill-name>/SKILL.md`\n"
            "- `assets/`、`scripts/`、`references/`、模板、示例、验证脚本等配套资源，"
            "必须放在同一个 `<skill-name>/` 目录下。\n"
            "- 不要把 skill 源文件创建到成员 skill 目录、team skill 目录、当前目录或临时目录。\n"
            f"{forbidden_paths}"
            "- 成员 skill 目录和 team skill 目录只用于读取当前可见技能，不作为新 skill 的创建目标。"
        )

    def _build_en_content(self) -> str:
        """Build the English prompt section."""
        forbidden_paths = self._format_forbidden_paths("en")
        return (
            "# Team Skill Storage Policy\n\n"
            "In team mode, whenever you create, convert, modify, or optimize a Skill, "
            "Swarm Skill, or Team Skill, the final skill source directory must be "
            "written under the global shared skills directory.\n\n"
            f"- Global shared skills directory: `{self._global_skills_dir}`\n"
            f"- A new skill entry file must be: `{self._global_skills_dir}/<skill-name>/SKILL.md`\n"
            "- Put related `assets/`, `scripts/`, `references/`, templates, examples, "
            "and validation scripts under the same `<skill-name>/` directory.\n"
            "- Do not create skill source files in member skill directories, team skill "
            "directories, the current directory, or temporary directories.\n"
            f"{forbidden_paths}"
            "- Member skill directories and team skill directories are only for reading "
            "currently visible skills, not for creating new skill sources."
        )

    def _format_forbidden_paths(self, language: str) -> str:
        """Format the team-level non-source workspace paths for the prompt.

        Only team-level paths belong here: they are identical for every member,
        so they keep the prompt prefix shared. The member workspace path is
        per-member and ships as an attachment instead.
        """
        lines: list[str] = []
        if self._team_workspace_root:
            label = "team 共享工作区" if language == "cn" else "Team shared workspace"
            lines.append(f"- {label}：`{self._team_workspace_root}`\n")
        if self._team_skills_dir:
            label = "team skills 共享视图" if language == "cn" else "Team skills shared view"
            lines.append(f"- {label}：`{self._team_skills_dir}`\n")
        return "".join(lines)

    def _build_member_workspace_section(self) -> PromptSection:
        """Build the per-member workspace section, rendered by language."""
        cn_content = (
            "# Team Skill 存储规则 — 成员工作区\n\n"
            f"- 成员工作区：`{self._member_workspace_root}`\n"
            "- 该目录不是 skill 源目录，不要把新 skill 创建到这里。"
        )
        en_content = (
            "# Team Skill Storage Policy — Member Workspace\n\n"
            f"- Member workspace: `{self._member_workspace_root}`\n"
            "- This is not a skill source directory; do not create new skills here."
        )
        return PromptSection(
            name=self.MEMBER_WORKSPACE_SECTION_NAME,
            content={"cn": cn_content, "en": en_content},
            priority=self.SECTION_PRIORITY,
        )

    async def _sync_member_workspace_attachment(self, ctx: AgentCallbackContext) -> None:
        """Upsert the member workspace path as a prompt attachment."""
        if self.attachment_manager is None or not self._member_workspace_root:
            return
        try:
            writer = self.attachment_manager.bind_context(ctx)
            await writer.add_from_prompt_section(
                prompt_section=self._build_member_workspace_section(),
                kind=PromptAttachmentKind.WORKSPACE_DELTA,
                source=self.ATTACHMENT_SOURCE,
                language=self._language,
                content_kind="text/markdown",
            )
        except ValueError as exc:
            logger.warning(
                "[TeamSkillStoragePolicyRail] skip member workspace attachment: %s",
                exc,
            )


__all__ = ["TeamSkillStoragePolicyRail"]
