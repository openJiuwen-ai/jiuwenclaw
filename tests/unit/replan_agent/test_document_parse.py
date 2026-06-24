# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
# pylint: disable=protected-access

"""DocumentParseNode 单元测试。"""

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
dp = _load_module(
    "jiuwenclaw.agentserver.replan_agent.skill_codes.ppt.document_parse",
    _PKG_ROOT / "jiuwenclaw/agentserver/replan_agent/skill_codes/ppt/document_parse.py",
)


def _mock_write_file(**kwargs: Any) -> dict[str, Any]:
    path = Path(str(kwargs["file_path"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(kwargs["content"], encoding="utf-8")
    return {"success": True, "file_path": str(path)}


def _make_node(
    *,
    read_file_map: dict[str, str] | None = None,
    read_file_calls: list[dict[str, Any]] | None = None,
    image_ocr_map: dict[str, str] | None = None,
    vqa_map: dict[str, str] | None = None,
    tools: set[str] | None = None,
    llm_responses: list[str] | None = None,
) -> dp.DocumentParseNode:
    node = dp.DocumentParseNode()
    read_file_map = read_file_map or {}
    image_ocr_map = image_ocr_map or {}
    vqa_map = vqa_map or {}
    registered = tools or {"read_file", "write_file", "image_ocr", "visual_question_answering"}
    llm_queue = list(llm_responses or [])

    def _has_tool(name: str) -> bool:
        return name in registered

    async def _call_tool(tool_name: str, **kwargs: Any) -> Any:
        if tool_name == "read_file":
            path = str(kwargs.get("file_path", ""))
            if read_file_calls is not None:
                read_file_calls.append(dict(kwargs))
            pages = kwargs.get("pages")
            paged_key = f"{path}|pages={pages}"
            if paged_key in read_file_map:
                return {"content": read_file_map[paged_key]}
            if path in read_file_map:
                return {"content": read_file_map[path]}
            disk_path = Path(path)
            if disk_path.is_file():
                return {"content": disk_path.read_text(encoding="utf-8")}
            return "[ERROR]: file not found"
        if tool_name == "write_file":
            return _mock_write_file(**kwargs)
        if tool_name == "image_ocr":
            path = kwargs.get("image_path_or_url", "")
            return image_ocr_map.get(path, "[ERROR]: ocr failed")
        if tool_name == "visual_question_answering":
            path = kwargs.get("image_path_or_url", "")
            return vqa_map.get(
                path,
                "OCR results:\nfallback ocr text\n\nVQA result:\nok",
            )
        raise ValueError(f"unknown tool: {tool_name}")

    async def _mock_call_llm(prompt: str, system_prompt: str = "") -> str:
        if not llm_queue:
            return '{"topic": ""}'
        return llm_queue.pop(0)

    async def _mock_stream_llm(prompt: str, system_prompt: str = "", node_name: str | None = None, **_):
        text = await _mock_call_llm(prompt, system_prompt=system_prompt)
        yield text

    node.set_runtime_callbacks(
        has_tool=_has_tool,
        use_tool=_call_tool,
        call_llm=_mock_call_llm,
        stream_llm=_mock_stream_llm,
    )
    return node


@pytest.mark.unit
def test_merge_doc_raw_sections() -> None:
    merged = dp._merge_doc_raw_sections(
        [("a.md", "hello"), ("b.txt", "world")]
    )
    assert "# a.md" in merged
    assert "# b.txt" in merged
    assert "---" in merged


@pytest.mark.unit
def test_extract_vqa_ocr_section() -> None:
    raw = "OCR results:\nline one\n\nVQA result:\nanswer"
    assert dp._extract_vqa_ocr_section(raw) == "line one"


@pytest.mark.unit
def test_skip_when_no_documents() -> None:
    node = _make_node()
    ctx: dict[str, Any] = {"has_documents": False}
    result = asyncio.run(node._execute(ctx))
    assert result["doc_parse_ok"] is False
    assert "doc_raw_path" not in result


@pytest.mark.unit
def test_parse_text_file_via_read_file(tmp_path: Path) -> None:
    source = tmp_path / "brief.md"
    source.write_text("# Title\n\nbody", encoding="utf-8")
    output_dir = tmp_path / "session"
    output_dir.mkdir()

    node = _make_node(
        read_file_map={str(source): "# Title\n\nbody"},
        tools={"read_file", "write_file"},
        llm_responses=['{"topic": ""}'],
    )
    ctx = {
        "has_documents": True,
        "output_dir": str(output_dir),
        "doc_paths": [str(source)],
    }
    result = asyncio.run(node._execute(ctx))
    assert result["doc_parse_ok"] is True
    assert result["doc_raw_path"].endswith("doc_raw.md")
    assert "doc_content" not in result
    doc_raw = Path(result["doc_raw_path"])
    assert doc_raw.is_file()
    text = doc_raw.read_text(encoding="utf-8")
    assert "body" in text
    assert "# brief.md" in text


@pytest.mark.unit
def test_parse_large_pdf_reads_in_page_batches(tmp_path: Path) -> None:
    source = tmp_path / "deck.pdf"
    with source.open("wb") as fh:
        fh.seek(10 * 1024 * 1024)
        fh.write(b"\0")
    output_dir = tmp_path / "session"
    output_dir.mkdir()
    calls: list[dict[str, Any]] = []

    node = _make_node(
        read_file_map={
            f"{source}|pages=1-10": "page batch 1",
            f"{source}|pages=11-20": "page batch 2",
            f"{source}|pages=21-30": (
                "[PDF_READ_ERROR] CODE=PDF_PAGE_RANGE_OUT_OF_BOUNDS\n"
                "No more pages."
            ),
        },
        read_file_calls=calls,
        tools={"read_file", "write_file"},
        llm_responses=['{"topic": ""}'],
    )

    ctx = {
        "has_documents": True,
        "output_dir": str(output_dir),
        "doc_paths": [str(source)],
    }
    result = asyncio.run(node._execute(ctx))

    assert result["doc_parse_ok"] is True
    pages = [call.get("pages") for call in calls if call.get("file_path") == str(source)]
    assert pages == ["1-10", "11-20", "21-30"]
    doc_raw = Path(result["doc_raw_path"]).read_text(encoding="utf-8")
    assert "page batch 1" in doc_raw
    assert "page batch 2" in doc_raw
    assert "PDF_PAGE_RANGE_OUT_OF_BOUNDS" not in doc_raw
    assert "[文档解析说明]" not in doc_raw


@pytest.mark.unit
def test_parse_large_pdf_adds_truncation_marker_when_page_cap_reached(tmp_path: Path) -> None:
    source = tmp_path / "huge.pdf"
    with source.open("wb") as fh:
        fh.seek(10 * 1024 * 1024)
        fh.write(b"\0")
    output_dir = tmp_path / "session"
    output_dir.mkdir()
    calls: list[dict[str, Any]] = []

    read_file_map: dict[str, str] = {}
    for start in range(1, dp._PDF_MAX_AUTO_PARSE_PAGES + 1, dp._PDF_BATCH_SIZE):
        end = start + dp._PDF_BATCH_SIZE - 1
        read_file_map[f"{source}|pages={start}-{end}"] = f"batch {start}-{end}"

    node = _make_node(
        read_file_map=read_file_map,
        read_file_calls=calls,
        tools={"read_file", "write_file"},
        llm_responses=['{"topic": ""}'],
    )

    ctx = {
        "has_documents": True,
        "output_dir": str(output_dir),
        "doc_paths": [str(source)],
    }
    result = asyncio.run(node._execute(ctx))

    assert result["doc_parse_ok"] is True
    pdf_calls = [call for call in calls if call.get("file_path") == str(source)]
    assert len(pdf_calls) == dp._PDF_MAX_AUTO_PARSE_PAGES // dp._PDF_BATCH_SIZE
    doc_raw = Path(result["doc_raw_path"]).read_text(encoding="utf-8")
    assert "batch 191-200" in doc_raw
    assert "[文档解析说明]" in doc_raw
    assert str(dp._PDF_MAX_AUTO_PARSE_PAGES) in doc_raw


@pytest.mark.unit
def test_parse_large_pdf_retries_with_smaller_batch_on_token_exceeded(tmp_path: Path) -> None:
    source = tmp_path / "token_heavy.pdf"
    with source.open("wb") as fh:
        fh.seek(10 * 1024 * 1024)
        fh.write(b"\0")
    output_dir = tmp_path / "session"
    output_dir.mkdir()
    calls: list[dict[str, Any]] = []

    token_error = (
        "[PDF_READ_ERROR] CODE=PDF_OUTPUT_TOKEN_EXCEEDED\n"
        "token limit exceeded"
    )
    node = _make_node(
        read_file_map={
            f"{source}|pages=1-10": token_error,
            f"{source}|pages=1-5": "pages 1-5",
            f"{source}|pages=6-10": "pages 6-10",
            f"{source}|pages=11-20": (
                "[PDF_READ_ERROR] CODE=PDF_PAGE_RANGE_OUT_OF_BOUNDS\n"
                "No more pages."
            ),
        },
        read_file_calls=calls,
        tools={"read_file", "write_file"},
        llm_responses=['{"topic": ""}'],
    )

    ctx = {
        "has_documents": True,
        "output_dir": str(output_dir),
        "doc_paths": [str(source)],
    }
    result = asyncio.run(node._execute(ctx))

    assert result["doc_parse_ok"] is True
    pages = [call.get("pages") for call in calls if call.get("file_path") == str(source)]
    assert pages == ["1-10", "1-5", "6-10", "11-20"]
    doc_raw = Path(result["doc_raw_path"]).read_text(encoding="utf-8")
    assert "pages 1-5" in doc_raw
    assert "pages 6-10" in doc_raw


@pytest.mark.unit
def test_parse_image_via_image_ocr(tmp_path: Path) -> None:
    source = tmp_path / "scan.png"
    source.write_bytes(b"\x89PNG")
    output_dir = tmp_path / "session"
    output_dir.mkdir()

    node = _make_node(
        image_ocr_map={str(source): "scanned text from image"},
        tools={"image_ocr", "read_file", "write_file"},
        llm_responses=['{"topic": ""}'],
    )
    ctx = {
        "has_documents": True,
        "output_dir": str(output_dir),
        "doc_paths": [str(source)],
    }
    result = asyncio.run(node._execute(ctx))
    assert result["doc_parse_ok"] is True
    assert "scanned text from image" in Path(result["doc_raw_path"]).read_text(encoding="utf-8")


@pytest.mark.unit
def test_parse_image_falls_back_to_vqa(tmp_path: Path) -> None:
    source = tmp_path / "chart.jpg"
    source.write_bytes(b"fake")
    output_dir = tmp_path / "session"
    output_dir.mkdir()

    node = _make_node(
        image_ocr_map={str(source): "[ERROR]: ocr failed"},
        vqa_map={str(source): "OCR results:\nchart labels\n\nVQA result:\nok"},
        tools={"image_ocr", "visual_question_answering", "read_file", "write_file"},
        llm_responses=['{"topic": ""}'],
    )
    ctx = {
        "has_documents": True,
        "output_dir": str(output_dir),
        "doc_paths": [str(source)],
    }
    result = asyncio.run(node._execute(ctx))
    assert result["doc_parse_ok"] is True
    assert "chart labels" in Path(result["doc_raw_path"]).read_text(encoding="utf-8")


@pytest.mark.unit
def test_parse_failure_when_all_reads_fail(tmp_path: Path) -> None:
    source = tmp_path / "missing.pdf"
    output_dir = tmp_path / "session"
    output_dir.mkdir()

    node = _make_node(
        read_file_map={},
        tools={"read_file", "write_file"},
    )
    ctx = {
        "has_documents": True,
        "output_dir": str(output_dir),
        "doc_paths": [str(source)],
    }
    result = asyncio.run(node._execute(ctx))
    assert result["doc_parse_ok"] is False
    assert result["doc_parse_error"]
    assert result["topic"] == ""
    assert result["topic_inferred"] is False


@pytest.mark.unit
def test_parse_topic_from_llm_response() -> None:
    assert dp._parse_topic_from_llm_response('{"topic": "2025 AI 趋势"}') == "2025 AI 趋势"
    assert dp._parse_topic_from_llm_response('{"topic": ""}') == ""
    assert dp._parse_topic_from_llm_response("invalid") == ""


@pytest.mark.unit
def test_infer_topic_after_parse(tmp_path: Path) -> None:
    source = tmp_path / "report.md"
    source.write_text("# Q1 销售复盘\n\n业绩总结", encoding="utf-8")
    output_dir = tmp_path / "session"
    output_dir.mkdir()

    node = _make_node(
        read_file_map={str(source): "# Q1 销售复盘\n\n业绩总结"},
        tools={"read_file", "write_file"},
        llm_responses=['{"topic": "Q1 销售复盘汇报"}'],
    )
    ctx = {
        "has_documents": True,
        "output_dir": str(output_dir),
        "doc_paths": [str(source)],
        "user_message": "请基于附件做一份汇报 PPT",
    }
    result = asyncio.run(node._execute(ctx))
    assert result["doc_parse_ok"] is True
    assert result["topic"] == "Q1 销售复盘汇报"
    assert result["topic_inferred"] is True


@pytest.mark.unit
def test_skip_topic_inference_when_user_provided(tmp_path: Path) -> None:
    source = tmp_path / "report.md"
    source.write_text("content", encoding="utf-8")
    output_dir = tmp_path / "session"
    output_dir.mkdir()

    node = _make_node(
        read_file_map={str(source): "content"},
        tools={"read_file", "write_file"},
        llm_responses=['{"topic": "should not use"}'],
    )
    ctx = {
        "has_documents": True,
        "output_dir": str(output_dir),
        "doc_paths": [str(source)],
        "topic": "用户已指定主题",
    }
    result = asyncio.run(node._execute(ctx))
    assert result["topic"] == "用户已指定主题"
    assert "topic_inferred" not in result or result.get("topic_inferred") is not True


@pytest.mark.unit
def test_infer_topic_empty_when_llm_returns_empty(tmp_path: Path) -> None:
    source = tmp_path / "report.md"
    source.write_text("???", encoding="utf-8")
    output_dir = tmp_path / "session"
    output_dir.mkdir()

    node = _make_node(
        read_file_map={str(source): "???"},
        tools={"read_file", "write_file"},
        llm_responses=['{"topic": ""}'],
    )
    ctx = {
        "has_documents": True,
        "output_dir": str(output_dir),
        "doc_paths": [str(source)],
    }
    result = asyncio.run(node._execute(ctx))
    assert result["doc_parse_ok"] is True
    assert result["topic"] == ""
    assert result["topic_inferred"] is False
