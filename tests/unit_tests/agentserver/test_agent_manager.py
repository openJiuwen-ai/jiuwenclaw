# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""AgentManager 单元测试。

覆盖：
- 纯函数：_build_acp_agent_config、_apply_env_overrides、_parse_request_meta
- 类方法：__init__、initialize、get_agent（并发竞态、复用）、_create_agent（回滚）、
  cleanup_session、cleanup（失败保留）、reload_agents_config（隔离失败）、
  process_message / process_message_stream（路由）
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import types
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture(autouse=True)
def _isolate_module(monkeypatch):
    """每个用例独立导入 agent_manager，并伪造 ACP 协议依赖。"""
    monkeypatch.delitem(sys.modules, "jiuwenclaw.agentserver.agent_manager", raising=False)

    # 伪造 jiuwenclaw.e2a.acp.protocol
    fake_protocol = types.ModuleType("jiuwenclaw.e2a.acp.protocol")
    fake_protocol.build_acp_initialize_result = lambda: {"capabilities": {"version": "test"}}
    monkeypatch.setitem(sys.modules, "jiuwenclaw.e2a.acp.protocol", fake_protocol)

    # 替换 logger，避免触发真实日志过滤器加载扩展模块
    yield


@pytest.fixture()
def am_module(monkeypatch):
    import importlib
    import jiuwenclaw.agentserver.agent_manager as mod
    mod = importlib.reload(mod)
    clean_logger = logging.getLogger("test.agent_manager.isolated")
    clean_logger.handlers.clear()
    clean_logger.propagate = False
    mod.logger = clean_logger
    return mod


def _make_fake_jiuwenclaw_class(monkeypatch, instances_holder: list) -> None:
    """注入伪造的 JiuWenClaw 类（懒导入路径）。"""

    class _FakeJiuWenClaw:
        def __init__(self, user_workspace_dir=None, agent_id="", service_id=""):
            self._agent_name = ""
            self.user_workspace_dir = user_workspace_dir
            self.agent_id = agent_id
            self.service_id = service_id
            self.created = False
            self.reload_calls: list[dict[str, Any]] = []
            self.cleanup_called = False
            self.reload_should_raise: Exception | None = None
            self.cleanup_should_raise: Exception | None = None
            self.is_working_value = False
            instances_holder.append(self)

        async def create_instance(self, config, mode):
            self.created = True
            self.config = config
            self.mode = mode

        async def reload_agent_config(self, config_base, env_overrides):
            self.reload_calls.append({"config": config_base, "env": env_overrides})
            if self.reload_should_raise is not None:
                raise self.reload_should_raise

        async def cleanup(self):
            self.cleanup_called = True
            if self.cleanup_should_raise is not None:
                raise self.cleanup_should_raise

        async def cancel_inflight_work(self, reason: str):
            self.cancel_reason = reason

        async def process_message(self, request):
            return {"ok": True, "request_id": getattr(request, "request_id", "")}

        async def process_message_stream(self, request):
            yield {"chunk": 1}
            yield {"chunk": 2}

        def is_working(self) -> bool:
            return self.is_working_value

        def get_instance(self):
            return types.SimpleNamespace(
                card=types.SimpleNamespace(),
                switch_mode=lambda session, mode: None,
                load_state=lambda session: types.SimpleNamespace(
                    to_session_dict=lambda: {}
                ),
            )

    fake_iface_module = types.ModuleType("jiuwenclaw.agentserver.interface")
    fake_iface_module.JiuWenClaw = _FakeJiuWenClaw
    monkeypatch.setitem(sys.modules, "jiuwenclaw.agentserver.interface", fake_iface_module)
    return _FakeJiuWenClaw


def _make_request(**kwargs):
    defaults = {
        "request_id": "rid-1",
        "channel_id": "default",
        "session_id": None,
        "params": {"mode": "agent.plan"},
    }
    defaults.update(kwargs)
    return types.SimpleNamespace(**defaults)


# ──────────────────────────────────────────────────────────────────────────────
# 纯函数测试
# ──────────────────────────────────────────────────────────────────────────────


class TestBuildAcpAgentConfig:
    def test_default(self, am_module):
        cfg = am_module._build_acp_agent_config()
        assert cfg["channel_id"] == "acp"
        assert cfg["tool_profile"] == "acp"
        assert cfg["enable_filesystem_rail"] is True

    def test_extra_config_merged(self, am_module):
        cfg = am_module._build_acp_agent_config({"foo": "bar"})
        assert cfg["foo"] == "bar"

    def test_fixed_fields_cannot_be_overridden(self, am_module):
        cfg = am_module._build_acp_agent_config({"channel_id": "evil", "tool_profile": "x"})
        assert cfg["channel_id"] == "acp"
        assert cfg["tool_profile"] == "acp"

    def test_none_extra(self, am_module):
        cfg = am_module._build_acp_agent_config(None)
        assert cfg["channel_id"] == "acp"


class TestApplyEnvOverrides:
    def test_none_is_noop(self, am_module, monkeypatch):
        monkeypatch.setenv("UT_AM_KEEP", "x")
        am_module._apply_env_overrides(None)
        assert os.environ["UT_AM_KEEP"] == "x"

    def test_empty_is_noop(self, am_module, monkeypatch):
        monkeypatch.setenv("UT_AM_KEEP", "x")
        am_module._apply_env_overrides({})
        assert os.environ["UT_AM_KEEP"] == "x"

    def test_sets_values(self, am_module, monkeypatch):
        monkeypatch.delenv("UT_AM_SET", raising=False)
        am_module._apply_env_overrides({"UT_AM_SET": "v"})
        assert os.environ["UT_AM_SET"] == "v"

    def test_none_value_deletes(self, am_module, monkeypatch):
        monkeypatch.setenv("UT_AM_DEL", "x")
        am_module._apply_env_overrides({"UT_AM_DEL": None})
        assert "UT_AM_DEL" not in os.environ

    def test_non_string_value_coerced(self, am_module, monkeypatch):
        monkeypatch.delenv("UT_AM_NUM", raising=False)
        am_module._apply_env_overrides({"UT_AM_NUM": 42})
        assert os.environ["UT_AM_NUM"] == "42"


class TestParseRequestMeta:
    def test_default_request(self, am_module):
        req = _make_request()
        channel, sid, mode_full, mode, ws = am_module._parse_request_meta(req)
        assert channel == "default"
        assert sid is None
        assert mode_full == "agent.plan"
        assert mode == "agent"
        assert ws is None

    def test_code_mode(self, am_module):
        req = _make_request(params={"mode": "code.write", "workspace_dir": "/ws"})
        _, _, mode_full, mode, ws = am_module._parse_request_meta(req)
        assert mode_full == "code.write"
        assert mode == "code"
        assert ws == "/ws"

    def test_invalid_params_falls_back(self, am_module):
        req = _make_request(params="not-a-dict")
        _, _, mode_full, mode, ws = am_module._parse_request_meta(req)
        assert mode == "agent"
        assert ws is None

    def test_missing_channel(self, am_module):
        req = types.SimpleNamespace(params={})
        channel, *_ = am_module._parse_request_meta(req)
        assert channel == ""


# ──────────────────────────────────────────────────────────────────────────────
# AgentManager 类方法测试
# ──────────────────────────────────────────────────────────────────────────────


class TestInit:
    def test_basic_init(self, am_module):
        am = am_module.AgentManager("aid", "sid")
        assert am.agent_id == "aid"
        assert am.service_id == "sid"
        assert am.agents == {}
        assert am._latest_env_overrides == {}

    def test_applies_initial_env(self, am_module, monkeypatch):
        monkeypatch.delenv("UT_AM_INIT", raising=False)
        am_module.AgentManager(
            "aid", "sid",
            env_overrides={"UT_AM_INIT": "init-val"},
        )
        assert os.environ["UT_AM_INIT"] == "init-val"

    def test_invalid_env_overrides_ignored(self, am_module):
        am = am_module.AgentManager("aid", "sid", env_overrides="not-dict")  # type: ignore[arg-type]
        assert am._latest_env_overrides == {}


class TestGetAgent:
    def test_creates_and_returns(self, am_module, monkeypatch):
        instances: list = []
        _make_fake_jiuwenclaw_class(monkeypatch, instances)
        am = am_module.AgentManager("aid", "sid")

        agent = asyncio.run(am.get_agent(channel_id="ch1", mode="agent"))
        assert agent is instances[0]
        assert agent.created is True

    def test_reuses_existing(self, am_module, monkeypatch):
        instances: list = []
        _make_fake_jiuwenclaw_class(monkeypatch, instances)
        am = am_module.AgentManager("aid", "sid")

        async def run():
            a1 = await am.get_agent(channel_id="ch1", mode="agent", session_id="s1")
            a2 = await am.get_agent(channel_id="ch1", mode="agent", session_id="s1")
            return a1, a2

        a1, a2 = asyncio.run(run())
        assert a1 is a2
        assert len(instances) == 1  # 只创建一次

    def test_concurrent_get_creates_only_once(self, am_module, monkeypatch):
        """验证并发 get_agent 不会重复创建（双检锁）。"""
        instances: list = []
        _make_fake_jiuwenclaw_class(monkeypatch, instances)
        am = am_module.AgentManager("aid", "sid")

        async def run():
            tasks = [
                am.get_agent(channel_id="ch1", mode="agent", session_id="same")
                for _ in range(10)
            ]
            results = await asyncio.gather(*tasks)
            return results

        results = asyncio.run(run())
        assert all(r is results[0] for r in results)
        assert len(instances) == 1  # 10 个并发请求只产生 1 个 agent

    def test_isolation_by_session_id(self, am_module, monkeypatch):
        instances: list = []
        _make_fake_jiuwenclaw_class(monkeypatch, instances)
        am = am_module.AgentManager("aid", "sid")

        async def run():
            a1 = await am.get_agent(channel_id="c", mode="m", session_id="s1")
            a2 = await am.get_agent(channel_id="c", mode="m", session_id="s2")
            return a1, a2

        a1, a2 = asyncio.run(run())
        assert a1 is not a2
        assert len(instances) == 2


class TestGetAgentNowait:
    def test_returns_none_when_missing(self, am_module):
        am = am_module.AgentManager("aid", "sid")
        assert am.get_agent_nowait(channel_id="x") is None

    def test_returns_existing(self, am_module, monkeypatch):
        instances: list = []
        _make_fake_jiuwenclaw_class(monkeypatch, instances)
        am = am_module.AgentManager("aid", "sid")
        asyncio.run(am.get_agent(channel_id="ch", mode="agent", session_id="s"))
        assert am.get_agent_nowait(channel_id="ch", mode="agent", session_id="s") is instances[0]


class TestCreateAgentRollback:
    def test_replay_failure_rolls_back(self, am_module, monkeypatch):
        """replay reload 失败时 agent 不应被注册，且应被 cleanup。"""
        instances: list = []
        fake_cls = _make_fake_jiuwenclaw_class(monkeypatch, instances)

        # 通过 config_base 触发 replay
        am = am_module.AgentManager("aid", "sid", config_base={"k": "v"})

        # patch fake_cls 以便注入 reload 失败
        original_init = fake_cls.__init__

        def _patched_init(self, **kwargs):
            original_init(self, **kwargs)
            self.reload_should_raise = RuntimeError("replay failed")

        fake_cls.__init__ = _patched_init

        with pytest.raises(RuntimeError, match="replay failed"):
            asyncio.run(am.get_agent(channel_id="ch", mode="m", session_id="s"))

        # 不应注册到 agents
        assert am._lookup_agent("ch", "m", "s") is None
        # cleanup 应被调用
        assert instances[0].cleanup_called is True


class TestCleanupSession:
    def test_removes_agent(self, am_module, monkeypatch):
        instances: list = []
        _make_fake_jiuwenclaw_class(monkeypatch, instances)
        am = am_module.AgentManager("aid", "sid")

        async def run():
            await am.get_agent(channel_id="ch", mode="m", session_id="s")
            await am.cleanup_session("ch", "m", "s")

        asyncio.run(run())
        assert instances[0].cleanup_called is True
        assert am._lookup_agent("ch", "m", "s") is None

    def test_no_op_when_missing(self, am_module):
        am = am_module.AgentManager("aid", "sid")
        # 不应抛异常
        asyncio.run(am.cleanup_session("x", "y", "z"))

    def test_cleanup_failure_logged(self, am_module, monkeypatch):
        instances: list = []
        fake_cls = _make_fake_jiuwenclaw_class(monkeypatch, instances)
        am = am_module.AgentManager("aid", "sid")

        async def run():
            agent = await am.get_agent(channel_id="ch", mode="m", session_id="s")
            agent.cleanup_should_raise = RuntimeError("boom")
            await am.cleanup_session("ch", "m", "s")

        # 不应抛异常
        asyncio.run(run())


class TestCleanup:
    def test_removes_successful_agents(self, am_module, monkeypatch):
        instances: list = []
        _make_fake_jiuwenclaw_class(monkeypatch, instances)
        am = am_module.AgentManager("aid", "sid")

        async def run():
            await am.get_agent(channel_id="c1", mode="m", session_id="s1")
            await am.get_agent(channel_id="c2", mode="m", session_id="s2")
            await am.cleanup()

        asyncio.run(run())
        assert am.agents == {}
        assert all(inst.cleanup_called for inst in instances)

    def test_keeps_failed_agents(self, am_module, monkeypatch):
        """cleanup 失败的 agent 应保留在 self.agents 中。"""
        instances: list = []
        _make_fake_jiuwenclaw_class(monkeypatch, instances)
        am = am_module.AgentManager("aid", "sid")

        async def run():
            a1 = await am.get_agent(channel_id="c1", mode="m", session_id="s1")
            a2 = await am.get_agent(channel_id="c2", mode="m", session_id="s2")
            a1.cleanup_should_raise = RuntimeError("c1 failed")
            await am.cleanup()

        asyncio.run(run())
        # c1 失败应保留
        assert "c1" in am.agents
        # c2 成功应被移除
        assert "c2" not in am.agents


class TestReloadAgentsConfig:
    def test_isolates_failures(self, am_module, monkeypatch):
        """单个 agent reload 失败不应中断其他 agent。"""
        instances: list = []
        _make_fake_jiuwenclaw_class(monkeypatch, instances)
        am = am_module.AgentManager("aid", "sid")

        async def run():
            await am.get_agent(channel_id="c1", mode="m", session_id="s1")
            await am.get_agent(channel_id="c2", mode="m", session_id="s2")
            # 让第一个 agent reload 失败
            instances[0].reload_should_raise = RuntimeError("fail")
            await am.reload_agents_config({"k": "v"}, {"E": "1"})

        asyncio.run(run())
        # 第二个 agent 仍应收到 reload 调用
        assert any("E" == k for call in instances[1].reload_calls for k in (call["env"] or {}))


class TestProcessMessage:
    def test_routes_to_agent(self, am_module, monkeypatch):
        instances: list = []
        _make_fake_jiuwenclaw_class(monkeypatch, instances)
        am = am_module.AgentManager("aid", "sid")
        req = _make_request(channel_id="ch", request_id="r1")
        result = asyncio.run(am.process_message(req))
        assert result == {"ok": True, "request_id": "r1"}

    def test_stream_yields_chunks(self, am_module, monkeypatch):
        instances: list = []
        _make_fake_jiuwenclaw_class(monkeypatch, instances)
        am = am_module.AgentManager("aid", "sid")
        req = _make_request(channel_id="ch")

        async def collect():
            return [c async for c in am.process_message_stream(req)]

        chunks = asyncio.run(collect())
        assert chunks == [{"chunk": 1}, {"chunk": 2}]


class TestCreateSession:
    def test_explicit_id_preserved(self, am_module):
        am = am_module.AgentManager("aid", "sid")
        sid = asyncio.run(am.create_session(channel_id="x", session_id="my-id"))
        assert sid == "my-id"

    def test_acp_generates_id(self, am_module):
        am = am_module.AgentManager("aid", "sid")
        sid = asyncio.run(am.create_session(channel_id="acp"))
        assert sid.startswith("acp_")

    def test_default_channel(self, am_module):
        am = am_module.AgentManager("aid", "sid")
        sid = asyncio.run(am.create_session(channel_id=""))
        assert sid == "default"


class TestGetClientCapabilities:
    def test_returns_empty_when_unset(self, am_module):
        am = am_module.AgentManager("aid", "sid")
        assert am.get_client_capabilities("x") == {}

    def test_returns_copy(self, am_module):
        am = am_module.AgentManager("aid", "sid")
        am._client_capabilities_by_channel["acp"] = {"k": "v"}
        caps = am.get_client_capabilities("acp")
        caps["mutated"] = True
        # 修改返回值不应影响内部状态
        assert "mutated" not in am._client_capabilities_by_channel["acp"]


class TestIsWorking:
    def test_no_agents(self, am_module):
        am = am_module.AgentManager("aid", "sid")
        assert am.is_working() is False

    def test_returns_true_if_any_working(self, am_module, monkeypatch):
        instances: list = []
        _make_fake_jiuwenclaw_class(monkeypatch, instances)
        am = am_module.AgentManager("aid", "sid")
        asyncio.run(am.get_agent(channel_id="c1", mode="m", session_id="s1"))
        asyncio.run(am.get_agent(channel_id="c2", mode="m", session_id="s2"))
        instances[1].is_working_value = True
        assert am.is_working() is True

    def test_all_idle(self, am_module, monkeypatch):
        instances: list = []
        _make_fake_jiuwenclaw_class(monkeypatch, instances)
        am = am_module.AgentManager("aid", "sid")
        asyncio.run(am.get_agent(channel_id="c", mode="m", session_id="s"))
        assert am.is_working() is False


class TestCancelAllInflightWork:
    def test_cancels_all_agents(self, am_module, monkeypatch):
        instances: list = []
        _make_fake_jiuwenclaw_class(monkeypatch, instances)
        am = am_module.AgentManager("aid", "sid")

        async def run():
            await am.get_agent(channel_id="c1", mode="m", session_id="s1")
            await am.get_agent(channel_id="c2", mode="m", session_id="s2")
            await am.cancel_all_inflight_work(reason="ut-reason")

        asyncio.run(run())
        for inst in instances:
            assert inst.cancel_reason == "ut-reason"
