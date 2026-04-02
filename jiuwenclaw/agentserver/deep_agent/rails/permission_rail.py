# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""PermissionInterruptRail - tool permission guardrail using ConfirmInterruptRail.

Implements permission checks via PermissionEngine and triggers HITL interrupts
for ASK decisions using the built-in interrupt rail flow.
"""
from __future__ import annotations

import json
from typing import Any, Iterable, Optional

from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.single_agent.interrupt.response import InterruptRequest
from openjiuwen.core.single_agent.interrupt.state import INTERRUPT_AUTO_CONFIRM_KEY
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.interrupt.confirm_rail import (
    ConfirmInterruptRail,
    ConfirmPayload,
)
from openjiuwen.harness.rails.interrupt.interrupt_base import InterruptDecision

from jiuwenclaw.agentserver.deep_agent.permissions.core import PermissionEngine
from jiuwenclaw.agentserver.deep_agent.permissions.checker import TOOL_PERMISSION_CHANNEL_ID
from jiuwenclaw.agentserver.deep_agent.permissions.models import PermissionLevel, PermissionResult
from jiuwenclaw.utils import logger


class PermissionInterruptRail(ConfirmInterruptRail):
    """Permission interrupt rail.

    - ALLOW: continue
    - DENY: reject
    - ASK: interrupt with ConfirmPayload schema

    Auto-confirm is stored in session state (INTERRUPT_AUTO_CONFIRM_KEY).
    Supports fine-grained auto-confirm keys for bash commands (e.g., bash_dir, bash_rm).
    """

    priority: int = 90

    def __init__(
        self,
        config: Optional[dict] = None,
        engine: Optional[PermissionEngine] = None,
        tool_names: Optional[Iterable[str]] = None,
        llm: Any = None,
        model_name: str | None = None,
    ) -> None:
        super().__init__(tool_names=tool_names)
        self._static_config = config or {}
        if engine is not None:
            self._engine = engine
        else:
            self._engine = PermissionEngine(
                config=self._static_config,
                llm=llm,
                model_name=model_name,
            )
        logger.info(
            "[PermissionRail] Initialized with tool_names=%s config_keys=%s llm=%s model_name=%s",
            list(self._tool_names), list(self._static_config.get("tools", {}).keys()),
            self._engine._llm is not None, self._engine._model_name,
        )

    def _get_auto_confirm_key(self, tool_call: ToolCall) -> str:
        """Generate fine-grained auto-confirm key based on tool call.
        
        For bash tool: key = "bash_<command>" (e.g., "bash_dir", "bash_rm")
        For mcp_exec_command: key = "mcp_exec_command_<command>"
        For other tools: key = tool_name
        
        This enables pattern-based auto-confirm like the old mcp_exec_command patterns.
        """
        if tool_call is None:
            return ""
        
        tool_name = tool_call.name or ""
        tool_args = self._parse_tool_args(tool_call)
        
        if tool_name == "bash":
            cmd = tool_args.get("command", tool_args.get("cmd", ""))
            if cmd:
                cmd_base = cmd.strip().split()[0] if cmd.strip() else ""
                if cmd_base:
                    return f"bash_{cmd_base}"
            return tool_name
        
        if tool_name == "mcp_exec_command":
            cmd = tool_args.get("command", tool_args.get("cmd", ""))
            if cmd:
                cmd_base = cmd.strip().split()[0] if cmd.strip() else ""
                if cmd_base:
                    return f"mcp_exec_command_{cmd_base}"
            return tool_name
        
        return tool_name

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        tool_name = ctx.inputs.tool_name
        logger.info(
            "[PermissionRail] before_tool_call: tool_name=%s _tool_names=%s will_intercept=%s",
            tool_name, list(self._tool_names), tool_name in self._tool_names
        )
        await super().before_tool_call(ctx)

    def update_config(self, config: dict, tool_names: Optional[Iterable[str]] = None) -> None:
        """Hot-update static permission config and tool_names."""
        self._static_config = config
        self._engine.update_config(config)
        if tool_names is not None:
            self._tool_names = set(tool_names)
            logger.info(
                "[PermissionRail] Hot-updated tool_names=%s",
                list(self._tool_names)
            )

    async def resolve_interrupt(
        self,
        ctx: AgentCallbackContext,
        tool_call: Optional[ToolCall],
        user_input: Optional[Any],
        auto_confirm_config: Optional[dict] = None,
    ):
        tool_name = tool_call.name if tool_call is not None else ""
        tool_args = self._parse_tool_args(tool_call)
        auto_confirm_key = self._get_auto_confirm_key(tool_call)

        logger.info(
            "[PermissionRail] resolve_interrupt called: tool_name=%s tool_args=%s auto_confirm_key=%s user_input=%s",
            tool_name, tool_args, auto_confirm_key, type(user_input).__name__ if user_input else None
        )

        if user_input is None:
            logger.info("[PermissionRail] First call - checking permission for tool=%s", tool_name)
            self._engine.update_config(self._static_config)
            result = await self._engine.check_permission(
                tool_name=tool_name,
                tool_args=tool_args,
                channel_id=self._resolve_channel_id(),
            )

            logger.info(
                "[PermissionRail] Engine returned: permission=%s matched_rule=%s risk=%s",
                result.permission.value, result.matched_rule, result.risk,
            )

            if result.permission == PermissionLevel.ALLOW:
                logger.info("[PermissionRail] ALLOW tool=%s rule=%s", tool_name, result.matched_rule)
                return self.approve()

            if result.permission == PermissionLevel.DENY:
                logger.warning("[PermissionRail] DENY tool=%s rule=%s", tool_name, result.matched_rule)
                return self.reject(tool_result=f"[PERMISSION_DENIED] {result.reason or 'Operation not allowed'}")

            if self._is_auto_confirmed(auto_confirm_config, auto_confirm_key):
                logger.info("[PermissionRail] AUTO_CONFIRM key=%s", auto_confirm_key)
                return self.approve()

            logger.info("[PermissionRail] ASK - triggering interrupt for tool=%s", tool_name)
            message = self._build_message(tool_call, result)
            return self.interrupt(InterruptRequest(
                message=message,
                payload_schema=ConfirmPayload.to_schema(),
            ))

        logger.info("[PermissionRail] User response received for tool=%s", tool_name)
        payload = self._parse_confirm_payload(user_input)
        if payload is None:
            message = self._build_message(tool_call, PermissionResult(
                permission=PermissionLevel.ASK,
                matched_rule=None,
                reason="Invalid confirmation payload",
            ))
            return self.interrupt(InterruptRequest(
                message=message,
                payload_schema=ConfirmPayload.to_schema(),
            ))

        if payload.auto_confirm and ctx.session is not None and auto_confirm_key:
            config = ctx.session.get_state(INTERRUPT_AUTO_CONFIRM_KEY) or {}
            if not isinstance(config, dict):
                config = {}
            config[auto_confirm_key] = True
            ctx.session.update_state({INTERRUPT_AUTO_CONFIRM_KEY: config})
            logger.info("[PermissionRail] Stored auto_confirm for key=%s", auto_confirm_key)

        if payload.approved:
            logger.info("[PermissionRail] User approved tool=%s", tool_name)
            return self.approve()

        logger.info("[PermissionRail] User rejected tool=%s", tool_name)
        return self.reject(tool_result=payload.feedback or "[PERMISSION_REJECTED] User rejected the request.")

    @staticmethod
    def _parse_tool_args(tool_call: Optional[ToolCall]) -> dict:
        if tool_call is None:
            return {}
        args = tool_call.arguments
        if isinstance(args, str):
            try:
                return json.loads(args)
            except Exception:
                return {}
        return args or {}

    @staticmethod
    def _parse_confirm_payload(user_input: Any) -> Optional[ConfirmPayload]:
        if isinstance(user_input, ConfirmPayload):
            return user_input
        if isinstance(user_input, dict):
            try:
                return ConfirmPayload.model_validate(user_input)
            except Exception:
                return None
        if isinstance(user_input, str):
            try:
                return ConfirmPayload.model_validate(json.loads(user_input))
            except Exception:
                return None
        return None

    @staticmethod
    def _resolve_channel_id() -> str:
        return TOOL_PERMISSION_CHANNEL_ID.get() or "web"

    @staticmethod
    def _is_auto_confirmed(auto_confirm_config: Optional[dict], tool_name: str) -> bool:
        if auto_confirm_config is None:
            return False
        return auto_confirm_config.get(tool_name, False)

    @staticmethod
    def _format_args_preview(tool_args: dict) -> str:
        try:
            return json.dumps(tool_args, ensure_ascii=False, indent=2)[:1000]
        except Exception:
            return str(tool_args)[:1000]

    def _build_message(
        self,
        tool_call: Optional[ToolCall],
        result: PermissionResult,
    ) -> str:
        tool_name = tool_call.name if tool_call else ""
        tool_args = self._parse_tool_args(tool_call)
        risk = result.risk or {"level": "中", "icon": "🟡", "explanation": "需要用户确认"}

        parts = [
            f"**工具 `{tool_name}` 需要授权才能执行**\n\n",
            f"**安全风险评估：** {risk.get('icon', '')} **{risk.get('level', '')}风险**\n\n",
            f"> {risk.get('explanation', '')}\n\n",
        ]

        args_preview = self._format_args_preview(tool_args)
        if args_preview and args_preview != "{}":
            parts.append(f"参数：\n```json\n{args_preview}\n```\n")

        parts.append(f"\n匹配规则：`{result.matched_rule or 'N/A'}`")

        external_paths = getattr(result, "external_paths", None) or []
        if external_paths:
            parts.append(f"\n\n**外部路径：** `{', '.join(external_paths)}`")

        parts.append(self._build_always_allow_hint(tool_call))

        return "".join(parts)

    def _build_always_allow_hint(self, tool_call: Optional[ToolCall]) -> str:
        if tool_call is None:
            return ""
        
        tool_name = tool_call.name or ""
        tool_args = self._parse_tool_args(tool_call)
        auto_confirm_key = self._get_auto_confirm_key(tool_call)
        
        if tool_name == "bash":
            cmd = tool_args.get("command", tool_args.get("cmd", ""))
            if cmd:
                cmd_base = cmd.strip().split()[0] if cmd.strip() else ""
                if cmd_base:
                    return f'\n\n> 选择"总是允许"将自动放行 `{cmd_base}` 命令'
        if tool_name == "mcp_exec_command":
            cmd = tool_args.get("command", tool_args.get("cmd", ""))
            if cmd:
                return f'\n\n> 选择"总是允许"将自动放行 `{cmd}` 命令'
        if auto_confirm_key:
            return f'\n\n> 选择"总是允许"将自动放行 `{auto_confirm_key}` 调用'
        return ""


__all__ = [
    "PermissionInterruptRail",
]
