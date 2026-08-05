"""Unit tests for the SearchAgent dispatch tool (vendored ReAct loop wrapper).

Covers:
- ``_extract_final_answer``: pulls the last ``output_text`` trace item; falls
  back to ``failure_reason`` / ``other_info`` when absent.
- ``SearchAgentTool.invoke``: with ``MMSearchAgent`` mocked, returns a
  ``ToolOutput`` carrying the final answer + ``agent_id`` and calls
  ``agent.close()``; handles empty prompt; surfaces a failure ``ToolOutput``
  when ``react_loop`` raises.
- ``build_search_agent_config_from_jiuwenswarm``: returns ``None`` without a
  dedicated search model; builds an ``AgentConfig`` carrying the dedicated
  model when ``models.search`` is configured.
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, patch

from jiuwenswarm.agents.harness.search.agent.nlp_react_agent import AgentConfig
from jiuwenswarm.agents.harness.search.agent.trace import ReactLoopResult, TraceItem
from jiuwenswarm.agents.harness.search.config_adapter import (
    build_search_agent_config_from_jiuwenswarm,
)
from jiuwenswarm.agents.harness.search.tool import (
    SearchAgentTool,
    _extract_final_answer,
    build_search_agent_tool_card,
)


def _agent_config() -> AgentConfig:
    return AgentConfig(
        model_name="qwen/test",
        api_key="k",
        base_url="http://x",
        system_prompt_name="SYSTEM_TEMPLATE_XIAOHAN0319",
        query_prompt_name="QUERY_TEMPLATE",
        tool_names=["web_search"],
        max_iterations=2,
        timeout=None,
        tokenizer_name=None,
    )


def test_extract_final_answer_picks_last_output_text() -> None:
    result = ReactLoopResult(
        success=True,
        finish_reason="stop",
        turn_num=3,
        trace_list=[
            TraceItem(type="tool_call", tool_name="web_search"),
            TraceItem(type="output_text", output="first"),
            TraceItem(type="output_text", output="final answer"),
        ],
    )
    assert _extract_final_answer(result) == "final answer"


def test_extract_final_answer_falls_back_to_failure_reason() -> None:
    result = ReactLoopResult(
        success=False, finish_reason="error", turn_num=1, trace_list=[], failure_reason="boom"
    )
    assert _extract_final_answer(result) == "boom"


def test_search_agent_tool_invoke_returns_answer() -> None:
    cfg = _agent_config()
    tool = SearchAgentTool(
        card=build_search_agent_tool_card(agent_id="t"),
        agent_config=cfg,
        logger=logging.getLogger("test"),
    )
    expected = ReactLoopResult(
        success=True,
        finish_reason="stop",
        turn_num=2,
        trace_list=[TraceItem(type="output_text", output="42")],
    )
    with patch("jiuwenswarm.agents.harness.search.tool.MMSearchAgent") as mock_cls:
        instance = mock_cls.return_value
        instance.react_loop = AsyncMock(return_value=expected)
        instance.close = AsyncMock()
        out = asyncio.run(tool.invoke({"prompt": "meaning of life"}))

    assert out.success is True
    assert out.data["output"] == "42"
    assert out.data["agent_id"] == "search_agent"
    instance.close.assert_awaited()


def test_search_agent_tool_invoke_empty_prompt() -> None:
    tool = SearchAgentTool(
        card=build_search_agent_tool_card(agent_id="t"),
        agent_config=_agent_config(),
        logger=logging.getLogger("test"),
    )
    out = asyncio.run(tool.invoke({}))
    assert out.success is False
    assert "non-empty" in out.data["output"]


def test_search_agent_tool_invoke_surfaces_failure() -> None:
    tool = SearchAgentTool(
        card=build_search_agent_tool_card(agent_id="t"),
        agent_config=_agent_config(),
        logger=logging.getLogger("test"),
    )
    with patch("jiuwenswarm.agents.harness.search.tool.MMSearchAgent") as mock_cls:
        instance = mock_cls.return_value
        instance.react_loop = AsyncMock(side_effect=RuntimeError("kaboom"))
        instance.close = AsyncMock()
        out = asyncio.run(tool.invoke({"prompt": "x"}))

    assert out.success is False
    assert "kaboom" in out.data["output"]
    instance.close.assert_awaited()


def test_config_adapter_returns_none_without_search_model() -> None:
    assert build_search_agent_config_from_jiuwenswarm({}) is None
    assert build_search_agent_config_from_jiuwenswarm({"models": {}}) is None
    # model_name missing -> None (skip mounting)
    assert (
        build_search_agent_config_from_jiuwenswarm(
            {"models": {"search": {"model_client_config": {"api_key": "k", "api_base": "u"}}}}
        )
        is None
    )


def test_config_adapter_builds_config_with_dedicated_model() -> None:
    cfg = build_search_agent_config_from_jiuwenswarm(
        {
            "models": {
                "search": {
                    "model_client_config": {
                        "model_name": "qwen/search",
                        "api_key": "sk",
                        "api_base": "http://search",
                    },
                    "model_config_obj": {"temperature": 0.3},
                }
            },
            "react": {"subagents": {"search_agent": {"max_iterations": 7, "tool_names": ["web_search"]}}},
        }
    )
    assert cfg is not None
    assert cfg.model_name == "qwen/search"
    assert cfg.api_key == "sk"
    assert cfg.base_url == "http://search"
    assert cfg.max_iterations == 7
    assert cfg.tool_names == ["web_search"]
