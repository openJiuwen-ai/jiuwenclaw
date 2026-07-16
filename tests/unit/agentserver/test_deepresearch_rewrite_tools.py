import hashlib
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from jiuwenclaw.agentserver.tools import deepresearch_tools as dt
from jiuwenclaw.agentserver.tools import deepresearch_rewrite_tools as rt
from jiuwenclaw.agentserver.tools.deepresearch_plugin.document_rewrite import iter_rewrite_blocks


def _document(root: Path):
    body = "原句。\n"
    report = root / "report.md"
    report.write_text(body, encoding="utf-8")
    snapshot = {
        "response_content": body,
        "citation_messages": {"code": 0, "msg": "success", "data": []},
        "infer_messages": [],
        "chart_messages": [],
    }
    snapshot_bytes = json.dumps(
        snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    snapshot_path = report.with_suffix(".final-result.json")
    snapshot_path.write_bytes(snapshot_bytes)
    provenance = {
        "schema_version": 2,
        "document_id": "doc_test",
        "revision_id": "rev_parent",
        "parent_revision_id": None,
        "conversation_id": "C1",
        "markdown_path": str(report),
        "content_sha256": hashlib.sha256(body.encode()).hexdigest(),
        "final_result_path": snapshot_path.name,
        "final_result_sha256": hashlib.sha256(snapshot_bytes).hexdigest(),
        "created_at": "2026-07-15T00:00:00+00:00",
        "operation": {"action": "deepresearch_generate"},
        "citations": [],
        "inference_manifest": [],
        "chart_manifest": [],
        "rewrite_history": [],
    }
    report.with_suffix(".provenance.json").write_text(json.dumps(provenance), encoding="utf-8")
    block = next(iter_rewrite_blocks(body))
    return report, provenance, block


@pytest.mark.asyncio
async def test_prepare_and_commit_tools_return_short_outcomes_and_deliver_file(tmp_path):
    report, provenance, block = _document(tmp_path)
    route = {"request_id": "R1", "channel_id": "CH1", "session_id": "S1"}
    with patch.object(rt, "_get_route", return_value=route), patch.object(
        rt, "get_effective_request_output_dir", return_value=str(tmp_path)
    ):
        prepared_raw = await rt.deepresearch_prepare_rewrite._func(
            report_path=str(report),
            document_id=provenance["document_id"],
            revision_id=provenance["revision_id"],
            content_sha256=provenance["content_sha256"],
            action="rewrite",
            block_id=block.block_id,
            start=0,
            end=3,
            selected_text="原句。",
            prefix="",
            suffix="",
            instruction="",
        )
        prepared = json.loads(prepared_raw)
        push = AsyncMock()
        with patch(
            "jiuwenclaw.agentserver.gateway_push.transport.WebSocketGatewayPushTransport",
            return_value=push,
        ):
            committed_raw = await rt.deepresearch_commit_rewrite._func(
                context_token=prepared["context_token"],
                structured_result={
                    "segments": [{"text": "新句。", "source_ids": []}],
                    "facts_added": False,
                },
            )

    committed = json.loads(committed_raw)
    assert prepared["status"] == "prepared"
    assert committed["status"] == "completed"
    assert committed["citation_integrity_status"] == "verified"
    assert committed["citation_semantic_status"] == "not_verified"
    assert "citation_status" not in committed
    assert Path(committed["report_path"]).is_file()
    push.send_push.assert_awaited_once()
    assert push.send_push.await_args.args[0]["payload"]["event_type"] == "chat.file"


@pytest.mark.asyncio
async def test_prepare_tool_returns_stable_error_code_without_leaking_selection(tmp_path):
    report, provenance, block = _document(tmp_path)
    with patch.object(rt, "_get_route", return_value={"session_id": "S1"}), patch.object(
        rt, "get_effective_request_output_dir", return_value=str(tmp_path)
    ):
        raw = await rt.deepresearch_prepare_rewrite._func(
            report_path=str(report),
            document_id=provenance["document_id"],
            revision_id=provenance["revision_id"],
            content_sha256="0" * 64,
            action="rewrite",
            block_id=block.block_id,
            start=0,
            end=3,
            selected_text="原句。",
        )
    result = json.loads(raw)
    assert result == {"status": "error", "error_code": "REVISION_CONFLICT", "error": "the report revision changed"}
    assert "原句" not in raw


def test_deepresearch_catalog_includes_stream_and_rewrite_tools(monkeypatch):
    monkeypatch.setattr(dt, "enable_deepresearch", lambda: True)
    monkeypatch.setattr(dt, "_deepresearch_dependency_available", lambda: True)
    assert dt.get_deepresearch_tools() == [
        dt.deepresearch_stream,
        rt.deepresearch_prepare_rewrite,
        rt.deepresearch_commit_rewrite,
    ]
