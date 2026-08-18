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
    _validate_cat_cafe_request_scoped_stdio,
    _validate_request_scoped_remote_mcp,
)
from jiuwenclaw.agentserver.tools.mcp_toolkits import (
    _normalize_stdio_command_kind,
    create_mcp_tool,
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

    def test_two_stdio_servers_same_tool_name_get_qualified_names(
        self, tool_manager, mock_agent
    ):
        """两个 stdio MCP 暴露同名工具时，agent.ability_manager.add 收到带 server 前缀的
        qualified card，resource_mgr.add_tool 收到带 raw_tool_name 的 EphemeralStdioMcpTool。
        """
        from openjiuwen.core.foundation.tool import ToolCard

        from jiuwenclaw.agentserver.tools.ephemeral_stdio_mcp_tool import (
            EphemeralStdioMcpTool,
        )

        captured_cards: list = []
        captured_ephemeral: list = []

        def fake_add_tool(tool, *, tag):
            captured_ephemeral.append((tool, tag))
            return None  # 视为成功

        async def fake_list_stdio(params):
            return [
                {"name": "execute_sql", "description": "Run SQL", "input_params": {}},
                {"name": "list_tables", "description": "List tables", "input_params": {}},
            ]

        def fake_eph_class(card, getter, *, raw_tool_name=None):
            # 断言构造时确实收到了 raw_tool_name
            assert raw_tool_name is not None, "raw_tool_name must be passed"
            inst = MagicMock(spec=EphemeralStdioMcpTool)
            inst._card = card
            inst._raw_tool_name = raw_tool_name
            return inst

        with patch("jiuwenclaw.agentserver.tool_manager.list_stdio_mcp_tool_defs",
                   new=fake_list_stdio), \
             patch("jiuwenclaw.agentserver.tool_manager.EphemeralStdioMcpTool",
                   new=fake_eph_class), \
             patch("jiuwenclaw.agentserver.tool_manager.Runner") as mock_runner:
            mock_runner.resource_mgr.add_tool = fake_add_tool

            def fake_add(card):
                captured_cards.append(card)

            mock_agent.ability_manager.add = fake_add

            result = asyncio.run(tool_manager.register_request_scoped_mcp(
                {
                    "mcpServers": {
                        "orders-3": {"command": "npx", "args": ["-y", "supabase"]},
                        "members": {"command": "npx", "args": ["-y", "supabase"]},
                    }
                },
                request_id="req_X",
            ))

        # 验证：每个 server 都成功注册（不抛错），且工具数 = 2 server * 2 tool
        assert len(result["tools"]) == 2
        assert len(captured_cards) == 4
        assert len(captured_ephemeral) == 4

        names_added_to_ability = [c.name for c in captured_cards]
        ids_added_to_ability = [c.id for c in captured_cards]

        # 关键断言 1：qualified name 出现且不重复
        assert "orders-3__execute_sql" in names_added_to_ability
        assert "members__execute_sql" in names_added_to_ability
        assert "orders-3__list_tables" in names_added_to_ability
        assert "members__list_tables" in names_added_to_ability
        assert len(set(names_added_to_ability)) == 4, names_added_to_ability

        # 关键断言 2：card.id 仍是全限定形式，资源侧不会撞 key
        assert "orders-3::req_X.orders-3.execute_sql" in ids_added_to_ability
        assert "members::req_X.members.execute_sql" in ids_added_to_ability

        # 关键断言 3：EphemeralStdioMcpTool 收到的是 raw name（去前缀后）
        for tool, _tag in captured_ephemeral:
            assert tool._raw_tool_name in {"execute_sql", "list_tables"}
            # card.name 是 qualified 形式
            assert tool._card.name.startswith(("orders-3__", "members__"))

    def test_unregister_stdio_uses_qualified_name(self, tool_manager, mock_agent):
        """unregister 路径按 qualified name 调 ability_manager.remove，与 add 时一致。"""
        tool_manager._request_registrations["req_X"] = [
            {
                "kind": "stdio",
                "server_name": "orders-3",
                "server_id": "orders-3::req_X",
                "tool_ids": [
                    "orders-3::req_X.orders-3.execute_sql",
                    "orders-3::req_X.orders-3.list_tables",
                ],
                "tool_names": ["orders-3__execute_sql", "orders-3__list_tables"],
            },
            {
                "kind": "stdio",
                "server_name": "members",
                "server_id": "members::req_X",
                "tool_ids": [
                    "members::req_X.members.execute_sql",
                ],
                "tool_names": ["members__execute_sql"],
            },
        ]

        with patch("jiuwenclaw.agentserver.tool_manager.Runner") as mock_runner:
            mock_runner.resource_mgr.remove_tool = MagicMock()
            asyncio.run(tool_manager.unregister_request_scoped_mcp("req_X"))

        removed_names = [c.args[0] for c in mock_agent.ability_manager.remove.call_args_list]
        assert removed_names == [
            "orders-3__execute_sql",
            "orders-3__list_tables",
            "members__execute_sql",
        ]
        assert "req_X" not in tool_manager._request_registrations


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


class TestNormalizeStdioCommandKind:
    def test_node_bare(self):
        assert _normalize_stdio_command_kind("node") == "node"

    def test_node_exe(self):
        assert _normalize_stdio_command_kind("node.exe") == "node"

    def test_node_absolute_path(self):
        assert _normalize_stdio_command_kind("/usr/local/bin/node") == "node"
        assert _normalize_stdio_command_kind(r"C:\Program Files\nodejs\node.exe") == "node"

    def test_python_variants(self):
        for cmd in ("python", "python3", "python.exe", "python3.11"):
            assert _normalize_stdio_command_kind(cmd) == "python", cmd

    def test_npx_bare(self):
        assert _normalize_stdio_command_kind("npx") == "npx"

    def test_npx_exe(self):
        assert _normalize_stdio_command_kind("npx.exe") == "npx"

    def test_npx_absolute_path(self):
        assert _normalize_stdio_command_kind("/usr/local/bin/npx") == "npx"
        assert _normalize_stdio_command_kind(r"C:\Users\me\AppData\Roaming\npm\npx.cmd") == "npx"

    def test_uvx_bare(self):
        assert _normalize_stdio_command_kind("uvx") == "uvx"

    def test_uvx_exe(self):
        assert _normalize_stdio_command_kind("uvx.exe") == "uvx"

    def test_uvx_absolute_path(self):
        assert _normalize_stdio_command_kind("/home/u/.local/bin/uvx") == "uvx"

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="缺少 'command'"):
            _normalize_stdio_command_kind("")

    def test_empty_after_strip_raises(self):
        with pytest.raises(ValueError, match="缺少 'command'"):
            _normalize_stdio_command_kind("   ")

    def test_unsupported_raises(self):
        for cmd in ("bash", "sh", "ruby", "cmd", "powershell", "deno"):
            with pytest.raises(ValueError, match="不支持的 command 类型"):
                _normalize_stdio_command_kind(cmd)


class TestCreateMcpToolStdio:
    def _make_stdio_config(self, command: str, args: list, name: str = "t", **extra):
        cfg = {"name": name, "command": command, "args": args}
        cfg.update(extra)
        return json.dumps(cfg)

    def test_npx_config(self):
        cfg_str = self._make_stdio_config(
            "npx", ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
        )
        mcp_cfg = create_mcp_tool(cfg_str)
        assert mcp_cfg.client_type == "stdio"
        assert mcp_cfg.params["command"] == "npx"
        assert mcp_cfg.params["args"] == ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]

    def test_uvx_config(self):
        cfg_str = self._make_stdio_config("uvx", ["mcp-server-fetch"])
        mcp_cfg = create_mcp_tool(cfg_str)
        assert mcp_cfg.client_type == "stdio"
        assert mcp_cfg.params["command"] == "uvx"
        assert mcp_cfg.params["args"] == ["mcp-server-fetch"]

    def test_npx_with_env_and_cwd(self):
        cfg_str = self._make_stdio_config(
            "npx", ["-y", "@scope/pkg"],
            env={"API_KEY": "secret"},
            cwd="/some/dir",
        )
        mcp_cfg = create_mcp_tool(cfg_str)
        assert mcp_cfg.params["env"] == {"API_KEY": "secret"}
        assert mcp_cfg.params["cwd"] == "/some/dir"

    def test_npx_absolute_path(self):
        cfg_str = self._make_stdio_config(
            "/usr/local/bin/npx", ["-y", "pkg"]
        )
        mcp_cfg = create_mcp_tool(cfg_str)
        assert mcp_cfg.params["command"] == "/usr/local/bin/npx"

    def test_uvx_absolute_path(self):
        cfg_str = self._make_stdio_config(
            r"C:\Users\me\.local\bin\uvx.exe", ["pkg"]
        )
        mcp_cfg = create_mcp_tool(cfg_str)
        assert mcp_cfg.params["command"] == r"C:\Users\me\.local\bin\uvx.exe"

    def test_unsupported_command_raises(self):
        cfg_str = self._make_stdio_config("bash", ["--version"])
        with pytest.raises(ValueError, match="不支持的 command 类型"):
            create_mcp_tool(cfg_str)

    def test_npx_dangerous_eval_arg_blocked(self):
        cfg_str = self._make_stdio_config("npx", ["-e", "code"])
        with pytest.raises(ValueError, match="危险标志"):
            create_mcp_tool(cfg_str)

    def test_uvx_dangerous_command_arg_blocked(self):
        cfg_str = self._make_stdio_config("uvx", ["-c", "print(1)"])
        with pytest.raises(ValueError, match="危险标志"):
            create_mcp_tool(cfg_str)


class TestValidateCatCafeRequestScopedStdio:
    def test_npx_skips_path_check(self):
        _validate_cat_cafe_request_scoped_stdio({
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "/etc", "/tmp"],
        })

    def test_uvx_skips_path_check(self):
        _validate_cat_cafe_request_scoped_stdio({
            "command": "uvx",
            "args": ["mcp-server-fetch", "/var/data"],
        })

    def test_npx_untrusted_cwd_allowed(self):
        _validate_cat_cafe_request_scoped_stdio({
            "command": "npx",
            "args": ["-y", "pkg"],
            "cwd": "/untrusted/random/dir",
        })

    def test_npx_dangerous_command_arg_still_blocked(self):
        with pytest.raises(ValueError, match="python -c"):
            _validate_cat_cafe_request_scoped_stdio({
                "command": "npx",
                "args": ["-c", "code"],
            })

    def test_uvx_dangerous_command_arg_still_blocked(self):
        with pytest.raises(ValueError, match="python -c"):
            _validate_cat_cafe_request_scoped_stdio({
                "command": "uvx",
                "args": ["--command", "code"],
            })

    def test_node_path_check_still_active(self):
        with pytest.raises(ValueError, match="受信根"):
            _validate_cat_cafe_request_scoped_stdio({
                "command": "node",
                "args": ["/untrusted/script.js"],
            })

    def test_python_path_check_still_active(self):
        with pytest.raises(ValueError, match="受信根"):
            _validate_cat_cafe_request_scoped_stdio({
                "command": "python",
                "args": ["/untrusted/script.py"],
            })

    def test_node_eval_still_blocked(self):
        with pytest.raises(ValueError, match="node -e"):
            _validate_cat_cafe_request_scoped_stdio({
                "command": "node",
                "args": ["-e", "console.log(1)"],
            })

    def test_args_not_list_raises(self):
        with pytest.raises(ValueError, match="args 须为列表"):
            _validate_cat_cafe_request_scoped_stdio({
                "command": "npx",
                "args": "not-a-list",
            })
