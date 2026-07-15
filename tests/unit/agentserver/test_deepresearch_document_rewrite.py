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
    provenance = {
        "schema_version": 1,
        "document_id": "doc_test",
        "revision_id": "rev_parent",
        "parent_revision_id": None,
        "conversation_id": "C1",
        "markdown_path": str(report),
        "content_sha256": hashlib.sha256(body.encode()).hexdigest(),
        "created_at": "2026-07-15T00:00:00+00:00",
        "operation": {"action": "deepresearch_generate"},
        "citations": [{
            "id": 3,
            "reference_index": 1,
            "url": "https://example.com/source",
            "title": "Source",
            "content": "evidence",
            "chunk": "evidence chunk",
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


def test_prepare_and_commit_create_child_revision_without_changing_parent(tmp_path):
    original = "原句需要润色。[[1]](https://example.com/source)\n"
    report, provenance = _write_document(tmp_path, original)

    prepared = _prepare(tmp_path, report, provenance, "原句需要润色。")
    assert prepared["allowed_source_ids"] == ["3"]
    assert prepared["selected_text"] == "原句需要润色。"

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
