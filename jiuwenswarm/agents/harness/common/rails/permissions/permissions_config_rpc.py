"""Permissions 配置 RPC（宿主侧）。

这是原 `jiuwenswarm.agents.harness.common.rails.permissions.config_rpc` 的新归档位置，
用于减少对 legacy permissions 包路径的依赖。
"""

from __future__ import annotations

import logging
from typing import Any

from jiuwenswarm.common.schema.agent import AgentRequest, AgentResponse
from jiuwenswarm.common.schema.message import ReqMethod

logger = logging.getLogger(__name__)

_PERMISSIONS_CFG_METHODS: frozenset[ReqMethod] = frozenset(
    {
        ReqMethod.PERMISSIONS_TOOLS_GET,
        ReqMethod.PERMISSIONS_TOOLS_SET,
        ReqMethod.PERMISSIONS_TOOLS_UPDATE,
        ReqMethod.PERMISSIONS_TOOLS_DELETE,
        ReqMethod.PERMISSIONS_RULES_GET,
        ReqMethod.PERMISSIONS_RULES_CREATE,
        ReqMethod.PERMISSIONS_RULES_UPDATE,
        ReqMethod.PERMISSIONS_RULES_DELETE,
        ReqMethod.PERMISSIONS_APPROVAL_OVERRIDES_GET,
        ReqMethod.PERMISSIONS_APPROVAL_OVERRIDES_DELETE,
        ReqMethod.PERMISSIONS_MODE_GET,
        ReqMethod.PERMISSIONS_MODE_SET,
    }
)


def get_permissions_config_req_methods() -> frozenset[ReqMethod]:
    return _PERMISSIONS_CFG_METHODS


def _err(request: AgentRequest, message: str, *, code: str = "BAD_REQUEST") -> AgentResponse:
    return AgentResponse(
        request_id=request.request_id,
        channel_id=request.channel_id,
        ok=False,
        payload={"error": message, "code": code},
        metadata=request.metadata,
    )


def _ok(request: AgentRequest, payload: dict[str, Any] | None) -> AgentResponse:
    return AgentResponse(
        request_id=request.request_id,
        channel_id=request.channel_id,
        ok=True,
        payload=payload or {},
        metadata=request.metadata,
    )


def dispatch_permissions_config_request(request: AgentRequest) -> AgentResponse:
    """执行一条 permissions 配置 RPC（与原先 WebSocket register_method 语义一致）。"""
    from jiuwenswarm.common.config import (
        create_permissions_rule_in_config,
        delete_permissions_approval_override_in_config,
        delete_permissions_rule_in_config,
        get_permissions_approval_overrides,
        get_permissions_rules,
        update_permissions_rule_in_config,
    )
    from jiuwenswarm.agents.harness.common.rails.permissions.permissions_layers import (
        delete_user_tool,
        get_user_tools_map,
        replace_user_tools_map,
        set_user_tool_level,
    )

    m = request.req_method
    params = request.params if isinstance(request.params, dict) else {}
    tag = m.value if m is not None else ""

    try:
        if m == ReqMethod.PERMISSIONS_TOOLS_GET:
            return _ok(request, {"tools": get_user_tools_map()})

        if m == ReqMethod.PERMISSIONS_TOOLS_SET:
            if not isinstance(params, dict):
                return _err(request, "params must be object")
            tools = params.get("tools")
            return _ok(request, {"tools": replace_user_tools_map(tools)})

        if m == ReqMethod.PERMISSIONS_TOOLS_UPDATE:
            if not isinstance(params, dict):
                return _err(request, "params must be object")
            tool = str(params.get("tool") or params.get("name") or "").strip()
            if not tool:
                return _err(request, "tool is required")
            if "level" not in params:
                return _err(request, "level is required")
            return _ok(request, {"tools": set_user_tool_level(tool, params.get("level"))})

        if m == ReqMethod.PERMISSIONS_TOOLS_DELETE:
            if not isinstance(params, dict):
                return _err(request, "params must be object")
            tool = str(params.get("tool") or params.get("name") or "").strip()
            if not tool:
                return _err(request, "tool is required")
            ok_del = delete_user_tool(tool)
            if not ok_del:
                return _err(request, "tool not found in user permission lists", code="NOT_FOUND")
            return _ok(request, {"tools": get_user_tools_map()})

        if m == ReqMethod.PERMISSIONS_RULES_GET:
            return _ok(request, dict(get_permissions_rules()))

        if m == ReqMethod.PERMISSIONS_RULES_CREATE:
            if not isinstance(params, dict):
                return _err(request, "params must be object")
            rule = params.get("rule")
            if not isinstance(rule, dict):
                return _err(request, "rule must be object")
            stored = create_permissions_rule_in_config(rule)
            return _ok(request, {"rule": stored})

        if m == ReqMethod.PERMISSIONS_RULES_UPDATE:
            if not isinstance(params, dict):
                return _err(request, "params must be object")
            rid = params.get("id")
            patch = params.get("patch")
            if not isinstance(patch, dict):
                return _err(request, "patch must be object")
            merged = update_permissions_rule_in_config(str(rid or ""), patch)
            return _ok(request, {"rule": merged})

        if m == ReqMethod.PERMISSIONS_RULES_DELETE:
            if not isinstance(params, dict):
                return _err(request, "params must be object")
            ok_del = delete_permissions_rule_in_config(str(params.get("id") or ""))
            if not ok_del:
                return _err(request, "rule not found", code="NOT_FOUND")
            return _ok(request, {"ok": True})

        if m == ReqMethod.PERMISSIONS_APPROVAL_OVERRIDES_GET:
            return _ok(request, dict(get_permissions_approval_overrides()))

        if m == ReqMethod.PERMISSIONS_APPROVAL_OVERRIDES_DELETE:
            if not isinstance(params, dict):
                return _err(request, "params must be object")
            ok_del = delete_permissions_approval_override_in_config(str(params.get("id") or ""))
            if not ok_del:
                return _err(request, "approval_override not found", code="NOT_FOUND")
            return _ok(request, {"ok": True})

        if m == ReqMethod.PERMISSIONS_MODE_GET:
            from jiuwenswarm.common.config import get_permissions_mode_from_config
            from jiuwenswarm.agents.harness.common.rails.permissions.permissions_layers import (
                get_sandbox_intent,
            )

            mode = get_permissions_mode_from_config()
            return _ok(
                request,
                {
                    "mode": mode,
                    "sandbox_intent": get_sandbox_intent(),
                    "options": ["full_access", "auto", "strict"],
                },
            )

        if m == ReqMethod.PERMISSIONS_MODE_SET:
            from jiuwenswarm.common.config import update_permissions_mode_in_config
            from jiuwenswarm.agents.harness.common.rails.permissions.permissions_layers import (
                get_sandbox_intent,
                normalize_permission_mode,
            )

            if not isinstance(params, dict):
                return _err(request, "params must be object")
            raw_mode = params.get("mode") or params.get("value")
            if not isinstance(raw_mode, str) or not raw_mode.strip():
                return _err(request, "mode is required")
            mode = update_permissions_mode_in_config(normalize_permission_mode(raw_mode))
            return _ok(
                request,
                {
                    "mode": mode,
                    "sandbox_intent": get_sandbox_intent(),
                    "ok": True,
                },
            )

    except ValueError as e:
        return _err(request, str(e))
    except Exception as e:
        logger.exception("[%s] %s", tag, e)
        return _err(request, str(e), code="INTERNAL_ERROR")

    return _err(request, "unknown permissions req_method", code="BAD_REQUEST")


__all__ = [
    "dispatch_permissions_config_request",
    "get_permissions_config_req_methods",
]

