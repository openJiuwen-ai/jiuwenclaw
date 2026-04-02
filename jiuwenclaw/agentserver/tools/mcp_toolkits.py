# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""MCP toolkit aggregator for openjiuwen tools."""

from __future__ import annotations
import json
import os

from openjiuwen.core.foundation.tool import Tool, McpServerConfig
from openjiuwen.core.foundation.tool.mcp.base import MCPTool, McpToolCard
from openjiuwen.core.foundation.tool.mcp.client.stdio_client import StdioClient

from jiuwenclaw.agentserver.tools.command_tools import mcp_exec_command
from jiuwenclaw.agentserver.tools.search_tools import mcp_free_search, mcp_paid_search
from jiuwenclaw.agentserver.tools.web_fetch_tools import mcp_fetch_webpage


async def create_mcp_tool(config_str: str) -> Tool:
    """从 JSON 字符串创建 MCPTool 实例。

    Args:
        config_str: JSON 格式配置字符串，格式为：
            {
                "name": "tool_name",
                "command": "node" | "python",
                "args": ["xxx.js"] | ["xxx.py"]
            }
            其中 command 支持 "node" 和 "python" 类型，对应使用 StdioClient。

    Returns:
        Tool 实例

    Raises:
        ValueError: JSON 解析失败或配置不合法时
    """
    try:
        config = json.loads(config_str)
    except json.JSONDecodeError as e:
        raise ValueError(f"无效的 JSON 配置: {e}")

    # 处理数组格式或单个字典格式
    if isinstance(config, list):
        if len(config) == 0:
            raise ValueError("工具配置数组不能为空")
        # 使用第一个工具配置
        tool_config = config[0]
    elif isinstance(config, dict):
        tool_config = config
    else:
        raise ValueError("配置必须是字典或数组类型")

    if not isinstance(tool_config, dict):
        raise ValueError("工具配置必须是字典类型")

    tool_name = tool_config.get("name")
    command = tool_config.get("command")
    args = tool_config.get("args", [])

    if not tool_name:
        raise ValueError("工具配置缺少 'name' 字段")

    if not command:
        raise ValueError(f"工具 '{tool_name}' 缺少 'command' 字段")

    if command not in ("node", "python"):
        raise ValueError(f"不支持的 command 类型: '{command}'，目前仅支持 'node' 和 'python'")

    if not isinstance(args, list):
        raise ValueError(f"工具 '{tool_name}' 的 args 必须是列表类型")

    # 构造 McpServerConfig
    mcp_config = McpServerConfig(
        server_id=tool_name,
        server_name=tool_name,
        server_path=f"stdio://{tool_name}",
        client_type="stdio",
        params={
            "command": command,
            "args": args,
        },
    )

    # 创建 StdioClient 实例
    stdio_client = StdioClient(
        server_path=mcp_config.server_path,
        name=mcp_config.server_name,
        params=mcp_config.params,
    )

    # 创建一个临时的 McpToolCard（实际使用时需要连接服务器获取真实信息）
    temp_card = McpToolCard(
        name=tool_name,
        server_name=tool_name,
        description=f"MCP tool from {command} with args {args}",
        input_params={},
    )

    # 创建 MCPTool 实例
    return MCPTool(mcp_client=stdio_client, tool_info=temp_card)


def _has_paid_search_api_key() -> bool:
    """Check if any paid search API key is configured."""
    return any([
        os.environ.get("PERPLEXITY_API_KEY"),
        os.environ.get("SERPER_API_KEY"),
        os.environ.get("JINA_API_KEY"),
    ])


def get_mcp_tools() -> list[Tool]:
    """Return all MCP toolkit tools for registration in Runner."""
    tools = [mcp_free_search, mcp_fetch_webpage, mcp_exec_command]
    if _has_paid_search_api_key():
        tools.append(mcp_paid_search)
    return tools


__all__ = [
    "mcp_free_search",
    "mcp_paid_search",
    "mcp_fetch_webpage",
    "mcp_exec_command",
    "get_mcp_tools",
    "create_mcp_tool",
]
