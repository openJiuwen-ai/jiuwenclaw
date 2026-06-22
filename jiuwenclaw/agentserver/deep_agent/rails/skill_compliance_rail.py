# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""SkillComplianceRail — lightweight SKILL execution state rail.

After removing the legacy step-planning toolchain, this rail only tracks whether
an SKILL.md body is active for the current session. It no longer reads or writes
any step-plan file and no longer exposes intermediate planning phases.

Phases (per session):
    IDLE    — no SKILL active
    ACTIVE  — SKILL.md loaded and being followed until skill_complete

The rail still keeps the useful safeguards from the old implementation:
- read the real SKILL.md body from ``active_skill_bodies`` when SkillUseRail stubs
  the tool message;
- parse bash blocks for mcp_exec_command failure recovery;
- detect obvious model-text step skips;
- reset the active skill on ``skill_complete`` or after repeated no-tool turns.
"""

from __future__ import annotations

import contextvars
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, List, Optional

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.base import DeepAgentRail

from jiuwenclaw.config import get_config
from jiuwenclaw.utils import logger


_BASH_BLOCK_RE = re.compile(r'```bash\s*\n(.*?)```', re.DOTALL)
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

_NO_TOOL_DEACTIVATE_THRESHOLD = 2
_DEFAULT_SESSION_ID = "default"

_current_session_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "skill_compliance_session_id", default=None,
)


class SkillPhase(str, Enum):
    IDLE = "idle"
    ACTIVE = "active"


@dataclass
class _SkillSessionState:
    phase: SkillPhase = SkillPhase.IDLE
    active_skill: Optional[str] = None
    active_skill_content: Optional[str] = None
    skill_bash_commands: List[str] = field(default_factory=list)
    last_declared_step: Optional[int] = None
    skip_warned: bool = False
    pending_skip_warning: Optional[str] = None
    no_tool_invoke_count: int = 0

    def reset(self) -> None:
        self.__init__()


_sessions: dict[str, _SkillSessionState] = {}


def _get_or_create_state(session_id: str) -> _SkillSessionState:
    state = _sessions.get(session_id)
    if state is None:
        state = _SkillSessionState()
        _sessions[session_id] = state
    return state


def get_session_phase(session_id: str) -> SkillPhase:
    """Public read-side retained for compatibility with old imports."""
    if not session_id:
        return SkillPhase.IDLE
    state = _sessions.get(session_id)
    return state.phase if state is not None else SkillPhase.IDLE


def get_session_active_skill(session_id: str) -> Optional[str]:
    """Public read-side retained for compatibility with old imports."""
    if not session_id:
        return None
    state = _sessions.get(session_id)
    return state.active_skill if state is not None else None


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


def _read_skill_body_from_session(
    ctx: AgentCallbackContext, skill_name: str, relative_file_path: str,
) -> Optional[str]:
    """Read the full SKILL.md body recorded by openjiuwen SkillUseRail."""
    try:
        from openjiuwen.core.context_engine.active_skill_bodies import (
            ACTIVE_SKILL_BODIES_STATE_KEY,
            _state_key,
            normalize_skill_relative_file_path,
        )
        ctx_model = getattr(ctx, "context", None)
        session = getattr(ctx_model, "_session_ref", None) if ctx_model is not None else None
        if session is None:
            return None
        active = session.get_state(ACTIVE_SKILL_BODIES_STATE_KEY) or {}
        if not isinstance(active, dict):
            return None
        key = _state_key(skill_name, normalize_skill_relative_file_path(relative_file_path))
        entry = active.get(key)
        if not isinstance(entry, dict):
            return None
        body = entry.get("body")
        return body if isinstance(body, str) and body else None
    except Exception as exc:
        logger.debug("[SkillComplianceRail] read body from session failed: %s", exc)
        return None


def _build_load_directive(lang: str, skill_name: str) -> str:
    if lang == "zh":
        return (
            f"\n\n[Skill {skill_name} 已激活] 请严格按照 SKILL.md 的步骤顺序执行。"
            "每次行动前声明当前步骤；遇到用户确认/审批点必须等待用户回复；"
            f"完成整个技能流程后调用 `skill_complete(skill_name=\"{skill_name}\")` 收尾。\n"
        )
    return (
        f"\n\n[Skill {skill_name} active] Follow SKILL.md in order. "
        "Declare the current step before each action, wait at user approval gates, "
        f"and call `skill_complete(skill_name=\"{skill_name}\")` when the full skill flow is done.\n"
    )


class SkillComplianceRail(DeepAgentRail):
    """Lightweight SKILL execution state rail (per session)."""

    priority = 30

    def __init__(self, session_id: Optional[str] = None, skill_dir_resolver: Optional[Any] = None) -> None:
        super().__init__()
        self._preset_session_id: Optional[str] = session_id
        self._skill_dir_resolver = skill_dir_resolver

    def _resolve_session_id(self, ctx: AgentCallbackContext) -> str:
        return (
            self._preset_session_id
            or _extract_session_id(ctx)
            or _current_session_var.get()
            or _DEFAULT_SESSION_ID
        )

    def _resolve_skill_dir(self, skill_name: str) -> Optional[str]:
        """Resolve skill directory via the injected resolver callable."""
        if self._skill_dir_resolver is None:
            return None
        try:
            skills = self._skill_dir_resolver()
            if not skills:
                return None
            for skill in skills:
                if getattr(skill, "name", None) == skill_name:
                    directory = getattr(skill, "directory", None)
                    return str(directory) if directory is not None else None
        except Exception as exc:
            logger.warning("[SkillComplianceRail] skill_dir_resolver failed: %s", exc)
        return None

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

    def _check_step_skip(self, state: _SkillSessionState, ai_content: str) -> None:
        if state.phase == SkillPhase.IDLE or not ai_content:
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
                            "请确认 SKILL.md 是否允许跳过这些步骤。"
                            "如果不允许，请立即回退执行被跳过的步骤。"
                        )
                    else:
                        state.pending_skip_warning = (
                            f"⚠️ You jumped from Stage {state.last_declared_step} "
                            f"to Stage {current}, skipping {skipped}. "
                            "Verify SKILL.md allows skipping these. If not, go back and execute them now."
                        )
                    logger.warning(
                        "[SkillComplianceRail] step skip warned %s -> %s (skipped %s)",
                        state.last_declared_step, current, skipped,
                    )
                    return
                if lang == "zh":
                    state.pending_skip_warning = (
                        f"[步骤跳跃拦截] 已警告过但你仍然跳过了 {skipped}。\n"
                        "请重新阅读 SKILL.md，从被跳过的步骤开始执行。"
                    )
                else:
                    state.pending_skip_warning = (
                        f"[Step skip blocked] You were warned but still skipped {skipped}.\n"
                        "Re-read SKILL.md and execute the skipped stages."
                    )
                logger.warning(
                    "[SkillComplianceRail] step skip blocked (post-warning) %s -> %s",
                    state.last_declared_step, current,
                )
                return

        state.skip_warned = False
        state.pending_skip_warning = None
        state.last_declared_step = current

    def _activate_skill(
        self, state: _SkillSessionState, skill_name: str,
        skill_content: str, tool_msg: Any, session_id: str,
    ) -> None:
        state.phase = SkillPhase.ACTIVE
        state.active_skill = skill_name
        state.active_skill_content = skill_content
        state.skill_bash_commands = _parse_skill_bash_commands(skill_content)
        state.last_declared_step = None
        state.skip_warned = False
        state.pending_skip_warning = None
        state.no_tool_invoke_count = 0
        logger.info(
            "[SkillComplianceRail] activated '%s' phase=%s bash=%d session=%s",
            skill_name, state.phase.value, len(state.skill_bash_commands), session_id,
        )
        tool_msg.content = _str_content(tool_msg) + _build_load_directive(_resolve_lang(), skill_name)

    def _handle_skill_complete(
        self, state: _SkillSessionState, skill_name: str, session_id: str,
    ) -> None:
        if not skill_name or state.active_skill != skill_name:
            return
        logger.info(
            "[SkillComplianceRail] skill_complete(%s) session=%s -> IDLE",
            skill_name, session_id,
        )
        state.reset()

    def _handle_tool_event(
        self, state: _SkillSessionState, tc: Any, tool_msg: Any,
        tool_name: str, session_id: str,
        *, ctx: Optional[AgentCallbackContext] = None,
    ) -> None:
        if not tool_name:
            return

        if tool_name == "skill_complete":
            skill_name = (self._get_arg(tc, "skill_name", "") or "").strip()
            self._handle_skill_complete(state, skill_name, session_id)
            return

        if tool_name == "skill_tool":
            meta = getattr(tool_msg, "metadata", None) or {}
            if not (meta.get("is_skill_body") or meta.get("original_is_skill_body")):
                return
            skill_name = (
                meta.get("skill_name") or self._get_arg(tc, "skill_name", "") or ""
            ).strip()
            if not skill_name:
                return
            rel_path = str(meta.get("relative_file_path") or "SKILL.md")
            body = _read_skill_body_from_session(ctx, skill_name, rel_path) or _str_content(tool_msg)
            if not body.strip():
                return
            self._activate_skill(state, skill_name, body, tool_msg, session_id)
            return

    def _detect_script_failure(
        self, state: _SkillSessionState, tc: Any, tool_msg: Any,
    ) -> None:
        if state.phase == SkillPhase.IDLE or not state.skill_bash_commands:
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
                f"\n\n[SKILL 脚本失败] 禁止自行决定跳过该步骤或后续步骤；"
                f"必须先尝试修复（如安装缺失依赖、修正参数）后重试 `{matching_cmd}`，"
                "修复失败则询问用户如何处理，等待用户指示后再继续。"
            )
        else:
            recovery = (
                "\n\n[SKILL script failed] Do NOT skip this step or any subsequent step on your own. "
                "First attempt to fix the issue (e.g. install missing dependencies, correct parameters) "
                f"and retry `{matching_cmd}`. If the fix fails, ask the user how to proceed and wait."
            )
        tool_msg.content = content + recovery
        logger.info(
            "[SkillComplianceRail] script failure detected, injected recovery for '%s'",
            matching_cmd,
        )

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        session_id = self._resolve_session_id(ctx)
        _current_session_var.set(session_id)
        state = _get_or_create_state(session_id)
        if state.phase != SkillPhase.IDLE and state.no_tool_invoke_count >= _NO_TOOL_DEACTIVATE_THRESHOLD:
            logger.info(
                "[SkillComplianceRail] deactivating '%s' (session=%s) after %d no-tool invokes",
                state.active_skill, session_id, state.no_tool_invoke_count,
            )
            state.reset()

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Trigger BEFORE_SKILL_EXECUTE hook event when skill_tool is about to execute."""
        inputs = getattr(ctx, "inputs", None)
        if inputs is None:
            return
        tool_name = getattr(inputs, "tool_name", "") or ""
        if tool_name != "skill_tool":
            return

        tool_args = getattr(inputs, "tool_args", None) or {}
        skill_name = str(tool_args.get("skill_name", "") or "").strip()
        if not skill_name:
            return

        skill_dir = self._resolve_skill_dir(skill_name)
        if not skill_dir:
            return

        try:
            from jiuwenclaw.extensions.registry import ExtensionRegistry
            from jiuwenclaw.schema import AgentServerHookEvents
            from jiuwenclaw.schema.hooks_context import BeforeSkillExecuteHookContext

            hook_ctx = BeforeSkillExecuteHookContext(
                skill_name=skill_name,
                skill_dir=skill_dir,
                session_id=self._resolve_session_id(ctx),
            )
            await ExtensionRegistry.get_instance().trigger(
                AgentServerHookEvents.BEFORE_SKILL_EXECUTE, hook_ctx
            )
        except Exception as exc:
            logger.warning(
                "[SkillComplianceRail] BEFORE_SKILL_EXECUTE handler failed: %s", exc
            )

    async def after_model_call(self, ctx: AgentCallbackContext) -> None:
        inputs = getattr(ctx, "inputs", None)
        response = getattr(inputs, "response", None) if inputs else None
        if response is None:
            return

        state = _get_or_create_state(self._resolve_session_id(ctx))
        content = getattr(response, "content", "") or ""
        if not isinstance(content, str):
            content = str(content)

        try:
            self._check_step_skip(state, content)
        except Exception as exc:
            logger.warning("[SkillComplianceRail] step skip check failed: %s", exc)

        if state.phase != SkillPhase.IDLE:
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
        state = _get_or_create_state(session_id)

        if tool_name == "skill_complete" and "SKILL_COMPLETE_BLOCKED" in _str_content(tool_msg):
            return

        try:
            self._handle_tool_event(
                state, tool_call, tool_msg, tool_name, session_id, ctx=ctx,
            )
        except Exception as exc:
            logger.warning("[SkillComplianceRail] handle tool event failed: %s", exc)

        if state.pending_skip_warning:
            tool_msg.content = _str_content(tool_msg) + f"\n{state.pending_skip_warning}"
            state.pending_skip_warning = None

        try:
            self._detect_script_failure(state, tool_call, tool_msg)
        except Exception as exc:
            logger.warning("[SkillComplianceRail] script failure detect failed: %s", exc)


__all__ = [
    "SkillComplianceRail",
    "SkillPhase",
    "get_session_phase",
    "get_session_active_skill",
]
