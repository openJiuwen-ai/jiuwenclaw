# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""SubagentSkillUseRail: spawn/fork subagent 专用的 SkillUseRail。

保留 active-skill body 的 lift / pin 生命周期：
  - after_tool_call → record_active_skill_body 把 body 写到子代理 session state
  - 原 ToolMessage 内容替换为短小的 [SKILL LOADED] stub
  - 后续每轮 get_window 时由 context_engine 的 append_active_skill_pins_to_window
    把 body 抬到 system 区作为 [ACTIVE SKILL BODY] 注入

禁用 before_model_call 注入的 "# 技能 / Available skills" prompt 区块：
  父代理已经在 task prompt 里指明子代理要用哪个 skill，
  再渲染一次完整可用技能列表是浪费 token，故在 subagent 这条链路上跳过。

不调用基类 before_invoke 中的 _prepare_skills / _fetch_evolution_texts：
  spawn/fork 从父代理继承的 skill_tool 与父级 SkillUseRail 绑定的 get_skills_meta
  仍是同一份，无需在子代理侧再扫盘构建 self.skills。若未来改为在子代理上
  独立注册 skill_tool 且回调指向本子类，则需要恢复准备逻辑。
"""

from __future__ import annotations

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts.builder import PromptSection
from openjiuwen.harness.prompts.sections import SectionName
from openjiuwen.harness.rails.skill_use_rail import SkillUseRail

# Subagent 专用的轻量 skill 指引：不列出可用技能（父代理已在任务里指明），
# 但需要告知 skill_tool / skill_complete 的存在和调用方式，否则当父任务里
# 出现 SKILL.md 绝对路径时，subagent 会退化成直接 read_file。
_SUBAGENT_SKILL_PROMPT_CN = (
    "# 技能\n\n"
    "需要使用某个技能时，先调用 skill_tool(skill_name=..., relative_file_path=\"SKILL.md\") "
    "加载正文并遵从其内容；完成该技能全部步骤、不再需要回看时，立即调用 "
    "skill_complete(skill_name=...) 释放上下文；之后若再需要可重新调用 skill_tool 加载。\n\n"
)

_SUBAGENT_SKILL_PROMPT_EN = (
    "# Skills\n\n"
    "When you need a skill, first call skill_tool(skill_name=..., relative_file_path=\"SKILL.md\") "
    "to load its body and follow it; after finishing all steps and no longer needing the body, "
    "immediately call skill_complete(skill_name=...) to release context; "
    "re-call skill_tool later if you need it again.\n\n"
)

_SUBAGENT_SKILL_PROMPT = {
    "cn": _SUBAGENT_SKILL_PROMPT_CN,
    "en": _SUBAGENT_SKILL_PROMPT_EN,
}


class SubagentSkillUseRail(SkillUseRail):
    """SkillUseRail 子类：仅保留 lift/pin 行为，不渲染 skill 列表 prompt。"""

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        # 不 await _prepare_skills() / _fetch_evolution_texts()：见模块说明。
        self._consume_pending_active_skill_hints(ctx)

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        if self.system_prompt_builder is None:
            return
        language = self.system_prompt_builder.language
        content = _SUBAGENT_SKILL_PROMPT.get(language, _SUBAGENT_SKILL_PROMPT_CN)
        self.system_prompt_builder.add_section(
            PromptSection(
                name=SectionName.SKILLS,
                content={language: content},
                priority=40,
            )
        )
