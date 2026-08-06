# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import pytest

from jiuwenclaw.agentserver.permissions.config_rpc import dispatch_permissions_config_request
from jiuwenclaw.config import (
    build_permissions_tools_list_view,
    get_permissions_defaults_level,
    normalize_permissions_tool_level,
)
from jiuwenclaw.schema.agent import AgentRequest
from jiuwenclaw.schema.message import ReqMethod


def test_normalize_permissions_tool_level_maps_guard_to_ask() -> None:
    assert normalize_permissions_tool_level("guard") == "ask"
    assert normalize_permissions_tool_level("ASK") == "ask"
    assert normalize_permissions_tool_level("allow") == "allow"
    assert normalize_permissions_tool_level({"*": "deny"}) == "deny"
    assert normalize_permissions_tool_level("invalid") is None


def test_get_permissions_defaults_level(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "jiuwenclaw.config.get_config",
        lambda: {"permissions": {"defaults": "allow"}},
    )
    assert get_permissions_defaults_level() == "allow"

    monkeypatch.setattr(
        "jiuwenclaw.config.get_config",
        lambda: {"permissions": {"defaults": "guard"}},
    )
    assert get_permissions_defaults_level() == "ask"


def test_build_permissions_tools_list_view_merges_catalog_and_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "jiuwenclaw.config.get_config",
        lambda: {
            "permissions": {
                "defaults": "ask",
                "tools": {
                    "bash": "deny",
                    "manual_only": "allow",
                },
            }
        },
    )
    catalog = {
        "bash": {
            "name": "bash",
            "description": "Run shell commands.",
            "short_description": "Run shell commands.",
        },
        "read_file": {
            "name": "read_file",
            "description": "Read a file from disk.",
            "short_description": "Read a file from disk.",
        },
    }

    payload = build_permissions_tools_list_view(catalog)

    assert payload["default_level"] == "ask"
    by_name = {item["name"]: item for item in payload["tools"]}
    assert set(by_name) == {"bash", "read_file", "manual_only"}
    assert by_name["bash"] == {
        "name": "bash",
        "short_description": "Run shell commands.",
        "level": "deny",
        "configured": True,
        "registered": True,
    }
    assert by_name["read_file"]["level"] == "ask"
    assert by_name["read_file"]["configured"] is False
    assert by_name["read_file"]["registered"] is True
    # config 独有键（catalog 里没有、但 permissions.tools 里配过）现在也会暴露，
    # 以便用户为按需注册的工具（如 agent-team 工具）预配审批档位。
    assert by_name["manual_only"]["name"] == "manual_only"
    assert by_name["manual_only"]["level"] == "allow"
    assert by_name["manual_only"]["configured"] is True
    assert by_name["manual_only"]["registered"] is False


def test_build_permissions_tools_list_view_strips_placeholder_short_description(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "jiuwenclaw.config.get_config",
        lambda: {"permissions": {"defaults": "ask", "tools": {}}},
    )
    payload = build_permissions_tools_list_view(
        {
            "bash": {
                "name": "bash",
                "description": "",
                "short_description": "工具「bash」（暂无简短说明）",
            }
        }
    )
    assert payload["tools"][0]["short_description"] == ""


def test_permissions_tools_list_rpc(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "jiuwenclaw.config.get_config",
        lambda: {"permissions": {"defaults": "ask", "tools": {"bash": "allow"}}},
    )
    request = AgentRequest(
        request_id="r1",
        channel_id="web",
        session_id="default",
        req_method=ReqMethod.PERMISSIONS_TOOLS_LIST,
        params={},
    )
    resp = dispatch_permissions_config_request(
        request,
        get_runtime_tools_catalog=lambda: {
            "bash": {
                "name": "bash",
                "description": "Run shell.",
                "short_description": "Run shell.",
            }
        },
    )
    assert resp.ok is True
    assert resp.payload["default_level"] == "ask"
    assert resp.payload["tools"][0]["name"] == "bash"
    assert resp.payload["tools"][0]["level"] == "allow"
    assert resp.payload["tools"][0]["configured"] is True
