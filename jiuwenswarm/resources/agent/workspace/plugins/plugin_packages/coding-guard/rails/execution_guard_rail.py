# -*- coding: utf-8 -*-
"""工具执行阶段的独立安全门禁 Rail。

本模块只判断当前真实工具调用是否允许，不依赖或调用安全审查 Tool。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.single_agent.interrupt.response import InterruptRequest
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.core.sys_operation.cwd import get_project_root, get_workspace
from openjiuwen.harness.rails.interrupt.confirm_rail import ConfirmPayload
from openjiuwen.harness.rails.interrupt.interrupt_base import BaseInterruptRail


logger = logging.getLogger(__name__)

RAIL_PRIORITY = 95
_COMMAND_KEYWORDS = ("bash", "shell", "cmd", "command", "powershell", "pwsh", "exec")
_DELETE_TOOLS = frozenset({"delete", "delete_file", "remove", "unlink", "rmdir", "rm"})
_WRITE_TOOLS = frozenset(
    {"write_file", "create_file", "overwrite_file", "append_file", "edit_file", "touch"}
)
_FILE_TOOLS = _DELETE_TOOLS | _WRITE_TOOLS | frozenset({"rename", "move", "mv", "copy", "cp"})
_PATH_KEYS = (
    "path",
    "file_path",
    "target",
    "target_path",
    "src",
    "source",
    "dst",
    "dest",
    "old_path",
    "new_path",
    "paths",
)
_CONTENT_KEYS = ("content", "text", "data", "body", "new_string", "script", "code")
_CREDENTIAL_FILE = re.compile(
    r"(^|[/\\])(?:\.env|credentials\.json|id_rsa|id_ed25519|[^/\\]+\.(?:pem|key|p12|pfx))$",
    re.IGNORECASE,
)
_SECRET_CONTENT = re.compile(
    r"(?:\bAKIA[0-9A-Z]{16}\b|\bsk-[A-Za-z0-9_-]{20,}\b|"
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)"
)
_ROOT_DELETE = re.compile(
    r"\brm\s+(?:-[A-Za-z]*r[A-Za-z]*f[A-Za-z]*|-[A-Za-z]*f[A-Za-z]*r[A-Za-z]*)\s+"
    r"(?:/|~|[A-Za-z]:\\)(?:\s|$)"
)
_DOWNLOAD_EXECUTE = re.compile(
    r"\b(?:curl|wget)\b[^\n|]*\|\s*(?:sh|bash|zsh|powershell|pwsh)\b",
    re.IGNORECASE,
)
_DESTRUCTIVE_GIT = re.compile(
    r"\bgit\s+(?:reset\s+--hard|clean\s+-[A-Za-z]*f|push\b[^\n]*--force)\b",
    re.IGNORECASE,
)
_APPROVE_VALUES = frozenset({"批准", "approve", "本次允许", "Proceed", "开始执行", "是", "yes", "Yes"})


@dataclass(frozen=True)
class _GuardDecision:
    decision: str
    policy_id: str = ""
    severity: str = "none"
    message: str = ""


class ExecutionGuardRail(BaseInterruptRail):
    """对实际命令和文件副作用执行确定性门禁。"""

    priority: int = RAIL_PRIORITY

    def __init__(self) -> None:
        super().__init__(tool_names=[])

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        tool_name = str(getattr(ctx.inputs, "tool_name", "") or "")
        tool_call: ToolCall | None = getattr(ctx.inputs, "tool_call", None)
        raw_args = getattr(ctx.inputs, "tool_args", None)
        if raw_args is None and tool_call is not None:
            raw_args = getattr(tool_call, "arguments", None)
        try:
            decision = self._evaluate_call(tool_name, raw_args)
        except Exception:  # noqa: BLE001
            logger.exception("[ExecutionGuardRail] 执行策略判断异常 tool=%s", tool_name)
            if self._is_side_effect_tool(tool_name):
                self._reject(
                    ctx,
                    tool_call,
                    tool_name,
                    _GuardDecision("deny", "guard-evaluation-failed", "high", "安全门禁异常，已拒绝副作用操作。"),
                )
            return

        if decision.decision == "deny":
            self._reject(ctx, tool_call, tool_name, decision)
        elif decision.decision == "require_approval":
            self._require_approval(ctx, tool_call, tool_name, decision)
        elif decision.decision == "warn":
            ctx.extra["_coding_guard_warn"] = {
                "source": "coding-guard",
                "policy_id": decision.policy_id,
                "severity": decision.severity,
                "message": decision.message,
            }

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        warning = ctx.extra.get("_coding_guard_warn")
        result = getattr(ctx.inputs, "tool_result", None)
        if warning and isinstance(result, dict):
            result.setdefault("guardrail_warns", []).append(warning)

    def _evaluate_call(self, tool_name: str, raw_args: Any) -> _GuardDecision:
        args = self._normalize_args(raw_args)
        lowered = tool_name.lower()

        if any(keyword in lowered for keyword in _COMMAND_KEYWORDS):
            command = self._first_text(args, ("command", "cmd", "script", "code"))
            decision = self._evaluate_command(command)
            if decision.decision != "allow":
                return decision

        if lowered in _FILE_TOOLS or "file" in lowered or "write" in lowered:
            action = "delete" if lowered in _DELETE_TOOLS else "write"
            paths = self._collect_values(args, _PATH_KEYS)
            decision = self._evaluate_paths(paths, action)
            if decision.decision != "allow":
                return decision
            if action == "write":
                contents = self._collect_values(args, _CONTENT_KEYS)
                if any(_SECRET_CONTENT.search(content) for content in contents):
                    return _GuardDecision(
                        "require_approval",
                        "secret-content-write",
                        "high",
                        "本次写入包含疑似密钥或私钥内容。",
                    )

        return _GuardDecision("allow")

    def _evaluate_command(self, command: str) -> _GuardDecision:
        if _ROOT_DELETE.search(command):
            return _GuardDecision(
                "deny",
                "destructive-root-command",
                "critical",
                "禁止针对根目录、磁盘根目录或用户目录执行递归强制删除。",
            )
        if _DOWNLOAD_EXECUTE.search(command):
            return _GuardDecision(
                "require_approval",
                "download-and-execute",
                "high",
                "命令将网络内容直接交给命令解释器执行。",
            )
        if _DESTRUCTIVE_GIT.search(command):
            return _GuardDecision(
                "require_approval",
                "destructive-git-command",
                "high",
                "命令可能不可逆地丢弃本地或远程 Git 历史。",
            )
        return _GuardDecision("allow")

    def _evaluate_paths(self, paths: list[str], action: str) -> _GuardDecision:
        if action == "delete" and len(paths) >= 3:
            return _GuardDecision(
                "require_approval",
                "batch-delete",
                "medium",
                "本次调用将删除多个路径。",
            )
        for path in paths:
            if _CREDENTIAL_FILE.search(path):
                decision = "deny" if action == "delete" else "require_approval"
                return _GuardDecision(
                    decision,
                    f"credential-file-{action}",
                    "high",
                    f"本次操作将{action}凭据或私钥文件。",
                )
            if self._outside_workspace(path):
                return _GuardDecision(
                    "require_approval",
                    "outside-workspace",
                    "high",
                    "本次操作目标位于工作区之外。",
                )
        return _GuardDecision("allow")

    def _outside_workspace(self, path: str) -> bool:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            return False
        candidate = candidate.resolve()
        for root in (get_project_root(), get_workspace()):
            if not root:
                continue
            try:
                candidate.relative_to(Path(root).resolve())
                return False
            except ValueError:
                continue
        return True

    def _require_approval(
        self,
        ctx: AgentCallbackContext,
        tool_call: ToolCall | None,
        tool_name: str,
        decision: _GuardDecision,
    ) -> None:
        tool_call_id = self._resolve_tool_call_id(tool_call)
        user_input = self._get_user_input(ctx, tool_call_id)
        if user_input is None:
            request = InterruptRequest(
                message=(
                    f"**[coding-guard] {tool_name or '工具'} 调用需要确认**\n\n"
                    f"- 策略：`{decision.policy_id}`\n"
                    f"- 风险：{decision.message}"
                ),
                payload_schema=ConfirmPayload.to_schema(),
            )
            self._apply_decision(ctx, tool_call, tool_name, self.interrupt(request))
            return
        payload = self._parse_confirmation(user_input)
        if payload is not None and payload.approved:
            return
        reason = payload.feedback if payload is not None else "未获得有效批准"
        self._reject(ctx, tool_call, tool_name, decision, reason)

    def _reject(
        self,
        ctx: AgentCallbackContext,
        tool_call: ToolCall | None,
        tool_name: str,
        decision: _GuardDecision,
        reason: str = "",
    ) -> None:
        message = f"[coding-guard] {decision.message}"
        if reason:
            message = f"{message} 原因：{reason}"
        result = {
            "success": False,
            "status": "denied" if decision.decision == "deny" else "rejected",
            "executed": False,
            "retryable": False,
            "source": "coding-guard",
            "tool_name": tool_name,
            "decision": decision.decision,
            "policy_id": decision.policy_id,
            "message": message,
        }
        self._apply_decision(ctx, tool_call, tool_name, self.reject(tool_result=result))

    @staticmethod
    def _parse_confirmation(user_input: Any) -> ConfirmPayload | None:
        if isinstance(user_input, ConfirmPayload):
            return user_input
        if isinstance(user_input, dict):
            try:
                return ConfirmPayload.model_validate(user_input)
            except Exception:  # noqa: BLE001
                return None
        if isinstance(user_input, str):
            value = user_input.strip()
            if value in _APPROVE_VALUES:
                return ConfirmPayload(approved=True)
            try:
                parsed = json.loads(value)
            except (TypeError, ValueError):
                return ConfirmPayload(approved=False, feedback=value)
            return ExecutionGuardRail._parse_confirmation(parsed)
        return None

    @staticmethod
    def _normalize_args(raw_args: Any) -> dict[str, Any]:
        if isinstance(raw_args, dict):
            return raw_args
        if isinstance(raw_args, str):
            try:
                parsed = json.loads(raw_args)
            except (TypeError, ValueError):
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}

    @staticmethod
    def _first_text(args: dict[str, Any], keys: tuple[str, ...]) -> str:
        for key in keys:
            value = args.get(key)
            if isinstance(value, str):
                return value
        return ""

    @staticmethod
    def _collect_values(args: dict[str, Any], keys: tuple[str, ...]) -> list[str]:
        values: list[str] = []
        for key in keys:
            value = args.get(key)
            if isinstance(value, str):
                values.append(value)
            elif isinstance(value, (list, tuple)):
                values.extend(item for item in value if isinstance(item, str))
        return values

    @staticmethod
    def _is_side_effect_tool(tool_name: str) -> bool:
        lowered = tool_name.lower()
        return (
            lowered in _FILE_TOOLS
            or "write" in lowered
            or "file" in lowered
            or any(keyword in lowered for keyword in _COMMAND_KEYWORDS)
        )


__all__ = ["ExecutionGuardRail"]
