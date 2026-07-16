import hashlib
import json
from pathlib import Path

import pytest

from jiuwenclaw.agentserver.tools.deepresearch_plugin.document_rewrite import (
    RewriteError,
    commit_rewrite,
    iter_rewrite_blocks,
    prepare_rewrite,
)


def _write_document(root: Path, body: str) -> tuple[Path, dict]:
    report = root / "report.md"
    report.write_text(body, encoding="utf-8")
    authoritative_citation = {
        "id": 3,
        "reference_index": 1,
        "url": "https://example.com/source",
        "title": "Source",
        "content": "authoritative snapshot evidence",
        "chunk": "authoritative snapshot chunk",
        "source": "web",
    }
    snapshot = {
        "response_content": body,
        "citation_messages": {"code": 0, "msg": "success", "data": [authoritative_citation]},
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
        "citations": [{
            "id": 3,
            "reference_index": 1,
            "url": "https://example.com/source",
            "title": "Source",
            "content": "stale sidecar evidence",
            "chunk": "stale sidecar chunk",
            "source": "web",
        }],
        "inference_manifest": [],
        "chart_manifest": [],
        "rewrite_history": [],
    }
    report.with_suffix(".provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False), encoding="utf-8"
    )
    return report, provenance


def _prepare(root: Path, report: Path, provenance: dict, selected: str):
    block = next(iter_rewrite_blocks(report.read_text(encoding="utf-8")))
    start = block.text.index(selected)
    return prepare_rewrite(
        workspace_root=root,
        report_path=report,
        document_id=provenance["document_id"],
        revision_id=provenance["revision_id"],
        content_sha256=provenance["content_sha256"],
        action_category="synonym_rewrite",
        action="polish",
        block_id=block.block_id,
        start=start,
        end=start + len(selected),
        selected_text=selected,
        prefix=block.text[max(0, start - 16):start],
        suffix=block.text[start + len(selected):start + len(selected) + 16],
        instruction="更清晰",
        session_id="S1",
    )


def test_block_id_matches_frontend_utf8_fnv_contract():
    block = next(iter_rewrite_blocks("中文 block\n"))
    assert block.block_id == "block_0_b7c45b70"


def test_prepare_rejects_non_synonym_rewrite_category(tmp_path):
    report, provenance = _write_document(tmp_path, "原句。\n")
    block = next(iter_rewrite_blocks(report.read_text(encoding="utf-8")))
    with pytest.raises(RewriteError) as caught:
        prepare_rewrite(
            workspace_root=tmp_path,
            report_path=report,
            document_id=provenance["document_id"],
            revision_id=provenance["revision_id"],
            content_sha256=provenance["content_sha256"],
            action_category="supplementary_search",
            action="polish",
            block_id=block.block_id,
            start=0,
            end=3,
            selected_text="原句。",
            session_id="S1",
        )
    assert caught.value.code == "BAD_REQUEST"


def test_prepare_and_commit_create_child_revision_without_changing_parent(tmp_path):
    original = "原句需要润色。[[1]](https://example.com/source)\n"
    report, provenance = _write_document(tmp_path, original)

    prepared = _prepare(tmp_path, report, provenance, "原句需要润色。")
    assert prepared["allowed_source_ids"] == ["3"]
    assert prepared["selected_text"] == "原句需要润色。"
    assert prepared["citation_evidence"][0]["content"] == "authoritative snapshot evidence"

    result = commit_rewrite(
        context_token=prepared["context_token"],
        session_id="S1",
        structured_result={
            "segments": [{"text": "这句话的表达更加清晰。", "source_ids": ["3"]}],
            "facts_added": False,
        },
    )

    assert report.read_text(encoding="utf-8") == original
    child = Path(result["report_path"])
    assert child != report
    assert "这句话的表达更加清晰。[[1]](https://example.com/source)" in child.read_text(encoding="utf-8")
    child_provenance = json.loads(Path(result["provenance_path"]).read_text(encoding="utf-8"))
    assert child_provenance["parent_revision_id"] == "rev_parent"
    assert child_provenance["operation"]["action"] == "polish"
    assert result["citation_integrity_status"] == "verified"
    assert result["citation_semantic_status"] == "not_verified"
    assert "citation_status" not in result


def test_child_revision_can_be_rewritten_again(tmp_path):
    report, provenance = _write_document(tmp_path, "原句。\n")
    prepared = _prepare(tmp_path, report, provenance, "原句。")
    first = commit_rewrite(
        context_token=prepared["context_token"],
        session_id="S1",
        structured_result={
            "segments": [{"text": "第一版句子。", "source_ids": []}],
            "facts_added": False,
        },
    )

    first_report = Path(first["report_path"])
    first_provenance = json.loads(Path(first["provenance_path"]).read_text(encoding="utf-8"))
    second_prepared = _prepare(tmp_path, first_report, first_provenance, "第一版句子。")
    second = commit_rewrite(
        context_token=second_prepared["context_token"],
        session_id="S1",
        structured_result={
            "segments": [{"text": "第二版句子。", "source_ids": []}],
            "facts_added": False,
        },
    )

    second_provenance = json.loads(Path(second["provenance_path"]).read_text(encoding="utf-8"))
    assert first_provenance["final_result_path"] == provenance["final_result_path"]
    assert second_provenance["final_result_sha256"] == provenance["final_result_sha256"]
    assert second_provenance["parent_revision_id"] == first_provenance["revision_id"]
    assert len(second_provenance["rewrite_history"]) == 2


@pytest.mark.parametrize(
    ("structured_result", "code"),
    [
        ({"segments": [{"text": "新增", "source_ids": ["99"]}], "facts_added": False}, "MODEL_OUTPUT_INVALID"),
        ({"segments": [{"text": "访问 https://evil.example", "source_ids": []}], "facts_added": False}, "MODEL_OUTPUT_INVALID"),
        ({"segments": [{"text": "新增", "source_ids": []}], "facts_added": True}, "MODEL_OUTPUT_INVALID"),
    ],
)
def test_commit_rejects_unsafe_model_output(tmp_path, structured_result, code):
    report, provenance = _write_document(tmp_path, "原句。[[1]](https://example.com/source)\n")
    prepared = _prepare(tmp_path, report, provenance, "原句。")

    with pytest.raises(RewriteError) as caught:
        commit_rewrite(
            context_token=prepared["context_token"],
            session_id="S1",
            structured_result=structured_result,
        )
    assert caught.value.code == code


def test_prepare_rejects_inference_block(tmp_path):
    report, provenance = _write_document(tmp_path, "[结论](#inference:7)需要润色。\n")
    with pytest.raises(RewriteError) as caught:
        _prepare(tmp_path, report, provenance, "需要润色。")
    assert caught.value.code == "INFERENCE_REWRITE_UNSUPPORTED"


def test_prepare_rejects_rendered_inference_resource_link(tmp_path):
    body = "[结论](report_infer/inference_7.html)需要润色。\n"
    report, provenance = _write_document(tmp_path, body)
    provenance["inference_manifest"] = [{
        "id": "7",
        "path": "report_infer/inference_7.html",
        "sha256": "a" * 64,
    }]
    report.with_suffix(".provenance.json").write_text(json.dumps(provenance), encoding="utf-8")

    with pytest.raises(RewriteError) as caught:
        _prepare(tmp_path, report, provenance, "需要润色。")
    assert caught.value.code == "INFERENCE_REWRITE_UNSUPPORTED"


def test_prepare_rejects_stale_hash_and_workspace_escape(tmp_path):
    report, provenance = _write_document(tmp_path, "原句。\n")
    provenance["content_sha256"] = "0" * 64
    with pytest.raises(RewriteError) as caught:
        _prepare(tmp_path, report, provenance, "原句。")
    assert caught.value.code == "REVISION_CONFLICT"

    outside = tmp_path.parent / "outside-report.md"
    outside.write_text("原句。\n", encoding="utf-8")
    outside_provenance = dict(provenance, content_sha256=hashlib.sha256("原句。\n".encode()).hexdigest())
    outside.with_suffix(".provenance.json").write_text(json.dumps(outside_provenance), encoding="utf-8")
    with pytest.raises(RewriteError) as caught:
        _prepare(tmp_path, outside, outside_provenance, "原句。")
    assert caught.value.code == "BAD_REQUEST"


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("missing", "DOCUMENT_NOT_FOUND"),
        ("changed", "REVISION_CONFLICT"),
        ("malformed", "DOCUMENT_NOT_FOUND"),
    ],
)
def test_prepare_rejects_invalid_final_result_snapshot(tmp_path, mutation, expected_code):
    report, provenance = _write_document(tmp_path, "原句。\n")
    snapshot_path = report.with_name(provenance["final_result_path"])
    if mutation == "missing":
        snapshot_path.unlink()
    elif mutation == "changed":
        snapshot_path.write_text("{}", encoding="utf-8")
    else:
        malformed = {"response_content": "原句。", "citation_messages": {"data": "invalid"}}
        snapshot_bytes = json.dumps(
            malformed, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        snapshot_path.write_bytes(snapshot_bytes)
        provenance["final_result_sha256"] = hashlib.sha256(snapshot_bytes).hexdigest()
        report.with_suffix(".provenance.json").write_text(json.dumps(provenance), encoding="utf-8")

    with pytest.raises(RewriteError) as caught:
        _prepare(tmp_path, report, provenance, "原句。")
    assert caught.value.code == expected_code


def test_prepare_rejects_final_result_snapshot_outside_workspace(tmp_path):
    report, provenance = _write_document(tmp_path, "原句。\n")
    outside = tmp_path.parent / "outside-final-result.json"
    outside.write_text("{}", encoding="utf-8")
    provenance["final_result_path"] = str(outside)
    provenance["final_result_sha256"] = hashlib.sha256(outside.read_bytes()).hexdigest()
    report.with_suffix(".provenance.json").write_text(json.dumps(provenance), encoding="utf-8")

    with pytest.raises(RewriteError) as caught:
        _prepare(tmp_path, report, provenance, "原句。")
    assert caught.value.code == "BAD_REQUEST"


def test_context_token_is_bound_to_session_and_single_use(tmp_path):
    report, provenance = _write_document(tmp_path, "原句。\n")
    prepared = _prepare(tmp_path, report, provenance, "原句。")
    payload = {"segments": [{"text": "新句。", "source_ids": []}], "facts_added": False}

    with pytest.raises(RewriteError) as caught:
        commit_rewrite(context_token=prepared["context_token"], session_id="S2", structured_result=payload)
    assert caught.value.code == "CONTEXT_EXPIRED"

    commit_rewrite(context_token=prepared["context_token"], session_id="S1", structured_result=payload)
    with pytest.raises(RewriteError) as caught:
        commit_rewrite(context_token=prepared["context_token"], session_id="S1", structured_result=payload)
    assert caught.value.code == "CONTEXT_EXPIRED"
