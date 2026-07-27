#!/usr/bin/env python3
"""Prompt capture — 完整捕获每次 LLM 调用的输入。

通过注册 LLMCallEvents.LLM_INPUT 回调，记录每次 LLM 调用前的完整请求内容，
包括 system prompt、对话历史、用户 query、工具定义等，并关联请求上下文
（session_id、request_id、user_query、start_time 等）。

启用方式（二选一）：
  1. 环境变量：JIUWENSWARM_PROMPT_CAPTURE=1
  2. 代码调用：setup_capture()

输出目录：~/.jiuwenswarm/logs/prompt_capture/<session_id>.jsonl
         每行一个 JSON 对象，对应一次 LLM 调用。
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openjiuwen.core.runner import Runner
from openjiuwen.core.runner.callback.events import LLMCallEvents

logger = logging.getLogger(__name__)

_ENV_ENABLE = "JIUWENSWARM_PROMPT_CAPTURE"

# ── 上下文变量：将当前请求信息传递到 LLM_INPUT 回调中 ──
# contextvars 是 asyncio-safe 的，支持多会话并发
_current_request: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "prompt_capture_request", default=None,
)
# _call_counter 和 _input_snapshot 不再用独立 ContextVar。
# 原因：agent-core 55116d61 给 streaming 加了 asyncio.wait_for(chunk.__anext__(), timeout)，
# wait_for 每次创建新 Task 并复制 context——独立 ContextVar 的 .set() 只改子 task 副本，
# 子 task 结束就丢。改存进 _current_request dict（dict 引用跨 task 共享）。
_call_counter_key = "_call_counter"
_input_snapshot_key = "_input_snapshot"


# ── 公开 API：在请求入口处调用 ──

def set_request_context(
    session_id: str,
    request_id: str,
    query: str,
    channel_id: str,
    mode: str,
) -> None:
    """设置当前请求上下文，供 LLM_INPUT 回调读取。

    在 process_message_impl / process_message_stream 入口处调用。
    """
    _current_request.set({
        "session_id": session_id,
        "request_id": request_id,
        "user_query": query,
        "channel_id": channel_id,
        "mode": mode,
        "request_start_time": datetime.now(timezone.utc).isoformat(),
        "request_start_timestamp": time.time(),
        _call_counter_key: 0,
        _input_snapshot_key: None,
    })


def clear_request_context() -> None:
    """清除请求上下文，在处理完成后调用。"""
    _current_request.set(None)


# ── JSON 安全序列化 ──

def _json_safe(value: Any) -> Any:
    """递归地将不可序列化的值转为字符串。"""
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except (TypeError, ValueError):
        if isinstance(value, dict):
            return {str(k): _json_safe(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_json_safe(item) for item in value]
        return str(value)


def _extract_system_messages(messages: list[dict[str, Any]]) -> list[str]:
    """提取所有 system role 的消息。"""
    results: list[str] = []
    for item in messages:
        if item.get("role") != "system":
            continue
        content = item.get("content", "")
        if isinstance(content, str):
            results.append(content)
        else:
            results.append(json.dumps(content, ensure_ascii=False, indent=2))
    return results


def _get_output_dir() -> Path:
    """获取 prompt capture 输出目录。"""
    from jiuwenswarm.common.utils import get_logs_dir
    logs_dir = get_logs_dir()
    output_dir = logs_dir / "prompt_capture"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


# ── 核心捕获器 ──

class PromptCaptureLogger:
    """完整捕获 LLM 调用的输入并写入结构化日志。

    单例模式，每个进程一个实例。
    """

    def __init__(self) -> None:
        self._registered = False

    def register(self) -> None:
        """注册 LLM_INPUT + LLM_OUTPUT 回调。"""
        if self._registered:
            return
        Runner.callback_framework.register_sync(
            LLMCallEvents.LLM_INPUT,
            self._on_llm_input,
            namespace="prompt_capture",
            priority=1000,
        )
        Runner.callback_framework.register_sync(
            LLMCallEvents.LLM_OUTPUT,
            self._on_llm_output,
            namespace="prompt_capture",
            priority=1000,
        )
        self._registered = True
        logger.info("[PromptCapture] 已注册 LLM_INPUT + LLM_OUTPUT 回调")

    async def unregister(self) -> None:
        """注销回调。"""
        if not self._registered:
            return
        await Runner.callback_framework.unregister(LLMCallEvents.LLM_INPUT, self._on_llm_input)
        await Runner.callback_framework.unregister(LLMCallEvents.LLM_OUTPUT, self._on_llm_output)
        self._registered = False
        logger.info("[PromptCapture] 已注销 LLM_INPUT + LLM_OUTPUT 回调")

    async def _on_llm_input(
        self,
        *,
        model_name: str | None = None,
        model_provider: Any = None,
        messages: list[dict[str, Any]] | None = None,
        tools: list[dict[str, Any]] | None = None,
        temperature: Any = None,
        top_p: Any = None,
        max_tokens: Any = None,
    ) -> None:
        """LLM 调用前的回调：组装完整记录并写入文件。"""
        req_ctx = _current_request.get()
        if req_ctx is None:
            return  # 没有活跃的请求上下文，跳过

        call_count = req_ctx.get(_call_counter_key, 0) + 1
        req_ctx[_call_counter_key] = call_count

        safe_messages = _json_safe(messages or [])
        safe_tools = _json_safe(tools or [])

        record: dict[str, Any] = {
            # ── 元信息 ──
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "call_index": call_count,

            # ── 请求上下文 ──
            "session_id": req_ctx["session_id"],
            "request_id": req_ctx["request_id"],
            "channel_id": req_ctx["channel_id"],
            "user_query": req_ctx["user_query"],
            "mode": req_ctx["mode"],
            "request_start_time": req_ctx["request_start_time"],

            # ── LLM 调用参数 ──
            "model_name": model_name,
            "model_provider": str(model_provider) if model_provider is not None else None,
            "temperature": temperature,
            "top_p": top_p,
            "max_tokens": max_tokens,

            # ── 完整 Prompt（包括 system、user、assistant 历史） ──
            "messages": safe_messages,
            "system_messages": _extract_system_messages(safe_messages),
            "message_count": len(safe_messages),

            # ── 工具定义 ──
            "tools": safe_tools,
            "tool_count": len(safe_tools),
        }

        # 暂存快照到 req_ctx dict（引用共享，跨 wait_for task 可见）
        req_ctx[_input_snapshot_key] = record

        self._write_record(req_ctx["session_id"], record)

    async def _on_llm_output(
        self,
        *,
        model_name: str | None = None,
        messages: list[dict[str, Any]] | None = None,
        response: Any = None,
        tool_calls: list[dict[str, Any]] | None = None,
        usage: Any = None,
        **kwargs: Any,
    ) -> None:
        """LLM 调用后的回调：补充 tool_calls 和 token 用量到上一条 input 记录中。"""
        req_ctx = _current_request.get()
        if req_ctx is None:
            return

        input_record = req_ctx.get(_input_snapshot_key)
        if input_record is None:
            return

        call_index = input_record.get("call_index", -1)

        # 提取 tool_calls（兼容 dict 和对象两种格式）
        called_tools: list[dict[str, Any]] = []
        if tool_calls:
            for tc in tool_calls:
                name = ""
                arguments = ""
                if isinstance(tc, dict):
                    func = tc.get("function", {})
                    name = func.get("name", "") if isinstance(func, dict) else ""
                    arguments = func.get("arguments", "") if isinstance(func, dict) else ""
                else:
                    # 对象格式：可能是 ToolCall(name=..., args=...) 或 .function.name
                    func = getattr(tc, "function", None)
                    if func is not None:
                        name = getattr(func, "name", "") or ""
                        arguments = getattr(func, "arguments", "") or ""
                    if not name:
                        name = getattr(tc, "name", "") or ""
                        arguments = getattr(tc, "arguments", "") or getattr(tc, "args", "") or ""
                called_tools.append({"name": str(name), "arguments": str(arguments)[:500]})

        output_record = {
            "type": "llm_output",
            "call_index": call_index,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "session_id": req_ctx["session_id"],
            "tool_calls_made": called_tools,
            "tool_call_count": len(called_tools),
        }

        # 提取 token 用量
        if usage is not None:
            try:
                if hasattr(usage, "input_tokens"):
                    output_record["input_tokens"] = usage.input_tokens
                    output_record["output_tokens"] = usage.output_tokens
                elif isinstance(usage, dict):
                    output_record["input_tokens"] = usage.get("input_tokens") or usage.get("prompt_tokens")
                    output_record["output_tokens"] = usage.get("output_tokens") or usage.get("completion_tokens")
            except Exception:
                pass

        self._write_record(req_ctx["session_id"], output_record)

    def snapshot_ability_manager(self, agent: Any) -> None:
        """记录 ability_manager 中的完整工具注册表（在初始化时调用一次）。"""
        try:
            am = getattr(agent, "ability_manager", None)
            if am is None:
                return
            tools_dict = getattr(am, "_tools", {}) or {}
            tool_names = sorted(tools_dict.keys())
            record = {
                "type": "ability_snapshot",
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "registered_tool_count": len(tool_names),
                "registered_tool_names": tool_names,
            }
            # 写入全局快照文件，不跟 session 绑定
            output_dir = _get_output_dir()
            snap_path = output_dir / "_ability_snapshot.json"
            snap_path.write_text(
                json.dumps(record, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.info(
                "[PromptCapture] ability snapshot: %d tools → %s",
                len(tool_names),
                snap_path,
            )
        except Exception as exc:
            logger.warning("[PromptCapture] ability snapshot failed: %s", exc)

    def _write_record(self, session_id: str, record: dict[str, Any]) -> None:
        """以 JSONL 格式追加写入文件。"""
        output_dir = _get_output_dir()
        safe_sid = session_id.replace("/", "_").replace(":", "_").replace(" ", "_")
        json_path = output_dir / f"{safe_sid}.jsonl"
        try:
            line = json.dumps(record, ensure_ascii=False)
            with open(json_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception as exc:
            logger.warning("[PromptCapture] 写入文件失败: %s", exc)


# ── 全局单例 ──

_capture_instance: PromptCaptureLogger | None = None


def get_capture() -> PromptCaptureLogger | None:
    """获取全局 PromptCaptureLogger 实例。"""
    return _capture_instance


def setup_capture() -> PromptCaptureLogger | None:
    """初始化 prompt capture（在应用启动时调用一次）。

    由 JIUWENSWARM_PROMPT_CAPTURE=1 环境变量控制是否启用。
    返回 capture 实例，未启用时返回 None。
    """
    global _capture_instance
    enabled = os.environ.get(_ENV_ENABLE, "").lower() in ("1", "true", "yes")
    if not enabled:
        logger.info("[PromptCapture] 未启用（设置 %s=1 可开启）", _ENV_ENABLE)
        return None
    if _capture_instance is None:
        _capture_instance = PromptCaptureLogger()
        _capture_instance.register()
    return _capture_instance
