import hashlib
import inspect
import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from jiuwenclaw.agentserver.tools import deepresearch_tools as dt
from jiuwenclaw.agentserver.tools import deepresearch_rewrite_tools as rt


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
    return report, provenance


def _selection() -> dict:
    selected = "原句。"
    selected_bytes = selected.encode("utf-8")
    return {
        "protocol_version": 2,
        "start_byte": 0,
        "end_byte": len(selected_bytes),
        "selected_text": selected,
        "source_sha256": hashlib.sha256(selected_bytes).hexdigest(),
    }


def _structured_result(prepared: dict, text: str = "新句。") -> dict:
    prepared_unit = prepared["units"][0]
    prepared_slot = prepared_unit["slots"][0]
    return {
        "units": [
            {
                "unit_id": prepared_unit["unit_id"],
                "slots": [{"slot_id": prepared_slot["slot_id"], "text": text}],
            }
        ],
        "facts_added": False,
    }


def test_rewrite_tool_schemas_expose_only_protocol_v2_contract():
    assert list(inspect.signature(rt.deepresearch_prepare_rewrite._func).parameters) == [
        "report_path",
        "action",
        "selection",
        "instruction",
    ]
    assert list(inspect.signature(rt.deepresearch_commit_rewrite._func).parameters) == [
        "context_token",
        "structured_result",
    ]
    prepare_card = rt.deepresearch_prepare_rewrite._card
    assert list(prepare_card.input_params["properties"]) == [
        "report_path",
        "action",
        "selection",
        "instruction",
    ]
    assert "Protocol v2" in prepare_card.description
    assert "UTF-8" in prepare_card.description
    assert "byte" in prepare_card.description
    prepare_schema = prepare_card.input_params
    assert prepare_schema["required"] == ["report_path", "action", "selection"]
    assert prepare_schema["additionalProperties"] is False
    assert prepare_schema["properties"]["action"]["enum"] == [
        "polish",
        "expand",
        "shorten",
    ]
    selection_schema = prepare_schema["properties"]["selection"]
    assert selection_schema["required"] == [
        "protocol_version",
        "start_byte",
        "end_byte",
        "selected_text",
        "source_sha256",
    ]
    assert selection_schema["additionalProperties"] is False
    assert selection_schema["properties"]["protocol_version"] == {
        "type": "integer",
        "const": 2,
    }
    assert selection_schema["properties"]["start_byte"] == {
        "type": "integer",
        "minimum": 0,
    }
    assert selection_schema["properties"]["end_byte"] == {
        "type": "integer",
        "minimum": 0,
    }
    assert selection_schema["properties"]["source_sha256"] == {
        "type": "string",
        "pattern": "^[0-9a-f]{64}$",
    }
    commit_card = rt.deepresearch_commit_rewrite._card
    assert list(commit_card.input_params["properties"]) == [
        "context_token",
        "structured_result",
    ]
    assert "units" in commit_card.description
    commit_schema = commit_card.input_params
    assert commit_schema["required"] == ["context_token", "structured_result"]
    assert commit_schema["additionalProperties"] is False
    structured_schema = commit_schema["properties"]["structured_result"]
    assert structured_schema["required"] == ["units", "facts_added"]
    assert structured_schema["additionalProperties"] is False
    assert structured_schema["properties"]["facts_added"] == {
        "type": "boolean",
        "const": False,
    }
    unit_schema = structured_schema["properties"]["units"]["items"]
    assert unit_schema["required"] == ["unit_id", "slots"]
    assert unit_schema["additionalProperties"] is False
    slot_schema = unit_schema["properties"]["slots"]["items"]
    assert slot_schema["required"] == ["slot_id", "text"]
    assert slot_schema["additionalProperties"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("change", "value", "error_code"),
    [
        ("action", "delete", "BAD_REQUEST"),
        ("selection_extra", "sensitive extra", "BAD_REQUEST"),
        ("protocol_version", 1, "SELECTION_PROTOCOL_UNSUPPORTED"),
    ],
)
async def test_prepare_invoke_rejects_contract_violations_before_core(
    change, value, error_code
):
    selection = _selection()
    payload = {
        "report_path": "/sensitive/report.md",
        "action": "polish",
        "selection": selection,
    }
    if change == "selection_extra":
        selection["extra"] = value
    elif change == "protocol_version":
        selection["protocol_version"] = value
    else:
        payload[change] = value

    with patch.object(
        rt, "_get_route", return_value={"session_id": "S1"}
    ), patch.object(
        rt, "get_effective_request_output_dir", return_value="/workspace"
    ), patch.object(
        rt, "prepare_rewrite", side_effect=AssertionError("core called")
    ) as core:
        raw = await rt.deepresearch_prepare_rewrite.invoke(payload)

    assert json.loads(raw)["error_code"] == error_code
    assert "原句" not in raw
    assert "/sensitive/report.md" not in raw
    core.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["structured_extra", "facts_added"])
async def test_commit_invoke_rejects_contract_violations_before_core(change):
    structured_result = {
        "units": [{
            "unit_id": "unit_1",
            "slots": [{"slot_id": "slot_1", "text": "sensitive output"}],
        }],
        "facts_added": False,
    }
    if change == "structured_extra":
        structured_result["extra"] = "sensitive extra"
    else:
        structured_result["facts_added"] = True

    with patch.object(
        rt, "_get_route", return_value={"session_id": "S1"}
    ), patch.object(
        rt, "commit_rewrite", side_effect=AssertionError("core called")
    ) as core:
        raw = await rt.deepresearch_commit_rewrite.invoke({
            "context_token": "sensitive-token",
            "structured_result": structured_result,
        })

    assert json.loads(raw)["error_code"] == "MODEL_OUTPUT_INVALID"
    assert "sensitive" not in raw
    core.assert_not_called()


@pytest.mark.asyncio
async def test_prepare_and_commit_tools_return_short_outcomes_and_deliver_file(tmp_path):
    report, _ = _document(tmp_path)
    selection = _selection()
    route = {"request_id": "R1", "channel_id": "CH1", "session_id": "S1"}
    with patch.object(rt, "_get_route", return_value=route), patch.object(
        rt, "get_effective_request_output_dir", return_value=str(tmp_path)
    ):
        prepared_raw = await rt.deepresearch_prepare_rewrite._func(
            report_path=str(report),
            action="polish",
            selection=selection,
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
                structured_result=_structured_result(prepared),
            )

    committed = json.loads(committed_raw)
    assert prepared["status"] == "prepared"
    assert committed["status"] == "completed"
    assert committed["report_delivered"] is True
    assert committed["delivery_status"] == "delivered"
    assert committed["delivery_error_code"] is None
    assert committed["citation_integrity_status"] == "verified"
    assert committed["citation_semantic_status"] == "not_verified"
    assert "citation_status" not in committed
    assert Path(committed["report_path"]).is_file()
    push.send_push.assert_awaited_once()
    assert push.send_push.await_args.args[0]["payload"]["event_type"] == "chat.file"


@pytest.mark.asyncio
async def test_prepare_core_exception_returns_safe_internal_error(tmp_path, caplog):
    report, _ = _document(tmp_path)
    route = {"session_id": "S1"}
    with patch.object(rt, "_get_route", return_value=route), patch.object(
        rt, "get_effective_request_output_dir", return_value=str(tmp_path)
    ), patch.object(
        rt, "prepare_rewrite", side_effect=RuntimeError("secret /internal/path")
    ), caplog.at_level(logging.ERROR, logger=rt.__name__):
        raw = await rt.deepresearch_prepare_rewrite._func(
            report_path=str(report), action="polish", selection=_selection()
        )

    assert json.loads(raw) == {
        "status": "error",
        "error_code": "INTERNAL_ERROR",
        "error": "rewrite preparation failed",
    }
    assert "secret" not in raw
    assert "/internal/path" not in raw
    assert "secret" not in caplog.text
    assert "/internal/path" not in caplog.text


@pytest.mark.asyncio
async def test_commit_core_exception_returns_safe_write_error_before_delivery(caplog):
    route = {"request_id": "R1", "channel_id": "CH1", "session_id": "S1"}
    delivery = AsyncMock()
    with patch.object(rt, "_get_route", return_value=route), patch.object(
        rt, "commit_rewrite", side_effect=OSError("secret /internal/path")
    ), patch.object(rt, "_deliver_report", delivery), caplog.at_level(
        logging.ERROR, logger=rt.__name__
    ):
        raw = await rt.deepresearch_commit_rewrite._func(
            context_token="token",
            structured_result={
                "units": [{
                    "unit_id": "unit_1",
                    "slots": [{"slot_id": "slot_1", "text": "new"}],
                }],
                "facts_added": False,
            },
        )

    assert json.loads(raw) == {
        "status": "error",
        "error_code": "WRITE_FAILED",
        "error": "rewrite commit failed",
    }
    assert "secret" not in raw
    assert "/internal/path" not in raw
    assert "secret" not in caplog.text
    assert "/internal/path" not in caplog.text
    delivery.assert_not_awaited()


@pytest.mark.asyncio
async def test_commit_without_channel_keeps_completed_revision_and_marks_delivery_failed(tmp_path):
    report, _ = _document(tmp_path)
    route = {"request_id": "R1", "session_id": "S1"}
    with patch.object(rt, "_get_route", return_value=route), patch.object(
        rt, "get_effective_request_output_dir", return_value=str(tmp_path)
    ):
        prepared = json.loads(
            await rt.deepresearch_prepare_rewrite._func(
                report_path=str(report), action="polish", selection=_selection()
            )
        )
        committed = json.loads(
            await rt.deepresearch_commit_rewrite._func(
                context_token=prepared["context_token"],
                structured_result=_structured_result(prepared),
            )
        )
        repeated = json.loads(
            await rt.deepresearch_commit_rewrite._func(
                context_token=prepared["context_token"],
                structured_result=_structured_result(prepared),
            )
        )

    assert committed["status"] == "completed"
    assert committed["report_delivered"] is False
    assert committed["delivery_status"] == "failed"
    assert committed["delivery_error_code"] == "REPORT_DELIVERY_FAILED"
    assert "error_code" not in committed
    assert Path(committed["report_path"]).is_file()
    assert Path(committed["provenance_path"]).is_file()
    assert committed["document_id"] == "doc_test"
    assert committed["revision_id"].startswith("rev_")
    assert committed["citation_integrity_status"] == "verified"
    assert committed["citation_semantic_status"] == "not_verified"
    assert repeated["error_code"] == "CONTEXT_EXPIRED"


@pytest.mark.asyncio
async def test_commit_transport_failure_does_not_mask_completed_revision(tmp_path):
    report, _ = _document(tmp_path)
    route = {"request_id": "R1", "channel_id": "CH1", "session_id": "S1"}
    with patch.object(rt, "_get_route", return_value=route), patch.object(
        rt, "get_effective_request_output_dir", return_value=str(tmp_path)
    ):
        prepared = json.loads(
            await rt.deepresearch_prepare_rewrite._func(
                report_path=str(report), action="polish", selection=_selection()
            )
        )
        push = AsyncMock()
        push.send_push.side_effect = RuntimeError("secret transport detail")
        with patch(
            "jiuwenclaw.agentserver.gateway_push.transport.WebSocketGatewayPushTransport",
            return_value=push,
        ):
            committed_raw = await rt.deepresearch_commit_rewrite._func(
                context_token=prepared["context_token"],
                structured_result=_structured_result(prepared),
            )

    committed = json.loads(committed_raw)
    assert committed["status"] == "completed"
    assert committed["report_delivered"] is False
    assert committed["delivery_status"] == "failed"
    assert committed["delivery_error_code"] == "REPORT_DELIVERY_FAILED"
    assert "error_code" not in committed
    assert "secret transport detail" not in committed_raw
    assert Path(committed["report_path"]).is_file()


@pytest.mark.asyncio
async def test_prepare_passes_protocol_v2_selection_to_core_unchanged(tmp_path):
    report, _ = _document(tmp_path)
    selection = _selection()
    prepared = {"context_token": "T1", "units": []}
    with patch.object(rt, "_get_route", return_value={"session_id": "S1"}), patch.object(
        rt, "get_effective_request_output_dir", return_value=str(tmp_path)
    ), patch.object(rt, "prepare_rewrite", return_value=prepared) as core_prepare:
        raw = await rt.deepresearch_prepare_rewrite._func(
            report_path=str(report),
            action="polish",
            selection=selection,
        )

    assert json.loads(raw) == {"status": "prepared", **prepared}
    assert core_prepare.call_args.kwargs["selection"] is selection


@pytest.mark.asyncio
async def test_prepare_tool_returns_stable_error_without_leaking_selection(tmp_path, caplog):
    report, _ = _document(tmp_path)
    report.write_text("已变化。\n", encoding="utf-8")
    selection = _selection()
    with patch.object(rt, "_get_route", return_value={"session_id": "S1"}), patch.object(
        rt, "get_effective_request_output_dir", return_value=str(tmp_path)
    ), caplog.at_level(logging.INFO, logger=rt.__name__):
        raw = await rt.deepresearch_prepare_rewrite._func(
            report_path=str(report), action="polish", selection=selection
        )
    result = json.loads(raw)
    assert result == {"status": "error", "error_code": "REVISION_CONFLICT", "error": "the report revision changed"}
    assert "原句" not in raw
    assert "原句" not in caplog.text


def test_deepresearch_catalog_includes_stream_and_rewrite_tools(monkeypatch):
    monkeypatch.setattr(dt, "enable_deepresearch", lambda: True)
    monkeypatch.setattr(dt, "_deepresearch_dependency_available", lambda: True)
    assert dt.get_deepresearch_tools() == [
        dt.deepresearch_stream,
        rt.deepresearch_prepare_rewrite,
        rt.deepresearch_commit_rewrite,
    ]
