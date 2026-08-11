# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""SkillProtocolPromptRail — 注入「技能执行规范」提示词。

技能清单与 ``skill_tool`` / ``skill_complete`` 约定由上游 SkillUseRail 的 ``skills`` 段负责；
本 rail 的 ``skill_protocol`` 与之对齐（加载 SKILL.md 正文**只能**使用 ``skill_tool``：
只有它会走 agent 侧 ``prompt_attachment_manager`` / 消息保护与 attachment 注入；
**禁止**用其它工具冒充等价加载）.
"""

from __future__ import annotations

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts import PromptSection
from openjiuwen.harness.rails.base import DeepAgentRail

from jiuwenswarm.agents.harness.common.prompt.prompt_builder import PromptPriority
from jiuwenswarm.common.utils import logger


_SKILL_PROTOCOL_SECTION_NAME = "skill_protocol"


def _build_skill_protocol_section_text(language: str) -> str:
    if language == "cn":
        return """## 技能执行规范（强制）

可用技能清单与加载/释放约定见本 prompt 的「技能」段（SkillUseRail 注入），**须与该段一致**。

### Skill Turbo 在线执行通道（skill_turbo_tool）

⚠️ **强制执行顺序**：当用户意图匹配已支持的 turbo 技能（当前：pptx-craft / PPT 制作）时，你的**第一个工具调用必须是 `skill_turbo_tool`（discover 模式）**（`scenario=None, plan_name=None`）。在调用它之前，**禁止**调用 `skill_tool`、`web_search`、`fetch_webpage` 或任何其他工具。

**场景选择规则（discover 后必读）**：discover 返回各 turbo 技能的**场景清单**（每个场景的 `scenario_id` + 触发条件）与**场景选择规则**，**据触发条件匹配用户意图选择正确 scenario**。

**执行流程（discover → activate → execute）**：
1. **discover**：调用 `skill_turbo_tool(skill_name="pptx-craft", scenario=None, plan_name=None)`，返回各场景的 `scenario_id` + 触发条件 + 场景选择规则。据触发条件选择正确 scenario。
2. **activate**：调用 `skill_turbo_tool(skill_name="pptx-craft", scenario="<据 discover 返回的触发条件选定的场景>")`，返回节点契约列表 `plan_tasks`（每个节点含 `plan_name`、`title`、`inputs`、`optional_inputs`、`outputs`、`when`、`when_category`、`when_known_after`、`when_self_noop`）。
3. **规划 todo**：根据 `plan_tasks` 创建 todo 列表。**条件节点按 `when_category` 三类区别处理**：
   - **todo 文本格式**：每个条目格式为 `Stage N: <title>`，例如 `Stage 1: 流水线初始化`。N 从 1 开始依次递增（1, 2, 3...），**禁止**出现跳号。**禁止**在文本中暴露 `plan_name`（如 `p0_pipeline_init`）、`pN` 短名、`(条件节点)` 后缀或其他内部代码细节——todo 是给用户看的进度面板，不是内部调试信息。
   - `when_category == "plan_time"`：条件值在 plan 阶段即可判定（来自用户请求/env）。**不满足时必须从 todo 中排除（不纳入），禁止先纳入再标 cancelled**——cancelled 状态会让前端置灰显示，误导用户以为节点失败。
   - `when_category == "runtime"`：条件值来自上游节点产出，plan 阶段未知，**必须纳入 todo 并调用**，节点运行时自判 no-op。
   - `when_category == "default_on"`：在线近恒真，**必须纳入 todo 并调用**。
   - 无 `when_category` 字段且无 `when` 条件：无条件执行，**必须纳入 todo**。
   - **严禁**在 plan 阶段删除 `runtime`/`default_on` 类节点（即使 `when` 条件看似不满足）。
4. **execute**：按顺序对每个节点调用 `skill_turbo_tool(skill_name="pptx-craft", scenario="<同 activate 场景>", plan_name="<节点名>", inputs={...})`。组装 `inputs` 时：
   - 从用户原始请求 + 前序节点返回的 `products` 组装。
   - **必传所有 `inputs`（必填键）+ 所有可从前序 `products` / 用户请求中获取的 `optional_inputs`**。
   - **即使某键在该节点属 `optional_inputs`，只要上游已产出就必须带上**——节点 code 可能严格要求这些键，漏传会导致节点崩溃触发 fallback。
   - **透传键纪律**：`p0_pipeline_init` 产出的 `pptx_root`/`output_dir`/`pages_dir`/`session_dir` 等共享路径键，必须透传给每一个在 `inputs` 或 `optional_inputs` 中声明了该键的下游节点。
    - **todo 推进（Agent 自驱）**：execute 成功后你**必须**调用 `todo_modify` 将对应 todo 条目标为 completed（含 when_self_noop 节点），并将下一待执行节点标为 in_progress。失败时不推进，由你自决重试或修复。
      - 返回 `products`（路径+标量）供后续组装；节点返回 `success: false` 时按 `error` 修复参数重试或询问用户。
5. **完成**：所有节点执行完成后，直接向用户总结结果 + 产物路径。

若你已错误地先调用了 `skill_tool` 或做了研究，**仍须立即调用 `skill_turbo_tool` activate**——不要因"已经做了研究"而放弃在线执行通道，此 reasoning 会导致冗余的慢速流程。

若 `skill_turbo_tool` 节点执行失败且无法修复，**必须**继续用 `skill_tool` 加载对应技能走标准流程完成用户任务。其余技能直接走下方 `skill_tool` 标准流程。

### 加载 SKILL.md 正文（禁止用 bash 执行工具名）
- **必须**且**只能**使用 `skill_tool(skill_name=..., relative_file_path="SKILL.md")` 加载；整段执行结束时调用 `skill_complete(skill_name=..., report="<最终回复>")`。`report` 即给用户的最终回复——**不要**再另写 stop；内容只写结果概要 + 产物路径，禁止复述步骤。不要把这些名字当作 shell 命令。
- 只有 `skill_tool` 会走系统集成路径（正文入会话、后续上下文中的保护与 [ACTIVE SKILL BODY] 注入），**禁止**用任何其它工具加载或拼凑 SKILL.md。
- 若你当前可用工具列表中**没有** `skill_tool`：无法按本系统路径加载技能正文，请向用户说明环境未开放该能力；**不得**用其它工具代替。
- 需要多看或刷新全文时，**只能**再次调用 `skill_tool`。

随后按 SKILL 工作流执行；下列规范约束执行过程。

1. **声明步骤**：默认情况下，每次行动前必须在回复开头声明当前所在步骤，格式：`[当前步骤: <步骤名称>]`。**无需调用任何工具来"开始"步骤**——声明本身即代表进入该步。若 SKILL.md 明确声明“阶段状态和阶段消息由工具事件唯一生成”，则以该声明为准，禁止自行输出 `[当前步骤: ...]` 或其他步骤声明。
2. **必须使用 todo**：在执行skill步骤前，必须先创建 todo 列表。创建后，必须在执行过程中持续更新（如打勾已完成项、添加遗漏项等），确保 todo 与实际执行状态始终保持一致。
   **Skill Turbo 在线执行时**：execute 成功后你**必须** `todo_modify` 将对应条目标为 completed（含 when_self_noop）并推进下一项 in_progress——todo 由 Agent 自驱推进，不依赖系统自动推进。
   放弃、跳过或决定不再执行某步骤时（如用户说「不生成 PPT 了」），**必须**立即 `todo_modify` 将该条标为 `cancelled`；
   禁止仅用口头回复收尾而仍保留 `in_progress`/`pending` 项。
3. **严格顺序**：按 SKILL.md 定义的顺序逐步执行，**禁止跳过、合并或重排步骤**，除非 SKILL.md 或用户明确允许。
4. **闸门等待**：遇到需要用户确认/审批的步骤时，**必须等待用户回复，禁止自行假设用户同意**。
5. **不确定时重读**：只能再次调用 `skill_tool`，**不得**用其它工具获取 SKILL.md。
6. **内容忠实**：SKILL.md 是规格说明，不是参考建议。其中定义的选项列表、参数值、标签文本、推荐标记等必须**原样使用**，禁止自行添加、删除、修改或重新措辞。
7. **错误处理**：执行子步骤出错时，**禁止自行决定跳过该步骤或后续步骤**。必须先尝试修复（如安装缺失依赖、修正参数），修复失败则询问用户如何处理，等待用户指示后再继续。
8. **工具降级**：SKILL.md 中提到的工具如果在当前环境中不存在，必须先告知用户该工具不可用并说明你打算如何替代，获得用户同意后再继续。不要花时间反复检查工具列表。
9. **用户打断后的处置**：按用户**原话**判定意图，**禁止自己猜**：
   - 原话含"继续"/"接着做"/"刚才那个继续" → 继续当前技能流程。
   - 其他情况 → 若仍有 active todo，先用 `todo_modify` 将相关项标为 `cancelled`，
     再调用 `skill_complete(skill_name="<当前技能>")` 释放技能上下文，然后回应新请求。

⚠️ 用户发 N 条消息 ≠ N 个并发任务；新消息**默认覆盖**旧任务，**不追加**。禁止措辞："让我先完成之前的"/"先把之前的收尾"/"两个都做"/"先 X 再 Y"（除非用户原话已明示并列）。仅凭 history 有两条任务消息就推断"用户想做两个"是错误推理。
"""
    return """## Skill Execution Protocol (Mandatory)

The "Skills" section of this prompt (from SkillUseRail) lists available skills and how to load/release them — **follow that section**.

### Skill Turbo Online Execution Channel (skill_turbo_tool)

⚠️ **Mandatory execution order**: When the user's intent matches a supported turbo skill (currently: pptx-craft / PPT creation), your **FIRST tool call MUST be `skill_turbo_tool` (discover mode)** (`scenario=None, plan_name=None`). Before calling it, you are **FORBIDDEN** from calling `skill_tool`, `web_search`, `fetch_webpage`, or any other tool.

**Scenario selection rules (read after discover)**: discover returns **scenario summaries** (each scenario's `scenario_id` + trigger condition) and **selection rules** for each turbo skill — **match the user's intent against the trigger conditions to select the correct scenario**.

**Execution flow (discover → activate → execute)**:
1. **discover**: Call `skill_turbo_tool(skill_name="pptx-craft", scenario=None, plan_name=None)`, returns each scenario's `scenario_id` + trigger condition + selection rules. Choose the correct scenario per trigger conditions.
2. **activate**: Call `skill_turbo_tool(skill_name="pptx-craft", scenario="<chosen per discover trigger conditions>")`, returns node contract list `plan_tasks` (each node has `plan_name`, `title`, `inputs`, `optional_inputs`, `outputs`, `when`, `when_category`, `when_known_after`, `when_self_noop`).
3. **Plan todo**: Based on `plan_tasks`, create a todo list. **Handle conditional nodes by `when_category`**:
   - **Todo text format**: Each item MUST be `Stage N: <title>`, e.g., `Stage 1: Pipeline init`. N starts at 1 and increments sequentially (1, 2, 3...), **NEVER** skip numbers. **NEVER** expose `plan_name` (e.g., `p0_pipeline_init`), `pN` short names, `(conditional)` suffix, or any internal code details in the text — todo is a progress panel for the user, not internal debug info.
   - `when_category == "plan_time"`: condition is decidable at plan time (from user request/env). **If not met, MUST exclude from todo (do not include). NEVER include then mark cancelled** — cancelled status greys out the node in frontend, misleading users into thinking it failed.
   - `when_category == "runtime"`: condition value comes from upstream node outputs, unknown at plan time — **must include in todo and call**; node self-evaluates no-op at runtime.
   - `when_category == "default_on"`: nearly always true online — **must include in todo and call**.
   - No `when_category` and no `when`: unconditional — **must include in todo**.
   - **NEVER** remove `runtime`/`default_on` nodes at plan time (even if `when` condition appears unmet).
4. **execute**: For each node in order, call `skill_turbo_tool(skill_name="pptx-craft", scenario="<same as activate>", plan_name="<node_name>", inputs={...})`. When assembling `inputs`:
   - Assemble from the user's original request + previous nodes' `products`.
   - **MUST pass all `inputs` (required keys) + all `optional_inputs` that are available from previous `products` / user request**.
   - **Even if a key is in `optional_inputs`, if upstream has produced it, you MUST pass it** — node code may strictly require these keys; omitting them causes node crashes and triggers fallback.
   - **Passthrough key discipline**: `pptx_root`/`output_dir`/`pages_dir`/`session_dir` and other shared path keys produced by `p0_pipeline_init` must be passed to every downstream node that declares them in `inputs` or `optional_inputs`.
    - **Todo advancement (Agent-driven)**: On execute success you **MUST** call `todo_modify` to mark the corresponding todo item as completed (including when_self_noop nodes), and mark the next pending node as in_progress. On failure the todo is NOT advanced; you decide retry or fix.
      - Returns `products` (paths + scalars) for subsequent assembly; on `success: false`, fix parameters per `error` and retry or ask the user.
5. **Complete**: After all nodes finish, summarize the result + artifact paths to the user.

If you have already mistakenly called `skill_tool` or done research, **you MUST still call `skill_turbo_tool` activate immediately** — do NOT abandon the online execution channel just because "research is already done"; that reasoning leads to redundant, slower execution.

If `skill_turbo_tool` node execution fails and cannot be fixed, you **MUST** fall back to `skill_tool` to load the corresponding skill and complete the user's task via the standard flow. All other skills use the `skill_tool` standard flow below.

### Load SKILL.md body (never run tool names as shell/bash commands)
- You **must** use **only** `skill_tool(skill_name=..., relative_file_path="SKILL.md")` to load the body; when the whole flow is done, call `skill_complete(skill_name=..., report="<final reply>")`. `report` IS the final reply to the user — do **not** write a separate stop turn; keep it to outcome summary + artifact paths, no step recaps.
- Only `skill_tool` enters the integrated path (session body copy, message protection, and `[ACTIVE SKILL BODY]` reinjection). **Do not** load or stitch SKILL.md with any other tool.
- If `skill_tool` is **not** in your available tool list, you cannot load skill bodies on this integration path—tell the user; **do not** substitute another file-reading tool.
- To see more or refresh the full SKILL.md, you **may only** call `skill_tool` again.

Then execute the workflow; the rules below govern execution.

1. **Declare step**: By default, before each action, state your current step at the start of your reply: `[Current Step: <step name>]`. **You do NOT call any tool to "start" a step** — the declaration itself enters the step. If SKILL.md explicitly states that stage status and stage messages are emitted exclusively by tool events, follow that rule and you must not declare `[Current Step: ...]` or any other step message yourself.
2. **Use todo (mandatory)**: For skills, you MUST create a todo list before executing the skill steps.
   Once created, you MUST continuously update it throughout execution (e.g. check off completed items,
   add missing steps) to ensure the todo always reflects the actual execution state.
    **For Skill Turbo online execution**: on execute success you **MUST** `todo_modify` to mark the
    corresponding item as completed (including when_self_noop) and advance the next item to
    in_progress — todo advancement is Agent-driven, not system-auto-advanced.
    When abandoning or skipping a step (e.g. the user says not to generate the PPT), you **must** call
    `todo_modify` to mark it `cancelled` immediately; never end with text only while items stay
    `in_progress` or `pending`.
3. **Strict order**: Execute steps in the order defined by SKILL.md. **Do not skip, merge, or reorder steps** unless SKILL.md or the user explicitly allows it.
4. **Gate enforcement**: When a step requires user confirmation/approval, **you MUST wait for the user's response. Never assume approval.**
5. **Re-read when unsure**: Refresh the SKILL.md body **only** by calling `skill_tool` again — **never** use any other tool to obtain SKILL.md.
6. **Content fidelity**: SKILL.md is a specification, not a suggestion. Option lists, parameter values, label text, and recommendation markers defined therein must be used **verbatim** — never add, remove, modify, or rephrase them.
7. **Error handling**: When a sub-step fails, **never decide on your own to skip it or subsequent steps**. First attempt to fix the issue (e.g. install missing dependencies, correct parameters). If the fix fails, ask the user how to proceed and wait for their instructions.
8. **Tool fallback**: If a tool mentioned in SKILL.md does not exist in your current environment, you MUST first inform the user that the tool is unavailable and explain how you plan to substitute it. Only proceed after the user agrees. Do not spend time repeatedly checking the tool list.
9. **Handling user interruption**: Decide intent strictly from the user's **literal words**, **never guess**:
   - Words include "continue" / "go on" / "resume the previous one" → continue the current skill flow.
   - Otherwise → if active todos remain, call `todo_modify` to mark them `cancelled` first,
     then call `skill_complete(skill_name="<current skill>")` to release the skill context,
     then respond to the new request.

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
        # 热重载后 agent.system_prompt_builder 可能已是新引用，退休清理前先同步缓存，
        # 确保 remove_section 落到当前生效的 builder 上。
        _builder = getattr(agent, "system_prompt_builder", None)
        if _builder is not None:
            self.system_prompt_builder = _builder
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
        # 热重载（DeepAgent._hot_reload_system_prompt）会新建 SystemPromptBuilder 并替换
        # agent.system_prompt_builder，但保留型 rail 不会重新 init()，缓存的
        # self.system_prompt_builder 可能指向旧 builder。这里每次从 ctx.agent 现取最新
        # builder 并刷新缓存，使后续 add_section 都落到当前生效的 builder 上。
        _builder = getattr(getattr(ctx, "agent", None), "system_prompt_builder", None)
        if _builder is not None:
            self.system_prompt_builder = _builder

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
