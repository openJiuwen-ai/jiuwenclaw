# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for the jiuwenswarm KV-cache prewarm package.

Tests cover:
  - PrewarmConfig.from_env() env-var parsing
  - PrewarmCoordinator body construction (reuses client._build_and_sanitize_params)
  - PrewarmCoordinator exception swallowing (HTTP / network errors never raise)
  - PrewarmCoordinator non-InferenceAffinity client is skipped
  - PrewarmRail scenario A (before_model_call, first call only)
  - PrewarmRail scenario B/C (after_model_call, with/without tool_calls)
  - PrewarmRail cache_sharing flag matches enable_kv_cache_release
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest

from jiuwenswarm.server.runtime.prewarm.config import PrewarmConfig
from jiuwenswarm.server.runtime.prewarm.coordinator import PrewarmCoordinator
from jiuwenswarm.server.runtime.prewarm.rail import PrewarmRail


# --------------------------------------------------------------------------- #
# Test doubles
# --------------------------------------------------------------------------- #

class _FakeModelClient:
    """Minimal stand-in for InferenceAffinityModelClient.

    Records the params handed to ``_build_and_sanitize_params`` so tests can
    assert the prewarm body, and exposes the attributes the coordinator reads
    (``__client_name__``, ``model_client_config``).
    """

    __client_name__ = "InferenceAffinity"

    def __init__(self, api_base: str = "http://vllm.local", custom_headers: Optional[dict] = None):
        self.calls: List[Dict[str, Any]] = []

        class _Cfg:
            pass
        cfg = _Cfg()
        cfg.api_base = api_base
        cfg.timeout = 5.0
        cfg.custom_headers = custom_headers or {}
        self.model_client_config = cfg

    def _build_and_sanitize_params(self, *, messages, tools, temperature, top_p,
                                    model, max_tokens, stop, stream,
                                    session_id=None, enable_cache_sharing=False,
                                    **kwargs):
        params: Dict[str, Any] = {
            "model": model or "test-model",
            "messages": list(messages),
            "stream": stream,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            params["tools"] = list(tools)
            params["tool_choice"] = "auto"
        if enable_cache_sharing and session_id:
            params["cache_sharing"] = True
            params["cache_salt"] = session_id
        self.calls.append({
            "messages": messages, "tools": tools, "temperature": temperature,
            "max_tokens": max_tokens, "stream": stream,
            "session_id": session_id, "enable_cache_sharing": enable_cache_sharing,
        })
        return params


class _OtherClient(_FakeModelClient):
    __client_name__ = "OpenAI"


class _FakeResponse:
    def __init__(self, status: int = 200):
        self.status = status
        self._body = b"{}"

    async def read(self):
        return self._body


class _FakePostCtx:
    def __init__(self, status: int = 200, exc: Optional[Exception] = None):
        self._response = _FakeResponse(status)
        self._exc = exc

    async def __aenter__(self):
        if self._exc is not None:
            raise self._exc
        return self._response

    async def __aexit__(self, *args):
        return False


class _FakeSessionCtx:
    def __init__(self, status: int = 200, exc: Optional[Exception] = None):
        self._status = status
        self._exc = exc
        self.posts = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def post(self, url, headers=None, json=None):
        ctx = _FakePostCtx(status=self._status, exc=self._exc)
        self.posts.append({"url": url, "headers": headers, "json": json})
        return ctx


def _patch_aiohttp_session(monkeypatch, status=200, exc=None):
    """Patch aiohttp.ClientSession in the coordinator module to a fake."""
    import jiuwenswarm.server.runtime.prewarm.coordinator as coord_mod

    captured = {}

    def _client_session(timeout=None):
        captured["timeout"] = timeout
        return _FakeSessionCtx(status=status, exc=exc)

    monkeypatch.setattr(coord_mod.aiohttp, "ClientSession", _client_session)
    return captured


# --------------------------------------------------------------------------- #
# PrewarmConfig
# --------------------------------------------------------------------------- #

class TestPrewarmConfig:
    def test_defaults_disabled(self, monkeypatch):
        for k in ("JIUWENSWARM_PREWARM_ENABLED",
                  "JIUWENSWARM_PREWARM_SCENARIO_A",
                  "JIUWENSWARM_PREWARM_SCENARIO_BC",
                  "JIUWENSWARM_PREWARM_TIMEOUT"):
            monkeypatch.delenv(k, raising=False)
        cfg = PrewarmConfig.from_env()
        assert cfg.enabled is False
        assert cfg.scenario_a is True
        assert cfg.scenario_bc is True
        assert cfg.timeout == 10.0

    def test_env_overrides(self, monkeypatch):
        monkeypatch.setenv("JIUWENSWARM_PREWARM_ENABLED", "true")
        monkeypatch.setenv("JIUWENSWARM_PREWARM_SCENARIO_A", "0")
        monkeypatch.setenv("JIUWENSWARM_PREWARM_SCENARIO_BC", "no")
        monkeypatch.setenv("JIUWENSWARM_PREWARM_TIMEOUT", "3.5")
        cfg = PrewarmConfig.from_env()
        assert cfg.enabled is True
        assert cfg.scenario_a is False
        assert cfg.scenario_bc is False
        assert cfg.timeout == 3.5


# --------------------------------------------------------------------------- #
# PrewarmCoordinator
# --------------------------------------------------------------------------- #

class TestPrewarmCoordinatorBody:
    async def test_build_body_max_tokens_one_stream_false(self):
        cfg = PrewarmConfig(enabled=True, scenario_a=True, scenario_bc=True, timeout=5.0)
        coord = PrewarmCoordinator(cfg)
        client = _FakeModelClient()

        # Patch HTTP to capture body without touching the network.
        captured = {}

        class _CapSession(_FakeSessionCtx):
            def post(self, url, headers=None, json=None):
                captured["url"] = url
                captured["headers"] = headers
                captured["body"] = json
                return super().post(url, headers=headers, json=json)

        import jiuwenswarm.server.runtime.prewarm.coordinator as coord_mod
        with patch.object(coord_mod.aiohttp, "ClientSession", lambda timeout=None: _CapSession()):
            await coord.prewarm(
                client,
                messages=[{"role": "system", "content": "hi"}],
                tools=None,
                model_name="glm-5",
                session_id="sess-1",
                enable_cache_sharing=False,
                scenario="A",
            )
            await asyncio.sleep(0.05)  # let the background task run

        assert client.calls[0]["max_tokens"] == 1
        assert client.calls[0]["stream"] is False
        assert client.calls[0]["temperature"] == 0
        assert captured["body"]["max_tokens"] == 1
        assert captured["body"]["stream"] is False
        assert captured["body"]["temperature"] == 0
        assert captured["url"] == "http://vllm.local/v1/chat/completions"

    async def test_build_body_cache_sharing_when_enabled(self):
        cfg = PrewarmConfig(enabled=True)
        coord = PrewarmCoordinator(cfg)
        client = _FakeModelClient()

        class _CapSession(_FakeSessionCtx):
            def __init__(self):
                super().__init__()
                self.body = None

            def post(self, url, headers=None, json=None):
                self.body = json
                return super().post(url, headers=headers, json=json)

        sess = _CapSession()
        import jiuwenswarm.server.runtime.prewarm.coordinator as coord_mod
        with patch.object(coord_mod.aiohttp, "ClientSession", lambda timeout=None: sess):
            await coord.prewarm(
                client,
                messages=[{"role": "user", "content": "hi"}],
                tools=None,
                model_name="glm-5",
                session_id="sess-42",
                enable_cache_sharing=True,
                scenario="C",
            )
            await asyncio.sleep(0.05)

        assert sess.body.get("cache_sharing") is True
        assert sess.body.get("cache_salt") == "sess-42"


class TestPrewarmCoordinatorSwallows:
    async def test_http_error_does_not_raise(self, monkeypatch):
        cfg = PrewarmConfig(enabled=True, timeout=2.0)
        coord = PrewarmCoordinator(cfg)
        client = _FakeModelClient()
        _patch_aiohttp_session(monkeypatch, status=500)
        # Should not raise.
        await coord.prewarm(
            client, messages=[{"role": "user", "content": "x"}],
            tools=None, model_name="m", session_id="s",
            enable_cache_sharing=False, scenario="A",
        )
        await asyncio.sleep(0.05)

    async def test_network_exception_does_not_raise(self, monkeypatch):
        cfg = PrewarmConfig(enabled=True, timeout=2.0)
        coord = PrewarmCoordinator(cfg)
        client = _FakeModelClient()
        _patch_aiohttp_session(monkeypatch, exc=asyncio.TimeoutError())
        await coord.prewarm(
            client, messages=[{"role": "user", "content": "x"}],
            tools=None, model_name="m", session_id="s",
            enable_cache_sharing=False, scenario="A",
        )
        await asyncio.sleep(0.05)

    async def test_disabled_coordinator_is_noop(self, monkeypatch):
        cfg = PrewarmConfig(enabled=False)
        coord = PrewarmCoordinator(cfg)
        client = _FakeModelClient()
        captured = _patch_aiohttp_session(monkeypatch)
        await coord.prewarm(
            client, messages=[{"role": "user", "content": "x"}],
            tools=None, model_name="m", session_id="s",
            enable_cache_sharing=False, scenario="A",
        )
        await asyncio.sleep(0.05)
        assert client.calls == []  # body never built

    async def test_other_provider_skipped(self, monkeypatch):
        cfg = PrewarmConfig(enabled=True)
        coord = PrewarmCoordinator(cfg)
        client = _OtherClient()
        _patch_aiohttp_session(monkeypatch)
        await coord.prewarm(
            client, messages=[{"role": "user", "content": "x"}],
            tools=None, model_name="m", session_id="s",
            enable_cache_sharing=False, scenario="A",
        )
        await asyncio.sleep(0.05)
        assert client.calls == []

    async def test_empty_messages_skipped(self, monkeypatch):
        cfg = PrewarmConfig(enabled=True)
        coord = PrewarmCoordinator(cfg)
        client = _FakeModelClient()
        _patch_aiohttp_session(monkeypatch)
        await coord.prewarm(
            client, messages=[], tools=None, model_name="m",
            session_id="s", enable_cache_sharing=False, scenario="A",
        )
        await asyncio.sleep(0.05)
        assert client.calls == []


# --------------------------------------------------------------------------- #
# PrewarmRail
# --------------------------------------------------------------------------- #

class _FakeUsageMetadata:
    def __init__(self, cache_tokens: int = 0, input_tokens: int = 0):
        self.cache_tokens = cache_tokens
        self.input_tokens = input_tokens


class _FakeAssistantMessage:
    def __init__(self, tool_calls=None, usage_metadata=None):
        self.tool_calls = tool_calls
        self.usage_metadata = usage_metadata


class _FakeInputs:
    def __init__(self, messages, tools=None, response=None):
        self.messages = messages
        self.tools = tools
        self.response = response


class _FakeSession:
    def __init__(self, sid="sess-rail"):
        self._sid = sid

    def get_session_id(self):
        return self._sid


class _FakeContextEngineConfig:
    def __init__(self, enable_kv_cache_release: bool):
        self.enable_kv_cache_release = enable_kv_cache_release


class _FakeReActConfig:
    def __init__(self, enable_kv_cache_release: bool, model_name: str = "glm-5"):
        self.model_name = model_name
        self.context_engine_config = _FakeContextEngineConfig(enable_kv_cache_release)


class _FakeModel:
    def __init__(self, client):
        self._client = client


class _FakeReActAgent:
    def __init__(self, client, enable_kv_cache_release: bool = False,
                 model_name: str = "glm-5"):
        self._client = client
        self._config = _FakeReActConfig(enable_kv_cache_release, model_name)

    def _get_llm(self):
        return _FakeModel(self._client)


class _FakeRailCtx:
    """Minimal stand-in for AgentCallbackContext."""

    def __init__(self, agent, inputs, session=None):
        self.agent = agent
        self.inputs = inputs
        self.session = session or _FakeSession()


class TestPrewarmRailScenarioA:
    async def test_before_model_call_fires_once(self, monkeypatch):
        coord = PrewarmCoordinator(PrewarmConfig(enabled=True))
        rail = PrewarmRail(coord)
        client = _FakeModelClient()
        agent = _FakeReActAgent(client)
        _patch_aiohttp_session(monkeypatch)

        inputs = _FakeInputs(messages=[{"role": "system", "content": "s"}], tools=None)
        ctx = _FakeRailCtx(agent, inputs)

        await rail.before_model_call(ctx)
        await asyncio.sleep(0.05)
        assert len(client.calls) == 1
        assert client.calls[0]["messages"] == inputs.messages

        # Second call must not fire scenario A again.
        await rail.before_model_call(ctx)
        await asyncio.sleep(0.05)
        assert len(client.calls) == 1

    async def test_before_model_call_skipped_when_disabled(self, monkeypatch):
        coord = PrewarmCoordinator(PrewarmConfig(enabled=False))
        rail = PrewarmRail(coord)
        client = _FakeModelClient()
        agent = _FakeReActAgent(client)
        _patch_aiohttp_session(monkeypatch)
        ctx = _FakeRailCtx(agent, _FakeInputs(messages=[{"role": "system", "content": "s"}]))
        await rail.before_model_call(ctx)
        await asyncio.sleep(0.05)
        assert client.calls == []


class TestPrewarmRailScenarioBC:
    async def test_after_model_call_scenario_b_with_tool_calls(self, monkeypatch):
        coord = PrewarmCoordinator(PrewarmConfig(enabled=True))
        rail = PrewarmRail(coord)
        client = _FakeModelClient()
        agent = _FakeReActAgent(client)
        captured = _patch_aiohttp_session(monkeypatch)

        response = _FakeAssistantMessage(tool_calls=[{"id": "t1", "function": {"name": "f"}}])
        inputs = _FakeInputs(
            messages=[{"role": "user", "content": "hi"}],
            tools=None,
            response=response,
        )
        ctx = _FakeRailCtx(agent, inputs)
        await rail.after_model_call(ctx)
        await asyncio.sleep(0.05)

        assert len(client.calls) == 1
        # Body should include the assistant response appended.
        sent_messages = client.calls[0]["messages"]
        assert sent_messages[-1] is response

    async def test_after_model_call_scenario_c_no_tool_calls(self, monkeypatch):
        coord = PrewarmCoordinator(PrewarmConfig(enabled=True))
        rail = PrewarmRail(coord)
        client = _FakeModelClient()
        agent = _FakeReActAgent(client)
        _patch_aiohttp_session(monkeypatch)

        response = _FakeAssistantMessage(tool_calls=None)
        inputs = _FakeInputs(messages=[{"role": "user", "content": "hi"}],
                             tools=None, response=response)
        ctx = _FakeRailCtx(agent, inputs)
        await rail.after_model_call(ctx)
        await asyncio.sleep(0.05)
        assert len(client.calls) == 1
        assert client.calls[0]["messages"][-1] is response

    async def test_after_model_call_no_response_skips(self, monkeypatch):
        coord = PrewarmCoordinator(PrewarmConfig(enabled=True))
        rail = PrewarmRail(coord)
        client = _FakeModelClient()
        agent = _FakeReActAgent(client)
        _patch_aiohttp_session(monkeypatch)
        inputs = _FakeInputs(messages=[{"role": "user", "content": "hi"}],
                             tools=None, response=None)
        ctx = _FakeRailCtx(agent, inputs)
        await rail.after_model_call(ctx)
        await asyncio.sleep(0.05)
        assert client.calls == []


class TestPrewarmRailCacheSharingFlag:
    async def test_sharing_true_when_kv_release_enabled(self, monkeypatch):
        coord = PrewarmCoordinator(PrewarmConfig(enabled=True))
        rail = PrewarmRail(coord)
        client = _FakeModelClient()
        agent = _FakeReActAgent(client, enable_kv_cache_release=True)
        _patch_aiohttp_session(monkeypatch)
        ctx = _FakeRailCtx(agent, _FakeInputs(
            messages=[{"role": "user", "content": "hi"}], tools=None,
            response=_FakeAssistantMessage()))
        await rail.after_model_call(ctx)
        await asyncio.sleep(0.05)
        assert client.calls[0]["enable_cache_sharing"] is True
        assert client.calls[0]["session_id"] == "sess-rail"

    async def test_sharing_false_when_kv_release_disabled(self, monkeypatch):
        coord = PrewarmCoordinator(PrewarmConfig(enabled=True))
        rail = PrewarmRail(coord)
        client = _FakeModelClient()
        agent = _FakeReActAgent(client, enable_kv_cache_release=False)
        _patch_aiohttp_session(monkeypatch)
        ctx = _FakeRailCtx(agent, _FakeInputs(
            messages=[{"role": "user", "content": "hi"}], tools=None,
            response=_FakeAssistantMessage()))
        await rail.after_model_call(ctx)
        await asyncio.sleep(0.05)
        assert client.calls[0]["enable_cache_sharing"] is False


class TestPrewarmRailDebugLog:
    @staticmethod
    def _patch_stdlib_logger(monkeypatch):
        """Force the rail module's logger to a stdlib logger so caplog can see it."""
        import logging
        import jiuwenswarm.server.runtime.prewarm.rail as rail_mod
        stdlib_logger = logging.getLogger("prewarm_test")
        monkeypatch.setattr(rail_mod, "logger", stdlib_logger)
        return "prewarm_test"

    async def test_debug_log_emits_cache_hit_ratio(self, monkeypatch, caplog):
        import logging
        log_name = self._patch_stdlib_logger(monkeypatch)
        coord = PrewarmCoordinator(PrewarmConfig(enabled=True, debug=True))
        rail = PrewarmRail(coord)
        client = _FakeModelClient()
        agent = _FakeReActAgent(client)
        _patch_aiohttp_session(monkeypatch)

        usage = _FakeUsageMetadata(cache_tokens=800, input_tokens=1000)
        response = _FakeAssistantMessage(tool_calls=None, usage_metadata=usage)
        ctx = _FakeRailCtx(agent, _FakeInputs(
            messages=[{"role": "user", "content": "hi"}], tools=None,
            response=response))

        with caplog.at_level(logging.INFO, logger=log_name):
            await rail.after_model_call(ctx)
            await asyncio.sleep(0.05)

        assert any(
            "cache_read=800" in r.message and "input=1000" in r.message
            and "hit_ratio=0.80" in r.message
            for r in caplog.records
        )

    async def test_debug_log_silent_when_disabled(self, monkeypatch, caplog):
        import logging
        log_name = self._patch_stdlib_logger(monkeypatch)
        coord = PrewarmCoordinator(PrewarmConfig(enabled=True, debug=False))
        rail = PrewarmRail(coord)
        client = _FakeModelClient()
        agent = _FakeReActAgent(client)
        _patch_aiohttp_session(monkeypatch)

        usage = _FakeUsageMetadata(cache_tokens=800, input_tokens=1000)
        response = _FakeAssistantMessage(usage_metadata=usage)
        ctx = _FakeRailCtx(agent, _FakeInputs(
            messages=[{"role": "user", "content": "hi"}], tools=None,
            response=response))

        with caplog.at_level(logging.INFO, logger=log_name):
            await rail.after_model_call(ctx)
            await asyncio.sleep(0.05)

        assert not any("hit_ratio" in r.message for r in caplog.records)

    async def test_debug_log_handles_missing_usage(self, monkeypatch, caplog):
        import logging
        log_name = self._patch_stdlib_logger(monkeypatch)
        coord = PrewarmCoordinator(PrewarmConfig(enabled=True, debug=True))
        rail = PrewarmRail(coord)
        client = _FakeModelClient()
        agent = _FakeReActAgent(client)
        _patch_aiohttp_session(monkeypatch)

        response = _FakeAssistantMessage(usage_metadata=None)
        ctx = _FakeRailCtx(agent, _FakeInputs(
            messages=[{"role": "user", "content": "hi"}], tools=None,
            response=response))

        with caplog.at_level(logging.INFO, logger=log_name):
            await rail.after_model_call(ctx)
            await asyncio.sleep(0.05)

        assert any("usage=none" in r.message for r in caplog.records)
