# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""SkillComplianceRail — Skill 执行合规检测 + 步骤跳跃警告 + 脚本失败恢复。

按 conversation_id 分桶管理 session 状态(多会话并发隔离)。三条钩子:
- before_invoke: 记录 session_id;连续两次 no-tool 调用后卸载 active skill
- after_model_call: 扫 LLM 回复识别 [当前步骤: Stage N] 步骤跳跃
- after_tool_call:  view_file/file_read/*_load_skill 命中 SKILL.md 时激活;
                    追加 todo 合规提醒;mcp_exec_command 失败时注入恢复指引

硬拦截通过把强警示拼到下一轮 tool_msg 上实现(DeepAgent 无 continue 重跑 LLM 的原语)。
"""

from __future__ import annotations

import contextvars
import re
from dataclasses import dataclass, field
from typing import Any, List, Optional

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.base import DeepAgentRail

from jiuwenclaw.config import get_config
from jiuwenclaw.utils import logger


_BASH_BLOCK_RE = re.compile(r'```bash\s*\n(.*?)```', re.DOTALL)
_SKILL_MD_RE = re.compile(r"[/\\]([^/\\]+)[/\\]SKILL\.md", re.IGNORECASE)
_STEP_DECL_RE = re.compile(r'\[(?:当前步骤|[Cc]urrent\s*[Ss]tep)[：:]\s*(.+?)\]')
_STAGE_NUM_RE = re.compile(r'[Ss]tage\s*(\d+)|阶段\s*(\d+)|[Ss]tep\s*(\d+)')

_FAILURE_INDICATORS_RE = re.compile(
    r'"exit_code"\s*:\s*[1-9]'
    r'|ModuleNotFoundError|No module named|ImportError'
    r'|not (?:found|installed)|library is missing'
    r'|\[ERROR\]',
    re.IGNORECASE,
)
_PY_SCRIPT_RE = re.compile(r'([\w][\w.-]*\.py)\b')

_SKILL_STEP_TOOL_NAMES = frozenset({
    "skill_step_create", "skill_step_complete", "skill_step_insert",
    "skill_step_remove", "skill_step_list",
})

_NO_TOOL_DEACTIVATE_THRESHOLD = 2
_DEFAULT_SESSION_ID = "default"

_current_session_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "skill_compliance_session_id", default=None,
)


@dataclass
class _SkillSessionState:
    active_skill: Optional[str] = None
    active_skill_content: Optional[str] = None
    skill_tool_count: int = 0
    last_declared_step: Optional[int] = None
    skip_warned: bool = False
    pending_skip_warning: Optional[str] = None
    no_tool_invoke_count: int = 0
    skill_bash_commands: List[str] = field(default_factory=list)

    def reset(self) -> None:
        self.__init__()


def _parse_skill_bash_commands(skill_md_text: str) -> List[str]:
    commands: List[str] = []
    for block in _BASH_BLOCK_RE.findall(skill_md_text):
        for raw in block.strip().splitlines():
            line = raw.strip()
            if line and not line.startswith('#'):
                commands.append(line)
    return commands


def _resolve_lang() -> str:
    cfg = get_config() if callable(get_config) else {}
    lang = cfg.get("preferred_language", "zh") if isinstance(cfg, dict) else "zh"
    return "zh" if lang in ("zh", "cn") else "en"


def _str_content(msg: Any) -> str:
    c = getattr(msg, "content", "")
    return c if isinstance(c, str) else str(c)


def _extract_session_id(ctx: AgentCallbackContext) -> Optional[str]:
    inputs = getattr(ctx, "inputs", None)
    if inputs is None:
        return None
    conv_id = getattr(inputs, "conversation_id", None)
    return str(conv_id) if conv_id else None


class SkillComplianceRail(DeepAgentRail):
    """Skill 执行合规检测状态机(按 session 分桶)。"""

    priority = 30

    def __init__(self, session_id: Optional[str] = None) -> None:
        super().__init__()
        # team 场景由 build_member_rails 预绑定;主 agent 从 ctx 解析。
        self._preset_session_id: Optional[str] = session_id
        self._sessions: dict[str, _SkillSessionState] = {}

    def _get_state(self, session_id: str) -> _SkillSessionState:
        state = self._sessions.get(session_id)
        if state is None:
            state = _SkillSessionState()
            self._sessions[session_id] = state
        return state

    def _resolve_session_id(self, ctx: AgentCallbackContext) -> str:
        return (
            self._preset_session_id
            or _extract_session_id(ctx)
            or _current_session_var.get()
            or _DEFAULT_SESSION_ID
        )

    def _resolve_todo_file_path(self, session_id: str) -> Optional[str]:
        try:
            from jiuwenclaw.agentserver.tools.todo_toolkits import SkillStepToolkit
            _, path = SkillStepToolkit(session_id=session_id).resolve_todo_path()
            return str(path)
        except Exception as exc:
            logger.debug("[SkillComplianceRail] resolve_todo_file_path failed: %s", exc)
            return None

    def _activate_skill(
        self, state: _SkillSessionState, skill_name: str,
        skill_content: str, tool_msg: Any, session_id: str,
    ) -> None:
        state.active_skill = skill_name
        state.active_skill_content = skill_content
        state.skill_tool_count = 0
        state.last_declared_step = None
        state.skip_warned = False
        state.pending_skip_warning = None
        state.skill_bash_commands = _parse_skill_bash_commands(skill_content)
        logger.info(
            "[SkillComplianceRail] now tracking '%s' (%d bash commands)",
            skill_name, len(state.skill_bash_commands),
        )

        lang = _resolve_lang()
        todo_path = self._resolve_todo_file_path(session_id)
        if lang == "zh":
            path_line = f"\nSkill 步骤追踪文件：{todo_path}\n" if todo_path else ""
            directive = (
                "\n\n[技能文档已加载] 如果你要执行此技能，请先调用 skill_step_create 为文档中定义的每个步骤创建 skill_step 项。"
                "如果只是查阅信息则无需创建。\n"
                "⚠️ 每完成一个步骤/子任务后，必须立即调用 skill_step_complete 标记完成（并填写 result 摘要），"
                "再开始下一项；禁止跳过 complete 直接推进。可随时用 skill_step_list 查看进度。"
                f"{path_line}"
                "⚠️ Skill 脚本执行原则：SKILL.md 中定义的脚本必须按原样执行，"
                "禁止自行编写代码替代其功能。脚本失败时应修复执行环境（如安装依赖）后重试原脚本。"
            )
        else:
            path_line = f"\nSkill step tracking file: {todo_path}\n" if todo_path else ""
            directive = (
                "\n\n[Skill document loaded] If you intend to execute this skill, "
                "call skill_step_create first with one skill_step item per step in SKILL.md. "
                "If you are only reading for reference, no action needed.\n"
                "After each step/sub-task, you MUST immediately call skill_step_complete "
                "(with a short result) before moving on. Never advance without marking the "
                "current item complete. Use skill_step_list anytime to inspect progress."
                f"{path_line}"
                "Script execution principle: Scripts defined in SKILL.md must be executed as specified. "
                "Do NOT write your own code to replace their functionality. "
                "On script failure, fix the environment (e.g., install dependencies) and retry the original script."
            )
        tool_msg.content = _str_content(tool_msg) + directive

    def _check_step_skip(self, state: _SkillSessionState, ai_content: str) -> None:
        if not state.active_skill or not ai_content:
            return
        m = _STEP_DECL_RE.search(ai_content)
        if not m:
            return
        nm = _STAGE_NUM_RE.search(m.group(1))
        if not nm:
            return

        current = int(next(g for g in nm.groups() if g is not None))
        lang = _resolve_lang()

        if state.last_declared_step is not None:
            gap = current - state.last_declared_step
            if gap > 1:
                skipped = ", ".join(
                    f"Stage {state.last_declared_step + i}" for i in range(1, gap)
                )
                if not state.skip_warned:
                    state.skip_warned = True
                    if lang == "zh":
                        state.pending_skip_warning = (
                            f"⚠️ 你从 Stage {state.last_declared_step} "
                            f"跳到了 Stage {current}，跳过了 {skipped}。"
                            f"请确认 SKILL.md 是否允许跳过这些步骤。"
                            f"如果不允许，请立即回退执行被跳过的步骤。"
                        )
                    else:
                        state.pending_skip_warning = (
                            f"⚠️ You jumped from Stage {state.last_declared_step} "
                            f"to Stage {current}, skipping {skipped}. "
                            f"Verify SKILL.md allows skipping these. If not, go back and execute them now."
                        )
                    logger.warning(
                        "[SkillComplianceRail] step skip warned %s -> %s (skipped %s)",
                        state.last_declared_step, current, skipped,
                    )
                    return
                if lang == "zh":
                    state.pending_skip_warning = (
                        f"[步骤跳跃拦截] 已警告过但你仍然跳过了 {skipped}。\n"
                        f"请重新阅读 SKILL.md，从被跳过的步骤开始执行。"
                    )
                else:
                    state.pending_skip_warning = (
                        f"[Step skip blocked] You were warned but still skipped {skipped}.\n"
                        f"Re-read SKILL.md and execute the skipped stages."
                    )
                logger.warning(
                    "[SkillComplianceRail] step skip blocked (post-warning) %s -> %s",
                    state.last_declared_step, current,
                )
                return

        state.skip_warned = False
        state.pending_skip_warning = None
        state.last_declared_step = current

    def _maybe_track_active_skill(
        self, state: _SkillSessionState, tc: Any, tool_msg: Any, session_id: str,
    ) -> None:
        tool_name = getattr(tc, "name", "")
        if not tool_name:
            return

        if tool_name in ("view_file", "file_read"):
            file_path = self._get_arg(tc, "file_path", default="")
            if not isinstance(file_path, str):
                file_path = str(file_path)
            m = _SKILL_MD_RE.search(file_path)
            if m:
                self._activate_skill(
                    state, m.group(1), _str_content(tool_msg), tool_msg, session_id,
                )
            return

        if tool_name.endswith("load_skill"):
            import json as _json
            raw = _str_content(tool_msg)
            try:
                try:
                    payload = _json.loads(raw)
                except (ValueError, TypeError):
                    import ast
                    payload = ast.literal_eval(raw)
                if isinstance(payload, dict) and isinstance(payload.get("result"), str):
                    payload = _json.loads(payload["result"])
                if isinstance(payload, dict):
                    skill_name = payload.get("name", "")
                    skill_md = payload.get("skillMarkdown", "")
                    if skill_name and skill_md:
                        self._activate_skill(state, skill_name, skill_md, tool_msg, session_id)
            except Exception as exc:
                logger.debug(
                    "[SkillComplianceRail] load_skill payload parse failed: %s", exc,
                )

    @staticmethod
    def _get_arg(tc: Any, key: str, default: Any = None) -> Any:
        args = getattr(tc, "arguments", None)
        if isinstance(args, dict):
            return args.get(key, default)
        if isinstance(args, str):
            try:
                import json as _json
                parsed = _json.loads(args)
                if isinstance(parsed, dict):
                    return parsed.get(key, default)
            except Exception as exc:
                logger.debug("[SkillComplianceRail] parse tc.arguments failed: %s", exc)
        return default

    def _get_todo_summary(self, session_id: str) -> dict:
        if not session_id:
            return {"state": "no_session"}
        try:
            from jiuwenclaw.agentserver.tools.todo_toolkits import SkillStepToolkit, TaskStatus
            tasks = SkillStepToolkit(session_id=session_id).load_tasks()
        except Exception as exc:
            logger.debug("[SkillComplianceRail] load skill_step tasks failed: %s", exc)
            return {"state": "error"}

        if not tasks:
            return {"state": "no_todos"}

        completed = [t for t in tasks if t.status == TaskStatus.COMPLETED]
        waiting = [t for t in tasks if t.status != TaskStatus.COMPLETED]
        return {
            "state": "all_done" if not waiting else "in_progress",
            "total": len(tasks),
            "completed_count": len(completed),
            "next_task_name": waiting[0].tasks if waiting else None,
        }

    def _maybe_inject_skill_compliance(
        self, state: _SkillSessionState, session_id: str,
        tool_msg: Any, tool_name: str,
    ) -> None:
        if not state.active_skill or tool_name in _SKILL_STEP_TOOL_NAMES:
            return

        state.skill_tool_count += 1
        lang = _resolve_lang()
        summary = self._get_todo_summary(session_id)
        summary_state = summary.get("state", "error")

        if summary_state == "no_todos":
            if lang == "zh":
                suffix = (
                    f"\n\n[技能 {state.active_skill}] 尚未创建 skill_step 列表。"
                    f"请立即调用 skill_step_create 为 SKILL.md 中的每个步骤创建 skill_step 项。"
                )
            else:
                suffix = (
                    f"\n\n[Skill {state.active_skill}] No skill_step list found. "
                    f"Call skill_step_create now with one item per step in SKILL.md."
                )
        elif summary_state == "in_progress":
            total = summary["total"]
            done = summary["completed_count"]
            next_name = summary.get("next_task_name", "?")
            todo_path = self._resolve_todo_file_path(session_id)
            if lang == "zh":
                path_line = f"\nSkill 步骤追踪文件：{todo_path}\n" if todo_path else ""
                suffix = (
                    f"\n\n[技能 {state.active_skill} · 进度: {done}/{total}]"
                    f"{path_line}"
                    f"当前步骤: '{next_name}'\n"
                    f"开始执行前，必须先用 skill_step_insert 将其拆解为原子级子步骤"
                    f"——每个 skill_step 项应对应单一、可独立验证的操作，不可再拆才算合格。\n"
                    f"⚠️ 每完成一项（含拆出来的子步骤），立即调用 skill_step_complete 标记完成并写 result 摘要，"
                    f"再开始下一项；禁止跳过 complete 直接推进。\n"
                    f"⚠️ 只执行当前 skill_step 项，禁止为了效率而合并或批量执行多个步骤。\n"
                    f"⚠️ SKILL.md 中定义的选项、参数、标签必须原样使用，禁止自行增删改。"
                )
            else:
                path_line = f"\nSkill step tracking file: {todo_path}\n" if todo_path else ""
                suffix = (
                    f"\n\n[Skill {state.active_skill} · Progress: {done}/{total}]"
                    f"{path_line}"
                    f"Current step: '{next_name}'\n"
                    f"Before starting, use skill_step_insert to break it into atomic sub-steps"
                    f"—each skill_step should be a single, independently verifiable action.\n"
                    f"⚠️ After each item (including atomic sub-steps), immediately call "
                    f"skill_step_complete with a short result summary before moving on. "
                    f"Never advance without marking the current item complete.\n"
                    f"⚠️ Only execute the current skill_step item. "
                    f"Do NOT batch or merge multiple steps for efficiency.\n"
                    f"⚠️ Options, parameters, and labels in SKILL.md must be used verbatim."
                )
        elif summary_state == "all_done":
            if lang == "zh":
                suffix = f"\n\n[技能 {state.active_skill}] 所有 {summary['total']} 个 skill_step 项已完成。"
            else:
                suffix = f"\n\n[Skill {state.active_skill}] All {summary['total']} skill_step items completed."
        else:
            suffix = ""

        if state.pending_skip_warning:
            suffix += f"\n{state.pending_skip_warning}"
            state.pending_skip_warning = None

        if suffix:
            tool_msg.content = _str_content(tool_msg) + suffix

    def _detect_script_failure(
        self, state: _SkillSessionState, tc: Any, tool_msg: Any,
    ) -> None:
        if not state.active_skill or not state.skill_bash_commands:
            return
        if getattr(tc, "name", "") != "mcp_exec_command":
            return
        content = _str_content(tool_msg)
        if not _FAILURE_INDICATORS_RE.search(content):
            return

        command = self._get_arg(tc, "command", default="")
        if not isinstance(command, str):
            command = str(command)
        if not command:
            return

        matching_cmd: Optional[str] = None
        for cmd in state.skill_bash_commands:
            if any(s in command for s in _PY_SCRIPT_RE.findall(cmd)):
                matching_cmd = cmd
                break
        if not matching_cmd:
            return

        lang = _resolve_lang()
        if lang == "zh":
            recovery = (
                f"\n\n[脚本执行失败 · 恢复指引]\n"
                f"SKILL.md 指定的脚本执行失败。请严格按以下步骤恢复：\n"
                f"1. 分析上方错误信息，判断失败原因（缺少依赖/路径错误/其他）\n"
                f"2. 使用 mcp_exec_command 修复问题（如 pip install 缺失的库）\n"
                f"3. 使用 mcp_exec_command 重新执行原始命令：\n"
                f"   {matching_cmd}\n"
                f"⚠️ 禁止使用 execute_python_code 自行编写代码替代该脚本。"
            )
        else:
            recovery = (
                f"\n\n[Script Failure · Recovery Guide]\n"
                f"A SKILL.md-designated script failed. Follow these steps:\n"
                f"1. Analyze the error above to determine the cause\n"
                f"2. Fix the issue via mcp_exec_command (e.g., pip install missing library)\n"
                f"3. Re-execute the original command via mcp_exec_command:\n"
                f"   {matching_cmd}\n"
                f"Do NOT use execute_python_code to rewrite the script's logic."
            )
        tool_msg.content = content + recovery
        logger.info(
            "[SkillComplianceRail] script failure detected, injected recovery for '%s'",
            matching_cmd,
        )

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        session_id = self._resolve_session_id(ctx)
        _current_session_var.set(session_id)
        state = self._get_state(session_id)
        if state.active_skill and state.no_tool_invoke_count >= _NO_TOOL_DEACTIVATE_THRESHOLD:
            logger.info(
                "[SkillComplianceRail] deactivating '%s' (session=%s) after %d no-tool invokes",
                state.active_skill, session_id, state.no_tool_invoke_count,
            )
            state.reset()

    async def after_model_call(self, ctx: AgentCallbackContext) -> None:
        inputs = getattr(ctx, "inputs", None)
        response = getattr(inputs, "response", None) if inputs else None
        if response is None:
            return

        state = self._get_state(self._resolve_session_id(ctx))
        content = getattr(response, "content", "") or ""
        if not isinstance(content, str):
            content = str(content)

        try:
            self._check_step_skip(state, content)
        except Exception as exc:
            logger.warning("[SkillComplianceRail] step skip check failed: %s", exc)

        if state.active_skill:
            if getattr(response, "tool_calls", None):
                state.no_tool_invoke_count = 0
            else:
                state.no_tool_invoke_count += 1

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        inputs = getattr(ctx, "inputs", None)
        if inputs is None:
            return
        tool_msg = getattr(inputs, "tool_msg", None)
        tool_call = getattr(inputs, "tool_call", None)
        if tool_msg is None or tool_call is None:
            return
        tool_name = getattr(inputs, "tool_name", "") or ""

        session_id = self._resolve_session_id(ctx)
        state = self._get_state(session_id)

        try:
            self._maybe_track_active_skill(state, tool_call, tool_msg, session_id)
        except Exception as exc:
            logger.warning("[SkillComplianceRail] track skill failed: %s", exc)

        try:
            self._maybe_inject_skill_compliance(state, session_id, tool_msg, tool_name)
        except Exception as exc:
            logger.warning("[SkillComplianceRail] inject compliance failed: %s", exc)

        try:
            self._detect_script_failure(state, tool_call, tool_msg)
        except Exception as exc:
            logger.warning("[SkillComplianceRail] script failure detect failed: %s", exc)


__all__ = ["SkillComplianceRail"]
