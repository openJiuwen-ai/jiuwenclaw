# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import asyncio
import json
import sys
from contextvars import ContextVar
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jiuwenclaw.agentserver.tool_manager import (
    ToolManager,
    _make_stdio_params_getter,
    _REQUEST_STDIO_PARAMS,
    _validate_request_scoped_remote_mcp,
)


@pytest.fixture
def mock_agent():
    agent = MagicMock()
    agent.ability_manager = MagicMock()
    agent.ability_manager.add = MagicMock()
    agent.ability_manager.remove = MagicMock()
    agent.ability_manager._mcp_servers = {}
    return agent


@pytest.fixture
def tool_manager(mock_agent):
    tm = ToolManager(get_agent=lambda: mock_agent)
    return tm


class TestRequestStdioParamsContextVar:
    def test_default_is_none(self):
        assert _REQUEST_STDIO_PARAMS.get() is None

    def test_set_and_get_single_server(self):
        _REQUEST_STDIO_PARAMS.set({"email": {"command": "node", "args": ["a.js"]}})
        getter = _make_stdio_params_getter("email")
        assert getter() == {"command": "node", "args": ["a.js"]}
        _REQUEST_STDIO_PARAMS.set(None)

    def test_missing_server_returns_empty(self):
        _REQUEST_STDIO_PARAMS.set({"email": {"command": "node"}})
        getter = _make_stdio_params_getter("cloud_docs")
        assert getter() == {}
        _REQUEST_STDIO_PARAMS.set(None)

    def test_multiple_servers(self):
        params_map = {
            "email": {"command": "node", "args": ["email.js"]},
            "cloud_docs": {"command": "python", "args": ["-m", "cloud"]},
        }
        _REQUEST_STDIO_PARAMS.set(params_map)
        assert _make_stdio_params_getter("email")() == {"command": "node", "args": ["email.js"]}
        assert _make_stdio_params_getter("cloud_docs")() == {"command": "python", "args": ["-m", "cloud"]}
        _REQUEST_STDIO_PARAMS.set(None)

    def test_concurrent_tasks_isolated(self):
        results = {}

        async def main():
            async def task_a():
                _REQUEST_STDIO_PARAMS.set({"email": {"command": "node_a"}})
                await asyncio.sleep(0.01)
                results["a"] = _make_stdio_params_getter("email")()

            async def task_b():
                _REQUEST_STDIO_PARAMS.set({"email": {"command": "node_b"}})
                await asyncio.sleep(0.01)
                results["b"] = _make_stdio_params_getter("email")()

            await asyncio.gather(task_a(), task_b())

        asyncio.run(main())
        assert results["a"] == {"command": "node_a"}
        assert results["b"] == {"command": "node_b"}
        _REQUEST_STDIO_PARAMS.set(None)


class TestRegistrationTracking:
    def test_init_has_empty_registrations(self, tool_manager):
        assert tool_manager._request_registrations == {}

    def test_unregister_nonexistent_is_noop(self, tool_manager):
        asyncio.run(tool_manager.unregister_request_scoped_mcp("nonexistent"))
        assert tool_manager._request_registrations == {}

    def test_unregister_removes_stdio_tools(self, tool_manager, mock_agent):
        tool_manager._request_registrations["req_A"] = [
            {
                "kind": "stdio",
                "server_name": "email",
                "server_id": "email::req_A",
                "tool_ids": ["email::req_A.email.send", "email::req_A.email.read"],
                "tool_names": ["send", "read"],
            }
        ]

        with patch("jiuwenclaw.agentserver.tool_manager.Runner") as mock_runner:
            mock_runner.resource_mgr.remove_tool = MagicMock()
            asyncio.run(tool_manager.unregister_request_scoped_mcp("req_A"))

        assert "req_A" not in tool_manager._request_registrations
        assert mock_runner.resource_mgr.remove_tool.call_count == 2
        assert mock_agent.ability_manager.remove.call_count == 2

    def test_unregister_multiple_requests(self, tool_manager, mock_agent):
        tool_manager._request_registrations["req_A"] = [
            {"kind": "stdio", "server_name": "email", "tool_ids": ["t1"], "tool_names": ["n1"]}
        ]
        tool_manager._request_registrations["req_B"] = [
            {"kind": "stdio", "server_name": "docs", "tool_ids": ["t2"], "tool_names": ["n2"]}
        ]

        with patch("jiuwenclaw.agentserver.tool_manager.Runner") as mock_runner:
            mock_runner.resource_mgr.remove_tool = MagicMock()
            asyncio.run(tool_manager.unregister_request_scoped_mcp("req_A"))

        assert "req_A" not in tool_manager._request_registrations
        assert "req_B" in tool_manager._request_registrations

    def test_unregister_all(self, tool_manager):
        tool_manager._request_registrations["req_A"] = [
            {"kind": "stdio", "server_name": "email", "tool_ids": [], "tool_names": []}
        ]
        tool_manager._request_registrations["req_B"] = [
            {"kind": "stdio", "server_name": "docs", "tool_ids": [], "tool_names": []}
        ]

        with patch("jiuwenclaw.agentserver.tool_manager.Runner") as mock_runner:
            mock_runner.resource_mgr.remove_tool = MagicMock()
            asyncio.run(tool_manager.unregister_all_request_scoped_mcp())

        assert tool_manager._request_registrations == {}

    def test_unregister_handles_remove_tool_error(self, tool_manager, mock_agent):
        tool_manager._request_registrations["req_A"] = [
            {"kind": "stdio", "server_name": "email", "tool_ids": ["t1"], "tool_names": ["n1"]}
        ]

        with patch("jiuwenclaw.agentserver.tool_manager.Runner") as mock_runner:
            mock_runner.resource_mgr.remove_tool = MagicMock(side_effect=Exception("boom"))
            asyncio.run(tool_manager.unregister_request_scoped_mcp("req_A"))

        assert "req_A" not in tool_manager._request_registrations

    def test_unregister_removes_shared_mcp_server(self, tool_manager, mock_agent):
        tool_manager._request_registrations["req_A"] = [
            {
                "kind": "shared",
                "server_name": "remote_api",
                "server_id": "remote_api::req_A",
            }
        ]

        with patch("jiuwenclaw.agentserver.tool_manager.Runner") as mock_runner:
            mock_runner.resource_mgr.remove_mcp_server = AsyncMock()
            asyncio.run(tool_manager.unregister_request_scoped_mcp("req_A"))

        assert "req_A" not in tool_manager._request_registrations
        mock_runner.resource_mgr.remove_mcp_server.assert_called_once_with(
            server_id="remote_api::req_A",
            tag="remote_api",
            ignore_exception=True,
        )
        mock_agent.ability_manager.remove.assert_called_once_with("remote_api")

    def test_unregister_mixed_stdio_and_shared(self, tool_manager, mock_agent):
        tool_manager._request_registrations["req_A"] = [
            {
                "kind": "stdio",
                "server_name": "local_tool",
                "server_id": "local_tool::req_A",
                "tool_ids": ["t1"],
                "tool_names": ["n1"],
            },
            {
                "kind": "shared",
                "server_name": "remote_api",
                "server_id": "remote_api::req_A",
            },
        ]

        with patch("jiuwenclaw.agentserver.tool_manager.Runner") as mock_runner:
            mock_runner.resource_mgr.remove_tool = MagicMock()
            mock_runner.resource_mgr.remove_mcp_server = AsyncMock()
            asyncio.run(tool_manager.unregister_request_scoped_mcp("req_A"))

        assert "req_A" not in tool_manager._request_registrations
        mock_runner.resource_mgr.remove_tool.assert_called_once_with("t1")
        mock_runner.resource_mgr.remove_mcp_server.assert_called_once()
        assert mock_agent.ability_manager.remove.call_count == 2

    def test_shared_mcp_cfg_copied_not_mutated(self, tool_manager, mock_agent):
        """shared 分支拷贝 mcp_cfg 后改 server_id 不污染原对象（防御未来缓存）"""
        from jiuwenclaw.agentserver.tools.mcp_toolkits import create_mcp_tool

        shared_cfg_str = json.dumps({
            "name": "test-sse",
            "type": "sse",
            "url": "https://mcp.example.com/sse",
        })
        shared_mcp_cfg = create_mcp_tool(shared_cfg_str)
        original_server_id = shared_mcp_cfg.server_id

        captured_cfgs: list = []

        async def fake_add(agent, cfg, *, tag):
            captured_cfgs.append(cfg)

        with patch("jiuwenclaw.agentserver.tool_manager.create_mcp_tool", return_value=shared_mcp_cfg), \
             patch("jiuwenclaw.agentserver.tool_manager._add_mcp_server_and_ability", new=fake_add):
            asyncio.run(tool_manager.register_request_scoped_mcp(
                {"mcpServers": {"test-sse": {"type": "sse", "url": "https://mcp.example.com/sse"}}},
                request_id="req_A",
            ))
            asyncio.run(tool_manager.register_request_scoped_mcp(
                {"mcpServers": {"test-sse": {"type": "sse", "url": "https://mcp.example.com/sse"}}},
                request_id="req_B",
            ))

        assert shared_mcp_cfg.server_id == original_server_id
        assert captured_cfgs[0].server_id == "test-sse::req_A"
        assert captured_cfgs[1].server_id == "test-sse::req_B"
        assert captured_cfgs[0] is not shared_mcp_cfg
        assert captured_cfgs[1] is not shared_mcp_cfg
        assert captured_cfgs[0] is not captured_cfgs[1]


class TestExtractRequestMcpPayload:
    def _make_request(self, params: dict):
        req = MagicMock()
        req.params = params
        return req

    def test_new_param_takes_priority(self):
        from jiuwenclaw.agentserver.interface import JiuWenClaw
        jc = JiuWenClaw.__new__(JiuWenClaw)
        req = self._make_request({
            "request_mcp_servers": {"mcpServers": {"email": {"command": "node"}}},
            "office_claw_mcp": {"command": "old"},
        })
        payload = jc._extract_request_mcp_payload(req)
        assert "email" in payload["mcpServers"]

    def test_legacy_office_claw_mcp_wrapped(self):
        from jiuwenclaw.agentserver.interface import JiuWenClaw
        jc = JiuWenClaw.__new__(JiuWenClaw)
        req = self._make_request({
            "office_claw_mcp": {"command": "node", "args": ["email.js"]},
        })
        payload = jc._extract_request_mcp_payload(req)
        assert payload == {"mcpServers": {"office-claw": {"command": "node", "args": ["email.js"]}}}

    def test_no_mcp_params_returns_none(self):
        from jiuwenclaw.agentserver.interface import JiuWenClaw
        jc = JiuWenClaw.__new__(JiuWenClaw)
        req = self._make_request({"query": "hello"})
        assert jc._extract_request_mcp_payload(req) is None

    def test_empty_mcp_servers_returns_none(self):
        from jiuwenclaw.agentserver.interface import JiuWenClaw
        jc = JiuWenClaw.__new__(JiuWenClaw)
        req = self._make_request({"request_mcp_servers": {"mcpServers": {}}})
        assert jc._extract_request_mcp_payload(req) is None

    def test_non_dict_mcp_servers_returns_none(self):
        from jiuwenclaw.agentserver.interface import JiuWenClaw
        jc = JiuWenClaw.__new__(JiuWenClaw)
        req = self._make_request({"request_mcp_servers": "not a dict"})
        assert jc._extract_request_mcp_payload(req) is None

    def test_mcp_servers_string_returns_none(self):
        from jiuwenclaw.agentserver.interface import JiuWenClaw
        jc = JiuWenClaw.__new__(JiuWenClaw)
        req = self._make_request({"request_mcp_servers": {"mcpServers": "not a dict"}})
        assert jc._extract_request_mcp_payload(req) is None

    def test_mcp_servers_list_returns_none(self):
        from jiuwenclaw.agentserver.interface import JiuWenClaw
        jc = JiuWenClaw.__new__(JiuWenClaw)
        req = self._make_request({"request_mcp_servers": {"mcpServers": ["a", "b"]}})
        assert jc._extract_request_mcp_payload(req) is None


class TestDeprecatedWrapper:
    def test_office_claw_wrapper_delegates(self, tool_manager):
        with patch.object(tool_manager, "register_request_scoped_mcp", new_callable=AsyncMock) as mock_reg:
            mock_reg.return_value = {"registered": True}
            asyncio.run(tool_manager.register_request_scoped_office_claw_mcp(
                {"command": "node", "args": ["email.js"]}
            ))
        assert mock_reg.called
        call_kwargs = mock_reg.call_args
        payload = call_kwargs[0][0]
        assert "office-claw" in payload["mcpServers"]
        assert payload["mcpServers"]["office-claw"]["command"] == "node"
        request_id = call_kwargs[1]["request_id"]
        assert request_id.startswith("legacy_office_claw_")

    def test_legacy_wrapper_request_id_unique_under_concurrency(self, tool_manager):
        seen_ids: list[str] = []

        async def capture(payload, *, request_id):
            seen_ids.append(request_id)
            return {"registered": True}

        tool_manager.register_request_scoped_mcp = capture

        async def run():
            await asyncio.gather(*[
                tool_manager.register_request_scoped_office_claw_mcp({"command": "node"})
                for _ in range(100)
            ])

        asyncio.run(run())
        assert len(seen_ids) == 100
        assert len(set(seen_ids)) == 100


class TestCleanupRequestScopedMcp:
    def test_cleanup_delegates_to_tool_manager(self):
        from jiuwenclaw.agentserver.interface import JiuWenClaw
        jc = JiuWenClaw.__new__(JiuWenClaw)
        mock_tm = MagicMock()
        mock_tm.unregister_request_scoped_mcp = AsyncMock()
        jc._tool_manager = mock_tm

        asyncio.run(jc._cleanup_request_scoped_mcp("req_123"))
        mock_tm.unregister_request_scoped_mcp.assert_awaited_once_with("req_123")

    def test_cleanup_noop_when_tool_manager_is_none(self):
        from jiuwenclaw.agentserver.interface import JiuWenClaw
        jc = JiuWenClaw.__new__(JiuWenClaw)
        jc._tool_manager = None
        asyncio.run(jc._cleanup_request_scoped_mcp("req_123"))

    def test_cleanup_catches_exceptions(self):
        from jiuwenclaw.agentserver.interface import JiuWenClaw
        jc = JiuWenClaw.__new__(JiuWenClaw)
        mock_tm = MagicMock()
        mock_tm.unregister_request_scoped_mcp = AsyncMock(side_effect=Exception("boom"))
        jc._tool_manager = mock_tm
        asyncio.run(jc._cleanup_request_scoped_mcp("req_123"))


class TestValidateRequestScopedRemoteMcp:
    def test_external_url_allowed(self):
        _validate_request_scoped_remote_mcp("ok", {"url": "https://mcp.example.com/sse"})

    def test_loopback_ipv4_blocked(self):
        for host in ("127.0.0.1", "127.1.2.3"):
            with pytest.raises(ValueError, match="SSRF"):
                _validate_request_scoped_remote_mcp("t", {"url": f"http://{host}/sse"})

    def test_metadata_endpoint_blocked(self):
        with pytest.raises(ValueError, match="SSRF"):
            _validate_request_scoped_remote_mcp("t", {"url": "http://169.254.169.254/latest/meta-data/"})

    def test_private_network_blocked(self):
        for host in ("10.0.0.1", "192.168.1.1", "172.16.0.1"):
            with pytest.raises(ValueError, match="SSRF"):
                _validate_request_scoped_remote_mcp("t", {"url": f"http://{host}/mcp"})

    def test_localhost_blocked(self):
        with pytest.raises(ValueError, match="SSRF"):
            _validate_request_scoped_remote_mcp("t", {"url": "http://localhost:8080/sse"})

    def test_unspecified_address_blocked(self):
        with pytest.raises(ValueError, match="SSRF"):
            _validate_request_scoped_remote_mcp("t", {"url": "http://0.0.0.0/mcp"})

    def test_ipv6_loopback_blocked(self):
        with pytest.raises(ValueError, match="SSRF"):
            _validate_request_scoped_remote_mcp("t", {"url": "http://[::1]/mcp"})

    def test_no_url_allowed(self):
        _validate_request_scoped_remote_mcp("t", {"type": "sse"})

    def test_non_dict_cfg_noop(self):
        _validate_request_scoped_remote_mcp("t", "not-a-dict")

    def test_auth_headers_value_blocked(self):
        with pytest.raises(ValueError, match="凭证外泄"):
            _validate_request_scoped_remote_mcp("t", {
                "url": "https://mcp.example.com/sse",
                "auth_headers": {"X-Redirect": "http://169.254.169.254/"},
            })

    def test_auth_query_params_value_blocked(self):
        with pytest.raises(ValueError, match="凭证外泄"):
            _validate_request_scoped_remote_mcp("t", {
                "url": "https://mcp.example.com/mcp",
                "auth_query_params": {"target": "127.0.0.1"},
            })

    def test_auth_headers_external_value_allowed(self):
        _validate_request_scoped_remote_mcp("t", {
            "url": "https://mcp.example.com/sse",
            "auth_headers": {"Authorization": "Bearer valid-token"},
            "auth_query_params": {"token": "abc123"},
        })

    def test_loopback_allowed_when_env_set(self, monkeypatch):
        monkeypatch.setenv("JIUWENCLAW_ALLOW_LOOPBACK_MCP", "1")
        _validate_request_scoped_remote_mcp("t", {"url": "http://127.0.0.1:9999/sse"})
        _validate_request_scoped_remote_mcp("t", {"url": "http://localhost:8080/mcp"})
        _validate_request_scoped_remote_mcp("t", {"url": "http://[::1]/mcp"})

    def test_metadata_still_blocked_when_loopback_allowed(self, monkeypatch):
        monkeypatch.setenv("JIUWENCLAW_ALLOW_LOOPBACK_MCP", "1")
        with pytest.raises(ValueError, match="SSRF"):
            _validate_request_scoped_remote_mcp("t", {"url": "http://169.254.169.254/meta-data/"})
        with pytest.raises(ValueError, match="SSRF"):
            _validate_request_scoped_remote_mcp("t", {"url": "http://metadata.google.internal/"})

    def test_private_still_blocked_when_loopback_allowed(self, monkeypatch):
        monkeypatch.setenv("JIUWENCLAW_ALLOW_LOOPBACK_MCP", "1")
        with pytest.raises(ValueError, match="SSRF"):
            _validate_request_scoped_remote_mcp("t", {"url": "http://10.0.0.1/sse"})
        with pytest.raises(ValueError, match="SSRF"):
            _validate_request_scoped_remote_mcp("t", {"url": "http://192.168.1.1/mcp"})
