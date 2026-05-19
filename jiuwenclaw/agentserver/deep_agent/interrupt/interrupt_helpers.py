# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Interrupt helpers for DeepAgent.

Provides utilities for converting interrupt payloads to frontend format
and building permission rails.
"""
from __future__ import annotations

from typing import Any

from jiuwenclaw.agentserver.permissions.core import get_permission_engine
from jiuwenclaw.utils import logger


def build_permission_rail(
    config: dict[str, Any],
    llm: Any = None,
    model_name: str | None = None,
) -> Any | None:
    """Build PermissionInterruptRail for tool permission checks.

    Args:
        config: Agent config dict containing permissions section
        llm: LLM instance for risk assessment
        model_name: Model name for risk assessment

    Returns:
        PermissionInterruptRail instance or None if disabled
    """
    from jiuwenclaw.agentserver.deep_agent.rails.permission_rail import PermissionInterruptRail

    permission_config = config.get("permissions", {})
    logger.info(
        "[InterruptHelpers] build_permission_rail called: enabled=%s",
        permission_config.get("enabled", False)
    )

    logger.info(
        "[InterruptHelpers] Building PermissionInterruptRail intercept=all llm=%s model_name=%s",
        llm is not None,
        model_name,
    )
    try:
        permission_rail = PermissionInterruptRail(
            config=permission_config,
            engine=get_permission_engine(),
            llm=llm,
            model_name=model_name,
        )
        logger.info(
            "[InterruptHelpers] PermissionInterruptRail created successfully intercept=all"
        )
    except Exception as exc:
        logger.warning("[InterruptHelpers] PermissionInterruptRail create failed: %s", exc)
        permission_rail = None
    return permission_rail


def build_ask_user_rail() -> Any | None:
    """Build AskUserRail for user input requests.

    Returns:
        AskUserRail instance or None if creation failed
    """
    from openjiuwen.harness.rails.interrupt.ask_user_rail import AskUserRail

    try:
        ask_user_rail = AskUserRail()
        logger.info("[InterruptHelpers] AskUserRail created successfully")
    except Exception as exc:
        logger.warning("[InterruptHelpers] AskUserRail create failed: %s", exc)
        ask_user_rail = None
    return ask_user_rail


def _intent_header_from_permission_message(message: str) -> str | None:
    """若正文首行是单行 ``**...**``，且为「助手意图」行（非工具行/风险行），返回其中纯文本作审批标题。

    正文首行也可能是加粗的工具授权提示行（以「工具」开头），该段不应当作标题。
    """
    if not message or not isinstance(message, str):
        return None
    first = message.lstrip("\ufeff").split("\n", 1)[0].strip()
    if len(first) < 4 or not first.startswith("**") or not first.endswith("**"):
        return None
    inner = first[2:-2].strip()
    if not inner:
        return None
    if inner.startswith("工具") or inner.startswith("风险等级"):
        return None
    return inner


def _header_for_permission_question(message: str, tool_name: str) -> str:
    """审批卡标题：优先旧版正文首行意图；否则与 ``PermissionInterruptRail.assistant_intent_first_line`` 一致。"""
    from_message = _intent_header_from_permission_message(message)
    if from_message:
        return from_message
    from jiuwenclaw.agentserver.deep_agent.rails.permission_rail import PermissionInterruptRail

    intent = PermissionInterruptRail.assistant_intent_first_line(tool_name or "").strip()
    if intent:
        return intent
    return f"权限审批: {tool_name}" if tool_name else "权限审批"


def convert_interactions_to_ask_user_question(state_outputs: list) -> dict | None:
    """Convert __interaction__ list to frontend chat.ask_user_question format.

    Args:
        state_outputs: List of OutputSchema(type=__interaction__, payload=InteractionOutput)
                      Note: In streaming mode, this list contains only one element per chunk

    Returns:
        Frontend expected chat.ask_user_question format dict
    """
    if not state_outputs:
        return None

    payload = state_outputs[0].payload if hasattr(state_outputs[0], 'payload') else state_outputs[0]
    question_data = extract_question_from_interaction(payload)
    if not question_data:
        return None

    request_id = getattr(payload, 'id', '') if hasattr(payload, 'id') else payload.get('id', '')

    return {
        "event_type": "chat.ask_user_question",
        "request_id": request_id,
        "questions": [question_data],
        "source": "permission_interrupt",
    }


def extract_question_from_interaction(payload: Any) -> dict | None:
    """Extract question info from a single interaction payload.

    Args:
        payload: InteractionOutput instance or dict

    Returns:
        Question format dict for frontend
    """
    if payload is None:
        return None

    tool_name = ""
    message = ""

    if hasattr(payload, 'value'):
        value_obj = payload.value
        message = getattr(value_obj, 'message', '') or getattr(value_obj, 'question', '')
        tool_name = getattr(value_obj, 'tool_name', '')
    elif isinstance(payload, dict):
        value_obj = payload.get('value', {})
        if isinstance(value_obj, dict):
            message = value_obj.get('message', '') or value_obj.get('question', '')
            tool_name = value_obj.get('tool_name', '')
        else:
            message = payload.get('message', '') or payload.get('question', '')
    else:
        return None

    return {
        "question": message or f"工具 `{tool_name}` 需要授权才能执行",
        "header": _header_for_permission_question(message, tool_name),
        "options": [
            {"label": "本次允许", "description": "仅本次授权执行"},
            {"label": "总是允许", "description": "记住该规则，以后自动放行"},
            {"label": "拒绝", "description": "拒绝执行此工具"},
        ],
        "multi_select": False,
    }
