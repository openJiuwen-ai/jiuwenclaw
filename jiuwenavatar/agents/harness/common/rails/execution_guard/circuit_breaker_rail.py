# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""CircuitBreakerRail - Agent 循环检测断路器.

检测类型:
  - generic_repeat:      相同工具+参数重复 (WARNING≥10)
  - unknown_tool_repeat: 错误工具连续调用 (CRITICAL≥10)
  - global_breaker:      工具无进展兜底中断 (CRITICAL≥30)
  - ping_pong:           两工具交替循环 (WARNING≥10, CRITICAL≥20)
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from typing import Any

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.base import DeepAgentRail

from jiuwenavatar.common.utils import logger


@dataclass
class CircuitBreakerConfig:
    warning_threshold: int = 10
    critical_threshold: int = 20
    global_breaker_threshold: int = 30
    unknown_tool_threshold: int = 10

    @property
    def history_size(self) -> int:
        return max(
            self.global_breaker_threshold,
            2 * self.critical_threshold,
            2 * self.warning_threshold,
        )


# 报错文案按语言 × 检测器分表，占位符 {tool_name} / {count} 在渲染时填充。
# 语言键与 openjiuwen SUPPORTED_LANGUAGES 对齐：("cn", "en")。
_MESSAGES: dict[str, dict[str, str]] = {
    "cn": {
        "global_circuit_breaker": "全局断路器: {tool_name} 连续 {count} 次无进展",
        "unknown_tool_repeat": "未知工具 {tool_name} 连续调用 {count} 次，停止重试",
        "ping_pong_critical": "Ping-Pong 循环: {count} 次交替无进展，阻断",
        "ping_pong_warning": "Ping-Pong 警告: {count} 次交替调用",
        "generic_repeat": "工具 {tool_name} 已重复调用 {count} 次，请检查是否有效",
    },
    "en": {
        "global_circuit_breaker": (
            "Circuit breaker: {tool_name} made no progress for {count} consecutive calls"
        ),
        "unknown_tool_repeat": (
            "Unknown tool {tool_name} called {count} times in a row, stopping retries"
        ),
        "ping_pong_critical": (
            "Ping-pong loop: {count} alternating calls with no progress, blocked"
        ),
        "ping_pong_warning": "Ping-pong warning: {count} alternating calls",
        "generic_repeat": (
            "Tool {tool_name} has been repeated {count} times, please verify it is effective"
        ),
    },
}

_DEFAULT_LANGUAGE = "cn"


def _normalize_language(language: str | None) -> str:
    """归一语言键：config 用 zh，rail 内部用 cn；非法值回落默认。"""
    lang = (language or "").strip().lower()
    if lang == "zh":
        lang = "cn"
    return lang if lang in _MESSAGES else _DEFAULT_LANGUAGE


_ERROR_CONTENT_KEYWORDS = (
    "tool not found", "unknown tool", "does not exist",
    "command not found", "not found in resource_mgr",
    "ability execution error", "tool execution error",
    "permission denied", "denied", "timeout", "not configured",
)


@dataclass
class ToolCallRecord:
    tool_name: str
    args_hash: str
    result_hash: str | None
    timestamp: float
    has_error: bool = False


@dataclass
class DetectionResult:
    stuck: bool = False
    level: str = ""
    detector: str = ""
    count: int = 0
    msg_key: str = ""
    tool_name: str = ""


@dataclass
class PingPongResult:
    count: int = 0
    paired_tool: str | None = None
    no_progress: bool = False


class CircuitBreakerRail(DeepAgentRail):
    priority: int = 95

    def __init__(
        self,
        config: CircuitBreakerConfig | None = None,
        language: str = _DEFAULT_LANGUAGE,
    ):
        super().__init__()
        self._config = config or CircuitBreakerConfig()
        self._history: list[ToolCallRecord] = []
        self._language = _normalize_language(language)

    def set_language(self, language: str) -> None:
        """per-request 更新报错文案语言。"""
        self._language = _normalize_language(language)

    def _format_message(self, result: DetectionResult) -> str:
        """按当前语言渲染检测结果文案。"""
        table = _MESSAGES.get(self._language, _MESSAGES[_DEFAULT_LANGUAGE])
        template = table.get(result.msg_key) or _MESSAGES[_DEFAULT_LANGUAGE].get(
            result.msg_key, ""
        )
        return template.format(tool_name=result.tool_name, count=result.count)

    # ------------------------------------------------------------------
    # before_invoke: 每条新消息清空历史，独立计数
    # after_tool_call: 记录调用 + 检测 + 告警/中断
    # ------------------------------------------------------------------

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        self._history = []

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        tool_call = ctx.inputs.tool_call
        if tool_call is None:
            return

        tool_name = getattr(tool_call, "name", "")
        tool_args = getattr(tool_call, "arguments", {})
        tool_result = ctx.inputs.tool_result

        if not tool_name:
            return

        args_hash = self._hash_args(tool_name, tool_args)
        result_hash = self._hash_outcome(tool_result)
        has_error = self._result_has_error(tool_result) or self._ctx_has_error(ctx)

        self._history.append(ToolCallRecord(
            tool_name=tool_name, args_hash=args_hash,
            result_hash=result_hash, timestamp=time.time(),
            has_error=has_error,
        ))
        if len(self._history) > self._config.history_size:
            self._history = self._history[-self._config.history_size:]

        result = self._detect(tool_name, args_hash)

        if result.stuck and result.level == "critical":
            message = self._format_message(result)
            logger.error("[CircuitBreaker] %s", message)
            ctx.request_force_finish({
                "output": message,
                "result_type": "error",
            })
        if result.stuck and result.level == "warning":
            logger.warning("[CircuitBreaker] %s", self._format_message(result))

    # ------------------------------------------------------------------
    # _detect: 四种检测器按优先级依次检查
    # ------------------------------------------------------------------

    def _detect(self, tool_name: str, args_hash: str) -> DetectionResult:
        cfg = self._config

        no_progress = self._get_no_progress_streak(tool_name, args_hash)
        if no_progress >= cfg.global_breaker_threshold:
            return DetectionResult(stuck=True, level="critical",
                detector="global_circuit_breaker", count=no_progress,
                msg_key="global_circuit_breaker", tool_name=tool_name)

        unknown_streak = self._get_unknown_tool_streak(tool_name)
        if unknown_streak >= cfg.unknown_tool_threshold:
            return DetectionResult(stuck=True, level="critical",
                detector="unknown_tool_repeat", count=unknown_streak,
                msg_key="unknown_tool_repeat", tool_name=tool_name)

        ping_pong = self._get_ping_pong_streak(args_hash)
        if ping_pong.count >= cfg.critical_threshold and ping_pong.no_progress:
            return DetectionResult(stuck=True, level="critical",
                detector="ping_pong", count=ping_pong.count,
                msg_key="ping_pong_critical", tool_name=tool_name)
        if ping_pong.count >= cfg.warning_threshold:
            return DetectionResult(stuck=True, level="warning",
                detector="ping_pong", count=ping_pong.count,
                msg_key="ping_pong_warning", tool_name=tool_name)

        recent = self._count_recent_same(tool_name, args_hash)
        if recent >= cfg.warning_threshold:
            return DetectionResult(stuck=True, level="warning",
                detector="generic_repeat", count=recent,
                msg_key="generic_repeat", tool_name=tool_name)

        return DetectionResult(stuck=False)

    @staticmethod
    def _hash_args(tool_name: str, params: dict) -> str:
        canonical = json.dumps(params, sort_keys=True, default=str)
        return hashlib.sha256(f"{tool_name}:{canonical}".encode()).hexdigest()

    def _hash_outcome(self, result: Any) -> str | None:
        if result is None:
            return None
        normalized = self._normalize_result(result)
        return hashlib.sha256(
            json.dumps(normalized, sort_keys=True, default=str).encode()
        ).hexdigest()

    @staticmethod
    def _normalize_result(result: Any) -> dict:
        if isinstance(result, dict):
            return {
                "content": str(result.get("content", "")).strip(),
                "output": str(result.get("output", "")).strip(),
                "error": str(result.get("error", "")).strip(),
                "status": str(result.get("status", "")),
            }
        return {"raw": str(result)}

    # ------------------------------------------------------------------
    # _result_has_error / _ctx_has_error: 工具调用错误检测
    # ------------------------------------------------------------------

    @staticmethod
    def _result_has_error(result: Any) -> bool:
        if result is None:
            return False
        if isinstance(result, str):
            if result.upper().startswith("[ERROR]"):
                return True
            return "error" in result.lower() or "failed" in result.lower()
        if isinstance(result, dict):
            if str(result.get("is_error", "")).lower() in ("true", "1"):
                return True
            if str(result.get("isError", "")).lower() in ("true", "1"):
                return True
            raw_error = result.get("error", "")
            if raw_error and str(raw_error).strip():
                return True
            content = str(result.get("content", "")).lower()
            if content.startswith("[error]"):
                return True
            text = json.dumps(result, default=str).lower()
            if re.search(r"\bsuccess\s*[:=]\s*false\b", text):
                return True
            for key in ("exit_code", "exitCode", "returncode", "return_code"):
                code = result.get(key)
                if code is not None and str(code) != "0":
                    return True
            return any(kw in content for kw in _ERROR_CONTENT_KEYWORDS)
        return False

    @staticmethod
    def _ctx_has_error(ctx: AgentCallbackContext) -> bool:
        if ctx.exception is not None:
            return True
        tool_msg = getattr(ctx.inputs, "tool_msg", None)
        if tool_msg is not None:
            msg_content = str(getattr(tool_msg, "content", "")).lower()
            if msg_content:
                return "error" in msg_content or "not found" in msg_content
        return False

    # ------------------------------------------------------------------
    # 计数方法
    # ------------------------------------------------------------------

    def _get_no_progress_streak(self, tool_name: str, args_hash: str) -> int:
        streak = 0
        latest_hash = None
        for record in reversed(self._history):
            if record.tool_name != tool_name or record.args_hash != args_hash:
                continue
            if record.result_hash is None:
                continue
            if latest_hash is None:
                latest_hash = record.result_hash
                streak = 1
            elif record.result_hash == latest_hash:
                streak += 1
            else:
                break
        return streak

    # 两阶段：先数交替次数，再验证结果是否有进展
    def _get_ping_pong_streak(self, current_args_hash: str) -> PingPongResult:
        if len(self._history) < 2:
            return PingPongResult()
        last = self._history[-1]
        other_hash = other_name = None
        for record in reversed(self._history[:-1]):
            if record.args_hash != last.args_hash:
                other_hash = record.args_hash
                other_name = record.tool_name
                break
        if not other_hash:
            return PingPongResult()

        count = 0
        for i in range(len(self._history) - 2, -1, -1):
            expected = other_hash if count % 2 == 0 else last.args_hash
            if self._history[i].args_hash != expected:
                break
            count += 1
        if count < 1:
            return PingPongResult()

        first_a = first_b = None
        no_progress = True
        for i in range(len(self._history) - count - 1, len(self._history) - 1):
            record = self._history[i]
            if record.result_hash is None:
                no_progress = False
                break
            if record.args_hash == last.args_hash:
                if first_a is None:
                    first_a = record.result_hash
                elif first_a != record.result_hash:
                    no_progress = False
                    break
            elif record.args_hash == other_hash:
                if first_b is None:
                    first_b = record.result_hash
                elif first_b != record.result_hash:
                    no_progress = False
                    break
        last_record = self._history[-1]
        if no_progress and last_record.result_hash is not None:
            if first_a is not None and last_record.result_hash != first_a:
                no_progress = False
            elif first_b is not None and last_record.result_hash != first_b:
                no_progress = False
        return PingPongResult(count=count, paired_tool=other_name,
                              no_progress=no_progress)

    def _count_recent_same(self, tool_name: str, args_hash: str) -> int:
        return sum(1 for r in self._history
                   if r.tool_name == tool_name and r.args_hash == args_hash)

    def _get_unknown_tool_streak(self, tool_name: str) -> int:
        streak = 0
        for record in reversed(self._history):
            if record.tool_name != tool_name:
                break
            if not record.has_error:
                break
            streak += 1
        return streak