#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Agent builders for runtime and browser worker."""

from __future__ import annotations

import asyncio
import inspect
import os
from typing import Any

import anyio

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


def _resolve_tool_timeout_s(default_s: float = 180.0) -> float:
    raw = (
        os.getenv("PLAYWRIGHT_TOOL_TIMEOUT_S")
        or os.getenv("PLAYWRIGHT_MCP_TIMEOUT_S")
        or os.getenv("BROWSER_TIMEOUT_S")
        or str(default_s)
    )
    try:
        parsed = float(raw)
        if parsed > 0:
            return parsed
    except (TypeError, ValueError):
        pass
    return default_s


def _format_tool_names(tool_call: Any) -> str:
    if isinstance(tool_call, list):
        names = [getattr(item, "name", "") for item in tool_call]
        names = [name for name in names if name]
        return ", ".join(names) if names else "<unknown>"
    name = getattr(tool_call, "name", "")
    return name or "<unknown>"


def _build_main_agent_system_prompt(default_timeout_s: float) -> str:
    timeout_text = f"{int(default_timeout_s)}" if default_timeout_s.is_integer() else f"{default_timeout_s:.1f}"
    return (
        "You are the main orchestration agent.\n"
        "For browser tasks, prefer browser_run_task.\n"
        "Default to one comprehensive browser_run_task call per user request.\n"
        "Do not split work into many small browser_run_task calls unless a prior browser result shows "
        "a concrete blocking error that requires a narrower retry.\n"
        "Reuse the same session_id across retries to preserve browser continuity.\n"
        f"Use a long browser timeout. Do not pass timeout_s below {timeout_text}s. "
        "Prefer omitting timeout_s so the default long timeout is used.\n"
        "When a request is not straightforward and needs custom logic, call browser_custom_action first.\n"
        "If action names or params are unclear, call browser_list_custom_actions first and "
        "then call browser_custom_action with the matching action and params.\n"
        "Do not simulate browser actions yourself.\n"
        "Pass through the full user goal clearly as browser task text.\n"
        "Keep user-facing answer concise and factual.\n"
        "If a browser tool returns an error, report it explicitly."
    )


def ensure_execute_signature_compat(agent: ReActAgent) -> None:
    """Adapt execute signature and add a timeout watchdog around tool execution."""
    execute_fn = getattr(agent.ability_manager, "execute", None)
    if execute_fn is None:
        return
    if getattr(execute_fn, "_playwright_timeout_wrapped", False):
        return

    try:
        params = inspect.signature(execute_fn).parameters
    except (TypeError, ValueError):
        return

    original_execute = execute_fn
    supports_tag = "tag" in params
    tool_timeout_s = _resolve_tool_timeout_s()

    async def execute_with_tag(tool_call, session, tag=None):
        tool_names = _format_tool_names(tool_call)
        try:
            with anyio.fail_after(tool_timeout_s):
                if supports_tag:
                    return await original_execute(tool_call, session, tag=tag)
                return await original_execute(tool_call, session)
        except TimeoutError as exc:
            logger.error(
                f"Tool execution timed out after {tool_timeout_s:.1f}s; tools={tool_names}"
            )
            raise RuntimeError(
                f"tool_execution_timeout: tools={tool_names}, timeout_s={tool_timeout_s:.1f}"
            ) from exc

    agent.ability_manager.execute = execute_with_tag
    setattr(agent.ability_manager.execute, "_playwright_timeout_wrapped", True)


def build_browser_worker_agent(
    provider: str,
    api_key: str,
    api_base: str,
    model_name: str,
    mcp_cfg: McpServerConfig,
    max_steps: int,
    screenshot_subdir: str = "screenshots",
) -> ReActAgent:
    screenshot_subdir = (
        (screenshot_subdir or "screenshots").strip().replace("\\", "/").strip("/") or "screenshots"
    )
    card = AgentCard(
        id="agent.playwright.browser_worker",
        name="playwright_browser_worker",
        description="Browser worker that executes web tasks using Playwright MCP tools.",
        input_params={},
    )
    agent = ReActAgent(card=card).configure(
        ReActAgentConfig()
        .configure_model_client(
            provider=provider,
            api_key=api_key,
            api_base=api_base,
            model_name=model_name,
        )
        .configure_max_iterations(max_steps)
        .configure_prompt_template(
            [
                {
                    "role": "system",
                    "content": (
                        "You are a browser worker agent.\n"
                        "Execute browser tasks step-by-step with Playwright MCP tools only.\n"
                        "Before interacting, ensure page or selector readiness.\n"
                        "Keep actions targeted and avoid unnecessary page snapshots.\n"
                        "If actions repeatedly fail, stop and report the exact failing action.\n"
                        "IMPORTANT: Do NOT use browser_take_screenshot unless strictly necessary. "
                        f"If a screenshot is needed, always save it under '{screenshot_subdir}/'. "
                        "Use browser_run_code with: "
                        f"async (page) => {{ await page.screenshot({{ path: '{screenshot_subdir}/screenshot.png' }}); "
                        f"return '{screenshot_subdir}/screenshot.png'; }}\n"
                        "Final output MUST be a single JSON object with keys:\n"
                        "ok (boolean), final (string), page (object with url and title), "
                        "screenshot (string|null), error (string|null).\n"
                        "Do not output markdown."
                    ),
                }
            ]
        )
    )
    agent.ability_manager.add(mcp_cfg)
    ensure_execute_signature_compat(agent)
    return agent


def build_main_agent(
    provider: str,
    api_key: str,
    api_base: str,
    model_name: str,
    browser_tool_card,
    custom_action_tool_card=None,
    list_actions_tool_card=None,
) -> ReActAgent:
    default_timeout_s = _resolve_tool_timeout_s()
    card = AgentCard(
        id="agent.playwright.main_runtime",
        name="playwright_main_runtime",
        description="Main runtime agent that delegates browser work to browser_run_task.",
        input_params={},
    )
    agent = ReActAgent(card=card).configure(
        ReActAgentConfig()
        .configure_model_client(
            provider=provider,
            api_key=api_key,
            api_base=api_base,
            model_name=model_name,
        )
        .configure_max_iterations(8)
        .configure_prompt_template(
            [
                {
                    "role": "system",
                    "content": _build_main_agent_system_prompt(default_timeout_s),
                }
            ]
        )
    )
    agent.ability_manager.add(browser_tool_card)
    if custom_action_tool_card is not None:
        agent.ability_manager.add(custom_action_tool_card)
    if list_actions_tool_card is not None:
        agent.ability_manager.add(list_actions_tool_card)
    ensure_execute_signature_compat(agent)
    return agent
