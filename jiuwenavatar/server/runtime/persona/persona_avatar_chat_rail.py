# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""PersonaAvatarChatRail — Web 主对话页选择数字分身时的 per-request 注入."""

from __future__ import annotations

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts import PromptSection
from openjiuwen.harness.rails.base import DeepAgentRail

from jiuwenavatar.server.runtime.persona.chat_context import AvatarChatContext

_SECTION_NAME = "persona_avatar_chat"
_PRIORITY = 112


class PersonaAvatarChatRail(DeepAgentRail):
    """主对话分身模式：注入 Persona 系统提示与技能约束."""

    priority = 88

    def __init__(self) -> None:
        super().__init__()
        self._context: AvatarChatContext | None = None
        self._injected = False

    def set_context(self, context: AvatarChatContext | None) -> None:
        self._context = context

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        builder = getattr(
            getattr(self, "_deep_agent", None) or ctx.agent,
            "system_prompt_builder",
            None,
        )
        if builder is None:
            return

        if self._injected:
            builder.remove_section(_SECTION_NAME)
            self._injected = False

        if self._context is None:
            return

        language = getattr(builder, "language", "cn") or "cn"
        content = _build_persona_avatar_prompt(self._context, language)
        builder.add_section(
            PromptSection(
                name=_SECTION_NAME,
                content={language: content},
                priority=_PRIORITY,
            )
        )
        self._injected = True


def _coding_engine_section(context: AvatarChatContext, language: str) -> str:
    """根据分身的 coding_engine 注入对应后端的编排提示（引擎无关）."""
    if not context.coding_engine:
        return ""
    from jiuwenavatar.server.runtime.coding import get_coding_engine

    engine = get_coding_engine(context.coding_engine)
    return engine.prompt_section(skills_root=context.skills_root, language=language)


def _build_persona_avatar_prompt(context: AvatarChatContext, language: str) -> str:
    skill_list = ", ".join(context.skills)
    cc_section = _coding_engine_section(context, language)
    if language == "cn":
        identity = (
            f"你当前正在以数字分身 **{context.avatar_name}**（模板：{context.persona_display_name}）与用户对话。"
        )
        constraints = (
            "【分身技能约束 — 必须遵守】\n"
            f"- 仅允许加载和使用以下 Skill：{skill_list}\n"
            "- 上述 Skill 已预装到本地，直接用 skill_tool 加载，不要用 search_skill / install_skill 去 SkillNet 搜索或安装\n"
            "- 禁止调用 browser_agent / task_tool 委派浏览器子任务\n"
            "- 禁止用 fetch_webpage 爬取 GitCode/GitHub PR 页面代替专用技能\n"
            "- 代码检视、Issue/PR 操作请遵循当前编码后端约束：若下方声明外部 CLI 后端，检视分析必须先通过 coding_task 委派；Leader 不要预读 diff 或自行分析，后续提交评论再使用 gitcode-repo 等已绑定 Skill\n"
            "- 不要以通用网页助手方式处理本任务，按 AIDLC 分身流水线执行"
        )
        body = context.system_prompt.strip() if context.system_prompt else ""
        parts = ["---", "# 数字分身对话模式", "", identity, "", constraints]
        if cc_section:
            parts.extend(["", cc_section])
        if body:
            parts.extend(["", "## 分身职责", "", body])
        return "\n".join(parts)

    identity = (
        f"You are chatting as avatar **{context.avatar_name}** "
        f"(persona: {context.persona_display_name})."
    )
    constraints = (
        "[Avatar skill constraints — mandatory]\n"
        f"- Only use these skills: {skill_list}\n"
        "- These skills are pre-installed locally; use skill_tool to load them. "
        "Do NOT use search_skill / install_skill on SkillNet\n"
        "- Do NOT delegate to browser_agent via task_tool\n"
        "- Do NOT use fetch_webpage to scrape PR pages; use dedicated skills instead\n"
        "- Follow the current coding backend constraints: if an external CLI backend is declared below, "
        "the Leader must delegate review analysis through coding_task before reading/analyzing the diff; "
        "use gitcode-repo only for follow-up comment submission / PR operations\n"
        "- Follow the bound AIDLC avatar workflow, not generic web browsing"
    )
    body = context.system_prompt.strip() if context.system_prompt else ""
    parts = ["---", "# Avatar chat mode", "", identity, "", constraints]
    if cc_section:
        parts.extend(["", cc_section])
    if body:
        parts.extend(["", "## Responsibilities", "", body])
    return "\n".join(parts)
