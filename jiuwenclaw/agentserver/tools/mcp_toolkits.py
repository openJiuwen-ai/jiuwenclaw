# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""MCP toolkit aggregator for openjiuwen tools."""

from __future__ import annotations

from openjiuwen.core.foundation.tool import Tool

from jiuwenclaw.agentserver.tools.command_tools import mcp_exec_command
from jiuwenclaw.agentserver.tools.search_tools import mcp_free_search, mcp_paid_search
from jiuwenclaw.agentserver.tools.web_fetch_tools import mcp_fetch_webpage


def get_mcp_tools() -> list[Tool]:
    """Return all MCP toolkit tools for registration in Runner."""
    return [mcp_free_search, mcp_paid_search, mcp_fetch_webpage, mcp_exec_command]


__all__ = [
    "mcp_free_search",
    "mcp_paid_search",
    "mcp_fetch_webpage",
    "mcp_exec_command",
    "get_mcp_tools",
]
