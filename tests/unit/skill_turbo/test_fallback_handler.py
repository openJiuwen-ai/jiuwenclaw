# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
# pylint: disable=protected-access

"""DeepAgentFallbackHandler 单元测试。

覆盖：
- _parse_fallback_output 对各种 JSON 契约格式的健壮解析
- _fallback_stream_impl 契约失败/spawn 失败时不再 yield chat.error（避免抢跑前端，
  阻断 LLM 按 skill_prompt_rail 转 skill_tool 降级），改为 raise FallbackContractError。
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator
from unittest.mock import MagicMock

import pytest

from jiuwenclaw.agentserver.skill_turbo.fallback_handler import (
    DeepAgentFallbackHandler,
    FallbackContractError,
)


# ─────────────────────── _parse_fallback_output ───────────────────────


class TestParseFallbackOutput:
    """契约 JSON 解析的健壮性。"""

    def test_inline_code_single_line(self) -> None:
        text = '正文...\n`{"success": true, "result": {"outline_path": "/tmp/o.md"}}`'
        success, result = DeepAgentFallbackHandler._parse_fallback_output(text)
        assert success is True
        assert result["outline_path"] == "/tmp/o.md"

    def test_inline_code_nested_object_not_truncated(self) -> None:
        # 嵌套对象 {} 不能被首个 } 截断（旧版 .*? 贪婪反向 bug）
        text = '`{"success": true, "result": {"outline_path": "/x.md", "pages": 3}}`'
        success, result = DeepAgentFallbackHandler._parse_fallback_output(text)
        assert success is True
        assert result["pages"] == 3

    def test_json_fence_block(self) -> None:
        text = '正文\n```json\n{"success": false, "result": {"reason": "未生成文件"}}\n```\n'
        success, result = DeepAgentFallbackHandler._parse_fallback_output(text)
        assert success is False
        assert result["reason"] == "未生成文件"

    def test_plain_fence_block_without_lang_tag(self) -> None:
        text = '```\n{"success": true, "result": {}}\n```'
        success, _ = DeepAgentFallbackHandler._parse_fallback_output(text)
        assert success is True

    def test_bare_json_at_tail(self) -> None:
        # 无围栏，末尾裸 JSON（契约要求单行收尾）
        text = '生成完成。\n{"success": true, "result": {"p4_validate_status": "passed"}}'
        success, result = DeepAgentFallbackHandler._parse_fallback_output(text)
        assert success is True
        assert result["p4_validate_status"] == "passed"

    def test_empty_output(self) -> None:
        success, result = DeepAgentFallbackHandler._parse_fallback_output("")
        assert success is False
        assert "未产出任何内容" in result["reason"]

    def test_output_without_json(self) -> None:
        success, result = DeepAgentFallbackHandler._parse_fallback_output("任务已完成但未给契约声明")
        assert success is False
        assert "未包含 JSON 契约声明" in result["reason"]

    def test_invalid_json(self) -> None:
        text = '`{"success": true, "result": }`'  # 畸形 JSON
        success, result = DeepAgentFallbackHandler._parse_fallback_output(text)
        assert success is False
        assert "JSON 解析失败" in result["reason"]

    def test_last_block_wins(self) -> None:
        # 出现多个契约块时取最后一个
        text = (
            '`{"success": false, "result": {"reason": "第一次失败"}}`\n'
            '后续重试...\n`{"success": true, "result": {"outline_path": "/ok.md"}}`'
        )
        success, result = DeepAgentFallbackHandler._parse_fallback_output(text)
        assert success is True
        assert result["outline_path"] == "/ok.md"

    def test_non_dict_result_coerced_to_reason(self) -> None:
        text = '`{"success": false, "result": "字符串原因"}`'
        success, result = DeepAgentFallbackHandler._parse_fallback_output(text)
        assert success is False
        assert result["reason"] == "字符串原因"

    def test_dedup_skips_redundant_json_loads(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """内联代码(#1)与裸 JSON 扫描(#4)会重复捕获同一片段；去重后避免重复解析。

        未去重时 reversed 先命中末尾裸 {"data": 1}（无 success，失败）再命中 success，
        共 2 次 json.loads；去重后 success 在 reversed 中最先命中，仅 1 次。
        """
        import json as _json

        text = (
            '`{"data": 1}`\n'
            '然后 `{"success": true, "result": {"path": "/ok"}}`\n'
            '末尾再出现裸 JSON: {"data": 1}'
        )
        orig_loads = _json.loads
        calls: list[str] = []

        def _spy(s, *a, **kw):
            calls.append(s)
            return orig_loads(s, *a, **kw)

        monkeypatch.setattr(_json, "loads", _spy)
        success, result = DeepAgentFallbackHandler._parse_fallback_output(text)
        assert success is True
        assert result["path"] == "/ok"
        assert len(calls) == 1, f"去重后应只解析 success 一次，实际解析 {calls}"

    def test_long_bare_text_still_finds_tail_contract(self) -> None:
        """超长裸文本（无围栏）末尾契约仍可被全文扫描命中。"""
        text = "正文" + "y" * 60_000 + '\n{"success": true, "result": {"path": "/ok"}}'
        success, result = DeepAgentFallbackHandler._parse_fallback_output(text)
        assert success is True
        assert result["path"] == "/ok"


# ─────────────────────── _scan_balanced_json_objects ─────────────────────


class TestScanBalancedJsonObjects:
    """裸 JSON 平衡扫描的健壮性。"""

    def test_nested_object_returned_in_full(self) -> None:
        text = '前缀 {"success": true, "result": {"path": "/x", "pages": 3}} 后缀'
        out = DeepAgentFallbackHandler._scan_balanced_json_objects(text)
        assert out == ['{"success": true, "result": {"path": "/x", "pages": 3}}']

    def test_multiple_top_level_objects(self) -> None:
        text = '{"a": 1} gap {"b": 2}'
        out = DeepAgentFallbackHandler._scan_balanced_json_objects(text)
        assert out == ['{"a": 1}', '{"b": 2}']

    def test_brace_inside_string_is_ignored(self) -> None:
        # 字符串内的 } 不应被误判为对象闭合
        text = '{"a": "}"}'
        out = DeepAgentFallbackHandler._scan_balanced_json_objects(text)
        assert out == ['{"a": "}"}']

    def test_escaped_quote_then_brace_stays_in_string(self) -> None:
        # \" 不结束字符串，其后串内的 } 不应误判为闭合
        text = r'{"a": "\"}"}'
        out = DeepAgentFallbackHandler._scan_balanced_json_objects(text)
        assert out == [r'{"a": "\"}"}']

    def test_stray_balanced_braces_then_contract(self) -> None:
        # 散文中的 {placeholder} 等平衡片段不应阻断后续契约捕获
        text = 'use the {placeholder} syntax then {"success": true, "result": {}}'
        out = DeepAgentFallbackHandler._scan_balanced_json_objects(text)
        assert '{placeholder}' in out
        assert '{"success": true, "result": {}}' in out

    def test_unbalanced_open_brace_no_match_no_crash(self) -> None:
        # 仅未闭合的 {：扫到末尾无匹配，不产出、不抛异常
        text = '前置 { 未闭合到末尾'
        out = DeepAgentFallbackHandler._scan_balanced_json_objects(text)
        assert out == []

    def test_empty_text_returns_empty(self) -> None:
        assert DeepAgentFallbackHandler._scan_balanced_json_objects("") == []


# ─────────────────────── _fallback_stream_impl 行为 ─────────────────────


class _FakeSubagentResult:
    """模拟 executor.execute_spawn 的返回结构。"""

    def __init__(self, *, success: bool, result: str = "", error: str = "", task_id: str = "t1") -> None:
        self.success = success
        self.result = result
        self.error = error
        self.task_id = task_id


def _make_handler() -> DeepAgentFallbackHandler:
    """构造一个 handler，_get_subagent_executor 返回 mock（不会被实际调用到 spawn）。"""
    handler = DeepAgentFallbackHandler(adapter=MagicMock())
    # 覆盖 _execute_spawn_fallback，由各用例指定返回值
    return handler


async def _collect(items: AsyncIterator[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    async for chunk in items:
        out.append(chunk)
    return out


@pytest.mark.unit
async def test_contract_failure_no_chat_error_only_raise() -> None:
    """契约失败（spawn 成功但产出无 JSON 契约）只 raise，不 yield chat.error。"""
    handler = _make_handler()
    handler._execute_spawn_fallback = lambda *a, **kw: _async_return(_FakeSubagentResult(success=True, result="无契约的正文"))  # type: ignore[assignment]

    chunks: list[dict[str, Any]] = []
    with pytest.raises(FallbackContractError):
        async for chunk in handler._fallback_stream_impl(
            "p4_2_quick_research", "instr", {"k": "v"}, RuntimeError("P4.2a 失败：LLM 返回为空"), None,
        ):
            chunks.append(chunk)

    event_types = [c.get("event_type") for c in chunks]
    assert "chat.error" not in event_types, "契约失败不应再 yield chat.error（会抢跑前端）"
    # 生命周期事件仍保留
    assert "fallback.started" in event_types
    assert "fallback.finished" in event_types


@pytest.mark.unit
async def test_spawn_success_false_raises_no_chat_error() -> None:
    """spawn 返回 success=false 时不再 yield chat.error，改为 raise FallbackContractError。"""
    handler = _make_handler()
    handler._execute_spawn_fallback = lambda *a, **kw: _async_return(_FakeSubagentResult(success=False, error="spawn 内部失败"))  # type: ignore[assignment]

    chunks: list[dict[str, Any]] = []
    with pytest.raises(FallbackContractError):
        async for chunk in handler._fallback_stream_impl(
            "p4_content_plan", "instr", {}, RuntimeError("节点失败"), None,
        ):
            chunks.append(chunk)

    event_types = [c.get("event_type") for c in chunks]
    assert "chat.error" not in event_types


@pytest.mark.unit
async def test_spawn_exception_raises_no_chat_error() -> None:
    """_execute_spawn_fallback 自身抛异常时 raise FallbackContractError，不 yield chat.error。"""
    handler = _make_handler()

    async def _raise(*a: Any, **kw: Any) -> Any:
        raise RuntimeError("spawn 连接失败")

    handler._execute_spawn_fallback = _raise  # type: ignore[assignment]

    chunks: list[dict[str, Any]] = []
    with pytest.raises(FallbackContractError):
        async for chunk in handler._fallback_stream_impl(
            "p4_content_plan", "instr", {}, RuntimeError("节点失败"), None,
        ):
            chunks.append(chunk)

    event_types = [c.get("event_type") for c in chunks]
    assert "chat.error" not in event_types
    assert "fallback.started" in event_types
    assert "fallback.finished" in event_types  # finally 仍发


@pytest.mark.unit
async def test_contract_success_writes_back_inputs() -> None:
    """契约成功时把 result 字段回写 inputs，标记 fallback=True、status=completed。"""
    handler = _make_handler()
    contract_json = '`{"success": true, "result": {"outline_path": "/tmp/outline.md"}}`'
    handler._execute_spawn_fallback = lambda *a, **kw: _async_return(_FakeSubagentResult(success=True, result=contract_json))  # type: ignore[assignment]

    inputs: dict[str, Any] = {"topic": "AI"}
    chunks = await _collect(handler._fallback_stream_impl(
        "p4_3_outline_gen", "instr", inputs, RuntimeError("节点失败"), None,
    ))

    assert inputs["outline_path"] == "/tmp/outline.md"
    assert inputs["fallback"] is True
    assert inputs["fallback_reason"]  # 失败原因回写，供下游感知
    # node/status 被有意排除（见 _build_success_result），不写回 inputs


async def _async_return(value: Any) -> Any:
    """把一个同步值包成 awaitable，供 lambda 形式的 _execute_spawn_fallback 使用。"""
    await asyncio.sleep(0)
    return value
