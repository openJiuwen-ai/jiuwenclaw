# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""SkillProtocolPromptRail — 注入「技能执行规范」+「Skill 加速面 catalog」提示词。

职责（设计 §5.1）：
1. ``skill_protocol`` section：技能执行规范 9 条（供 skill_tool ReAct 回退用）
2. ``SKILL_TURBO`` section（层1 常驻轻量）：从 SKILL_TURBO.md frontmatter 动态构建
   turbo 面 name+description + scenario 列表，指向 ``skill_turbo_tool``，供 Agent 决策
3. ``after_tool_call``（层2 按需）：``skill_turbo_tool`` activate 调用时把
   SKILL_TURBO.md 正文钉进上下文窗口（复用 active_skill_bodies 机制）

与 SkillUseRail 的关系：并存且同阶段，共用同一份共享 skill registry（同一 skill_root）。
``skill_tool`` 加载源 SKILL.md、``skill_turbo_tool`` activate 加载 SKILL_TURBO.md，
二者均走 ``active_skill_bodies``（不同 skill_name/section）。
"""

from __future__ import annotations

from typing import Any

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.core.single_agent.rail.base import ToolCallInputs
from openjiuwen.harness.prompts import PromptSection
from openjiuwen.harness.rails.base import DeepAgentRail

from jiuwenclaw.agentserver.deep_agent.prompt_builder import PromptPriority
from jiuwenclaw.utils import logger

_SKILL_PROTOCOL_SECTION_NAME = "skill_protocol"
# 层1 turbo catalog section（与 skill_protocol section 并存、互不替换）
_SKILL_TURBO_SECTION_NAME = "SKILL_TURBO"


def _build_skill_protocol_section_text(language: str) -> str:
    if language == "cn":
        return """## 技能执行规范（强制）

可用技能清单与加载/释放约定见本 prompt 的「技能」段（SkillUseRail 注入），**须与该段一致**。

### Skill 加速通道（skill_turbo_tool 在线执行）

⚠️ **强制执行顺序**：当本 prompt 的「可用 Skill 加速面（SkillTurbo）」段列出匹配的 turbo 加速面时，你的**第一个工具调用必须是 `skill_turbo_tool`**（先 activate 初始化 + 加载执行流程，再按 `next_candidates` 逐节点 execute）。在首次 `skill_turbo_tool` 调用之前，**禁止**调用 `skill_tool`、`web_search`、`fetch_webpage` 或任何其他工具——`skill_turbo_tool` 内部会自行完成逐节点执行（含研究所需步骤）+ 产物推送，无需你提前加载 SKILL.md 或做研究。

若你已错误地先调用了 `skill_tool` 或做了研究，**仍须立即调用 `skill_turbo_tool`**——不要因"已经做了研究"而放弃加速通道，此 reasoning 会导致冗余的慢速流程。

若 `skill_turbo_tool` 返回成功（task_complete=true），**禁止**再用 `skill_tool` 重复同一任务——直接向用户总结结果即可。若 `skill_turbo_tool` 返回失败或要求回退，**必须**按回退指引改用 `skill_tool`（标准 ReAct）完成用户任务。其余技能直接走下方 `skill_tool` 标准流程。

**走 `skill_turbo_tool` 时的进度与参数约定：**
- 系统会通过 task 进度事件自动维护任务列表（与批量 skill 加速一致），**禁止**再调用 `todo_create` / `todo_modify` 创建与 Stage 列表同构的待办，以免双轨冲突。
- 只需按 `next_candidates` 逐个调用 `skill_turbo_tool(plan_name=...)`。
- `plan_name` 传**字符串**节点名（如 `"p0_pipeline_init"`），不要传对象。

### 加载 SKILL.md / SKILL_TURBO.md 正文（禁止用 bash 执行工具名）
- 源 skill 正文：**必须**且**只能**使用 `skill_tool(skill_name=..., relative_file_path="SKILL.md")` 加载；整段执行结束时调用 `skill_complete(skill_name=..., report="<最终回复>")`。`report` 即给用户的最终回复——**不要**再另写 stop；内容只写结果概要 + 产物路径，禁止复述步骤。不要把这些名字当作 shell 命令。
- turbo 加速面正文：`skill_turbo_tool` **activate 调用**（`plan_name` 省略）会自动加载 SKILL_TURBO.md 正文钉入上下文窗口（`[ACTIVE SKILL_TURBO BODY]` 块），**无需**也**禁止**用 `skill_tool` 加载 SKILL_TURBO.md。
- `skill_tool` 与 `skill_turbo_tool` activate 均走系统集成路径（正文入会话、后续上下文中的保护与 pin 注入），**禁止**用任何其它工具加载或拼凑 SKILL.md / SKILL_TURBO.md。
- 若你当前可用工具列表中**没有** `skill_tool` / `skill_turbo_tool`：无法按本系统路径加载技能正文，请向用户说明环境未开放该能力；**不得**用其它工具代替。
- 需要多看或刷新全文时，**只能**再次调用 `skill_tool`（源 skill）或重新 `skill_turbo_tool` activate（turbo）。

随后按 SKILL 工作流执行；下列规范约束执行过程。

1. **声明步骤**：每次行动前，必须在回复开头声明当前所在步骤，格式：`[当前步骤: <步骤名称>]`。**无需调用任何工具来"开始"步骤**——声明本身即代表进入该步。
2. **必须使用 todo**：在执行 **skill_tool 标准流程** 的步骤前，必须先创建 todo 列表。创建后，必须在执行过程中持续更新（如打勾已完成项、添加遗漏项等），确保 todo 与实际执行状态始终保持一致。
   （若正在使用 `skill_turbo_tool` 在线加速，系统已自动维护任务进度列表，**不要**再 `todo_create` 同构待办。）
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

### Skill Acceleration Channel (skill_turbo_tool online execution)

⚠️ **Mandatory execution order**: When the "Available Skill Acceleration Faces (SkillTurbo)" section lists a matching turbo face, your **FIRST tool call MUST be `skill_turbo_tool`** (activate to init + load flow, then execute each node via `next_candidates`). Before that first `skill_turbo_tool` call, you are **FORBIDDEN** from calling `skill_tool`, `web_search`, `fetch_webpage`, or any other tool — `skill_turbo_tool` handles per-node execution (including any research steps) + artifact delivery internally; you do NOT need to load SKILL.md or do research beforehand.

If you have already mistakenly called `skill_tool` or done research, **you MUST still call `skill_turbo_tool` immediately** — do NOT abandon the acceleration channel just because "research is already done"; that reasoning leads to redundant, slower execution.

If `skill_turbo_tool` returns success (task_complete=true), you are **forbidden** from calling `skill_tool` again for the same task — just summarize the result to the user. If `skill_turbo_tool` returns failure or requests fallback, you **MUST** follow the fallback guidance to use `skill_tool` (standard ReAct) to complete the user's task. All other skills use the `skill_tool` standard flow below.

**Progress & args when using `skill_turbo_tool`:**
- The system auto-maintains the task list via task progress events (same as batch skill acceleration). **Do not** call `todo_create` / `todo_modify` to build a duplicate Stage todo list.
- Just call `skill_turbo_tool(plan_name=...)` for each entry in `next_candidates`.
- Pass `plan_name` as a **string** node name (e.g. `"p0_pipeline_init"`), never as an object.

### Load SKILL.md / SKILL_TURBO.md body (never run tool names as shell/bash commands)
- Source skill body: You **must** use **only** `skill_tool(skill_name=..., relative_file_path="SKILL.md")` to load the body; when the whole flow is done, call `skill_complete(skill_name=..., report="<final reply>")`. `report` IS the final reply to the user — do **not** write a separate stop turn; keep it to outcome summary + artifact paths, no step recaps.
- Turbo face body: `skill_turbo_tool` **activate call** (`plan_name` omitted) auto-loads the SKILL_TURBO.md body into the context window (`[ACTIVE SKILL_TURBO BODY]` block) — you do NOT and **must not** use `skill_tool` to load SKILL_TURBO.md.
- Both `skill_tool` and `skill_turbo_tool` activate enter the integrated path (session body copy, message protection, and pin reinjection). **Do not** load or stitch SKILL.md / SKILL_TURBO.md with any other tool.
- If `skill_tool` / `skill_turbo_tool` are **not** in your available tool list, you cannot load skill bodies on this integration path—tell the user; **do not** substitute another file-reading tool.
- To see more or refresh the full SKILL.md, you **may only** call `skill_tool` again (source skill) or re-`skill_turbo_tool` activate (turbo).

Then execute the workflow; the rules below govern execution.

1. **Declare step**: Before each action, state your current step at the start of your reply: `[Current Step: <step name>]`. **You do NOT call any tool to "start" a step** — the declaration itself enters the step.
2. **Use todo (mandatory for skill_tool)**: For **skill_tool** standard flows, you MUST create a todo list before executing the skill steps.
   Once created, you MUST continuously update it throughout execution (e.g. check off completed items,
   add missing steps) to ensure the todo always reflects the actual execution state.
   (If you are using `skill_turbo_tool` online acceleration, the system already maintains the task
   progress list — **do not** `todo_create` a duplicate Stage list.)
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


def _discover_turbo_faces_from_registry() -> list:
    """从共享 skill registry 探测所有有 turbo 产物的 skill（层1 catalog 用）。

    复用 skill_use_rail 的 skill registry（同一 skill_root），不重复加载 skill。
    只读 registry 里已有的 skill_root 做 turbo 存在性探测（设计 §5.1）。
    """
    try:
        from jiuwenclaw.agentserver.skill_turbo.online.schema_loader import (
            discover_all_turbo_faces,
        )
        from jiuwenclaw.utils import get_agent_registered_skill_dirs
        skill_dirs = get_agent_registered_skill_dirs()
        return discover_all_turbo_faces([str(d) for d in skill_dirs])
    except Exception as exc:
        logger.debug("[SkillProtocolPromptRail] discover turbo faces failed: %s", exc)
        return []


def _build_skill_turbo_catalog_text(faces: list, language: str) -> str:
    """构建层1 轻量 catalog（turbo 面 name+description + scenario 列表）。

    设计 §5.1：只放 frontmatter 的 name+description + 可用 scenario 列表，
    不放正文、不放 entry_plan。体量与源 skill catalog 一致。
    """
    if not faces:
        return ""
    if language == "cn":
        lines = ["## 可用 Skill 加速面（SkillTurbo）"]
        for face in faces:
            scenarios_str = ", ".join(face.scenarios)
            lines.append(
                f"- {face.turbo_name}: {face.description}  "
                f"(source: {face.source_skill}, scenarios: {scenarios_str})"
            )
        lines.append(
            "当用户意图匹配某 skill 加速面时，优先调用 skill_turbo_tool 在线执行"
            "（先 activate 初始化 + 加载执行流程，再逐节点 execute）。"
        )
        return "\n".join(lines)
    lines = ["## Available Skill Acceleration Faces (SkillTurbo)"]
    for face in faces:
        scenarios_str = ", ".join(face.scenarios)
        lines.append(
            f"- {face.turbo_name}: {face.description}  "
            f"(source: {face.source_skill}, scenarios: {scenarios_str})"
        )
    lines.append(
        "When the user's intent matches a skill acceleration face, prefer calling "
        "skill_turbo_tool for online execution (activate first to init + load flow, "
        "then execute each node)."
    )
    return "\n".join(lines)


def _record_turbo_active_body(
    session: Any,
    turbo_name: str,
    body: str,
    tool_call_id: str = "",
    *,
    max_active_skill_bodies: int | None = None,
) -> bool:
    """把 SKILL_TURBO.md 正文作为 active skill body 记录到 session state（层2）。

    走公共 API ``record_active_skill_body(session, tool_message, result)``（优化修复 F6）。
    实际签名需要 ToolMessage（metadata.is_skill_body）+ ToolOutput.data.skill_content，
    故构造包装对象；**不 stub** 真实工具返回（turbo 正文不在工具返回里）。
    """
    if session is None or not turbo_name or not body:
        return False
    try:
        from openjiuwen.core.context_engine.active_skill_bodies import (
            DEFAULT_MAX_ACTIVE_SKILL_BODIES,
            record_active_skill_body,
        )
        from openjiuwen.core.foundation.llm import ToolMessage
        from openjiuwen.harness.tools import ToolOutput
    except Exception as exc:
        logger.warning("[SkillProtocolPromptRail] import active_skill_bodies failed: %s", exc)
        return False
    try:
        tcid = tool_call_id or f"skill_turbo_activate:{turbo_name}"
        tool_message = ToolMessage(
            content="",
            tool_call_id=tcid,
            metadata={
                "is_skill_body": True,
                "skill_name": turbo_name,
                "relative_file_path": "SKILL_TURBO.md",
            },
        )
        result = ToolOutput(
            success=True,
            data={"skill_content": body},
        )
        limit = (
            DEFAULT_MAX_ACTIVE_SKILL_BODIES
            if max_active_skill_bodies is None
            else max_active_skill_bodies
        )
        recorded = record_active_skill_body(
            session,
            tool_message,
            result,
            max_active_skill_bodies=limit,
        )
        if recorded:
            logger.info(
                "[SkillProtocolPromptRail] recorded turbo active body turbo=%s body_len=%d",
                turbo_name, len(body),
            )
        else:
            logger.warning(
                "[SkillProtocolPromptRail] record_active_skill_body returned False turbo=%s",
                turbo_name,
            )
        return bool(recorded)
    except Exception as exc:
        logger.warning("[SkillProtocolPromptRail] record turbo active body failed: %s", exc)
        return False


def _unregister_turbo_active_body(session: Any, turbo_name: str) -> int:
    """释放层2 turbo 正文 pin（任务完成/回退时调用）。"""
    if session is None or not turbo_name:
        return 0
    try:
        from openjiuwen.core.context_engine.active_skill_bodies import (
            unregister_active_skill_body,
        )
        return unregister_active_skill_body(session, turbo_name, "SKILL_TURBO.md")
    except Exception as exc:
        logger.warning("[SkillProtocolPromptRail] unregister turbo active body failed: %s", exc)
        return 0


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
            self.system_prompt_builder.remove_section(_SKILL_TURBO_SECTION_NAME)
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

        # 层1 turbo catalog（常驻轻量，与 skill_protocol section 并存）
        try:
            faces = _discover_turbo_faces_from_registry()
            catalog_text = _build_skill_turbo_catalog_text(faces, language)
            if catalog_text:
                self.system_prompt_builder.add_section(PromptSection(
                    name=_SKILL_TURBO_SECTION_NAME,
                    content={language: catalog_text},
                    priority=self._resolve_priority(
                        _SKILL_TURBO_SECTION_NAME, PromptPriority.SKILL_PROTOCOL,
                    ),
                ))
            else:
                # 无 turbo 产物 → 移除旧 section（避免热重载后残留）
                self.system_prompt_builder.remove_section(_SKILL_TURBO_SECTION_NAME)
        except Exception as exc:
            logger.warning(
                "[SkillProtocolPromptRail] build SKILL_TURBO catalog section failed: %s", exc,
            )

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        """层2：skill_turbo_tool activate 调用 → record_active_skill_body 钉入 SKILL_TURBO.md 正文。

        设计 §5.1：
        - 仅 activate 调用（plan_name 省略）触发；execute 调用不处理
        - 保留工具返回（activate 返回 loaded+next_candidates，不 stub）
        - 正文由 context engine 后续每轮钉进上下文窗口作 [ACTIVE SKILL_TURBO BODY] 块
        - 任务完成/回退时由 skill_turbo_tool 内部调 unregister 释放
        """
        inputs = getattr(ctx, "inputs", None)
        if not isinstance(inputs, ToolCallInputs):
            return

        tool_name = (inputs.tool_name or "").strip()
        if tool_name != "skill_turbo_tool":
            return

        # 判断是否 activate 调用（plan_name 省略）
        tool_args = inputs.tool_args
        if not isinstance(tool_args, dict):
            return
        if tool_args.get("plan_name") is not None:
            return  # execute 调用，不处理

        # 取工具返回，判断 activate 是否成功
        tool_result = inputs.tool_result
        if not _is_activate_success(tool_result):
            return  # activate 失败不加载正文

        skill_name = str(tool_args.get("skill_name", "") or "").strip()
        if not skill_name:
            return

        # 读 SKILL_TURBO.md 正文
        try:
            from jiuwenclaw.agentserver.skill_turbo.online.schema_loader import (
                discover_turbo_face,
                load_skill_turbo_body,
            )
            # 从共享 skill registry 解析 skill_root
            skill_root = _resolve_skill_root_for_rail(skill_name)
            if not skill_root:
                logger.warning(
                    "[SkillProtocolPromptRail] after_tool_call: skill_root not found for %s",
                    skill_name,
                )
                return
            face = discover_turbo_face(skill_root)
            if face is None:
                return
            body = load_skill_turbo_body(face.turbo_dir)
        except Exception as exc:
            logger.warning(
                "[SkillProtocolPromptRail] after_tool_call load SKILL_TURBO.md failed: %s", exc,
            )
            return

        # record_active_skill_body（层2 正文钉入上下文窗口）
        turbo_name = face.turbo_name if face else f"{skill_name}_turbo"
        session = _resolve_session_for_rail(ctx)
        tool_call_id = ""
        tool_call = getattr(inputs, "tool_call", None)
        if tool_call is not None:
            tool_call_id = str(getattr(tool_call, "id", "") or "")
        _record_turbo_active_body(session, turbo_name, body, tool_call_id)


def _is_activate_success(tool_result: Any) -> bool:
    """判断 skill_turbo_tool activate 调用是否成功。"""
    if tool_result is None:
        return False
    # tool_result 可能是 ToolOutput / dict
    data = getattr(tool_result, "data", None)
    if isinstance(data, dict):
        return bool(data.get("success")) and data.get("mode") == "activate"
    if isinstance(tool_result, dict):
        return bool(tool_result.get("success")) and tool_result.get("mode") == "activate"
    return False


def _resolve_session_for_rail(ctx: AgentCallbackContext) -> Any:
    """从 ctx 解析 session（与 SkillUseRail.after_tool_call 同范式）。"""
    session = getattr(ctx, "session", None)
    if session is not None:
        return session
    context = getattr(ctx, "context", None)
    if context is not None:
        getter = getattr(context, "get_session_ref", None)
        if callable(getter):
            try:
                return getter()
            except Exception:
                return None
    return None


def _resolve_skill_root_for_rail(skill_name: str) -> str:
    """从共享 skill registry 解析指定 skill 的根目录。"""
    try:
        from jiuwenclaw.utils import get_agent_registered_skill_dirs
        from pathlib import Path
        skill_dirs = get_agent_registered_skill_dirs()
        for d in skill_dirs:
            candidate = d / skill_name
            if candidate.is_dir():
                return str(candidate.resolve())
            if d.name == skill_name and d.is_dir():
                return str(d.resolve())
    except Exception as exc:
        logger.debug("[SkillProtocolPromptRail] resolve skill_root failed: %s", exc)
    return ""


__all__ = ["SkillProtocolPromptRail"]
