# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""PermissionInterruptRail - tool permission guardrail using ConfirmInterruptRail.

Implements permission checks via PermissionEngine and triggers HITL interrupts
for ASK decisions using the built-in interrupt rail flow.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable, Optional
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.single_agent.interrupt.response import InterruptRequest
from openjiuwen.core.single_agent.interrupt.state import INTERRUPT_AUTO_CONFIRM_KEY
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.interrupt.confirm_rail import (
    ConfirmInterruptRail,
    ConfirmPayload,
)

from jiuwenclaw.agentserver.permissions.core import PermissionEngine, get_permission_engine
from jiuwenclaw.agentserver.permissions.patterns import persist_permission_allow_rule
from jiuwenclaw.agentserver.permissions.shell_ast import parse_shell_for_permission
from jiuwenclaw.agentserver.permissions.suggestions import build_permission_suggestions
from jiuwenclaw.config import get_config
from jiuwenclaw.agentserver.permissions.checker import (
    TOOL_PERMISSION_CHANNEL_ID,
)
from jiuwenclaw.agentserver.permissions import PermissionLevel, PermissionResult
from jiuwenclaw.e2a.acp_tool_updates import build_acp_tool_descriptor
from jiuwenclaw.utils import logger


TOOL_NAME_ALIASES = {
    "free_search": "mcp_free_search",
    "paid_search": "mcp_paid_search",
    "fetch_webpage": "mcp_fetch_webpage",
    "exec_command": "mcp_exec_command",
}

INTERRUPT_PENDING_PERMISSION_CONTEXT_KEY = "jiuwenclaw_pending_permission_contexts"
_SHELL_PERMISSION_TOOLS = frozenset({"bash", "mcp_exec_command", "create_terminal"})


@dataclass(frozen=True)
class PermissionConfirmResponse:
    approved: bool
    feedback: str = ""
    auto_confirm: bool = False
    persist_allow: bool = False


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
        # This rail overrides before_tool_call and intentionally evaluates every
        # tool call. Parent tool_names are therefore diagnostic only.
        super().__init__(tool_names=tool_names or [])
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
            "[PermissionEngine] permission.rail.init tool_names=%s tools_keys=%s llm_enabled=%s model_name=%s",
            list(self._tool_names),
            list((self._static_config.get("tools") or {}).keys()),
            self._engine._llm is not None,
            self._engine._model_name,
        )

    def init(self, agent: Any) -> None:
        super().init(agent)
        callbacks = self.get_callbacks()
        before_cb = callbacks.get("before_tool_call")
        if before_cb is None:
            from openjiuwen.core.single_agent.rail.base import AgentCallbackEvent
            before_cb = callbacks.get(AgentCallbackEvent.BEFORE_TOOL_CALL)
        logger.info(
            "[PermissionEngine] permission.rail.init.callbacks rail_class=%s "
            "bound_before=%s bound_before_qualname=%s get_callbacks_before=%s get_callbacks_before_qualname=%s",
            self.__class__.__name__,
            type(self.before_tool_call).__name__,
            getattr(self.before_tool_call, "__qualname__", None),
            type(before_cb).__name__ if before_cb is not None else None,
            getattr(before_cb, "__qualname__", None) if before_cb is not None else None,
        )

    def _normalize_tool_name(self, tool_name: str) -> str:
        """Normalize tool name using aliases.

        Maps tool names from openjiuwen.harness.tools to mcp_* names used in config.
        """
        return TOOL_NAME_ALIASES.get(tool_name, tool_name)

    def _get_auto_confirm_key(self, tool_call: ToolCall) -> str:
        """Generate a conservative session auto-confirm key for the tool call."""
        if tool_call is None:
            return ""

        tool_name = tool_call.name or ""
        tool_args = self._parse_tool_args(tool_call)

        if tool_name in {"bash", "mcp_exec_command", "create_terminal"}:
            cmd = tool_args.get("command", tool_args.get("cmd", ""))
            return self._build_shell_auto_confirm_key(tool_name, str(cmd or ""))

        return tool_name

    @staticmethod
    def _build_shell_auto_confirm_key(tool_name: str, command: str) -> str:
        text = (command or "").strip()
        if not text:
            return ""

        shell_ast_result = parse_shell_for_permission(text)
        if shell_ast_result.kind != "simple":
            return ""
        if shell_ast_result.flags.has_risky_structure():
            return ""
        if len(shell_ast_result.subcommands) != 1:
            return ""

        subcommand = (shell_ast_result.subcommands[0].text or "").strip()
        if not subcommand:
            return ""
        return f"{tool_name}:{subcommand}"

    @staticmethod
    def _should_store_auto_confirm(
        *,
        auto_confirm: bool,
        session: Any,
        auto_confirm_key: str,
        persisted: bool,
    ) -> bool:
        return bool(auto_confirm and session is not None and auto_confirm_key and not persisted)

    @staticmethod
    def _read_session_state(session: Any, key: str) -> Any:
        if session is None:
            return None
        try:
            return session.get_state(key)
        except Exception:
            logger.debug(
                "[PermissionEngine] permission.rail.session_state_read_failed key=%s",
                key,
                exc_info=True,
            )
            return None

    @classmethod
    def _get_pending_permission_contexts(cls, session: Any) -> dict[str, dict[str, Any]]:
        data = cls._read_session_state(session, INTERRUPT_PENDING_PERMISSION_CONTEXT_KEY)
        return data if isinstance(data, dict) else {}

    @classmethod
    def _store_pending_permission_context(
        cls,
        ctx: AgentCallbackContext,
        tool_call_id: str,
        context: dict[str, Any],
    ) -> None:
        session = getattr(ctx, "session", None)
        if session is None or not tool_call_id:
            return
        pending = dict(cls._get_pending_permission_contexts(session))
        pending[tool_call_id] = context
        session.update_state({INTERRUPT_PENDING_PERMISSION_CONTEXT_KEY: pending})

    @classmethod
    def _pop_pending_permission_context(
        cls,
        ctx: AgentCallbackContext,
        tool_call_id: str,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> dict[str, Any] | None:
        session = getattr(ctx, "session", None)
        if session is None or not tool_call_id:
            return None
        pending = dict(cls._get_pending_permission_contexts(session))
        payload = pending.pop(tool_call_id, None)
        session.update_state({INTERRUPT_PENDING_PERMISSION_CONTEXT_KEY: pending})
        if not isinstance(payload, dict):
            return None
        if payload.get("tool_name") != tool_name:
            return None
        stored_args = payload.get("tool_args")
        if isinstance(stored_args, dict) and stored_args != tool_args:
            return None
        return payload

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        tool_name = ctx.inputs.tool_name
        tool_call = ctx.inputs.tool_call
        normalized_name = self._normalize_tool_name(tool_name)
        logger.info(
            "[PermissionEngine] permission.rail.before_tool_call tool=%s normalized=%s tracked_tools=%s",
            tool_name, normalized_name, list(self._tool_names)
        )

        tool_call_id = self._resolve_tool_call_id(tool_call)
        user_input = self._get_user_input(ctx, tool_call_id)
        auto_confirm_config = None
        if ctx.session:
            auto_confirm_config = ctx.session.get_state(INTERRUPT_AUTO_CONFIRM_KEY)
            if not isinstance(auto_confirm_config, dict):
                auto_confirm_config = {}

        decision = await self.resolve_interrupt(
            ctx=ctx,
            tool_call=tool_call,
            user_input=user_input,
            auto_confirm_config=auto_confirm_config,
        )
        ctx.extra["_interrupt_decision"] = decision
        self._apply_decision(ctx, tool_call, tool_name, decision)

    def update_config(self, config: dict, tool_names: Optional[Iterable[str]] = None) -> None:
        """Hot-update static permission config.

        Config updates must not shrink the rail interception surface. This rail
        checks every tool call regardless of whether the tool appears in config.
        """
        self._static_config = config
        self._engine.update_config(config)
        if tool_names is not None:
            self._tool_names.update(str(x).strip() for x in tool_names if str(x).strip())
        logger.info(
            "[PermissionEngine] permission.rail.config_updated intercept=all diagnostic_tool_names=%s",
            list(self._tool_names),
        )

    def _build_pending_permission_context(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        result: PermissionResult,
    ) -> dict[str, Any]:
        context: dict[str, Any] = {
            "tool_name": tool_name,
            "tool_args": dict(tool_args),
            "permission": result.permission.value,
            "matched_rule": result.matched_rule,
            "reason": result.reason,
        }
        if tool_name in _SHELL_PERMISSION_TOOLS:
            ask_subcommands = self._extract_ask_subcommands(result)
            if ask_subcommands:
                context["ask_subcommands"] = ask_subcommands
            context["would_persist_patterns"] = self._build_would_persist_patterns(
                tool_name,
                tool_args,
                ask_subcommands=ask_subcommands,
            )
            context["would_persist_whole_tool"] = False
        else:
            context["would_persist_patterns"] = []
            context["would_persist_whole_tool"] = True
        return context

    def _build_would_persist_patterns(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        *,
        ask_subcommands: list[str] | None = None,
    ) -> list[str]:
        command = str(tool_args.get("command", "") or tool_args.get("cmd", "") or "").strip()
        if not command:
            return []
        suggestions = build_permission_suggestions(
            tool_name,
            tool_args,
            shell_ast_result=parse_shell_for_permission(command),
            ask_subcommands=ask_subcommands,
            existing_patterns=self._existing_allow_override_patterns(),
        )
        return [item.pattern for item in suggestions]

    def _build_persist_allow_targets(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        result: PermissionResult | None = None,
    ) -> list[str]:
        if tool_name in _SHELL_PERMISSION_TOOLS:
            ask_subcommands = self._extract_ask_subcommands(result) if result is not None else []
            return self._build_would_persist_patterns(
                tool_name,
                tool_args,
                ask_subcommands=ask_subcommands,
            )
        return [tool_name] if tool_name else []

    @staticmethod
    def _format_inline_code_items(items: list[str]) -> str:
        cleaned = [str(item).strip() for item in items if str(item).strip()]
        return " ".join(f"`{item}`" for item in cleaned)

    @staticmethod
    def _display_matched_rule(tool_name: str, result: PermissionResult) -> str:
        raw = str(result.matched_rule or "").strip()
        if result.permission != PermissionLevel.ASK:
            return raw or "N/A"
        if tool_name in _SHELL_PERMISSION_TOOLS:
            return f"{tool_name}.shell_command.ask"
        if raw == "defaults.ask":
            return raw
        if raw == f"tools.{tool_name}":
            return f"{tool_name}.ask"
        if raw.startswith("tools."):
            configured_tool = raw.removeprefix("tools.").strip()
            if configured_tool:
                return f"{configured_tool}.ask"
        return raw or f"{tool_name}.ask"

    def _existing_allow_override_patterns(self) -> set[str]:
        raw = self._static_config.get("approval_overrides")
        if not isinstance(raw, list):
            return set()
        patterns: set[str] = set()
        for item in raw:
            if not isinstance(item, dict):
                continue
            if str(item.get("action") or "").strip().lower() != "allow":
                continue
            pattern = item.get("pattern")
            if isinstance(pattern, str) and pattern:
                patterns.add(pattern)
        return patterns

    @property
    def diagnostic_tool_names(self) -> set[str]:
        return set(self._tool_names)

    def build_pending_permission_context(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        result: PermissionResult,
    ) -> dict[str, Any]:
        return self._build_pending_permission_context(tool_name, tool_args, result)

    def build_permission_message(
        self,
        tool_call: Optional[ToolCall],
        result: PermissionResult,
    ) -> str:
        return self._build_message(tool_call, result)

    def build_acp_permission_request(
        self,
        tool_call: Optional[ToolCall],
        result: PermissionResult,
    ) -> dict[str, Any]:
        return self._build_acp_permission_request(tool_call, result)

    @staticmethod
    def _extract_ask_subcommands(result: PermissionResult) -> list[str]:
        """复用第一次 ``check_permission`` 已经算好的子命令权限结果。

        仅在 simple shell 多/单子命令场景下 ``result.subcommand_results`` 非空。
        这里只挑出 ``ASK`` 子命令；本身已被 rules 判为 ``allow`` 的子命令会被
        过滤掉，从而避免在用户「始终允许」时为它们再生成新的规则。
        """
        if not result or not result.subcommand_results:
            return []
        return [
            item.text
            for item in result.subcommand_results
            if item.permission == PermissionLevel.ASK and item.text
        ]

    async def resolve_interrupt(
        self,
        ctx: AgentCallbackContext,
        tool_call: Optional[ToolCall],
        user_input: Optional[Any],
        auto_confirm_config: Optional[dict] = None,
    ):
        tool_name = tool_call.name if tool_call is not None else ""
        normalized_name = self._normalize_tool_name(tool_name)
        tool_args = self._parse_tool_args(tool_call)
        auto_confirm_key = self._get_auto_confirm_key(tool_call)
        tool_call_id = self._resolve_tool_call_id(tool_call)

        logger.info(
            "[PermissionEngine] permission.rail.resolve tool=%s normalized=%s "
            "tool_args=%s auto_confirm_key=%s user_input_type=%s",
            tool_name, normalized_name, tool_args, auto_confirm_key,
            type(user_input).__name__ if user_input else None
        )

        from jiuwenclaw.agentserver.deep_agent.permissions.owner_scopes import (
            TOOL_PERMISSION_CONTEXT,
            check_avatar_permission,
            _resolve_owner_scope_level,
        )
        perm_ctx = TOOL_PERMISSION_CONTEXT.get()

        if perm_ctx is not None:
            logger.info(
                "[PermissionEngine] permission.rail.context scene=%s channel_id=%s principal_user_id=%s",
                perm_ctx.scene, perm_ctx.channel_id, perm_ctx.principal_user_id
            )
            if perm_ctx.scene == "group_digital_avatar":
                if user_input is None:
                    level = await check_avatar_permission(
                        normalized_name, tool_args,
                        channel_id=self._resolve_channel_id(),
                        session_id=None,
                    )
                    if level == "allow":
                        return self.approve()
                    return self.reject(
                        tool_result="[PERMISSION_DENIED] 该工具未被授权在数字分身场景下使用"
                    )
                return self.reject(tool_result="[PERMISSION_DENIED] 数字分身场景不支持交互审批")

            if perm_ctx.principal_user_id:
                owner_scopes = self._static_config.get("owner_scopes", {})
                logger.info(
                    "[PermissionEngine] permission.rail.owner_scope_lookup "
                    "channel_id=%s user_id=%s owner_scope_channels=%s",
                    perm_ctx.channel_id,
                    perm_ctx.principal_user_id,
                    list(owner_scopes.keys()) if owner_scopes else [],
                )
                if isinstance(owner_scopes, dict) and owner_scopes:
                    cid = perm_ctx.channel_id.strip()
                    uid = perm_ctx.principal_user_id.strip()
                    scope_cfg = (owner_scopes.get(cid) or {}).get(uid)
                    owner_level = _resolve_owner_scope_level(scope_cfg, normalized_name, tool_args)
                    if owner_level is not None:
                        logger.info(
                            "[PermissionEngine] permission.rail.owner_scope_match tool=%s normalized=%s level=%s",
                            tool_name, normalized_name, owner_level
                        )
                        if owner_level == "allow":
                            return self.approve()
                        return self.reject(
                            tool_result=f"[PERMISSION_DENIED] 该工具未被授权 (owner_scopes: {owner_level})"
                        )

        if user_input is None:
            logger.info(
                "[PermissionEngine] permission.rail.first_check tool=%s normalized=%s",
                tool_name, normalized_name
            )
            # 与磁盘上的 permissions 对齐：persist_cli_trusted_directory 等只更新了全局
            # PermissionEngine；若此处仍用旧的 _static_config 覆盖引擎，会抹掉刚写入的
            # approval_overrides / external_directory。
            perm = get_config().get("permissions")
            if isinstance(perm, dict):
                self.update_config(perm)
            elif self._engine is get_permission_engine():
                self._static_config = dict(self._engine.config)
            else:
                self._engine.update_config(self._static_config)
            result = await self._engine.check_permission(
                tool_name=normalized_name,
                tool_args=tool_args,
                channel_id=self._resolve_channel_id(),
            )

            if result.permission == PermissionLevel.ALLOW:
                logger.info(
                    "[PermissionEngine] permission.rail.result tool=%s decision=allow matched_rule=%s",
                    tool_name,
                    result.matched_rule,
                )
                return self.approve()

            if result.permission == PermissionLevel.DENY:
                logger.warning(
                    "[PermissionEngine] permission.rail.result tool=%s decision=deny matched_rule=%s",
                    tool_name,
                    result.matched_rule,
                )
                return self.reject(tool_result=f"[PERMISSION_DENIED] {result.reason or 'Operation not allowed'}")

            if self._is_auto_confirmed(auto_confirm_config, auto_confirm_key):
                logger.info(
                    "[PermissionEngine] permission.auto_confirm.hit tool=%s key=%s",
                    tool_name,
                    auto_confirm_key,
                )
                return self.approve()

            resolved_channel = self._resolve_channel_id()
            if resolved_channel == "acp":
                confirm_payload = await self._request_acp_permission(
                    ctx=ctx,
                    tool_call=tool_call,
                    result=result,
                    auto_confirm_key=auto_confirm_key,
                )
                if confirm_payload is None:
                    return self.reject(
                        tool_result=(
                            f"[PERMISSION_DENIED] {result.reason or 'Operation requires approval'} "
                            "(ACP permission request failed)"
                        )
                    )
                should_persist = confirm_payload.persist_allow
                persisted = False
                if should_persist:
                    persisted = persist_permission_allow_rule(
                        normalized_name,
                        tool_args,
                        permission_context=self._build_pending_permission_context(
                            normalized_name,
                            tool_args,
                            result,
                        ),
                    )
                    logger.info(
                        "[PermissionEngine] permission.persist.result tool=%s channel=acp persisted=%s",
                        tool_name,
                        persisted,
                    )
                if self._should_store_auto_confirm(
                    auto_confirm=confirm_payload.auto_confirm,
                    session=ctx.session,
                    auto_confirm_key=auto_confirm_key,
                    persisted=persisted,
                ):
                    self._store_auto_confirm(ctx, auto_confirm_key)
                if confirm_payload.approved:
                    decision = "allow_always" if confirm_payload.persist_allow else "allow_once"
                    logger.info(
                        "[PermissionEngine] permission.user.decision tool=%s channel=acp decision=%s persisted=%s",
                        tool_name,
                        decision,
                        persisted,
                    )
                    return self.approve()
                logger.info(
                    "[PermissionEngine] permission.user.decision tool=%s channel=acp decision=deny",
                    tool_name,
                )
                return self.reject(
                    tool_result=confirm_payload.feedback or "[PERMISSION_REJECTED] User rejected the request."
                )

            logger.info(
                "[PermissionEngine] permission.interrupt.ask tool=%s matched_rule=%s",
                tool_name,
                result.matched_rule,
            )
            self._store_pending_permission_context(
                ctx,
                tool_call_id,
                self._build_pending_permission_context(normalized_name, tool_args, result),
            )
            message = self._build_message(tool_call, result)
            return self.interrupt(InterruptRequest(
                message=message,
                payload_schema=ConfirmPayload.to_schema(),
            ))

        logger.info("[PermissionEngine] permission.rail.user_response tool=%s", tool_name)
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

        persisted = False
        pending_context = self._pop_pending_permission_context(
            ctx,
            tool_call_id,
            normalized_name,
            tool_args,
        )
        if payload.persist_allow:
            persisted = persist_permission_allow_rule(
                normalized_name,
                tool_args,
                permission_context=pending_context,
            )
            logger.info(
                "[PermissionEngine] permission.persist.result tool=%s channel=%s persisted=%s",
                tool_name,
                self._resolve_channel_id(),
                persisted,
            )

        if self._should_store_auto_confirm(
            auto_confirm=payload.auto_confirm,
            session=ctx.session,
            auto_confirm_key=auto_confirm_key,
            persisted=persisted,
        ):
            self._store_auto_confirm(ctx, auto_confirm_key)

        if payload.approved:
            decision = "allow_always" if payload.persist_allow else "allow_once"
            logger.info(
                "[PermissionEngine] permission.user.decision tool=%s channel=%s decision=%s persisted=%s",
                tool_name,
                self._resolve_channel_id(),
                decision,
                persisted,
            )
            return self.approve()

        logger.info(
            "[PermissionEngine] permission.user.decision tool=%s channel=%s decision=deny",
            tool_name,
            self._resolve_channel_id(),
        )
        return self.reject(tool_result=payload.feedback or "[PERMISSION_REJECTED] User rejected the request.")

    @staticmethod
    def _parse_tool_args(tool_call: Optional[ToolCall]) -> dict:
        if tool_call is None:
            return {}
        args = tool_call.arguments
        if isinstance(args, str):
            try:
                parsed = json.loads(args)
            except Exception:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        if isinstance(args, dict):
            return args
        return {}

    @staticmethod
    def _parse_confirm_payload(user_input: Any) -> Optional[PermissionConfirmResponse]:
        if isinstance(user_input, PermissionConfirmResponse):
            return user_input
        if isinstance(user_input, ConfirmPayload):
            return PermissionConfirmResponse(
                approved=user_input.approved,
                feedback=user_input.feedback,
                auto_confirm=user_input.auto_confirm,
            )
        if isinstance(user_input, dict):
            try:
                payload = ConfirmPayload.model_validate(user_input)
            except Exception:
                return None
            return PermissionConfirmResponse(
                approved=payload.approved,
                feedback=payload.feedback,
                auto_confirm=payload.auto_confirm,
                persist_allow=bool(user_input.get("persist_allow", False)),
            )
        if isinstance(user_input, str):
            try:
                raw_payload = json.loads(user_input)
            except Exception:
                return None
            if not isinstance(raw_payload, dict):
                return None
            return PermissionInterruptRail._parse_confirm_payload(raw_payload)
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
    def _store_auto_confirm(ctx: AgentCallbackContext, auto_confirm_key: str) -> None:
        config = ctx.session.get_state(INTERRUPT_AUTO_CONFIRM_KEY) or {}
        if not isinstance(config, dict):
            config = {}
        config[auto_confirm_key] = True
        ctx.session.update_state({INTERRUPT_AUTO_CONFIRM_KEY: config})
        logger.info("[PermissionEngine] permission.auto_confirm.store key=%s", auto_confirm_key)

    @staticmethod
    def _read_session_attr_value(session: Any, attr_name: str) -> Any:
        attr = getattr(session, attr_name, None)
        if not callable(attr):
            return attr
        try:
            return attr()
        except Exception:
            logger.debug(
                "[PermissionEngine] permission.rail.session_attr_read_failed attr=%s",
                attr_name,
                exc_info=True,
            )
            return None

    @staticmethod
    def _resolve_session_id(ctx: AgentCallbackContext) -> str | None:
        session = getattr(ctx, "session", None)
        if session is None:
            return None

        for attr_name in ("get_session_id", "session_id"):
            value = PermissionInterruptRail._read_session_attr_value(session, attr_name)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @staticmethod
    def _tool_kind_for_permission(tool_name: str) -> str:
        if tool_name in {"bash", "mcp_exec_command", "create_terminal", "exec_command"}:
            return "execute"
        if tool_name in {"read_file", "read_text_file", "memory_get"}:
            return "read"
        if tool_name in {"write_file", "write_text_file", "edit_file", "write"}:
            return "edit"
        if tool_name in {"grep", "glob_file_search", "mcp_free_search", "mcp_paid_search", "mcp_petal_search"}:
            return "search"
        if tool_name in {"fetch_webpage", "mcp_fetch_webpage"}:
            return "fetch"
        return "other"

    def _build_acp_permission_request(
        self,
        tool_call: Optional[ToolCall],
        result: PermissionResult,
    ) -> dict[str, Any]:
        tool_name = tool_call.name if tool_call else ""
        tool_args = self._parse_tool_args(tool_call)
        tool_call_id = str(getattr(tool_call, "id", "") or f"permission_{tool_name or 'tool'}").strip()
        descriptor = build_acp_tool_descriptor(
            tool_name,
            tool_args,
            tool_call_id=tool_call_id,
            status="pending",
            kind=self._tool_kind_for_permission(tool_name),
        )
        title = str(descriptor.get("title") or f"Approve `{tool_name}`")
        if result.reason:
            title = f"{title}: {result.reason}"

        request: dict[str, Any] = {
            "toolCall": {
                **descriptor,
                "title": title,
            },
            "options": [
                {
                    "optionId": "allow-once",
                    "name": "Allow once",
                    "kind": "allow_once",
                },
                {
                    "optionId": "allow-always",
                    "name": "Always allow",
                    "kind": "allow_always",
                },
                {
                    "optionId": "reject-once",
                    "name": "Reject",
                    "kind": "reject_once",
                },
            ],
        }
        permission_context = self._build_pending_permission_context(tool_name, tool_args, result)
        request["permissionContext"] = {
            "askSubcommands": permission_context.get("ask_subcommands", []),
            "wouldPersistPatterns": permission_context.get("would_persist_patterns", []),
            "wouldPersistWholeTool": bool(permission_context.get("would_persist_whole_tool", False)),
            "persistAllowTargets": self._build_persist_allow_targets(tool_name, tool_args, result),
            "displayMatchedRule": self._display_matched_rule(tool_name, result),
            "toolName": tool_name,
        }
        return request

    async def _request_acp_permission(
        self,
        ctx: AgentCallbackContext,
        tool_call: Optional[ToolCall],
        result: PermissionResult,
        auto_confirm_key: str,
    ) -> PermissionConfirmResponse | None:
        session_id = self._resolve_session_id(ctx)
        if not session_id:
            logger.warning("[PermissionEngine] permission.acp.request_skipped reason=missing_session_id")
            return None

        from jiuwenclaw.agentserver.tools.acp_output_tools import get_acp_output_manager

        request_params = self._build_acp_permission_request(tool_call, result)
        logger.info(
            "[PermissionEngine] permission.acp.request_start session_id=%s tool=%s auto_confirm_key=%s",
            session_id,
            tool_call.name if tool_call else "",
            auto_confirm_key,
        )
        try:
            response = await get_acp_output_manager().send_jsonrpc_request(
                "session/request_permission",
                request_params,
                session_id=session_id,
            )
        except Exception as exc:
            logger.warning("[PermissionEngine] permission.acp.request_failed error=%s", exc)
            return None

        if not isinstance(response, dict):
            logger.warning("[PermissionEngine] permission.acp.invalid_response response=%s", response)
            return None

        if isinstance(response.get("error"), dict):
            err = response["error"]
            message = str(err.get("message") or "Permission request failed")
            logger.warning("[PermissionEngine] permission.acp.error_response message=%s", message)
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
                return PermissionConfirmResponse(
                    approved=True,
                    auto_confirm=True,
                    persist_allow=True,
                    feedback="",
                )
            if option_id in {"reject-once", "reject-always"}:
                return PermissionConfirmResponse(
                    approved=False,
                    auto_confirm=False,
                    feedback="[PERMISSION_REJECTED] User rejected the request.",
                )
            logger.warning(
                "[PermissionEngine] permission.acp.unknown_option option_id=%s",
                option_id,
            )
            return PermissionConfirmResponse(
                approved=False,
                auto_confirm=False,
                feedback=f"[PERMISSION_DENIED] Unknown permission option: {option_id or 'empty'}",
            )

        if outcome_kind == "cancelled":
            return PermissionConfirmResponse(
                approved=False,
                auto_confirm=False,
                feedback="[PERMISSION_REJECTED] Permission request was cancelled.",
            )

        logger.warning(
            "[PermissionEngine] permission.acp.unknown_outcome outcome=%s payload=%s",
            outcome_kind,
            result_payload,
        )
        return PermissionConfirmResponse(
            approved=False,
            auto_confirm=False,
            feedback="[PERMISSION_DENIED] Invalid ACP permission response.",
        )

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
        persist_targets = self._build_persist_allow_targets(tool_name, tool_args, result)
        persist_targets_text = self._format_inline_code_items(persist_targets)
        target_suffix = ""
        if tool_name in _SHELL_PERMISSION_TOOLS and persist_targets_text:
            target_suffix = f" {persist_targets_text}"

        parts = [
            f"**工具 `{tool_name}` 需要授权才能执行{target_suffix}**\n\n",
            f"**安全风险评估：** {risk.get('icon', '')} **{risk.get('level', '')}风险**\n\n",
            f"> {risk.get('explanation', '')}\n\n",
        ]

        args_preview = self._format_args_preview(tool_args)
        if args_preview and args_preview != "{}":
            parts.append(f"参数：\n```json\n{args_preview}\n```\n")

        parts.append(f"\n匹配规则：`{self._display_matched_rule(tool_name, result)}`")

        external_paths = getattr(result, "external_paths", None) or []
        if external_paths:
            parts.append(f"\n\n**外部路径：** `{', '.join(external_paths)}`")

        parts.append(self._build_always_allow_hint(tool_call, result))

        return "".join(parts)

    def _build_always_allow_hint(
        self,
        tool_call: Optional[ToolCall],
        result: PermissionResult | None = None,
    ) -> str:
        if tool_call is None:
            return ""

        tool_name = tool_call.name or ""
        tool_args = self._parse_tool_args(tool_call)
        targets = self._build_persist_allow_targets(tool_name, tool_args, result)
        targets_text = self._format_inline_code_items(targets)
        if targets_text:
            return f'\n\n> 选择"总是允许"将写入持久化允许规则：{targets_text}'

        auto_confirm_key = self._get_auto_confirm_key(tool_call)
        if auto_confirm_key:
            return f'\n\n> 选择"总是允许"将在当前会话自动放行 `{auto_confirm_key}` 调用'
        return ""


__all__ = [
    "PermissionInterruptRail",
]
