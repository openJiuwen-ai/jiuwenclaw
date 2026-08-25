# -*- coding: utf-8 -*-
"""Coding Guard —— 运行时执行护栏。

before_tool_call 对命令与文件类工具跑与 Tool 同一套策略：
block 跳过、confirm 走平台确认、warn 放行并在 after_tool_call 回写原因。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.single_agent.interrupt.response import InterruptRequest
from openjiuwen.core.single_agent.interrupt.state import RESUME_USER_INPUT_KEY
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.interrupt.confirm_rail import ConfirmPayload
from openjiuwen.harness.rails.interrupt.interrupt_base import BaseInterruptRail

from ..tools.risk_scan_tool import RULES
from ..tools.risk_scan_tool import RAIL_PRIORITY
from ..tools.risk_scan_tool import Finding
from ..tools.risk_scan_tool import ScanResult
from ..tools.risk_scan_tool import evaluate
from ..tools.risk_scan_tool import scan_path_targets
from ..tools.risk_scan_tool import scan_text
from ..tools.risk_scan_tool import scan_text_for_files

logger = logging.getLogger(__name__)

_COMMAND_TOOL_KEYWORDS = (
    "bash",
    "shell",
    "cmd",
    "command",
    "powershell",
    "pwsh",
    "exec",
)
_DELETE_TOOL_NAMES = frozenset(
    {"delete", "delete_file", "remove", "unlink", "rmdir", "rm"}
)
_WRITE_TOOL_NAMES = frozenset(
    {
        "write_file",
        "create_file",
        "overwrite_file",
        "append_file",
        "touch",
        "edit_file",
    }
)
_FILE_TOOL_NAMES = (
    _DELETE_TOOL_NAMES
    | _WRITE_TOOL_NAMES
    | frozenset({"rename", "move", "mv", "cp", "copy"})
)
_PATH_KEYS = (
    "file_path",
    "path",
    "target",
    "target_path",
    "old_path",
    "new_path",
    "src",
    "source",
    "dst",
    "dest",
    "old",
    "new",
    "paths",
    "dir",
    "directory",
)
_CONTENT_KEYS = (
    "content",
    "contents",
    "text",
    "data",
    "body",
    "script",
    "code",
    "command",
    "cmd",
    "new_string",
    "old_string",
    "new_str",
    "old_str",
)
_DELETE_COUNT_ESCALATE_AT = 3
_AUTO_CONFIRM_PREFIX = "coding-guard:"
_STATE_DIR_NAME = ".coding-guard-state"
_REJECT_VALUES = frozenset(
    {
        "拒绝",
        "reject",
        "Reject",
        "deny",
        "Deny",
        "no",
        "No",
        "否",
        "取消",
        "cancel",
        "Cancel",
    }
)
_APPROVE_VALUES = frozenset(
    {
        "批准",
        "approve",
        "Approve",
        "本次允许",
        "Proceed",
        "开始执行",
        "是",
        "yes",
        "Yes",
    }
)
_SESSION_ALLOW_VALUES = frozenset(
    {
        "会话内记住",
        "session_allow",
        "Session Allow",
        "永久记住",
        "always_allow",
        "Always Allow",
        "总是允许",
        "allow_always",
    }
)


class ExecutionGuardRail(BaseInterruptRail):
    """运行时护栏：对工具调用做 allow/warn/confirm/block 决策。"""

    priority: int = RAIL_PRIORITY

    def __init__(self) -> None:
        super().__init__(tool_names=[])
        self._agent: Any | None = None
        self._workspace: Any | None = None

    def init(self, agent: Any) -> None:
        self._agent = agent

    def set_workspace(self, workspace: Any) -> None:
        self._workspace = workspace

    def uninit(self, agent: Any) -> None:  # noqa: ARG002
        self._agent = None
        self._workspace = None

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        await self._ensure_resume_rail()
        tool_name = str(getattr(ctx.inputs, "tool_name", "") or "")
        tool_args = getattr(ctx.inputs, "tool_args", None)
        tool_call: ToolCall | None = getattr(ctx.inputs, "tool_call", None)
        if not tool_args and tool_call is not None:
            tool_args = getattr(tool_call, "arguments", None)
        try:
            result = self._decide(ctx, tool_name, tool_args)
        except Exception:  # noqa: BLE001
            logger.exception(
                "[ExecutionGuardRail] 风险检测异常 tool=%s", tool_name
            )
            if self._is_side_effect_tool(tool_name):
                self._reject_tool(
                    ctx,
                    tool_call,
                    tool_name,
                    ScanResult(
                        "high",
                        "block",
                        False,
                        [],
                        "风险检测异常，已拦截本次写删/执行",
                    ),
                    reason="风险检测异常",
                )
            return
        if result is None:
            return
        if result.decision == "block":
            self._reject_tool(ctx, tool_call, tool_name, result)
            return
        if result.decision == "confirm":
            self._handle_confirm(ctx, tool_call, result)
            return
        if result.decision == "warn":
            warns: list[dict[str, str]] = list(ctx.extra.get("_guardrail_warns", []))
            warns.extend(self._warn_entries(result))
            ctx.extra["_guardrail_warns"] = warns

    def _decide(
        self, ctx: AgentCallbackContext, tool_name: str, tool_args: Any
    ) -> ScanResult | None:
        tool_args = self._normalize_tool_args(tool_args)
        lowered = (tool_name or "").lower()
        ws_root = self._workspace_root()
        findings: list[Finding] = []

        if any(keyword in lowered for keyword in _COMMAND_TOOL_KEYWORDS):
            command_text = self._first_str(
                tool_args, ("command", "cmd", "script", "code")
            )
            findings.extend(
                scan_text(
                    command_text, "shell", workspace_root=ws_root, location=tool_name
                ).findings
            )

        if lowered in _FILE_TOOL_NAMES or "file" in lowered or "write" in lowered:
            action = "delete" if lowered in _DELETE_TOOL_NAMES else "write"
            findings.extend(
                scan_path_targets(
                    self._collect_paths(tool_args),
                    action=action,
                    workspace_root=ws_root,
                    location=tool_name,
                )
            )
            if action == "write":
                findings.extend(
                    scan_text_for_files(
                        self._collect_contents(tool_args),
                        workspace_root=ws_root,
                        location=tool_name,
                    )
                )

        if lowered in _DELETE_TOOL_NAMES:
            prior = self._delete_count(ctx)
            if prior >= _DELETE_COUNT_ESCALATE_AT - 1:
                rule = RULES.get("delete-batch-escalation")
                findings.append(
                    Finding(
                        rule_id="delete-batch-escalation",
                        category="boundary",
                        severity=rule.severity if rule else "medium",
                        action=rule.action if rule else "confirm",
                        message=rule.message
                        if rule and rule.message
                        else (f"本会话已累计 {prior + 1} 次删除操作，需确认后继续"),
                        snippet="",
                        recommendation=rule.recommendation if rule else "",
                        location=tool_name,
                    )
                )

        if not findings:
            return None
        return evaluate(findings)

    def _handle_confirm(
        self, ctx: AgentCallbackContext, tool_call: ToolCall | None, result: ScanResult
    ) -> None:
        tool_name = str(getattr(ctx.inputs, "tool_name", "") or "")
        tool_call_id = self._resolve_tool_call_id(tool_call)
        user_input = self._get_user_input(ctx, tool_call_id)
        rule_id = result.findings[0].rule_id if result.findings else ""
        auto_key = f"{_AUTO_CONFIRM_PREFIX}{rule_id}" if rule_id else ""

        if user_input is None:
            sibling_reject = self._resume_rejection_payload(ctx)
            if sibling_reject is not None:
                self._reject_tool(
                    ctx,
                    tool_call,
                    tool_name,
                    result,
                    reason=sibling_reject.feedback or "用户拒绝",
                )
                return
            if auto_key and self._is_auto_confirmed(
                self._auto_confirm_cfg(ctx), auto_key
            ):
                return
            self._apply_decision(
                ctx,
                tool_call,
                tool_name,
                self.interrupt(
                    InterruptRequest(
                        message=self._build_confirm_message(tool_name, result),
                        payload_schema=ConfirmPayload.to_schema(),
                    )
                ),
            )
            return

        payload = self._parse_confirm_payload(user_input)
        if payload is None:
            self._reject_tool(
                ctx,
                tool_call,
                tool_name,
                result,
                reason="未能解析确认结果，已按拒绝处理",
            )
            return
        if payload.approved:
            if payload.auto_confirm:
                self._store_auto_confirm(ctx, auto_key)
            return
        self._reject_tool(ctx, tool_call, tool_name, result, reason=payload.feedback)

    def _reject_tool(
        self,
        ctx: AgentCallbackContext,
        tool_call: ToolCall | None,
        tool_name: str,
        result: ScanResult,
        *,
        reason: str = "",
    ) -> None:
        message = self._build_block_message(tool_name, result, reason)
        status = "blocked" if result.decision == "block" else "rejected"
        tool_result = {
            "success": False,
            "status": status,
            "executed": False,
            "retryable": False,
            "source": "coding-guard",
            "tool_name": tool_name,
            "decision": result.decision,
            "message": message,
        }
        self._apply_decision(
            ctx,
            tool_call,
            tool_name,
            self.reject(tool_result=tool_result),
        )

    @staticmethod
    def _warn_entries(result: ScanResult) -> list[dict[str, str]]:
        return [
            {
                "source": "coding-guard",
                "rule_id": finding.rule_id,
                "severity": finding.severity,
                "message": finding.message,
            }
            for finding in result.findings
        ]

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        warns = ctx.extra.get("_guardrail_warns")
        if warns:
            result = getattr(ctx.inputs, "tool_result", None)
            if isinstance(result, dict):
                existing = result.get("guardrail_warns")
                if not isinstance(existing, list):
                    existing = []
                result["guardrail_warns"] = existing + warns
        tool_name = str(getattr(ctx.inputs, "tool_name", "") or "")
        if tool_name.lower() not in _DELETE_TOOL_NAMES:
            return
        count = self._bump_delete_count(ctx)
        if count != _DELETE_COUNT_ESCALATE_AT:
            return
        try:
            ctx.push_steering(
                "[coding-guard] 本会话已累计多次删除操作，后续删除将要求确认；"
                "若为批量清理请先明确列出目标文件。"
            )
        except Exception:  # noqa: BLE001
            logger.debug("[ExecutionGuardRail] push_steering failed", exc_info=True)

    def _delete_count(self, ctx: AgentCallbackContext) -> int:
        return int(self._load_state(ctx).get("delete_count", 0))

    def _bump_delete_count(self, ctx: AgentCallbackContext) -> int:
        state = self._load_state(ctx)
        count = int(state.get("delete_count", 0)) + 1
        state["delete_count"] = count
        self._save_state(ctx, state)
        return count

    def _load_state(self, ctx: AgentCallbackContext) -> dict[str, Any]:
        path = self._state_path(ctx)
        if path is None or not path.is_file():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_state(self, ctx: AgentCallbackContext, state: dict[str, Any]) -> None:
        path = self._state_path(ctx)
        if path is None:
            return
        tmp_name: str | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                dir=str(path.parent), prefix=".state-", suffix=".tmp"
            )
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(state, handle, ensure_ascii=False)
            os.replace(tmp_name, str(path))
            tmp_name = None
        except OSError:
            logger.debug("[ExecutionGuardRail] 状态写入失败", exc_info=True)
        finally:
            if tmp_name is not None and os.path.exists(tmp_name):
                os.unlink(tmp_name)

    def _state_path(self, ctx: AgentCallbackContext) -> Path | None:
        root = self._workspace_root()
        if not root:
            return None
        session_id = self._session_id(ctx)
        if not session_id:
            return None
        safe = (
            "".join(ch for ch in session_id if ch.isalnum() or ch in "-_") or "default"
        )
        return Path(root) / _STATE_DIR_NAME / f"{safe}.json"

    def _session_id(self, ctx: AgentCallbackContext) -> str:
        session = getattr(ctx, "session", None)
        if session is None:
            return ""
        for attr in ("get_session_id", "session_id"):
            method = getattr(session, attr, None)
            if callable(method):
                try:
                    return str(method())
                except Exception:  # noqa: BLE001
                    continue
            if method:
                return str(method)
        return ""

    def _build_block_message(
        self, tool_name: str, result: ScanResult, reason: str
    ) -> str:
        lines = [
            f"[coding-guard] 已拦截 {tool_name or '工具'} 调用（来源：coding-guard，非平台权限拦截）",
        ]
        for finding in result.findings:
            lines.append(
                f"- rule_id={finding.rule_id} severity={finding.severity} action=block: {finding.message}"
            )
        if reason:
            lines.append(f"用户拒绝原因：{reason}")
        lines.append("如确有必要，请说明目的后确认；涉及密钥请先完成轮换/备份。")
        return "\n".join(lines)

    def _build_confirm_message(self, tool_name: str, result: ScanResult) -> str:
        rule_id = result.findings[0].rule_id if result.findings else ""
        lines = [
            f"**[coding-guard] {tool_name or '工具'} 调用需要确认**（来源：coding-guard）\n",
            "请确认是否允许该操作：\n",
        ]
        for finding in result.findings:
            lines.append(
                f"- `{finding.rule_id}`（{finding.severity}）：{finding.message}"
            )
        lines.append(f"\n匹配规则：`{rule_id}`")
        lines.append("\n> 选择「会话内记住」可本会话放行同类调用（auto-confirm）。")
        return "\n".join(lines)

    @staticmethod
    def _normalize_tool_args(tool_args: Any) -> dict[str, Any]:
        if isinstance(tool_args, dict):
            return tool_args
        if isinstance(tool_args, str):
            try:
                parsed = json.loads(tool_args)
            except (json.JSONDecodeError, TypeError):
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}

    @staticmethod
    def _first_str(tool_args: Any, keys: tuple[str, ...]) -> str:
        if not isinstance(tool_args, dict):
            return ""
        for key in keys:
            value = tool_args.get(key)
            if isinstance(value, str):
                return value
            if isinstance(value, (list, tuple)):
                joined = "\n".join(str(item) for item in value if isinstance(item, str))
                if joined.strip():
                    return joined
        return ""

    def _collect_paths(self, tool_args: Any) -> list[str]:
        if not isinstance(tool_args, dict):
            return []
        paths: list[str] = []
        for key, value in tool_args.items():
            if key not in _PATH_KEYS:
                continue
            if isinstance(value, str):
                paths.append(value)
            elif isinstance(value, (list, tuple)):
                paths.extend(item for item in value if isinstance(item, str))
        return paths

    def _collect_contents(self, tool_args: Any) -> list[str]:
        if not isinstance(tool_args, dict):
            return []
        contents: list[str] = []
        for key, value in tool_args.items():
            if isinstance(value, str) and key in _CONTENT_KEYS:
                contents.append(value)
            elif isinstance(value, (list, tuple)):
                contents.extend(
                    item for item in value if isinstance(item, str) and item.strip()
                )
        return contents

    def _workspace_root(self) -> str:
        try:
            from openjiuwen.core.sys_operation.cwd import get_workspace

            workspace = get_workspace()
            if workspace:
                return workspace
        except Exception:  # noqa: BLE001
            pass
        workspace = getattr(self._workspace, "root_path", None)
        if workspace:
            return str(workspace)
        if self._agent is not None:
            attached = getattr(self._agent, "workspace", None)
            root = getattr(attached, "root_path", None)
            if root:
                return str(root)
        try:
            from openjiuwen.core.sys_operation.cwd import get_cwd

            return get_cwd()
        except Exception:  # noqa: BLE001
            return ""

    @staticmethod
    def _is_side_effect_tool(tool_name: str) -> bool:
        lowered = (tool_name or "").lower()
        if lowered in _DELETE_TOOL_NAMES or lowered in _WRITE_TOOL_NAMES:
            return True
        if any(keyword in lowered for keyword in _COMMAND_TOOL_KEYWORDS):
            return True
        return "write" in lowered or "edit_file" in lowered

    def _resume_rejection_payload(
        self, ctx: AgentCallbackContext
    ) -> ConfirmPayload | None:
        """Resume 答卷绑在 ask_user 的 id 上时，拒绝必须同样跳过并行的写工具。"""
        raw_input = ctx.extra.get(RESUME_USER_INPUT_KEY)
        candidates: list[Any] = []
        if isinstance(raw_input, InteractiveInput):
            candidates.extend(raw_input.user_inputs.values())
        elif raw_input is not None:
            candidates.append(raw_input)
        for item in candidates:
            payload = self._parse_confirm_payload(item)
            if payload is not None and not payload.approved:
                return payload
        return None

    @staticmethod
    def _tokens_from_user_input(user_input: Any) -> list[str]:
        if isinstance(user_input, str) and user_input.strip():
            return [user_input.strip()]
        if not isinstance(user_input, dict):
            return []
        raw_values: list[Any] = []
        answers = user_input.get("answers")
        if isinstance(answers, dict):
            raw_values.extend(answers.values())
        elif isinstance(answers, list):
            raw_values.extend(answers)
        selected = user_input.get("selected_options")
        if isinstance(selected, list):
            raw_values.extend(selected)
        tokens: list[str] = []
        for item in raw_values:
            if isinstance(item, str) and item.strip():
                tokens.append(item.strip())
            elif isinstance(item, (list, tuple)):
                tokens.extend(
                    str(part).strip() for part in item if str(part).strip()
                )
        return tokens

    @staticmethod
    def _payload_from_tokens(tokens: list[str]) -> ConfirmPayload | None:
        if any(token in _REJECT_VALUES for token in tokens):
            reason = next(
                (token for token in tokens if token in _REJECT_VALUES), "用户拒绝"
            )
            return ConfirmPayload(approved=False, feedback=reason)
        if any(token in _SESSION_ALLOW_VALUES for token in tokens):
            return ConfirmPayload(approved=True, auto_confirm=True)
        if any(token in _APPROVE_VALUES for token in tokens):
            return ConfirmPayload(approved=True, auto_confirm=False)
        return None

    @staticmethod
    def _parse_confirm_payload(user_input: Any) -> ConfirmPayload | None:
        if isinstance(user_input, ConfirmPayload):
            return user_input
        if isinstance(user_input, dict):
            try:
                return ConfirmPayload.model_validate(user_input)
            except Exception:  # noqa: BLE001
                from_tokens = ExecutionGuardRail._payload_from_tokens(
                    ExecutionGuardRail._tokens_from_user_input(user_input)
                )
                if from_tokens is not None:
                    return from_tokens
                return None
        if isinstance(user_input, str):
            from_token = ExecutionGuardRail._payload_from_tokens(
                ExecutionGuardRail._tokens_from_user_input(user_input)
            )
            if from_token is not None:
                return from_token
            try:
                raw = json.loads(user_input)
            except (ValueError, TypeError):
                return None
            if isinstance(raw, dict):
                return ExecutionGuardRail._parse_confirm_payload(raw)
        return None

    async def _ensure_resume_rail(self) -> None:
        """Register a durable resume handler not owned by the plugin load record."""
        if self._agent is None or isinstance(self, ExecutionGuardResumeRail):
            return
        if self._agent.find_rail_by_name("ExecutionGuardResumeRail") is not None:
            return
        await self._agent.register_rail(ExecutionGuardResumeRail())

    @staticmethod
    def _auto_confirm_cfg(ctx: AgentCallbackContext) -> dict[str, Any]:
        if ctx.session is None:
            return {}
        try:
            from openjiuwen.core.single_agent.interrupt.state import (
                INTERRUPT_AUTO_CONFIRM_KEY,
            )

            config = ctx.session.get_state(INTERRUPT_AUTO_CONFIRM_KEY)
            return config if isinstance(config, dict) else {}
        except Exception:  # noqa: BLE001
            return {}

    @staticmethod
    def _is_auto_confirmed(config: dict[str, Any], key: str) -> bool:
        return bool(key and config.get(key))

    @classmethod
    def _store_auto_confirm(cls, ctx: AgentCallbackContext, key: str) -> None:
        if ctx.session is None or not key:
            return
        try:
            from openjiuwen.core.single_agent.interrupt.state import (
                INTERRUPT_AUTO_CONFIRM_KEY,
            )

            config = cls._auto_confirm_cfg(ctx)
            config[key] = True
            ctx.session.update_state({INTERRUPT_AUTO_CONFIRM_KEY: config})
        except Exception:  # noqa: BLE001
            logger.debug("[ExecutionGuardRail] auto-confirm 存储失败", exc_info=True)


class ExecutionGuardResumeRail(ExecutionGuardRail):
    """Consume confirmation answers after the request adapter unloads the plugin."""

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        if RESUME_USER_INPUT_KEY not in ctx.extra or ctx.extra.get("_skip_tool"):
            return
        await super().before_tool_call(ctx)


__all__ = ["ExecutionGuardRail", "ExecutionGuardResumeRail"]
