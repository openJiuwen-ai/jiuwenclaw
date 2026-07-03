# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""SkillProtocolPromptRail — 注入「技能执行规范」提示词。

技能清单与 ``skill_tool`` / ``skill_complete`` 约定由上游 SkillUseRail 的 ``skills`` 段负责；
本 rail 的 ``skill_protocol`` 与之对齐（加载 SKILL.md 正文**只能**使用 ``skill_tool``：
只有它会走 agent 侧 ``active_skill_bodies`` / 消息保护与 pin 注入；
**禁止**用其它工具冒充等价加载）.
"""

from __future__ import annotations

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts import PromptSection
from openjiuwen.harness.rails.base import DeepAgentRail

from jiuwenclaw.agentserver.deep_agent.prompt_builder import PromptPriority
from jiuwenclaw.utils import logger


_SKILL_PROTOCOL_SECTION_NAME = "skill_protocol"


def _build_skill_protocol_section_text(language: str) -> str:
    if language == "cn":
        return """## 技能执行规范（强制）

可用技能清单与加载/释放约定见本 prompt 的「技能」段（SkillUseRail 注入），**须与该段一致**。

## 技能加速通道（skill_turbo）

当用户意图明确匹配 `skill_turbo` 已支持的技能（当前：pptx-craft / PPT 制作）时，**优先调用 `skill_turbo`**，而非 `skill_tool`。`skill_turbo` 内部会完成规划+生成+推送全流程，无需你再 `skill_tool` 加载 SKILL.md。若 `skill_turbo` 返回成功（产物已生成），**禁止**再用 `skill_tool` 重复同一任务——直接向用户总结结果即可。若 `skill_turbo` 返回失败或未处理，**必须**继续用 `skill_tool` 加载对应技能走标准流程完成用户任务。其余技能直接走下方 `skill_tool` 标准流程。

**加载 SKILL.md 正文（禁止用 bash 执行工具名）：**
- **必须**且**只能**使用 `skill_tool(skill_name=..., relative_file_path="SKILL.md")` 加载；整段执行结束时调用 `skill_complete(skill_name=..., report="<最终回复>")`。`report` 即给用户的最终回复——**不要**再另写 stop；内容只写结果概要 + 产物路径，禁止复述步骤。不要把这些名字当作 shell 命令。
- 只有 `skill_tool` 会走系统集成路径（正文入会话、后续上下文中的保护与 [ACTIVE SKILL BODY] 注入），**禁止**用任何其它工具加载或拼凑 SKILL.md。
- 若你当前可用工具列表中**没有** `skill_tool`：无法按本系统路径加载技能正文，请向用户说明环境未开放该能力；**不得**用其它工具代替。
- 需要多看或刷新全文时，**只能**再次调用 `skill_tool`。

随后按 SKILL 工作流执行；下列规范约束执行过程。

1. **声明步骤**：每次行动前，必须在回复开头声明当前所在步骤，格式：`[当前步骤: <步骤名称>]`。**无需调用任何工具来"开始"步骤**——声明本身即代表进入该步。
2. **必须使用 todo**：在执行skill步骤前，必须先创建 todo 列表。创建后，必须在执行过程中持续更新（如打勾已完成项、添加遗漏项等），确保 todo 与实际执行状态始终保持一致。
3. **严格顺序**：按 SKILL.md 定义的顺序逐步执行，**禁止跳过、合并或重排步骤**，除非 SKILL.md 或用户明确允许。
4. **闸门等待**：遇到需要用户确认/审批的步骤时，**必须等待用户回复，禁止自行假设用户同意**。
5. **不确定时重读**：只能再次调用 `skill_tool`，**不得**用其它工具获取 SKILL.md。
6. **内容忠实**：SKILL.md 是规格说明，不是参考建议。其中定义的选项列表、参数值、标签文本、推荐标记等必须**原样使用**，禁止自行添加、删除、修改或重新措辞。
7. **错误处理**：执行子步骤出错时，**禁止自行决定跳过该步骤或后续步骤**。必须先尝试修复（如安装缺失依赖、修正参数），修复失败则询问用户如何处理，等待用户指示后再继续。
8. **工具降级**：SKILL.md 中提到的工具如果在当前环境中不存在，必须先告知用户该工具不可用并说明你打算如何替代，获得用户同意后再继续。不要花时间反复检查工具列表。
9. **用户打断后的处置**：按用户**原话**判定意图，**禁止自己猜**：
   - 原话含"继续"/"接着做"/"刚才那个继续" → 继续当前技能流程。
   - 其他情况 → 先调用 `skill_complete(skill_name="<当前技能>")` 释放技能上下文，再回应新请求。

⚠️ 用户发 N 条消息 ≠ N 个并发任务；新消息**默认覆盖**旧任务，**不追加**。禁止措辞："让我先完成之前的"/"先把之前的收尾"/"两个都做"/"先 X 再 Y"（除非用户原话已明示并列）。仅凭 history 有两条任务消息就推断"用户想做两个"是错误推理。
"""
    return """## Skill Execution Protocol (Mandatory)

The "Skills" section of this prompt (from SkillUseRail) lists available skills and how to load/release them — **follow that section**.

## Skill Acceleration Channel (skill_turbo)

When the user's intent clearly matches a skill already supported by `skill_turbo` (currently: pptx-craft / PPT creation), **call `skill_turbo` first** instead of `skill_tool`. `skill_turbo` handles the full plan-and-generate pipeline internally — you do NOT need to load SKILL.md via `skill_tool`. If `skill_turbo` returns success (the artifact is already generated), you are **forbidden** from calling `skill_tool` again for the same task — just summarize the result to the user. If `skill_turbo` returns failure or is not handled, you **MUST** fall back to `skill_tool` to load the corresponding skill and complete the user's task via the standard flow. All other skills use the `skill_tool` standard flow below.

**Load SKILL.md body (never run tool names as shell/bash commands):**
- You **must** use **only** `skill_tool(skill_name=..., relative_file_path="SKILL.md")` to load the body; when the whole flow is done, call `skill_complete(skill_name=..., report="<final reply>")`. `report` IS the final reply to the user — do **not** write a separate stop turn; keep it to outcome summary + artifact paths, no step recaps.
- Only `skill_tool` enters the integrated path (session body copy, message protection, and `[ACTIVE SKILL BODY]` reinjection). **Do not** load or stitch SKILL.md with any other tool.
- If `skill_tool` is **not** in your available tool list, you cannot load skill bodies on this integration path—tell the user; **do not** substitute another file-reading tool.
- To see more or refresh the full SKILL.md, you **may only** call `skill_tool` again.

Then execute the workflow; the rules below govern execution.

1. **Declare step**: Before each action, state your current step at the start of your reply: `[Current Step: <step name>]`. **You do NOT call any tool to "start" a step** — the declaration itself enters the step.
2. **Use todo (mandatory)**: For skills, you MUST create a todo list before executing the skill steps. Once created, you MUST continuously update it throughout execution (e.g. check off completed items, add missing steps) to ensure the todo always reflects the actual execution state.
3. **Strict order**: Execute steps in the order defined by SKILL.md. **Do not skip, merge, or reorder steps** unless SKILL.md or the user explicitly allows it.
4. **Gate enforcement**: When a step requires user confirmation/approval, **you MUST wait for the user's response. Never assume approval.**
5. **Re-read when unsure**: Refresh the SKILL.md body **only** by calling `skill_tool` again — **never** use any other tool to obtain SKILL.md.
6. **Content fidelity**: SKILL.md is a specification, not a suggestion. Option lists, parameter values, label text, and recommendation markers defined therein must be used **verbatim** — never add, remove, modify, or rephrase them.
7. **Error handling**: When a sub-step fails, **never decide on your own to skip it or subsequent steps**. First attempt to fix the issue (e.g. install missing dependencies, correct parameters). If the fix fails, ask the user how to proceed and wait for their instructions.
8. **Tool fallback**: If a tool mentioned in SKILL.md does not exist in your current environment, you MUST first inform the user that the tool is unavailable and explain how you plan to substitute it. Only proceed after the user agrees. Do not spend time repeatedly checking the tool list.
9. **Handling user interruption**: Decide intent strictly from the user's **literal words**, **never guess**:
   - Words include "continue" / "go on" / "resume the previous one" → continue the current skill flow.
   - Otherwise → call `skill_complete(skill_name="<current skill>")` to release the skill context, then respond to the new request.

⚠️ N user messages ≠ N parallel tasks; a new substantive message **replaces** the prior request by default — it does NOT stack. Forbidden phrasing: "let me finish the previous task first" / "let me wrap that up" / "let me do both" / "first X then Y" (unless the user's own words explicitly stated parallel intent). Inferring multiple parallel tasks purely from history is a **wrong inference** that MUST be avoided.
"""


class SkillProtocolPromptRail(DeepAgentRail):
    """Refresh the skill_protocol prompt section before each model call."""

    priority = 8

    def __init__(self) -> None:
        super().__init__()
        self.system_prompt_builder = None

    def init(self, agent) -> None:
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)

    def uninit(self, agent) -> None:
        if self.system_prompt_builder is not None:
            self.system_prompt_builder.remove_section(_SKILL_PROTOCOL_SECTION_NAME)
        self.system_prompt_builder = None

    def _resolve_priority(self, name: str, default_priority: int) -> int:
        existing = self.system_prompt_builder.get_section(name)
        return existing.priority if existing is not None else default_priority

    def _resolve_language(self) -> str:
        lang = getattr(self.system_prompt_builder, "language", None) or "cn"
        return "cn" if lang in ("cn", "zh") else "en"

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        if self.system_prompt_builder is None:
            return

        language = self._resolve_language()
        try:
            protocol_text = _build_skill_protocol_section_text(language)
            self.system_prompt_builder.add_section(PromptSection(
                name=_SKILL_PROTOCOL_SECTION_NAME,
                content={language: protocol_text},
                priority=self._resolve_priority(
                    _SKILL_PROTOCOL_SECTION_NAME, PromptPriority.SKILL_PROTOCOL,
                ),
            ))
        except Exception as exc:
            logger.warning(
                "[SkillProtocolPromptRail] build skill_protocol section failed: %s", exc,
            )


__all__ = ["SkillProtocolPromptRail"]
