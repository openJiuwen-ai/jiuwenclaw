# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
# pylint: disable=protected-access

"""IntentClassifyNode 单元测试。"""

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
    "jiuwenclaw.agentserver.skill_turbo.plan_node",
    _PKG_ROOT / "jiuwenclaw/agentserver/skill_turbo/plan_node.py",
)
ic = _load_module(
    "jiuwenclaw.agentserver.skill_turbo.skill_codes.ppt.intent_classify",
    _PKG_ROOT / "jiuwenclaw/agentserver/skill_turbo/skill_codes/ppt/intent_classify.py",
)


def _make_node_with_llm(responses: list[str]) -> ic.IntentClassifyNode:
    node = ic.IntentClassifyNode()
    queue = list(responses)

    async def _mock_call_llm(prompt: str, system_prompt: str = "", **_) -> str:
        if not queue:
            return '{"doc_paths": []}'
        return queue.pop(0)

    async def _mock_stream_llm(prompt: str, system_prompt: str = "", node_name: str | None = None, **_):
        text = await _mock_call_llm(prompt, system_prompt=system_prompt)
        yield text

    node.set_runtime_callbacks(call_llm=_mock_call_llm, stream_llm=_mock_stream_llm)
    return node


@pytest.mark.unit
def test_collect_attachment_paths_from_strings() -> None:
    inputs = {"attachments": ["D:/docs/report.pdf", "D:/docs/slides.docx"]}
    paths = ic._collect_attachment_paths(inputs)
    assert len(paths) == 2
    assert any(p.replace("\\", "/").endswith("report.pdf") for p in paths)


@pytest.mark.unit
def test_collect_attachment_paths_from_objects() -> None:
    inputs = {"attachments": [{"path": "D:/docs/report.pdf"}]}
    paths = ic._collect_attachment_paths(inputs)
    assert len(paths) == 1


@pytest.mark.unit
def test_collect_files_paths_officeclaw_uploaded() -> None:
    inputs = {
        "files": {
            "uploaded": [
                {
                    "type": "file",
                    "name": "新建 DOCX 文档.docx",
                    "path": "D:/data/uploads/report.docx",
                }
            ]
        }
    }
    paths = ic._collect_files_paths(inputs)
    assert len(paths) == 1
    assert paths[0].replace("\\", "/").endswith("report.docx")


@pytest.mark.unit
def test_collect_files_paths_from_list() -> None:
    inputs = {"files": [{"path": "D:/docs/brief.pdf"}]}
    paths = ic._collect_files_paths(inputs)
    assert len(paths) == 1


@pytest.mark.unit
def test_parse_paths_from_llm_response_object() -> None:
    raw = '{"doc_paths": ["D:/materials/source.md", "briefing.docx"]}'
    paths = ic._parse_paths_from_llm_response(raw)
    assert len(paths) == 2
    assert any(p.endswith("source.md") for p in paths)
    assert any(p.endswith("briefing.docx") for p in paths)


@pytest.mark.unit
def test_parse_paths_from_llm_response_array() -> None:
    raw = '["D:/docs/report.pdf"]'
    paths = ic._parse_paths_from_llm_response(raw)
    assert len(paths) == 1


@pytest.mark.unit
def test_parse_paths_from_llm_response_with_fence() -> None:
    raw = '```json\n{"doc_paths": ["D:/docs/report.pdf"]}\n```'
    paths = ic._parse_paths_from_llm_response(raw)
    assert len(paths) == 1


@pytest.mark.unit
def test_parse_paths_filters_non_document_extensions() -> None:
    raw = '{"doc_paths": ["D:/docs/report.pdf", "D:/docs/script.js"]}'
    paths = ic._parse_paths_from_llm_response(raw)
    assert len(paths) == 1
    assert paths[0].endswith("report.pdf")


@pytest.mark.unit
def test_dedupe_paths() -> None:
    paths = ic._dedupe_paths(["D:/a/report.pdf", "D:/a/report.pdf", "d:/a/report.pdf"])
    assert len(paths) == 1


@pytest.mark.unit
def test_collect_doc_paths_from_attachments_calls_llm_for_extra_paths() -> None:
    """场景 A：有附件时仍调 LLM 提取 query 中额外路径，再合并去重。"""
    node = _make_node_with_llm(['{"doc_paths": []}'])
    inputs = {"attachments": ["D:/docs/report.pdf"], "task": "请做 PPT"}
    paths, slots = asyncio.run(node._collect_doc_paths(inputs))
    assert len(paths) == 1
    assert paths[0].replace("\\", "/").endswith("report.pdf")
    assert slots == {}


@pytest.mark.unit
def test_collect_doc_paths_from_files_calls_llm_for_extra_paths() -> None:
    """场景 A：有 files 时仍调 LLM 提取 query 中额外路径，再合并去重。"""
    node = _make_node_with_llm(['{"doc_paths": []}'])
    inputs = {
        "query": "使用 pptx-craft 技能帮我生成一个ppt",
        "files": {
            "uploaded": [
                {"type": "file", "path": "D:/data/uploads/report.docx"},
            ]
        },
    }
    paths, slots = asyncio.run(node._collect_doc_paths(inputs))
    assert len(paths) == 1
    assert paths[0].replace("\\", "/").endswith("report.docx")
    assert slots == {}


@pytest.mark.unit
def test_collect_doc_paths_merges_attachments_and_files() -> None:
    """场景 A：同时有 attachments 和 files 时合并两者路径。"""
    node = _make_node_with_llm(['{"doc_paths": []}'])
    inputs = {
        "attachments": ["D:/docs/from-attachments.pdf"],
        "files": {"uploaded": [{"path": "D:/docs/from-files.pdf"}]},
    }
    paths, slots = asyncio.run(node._collect_doc_paths(inputs))
    assert len(paths) == 2
    assert any(p.replace("\\", "/").endswith("from-attachments.pdf") for p in paths)
    assert any(p.replace("\\", "/").endswith("from-files.pdf") for p in paths)
    assert slots == {}


@pytest.mark.unit
def test_collect_doc_paths_from_text_via_llm() -> None:
    node = _make_node_with_llm(['{"doc_paths": ["D:/materials/source.md"]}'])
    inputs = {"user_message": "根据 D:/materials/source.md 做一份汇报 PPT"}
    paths, slots = asyncio.run(node._collect_doc_paths(inputs))
    assert len(paths) == 1
    assert paths[0].replace("\\", "/").endswith("source.md")
    assert slots == {}


@pytest.mark.unit
def test_collect_doc_paths_empty_when_llm_returns_empty() -> None:
    node = _make_node_with_llm(['{"doc_paths": []}'])
    inputs = {"task": "帮我做一份关于 AI 趋势的 PPT，8 页"}
    paths, slots = asyncio.run(node._collect_doc_paths(inputs))
    assert paths == []
    # 场景 C：无附件也无路径时 slots 非空（由 _extract_paths_and_slots_with_llm 返回）
    # mock 未提供 slots 数据，故为空


@pytest.mark.unit
def test_intent_classify_node_execute() -> None:
    node = ic.IntentClassifyNode()
    ctx: dict[str, Any] = {"attachments": ["D:/docs/report.pdf"]}
    result = asyncio.run(node._execute(ctx))
    assert result["has_documents"] is True
    assert result["doc_paths"]
