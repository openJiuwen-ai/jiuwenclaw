#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""CLI entrypoint for Playwright browser runtime."""

from __future__ import annotations

import asyncio
import json
import os
import traceback

from openjiuwen.deepagents.tools.browser_move.clients.stdio_client import BrowserMoveStdioClient
from openjiuwen.deepagents.tools.browser_move.clients.streamable_http_client import (
    BrowserMoveStreamableHttpClient,
)
from openjiuwen.deepagents.tools.browser_move.playwright_runtime.config import (
    MISSING_API_KEY_MESSAGE,
    build_runtime_settings,
    load_repo_dotenv,
)
from openjiuwen.deepagents.tools.browser_move.playwright_runtime.runtime import BrowserAgentRuntime

import openjiuwen.core.runner.resources_manager.tool_manager as _tool_mgr_mod
from openjiuwen.core.common.logging import logger
from openjiuwen.core.runner import Runner

_tool_mgr_mod.StdioClient = BrowserMoveStdioClient
_tool_mgr_mod.StreamableHttpClient = BrowserMoveStreamableHttpClient


async def main() -> None:
    load_repo_dotenv()
    settings = build_runtime_settings()
    if not settings.api_key:
        raise RuntimeError(MISSING_API_KEY_MESSAGE)

    runtime = BrowserAgentRuntime(
        provider=settings.provider,
        api_key=settings.api_key,
        api_base=settings.api_base,
        model_name=settings.model_name,
        mcp_cfg=settings.mcp_cfg,
        guardrails=settings.guardrails,
    )

    initial_query = (os.getenv("AGENT_QUERY") or "").strip()
    session_id = (os.getenv("AGENT_SESSION_ID") or "").strip() or "demo-browser-session"

    try:
        await runtime.ensure_started()
        mcp_tools = await Runner.resource_mgr.get_mcp_tool_infos(server_id=settings.mcp_cfg.server_id) or []

        logger.info("=" * 72)
        logger.info("Playwright Browser Runtime")
        logger.info("=" * 72)
        logger.info(f"Model provider: {settings.provider}")
        logger.info(f"Model: {settings.model_name}")
        logger.info(f"MCP command: {settings.mcp_cfg.params.get('command')}")
        logger.info(f"MCP args: {settings.mcp_cfg.params.get('args')}")
        logger.info(f"Discovered browser tools: {len(mcp_tools)}")
        for item in mcp_tools:
            logger.info(f"  - {getattr(item, 'name', 'unknown')}")
        logger.info("=" * 72)
        logger.info(f"Session: {session_id}")
        logger.info("Continuous mode: enter a task and press Enter.")
        logger.info("Type 'exit' or 'quit' to stop.")

        query = initial_query
        while True:
            if not query:
                query = input("query> ").strip()
            if not query:
                continue
            if query.lower() in {"exit", "quit"}:
                break

            answer = await runtime.handle_request(query=query, session_id=session_id)
            logger.info("Result:")
            logger.info(json.dumps(answer, ensure_ascii=False, indent=2))
            query = ""
    except Exception as exc:
        logger.error(f"Runtime error: {exc}")
        traceback.print_exc()
        raise
    except KeyboardInterrupt:
        logger.info("Interrupted by user.")
    finally:
        await runtime.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
