from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from jiuwenswarm.server.runtime import video_research
from jiuwenswarm.common.e2a.wire_codec import parse_agent_server_wire_unary
from jiuwenswarm.common.schema.agent import AgentRequest
from jiuwenswarm.common.schema.message import ReqMethod
from jiuwenswarm.server.agent_ws_server import AgentWebSocketServer


def test_build_video_research_agent_registers_search_and_fetch_tools(monkeypatch) -> None:
    captured = {}
    sentinel = object()

    def fake_create_research_agent(**kwargs):
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(video_research, "create_research_agent", fake_create_research_agent)

    assert video_research.build_video_research_agent("model", max_iterations=6) is sentinel
    assert captured["model"] == "model"
    assert captured["tools"] == [
        video_research.mcp_free_search,
        video_research.mcp_fetch_webpage,
    ]
    assert captured["enable_task_loop"] is False
    assert captured["max_iterations"] == 6
    assert "必须先使用 mcp_free_search" in captured["system_prompt"]
    assert "通常使用2至4句话且不超过300个汉字" in captured["system_prompt"]
    assert "不要复述搜索过程" in captured["system_prompt"]
    assert "最多附两个最相关的来源链接" in captured["system_prompt"]


@pytest.mark.asyncio
async def test_run_video_research_passes_visual_clue_and_normalizes_result(monkeypatch) -> None:
    calls = []

    class FakeResearchAgent:
        async def invoke(self, inputs):
            calls.append(inputs)
            return {"output": "有证据的回答。https://example.com/source"}

    monkeypatch.setattr(
        video_research,
        "build_video_research_agent",
        lambda model: FakeResearchAgent(),
    )

    result = await video_research.run_video_research(
        SimpleNamespace(),
        question="这个品牌是什么？",
        query="农夫山泉品牌资料",
        visual_context="画面模型识别为农夫山泉",
        search_session_id="search-session",
    )

    assert "建议搜索词：农夫山泉品牌资料" in calls[0]["query"]
    assert "画面模型识别为农夫山泉" in calls[0]["query"]
    assert "未经验证" in calls[0]["query"]
    assert "最终只给2至4句话的必要结论" in calls[0]["query"]
    assert "最多保留两个来源链接" in calls[0]["query"]
    assert "不要输出搜索过程" in calls[0]["query"]
    assert result == {
        "answer": "有证据的回答。https://example.com/source",
        "sources": ["https://example.com/source"],
        "tools_used": ["mcp_free_search", "mcp_fetch_webpage"],
        "original_answer_chars": len("有证据的回答。https://example.com/source"),
        "answer_chars": len("有证据的回答。https://example.com/source"),
    }


@pytest.mark.asyncio
async def test_run_video_research_compacts_verbose_output_before_returning(monkeypatch) -> None:
    compact_calls = []
    verbose = (
        "根据搜索结果，情况如下：\n\n"
        "## 实时状况\n"
        "- 香港目前31°C，相对湿度66%。\n"
        "- 今天大致多云，有几阵骤雨及局部雷暴。\n"
        f"- {'无关背景信息' * 80}\n"
        "来源：https://www.hko.gov.hk/weather"
    )

    class FakeResearchAgent:
        async def invoke(self, inputs):
            del inputs
            return {"output": verbose}

    class FakeModel:
        async def invoke(self, messages, temperature):
            compact_calls.append((messages, temperature))
            return SimpleNamespace(
                content=(
                    "香港目前约31°C，今天大致多云，有几阵骤雨及局部雷暴，外出建议带伞。"
                    " [香港天文台](https://www.hko.gov.hk/weather)"
                )
            )

    monkeypatch.setattr(
        video_research,
        "build_video_research_agent",
        lambda model: FakeResearchAgent(),
    )

    result = await video_research.run_video_research(
        FakeModel(),
        question="今天香港天气怎么样？",
        query="香港天气",
    )

    assert len(compact_calls) == 1
    compact_prompt = compact_calls[0][0][0].content
    assert "正文不超过220个汉字" in compact_prompt
    assert "不要标题、列表、搜索过程" in compact_prompt
    assert "无关背景信息" in compact_prompt
    assert compact_calls[0][1] == 0
    assert "香港目前约31°C" in result["answer"]
    assert "无关背景信息" not in result["answer"]
    assert result["sources"] == ["https://www.hko.gov.hk/weather"]
    assert result["original_answer_chars"] == len(verbose)
    assert result["answer_chars"] == len(result["answer"])


@pytest.mark.asyncio
async def test_verbose_research_output_has_complete_sentence_fallback(monkeypatch) -> None:
    verbose = "\n".join(
        ["## 搜索结果"]
        + [f"- 第{index}项事实说明，包含一些补充信息。" for index in range(1, 30)]
        + ["来源：https://example.com/source"]
    )

    class FakeResearchAgent:
        async def invoke(self, inputs):
            del inputs
            return {"output": verbose}

    class FailingModel:
        async def invoke(self, messages, temperature):
            del messages, temperature
            raise RuntimeError("compression unavailable")

    monkeypatch.setattr(
        video_research,
        "build_video_research_agent",
        lambda model: FakeResearchAgent(),
    )

    result = await video_research.run_video_research(
        FailingModel(),
        question="概括搜索结果",
        query="测试",
    )

    visible = result["answer"].partition(" [来源]")[0]
    assert len(visible) <= 300
    assert visible.endswith("。")
    assert "第1项事实说明" in visible
    assert "第29项事实说明" not in visible
    assert result["sources"] == ["https://example.com/source"]


@pytest.mark.asyncio
async def test_run_video_research_rejects_empty_agent_output(monkeypatch) -> None:
    class FakeResearchAgent:
        async def invoke(self, inputs):
            del inputs
            return {"output": ""}

    monkeypatch.setattr(
        video_research,
        "build_video_research_agent",
        lambda model: FakeResearchAgent(),
    )

    with pytest.raises(RuntimeError, match="empty output"):
        await video_research.run_video_research(
            object(),
            question="测试",
            query="测试",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "agent_result",
    [
        {
            "output": "Max iterations reached without completion",
            "result_type": "error",
        },
        {"output": "Max iterations reached without completion"},
    ],
)
async def test_run_video_research_rejects_iteration_limit_result(
    monkeypatch,
    agent_result,
) -> None:
    class FakeResearchAgent:
        async def invoke(self, inputs):
            del inputs
            return agent_result

    monkeypatch.setattr(
        video_research,
        "build_video_research_agent",
        lambda model: FakeResearchAgent(),
    )

    with pytest.raises(RuntimeError, match="Max iterations reached"):
        await video_research.run_video_research(
            object(),
            question="今天香港天气怎么样？",
            query="香港天气",
        )


@pytest.mark.asyncio
async def test_agent_server_video_research_rpc_returns_normalized_payload(monkeypatch) -> None:
    calls = []

    async def fake_run(model, **kwargs):
        calls.append((model, kwargs))
        return {
            "answer": "研究结论",
            "sources": ["https://example.com"],
            "tools_used": ["mcp_free_search", "mcp_fetch_webpage"],
        }

    class FakeWebSocket:
        def __init__(self):
            self.sent = []

        async def send(self, payload):
            self.sent.append(json.loads(payload))

    model = object()
    monkeypatch.setattr(video_research, "run_video_research", fake_run)
    monkeypatch.setattr(
        AgentWebSocketServer,
        "_resolve_model",
        lambda self, name=None: model,
    )
    server = object.__new__(AgentWebSocketServer)
    ws = FakeWebSocket()
    request = AgentRequest(
        request_id="video-research-request",
        channel_id="web",
        req_method=ReqMethod.VIDEO_RESEARCH,
        params={
            "question": "这个品牌是什么？",
            "query": "品牌资料",
            "visual_context": "画面品牌为农夫山泉",
            "search_session_id": "search-session",
        },
    )

    await server._handle_video_research(ws, request, asyncio.Lock())

    assert calls == [(model, {
        "question": "这个品牌是什么？",
        "query": "品牌资料",
        "visual_context": "画面品牌为农夫山泉",
        "search_session_id": "search-session",
    })]
    response = parse_agent_server_wire_unary(ws.sent[0])
    assert response.ok is True
    assert response.payload["answer"] == "研究结论"
    assert response.payload["model"] == "default"
