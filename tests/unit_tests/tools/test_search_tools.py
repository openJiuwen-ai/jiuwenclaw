from __future__ import annotations

import importlib
import os
import sys
import types
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[3]
_MODULE_PATH = _REPO_ROOT / "jiuwenswarm" / "agents" / "harness" / "common" / "tools" / "search_tools.py"


def _load_module():
    if "search_tools_mod" in sys.modules:
        return sys.modules["search_tools_mod"]

    ssl_config_mod = types.ModuleType("jiuwenswarm.agents.harness.common.tools.ssl_config")
    ssl_config_mod.get_requests_verify = lambda: True
    ssl_config_mod.get_ssl_verify = lambda: True
    previous_ssl = sys.modules.get("jiuwenswarm.agents.harness.common.tools.ssl_config")
    sys.modules["jiuwenswarm.agents.harness.common.tools.ssl_config"] = ssl_config_mod

    tools_pkg = sys.modules.get("jiuwenswarm.agents.harness.common.tools")
    if tools_pkg is None:
        tools_pkg = types.ModuleType("jiuwenswarm.agents.harness.common.tools")
        tools_pkg.__path__ = [str(_REPO_ROOT / "jiuwenswarm" / "agents" / "harness" / "common" / "tools")]
        tools_pkg.__package__ = "jiuwenswarm.agents.harness.common.tools"
        sys.modules["jiuwenswarm.agents.harness.common.tools"] = tools_pkg

    spec = importlib.util.spec_from_file_location("search_tools_mod", str(_MODULE_PATH))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["search_tools_mod"] = mod
    try:
        spec.loader.exec_module(mod)
    finally:
        # Avoid permanently shadowing the real ssl_config for other test modules.
        if previous_ssl is None:
            sys.modules.pop("jiuwenswarm.agents.harness.common.tools.ssl_config", None)
        else:
            sys.modules["jiuwenswarm.agents.harness.common.tools.ssl_config"] = previous_ssl
    return mod


_mod = _load_module()


def _tool_fn(tool_obj):
    if hasattr(tool_obj, "_func"):
        return tool_obj._func
    if hasattr(tool_obj, "fn"):
        return tool_obj.fn
    if hasattr(tool_obj, "__wrapped__"):
        return tool_obj.__wrapped__
    return tool_obj


_paid_search_fn = _tool_fn(_mod.mcp_paid_search)


def test_parse_default_headers_json():
    result = _mod._parse_default_headers('{"Authorization": "Bearer test-token", "X-Custom": "value"}')
    assert result == {"Authorization": "Bearer test-token", "X-Custom": "value"}


def test_parse_default_headers_semicolon():
    raw = "Authorization=Bearer test-token; X-Custom=value"
    result = _mod._parse_default_headers(raw)
    assert result == {"Authorization": "Bearer test-token", "X-Custom": "value"}


def test_parse_default_headers_empty():
    assert _mod._parse_default_headers("") == {}
    assert _mod._parse_default_headers("  ") == {}


def test_resolve_petal_search_url_reads_dedicated_env_var(monkeypatch):
    monkeypatch.setenv("PETAL_SEARCH_URL", "https://petal.example.com/web-search")
    assert _mod._resolve_petal_search_url() == "https://petal.example.com/web-search"


def test_resolve_petal_search_url_raises_when_not_set(monkeypatch):
    monkeypatch.delenv("PETAL_SEARCH_URL", raising=False)
    with pytest.raises(ValueError, match="PETAL_SEARCH_URL is not set"):
        _mod._resolve_petal_search_url()


def test_load_petal_default_headers_reads_dedicated_env_var(monkeypatch):
    monkeypatch.setenv("PETAL_SEARCH_HEADERS", '{"Authorization": "Bearer test-token"}')
    result = _mod._load_petal_default_headers()
    assert result == {"Authorization": "Bearer test-token"}


def test_load_petal_default_headers_raises_when_not_set(monkeypatch):
    monkeypatch.delenv("PETAL_SEARCH_HEADERS", raising=False)
    with pytest.raises(ValueError, match="PETAL_SEARCH_HEADERS is not set"):
        _mod._load_petal_default_headers()


def test_paid_provider_available_petal(monkeypatch):
    monkeypatch.delenv("PETAL_SEARCH_URL", raising=False)
    monkeypatch.delenv("PETAL_SEARCH_HEADERS", raising=False)
    assert _mod._paid_provider_available("petal") is False

    monkeypatch.setenv("PETAL_SEARCH_URL", "https://petal.example.com")
    monkeypatch.setenv("PETAL_SEARCH_HEADERS", '{"Authorization": "Bearer test"}')
    assert _mod._paid_provider_available("petal") is True


def test_paid_provider_available_bocha(monkeypatch):
    monkeypatch.delenv("BOCHA_API_KEY", raising=False)
    assert _mod._paid_provider_available("bocha") is False

    monkeypatch.setenv("BOCHA_API_KEY", "test-key")
    assert _mod._paid_provider_available("bocha") is True


def test_paid_provider_skip_reason(monkeypatch):
    monkeypatch.delenv("BOCHA_API_KEY", raising=False)
    assert "BOCHA_API_KEY not set" in _mod._paid_provider_skip_reason("bocha")

    monkeypatch.delenv("PETAL_SEARCH_URL", raising=False)
    assert "PETAL_SEARCH_URL not set" in _mod._paid_provider_skip_reason("petal")


def test_configured_paid_providers(monkeypatch):
    monkeypatch.delenv("PETAL_SEARCH_URL", raising=False)
    monkeypatch.delenv("PETAL_SEARCH_HEADERS", raising=False)
    monkeypatch.delenv("BOCHA_API_KEY", raising=False)
    monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    monkeypatch.delenv("JINA_API_KEY", raising=False)

    assert _mod._configured_paid_providers() == []

    monkeypatch.setenv("BOCHA_API_KEY", "test-key")
    assert _mod._configured_paid_providers() == ["bocha"]

    monkeypatch.setenv("PETAL_SEARCH_URL", "https://petal.example.com")
    monkeypatch.setenv("PETAL_SEARCH_HEADERS", '{"Authorization": "Bearer test"}')
    assert _mod._configured_paid_providers() == ["petal", "bocha"]


def test_detect_requested_engine():
    assert _mod._detect_requested_engine("bing 今天的天气") == "bing"
    assert _mod._detect_requested_engine("用duckduckgo搜索") == "duckduckgo"
    assert _mod._detect_requested_engine("用花瓣搜索") == "petal"
    assert _mod._detect_requested_engine("博查搜索结果") == "bocha"
    assert _mod._detect_requested_engine("普通搜索") is None


def test_detect_requested_engine_avoids_substring_false_positive():
    assert _mod._detect_requested_engine("person 如何修复") is None
    assert _mod._detect_requested_engine("also 相关资讯") is None
    assert _mod._detect_requested_engine("so 如何解决") == "360"
    assert _mod._detect_requested_engine("googlesearch 资讯") is None
    assert _mod._detect_requested_engine("谷歌搜索") == "google"


def test_generate_engine_mismatch_warning_match():
    assert _mod._generate_engine_mismatch_warning("用bocha搜索", "bocha") is None
    assert _mod._generate_engine_mismatch_warning("用花瓣搜索", "petal") is None


def test_generate_engine_mismatch_warning_mismatch():
    warning = _mod._generate_engine_mismatch_warning("用bing搜索", "bocha")
    assert warning is not None
    assert "bing" in warning
    assert "bocha" in warning


def test_free_search_engines_disabled_by_default(monkeypatch):
    monkeypatch.delenv("FREE_SEARCH_DDG_ENABLED", raising=False)
    monkeypatch.delenv("FREE_SEARCH_BING_ENABLED", raising=False)

    assert _mod._free_search_engines() == []


def test_free_search_engines_enabled(monkeypatch):
    monkeypatch.setenv("FREE_SEARCH_DDG_ENABLED", "true")
    monkeypatch.setenv("FREE_SEARCH_BING_ENABLED", "true")

    engines = _mod._free_search_engines()
    assert "duckduckgo" in engines
    assert "bing" in engines


def test_build_paid_search_description_contains_configured_providers(monkeypatch):
    monkeypatch.delenv("PETAL_SEARCH_URL", raising=False)
    monkeypatch.delenv("PETAL_SEARCH_HEADERS", raising=False)
    monkeypatch.setenv("BOCHA_API_KEY", "test-key")
    monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    monkeypatch.delenv("JINA_API_KEY", raising=False)

    desc = _mod._build_paid_search_description()
    assert "bocha" in desc
    assert "search_source" in desc


def test_build_paid_search_description_includes_petal(monkeypatch):
    monkeypatch.setenv("PETAL_SEARCH_URL", "https://petal.example.com")
    monkeypatch.setenv("PETAL_SEARCH_HEADERS", '{"Authorization": "Bearer test"}')
    monkeypatch.setenv("BOCHA_API_KEY", "test-key")
    monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    monkeypatch.delenv("JINA_API_KEY", raising=False)

    desc = _mod._build_paid_search_description()
    assert "petal" in desc
    assert "bocha" in desc


def test_build_paid_search_description_hides_free_engines_when_disabled(monkeypatch):
    monkeypatch.delenv("FREE_SEARCH_DDG_ENABLED", raising=False)
    monkeypatch.delenv("FREE_SEARCH_BING_ENABLED", raising=False)
    monkeypatch.delenv("PETAL_SEARCH_URL", raising=False)
    monkeypatch.delenv("PETAL_SEARCH_HEADERS", raising=False)
    monkeypatch.setenv("BOCHA_API_KEY", "test-key")

    desc = _mod._build_paid_search_description()
    assert "duckduckgo" not in desc


def test_build_paid_search_description_shows_free_engines_when_enabled(monkeypatch):
    monkeypatch.setenv("FREE_SEARCH_DDG_ENABLED", "true")
    monkeypatch.setenv("FREE_SEARCH_BING_ENABLED", "true")
    monkeypatch.delenv("PETAL_SEARCH_URL", raising=False)
    monkeypatch.delenv("PETAL_SEARCH_HEADERS", raising=False)
    monkeypatch.setenv("BOCHA_API_KEY", "test-key")

    desc = _mod._build_paid_search_description()
    assert "duckduckgo" in desc


@pytest.mark.asyncio
async def test_mcp_paid_search_with_search_source(monkeypatch):
    monkeypatch.setenv("BOCHA_API_KEY", "test-key")
    monkeypatch.delenv("PETAL_SEARCH_URL", raising=False)
    monkeypatch.delenv("PETAL_SEARCH_HEADERS", raising=False)

    result = await _paid_search_fn("test query", provider="auto", search_source="bocha")
    assert isinstance(result, str)


@pytest.mark.asyncio
async def test_mcp_paid_search_unavailable_source_returns_error(monkeypatch):
    monkeypatch.delenv("PETAL_SEARCH_URL", raising=False)
    monkeypatch.delenv("PETAL_SEARCH_HEADERS", raising=False)
    monkeypatch.delenv("BOCHA_API_KEY", raising=False)
    monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    monkeypatch.delenv("JINA_API_KEY", raising=False)

    result = await _paid_search_fn("test query", provider="auto", search_source="bocha")
    assert "[ERROR]" in result


@pytest.mark.asyncio
async def test_mcp_paid_search_engine_mismatch_warning(monkeypatch):
    monkeypatch.setenv("BOCHA_API_KEY", "test-key")
    monkeypatch.delenv("PETAL_SEARCH_URL", raising=False)
    monkeypatch.delenv("PETAL_SEARCH_HEADERS", raising=False)
    monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    monkeypatch.delenv("JINA_API_KEY", raising=False)

    from unittest.mock import patch

    def fake_bocha(query, max_results, timeout_seconds):
        return {"provider": "bocha", "answer": "test answer", "urls": ["https://example.com"]}

    with patch.object(_mod, "_bocha_search_sync", side_effect=fake_bocha):
        result = await _paid_search_fn("用bing搜索天气", provider="auto")
        assert "bocha" in result
        assert "⚠️" in result


@pytest.mark.asyncio
async def test_mcp_paid_search_petal_provider(monkeypatch):
    monkeypatch.setenv("PETAL_SEARCH_URL", "https://petal.example.com")
    monkeypatch.setenv("PETAL_SEARCH_HEADERS", '{"Authorization": "Bearer test"}')
    monkeypatch.setenv("BOCHA_API_KEY", "test-key")

    result = await _paid_search_fn("test query", provider="petal")
    assert isinstance(result, str)


@pytest.mark.asyncio
async def test_mcp_paid_search_petal_not_configured(monkeypatch):
    monkeypatch.delenv("PETAL_SEARCH_URL", raising=False)
    monkeypatch.delenv("PETAL_SEARCH_HEADERS", raising=False)

    result = await _paid_search_fn("test query", provider="petal")
    assert "[ERROR]" in result
    assert "PETAL_SEARCH_URL" in result


def test_env_flag_defaults_to_false(monkeypatch):
    monkeypatch.delenv("FREE_SEARCH_DDG_ENABLED", raising=False)
    assert _mod._env_flag("FREE_SEARCH_DDG_ENABLED") is False


def test_env_flag_can_be_enabled(monkeypatch):
    monkeypatch.setenv("FREE_SEARCH_DDG_ENABLED", "true")
    assert _mod._env_flag("FREE_SEARCH_DDG_ENABLED") is True
