# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Interrupt helpers for DeepAgent.

Provides utilities for converting interrupt payloads to frontend format
and building permission rails.
"""
from __future__ import annotations

import json
from typing import Any

from jiuwenclaw.common.utils import logger


def build_permission_rail(
    config: dict[str, Any],
    llm: Any = None,
    model_name: str | None = None,
) -> Any | None:
    """Build openjiuwen PermissionInterruptRail for tool permission checks.

    Args:
        config: Agent config dict containing permissions section
        llm: LLM instance for risk assessment
        model_name: Model name for risk assessment

    Returns:
        PermissionInterruptRail instance or None if disabled
    """
    from openjiuwen.harness.rails.security.tool_security_rail import PermissionInterruptRail
    from openjiuwen.harness.security.host import (
        PermissionConfirmationRequest,
        PermissionSceneHookInput,
        ToolPermissionHost,
    )
    from openjiuwen.harness.security.models import PermissionConfirmResponse

    from jiuwenclaw.agents.harness.common.rails.permissions.tool_permission_context import (
        TOOL_PERMISSION_CHANNEL_ID,
    )
    from jiuwenclaw.common.config import get_config
    from jiuwenclaw.common.e2a.acp.acp_tool_updates import build_acp_tool_descriptor
    from jiuwenclaw.common.utils import get_config_file, get_workspace_dir

    permission_config = config.get("permissions", {})
    logger.info(
        "[InterruptHelpers] build_permission_rail called: enabled=%s",
        permission_config.get("enabled", False)
    )

    if not permission_config.get("enabled", False):
        logger.info("[InterruptHelpers] Permission system is disabled, returning None")
        return None

    def _collect_optional_tool_tags(cfg: dict[str, Any]) -> list[str]:
        # openjiuwen PermissionInterruptRail 会拦截所有工具；
        # 这里的 tool_names 仅作为标签展示/日志辅助（尽量覆盖 tools + rules 声明）。
        names: set[str] = set()
        tools_cfg = cfg.get("tools") or {}
        if isinstance(tools_cfg, dict):
            for k in tools_cfg.keys():
                label = str(k).strip()
                if label:
                    names.add(label)
        rules = cfg.get("rules") or []
        if isinstance(rules, list):
            for entry in rules:
                if not isinstance(entry, dict):
                    continue
                raw_tools = entry.get("tools")
                if raw_tools is None:
                    continue
                if isinstance(raw_tools, str):
                    raw_tools = [raw_tools]
                if isinstance(raw_tools, list):
                    for item in raw_tools:
                        if isinstance(item, str) and item.strip():
                            names.add(item.strip())
        return sorted(names)

    tool_names = _collect_optional_tool_tags(permission_config)
    logger.info(
        "[InterruptHelpers] tools_config keys: %s, rail tool_names (with rules): %s",
        list((permission_config.get("tools") or {}).keys()),
        tool_names,
    )
    logger.info(
        "[InterruptHelpers] Building PermissionInterruptRail with tool_names=%s llm=%s model_name=%s",
        tool_names, llm is not None, model_name,
    )
    try:
        def _persist_allow_rule(permissions: dict[str, Any]) -> bool:
            """Persist merged `permissions` config back to config.yaml.

            openjiuwen PermissionInterruptRail calls this when user selects "always allow".
            """
            try:
                from jiuwenclaw.common.config import _dump_yaml_round_trip, _load_yaml_round_trip

                yaml_path = get_config_file()
                data = _load_yaml_round_trip(yaml_path)
                if not isinstance(data, dict):
                    data = {}
                data["permissions"] = permissions
                _dump_yaml_round_trip(yaml_path, data)
                return True
            except Exception as exc:
                logger.warning("[InterruptHelpers] persist_allow_rule failed: %s", exc)
                return False

        def _resolve_session_id(ctx: Any) -> str | None:
            session = getattr(ctx, "session", None)
            if session is None:
                return None
            for attr_name in ("get_session_id", "session_id"):
                attr = getattr(session, attr_name, None)
                try:
                    value = attr() if callable(attr) else attr
                except Exception:
                    value = None
                if isinstance(value, str) and value.strip():
                    return value.strip()
            return None

        async def _request_permission_confirmation(
            req: PermissionConfirmationRequest,
        ) -> PermissionConfirmResponse | str | None:
            channel = TOOL_PERMISSION_CHANNEL_ID.get() or "web"
            if channel != "acp":
                return "interrupt"

            session_id = _resolve_session_id(req.ctx)
            if not session_id:
                return None

            from jiuwenclaw.agents.harness.common.tools.acp_output_tools import get_acp_output_manager

            tool_call = req.tool_call
            tool_name = getattr(tool_call, "name", "") if tool_call is not None else ""
            tool_args_raw = getattr(tool_call, "arguments", None) if tool_call is not None else None
            tool_call_id = str(getattr(tool_call, "id", "") or f"permission_{tool_name or 'tool'}").strip()
            descriptor = build_acp_tool_descriptor(
                tool_name,
                tool_args_raw,
                tool_call_id=tool_call_id,
                status="pending",
                kind="other",
            )
            title = str(descriptor.get("title") or f"Approve `{tool_name}`")
            if getattr(req.result, "reason", None):
                title = f"{title}: {req.result.reason}"

            request_params: dict[str, Any] = {
                "toolCall": {
                    **descriptor,
                    "title": title,
                },
                "options": [
                    {"optionId": "allow-once", "name": "Allow once", "kind": "allow_once"},
                    {"optionId": "allow-always", "name": "Always allow", "kind": "allow_always"},
                    {"optionId": "reject-once", "name": "Reject", "kind": "reject_once"},
                ],
            }

            try:
                response = await get_acp_output_manager().send_jsonrpc_request(
                    "session/request_permission",
                    request_params,
                    session_id=session_id,
                )
            except Exception as exc:
                logger.warning("[InterruptHelpers] ACP permission request failed: %s", exc)
                return None

            if not isinstance(response, dict):
                return None
            if isinstance(response.get("error"), dict):
                message = str(response["error"].get("message") or "Permission request failed")
                return PermissionConfirmResponse(
                    approved=False,
                    auto_confirm=False,
                    feedback=f"[PERMISSION_DENIED] {message}",
                )

            result_payload = response.get("result") if isinstance(response.get("result"), dict) else {}
            outcome = result_payload.get("outcome") if isinstance(result_payload.get("outcome"), dict) else {}
            outcome_kind = str(outcome.get("outcome") or "").strip().lower()
            option_id = str(outcome.get("optionId") or "").strip().lower()

            if outcome_kind == "selected":
                if option_id == "allow-once":
                    return PermissionConfirmResponse(approved=True, auto_confirm=False, feedback="")
                if option_id == "allow-always":
                    return PermissionConfirmResponse(approved=True, auto_confirm=True, feedback="")
                return PermissionConfirmResponse(
                    approved=False,
                    auto_confirm=False,
                    feedback="[PERMISSION_REJECTED] User rejected the request.",
                )

            if outcome_kind == "cancelled":
                return PermissionConfirmResponse(
                    approved=False,
                    auto_confirm=False,
                    feedback="[PERMISSION_REJECTED] Permission request was cancelled.",
                )
            return None

        async def _permission_scene_hook(
            inp: PermissionSceneHookInput,
        ) -> tuple[str, ...] | None:
            from jiuwenclaw.agents.harness.common.rails.permissions.owner_scopes import (
                TOOL_PERMISSION_CONTEXT,
                check_avatar_permission,
                _resolve_owner_scope_level,
            )

            perm_ctx = TOOL_PERMISSION_CONTEXT.get()
            if perm_ctx is None:
                return None

            if getattr(perm_ctx, "scene", None) == "group_digital_avatar":
                if inp.user_input is not None:
                    return ("reject", "[PERMISSION_DENIED] 数字分身场景不支持交互审批")
                level = await check_avatar_permission(
                    inp.normalized_tool_name,
                    inp.tool_args,
                    channel_id=str(getattr(perm_ctx, "channel_id", "") or ""),
                    session_id=None,
                )
                if level == "allow":
                    return ("approve",)
                return ("reject", "[PERMISSION_DENIED] 该工具未被授权在数字分身场景下使用")

            principal_user_id = str(getattr(perm_ctx, "principal_user_id", "") or "").strip()
            channel_id = str(getattr(perm_ctx, "channel_id", "") or "").strip()
            if not principal_user_id or not channel_id:
                return None

            perm_all = get_config().get("permissions") if isinstance(get_config(), dict) else {}
            owner_scopes = perm_all.get("owner_scopes") if isinstance(perm_all, dict) else None
            if not isinstance(owner_scopes, dict) or not owner_scopes:
                return None

            scope_cfg = (owner_scopes.get(channel_id) or {}).get(principal_user_id)
            owner_level = _resolve_owner_scope_level(
                scope_cfg, inp.normalized_tool_name, inp.tool_args
            )
            if owner_level is None:
                return None
            if owner_level == "allow":
                return ("approve",)
            return ("reject", f"[PERMISSION_DENIED] 该工具未被授权 (owner_scopes: {owner_level})")

        host = ToolPermissionHost(
            get_permissions_snapshot=lambda: (
                get_config().get("permissions") if isinstance(get_config(), dict) else {}
            ),
            persist_allow_rule=_persist_allow_rule,
            resolve_workspace_dir=get_workspace_dir,
            permission_yaml_path=get_config_file(),
            request_permission_confirmation=_request_permission_confirmation,
            permission_scene_hook=_permission_scene_hook,
        )

        permission_rail = PermissionInterruptRail(
            config=permission_config,
            tool_names=tool_names,
            llm=llm,
            model_name=model_name,
            host=host,
        )
        logger.info(
            "[InterruptHelpers] PermissionInterruptRail created successfully with tool_names=%s",
            tool_names
        )
    except Exception as exc:
        logger.warning("[InterruptHelpers] PermissionInterruptRail create failed: %s", exc)
        permission_rail = None
    return permission_rail



def convert_interactions_to_ask_user_question(state_outputs: list) -> dict | None:
    """Convert __interaction__ list to frontend chat.ask_user_question format.

    AskUserRail 中断: value 有 questions 字段 → source="ask_user_interrupt"
    PermissionRail 中断: value 无 questions 字段 → source="permission_interrupt"

    state_outputs 中的元素可能是:
    - InteractionOutput 对象 (有 id, value 属性, value 是 ToolCallInterruptRequest)
    - dict (有 id, value 键)
    """
    if not state_outputs:
        return None

    interaction = state_outputs[0]
    if hasattr(interaction, "id"):
        request_id = interaction.id
        value_obj = interaction.value
    elif isinstance(interaction, dict):
        request_id = interaction.get("id", "")
        value_obj = interaction.get("value", {})
    else:
        return None

    questions_raw = _extract_questions_from_value(value_obj)

    if questions_raw is not None:
        questions = _build_multi_questions(questions_raw)
        return {
            "event_type": "chat.ask_user_question",
            "request_id": request_id,
            "questions": questions,
            "source": "ask_user_interrupt",
        }

    question_data = extract_question_from_interaction(interaction)
    if not question_data:
        return None

    return {
        "event_type": "chat.ask_user_question",
        "request_id": request_id,
        "questions": [question_data],
        "source": "permission_interrupt",
    }


def _extract_questions_from_value(value_obj: Any) -> list | None:
    """从 value 对象中提取 questions 列表.

    AskUserRail 的 value (ToolCallInterruptRequest) 有 questions 属性.
    如果 questions 存在且非空, 返回列表; 否则返回 None 表示不是 AskUserRail 中断.

    Additional source: StructuredAskUserRail puts `questions` in the tool call
    arguments, which are preserved in ToolCallInterruptRequest.tool_args.
    """
    # 1. Direct questions attribute on value_obj
    if hasattr(value_obj, 'questions'):
        qs = value_obj.questions
        if qs and len(qs) > 0:
            return qs
    elif isinstance(value_obj, dict):
        qs = value_obj.get("questions", [])
        if qs and len(qs) > 0:
            return qs

    # 2. questions embedded in tool_args (StructuredAskUserRail path)
    # ToolCallInterruptRequest.tool_args preserves the original tool call
    # arguments, including the `questions` parameter.
    tool_args = getattr(value_obj, 'tool_args', None)
    if tool_args is not None:
        if isinstance(tool_args, str):
            try:
                tool_args = json.loads(tool_args)
            except (ValueError, TypeError):
                pass
        if isinstance(tool_args, dict):
            qs = tool_args.get("questions", [])
            if qs and len(qs) > 0:
                return qs

    return None


def _build_multi_questions(questions_data: list) -> list:
    """Build frontend PendingQuestionItem list from questions data.

    有选项的问题: 保留原始选项 + 追加 __other__ (自定义输入)
    无选项的问题: 不追加 __other__, 前端应直接进入自由输入模式
    """
    questions = []
    for q in questions_data:
        raw_options = q.get("options", [])
        if raw_options:
            options = [{"label": opt["label"], "description": opt.get("description", "")}
                       for opt in raw_options]
            options.append({"label": "Other", "description": "Custom input"})
        else:
            options = []
        questions.append({
            "question": q["question"],
            "header": q["header"],
            "options": options,
            "multi_select": q.get("multi_select", False),
        })
    return questions


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
        "header": f"权限审批: {tool_name}" if tool_name else "权限审批",
        "options": [
            {"label": "本次允许", "description": "仅本次授权执行"},
            {"label": "总是允许", "description": "记住该规则，以后自动放行"},
            {"label": "拒绝", "description": "拒绝执行此工具"},
        ],
        "multi_select": False,
    }
