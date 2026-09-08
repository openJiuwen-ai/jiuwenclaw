from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from jiuwenswarm.agents.harness.common.rails.read_file_validation import (
    is_non_text_file_path,
    validate_read_file_result,
)
from jiuwenswarm.server.runtime.skill_turbo.skill_codes.ppt import document_parse
from jiuwenswarm.server.runtime.skill_turbo.skill_codes.ppt.document_parse import (
    DocumentParseNode,
    _filter_parseable_paths,
    _normalize_tool_text,
)
from jiuwenswarm.server.runtime.skill_turbo.skill_codes.ppt.ppt_gen_root import (
    PPTGenRootNode,
)
from jiuwenswarm.server.runtime.skill_turbo.validator import PlanCodeValidator


def test_document_parse_passes_builtin_skill_validation() -> None:
    source = Path(document_parse.__file__).read_text(encoding="utf-8")
    validator = PlanCodeValidator.for_builtin_skill_code(
        ["jiuwenswarm.server.runtime.skill_turbo.skill_codes"]
    )

    assert validator.validate(source) == []


def test_pdf_is_delegated_to_read_file() -> None:
    assert is_non_text_file_path("report.pdf") is False
    assert validate_read_file_result("report.pdf", "extracted PDF text") == (True, None)


def test_normalize_tool_text_preserves_object_failure() -> None:
    result = {"success": False, "data": None, "error": "read failed"}

    assert _normalize_tool_text(result) == "[ERROR]: read failed"


@pytest.mark.asyncio
async def test_degraded_parse_returns_failure_when_all_reads_fail(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-placeholder")
    node = DocumentParseNode()
    paths = node._artifact_paths(tmp_path)
    inputs: dict[str, Any] = {}

    monkeypatch.setattr(node, "has_tool", lambda _name: True)

    async def call_tool(_name: str, **_kwargs: Any) -> Any:
        return {"success": False, "data": None, "error": "read failed"}

    monkeypatch.setattr(node, "call_tool", call_tool)

    ok, error = await node._degraded_parse(
        inputs, [str(source)], paths, "parse-docs unavailable"
    )

    assert ok is False
    assert error == "降级解析失败: parse-docs unavailable"


@pytest.mark.asyncio
async def test_degraded_parse_writes_summary_and_manifest_on_success(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "source.docx"
    source.write_bytes(b"placeholder")
    node = DocumentParseNode()
    paths = node._artifact_paths(tmp_path)
    inputs: dict[str, Any] = {}

    monkeypatch.setattr(node, "has_tool", lambda _name: True)

    async def call_tool(name: str, **kwargs: Any) -> Any:
        if name == "read_file":
            return {"success": True, "data": {"content": "Document body"}}
        if name == "write_file":
            Path(kwargs["file_path"]).write_text(kwargs["content"], encoding="utf-8")
            return {"success": True}
        raise AssertionError(f"unexpected tool: {name}")

    monkeypatch.setattr(node, "call_tool", call_tool)

    ok, error = await node._degraded_parse(
        inputs, [str(source)], paths, "parse-docs unavailable"
    )

    assert ok is True
    assert error is None
    assert inputs["parse_degraded"] is True
    assert inputs["images_extracted"] is False
    assert paths["raw"].is_file()
    assert paths["summary"].is_file()
    assert paths["manifest"].is_file()
    assert "Document body" in paths["raw"].read_text(encoding="utf-8")
    summary_text = paths["summary"].read_text(encoding="utf-8")
    assert "# 文档摘要（降级）" in summary_text
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    assert manifest["degraded"] is True
    assert manifest["documents"][0]["status"] == "degraded_text"


def test_filter_parseable_paths_excludes_presentations() -> None:
    paths = [
        "report.pdf",
        "notes.docx",
        "slides.pptx",
        "template.PPT",
    ]

    assert _filter_parseable_paths(paths) == ["report.pdf", "notes.docx"]


@pytest.mark.asyncio
async def test_execute_marks_presentation_only_inputs_as_unparseable(tmp_path: Path) -> None:
    node = DocumentParseNode()
    inputs = {
        "has_documents": True,
        "output_dir": str(tmp_path),
        "doc_paths": [str(tmp_path / "slides.pptx")],
    }

    result = await node._execute(inputs)

    assert result["doc_parse_ok"] is False
    assert result["doc_parse_error"] == "无可解析文档（演示文稿不进入 parse-docs）"
    assert result["has_documents"] is False


@pytest.mark.asyncio
async def test_root_stops_when_all_documents_fail(monkeypatch) -> None:
    root = PPTGenRootNode()
    calls: list[str] = []

    async def run_subplan(subplan, inputs, results) -> None:
        calls.append(subplan.plan_name)
        if subplan is root._p1:
            inputs["has_documents"] = True
        if subplan is root._p3:
            inputs["doc_parse_ok"] = False
            inputs["doc_parse_error"] = "all reads failed"
        results.append({"node": subplan.plan_name, "status": "ok"})

    monkeypatch.setattr(root, "_run_subplan", run_subplan)

    result = await root._execute({})

    assert result["status"] == "error"
    assert "all reads failed" in result["message"]
    assert calls == ["p0_pipeline_init", "p1_intent_classify", "p3_document_parse"]


@pytest.mark.asyncio
async def test_stream_root_stops_when_all_documents_fail(monkeypatch) -> None:
    root = PPTGenRootNode()
    calls: list[str] = []

    async def should_skip(_subplan, _inputs) -> bool:
        return False

    async def run_subplan_stream(subplan, inputs, results, **_kwargs):
        calls.append(subplan.plan_name)
        if subplan is root._p1:
            inputs["has_documents"] = True
        if subplan is root._p3:
            inputs["doc_parse_ok"] = False
            inputs["doc_parse_error"] = "all reads failed"
        result = {"node": subplan.plan_name, "status": "ok"}
        results.append({"node": subplan.plan_name, "status": "ok", "result": result})
        yield result

    monkeypatch.setattr(root, "should_skip_subplan", should_skip)
    monkeypatch.setattr(root, "_run_subplan_stream", run_subplan_stream)

    chunks = [chunk async for chunk in root._execute_stream({})]

    assert chunks[-1]["status"] == "error"
    assert "all reads failed" in chunks[-1]["message"]
    assert calls == ["p0_pipeline_init", "p1_intent_classify", "p3_document_parse"]
