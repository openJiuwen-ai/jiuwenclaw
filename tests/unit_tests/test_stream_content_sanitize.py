# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for stream_content_sanitize module."""

from jiuwenclaw.agentserver.stream_content_sanitize import (
    StreamProtocolBuffer,
    _split_safe_and_pending,
    strip_inline_tool_protocol,
)


# ---------------------------------------------------------------------------
# strip_inline_tool_protocol — 完整文本剥离
# ---------------------------------------------------------------------------

class TestStripInlineToolProtocol:

    @staticmethod
    def test_no_protocol_unchanged():
        """普通文本不受影响。"""
        text = "今天天气真好，我来帮你完成任务。"
        assert strip_inline_tool_protocol(text) == text

    @staticmethod
    def test_empty_string():
        assert strip_inline_tool_protocol("") == ""

    @staticmethod
    def test_full_outer_and_inner_single_task():
        """带完整外层标签+内层 JSON 的单条 todo_insert 被完全剥除。"""
        protocol = (
            '<tool_calls_begin><tool_call_begin>function<tool_sep>todo_insert{'
            '"idx": 3, "tasks": ["写报告"]}'
            '</tool_call_end></tool_calls_end>'
        )
        result = strip_inline_tool_protocol(protocol)
        assert result == ""

    @staticmethod
    def test_full_outer_and_inner_nested_array():
        """tasks 为多元素数组（含嵌套括号）时正确剥除。"""
        protocol = (
            '<tool_calls_begin><tool_call_begin>function<tool_sep>todo_insert{'
            '"idx": 1, "tasks": ["a", "b", "c"]}'
            '</tool_call_end>'
        )
        result = strip_inline_tool_protocol(protocol)
        assert result == ""

    @staticmethod
    def test_inner_only_no_outer_tag():
        """无外层标签时仍剥除 function<tool_sep>...{...}。"""
        protocol = 'function<tool_sep>todo_create{"tasks": ["任务1"]}'
        result = strip_inline_tool_protocol(protocol)
        assert result == ""

    @staticmethod
    def test_text_before_and_after():
        """协议前后有普通文本，只去掉协议段。"""
        text = (
            '好的，我来创建待办列表。<tool_calls_begin><tool_call_begin>function<tool_sep>todo_create{'
            '"tasks": ["step1"]}</tool_call_end>创建完成！'
        )
        result = strip_inline_tool_protocol(text)
        assert result == "好的，我来创建待办列表。创建完成！"

    @staticmethod
    def test_multiple_protocol_segments():
        """同一字符串中多段协议均被剥除。"""
        p1 = 'function<tool_sep>todo_insert{"idx": 1, "tasks": ["a"]}'
        p2 = 'function<tool_sep>todo_complete{"idx": 1}'
        text = f"前文{p1}中间{p2}后文"
        result = strip_inline_tool_protocol(text)
        assert result == "前文中间后文"

    @staticmethod
    def test_truncated_at_eof_outer_tag():
        """仅有外层开标签，JSON 未闭合 → 从锚点起删至末尾。"""
        text = (
            '回答内容。<tool_calls_begin><tool_call_begin>function<tool_sep>todo_insert{'
            '"idx": 2, "tasks":'
        )
        result = strip_inline_tool_protocol(text)
        assert result == "回答内容。"

    @staticmethod
    def test_truncated_at_eof_inner_only():
        """无外层标签、JSON 截断 → 从 function<tool_sep> 起删至末尾。"""
        text = '部分内容。function<tool_sep>todo_remove{"idx":'
        result = strip_inline_tool_protocol(text)
        assert result == "部分内容。"

    @staticmethod
    def test_non_whitelist_tool_not_stripped():
        """白名单外的工具名不被剥除（默认白名单模式）。"""
        text = 'function<tool_sep>run_command{"cmd": "ls"}'
        result = strip_inline_tool_protocol(text)
        assert result == text

    @staticmethod
    def test_stripped_content_becomes_empty():
        """剥除后结果为空字符串时返回空字符串而非 None。"""
        protocol = '<tool_calls_begin><tool_call_begin>function<tool_sep>todo_list{}</tool_calls_end>'
        result = strip_inline_tool_protocol(protocol)
        assert result == ""
        assert isinstance(result, str)

    @staticmethod
    def test_outer_tag_no_inner_func_sep_with_close():
        """有外层标签和闭合但无 function<tool_sep>，整段删除。"""
        text = '<tool_calls_begin>随机内容</tool_calls_end>'
        result = strip_inline_tool_protocol(text)
        assert result == ""

    @staticmethod
    def test_outer_tag_blob_preserves_text_after_close():
        """闭合标签后的合法正文应保留（find_close_tags 须从开标签结束之后扫描）。"""
        text = '前文<tool_calls_begin>随机内容</tool_calls_end>后续'
        result = strip_inline_tool_protocol(text)
        assert result == "前文后续"

    @staticmethod
    def test_outer_tag_no_inner_func_sep_no_close():
        """有外层开标签但无 function<tool_sep> 且无闭合 → 删至末尾。"""
        text = '前文<tool_calls_begin>悬空内容'
        result = strip_inline_tool_protocol(text)
        assert result == "前文"

    @staticmethod
    def test_deeply_nested_json():
        """JSON 内嵌套 {} 仍可正确识别闭合。"""
        protocol = 'function<tool_sep>todo_create{"tasks": ["a{b}c", "d"]}'
        result = strip_inline_tool_protocol(protocol)
        assert result == ""

    @staticmethod
    def test_idempotent():
        """多次调用结果一致（幂等）。"""
        text = "普通回答，不含协议。"
        assert strip_inline_tool_protocol(strip_inline_tool_protocol(text)) == text


# ---------------------------------------------------------------------------
# StreamProtocolBuffer — 流式场景
# ---------------------------------------------------------------------------

class TestStreamProtocolBuffer:

    @staticmethod
    def test_safe_content_passed_through():
        """无协议的纯文本直接返回，缓冲为空。"""
        buf = StreamProtocolBuffer()
        safe = buf.feed("你好，世界！")
        assert safe == "你好，世界！"
        assert buf.flush() == ""

    @staticmethod
    def test_complete_protocol_absorbed():
        """完整协议段在单次 feed 中被识别并丢弃，不传给 safe。"""
        buf = StreamProtocolBuffer()
        protocol = 'function<tool_sep>todo_insert{"idx": 1, "tasks": ["x"]}'
        safe = buf.feed(protocol)
        # 协议被剥除，safe 为空（或空白）
        assert "todo_insert" not in safe
        assert "function<tool_sep>" not in safe

    @staticmethod
    def test_protocol_split_across_feeds():
        """协议被拆分到两次 feed 中，直到第二次才可确认完整。"""
        buf = StreamProtocolBuffer()
        # 第一次：只有前缀，无法确认是否协议
        part1 = (
            '<tool_calls_begin><tool_call_begin>function<tool_sep>todo_insert{'
            '"idx": 3, "tasks":'
        )
        safe1 = buf.feed(part1)
        # 第一次应缓冲，safe1 不含协议内容
        assert "todo_insert" not in safe1

        # 第二次：补充剩余，协议闭合
        part2 = ' ["任务A"]}</tool_calls_end>'
        safe2 = buf.feed(part2)
        remainder = buf.flush()

        total = safe1 + safe2 + remainder
        assert "todo_insert" not in total
        assert "function<tool_sep>" not in total

    @staticmethod
    def test_text_before_protocol_safe():
        """协议前的正常文本出现在 safe 中，协议被缓冲。"""
        buf = StreamProtocolBuffer()
        safe = buf.feed("好的！function<tool_sep>todo_list{")
        # "好的！" 应在 safe 中
        assert "好的！" in safe
        # 协议部分被缓冲
        assert "function<tool_sep>" not in safe

    @staticmethod
    def test_flush_incomplete_protocol_dropped():
        """流结束时缓冲中有未闭合协议，flush 将其丢弃。"""
        buf = StreamProtocolBuffer()
        buf.feed("前文。function<tool_sep>todo_remove{")
        remainder = buf.flush()
        # 未闭合协议被丢弃
        assert "todo_remove" not in remainder
        assert "function<tool_sep>" not in remainder

    @staticmethod
    def test_flush_safe_tail_returned():
        """流结束时缓冲仅含安全文本（无协议），flush 将其正常返回。"""
        buf = StreamProtocolBuffer()
        buf.feed("第一句。")
        safe2 = buf.feed("第二句")
        remainder = buf.flush()
        total = "第一句。" + safe2 + remainder
        assert "第一句。" in total
        assert "第二句" in total

    @staticmethod
    def test_non_whitelist_tool_streamed_incrementally():
        """白名单外工具协议样式不应卡住后续流式文本。"""
        buf = StreamProtocolBuffer()
        text = '前文function<tool_sep>run_command{"cmd": "ls"}后文'
        safe = buf.feed(text)
        remainder = buf.flush()
        assert safe + remainder == text

    @staticmethod
    def test_incomplete_whitelist_prefix_still_buffered():
        """白名单工具名前缀被截断时，仍应继续缓冲等待后续补全。"""
        buf = StreamProtocolBuffer()
        safe = buf.feed("前文function<tool_sep>todo_cr")
        remainder = buf.flush()
        assert safe == "前文"
        assert "function<tool_sep>" not in remainder


# ---------------------------------------------------------------------------
# _split_safe_and_pending — 内部辅助
# ---------------------------------------------------------------------------

class TestSplitSafeAndPending:

    @staticmethod
    def test_no_anchor():
        """无协议锚点时全部是 safe，pending 为空。"""
        safe, pending = _split_safe_and_pending("普通文本")
        assert safe == "普通文本"
        assert pending == ""

    @staticmethod
    def test_anchor_in_middle():
        """锚点在中间：锚点前为 safe，锚点及之后为 pending。"""
        text = "前文function<tool_sep>todo_list{"
        safe, pending = _split_safe_and_pending(text)
        assert safe == "前文"
        assert "function<tool_sep>" in pending

    @staticmethod
    def test_complete_protocol_stripped_before_split():
        """完整协议先被 strip，再做 split；结果 pending 中无协议片段。"""
        text = 'function<tool_sep>todo_complete{"idx": 1}'
        safe, pending = _split_safe_and_pending(text)
        assert "todo_complete" not in safe + pending

    @staticmethod
    def test_non_whitelist_anchor_treated_as_safe_text():
        """白名单外工具名不应被视为未完成协议而整段挂起。"""
        text = '前文function<tool_sep>run_command{"cmd":"ls"}后文'
        safe, pending = _split_safe_and_pending(text)
        assert safe == text
        assert pending == ""

    @staticmethod
    def test_incomplete_whitelist_tool_prefix_still_pending():
        """可能补全为白名单工具名的前缀仍应进入 pending。"""
        text = "前文function<tool_sep>todo_cr"
        safe, pending = _split_safe_and_pending(text)
        assert safe == "前文"
        assert pending == "function<tool_sep>todo_cr"
