# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
TrajectoryCollectionRail
------------------------

Collects LLM call trajectory data via AgentRail lifecycle hooks:

- before_model_call : records serialized input messages + tools
- after_model_call  : records serialized LLM response → creates Rollout
- after_tool_call   : records actual tool result keyed by tool_call_id,
                      so the next before_model_call can replace any
                      placeholder content that _serialize_tool_result put
                      in the context messages.

TrajectoryCollector wraps the above rail into a one-shot helper that
registers the rail, runs the agent, and returns the collected Rollout list.
"""

import json
from typing import Any, Dict, List, Optional

from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    AgentRail,
    ToolCallInputs,
)
from openjiuwen.dev_tools.agentrl.coordinator.schemas import Rollout


class TrajectoryCollectionRail(AgentRail):
    """Rail that collects one Rollout per LLM turn.

    Usage::

        rail = TrajectoryCollectionRail()
        await agent.register_rail(rail)
        await agent.invoke({"query": "..."})
        rollouts = rail.get_rollouts()
        rail.clear()
        await agent.unregister_rail(rail)
    """

    priority = 100

    def __init__(self) -> None:
        self._turns: List[Rollout] = []
        # Actual tool content keyed by tool_call_id, captured in after_tool_call.
        self._tool_results: Dict[str, str] = {}
        # Snapshot of the current turn's serialized inputs (set in before_model_call).
        self._current_input: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------------
    # Rail hooks
    # ------------------------------------------------------------------

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Serialize input messages and tools; patch tool messages with real content."""
        inputs = ctx.inputs

        messages: List[Dict[str, Any]] = []
        if hasattr(inputs, "messages"):
            for msg in inputs.messages:
                msg_dict = self._to_dict(msg)
                # Patch stale/wrong tool-message content with what we captured.
                if msg_dict.get("role") == "tool":
                    tcid = msg_dict.get("tool_call_id", "")
                    if tcid and tcid in self._tool_results:
                        msg_dict["content"] = self._tool_results[tcid]
                messages.append(msg_dict)

        tools: Optional[List[Dict[str, Any]]] = None
        if hasattr(inputs, "tools") and inputs.tools:
            tools = self._normalize_tools(inputs.tools)

        self._current_input = {
            "messages": messages,
            "tools": tools,
            "llm_config": self._extract_llm_config(ctx),
        }

    async def after_model_call(self, ctx: AgentCallbackContext) -> None:
        """Serialize LLM response and commit the current turn as a Rollout."""
        if self._current_input is None:
            return

        response = getattr(ctx.inputs, "response", None)
        response_dict = self._build_response_dict(response)

        turn = Rollout(
            turn_id=len(self._turns),
            input_prompt={
                "message": self._current_input["messages"],
                "tools": self._current_input["tools"],
            },
            output_response=response_dict,
            llm_config=self._current_input["llm_config"],
        )
        self._turns.append(turn)
        self._current_input = None

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Capture the raw tool result to override bad serialization in context messages."""
        inputs = ctx.inputs
        if not isinstance(inputs, ToolCallInputs):
            return

        tool_call = inputs.tool_call
        if tool_call is None:
            return

        tool_call_id: str = getattr(tool_call, "id", "") or ""
        if not tool_call_id:
            return

        raw_result = inputs.tool_result
        if raw_result is None:
            # Tool returned nothing; use tool_msg content as fallback.
            tool_msg = inputs.tool_msg
            fallback = None
            if tool_msg is not None:
                c = getattr(tool_msg, "content", None)
                if isinstance(c, str) and c:
                    fallback = c
            self._tool_results[tool_call_id] = fallback or "(tool returned no result)"
            return

        self._tool_results[tool_call_id] = self._serialize_tool_content(raw_result)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_rollouts(self) -> List[Rollout]:
        """Return a copy of all collected Rollout objects for the current run."""
        return list(self._turns)

    def clear(self) -> None:
        """Clear all collected rollouts and internal state for a fresh collection."""
        self._turns.clear()
        self._tool_results.clear()
        self._current_input = None

    # ------------------------------------------------------------------
    # Serialization helpers (static)
    # ------------------------------------------------------------------

    @staticmethod
    def _serialize_tool_content(result: Any) -> str:
        """Convert raw tool result to a string for the trajectory.

        Checks str() FIRST to detect useless Python object reprs (e.g.
        '<object object at 0x...>') before attempting JSON serialization,
        so that json.dumps(..., default=str) can never silently swallow them.
        """
        if isinstance(result, str):
            return result
        # Detect useless reprs before json.dumps can hide them via default=str.
        s = str(result)
        if " at 0x" in s and "object" in s.lower():
            return "(tool returned non-serializable value)"
        try:
            # No default=str: let unserializable types raise so we catch them.
            return json.dumps(result, ensure_ascii=False)
        except Exception:
            return s

    @staticmethod
    def _to_dict(msg: Any) -> Dict[str, Any]:
        """Convert a message object to a plain dict."""
        if isinstance(msg, dict):
            return dict(msg)
        if hasattr(msg, "model_dump"):
            return msg.model_dump()
        return {
            "role": getattr(msg, "role", ""),
            "content": getattr(msg, "content", ""),
            "name": getattr(msg, "name", None),
        }

    @staticmethod
    def _normalize_tools(tools: List[Any]) -> List[Dict[str, Any]]:
        """Convert tool definitions to OpenAI function-call format."""
        result: List[Dict[str, Any]] = []
        for tool in tools:
            if isinstance(tool, dict):
                if "function" in tool and isinstance(tool["function"], dict):
                    result.append(tool)
                else:
                    result.append({
                        "type": tool.get("type", "function"),
                        "function": {
                            "name": tool.get("name", ""),
                            "description": tool.get("description", ""),
                            "parameters": tool.get("parameters", {}),
                        },
                    })
            elif hasattr(tool, "model_dump"):
                d = tool.model_dump()
                params = d.get("parameters", {})
                if hasattr(params, "model_dump"):
                    params = params.model_dump()
                result.append({
                    "type": d.get("type", "function"),
                    "function": {
                        "name": d.get("name", ""),
                        "description": d.get("description", ""),
                        "parameters": params if isinstance(params, dict) else {},
                    },
                })
            else:
                result.append({
                    "type": getattr(tool, "type", "function"),
                    "function": {
                        "name": getattr(tool, "name", ""),
                        "description": getattr(tool, "description", ""),
                        "parameters": getattr(tool, "parameters", {}),
                    },
                })
        return result

    @staticmethod
    def _extract_llm_config(ctx: AgentCallbackContext) -> Optional[Dict[str, Any]]:
        agent = getattr(ctx, "agent", None)
        if agent is None:
            return None
        config = getattr(agent, "_config", None)
        if config is None:
            return None
        model_config = getattr(config, "model_config_obj", None)
        if model_config is None:
            return None
        return {
            "temperature": getattr(model_config, "temperature", None),
            "top_p": getattr(model_config, "top_p", None),
            "max_tokens": getattr(model_config, "max_tokens", None),
        }

    @staticmethod
    def _build_response_dict(response: Any) -> Dict[str, Any]:
        """Serialize LLM response to OpenAI assistant-message format."""
        if response is None:
            return {"role": "assistant", "content": ""}

        # AssistantMessage has a custom model_dump() that produces OpenAI format.
        if hasattr(response, "model_dump"):
            return response.model_dump()

        content = getattr(response, "content", "") or ""
        result: Dict[str, Any] = {"role": "assistant", "content": content}

        tool_calls = getattr(response, "tool_calls", None)
        if tool_calls:
            result["tool_calls"] = [
                {
                    "id": getattr(tc, "id", ""),
                    "type": getattr(tc, "type", "function"),
                    "function": {
                        "name": getattr(tc, "name", ""),
                        "arguments": getattr(tc, "arguments", ""),
                    },
                }
                for tc in tool_calls
            ]
        return result


class TrajectoryCollector:
    """Run an agent and collect trajectory data via TrajectoryCollectionRail.

    Usage::

        collector = TrajectoryCollector()
        rollouts = await collector.collect(agent, inputs={"query": "..."})
    """

    async def collect(
        self,
        agent: Any,
        inputs: Dict[str, Any],
    ) -> List[Rollout]:
        """Run agent and return list of Rollout objects (one per LLM turn).

        Args:
            agent: A ReActAgent (or any agent supporting register_rail/unregister_rail).
            inputs: Agent input dict (must contain 'query').

        Returns:
            List of Rollout objects collected during the run.
        """
        if not hasattr(agent, "register_rail"):
            raise ValueError(
                "Agent does not support rail-based trajectory collection. "
                "Use a ReActAgent with register_rail()."
            )

        rail = TrajectoryCollectionRail()
        await agent.register_rail(rail)
        rollouts: List[Rollout] = []
        try:
            if hasattr(agent, "invoke"):
                await agent.invoke(inputs)
            else:
                from openjiuwen.core.runner.runner import Runner
                await Runner.run_agent(agent=agent, inputs=inputs)
        except Exception as e:
            from openjiuwen.core.common.logging import logger
            logger.warning(
                "Agent invoke raised exception during trajectory collection, "
                "returning partial trajectory. error=%s", e,
            )
        finally:
            rollouts = rail.get_rollouts()
            rail.clear()
            if hasattr(agent, "unregister_rail"):
                await agent.unregister_rail(rail)

        return rollouts
