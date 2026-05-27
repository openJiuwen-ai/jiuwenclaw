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

import json
from typing import Any

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts.builder import PromptSection
from openjiuwen.harness.prompts.sections import SectionName
from openjiuwen.harness.rails.skill_use_rail import SkillUseRail


def _extract_skill_complete_arg(tool_call: Any, key: str) -> str:
    """Read a string arg from skill_complete tool_call.arguments (dict or JSON string)."""
    args = getattr(tool_call, "arguments", None)
    val: Any = ""
    if isinstance(args, dict):
        val = args.get(key, "")
    elif isinstance(args, str):
        try:
            parsed = json.loads(args)
        except json.JSONDecodeError:
            return ""
        if isinstance(parsed, dict):
            val = parsed.get(key, "")
    return str(val).strip() if val else ""

# Subagent 专用的轻量 skill 指引：不列出可用技能（父代理已在任务里指明），
# 但需要告知 skill_tool / skill_complete 的存在和调用方式，否则当父任务里
# 出现 SKILL.md 绝对路径时，subagent 会退化成直接 read_file。
_SUBAGENT_SKILL_PROMPT_CN = (
    "# 技能\n\n"
    "需要使用某个技能时，先调用 skill_tool(skill_name=..., relative_file_path=\"SKILL.md\") "
    "加载正文并遵从其内容；完成该技能全部步骤、不再需要回看时，立即调用 "
    "skill_complete(skill_name=...) 释放上下文；之后若再需要可重新调用 skill_tool 加载。\n\n"
    "**强制规则**：\n"
    "- 任务中只要出现技能名（如「使用 X 技能」「执行 X 技能」）或给出指向某个 `SKILL.md` 的绝对路径，"
    "**必须**用 `skill_tool(skill_name=\"<skill_name>\")` 加载，**禁止**用 `read_file` 直接读取该 SKILL.md。\n"
    "- 给定路径 `.../<Y>/<X>/SKILL.md`，**`<skill_name>` 取 `<X>`**（SKILL.md 的直接父目录名），不取更上层的 `<Y>`。"
    "即便 `<Y>` 本身也注册为同名 skill，**本次任务的指令集仍是 `<X>` 里的 SKILL.md**，`<Y>` 只是 umbrella 不含本次具体规则。\n"
    "- 即使父任务文案写「必读」「强制读取」「按路径读取 SKILL.md」，依然走 `skill_tool`：技能正文会以 "
    "`[ACTIVE SKILL BODY]` 形式注入到后续 system 区，比 read_file 输出更省 token、更可控。\n"
    "- 该规则只适用于 `SKILL.md` 路径；非技能的普通参考文件（如 `styles/*.md`、大纲、研究报告等）仍按原指令用 `read_file` 读取。\n\n"
)

_SUBAGENT_SKILL_PROMPT_EN = (
    "# Skills\n\n"
    "When you need a skill, first call skill_tool(skill_name=..., relative_file_path=\"SKILL.md\") "
    "to load its body and follow it; after finishing all steps and no longer needing the body, "
    "immediately call skill_complete(skill_name=...) to release context; "
    "re-call skill_tool later if you need it again.\n\n"
    "**Hard rule**:\n"
    "- Whenever the task names a skill (e.g. \"use skill X\", \"execute the X skill\") or gives an absolute path "
    "to a `SKILL.md`, you **must** load it via `skill_tool(skill_name=\"<skill_name>\")` "
    "and **must not** open that SKILL.md with `read_file`.\n"
    "- Given a path `.../<Y>/<X>/SKILL.md`, **`<skill_name>` is `<X>`** (the directory directly containing SKILL.md), "
    "**not** the outer `<Y>`. Even if `<Y>` itself is also registered as a same-named skill, "
    "**the instructions for the current task are in `<X>`'s SKILL.md**; `<Y>` is just an umbrella "
    "and does not contain the specific rules for this task.\n"
    "- This applies even when the parent task says \"must read\", \"force read\", or \"open SKILL.md by path\": "
    "the skill body is re-injected as `[ACTIVE SKILL BODY]` in subsequent system context, which is cheaper and "
    "more controllable than a raw read_file output.\n"
    "- The rule only applies to `SKILL.md` paths. Non-skill reference files (e.g. `styles/*.md`, outlines, "
    "research reports) should still be opened with `read_file` as instructed.\n\n"
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

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        # 先跑父类 skill_tool/skill_complete 的 body lift/unload 处理（不能跳过，
        # 否则 skill_complete 时 active skill body 不会被释放）。
        await super().after_tool_call(ctx)

        # 然后追加：skill_complete 带 report 时直接 force_finish，避免 subagent
        # 在 tool_call 之后再单跑一轮 stop 重复写一遍最终汇报。
        # 不调 _emit_user_visible_text——subagent 的 report 通过 force_finish.result
        # 上行作为 spawn_subagent 工具结果回到 parent agent，由 parent 决定如何展示。
        inputs = getattr(ctx, "inputs", None)
        if inputs is None:
            return
        if getattr(inputs, "tool_name", "") != "skill_complete":
            return
        tool_call = getattr(inputs, "tool_call", None)
        if tool_call is None:
            return
        report = _extract_skill_complete_arg(tool_call, "report")
        if not report:
            return
        ctx.request_force_finish(
            {
                "output": report,
                "result_type": "skill_complete_report",
                "skill_complete": {
                    "skill_name": _extract_skill_complete_arg(tool_call, "skill_name"),
                },
            },
        )
