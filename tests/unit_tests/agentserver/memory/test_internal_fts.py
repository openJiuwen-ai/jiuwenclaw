# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for tokenize_for_fts and build_fts_query from internal.py."""

import pytest

from jiuwenclaw.agentserver.memory.internal import tokenize_for_fts, build_fts_query, _is_valid_fts_token


# ---------------------------------------------------------------------------
# TestTokenizeForFts
# ---------------------------------------------------------------------------

class TestTokenizeForFts:
    """Tests for tokenize_for_fts(text, for_search)."""

    @staticmethod
    def test_chinese_normal_mode():
        """Chinese text in normal mode produces expected tokens."""
        result = tokenize_for_fts("你好世界", False)
        tokens = result.split()
        assert "你好" in tokens
        assert "世界" in tokens

    @staticmethod
    def test_chinese_search_mode_finer_grained():
        """Chinese text in search mode produces finer-grained sub-word tokens.

        For compound words like '机器人', search mode should include both
        the whole word and its sub-components (e.g. '机器' + '机器人').
        """
        result_normal = tokenize_for_fts("机器人", False)
        result_search = tokenize_for_fts("机器人", True)
        # Search mode should produce strictly more (or equal) tokens than normal
        assert len(result_search.split()) >= len(result_normal.split())
        # The full word '机器人' should appear in both
        assert "机器人" in result_normal.split()
        # Search mode should also include '机器' as a sub-token
        search_tokens = result_search.split()
        assert "机器人" in search_tokens
        assert "机器" in search_tokens

    @staticmethod
    def test_english_text():
        """English text passes through with word-level segmentation."""
        result = tokenize_for_fts("hello world", False)
        tokens = result.split()
        assert "hello" in tokens
        assert "world" in tokens

    @staticmethod
    def test_mixed_chinese_and_english():
        """Mixed Chinese+English text produces both English and Chinese tokens."""
        result = tokenize_for_fts("Python编程语言", False)
        tokens = result.split()
        assert "Python" in tokens
        # Chinese segments should also be present
        cn_tokens = [t for t in tokens if any(ord(c) > 127 for c in t)]
        assert len(cn_tokens) >= 1

    @staticmethod
    def test_empty_string():
        """Empty string should return empty output."""
        result = tokenize_for_fts("", False)
        assert result == ""

    @staticmethod
    def test_whitespace_only():
        """Whitespace-only input should return empty or minimal output."""
        result = tokenize_for_fts("   ", False)
        assert result == "" or len(result.strip()) == 0

    @staticmethod
    def test_markdown_symbols_filtered():
        """Markdown formatting symbols are filtered from tokenization."""
        result = tokenize_for_fts("# 标题 **加粗** - 列表项", True)
        tokens = result.split()
        # Markdown symbols should not appear as tokens
        assert "#" not in tokens
        assert "**" not in tokens
        assert "-" not in tokens
        # Meaningful words should still appear
        assert "标题" in tokens
        assert "加粗" in tokens

    @staticmethod
    def test_stop_words_filtered():
        """Common Chinese stop words are filtered from tokenization."""
        result = tokenize_for_fts("这是一个测试", False)
        tokens = result.split()
        # Stop words should not appear
        assert "这" not in tokens
        assert "是" not in tokens
        assert "一个" not in tokens
        # Meaningful words should appear
        assert "测试" in tokens

    @staticmethod
    def test_strip_before_tokenizing():
        """Leading/trailing whitespace is stripped before tokenization."""
        result = tokenize_for_fts("  你好  ", False)
        assert "你好" in result.split()

    @staticmethod
    def test_returns_space_separated_string():
        """Result is a space-separated string of tokens."""
        result = tokenize_for_fts("hello world", False)
        assert isinstance(result, str)
        # Should contain spaces between tokens
        assert " " in result or len(result.split()) == 1


# ---------------------------------------------------------------------------
# TestBuildFtsQuery
# ---------------------------------------------------------------------------

class TestBuildFtsQuery:
    """Tests for build_fts_query(query)."""

    @staticmethod
    def test_chinese_query():
        """Chinese query produces OR-joined quoted tokens."""
        result = build_fts_query("你好世界")
        # Should contain quoted tokens joined by OR
        assert "OR" in result
        assert '"你好"' in result
        assert '"世界"' in result

    @staticmethod
    def test_english_query():
        """English query produces OR-joined quoted tokens."""
        result = build_fts_query("hello world")
        assert result == '"hello" OR "world"'

    @staticmethod
    def test_empty_query():
        """Empty query returns empty string."""
        result = build_fts_query("")
        assert result == ""

    @staticmethod
    def test_whitespace_query():
        """Whitespace-only query returns empty string."""
        result = build_fts_query("   ")
        assert result == ""

    @staticmethod
    def test_long_query_truncation():
        """Query producing >10 tokens is truncated to at most 10 tokens (9 OR separators)."""
        # Use many distinct short tokens to exceed the 10-token limit
        long_query = "一 二 三 四 五 六 七 八 九 十 十一 十二 十三"
        result = build_fts_query(long_query)
        or_count = result.count(" OR ")
        # At most 9 OR separators (= 10 tokens)
        assert or_count <= 9

    @staticmethod
    def test_single_word_query():
        """Single word query produces a single quoted token with no OR."""
        result = build_fts_query("hello")
        assert result == '"hello"'
        assert " OR " not in result

    @staticmethod
    def test_mixed_query():
        """Mixed Chinese+English query produces tokens for both parts."""
        result = build_fts_query("Python开发")
        assert '"Python"' in result
        # Chinese token should also appear as a quoted token
        assert "OR" in result or '"开发"' in result

    @staticmethod
    def test_query_stop_words_filtered():
        """Stop words are filtered from FTS queries."""
        result = build_fts_query("这是一个关于Python的问题")
        # Stop words should not appear in the query
        assert '"是"' not in result
        assert '"一个"' not in result
        # Meaningful words should appear
        assert '"Python"' in result


# ---------------------------------------------------------------------------
# TestIsValidFtsToken
# ---------------------------------------------------------------------------

class TestIsValidFtsToken:
    """Tests for _is_valid_fts_token(token)."""

    @staticmethod
    def test_empty_token():
        """Empty string is invalid."""
        assert _is_valid_fts_token("") is False

    @staticmethod
    def test_whitespace_token():
        """Whitespace-only token is invalid."""
        assert _is_valid_fts_token("   ") is False

    @staticmethod
    def test_single_char_stop_word():
        """Single-char Chinese stop words are invalid."""
        assert _is_valid_fts_token("的") is False
        assert _is_valid_fts_token("了") is False
        assert _is_valid_fts_token("是") is False

    @staticmethod
    def test_single_char_alnum():
        """Single alphanumeric char is valid."""
        assert _is_valid_fts_token("A") is True
        assert _is_valid_fts_token("3") is True

    @staticmethod
    def test_meaningful_chinese_word():
        """Multi-char Chinese words are valid (if not in stop words)."""
        assert _is_valid_fts_token("清华") is True
        assert _is_valid_fts_token("人工智能") is True

    @staticmethod
    def test_meaningful_english_word():
        """Multi-char English words are valid (if not in stop words)."""
        assert _is_valid_fts_token("Python") is True
        assert _is_valid_fts_token("hello") is True

    @staticmethod
    def test_english_stop_word():
        """English stop words are invalid."""
        assert _is_valid_fts_token("the") is False
        assert _is_valid_fts_token("is") is False
        assert _is_valid_fts_token("and") is False

    @staticmethod
    def test_multi_char_chinese_stop_word():
        """Multi-char Chinese stop words are invalid."""
        assert _is_valid_fts_token("因为") is False
        assert _is_valid_fts_token("所以") is False

    @staticmethod
    def test_mixed_char_token():
        """Token with mixed alnum and symbols is valid (not pure P/S)."""
        assert _is_valid_fts_token("Python3") is True
