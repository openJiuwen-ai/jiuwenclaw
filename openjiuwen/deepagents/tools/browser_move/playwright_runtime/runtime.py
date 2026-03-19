#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Runtime wiring for main agent and browser tool."""

from __future__ import annotations

import contextvars
import uuid
from typing import Any, Dict, Optional

from openjiuwen.core.foundation.tool import McpServerConfig, tool
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent

from .agents import build_main_agent
from ..controllers import ActionController, BaseController
from .config import BrowserRunGuardrails
from .service import BrowserService

_ctx_parent_session_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "playwright_runtime_parent_session_id",
    default="",
)
_ctx_parent_request_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "playwright_runtime_parent_request_id",
    default="",
)


class BrowserAgentRuntime:
    """Runtime that wires main agent + browser backend tool contract."""

    def __init__(
        self,
        provider: str,
        api_key: str,
        api_base: str,
        model_name: str,
        mcp_cfg: McpServerConfig,
        guardrails: BrowserRunGuardrails,
    ) -> None:
        self._service = BrowserService(
            provider=provider,
            api_key=api_key,
            api_base=api_base,
            model_name=model_name,
            mcp_cfg=mcp_cfg,
            guardrails=guardrails,
        )
        self._main_agent: Optional[ReActAgent] = None
        self._browser_tool = None
        self._browser_custom_action_tool = None
        self._browser_list_actions_tool = None
        self._controller: BaseController = ActionController()

    @property
    def service(self):
        return self._service

    @property
    def main_agent(self):
        return self._main_agent

    @main_agent.setter
    def main_agent(self, value) -> None:
        self._main_agent = value

    @property
    def browser_tool(self):
        return self._browser_tool

    @property
    def browser_custom_action_tool(self):
        return self._browser_custom_action_tool

    @property
    def browser_list_actions_tool(self):
        return self._browser_list_actions_tool

    @staticmethod
    def _register_runtime_tool(tool_obj: Any, *, tool_name: str) -> None:
        add_result = Runner.resource_mgr.add_tool(tool_obj, tag="agent.playwright.main_runtime")
        if add_result is None or getattr(add_result, "is_ok", lambda: False)():
            return
        error_value = getattr(add_result, "value", add_result)
        if "already exist" in str(error_value):
            return
        raise RuntimeError(f"Failed to register {tool_name} tool: {error_value}")

    async def cancel_run(self, session_id: str, request_id: Optional[str] = None) -> Dict[str, Any]:
        await self._service.request_cancel(session_id=session_id, request_id=request_id)
        return {
            "ok": True,
            "session_id": session_id,
            "request_id": request_id,
            "error": None,
        }

    async def clear_cancel(self, session_id: str, request_id: Optional[str] = None) -> Dict[str, Any]:
        await self._service.clear_cancel(session_id=session_id, request_id=request_id)
        return {
            "ok": True,
            "session_id": session_id,
            "request_id": request_id,
            "error": None,
        }

    async def ensure_started(self) -> None:
        await self._service.ensure_started()
        if self._main_agent is not None:
            return
        self._controller.register_example_actions()
        action_details = self._controller.describe_actions()
        action_summary_lines: list[str] = []
        for action_name in sorted(action_details.keys()):
            spec = action_details.get(action_name, {})
            summary = str(spec.get("summary", "")).strip() or "No summary."
            when_to_use = str(spec.get("when_to_use", "")).strip()
            if when_to_use:
                action_summary_lines.append(f"- {action_name}: {summary} Use when: {when_to_use}")
            else:
                action_summary_lines.append(f"- {action_name}: {summary}")
        action_summary_text = "\n".join(action_summary_lines) if action_summary_lines else "- (none)"

        @tool(
            name="browser_run_task",
            description=(
                "Run a browser task in a sticky logical session. "
                "Prefer one comprehensive task per request instead of many tiny retries. "
                "Use a long timeout and do not pass timeout_s below the configured default; "
                "omit timeout_s to use the default long timeout. "
                "Returns JSON with ok/session_id/final/page/screenshot/error/attempt/failure_summary."
            ),
        )
        async def browser_run_task(
            task: str,
            session_id: str = "",
            request_id: str = "",
            timeout_s: int = 180,
        ) -> Dict[str, Any]:
            effective_session_id = (session_id or "").strip() or _ctx_parent_session_id.get()
            effective_request_id = (request_id or "").strip() or _ctx_parent_request_id.get()
            return await self._service.run_task(
                task=task,
                session_id=effective_session_id,
                request_id=effective_request_id,
                timeout_s=timeout_s,
            )

        @tool(
            name="browser_custom_action",
            description=(
                "Run a registered custom browser action by name. "
                "Use this for higher-level actions such as drag-and-drop helpers. "
                "For browser_get_element_coordinates provide at least element_source (element_target optional); "
                "for browser_drag_and_drop provide element_source + element_target. "
                "Or use (coord_source_x + coord_source_y + coord_target_x + coord_target_y). "
                "Aliases source/target and source_x/source_y/target_x/target_y are accepted.\n"
                "Current registered actions:\n"
                f"{action_summary_text}\n"
                "If uncertain about params, call browser_list_custom_actions first and use its details."
            ),
        )
        async def browser_custom_action(
            action: str,
            session_id: str = "",
            request_id: str = "",
            params: Optional[Dict[str, Any]] = None,
        ) -> Dict[str, Any]:
            effective_session_id = (session_id or "").strip() or _ctx_parent_session_id.get()
            effective_request_id = (request_id or "").strip() or _ctx_parent_request_id.get()
            self._controller.bind_runtime(self)
            return await self._controller.run_action(
                action=action,
                session_id=effective_session_id,
                request_id=effective_request_id,
                **(params or {}),
            )

        @tool(
            name="browser_list_custom_actions",
            description=(
                "List available custom actions and detailed parameter guidance "
                "for browser_custom_action."
            ),
        )
        async def browser_list_custom_actions() -> Dict[str, Any]:
            return {
                "ok": True,
                "actions": self._controller.list_actions(),
                "details": self._controller.describe_actions(),
            }

        self._browser_tool = browser_run_task
        self._browser_custom_action_tool = browser_custom_action
        self._browser_list_actions_tool = browser_list_custom_actions
        self._register_runtime_tool(self._browser_tool, tool_name="browser_run_task")
        self._register_runtime_tool(self._browser_custom_action_tool, tool_name="browser_custom_action")
        self._register_runtime_tool(
            self._browser_list_actions_tool,
            tool_name="browser_list_custom_actions",
        )

        self._main_agent = build_main_agent(
            provider=self._service.provider,
            api_key=self._service.api_key,
            api_base=self._service.api_base,
            model_name=self._service.model_name,
            browser_tool_card=self._browser_tool.card,
            custom_action_tool_card=self._browser_custom_action_tool.card,
            list_actions_tool_card=self._browser_list_actions_tool.card,
        )

    async def handle_request(
        self,
        query: str,
        session_id: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        await self.ensure_started()
        sid = self._service.session_new(session_id)
        rid = (request_id or "").strip() or uuid.uuid4().hex

        if self._main_agent is None:
            raise RuntimeError("Main agent not initialized")

        token_sid = _ctx_parent_session_id.set(sid)
        token_rid = _ctx_parent_request_id.set(rid)
        try:
            result = await Runner.run_agent(
                self._main_agent,
                {"query": query, "conversation_id": sid, "request_id": rid},
            )
        finally:
            _ctx_parent_session_id.reset(token_sid)
            _ctx_parent_request_id.reset(token_rid)
        result_type = result.get("result_type") if isinstance(result, dict) else None
        final = result.get("output", "") if isinstance(result, dict) else str(result)
        ok = result_type != "error"
        error = None if ok else str(final)

        return {
            "ok": ok,
            "session_id": sid,
            "request_id": rid,
            "final": final,
            "error": error,
        }

    async def run_browser_task(
        self,
        task: str,
        session_id: Optional[str] = None,
        request_id: Optional[str] = None,
        timeout_s: Optional[int] = None,
    ) -> Dict[str, Any]:
        await self.ensure_started()
        return await self._service.run_task(
            task=task,
            session_id=session_id,
            request_id=request_id,
            timeout_s=timeout_s,
        )

    async def shutdown(self) -> None:
        await self._service.shutdown()
