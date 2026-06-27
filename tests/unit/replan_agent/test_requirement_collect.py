# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
# pylint: disable=protected-access

"""RequirementCollectNode 单元测试。"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

_PKG_ROOT = Path(__file__).resolve().parents[3]


def _load_module(module_name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_load_module(
    "jiuwenclaw.agentserver.replan_agent.plan_node",
    _PKG_ROOT / "jiuwenclaw/agentserver/replan_agent/plan_node.py",
)
rc = _load_module(
    "jiuwenclaw.agentserver.replan_agent.skill_codes.ppt.requirement_collect",
    _PKG_ROOT / "jiuwenclaw/agentserver/replan_agent/skill_codes/ppt/requirement_collect.py",
)

_SLOT_COMPLETE = (
    '{"topic":"2025 AI 趋势","page_count":8,"audience":"企业高管",'
    '"presentation_purpose":"工作汇报","style_id":"business-classic","style_description":"",'
    '"missing_fields":[],"need_ask_style":false}'
)
_SLOT_WITH_TOPIC_PRESET = (
    '{"page_count":8,"audience":"企业高管","presentation_purpose":"工作汇报",'
    '"style_id":"business-classic","style_description":"","missing_fields":[],"need_ask_style":false}'
)
_DERIVE_RESPONSE = (
    '{"search_mode":"force_search","source_type":"topic","research_depth":"L3"}'
)


def _make_root_node(
    *,
    llm_responses: list[str] | None = None,
    ask_results: list[dict[str, Any]] | None = None,
    has_ask_tool: bool = True,
) -> rc.RequirementCollectNode:
    node = rc.RequirementCollectNode()
    llm_queue = list(llm_responses or [])
    ask_queue = list(ask_results or [])

    def _has_tool(name: str) -> bool:
        if name == "ask_user_question":
            return has_ask_tool
        return False

    async def _mock_call_llm(prompt: str, system_prompt: str = "", **_) -> str:
        if not llm_queue:
            return _SLOT_COMPLETE
        return llm_queue.pop(0)

    async def _mock_call_tool(tool_name: str, **kwargs: Any) -> Any:
        if tool_name == "ask_user_question":
            if not ask_queue:
                return {"status": "skipped", "answers": []}
            return ask_queue.pop(0)
        raise ValueError(f"unknown tool: {tool_name}")

    async def _mock_stream_llm(prompt: str, system_prompt: str = "", node_name: str | None = None, **_):
        text = await _mock_call_llm(prompt, system_prompt=system_prompt)
        yield text

    node.set_runtime_callbacks(
        has_tool=_has_tool,
        use_tool=_mock_call_tool,
        call_llm=_mock_call_llm,
        stream_llm=_mock_stream_llm,
    )
    return node


@pytest.mark.unit
def test_normalize_page_count() -> None:
    assert rc._normalize_page_count(8) == 8
    assert rc._normalize_page_count("8-12 页") == 10
    assert rc._normalize_page_count("40") == 30


@pytest.mark.unit
def test_style_id_from_label() -> None:
    assert rc._style_id_from_label("商务经典") == ("business-classic", "")
    style_id, desc = rc._style_id_from_label("赛博朋克风")
    assert style_id == "custom"
    assert desc == "赛博朋克风"


@pytest.mark.unit
def test_parse_derive_params_response() -> None:
    parsed = rc._parse_derive_params_response(_DERIVE_RESPONSE)
    assert parsed["search_mode"] == "force_search"
    assert parsed["source_type"] == "topic"
    assert parsed["research_depth"] == "L3"


@pytest.mark.unit
def test_parse_derive_params_response_raises_on_invalid_json() -> None:
    with pytest.raises(rc.RequirementCollectError, match="未返回有效 JSON"):
        rc._parse_derive_params_response("not json")


@pytest.mark.unit
def test_parse_derive_params_response_raises_on_missing_field() -> None:
    with pytest.raises(rc.RequirementCollectError, match="缺少 search_mode"):
        rc._parse_derive_params_response('{"source_type":"topic","research_depth":"L2"}')


@pytest.mark.unit
def test_parse_derive_params_response_raises_on_invalid_enum() -> None:
    with pytest.raises(rc.RequirementCollectError, match="search_mode='bogus'"):
        rc._parse_derive_params_response(
            '{"search_mode":"bogus","source_type":"topic","research_depth":"L2"}'
        )


@pytest.mark.unit
def test_apply_answer_item_page_count() -> None:
    ctx: dict[str, Any] = {}
    sent = rc._build_batch_questions(["page_count"])
    rc._apply_ask_answers(
        ctx,
        [{"question": "需要多少页内容页？（不含封面、结束页）", "selected_options": ["8-12 页"]}],
        sent_questions=sent,
    )
    assert ctx["page_count"] == 10


@pytest.mark.unit
def test_apply_answer_item_page_count_without_question_or_header() -> None:
    """未传 sent_questions 且无 question/header 时不应写入槽位。"""
    ctx: dict[str, Any] = {}
    rc._apply_answer_item(ctx, {"selected_options": ["8-12 页"]})
    assert "page_count" not in ctx


@pytest.mark.unit
def test_parse_topic_suggestions() -> None:
    raw = (
        '{"topics":["2026政府工作报告解读：增长目标与产业政策","2026政府工作报告解读：民生与就业重点",'
        '"2026政府工作报告解读：科技创新与数字经济","2026政府工作报告解读：绿色转型与双碳路径"]}'
    )
    topics = rc._parse_topic_suggestions(raw)
    assert len(topics) == 4
    assert "2026政府工作报告解读：增长目标与产业政策" in topics[0]


@pytest.mark.unit
def test_build_topic_ask_question_from_suggestions() -> None:
    topics = [
        "Q1 销售复盘与增长分析",
        "Q1 区域销售绩效对标",
        "Q1 大客户续约策略",
        "Q1 销售渠道优化建议",
    ]
    question = rc._build_topic_ask_question(topics)
    assert question["header"] == "主题"
    assert len(question["options"]) == 4
    assert question["options"][0]["label"] == topics[0]


@pytest.mark.unit
def test_topic_text_from_ask_answers() -> None:
    assert rc._topic_text_from_ask_answers(
        [{"header": "主题", "custom_input": "2025 年 AI 趋势"}]
    ) == "2025 年 AI 趋势"
    assert rc._topic_text_from_ask_answers(
        [{"header": "主题", "selected_options": ["年度总结汇报与来年规划"]}]
    ) == "年度总结汇报与来年规划"


@pytest.mark.unit
def test_collect_user_text_includes_query() -> None:
    text = rc._collect_user_text({"query": "帮我生成一页华为风格 PPT"})
    assert "帮我生成一页华为风格 PPT" in text


@pytest.mark.unit
def test_collect_user_text_extracts_officeclaw_query_content() -> None:
    wrapped = (
        '你收到一条消息：\n'
        '{"source": "officeclaw", "preferred_response_language": "zh", '
        '"content": "制作一份解读2026年政府工作报告的PPT，华为风格", '
        '"files_updated_by_user": "{}", "type": "user input"}'
    )
    text = rc._collect_user_text({"query": wrapped})
    assert text == "制作一份解读2026年政府工作报告的PPT，华为风格"
    assert "你收到一条消息" not in text


@pytest.mark.unit
def test_build_p21_slot_prompt_includes_user_message_from_query() -> None:
    wrapped = (
        '你收到一条消息：\n'
        '{"source": "officeclaw", "content": "5页以上华为风格政府工作报告PPT", "type": "user input"}'
    )
    prompt = rc._build_p21_slot_prompt(
        rc._collect_user_text({"query": wrapped}),
        "",
        {},
        preserve_topic=False,
    )
    assert "用户消息：" in prompt
    assert "5页以上华为风格政府工作报告PPT" in prompt


@pytest.mark.unit
def test_execute_with_topic_from_p3_single_llm_then_p22_skipped() -> None:
    node = _make_root_node(
        llm_responses=[_SLOT_WITH_TOPIC_PRESET, _DERIVE_RESPONSE],
    )
    ctx = {
        "topic": "2025 AI 趋势",
        "topic_inferred": True,
        "user_message": "请基于附件做 PPT",
    }
    result = asyncio.run(node._execute(ctx))
    assert result["topic"] == "2025 AI 趋势"
    assert result["page_count"] == 8
    assert result["style_id"] == "business-classic"
    assert result["search_mode"] == "force_search"
    assert result["requirement_collect_status"] == "slots_analyzed"


@pytest.mark.unit
def test_execute_no_topic_asks_user_then_uses_selected_topic() -> None:
    slot_no_topic = (
        '{"topic":"","page_count":null,"audience":"","presentation_purpose":"",'
        f'"style_id":"","style_description":"","missing_fields":["topic","page_count","audience",'
        f'"presentation_purpose"],"need_ask_style":true}}'
    )
    topic_suggest = (
        '{"topics":["Q1 销售复盘与增长分析","Q1 区域销售绩效对标",'
        '"Q1 大客户续约策略","Q1 销售渠道优化建议"]}'
    )
    node = _make_root_node(
        llm_responses=[slot_no_topic, topic_suggest, _DERIVE_RESPONSE],
        ask_results=[
            {
                "status": "answered",
                "answers": [
                    {
                        "question": "请选择本次演示的主题方向（每个选项均可直接作为完整 PPT 主题）：",
                        "selected_options": ["Q1 销售复盘与增长分析"],
                    },
                ],
            },
            {
                "status": "answered",
                "answers": [
                    {"question": "需要多少页内容页？（不含封面、结束页）", "selected_options": ["3-6 页（推荐）"]},
                    {"question": "目标受众是谁？", "selected_options": ["企业高管"]},
                    {"question": "这次演示的主要目的是？", "selected_options": ["工作汇报"]},
                ],
            },
            {
                "status": "answered",
                "answers": [
                    {
                        "question": "请选择演示文稿的视觉风格",
                        "selected_options": ["自由发挥"],
                    },
                ],
            },
        ],
    )
    ctx = {"user_message": "帮我做 PPT"}
    result = asyncio.run(node._execute(ctx))
    assert result["topic"] == "Q1 销售复盘与增长分析"
    assert result["topic_user_reply"] == "Q1 销售复盘与增长分析"
    assert result["page_count"] == 6
    assert result["requirement_collect_status"] == "slots_analyzed"


@pytest.mark.unit
def test_execute_no_topic_raises_when_suggestions_insufficient() -> None:
    slot_no_topic = (
        '{"topic":"","page_count":null,"audience":"","presentation_purpose":"",'
        '"style_id":"","style_description":"","missing_fields":["topic"],'
        '"need_ask_style":true}'
    )
    node = _make_root_node(
        llm_responses=[slot_no_topic, '{"topics":["仅一个主题"]}'],
    )
    ctx = {"user_message": "帮我做 PPT"}
    with pytest.raises(rc.RequirementCollectError, match="未能生成 4 个有效主题候选"):
        asyncio.run(node.sub_plans[0]._execute(ctx))


@pytest.mark.unit
def test_execute_no_topic_raises_without_ask_tool() -> None:
    slot_no_topic = (
        '{"topic":"","page_count":null,"audience":"","presentation_purpose":"",'
        '"style_id":"","style_description":"","missing_fields":["topic"],'
        '"need_ask_style":true}'
    )
    node = _make_root_node(llm_responses=[slot_no_topic], has_ask_tool=False)
    ctx = {"user_message": "帮我做 PPT"}
    with pytest.raises(rc.RequirementCollectError, match="ask_user_question"):
        asyncio.run(node.sub_plans[0]._execute(ctx))


@pytest.mark.unit
def test_execute_asks_missing_in_p22_when_topic_known() -> None:
    slot_partial = (
        '{"page_count":null,"audience":"","presentation_purpose":"",'
        '"style_id":"","style_description":"","missing_fields":["page_count","audience",'
        '"presentation_purpose"],"need_ask_style":true}'
    )
    node = _make_root_node(
        llm_responses=[slot_partial, _DERIVE_RESPONSE],
        ask_results=[
            {
                "status": "answered",
                "answers": [
                    {
                        "question": "需要多少页内容页？（不含封面、结束页）",
                        "selected_options": ["3-6 页（推荐）"],
                    },
                    {"question": "目标受众是谁？", "selected_options": ["企业高管"]},
                    {"question": "这次演示的主要目的是？", "selected_options": ["工作汇报"]},
                ],
            },
            {
                "status": "answered",
                "answers": [
                    {
                        "question": "请选择演示文稿的视觉风格",
                        "selected_options": ["工业科技"],
                    },
                ],
            },
        ],
    )
    ctx = {"topic": "Q1 复盘", "user_message": "基于 Q1 复盘做 PPT"}
    result = asyncio.run(node._execute(ctx))
    assert result["topic"] == "Q1 复盘"
    assert result["page_count"] == 6
    assert result["audience"] == "企业高管"
    assert result["presentation_purpose"] == "工作汇报"
    assert result["style_id"] == "industrial-tech"


@pytest.mark.unit
def test_execute_extracts_topic_from_user_message_then_continues() -> None:
    extract_with_topic = (
        '{"topic":"产品发布","page_count":10,"audience":"投资人/客户",'
        '"presentation_purpose":"产品展示","style_id":"","style_description":"",'
        '"missing_fields":[],"need_ask_style":true}'
    )
    node = _make_root_node(
        llm_responses=[extract_with_topic, _DERIVE_RESPONSE],
        ask_results=[
            {
                "status": "answered",
                "answers": [
                    {
                        "question": "请选择演示文稿的视觉风格",
                        "selected_options": ["自由发挥"],
                    },
                ],
            },
        ],
    )
    ctx = {"user_message": "做一份产品发布 PPT，10 页，给投资人看"}
    result = asyncio.run(node._execute(ctx))
    assert result["topic"] == "产品发布"
    assert result["page_count"] == 10
    assert result["style_id"] == "free"


@pytest.mark.unit
def test_prune_satisfied_batch_missing_fields() -> None:
    ctx: dict[str, Any] = {
        "missing_fields": ["page_count", "audience", "style_id"],
        "page_count": 8,
        "audience": "企业高管",
    }
    rc._prune_satisfied_batch_missing_fields(ctx)
    assert ctx["missing_fields"] == ["style_id"]


@pytest.mark.unit
def test_execute_without_ask_tool_raises_on_p22() -> None:
    slot_partial = (
        '{"page_count":null,"audience":"","presentation_purpose":"",'
        '"style_id":"","style_description":"","missing_fields":["page_count","audience",'
        '"presentation_purpose"],"need_ask_style":true}'
    )
    node = _make_root_node(
        llm_responses=[slot_partial, _DERIVE_RESPONSE],
        has_ask_tool=False,
    )
    ctx = {"topic": "产品发布", "user_message": "做产品发布 PPT"}
    with pytest.raises(rc.RequirementCollectError, match="ask_user_question"):
        asyncio.run(node.sub_plans[1]._execute(ctx))


@pytest.mark.unit
def test_p22_raises_when_ask_skipped() -> None:
    slot_partial = (
        '{"page_count":null,"audience":"","presentation_purpose":"",'
        '"style_id":"","style_description":"","missing_fields":["page_count","audience",'
        '"presentation_purpose"],"need_ask_style":true}'
    )
    node = _make_root_node(
        llm_responses=[slot_partial],
        ask_results=[{"status": "skipped", "answers": []}],
    )
    ctx = {"topic": "产品发布", "user_message": "做产品发布 PPT"}
    with pytest.raises(rc.RequirementCollectError, match="批量需求收集未完成"):
        asyncio.run(node.sub_plans[1]._execute(ctx))


@pytest.mark.unit
def test_p23_raises_when_ask_skipped() -> None:
    node = _make_root_node(
        ask_results=[{"status": "skipped", "answers": []}],
    )
    ctx = {
        "topic": "产品发布",
        "page_count": 10,
        "audience": "投资人",
        "presentation_purpose": "产品展示",
        "need_ask_style": True,
    }
    with pytest.raises(rc.RequirementCollectError, match="风格收集未完成"):
        asyncio.run(node.sub_plans[2]._execute(ctx))


@pytest.mark.unit
def test_p23_uses_free_when_style_not_required() -> None:
    node = _make_root_node()
    ctx = {
        "topic": "产品发布",
        "page_count": 10,
        "audience": "投资人",
        "presentation_purpose": "产品展示",
        "need_ask_style": False,
        "missing_fields": [],
    }
    result = asyncio.run(node.sub_plans[2]._execute(ctx))
    assert result["style_id"] == "free"
    assert result["need_ask_style"] is False


@pytest.mark.unit
def test_p22_falls_back_to_llm_when_batch_fields_still_missing_after_ask(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """用户只回答了部分字段时，剩余字段走 LLM 兜底而非 raise。"""
    captured: dict[str, Any] = {}

    async def _fake_llm_default_batch_fields(
        _node, inputs: dict[str, Any], missing_fields: list[str],
    ) -> None:
        captured["missing"] = list(missing_fields)
        if "audience" in missing_fields:
            inputs["audience"] = "技术团队"
        if "presentation_purpose" in missing_fields:
            inputs["presentation_purpose"] = "工作汇报"
        if "page_count" in missing_fields:
            inputs["page_count"] = rc._DEFAULT_PAGE_COUNT

    monkeypatch.setattr(rc, "_llm_default_batch_fields", _fake_llm_default_batch_fields)

    node = _make_root_node(
        ask_results=[
            {
                "status": "answered",
                "answers": [
                    {"question": "需要多少页内容页？（不含封面、结束页）", "selected_options": ["3-6 页（推荐）"]},
                ],
            },
        ],
    )
    ctx = {"topic": "产品发布", "user_message": "做产品发布 PPT"}
    result = asyncio.run(node.sub_plans[1]._execute(ctx))
    assert captured["missing"] == ["audience", "presentation_purpose"]
    assert result["page_count"] == 6
    assert result["audience"] == "技术团队"
    assert result["presentation_purpose"] == "工作汇报"


@pytest.mark.unit
def test_p22_auto_skip_uses_llm_then_static_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Relay-Claw 自动应答（answered + 空 selected_options）触发 LLM 兜底；LLM 给空时回退默认值。"""
    captured: dict[str, Any] = {}

    async def _fake_llm_default_batch_fields(
        _node, inputs: dict[str, Any], missing_fields: list[str],
    ) -> None:
        # 模拟 LLM 解析失败 / 返回空 → 走静态默认值
        captured["missing"] = list(missing_fields)
        if "page_count" in missing_fields:
            inputs["page_count"] = rc._DEFAULT_PAGE_COUNT
        if "audience" in missing_fields:
            inputs["audience"] = rc._DEFAULT_AUDIENCE
        if "presentation_purpose" in missing_fields:
            inputs["presentation_purpose"] = rc._DEFAULT_PRESENTATION_PURPOSE

    monkeypatch.setattr(rc, "_llm_default_batch_fields", _fake_llm_default_batch_fields)

    node = _make_root_node(
        ask_results=[
            {
                "status": "answered",
                "answers": [
                    {"question": "需要多少页内容页？（不含封面、结束页）", "selected_options": []},
                    {"question": "目标受众是谁？", "selected_options": []},
                    {"question": "这次演示的主要目的是？", "selected_options": []},
                ],
            },
        ],
    )
    ctx = {"topic": "产品发布", "user_message": "做产品发布 PPT"}
    result = asyncio.run(node.sub_plans[1]._execute(ctx))
    assert captured["missing"] == ["page_count", "audience", "presentation_purpose"]
    assert result["page_count"] == rc._DEFAULT_PAGE_COUNT
    assert result["audience"] == rc._DEFAULT_AUDIENCE
    assert result["presentation_purpose"] == rc._DEFAULT_PRESENTATION_PURPOSE


@pytest.mark.unit
def test_p23_auto_skip_uses_llm_fallback_style(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Relay-Claw 自动应答时风格走 LLM 兜底。"""

    async def _fake_llm_default_style(_node, _inputs) -> str:
        return "industrial-tech"

    monkeypatch.setattr(rc, "_llm_default_style", _fake_llm_default_style)

    node = _make_root_node(
        ask_results=[
            {
                "status": "answered",
                "answers": [{"question": "请选择演示文稿的视觉风格", "selected_options": []}],
            },
        ],
    )
    ctx = {
        "topic": "产品发布",
        "page_count": 10,
        "audience": "投资人",
        "presentation_purpose": "产品展示",
        "need_ask_style": True,
    }
    result = asyncio.run(node.sub_plans[2]._execute(ctx))
    assert result["style_id"] == "industrial-tech"
    assert result["need_ask_style"] is False


@pytest.mark.unit
def test_p23_auto_skip_falls_back_to_business_classic_when_llm_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM 返回非法 JSON 时风格最终兜底为 'business-classic'。"""

    async def _fake_llm_default_style(_node, _inputs) -> str:
        # 模拟 _llm_default_style 内部 LLM 解析失败 → 落到 'business-classic'
        return "business-classic"

    monkeypatch.setattr(rc, "_llm_default_style", _fake_llm_default_style)

    node = _make_root_node(
        ask_results=[
            {
                "status": "answered",
                "answers": [{"question": "请选择演示文稿的视觉风格", "selected_options": []}],
            },
        ],
    )
    ctx = {
        "topic": "产品发布",
        "page_count": 10,
        "audience": "投资人",
        "presentation_purpose": "产品展示",
        "need_ask_style": True,
    }
    result = asyncio.run(node.sub_plans[2]._execute(ctx))
    assert result["style_id"] == "business-classic"


@pytest.mark.unit
def test_topic_auto_skip_uses_llm_fallback_then_first_option(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """主题自动应答时 LLM 兜底；LLM 返回非法时取候选首项（由 _llm_default_topic 自身兜底）。"""
    captured: dict[str, Any] = {}

    async def _fake_llm_default_topic(_node, _inputs, topic_options: list[str]) -> str:
        captured["options"] = list(topic_options)
        return topic_options[0]

    monkeypatch.setattr(rc, "_llm_default_topic", _fake_llm_default_topic)

    slot_no_topic = (
        '{"topic":"","page_count":null,"audience":"","presentation_purpose":"",'
        '"style_id":"","style_description":"","missing_fields":["topic"],'
        '"need_ask_style":true}'
    )
    topic_suggest = '{"topics":["主题A","主题B","主题C","主题D"]}'
    node = _make_root_node(
        llm_responses=[slot_no_topic, topic_suggest],
        ask_results=[
            {
                "status": "answered",
                "answers": [
                    {
                        "question": "请选择本次演示的主题方向（每个选项均可直接作为完整 PPT 主题）：",
                        "selected_options": [],
                    },
                ],
            },
        ],
    )
    ctx = {"user_message": "帮我做 PPT"}
    result = asyncio.run(node.sub_plans[0]._execute(ctx))
    assert captured["options"] == ["主题A", "主题B", "主题C", "主题D"]
    assert result["topic"] == "主题A"


@pytest.mark.unit
def test_execute_no_topic_falls_back_to_llm_when_user_does_not_select(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """用户在主题选择中只点 "其他"（未填文本）时走 LLM 兜底而非 raise。"""

    async def _fake_llm_default_topic(_node, _inputs, topic_options: list[str]) -> str:
        return topic_options[2]

    monkeypatch.setattr(rc, "_llm_default_topic", _fake_llm_default_topic)

    slot_no_topic = (
        '{"topic":"","page_count":null,"audience":"","presentation_purpose":"",'
        '"style_id":"","style_description":"","missing_fields":["topic"],'
        '"need_ask_style":true}'
    )
    topic_suggest = (
        '{"topics":["主题A","主题B","主题C","主题D"]}'
    )
    node = _make_root_node(
        llm_responses=[slot_no_topic, topic_suggest],
        ask_results=[
            {
                "status": "answered",
                "answers": [{"header": "主题", "selected_options": ["其他"]}],
            },
        ],
    )
    ctx = {"user_message": "帮我做 PPT"}
    result = asyncio.run(node.sub_plans[0]._execute(ctx))
    assert result["topic"] == "主题C"


@pytest.mark.unit
def test_is_auto_skip_helpers() -> None:
    assert rc._is_auto_skip("answered", [{"selected_options": []}]) is True
    assert rc._is_auto_skip("answered", [{"selected_options": ["x"]}]) is False
    assert rc._is_auto_skip("answered", [{"custom_input": "abc"}]) is False
    assert rc._is_auto_skip("skipped", [{"selected_options": []}]) is False
    assert rc._is_auto_skip("answered", []) is False
