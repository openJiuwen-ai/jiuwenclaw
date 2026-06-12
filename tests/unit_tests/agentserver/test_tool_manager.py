# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""ToolManager 单元测试。

覆盖：
- 纯函数：_safe_tool_file_stem、_check_mcp_security、_tool_record_for_disk、
  _mcp_result_is_ok、_mcp_result_error_text、_is_already_exist_error、
  _path_is_under_trusted_root、_get_office_claw_stdio_params。
- 类方法：_require_agent、_unregister_ephemeral_office_claw_tools、
  handle_tools_add（正常 + 注册失败回滚）、load_tools_from_disk、
  register_request_scoped_office_claw_mcp（HTTP 分支）。
"""

from __future__ import annotations

import asyncio
import json
import sys
import types
from contextvars import copy_context
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest


# ──────────────────────────────────────────────────────────────────────────────
# 测试 fixture：注入伪造的 openjiuwen / jiuwenclaw.utils 依赖，
# 避免单元测试加载真实依赖与全局副作用。
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_tool_manager_module(monkeypatch, tmp_path):
    """每个用例独立导入 tool_manager，避免模块级全局状态串台。"""
    # 重置可能已被其它测试加载的 tool_manager 模块
    monkeypatch.delitem(sys.modules, "jiuwenclaw.agentserver.tool_manager", raising=False)

    # 提供伪造的 Runner.resource_mgr
    class _FakeResourceMgr:
        def __init__(self) -> None:
            self.added_servers: list[tuple[Any, str]] = []
            self.removed_tools: list[str] = []
            self.removed_servers: list[str] = []
            self.added_tools: list[tuple[Any, str]] = []
            self.add_result = None  # None 表示成功
            self.add_tool_result = None

        async def add_mcp_server(self, mcp_cfg, *, tag):
            self.added_servers.append((mcp_cfg, tag))
            return self.add_result

        async def remove_tool_server(self, server_id, *, ignore_not_exist=False):
            self.removed_servers.append(server_id)

        def get_mcp_server_ids(self, name):
            return []

        def add_tool(self, tool, *, tag):
            self.added_tools.append((tool, tag))
            return self.add_tool_result

        def remove_tool(self, tool_id, *, ignore_not_exist=False):
            self.removed_tools.append(tool_id)

    fake_runner_module = types.ModuleType("openjiuwen.core.runner")
    fake_runner_module.Runner = types.SimpleNamespace(resource_mgr=_FakeResourceMgr())
    monkeypatch.setitem(sys.modules, "openjiuwen.core.runner", fake_runner_module)

    # 伪造 openjiuwen.core.foundation.tool.ToolCard
    fake_tool_module = types.ModuleType("openjiuwen.core.foundation.tool")

    class _FakeToolCard:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    fake_tool_module.ToolCard = _FakeToolCard
    monkeypatch.setitem(sys.modules, "openjiuwen.core.foundation.tool", fake_tool_module)

    # 伪造 ephemeral_stdio_mcp_tool
    fake_eph = types.ModuleType("jiuwenclaw.agentserver.tools.ephemeral_stdio_mcp_tool")

    class _FakeEphemeralStdioMcpTool:
        def __init__(self, card, params_provider):
            self.card = card
            self.params_provider = params_provider

    async def _fake_list_defs(params):
        return []

    def _fake_stdio_params(params):
        return dict(params or {})

    fake_eph.EphemeralStdioMcpTool = _FakeEphemeralStdioMcpTool
    fake_eph.list_stdio_mcp_tool_defs = _fake_list_defs
    fake_eph.stdio_params_from_mcp_config = _fake_stdio_params
    monkeypatch.setitem(sys.modules, "jiuwenclaw.agentserver.tools.ephemeral_stdio_mcp_tool", fake_eph)

    # 伪造 mcp_toolkits
    fake_mcp = types.ModuleType("jiuwenclaw.agentserver.tools.mcp_toolkits")

    def _normalize_kind(cmd: str) -> str:
        cmd = (cmd or "").lower()
        if "python" in cmd:
            return "python"
        if "node" in cmd:
            return "node"
        return ""

    def _create_mcp_tool(single_json: str):
        record = json.loads(single_json)
        name = record.get("name") or "unknown"
        return types.SimpleNamespace(
            server_name=name,
            server_id=record.get("server_id") or f"id-{name}",
            client_type=record.get("type") or "http",
            params=record,
        )

    fake_mcp._normalize_stdio_command_kind = _normalize_kind
    fake_mcp.create_mcp_tool = _create_mcp_tool
    monkeypatch.setitem(sys.modules, "jiuwenclaw.agentserver.tools.mcp_toolkits", fake_mcp)

    # 伪造 jiuwenclaw.utils（仅提供 get_agent_tools_dir / logger）
    if "jiuwenclaw.utils" not in sys.modules:
        fake_utils = types.ModuleType("jiuwenclaw.utils")
        fake_utils.get_agent_tools_dir = lambda: tmp_path / "tools"
        import logging
        fake_utils.logger = logging.getLogger("test.tool_manager")
        monkeypatch.setitem(sys.modules, "jiuwenclaw.utils", fake_utils)
    else:
        monkeypatch.setattr(
            "jiuwenclaw.utils.get_agent_tools_dir",
            lambda: tmp_path / "tools",
            raising=False,
        )

    yield


@pytest.fixture()
def tm_module():
    """按需导入 tool_manager 模块（依赖 _isolate_tool_manager_module 已生效）。"""
    import importlib
    import logging
    import jiuwenclaw.agentserver.tool_manager as mod
    mod = importlib.reload(mod)
    # 替换 logger，避免触发真实日志过滤器（其内部会动态加载扩展模块）
    clean_logger = logging.getLogger("test.tool_manager.isolated")
    clean_logger.handlers.clear()
    clean_logger.propagate = False
    mod.logger = clean_logger
    return mod


def _make_fake_agent() -> Any:
    """构造带 ability_manager 的伪 agent。"""
    ability_manager = MagicMock()
    ability_manager._mcp_servers = {}
    return types.SimpleNamespace(ability_manager=ability_manager)


# ──────────────────────────────────────────────────────────────────────────────
# 纯函数测试
# ──────────────────────────────────────────────────────────────────────────────


class TestSafeToolFileStem:
    def test_normal_name(self, tm_module):
        assert tm_module._safe_tool_file_stem("my-tool") == "my-tool"

    def test_name_with_special_chars_sanitized(self, tm_module):
        assert tm_module._safe_tool_file_stem("my tool@v1") == "my_tool_v1"

    def test_empty_name_raises(self, tm_module):
        with pytest.raises(ValueError):
            tm_module._safe_tool_file_stem("")

    def test_whitespace_only_raises(self, tm_module):
        with pytest.raises(ValueError):
            tm_module._safe_tool_file_stem("   ")

    @pytest.mark.parametrize("evil", ["..", "../etc", "a/b", "a\\b", "____"])
    def test_path_traversal_or_no_alnum_raises(self, tm_module, evil):
        with pytest.raises(ValueError):
            tm_module._safe_tool_file_stem(evil)


class TestCheckMcpSecurity:
    @pytest.mark.parametrize("blocked_key", [
        "command", "args", "cwd", "env", "url", "auth_headers", "auth_query_params",
    ])
    def test_blocks_dangerous_keys(self, tm_module, blocked_key):
        with pytest.raises(ValueError) as ei:
            tm_module._check_mcp_security("evil_tool", {blocked_key: "any"})
        assert "安全拦截阻断" in str(ei.value)
        assert "evil_tool" in str(ei.value)

    def test_allows_safe_config(self, tm_module):
        # 不抛异常即视为通过
        tm_module._check_mcp_security("safe_tool", {"description": "ok", "type": "http"})

    def test_non_dict_silently_passes(self, tm_module):
        # 函数对非 dict 输入不做拦截（由上游校验）；
        # 故意传入 str 验证容错分支，需抑制 mypy arg-type。
        tm_module._check_mcp_security("x", "not a dict")  # type: ignore[arg-type]


class TestIsAlreadyExistError:
    @pytest.mark.parametrize("msg,expected", [
        ("Resource already exist", True),
        ("ALREADY EXIST in registry", True),
        ("resource already-exist", False),  # 中间多了连字符
        ("not found", False),
        ("", False),
        (None, False),  # 容错 None
    ])
    def test_classification(self, tm_module, msg, expected):
        assert tm_module._is_already_exist_error(msg) is expected


class TestMcpResultIsOk:
    def test_none_is_ok(self, tm_module):
        assert tm_module._mcp_result_is_ok(None) is True

    def test_ok_via_callable(self, tm_module):
        result = types.SimpleNamespace(is_ok=lambda: True)
        assert tm_module._mcp_result_is_ok(result) is True

    def test_not_ok(self, tm_module):
        result = types.SimpleNamespace(is_ok=lambda: False)
        assert tm_module._mcp_result_is_ok(result) is False

    def test_missing_is_ok(self, tm_module):
        result = types.SimpleNamespace()
        assert tm_module._mcp_result_is_ok(result) is False

    def test_is_ok_raises_logs_and_returns_false(self, tm_module):
        def _raise():
            raise RuntimeError("boom")
        result = types.SimpleNamespace(is_ok=_raise)
        assert tm_module._mcp_result_is_ok(result) is False


class TestMcpResultErrorText:
    def test_none_returns_empty(self, tm_module):
        assert tm_module._mcp_result_error_text(None) == ""

    def test_error_callable_first_priority(self, tm_module):
        result = types.SimpleNamespace(
            error=lambda: "err msg",
            msg=lambda: "msg msg",
        )
        assert tm_module._mcp_result_error_text(result) == "err msg"

    def test_falls_back_to_msg(self, tm_module):
        result = types.SimpleNamespace(msg=lambda: "msg only")
        assert tm_module._mcp_result_error_text(result) == "msg only"

    def test_falls_back_to_private_error_attr(self, tm_module):
        result = types.SimpleNamespace(_error="raw")
        assert tm_module._mcp_result_error_text(result) == "raw"

    def test_falls_back_to_str(self, tm_module):
        assert tm_module._mcp_result_error_text("plain string") == "plain string"

    def test_callable_raise_then_continue(self, tm_module):
        def _raise():
            raise RuntimeError("x")
        result = types.SimpleNamespace(error=_raise, msg=lambda: "ok msg")
        # error() 抛异常后应继续尝试 msg()
        assert tm_module._mcp_result_error_text(result) == "ok msg"


class TestToolRecordForDisk:
    def test_uses_mcpservers_key_as_name(self, tm_module):
        record = tm_module._tool_record_for_disk("my-tool", {"name": "ignored"})
        assert record["name"] == "my-tool"

    def test_default_values_for_missing_fields(self, tm_module):
        record = tm_module._tool_record_for_disk("t1", {})
        assert record["description"] == ""
        assert record["url"] == ""
        assert record["env"] == {}
        assert record["args"] == []

    def test_extra_keys_are_preserved(self, tm_module):
        record = tm_module._tool_record_for_disk("t1", {"extra_field": 123})
        assert record["extra_field"] == 123

    def test_text_field_coerces_non_string(self, tm_module):
        record = tm_module._tool_record_for_disk("t1", {"description": 42})
        assert record["description"] == "42"

    def test_list_field_falls_back_to_default_when_not_list(self, tm_module):
        record = tm_module._tool_record_for_disk("t1", {"args": "not-a-list"})
        assert record["args"] == []  # default 的拷贝

    def test_mutable_default_is_copied(self, tm_module):
        r1 = tm_module._tool_record_for_disk("t1", {})
        r2 = tm_module._tool_record_for_disk("t2", {})
        r1["env"]["FOO"] = "1"
        # 默认值不应被污染
        assert r2["env"] == {}


class TestPathIsUnderTrustedRoot:
    def test_under_root(self, tm_module, tmp_path):
        root = tmp_path / "root"
        root.mkdir()
        sub = root / "child.txt"
        sub.write_text("x")
        assert tm_module._path_is_under_trusted_root(sub, [root]) is True

    def test_outside_root(self, tm_module, tmp_path):
        root = tmp_path / "root"
        root.mkdir()
        other = tmp_path / "other.txt"
        other.write_text("x")
        assert tm_module._path_is_under_trusted_root(other, [root]) is False

    def test_no_roots_returns_false(self, tm_module, tmp_path):
        f = tmp_path / "f"
        f.write_text("x")
        assert tm_module._path_is_under_trusted_root(f, []) is False


class TestGetOfficeClawStdioParams:
    def test_unset_context_returns_empty_dict(self, tm_module):
        # 在新 context 中读取未设置的 ContextVar
        def _runner():
            return tm_module._get_office_claw_stdio_params()
        ctx = copy_context()
        result = ctx.run(_runner)
        assert result == {}

    def test_unset_returns_new_dict_each_call(self, tm_module):
        # 验证不共享同一可变默认对象
        def _runner_a():
            d = tm_module._get_office_claw_stdio_params()
            d["leak"] = 1
            return d
        def _runner_b():
            return tm_module._get_office_claw_stdio_params()
        copy_context().run(_runner_a)
        result = copy_context().run(_runner_b)
        assert result == {}

    def test_set_returns_value(self, tm_module):
        def _runner():
            tm_module._OFFICE_CLAW_STDIO_PARAMS.set({"k": "v"})
            return tm_module._get_office_claw_stdio_params()
        result = copy_context().run(_runner)
        assert result == {"k": "v"}


class TestBuildRegisteredPayload:
    def test_payload_shape(self, tm_module):
        p = tm_module._build_registered_payload("name1", "id1")
        assert p == {"registered": True, "name": "name1", "server_id": "id1"}


# ──────────────────────────────────────────────────────────────────────────────
# ToolManager 类方法测试
# ──────────────────────────────────────────────────────────────────────────────


class TestRequireAgent:
    def test_returns_agent(self, tm_module):
        agent = _make_fake_agent()
        tm = tm_module.ToolManager(get_agent=lambda: agent)
        assert tm._require_agent() is agent

    def test_raises_when_agent_none(self, tm_module):
        tm = tm_module.ToolManager(get_agent=lambda: None)
        with pytest.raises(RuntimeError, match="未初始化"):
            tm._require_agent()

    def test_raises_when_no_get_agent(self, tm_module):
        tm = tm_module.ToolManager(get_agent=None)
        with pytest.raises(RuntimeError):
            tm._require_agent()


class TestUnregisterEphemeralOfficeClawTools:
    def test_no_op_when_empty(self, tm_module):
        agent = _make_fake_agent()
        tm = tm_module.ToolManager(get_agent=lambda: agent)
        # 不应抛异常
        asyncio.run(tm._unregister_ephemeral_office_claw_tools(agent))
        assert tm._office_claw_ephemeral_tools == []

    def test_clears_list_and_calls_remove_tool(self, tm_module):
        from openjiuwen.core.runner import Runner

        agent = _make_fake_agent()
        tm = tm_module.ToolManager(get_agent=lambda: agent)
        tm._office_claw_ephemeral_tools = [("id-1", "tool-1"), ("id-2", "tool-2")]

        asyncio.run(tm._unregister_ephemeral_office_claw_tools(agent))

        assert tm._office_claw_ephemeral_tools == []
        assert Runner.resource_mgr.removed_tools == ["id-1", "id-2"]
        assert agent.ability_manager.remove.call_count == 2


class TestHandleToolsAddValidation:
    def test_missing_mcp_json(self, tm_module):
        tm = tm_module.ToolManager(get_agent=_make_fake_agent)
        with pytest.raises(ValueError, match="mcp_json"):
            asyncio.run(tm.handle_tools_add({}))

    def test_invalid_json(self, tm_module):
        tm = tm_module.ToolManager(get_agent=_make_fake_agent)
        with pytest.raises(ValueError, match="JSON 解析失败"):
            asyncio.run(tm.handle_tools_add({"mcp_json": "not-json"}))

    def test_missing_mcpservers(self, tm_module):
        tm = tm_module.ToolManager(get_agent=_make_fake_agent)
        with pytest.raises(ValueError, match="mcpServers"):
            asyncio.run(tm.handle_tools_add({"mcp_json": "{}"}))

    def test_blocks_dangerous_via_rpc(self, tm_module):
        tm = tm_module.ToolManager(get_agent=_make_fake_agent)
        body = {"mcpServers": {"evil": {"command": "/bin/sh"}}}
        with pytest.raises(ValueError, match="安全拦截阻断"):
            asyncio.run(tm.handle_tools_add({"mcp_json": json.dumps(body)}, source="rpc"))

    def test_local_source_allows_dangerous(self, tm_module, tmp_path):
        agent = _make_fake_agent()
        tools_dir = tmp_path / "tools"
        tm = tm_module.ToolManager(
            get_agent=lambda: agent,
            get_tools_dir=lambda: tools_dir,
        )
        body = {"mcpServers": {"safe_local": {"type": "http", "url": "http://x"}}}
        result = asyncio.run(tm.handle_tools_add({"mcp_json": json.dumps(body)}, source="local"))
        assert len(result["registered_tools"]) == 1
        # 文件已落盘
        assert (tools_dir / "safe_local.json").exists()


class TestHandleToolsAddRollback:
    def test_rollback_on_registration_failure(self, tm_module, tmp_path):
        """注册失败时应回滚落盘文件并从 saved 中移除。"""
        from openjiuwen.core.runner import Runner

        agent = _make_fake_agent()
        tools_dir = tmp_path / "tools"

        # 让 add_mcp_server 返回失败结果
        Runner.resource_mgr.add_result = types.SimpleNamespace(
            is_ok=lambda: False,
            error=lambda: "internal failure",
        )

        tm = tm_module.ToolManager(
            get_agent=lambda: agent,
            get_tools_dir=lambda: tools_dir,
        )
        body = {"mcpServers": {"bad_tool": {"type": "http"}}}

        with pytest.raises(RuntimeError):
            asyncio.run(tm.handle_tools_add({"mcp_json": json.dumps(body)}, source="local"))

        # 文件应被回滚（不存在）
        assert not (tools_dir / "bad_tool.json").exists()

    def test_success_keeps_file(self, tm_module, tmp_path):
        agent = _make_fake_agent()
        tools_dir = tmp_path / "tools"
        tm = tm_module.ToolManager(
            get_agent=lambda: agent,
            get_tools_dir=lambda: tools_dir,
        )
        body = {"mcpServers": {"ok_tool": {"type": "http", "description": "desc"}}}
        result = asyncio.run(tm.handle_tools_add({"mcp_json": json.dumps(body)}, source="local"))

        assert (tools_dir / "ok_tool.json").exists()
        assert result["registered_tools"] == [{"name": "ok_tool", "id": "id-ok_tool"}]
        assert len(result["saved"]) == 1
        # ability_manager.add 被调用
        assert agent.ability_manager.add.call_count == 1


class TestLoadToolsFromDisk:
    def test_scans_and_registers(self, tm_module, tmp_path):
        agent = _make_fake_agent()
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        # 两个工具配置
        (tools_dir / "a.json").write_text(
            json.dumps({"name": "a", "type": "http", "url": "http://a"}),
            encoding="utf-8",
        )
        (tools_dir / "b.json").write_text(
            json.dumps({"name": "b", "type": "http", "url": "http://b"}),
            encoding="utf-8",
        )

        tm = tm_module.ToolManager(get_agent=lambda: agent, get_tools_dir=lambda: tools_dir)
        result = asyncio.run(tm.load_tools_from_disk())

        names = {item["name"] for item in result["registered_tools"]}
        assert names == {"a", "b"}
        assert result["errors"] == []

    def test_skip_server_names(self, tm_module, tmp_path):
        agent = _make_fake_agent()
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        (tools_dir / "skip.json").write_text(
            json.dumps({"name": "skip", "type": "http"}),
            encoding="utf-8",
        )
        (tools_dir / "keep.json").write_text(
            json.dumps({"name": "keep", "type": "http"}),
            encoding="utf-8",
        )

        tm = tm_module.ToolManager(get_agent=lambda: agent, get_tools_dir=lambda: tools_dir)
        result = asyncio.run(tm.load_tools_from_disk(skip_server_names={"skip"}))
        names = {item["name"] for item in result["registered_tools"]}
        assert names == {"keep"}

    def test_invalid_json_is_reported_but_continues(self, tm_module, tmp_path):
        agent = _make_fake_agent()
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        (tools_dir / "bad.json").write_text("not-json", encoding="utf-8")
        (tools_dir / "good.json").write_text(
            json.dumps({"name": "good", "type": "http"}),
            encoding="utf-8",
        )

        tm = tm_module.ToolManager(get_agent=lambda: agent, get_tools_dir=lambda: tools_dir)
        result = asyncio.run(tm.load_tools_from_disk())
        names = [item["name"] for item in result["registered_tools"]]
        assert names == ["good"]
        assert len(result["errors"]) == 1
        assert "bad.json" in result["errors"][0]["path"]


class TestRegisterRequestScopedOfficeClawMcp:
    def test_invalid_cfg_type(self, tm_module):
        tm = tm_module.ToolManager(get_agent=_make_fake_agent)
        with pytest.raises(ValueError, match="office_claw_mcp"):
            # 故意传入 str 验证 ValueError 校验分支，需抑制 mypy arg-type。
            asyncio.run(tm.register_request_scoped_office_claw_mcp("not-dict"))  # type: ignore[arg-type]

    def test_http_branch_registers_and_returns_payload(self, tm_module):
        agent = _make_fake_agent()
        tm = tm_module.ToolManager(get_agent=lambda: agent)

        result = asyncio.run(
            tm.register_request_scoped_office_claw_mcp({"type": "http", "url": "http://x"})
        )

        assert result["registered"] is True
        assert result["name"] == "office-claw"
        assert result["server_id"] == "office-claw-request"
        # ability_manager.add 应被调用一次
        assert agent.ability_manager.add.call_count == 1

    def test_cleans_existing_ephemeral_tools_before_registering(self, tm_module):
        from openjiuwen.core.runner import Runner

        agent = _make_fake_agent()
        tm = tm_module.ToolManager(get_agent=lambda: agent)
        # 模拟上一次注册留下的 ephemeral 工具
        tm._office_claw_ephemeral_tools = [("old-id", "old-tool")]

        asyncio.run(
            tm.register_request_scoped_office_claw_mcp({"type": "http", "url": "http://x"})
        )

        # 旧 ephemeral 工具应被清理
        assert "old-id" in Runner.resource_mgr.removed_tools
        # 列表应被清空（HTTP 分支不再 append）
        assert tm._office_claw_ephemeral_tools == []


class TestFindHostProjectMcpJson:
    def test_returns_none_when_env_unset(self, tm_module, monkeypatch):
        monkeypatch.delenv("CAT_CAFE_MCP_CWD", raising=False)
        assert tm_module.ToolManager.find_host_project_mcp_json() is None

    def test_returns_none_when_file_missing(self, tm_module, tmp_path, monkeypatch):
        monkeypatch.setenv("CAT_CAFE_MCP_CWD", str(tmp_path))
        assert tm_module.ToolManager.find_host_project_mcp_json() is None

    def test_returns_path_when_file_exists(self, tm_module, tmp_path, monkeypatch):
        monkeypatch.setenv("CAT_CAFE_MCP_CWD", str(tmp_path))
        mcp = tmp_path / ".mcp.json"
        mcp.write_text("{}", encoding="utf-8")
        result = tm_module.ToolManager.find_host_project_mcp_json()
        assert result is not None
        assert result.name == ".mcp.json"


class TestLoadProjectMcpJson:
    def test_returns_skipped_when_not_found(self, tm_module, tmp_path):
        tm = tm_module.ToolManager(get_agent=_make_fake_agent)
        result = asyncio.run(tm.load_project_mcp_json(tmp_path / "nope.json"))
        assert result["skipped"] is True
        assert result["reason"] == "not_found"

    def test_imports_and_calls_handle_tools_add(self, tm_module, tmp_path):
        agent = _make_fake_agent()
        tools_dir = tmp_path / "tools"
        mcp_file = tmp_path / ".mcp.json"
        mcp_file.write_text(
            json.dumps({"mcpServers": {"proj": {"type": "http", "url": "http://p"}}}),
            encoding="utf-8",
        )
        tm = tm_module.ToolManager(
            get_agent=lambda: agent,
            get_tools_dir=lambda: tools_dir,
        )
        result = asyncio.run(tm.load_project_mcp_json(mcp_file))
        assert result["skipped"] is False
        assert result["source"] == str(mcp_file.resolve())
        names = {item["name"] for item in result["registered_tools"]}
        assert names == {"proj"}
