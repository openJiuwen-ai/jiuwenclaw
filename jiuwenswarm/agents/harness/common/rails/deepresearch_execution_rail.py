# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Native HITL bridge for the high-level DeepResearch execution tool."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Mapping

from openjiuwen.core.single_agent.interrupt.exception import ToolInterruptException
from openjiuwen.core.single_agent.interrupt.state import RESUME_USER_INPUT_KEY
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.harness.rails.interrupt.ask_user_rail import AskUserRequest

from jiuwenswarm.agents.harness.common.tools.deepresearch.execution import (
    EXECUTION_SCHEMA,
    bind_deepresearch_execution_context,
    reset_deepresearch_execution_context,
)
from jiuwenswarm.common.schema.ask_user import ask_user_response_schema
from jiuwenswarm.common.utils import logger
from jiuwenswarm.perf.context import (
    extract_session_id_from_callback,
    get_request_context,
)

DEEPRESEARCH_EXECUTION_STATE_KEY = "deepresearch_execution_states"
DEEPRESEARCH_EXECUTION_ALIAS_KEY = "deepresearch_execution_aliases"
_TOKENS_KEY = "__deepresearch_execution_context_tokens__"
_TOOL_NAME = "deepresearch_execute"
_TODO_DONE = frozenset({"completed", "cancelled"})
_CURRENT_RESEARCH_TODO_IDS = frozenset({"deepresearch", "todo:deepresearch"})
_FOLLOWUP_HANDOFF_BODY = (
    "深度研究工具已成功结束。完整研究报告已通过文件交付，无需再向用户确认研究方向。"
)


def _todo_item_id(item: Mapping[str, Any]) -> str:
    return str(item.get("id") or item.get("task_id") or "").strip()


def _is_current_research_todo(task_id: str) -> bool:
    if task_id in _CURRENT_RESEARCH_TODO_IDS:
        return True
    if task_id.startswith("deepresearch_stage_"):
        return True
    return task_id.endswith(":deepresearch")


def _session_id_from_ctx(ctx: AgentCallbackContext) -> str:
    session = ctx.session
    if session is None:
        return ""
    get_sid = getattr(session, "get_session_id", None)
    if not callable(get_sid):
        return ""
    try:
        return str(get_sid() or "").strip()
    except Exception:
        return ""


def _todo_json_path(ctx: AgentCallbackContext) -> Path | None:
    """Resolve outer harness todo.json; prefer TaskExecutionRail workspace."""
    session_id = _session_id_from_ctx(ctx)
    if not session_id:
        return None
    agent = getattr(ctx, "agent", None)
    try:
        from jiuwenswarm.agents.harness.common.rails.task_execution_rail import (  # noqa: PLC0415
            TaskExecutionRail,
        )

        rail = TaskExecutionRail()
        rail.init(agent)
        path = rail._get_todo_workspace_path(session_id)
        if path is not None:
            return path
    except Exception:
        logger.debug(
            "[DeepResearchExecutionRail] todo path via TaskExecutionRail failed",
            exc_info=True,
        )
    try:
        from jiuwenswarm.agents.harness.common.tools.deepresearch.todo_progress import (  # noqa: PLC0415
            deepresearch_todo_path,
        )

        return deepresearch_todo_path(session_id=session_id)
    except Exception:
        logger.debug(
            "[DeepResearchExecutionRail] todo path via deepresearch workspace failed",
            exc_info=True,
        )
        return None


def _load_todo_items(ctx: AgentCallbackContext) -> list[dict[str, Any]]:
    path = _todo_json_path(ctx)
    if path is None or not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.debug(
            "[DeepResearchExecutionRail] failed to read todo.json path=%s",
            path,
            exc_info=True,
        )
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _save_todo_items(ctx: AgentCallbackContext, items: list[dict[str, Any]]) -> bool:
    path = _todo_json_path(ctx)
    if path is None:
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(items, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return True
    except OSError:
        logger.warning(
            "[DeepResearchExecutionRail] failed to write todo.json path=%s",
            path,
            exc_info=True,
        )
        return False


def _has_followup_todos(ctx: AgentCallbackContext) -> bool:
    """True when the outer plan still has work after DeepResearch returns."""
    for item in _load_todo_items(ctx):
        status = str(item.get("status") or "pending").strip().lower()
        if status in _TODO_DONE:
            continue
        if _is_current_research_todo(_todo_item_id(item)):
            continue
        return True
    return False


def _followup_todo_label(item: Mapping[str, Any] | None) -> str:
    if not item:
        return ""
    task_id = _todo_item_id(item)
    subject = str(item.get("content") or item.get("subject") or "").strip()
    if task_id and subject:
        return f"{task_id}: {subject}"
    return task_id or subject


def _compact_followup_tool_content(
    next_followup: Mapping[str, Any] | None,
) -> str:
    """Drop the report body and point the model at the next outer todo.

    The full report is already delivered via chat.file. Replaying a research
    summary in the tool message caused the model to re-clarify instead of
    continuing the remaining plan.
    """
    label = _followup_todo_label(next_followup)
    next_step = (
        f"下一步立即执行外层待办「{label}」。"
        if label
        else "下一步立即执行尚未完成的外层待办。"
    )
    return (
        f"{_FOLLOWUP_HANDOFF_BODY}\n\n"
        "[系统衔接] 深度研究已全部完成（用户澄清与大纲确认均已结束），"
        "研究报告已通过文件交付。"
        f"{next_step}"
        "禁止再次调用 deepresearch_execute，禁止调用 ask_user 重复提问或再次澄清研究方向。"
    )


def _apply_followup_handoff(
    ctx: AgentCallbackContext,
    payload: dict[str, Any],
) -> None:
    """Mark research done, advance the next outer todo, compact the tool result."""
    items = _load_todo_items(ctx)
    if not items:
        return

    changed = False
    next_followup: dict[str, Any] | None = None
    for item in items:
        task_id = _todo_item_id(item)
        status = str(item.get("status") or "pending").strip().lower()
        if _is_current_research_todo(task_id) and status not in _TODO_DONE:
            item["status"] = "completed"
            changed = True
            continue
        if (
            next_followup is None
            and status not in _TODO_DONE
            and not _is_current_research_todo(task_id)
        ):
            next_followup = item

    if next_followup is not None:
        followup_status = str(next_followup.get("status") or "pending").strip().lower()
        if followup_status != "in_progress":
            next_followup["status"] = "in_progress"
            changed = True

    if changed:
        _save_todo_items(ctx, items)

    content = str(payload.get("content") or "").rstrip()
    if not content:
        return
    handoff_content = _compact_followup_tool_content(next_followup)
    payload["content"] = handoff_content
    # Keep kind/status as the tool's real completed result. RelayClaw must not
    # be steered by camouflaged followup statuses.

    tool_result = ctx.inputs.tool_result
    detailed = getattr(tool_result, "detailed_output", None)
    if isinstance(detailed, dict):
        detailed["content"] = handoff_content
    elif isinstance(tool_result, dict):
        tool_result["content"] = handoff_content
    else:
        ctx.inputs.tool_result = dict(payload)

    tool_msg = getattr(ctx.inputs, "tool_msg", None)
    if tool_msg is not None and hasattr(tool_msg, "content"):
        try:
            tool_msg.content = handoff_content
        except Exception:
            logger.debug(
                "[DeepResearchExecutionRail] failed to rewrite tool_msg content",
                exc_info=True,
            )

    logger.info(
        "[DeepResearchExecutionRail] followup handoff applied; "
        "next_todo=%s content_chars=%s",
        _todo_item_id(next_followup) if next_followup else "",
        len(handoff_content),
    )


def _tool_name(ctx: AgentCallbackContext) -> str:
    if not isinstance(ctx.inputs, ToolCallInputs):
        return ""
    return str(
        ctx.inputs.tool_name
        or getattr(ctx.inputs.tool_call, "name", "")
        or ""
    ).strip()


def _tool_call_id(ctx: AgentCallbackContext) -> str:
    if not isinstance(ctx.inputs, ToolCallInputs):
        return ""
    return str(getattr(ctx.inputs.tool_call, "id", "") or "").strip()


def _load_states(session: Any) -> dict[str, dict[str, Any]]:
    if session is None:
        return {}
    value = session.get_state(DEEPRESEARCH_EXECUTION_STATE_KEY)
    if not isinstance(value, Mapping):
        return {}
    return {
        str(key): dict(state)
        for key, state in value.items()
        if isinstance(state, Mapping)
    }


def _save_states(session: Any, states: dict[str, dict[str, Any]]) -> None:
    if session is not None:
        session.update_state({DEEPRESEARCH_EXECUTION_STATE_KEY: states})


def _load_aliases(session: Any) -> dict[str, str]:
    if session is None:
        return {}
    value = session.get_state(DEEPRESEARCH_EXECUTION_ALIAS_KEY)
    if not isinstance(value, Mapping):
        return {}
    return {
        str(key): str(workflow_id)
        for key, workflow_id in value.items()
        if str(key).strip() and str(workflow_id).strip()
    }


def _save_aliases(session: Any, aliases: dict[str, str]) -> None:
    if session is not None:
        session.update_state({DEEPRESEARCH_EXECUTION_ALIAS_KEY: aliases})


def _workflow_id(session: Any, tool_call_id: str) -> str:
    return _load_aliases(session).get(tool_call_id, tool_call_id)


def _interaction_tool_call(tool_call: Any, interaction_id: str) -> Any:
    model_copy = getattr(tool_call, "model_copy", None)
    cloned = model_copy(deep=True) if callable(model_copy) else copy.deepcopy(tool_call)
    cloned.id = interaction_id
    return cloned


def _resume_input(ctx: AgentCallbackContext, tool_call_id: str) -> Any:
    value = getattr(ctx, "extra", {}).get(RESUME_USER_INPUT_KEY)
    if isinstance(value, InteractiveInput):
        return value.user_inputs.get(tool_call_id)
    if isinstance(value, Mapping) and tool_call_id in value:
        return value[tool_call_id]
    return value


def _result_payload(value: Any) -> dict[str, Any] | None:
    detailed_output = getattr(value, "detailed_output", None)
    if isinstance(detailed_output, Mapping):
        value = detailed_output
    return dict(value) if isinstance(value, Mapping) else None


class DeepResearchExecutionRail(DeepAgentRail):
    """Persist the workflow state and turn tool envelopes into native HITL."""

    # Higher priorities run first. Convert the envelope before
    # JiuSwarmStreamEventRail(priority=80), so the stream rail sees the native
    # interrupt and does not leak the internal envelope as a tool result.
    priority = 81

    def __init__(self, *, model_provider):
        super().__init__()
        self._model_provider = model_provider
        self._agent_id = "jiuwenswarm"

    def init(self, agent: Any) -> None:
        card = getattr(agent, "card", None)
        self._agent_id = str(
            getattr(card, "id", "")
            or getattr(card, "name", "")
            or "jiuwenswarm"
        ).strip()

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        if _tool_name(ctx) != _TOOL_NAME:
            return
        tool_call_id = _tool_call_id(ctx)
        workflow_id = _workflow_id(ctx.session, tool_call_id)
        states = _load_states(ctx.session)

        def save_state(state: dict[str, Any]) -> None:
            current = _load_states(ctx.session)
            current[workflow_id] = dict(state)
            _save_states(ctx.session, current)

        model = self._model_provider() if callable(self._model_provider) else None
        request_context = get_request_context(
            session_id=extract_session_id_from_callback(ctx)
        )
        request_id = str((request_context or {}).get("request_id") or "").strip()
        token = bind_deepresearch_execution_context(
            tool_call_id=workflow_id,
            state=states.get(workflow_id),
            user_input=_resume_input(ctx, tool_call_id),
            model=model,
            save_state=save_state,
            agent_id=self._agent_id,
            request_id=request_id,
        )
        tokens = ctx.extra.setdefault(_TOKENS_KEY, {})
        tokens[tool_call_id] = token

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        if _tool_name(ctx) != _TOOL_NAME:
            return
        try:
            payload = _result_payload(ctx.inputs.tool_result)
            if payload is None or payload.get("schema_version") != EXECUTION_SCHEMA:
                return
            tool_call_id = _tool_call_id(ctx)
            workflow_id = _workflow_id(ctx.session, tool_call_id)
            state = payload.get("state")
            states = _load_states(ctx.session)
            if isinstance(state, Mapping):
                states[workflow_id] = dict(state)
                _save_states(ctx.session, states)

            if payload.get("kind") == "interaction":
                interaction = payload.get("interaction")
                if not isinstance(interaction, Mapping):
                    return
                questions = interaction.get("questions")
                if not isinstance(questions, list):
                    return
                request = AskUserRequest(
                    message=str(interaction.get("query") or ""),
                    payload_schema=ask_user_response_schema(),
                    questions=questions,
                )
                revision = int(state.get("revision") or 0) if isinstance(state, Mapping) else 0
                interaction_id = f"{workflow_id}_interaction_{revision}"
                aliases = _load_aliases(ctx.session)
                aliases[interaction_id] = workflow_id
                _save_aliases(ctx.session, aliases)
                ctx.inputs.tool_result = ToolInterruptException(
                    request=request,
                    tool_call=_interaction_tool_call(
                        ctx.inputs.tool_call,
                        interaction_id,
                    ),
                )
                ctx.inputs.tool_msg = None
                return

            states.pop(workflow_id, None)
            _save_states(ctx.session, states)
            aliases = {
                alias: target
                for alias, target in _load_aliases(ctx.session).items()
                if target != workflow_id
            }
            _save_aliases(ctx.session, aliases)
            content = payload.get("content")
            if isinstance(content, str) and content.strip():
                if _has_followup_todos(ctx):
                    if payload.get("kind") == "completed":
                        _apply_followup_handoff(ctx, payload)
                    logger.info(
                        "[DeepResearchExecutionRail] skip force_finish; "
                        "outer todos still pending after deepresearch_execute"
                    )
                else:
                    ctx.request_force_finish(
                        {"output": content.strip(), "result_type": "answer"}
                    )
        finally:
            tool_call_id = _tool_call_id(ctx)
            tokens = ctx.extra.get(_TOKENS_KEY, {})
            token = tokens.pop(tool_call_id, None) if isinstance(tokens, dict) else None
            if not tokens:
                ctx.extra.pop(_TOKENS_KEY, None)
            reset_deepresearch_execution_context(token)


__all__ = [
    "DEEPRESEARCH_EXECUTION_ALIAS_KEY",
    "DEEPRESEARCH_EXECUTION_STATE_KEY",
    "DeepResearchExecutionRail",
]
