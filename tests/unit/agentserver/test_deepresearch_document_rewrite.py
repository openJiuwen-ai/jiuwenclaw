import hashlib
import json
from pathlib import Path

import pytest

from jiuwenclaw.agentserver.tools.deepresearch_plugin import document_rewrite as rewrite_module
from jiuwenclaw.agentserver.tools.deepresearch_plugin.document_rewrite import (
    RewriteError,
    commit_rewrite,
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
        "secret": "must not escape",
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
        "citations": [],
        "inference_manifest": [],
        "chart_manifest": [],
        "rewrite_history": [],
    }
    report.with_suffix(".provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False), encoding="utf-8"
    )
    return report, provenance


def _selection(body: str, raw: str, visible: str | None = None, occurrence: int = 1) -> dict:
    cursor = -1
    for _ in range(occurrence):
        cursor = body.index(raw, cursor + 1)
    start = len(body[:cursor].encode("utf-8"))
    end = start + len(raw.encode("utf-8"))
    return {
        "protocol_version": 2,
        "start_byte": start,
        "end_byte": end,
        "selected_text": raw if visible is None else visible,
        "source_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
    }


def _prepare(
    root: Path,
    report: Path,
    raw: str,
    *,
    visible: str | None = None,
    occurrence: int = 1,
    action: str = "shorten",
):
    body = report.read_text(encoding="utf-8")
    return prepare_rewrite(
        workspace_root=root,
        report_path=report,
        action=action,
        selection=_selection(body, raw, visible, occurrence),
        instruction="更清晰",
        session_id="S1",
    )


@pytest.mark.parametrize(
    "selection",
    [None, [], {}, {"protocol_version": 1}, {"protocol_version": True}, {"protocol_version": "2"},
     {"protocol_version": 2.0}, {"protocol_version": 2, "extra": "ignored"}],
)
def test_prepare_rejects_unsupported_selection_protocol(tmp_path, selection):
    report, _ = _write_document(tmp_path, "text\n")
    if isinstance(selection, dict) and selection.get("protocol_version") == 2:
        selection = dict(selection, start_byte=0, end_byte=4, selected_text="text", source_sha256="0" * 64)
        selection.pop("source_sha256")
    with pytest.raises(RewriteError) as caught:
        prepare_rewrite(
            workspace_root=tmp_path, report_path=report, action="polish",
            selection=selection, session_id="S1",
        )
    expected = "SELECTION_PROTOCOL_UNSUPPORTED" if not isinstance(selection, dict) or type(selection.get("protocol_version")) is not int or selection.get("protocol_version") != 2 else "SELECTION_MAPPING_CONFLICT"
    assert caught.value.code == expected


@pytest.mark.parametrize(
    ("change", "value"),
    [("start_byte", True), ("end_byte", 1.5), ("start_byte", -1),
     ("end_byte", 99), ("end_byte", 0), ("source_sha256", "bad"),
     ("selected_text", 7)],
)
def test_prepare_rejects_invalid_selection_mapping_fields(tmp_path, change, value):
    report, _ = _write_document(tmp_path, "A😀中\n")
    selection = _selection("A😀中\n", "A")
    selection[change] = value
    with pytest.raises(RewriteError) as caught:
        prepare_rewrite(
            workspace_root=tmp_path, report_path=report, action="polish",
            selection=selection, session_id="S1",
        )
    assert caught.value.code == "SELECTION_MAPPING_CONFLICT"


@pytest.mark.parametrize("offset", [2, 3, 4])
def test_prepare_rejects_utf8_mid_codepoint_boundary(tmp_path, offset):
    report, _ = _write_document(tmp_path, "A😀中\n")
    selection = _selection("A😀中\n", "😀")
    selection["start_byte"] = offset
    with pytest.raises(RewriteError) as caught:
        prepare_rewrite(
            workspace_root=tmp_path, report_path=report, action="polish",
            selection=selection, session_id="S1",
        )
    assert caught.value.code == "SELECTION_MAPPING_CONFLICT"


def test_prepare_uses_global_utf8_range_and_second_duplicate_occurrence(tmp_path):
    body = "😀 e\u0301 same\n\nsame second\n"
    report, _ = _write_document(tmp_path, body)
    prepared = _prepare(tmp_path, report, "same", occurrence=2)
    assert prepared["units"][0]["slots"] == [{
        "slot_id": prepared["units"][0]["slots"][0]["slot_id"],
        "text": "same", "format": [],
    }]
    assert prepared["readonly_context"] == {
        "previous_unit": "😀 e\u0301 same", "next_unit": None,
    }


def test_context_stores_exact_selected_unit_and_slot_byte_ranges(tmp_path):
    body = "prefix selected suffix\n"
    report, _ = _write_document(tmp_path, body)
    prepared = _prepare(tmp_path, report, "selected")
    context = rewrite_module._CONTEXTS[prepared["context_token"]]
    selected_unit = context.selected_units[0]
    assert (selected_unit.start_byte, selected_unit.end_byte) == (
        len("prefix ".encode()),
        len("prefix selected".encode()),
    )
    assert (
        selected_unit.slots[0].start_byte,
        selected_unit.slots[0].end_byte,
    ) == (selected_unit.start_byte, selected_unit.end_byte)


@pytest.mark.parametrize("selected", ["😀", "e\u0301", "中"])
def test_prepare_preserves_emoji_combining_and_cjk_visible_boundaries(tmp_path, selected):
    body = "prefix 😀 e\u0301 中 suffix\n"
    report, _ = _write_document(tmp_path, body)
    prepared = _prepare(tmp_path, report, selected)
    assert prepared["units"][0]["slots"][0]["text"] == selected


@pytest.mark.parametrize("field", ["source_sha256", "selected_text"])
def test_prepare_rejects_hash_or_visible_text_mismatch(tmp_path, field):
    report, _ = _write_document(tmp_path, "first second\n")
    selection = _selection("first second\n", "second")
    selection[field] = "0" * 64 if field == "source_sha256" else "first"
    with pytest.raises(RewriteError) as caught:
        prepare_rewrite(
            workspace_root=tmp_path, report_path=report, action="polish",
            selection=selection, session_id="S1",
        )
    assert caught.value.code == "SELECTION_MAPPING_CONFLICT"


def test_prepare_normalizes_visible_markdown_and_returns_partial_slots(tmp_path):
    body = "before **bold** [label](https://ordinary.example) soft\nbreak [[1]](https://example.com/source) hard  \nline `code` after\n"
    report, _ = _write_document(tmp_path, body)
    raw = "bold** [label](https://ordinary.example) soft\nbreak [[1]](https://example.com/source) hard  \nline `code` after"
    visible = "bold label soft break [1] hard\nline code after"
    prepared = _prepare(tmp_path, report, raw, visible=visible)

    slots = prepared["units"][0]["slots"]
    assert "".join(slot["text"] for slot in slots) == "bold label soft break  hardline  after"
    assert slots[0]["format"] == ["strong"]
    assert any(slot.get("link_id") for slot in slots)
    assert prepared["allowed_source_ids"] == ["3"]
    assert prepared["citation_evidence"] == [{
        "id": 3, "title": "Source", "content": "authoritative snapshot evidence",
        "chunk": "authoritative snapshot chunk", "source": "web",
    }]
    assert "selected_text" not in prepared
    assert "block_context" not in prepared
    assert "report_path" not in prepared


def test_prepare_normalizes_multiple_citations_without_duplicate_evidence(tmp_path):
    body = "left [[1]](https://example.com/source) middle [[1]](https://example.com/source) right\n"
    report, _ = _write_document(tmp_path, body)
    prepared = _prepare(
        tmp_path,
        report,
        body.rstrip("\n"),
        visible="left [1] middle [1] right",
    )
    assert prepared["allowed_source_ids"] == ["3"]
    assert len(prepared["citation_evidence"]) == 1


@pytest.mark.parametrize(
    ("body", "raw", "code"),
    [
        ("before\n\n> quote\n\nafter\n", "before\n\n> quote\n\nafter", "UNSUPPORTED_SELECTION"),
        ("before ![alt](image.png) after\n", "before ![alt](image.png) after", "UNSUPPORTED_SELECTION"),
        ("before [claim](#inference:7) after\n", "before [claim](#inference:7) after", "INFERENCE_REWRITE_UNSUPPORTED"),
    ],
)
def test_prepare_rejects_unsupported_image_and_inference_intersections(tmp_path, body, raw, code):
    report, _ = _write_document(tmp_path, body)
    with pytest.raises(RewriteError) as caught:
        _prepare(tmp_path, report, raw)
    assert caught.value.code == code


def test_prepare_rejects_inference_manifest_ordinary_link_destination(tmp_path):
    body = "before [claim](report_infer/inference_7.html) after\n"
    report, provenance = _write_document(tmp_path, body)
    provenance["inference_manifest"] = [{"id": "7", "path": "report_infer/inference_7.html", "sha256": "a" * 64}]
    report.with_suffix(".provenance.json").write_text(json.dumps(provenance), encoding="utf-8")
    with pytest.raises(RewriteError) as caught:
        _prepare(tmp_path, report, "before [claim](report_infer/inference_7.html) after", visible="before claim after")
    assert caught.value.code == "INFERENCE_REWRITE_UNSUPPORTED"


@pytest.mark.parametrize(
    "raw",
    ["**", "https://ordinary.example", "`", "![alt]"],
)
def test_prepare_rejects_endpoint_inside_protected_source(tmp_path, raw):
    body = "**bold** [label](https://ordinary.example) `code` ![alt](x.png)\n"
    report, _ = _write_document(tmp_path, body)
    with pytest.raises(RewriteError) as caught:
        _prepare(tmp_path, report, raw)
    assert caught.value.code == "UNSUPPORTED_SELECTION"


def test_prepare_allows_partial_outer_units_and_requires_full_middle_unit(tmp_path):
    body = "first alpha\n\nmiddle whole\n\nlast omega\n"
    report, _ = _write_document(tmp_path, body)
    raw = "alpha\n\nmiddle whole\n\nlast"
    prepared = _prepare(tmp_path, report, raw, visible="alpha\nmiddle whole\nlast")
    assert [unit["type"] for unit in prepared["units"]] == ["paragraph", "paragraph", "paragraph"]
    assert ["".join(slot["text"] for slot in unit["slots"]) for unit in prepared["units"]] == ["alpha", "middle whole", "last"]

def test_prepare_list_units_keep_metadata_and_reject_noncontinuous_depth(tmp_path):
    body = "- alpha\n- beta\n"
    report, _ = _write_document(tmp_path, body)
    prepared = _prepare(tmp_path, report, "alpha\n- beta", visible="alpha\nbeta")
    assert [(unit["type"], unit["list_depth"], unit["list_marker"]) for unit in prepared["units"]] == [
        ("list_item", 0, "-"), ("list_item", 0, "-"),
    ]

    nested, _ = _write_document(tmp_path, "- outer\n  - inner\n")
    with pytest.raises(RewriteError) as caught:
        _prepare(tmp_path, nested, "outer\n  - inner", visible="outer\ninner")
    assert caught.value.code == "UNSUPPORTED_SELECTION"


def test_readonly_neighbors_do_not_expand_citation_allowlist(tmp_path):
    body = "previous [[1]](https://example.com/source)\n\ntarget\n\nnext **safe**\n"
    report, _ = _write_document(tmp_path, body)
    prepared = _prepare(tmp_path, report, "target")
    assert prepared["readonly_context"] == {"previous_unit": "previous [1]", "next_unit": "next safe"}
    assert prepared["allowed_source_ids"] == []
    assert prepared["citation_evidence"] == []


def test_prepare_and_commit_simple_unit_remain_compatible(tmp_path):
    original = "原句需要润色。[[1]](https://example.com/source)\n"
    report, provenance = _write_document(tmp_path, original)
    prepared = _prepare(tmp_path, report, "原句需要润色。")
    result = commit_rewrite(
        context_token=prepared["context_token"], session_id="S1",
        structured_result={"segments": [{"text": "这句话更加清晰。", "source_ids": []}], "facts_added": False},
    )
    child = Path(result["report_path"])
    assert report.read_text(encoding="utf-8") == original
    assert "这句话更加清晰。[[1]]" in child.read_text(encoding="utf-8")
    child_provenance = json.loads(Path(result["provenance_path"]).read_text(encoding="utf-8"))
    assert child_provenance["parent_revision_id"] == provenance["revision_id"]


def test_prepare_rejects_legacy_action_stale_hash_and_workspace_escape(tmp_path):
    report, provenance = _write_document(tmp_path, "原句。\n")
    with pytest.raises(RewriteError) as caught:
        _prepare(tmp_path, report, "原句。", action="rewrite")
    assert caught.value.code == "BAD_REQUEST"
    provenance["content_sha256"] = "0" * 64
    report.with_suffix(".provenance.json").write_text(json.dumps(provenance), encoding="utf-8")
    with pytest.raises(RewriteError) as caught:
        _prepare(tmp_path, report, "原句。")
    assert caught.value.code == "REVISION_CONFLICT"

    outside = tmp_path.parent / "outside-report.md"
    outside.write_text("原句。\n", encoding="utf-8")
    with pytest.raises(RewriteError) as caught:
        prepare_rewrite(workspace_root=tmp_path, report_path=outside, action="polish", selection=_selection("原句。\n", "原句。"), session_id="S1")
    assert caught.value.code == "BAD_REQUEST"


def test_prepare_preserves_file_and_size_limits(tmp_path):
    report, _ = _write_document(tmp_path, "text\n")
    with pytest.raises(RewriteError) as caught:
        prepare_rewrite(
            workspace_root=tmp_path, report_path=report.with_suffix(".txt"), action="polish",
            selection=_selection("text\n", "text"), session_id="S1",
        )
    assert caught.value.code == "BAD_REQUEST"

    with pytest.raises(RewriteError) as caught:
        prepare_rewrite(
            workspace_root=tmp_path, report_path=report, action="polish",
            selection=_selection("text\n", "text"), instruction="x" * 2001, session_id="S1",
        )
    assert caught.value.code == "BAD_REQUEST"

    long_body = "x" * 12001 + "\n"
    long_report, _ = _write_document(tmp_path, long_body)
    with pytest.raises(RewriteError) as caught:
        _prepare(tmp_path, long_report, long_body.rstrip("\n"))
    assert caught.value.code == "BAD_REQUEST"


@pytest.mark.parametrize(("mutation", "code"), [("missing", "DOCUMENT_NOT_FOUND"), ("changed", "REVISION_CONFLICT"), ("malformed", "DOCUMENT_NOT_FOUND")])
def test_prepare_rejects_stale_final_result(tmp_path, mutation, code):
    report, provenance = _write_document(tmp_path, "text\n")
    snapshot = report.with_name(provenance["final_result_path"])
    if mutation == "missing":
        snapshot.unlink()
    elif mutation == "changed":
        snapshot.write_text("{}", encoding="utf-8")
    else:
        snapshot.write_text(json.dumps({"citation_messages": {"data": "bad"}}), encoding="utf-8")
        provenance["final_result_sha256"] = hashlib.sha256(snapshot.read_bytes()).hexdigest()
        report.with_suffix(".provenance.json").write_text(json.dumps(provenance), encoding="utf-8")
    with pytest.raises(RewriteError) as caught:
        _prepare(tmp_path, report, "text")
    assert caught.value.code == code


def test_prepare_rejects_final_result_outside_workspace(tmp_path):
    report, provenance = _write_document(tmp_path, "text\n")
    outside = tmp_path.parent / "outside-final-result.json"
    outside.write_text("{}", encoding="utf-8")
    provenance["final_result_path"] = str(outside)
    provenance["final_result_sha256"] = hashlib.sha256(outside.read_bytes()).hexdigest()
    report.with_suffix(".provenance.json").write_text(json.dumps(provenance), encoding="utf-8")
    with pytest.raises(RewriteError) as caught:
        _prepare(tmp_path, report, "text")
    assert caught.value.code == "BAD_REQUEST"


def test_context_token_is_session_bound_and_single_use(tmp_path):
    report, _ = _write_document(tmp_path, "text\n")
    prepared = _prepare(tmp_path, report, "text")
    payload = {"segments": [{"text": "new", "source_ids": []}], "facts_added": False}
    with pytest.raises(RewriteError) as caught:
        commit_rewrite(context_token=prepared["context_token"], session_id="S2", structured_result=payload)
    assert caught.value.code == "CONTEXT_EXPIRED"
    commit_rewrite(context_token=prepared["context_token"], session_id="S1", structured_result=payload)
    with pytest.raises(RewriteError) as caught:
        commit_rewrite(context_token=prepared["context_token"], session_id="S1", structured_result=payload)
    assert caught.value.code == "CONTEXT_EXPIRED"


def test_context_token_expires_after_ttl(tmp_path):
    report, _ = _write_document(tmp_path, "text\n")
    prepared = _prepare(tmp_path, report, "text")
    rewrite_module._CONTEXTS[prepared["context_token"]].expires_at = 0
    with pytest.raises(RewriteError) as caught:
        commit_rewrite(
            context_token=prepared["context_token"], session_id="S1",
            structured_result={"segments": [{"text": "new", "source_ids": []}], "facts_added": False},
        )
    assert caught.value.code == "CONTEXT_EXPIRED"
