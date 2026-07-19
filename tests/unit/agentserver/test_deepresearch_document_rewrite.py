import hashlib
import json
import re
from dataclasses import replace
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


def _set_snapshot_citations(report: Path, provenance: dict, citations: list[dict]) -> None:
    snapshot_path = report.with_name(provenance["final_result_path"])
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    snapshot["citation_messages"]["data"] = citations
    snapshot_bytes = json.dumps(
        snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    snapshot_path.write_bytes(snapshot_bytes)
    provenance["final_result_sha256"] = hashlib.sha256(snapshot_bytes).hexdigest()
    report.with_suffix(".provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False), encoding="utf-8"
    )


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


def _structured_payload(prepared, replacements=None):
    replacements = replacements or {}
    return {
        "units": [
            {
                "unit_id": unit["unit_id"],
                "slots": [
                    {
                        "slot_id": slot["slot_id"],
                        "text": replacements.get(slot["slot_id"], slot["text"]),
                    }
                    for slot in unit["slots"]
                ],
            }
            for unit in prepared["units"]
        ],
        "facts_added": False,
    }


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


def test_prepare_rejects_uppercase_source_hash(tmp_path):
    report, _ = _write_document(tmp_path, "text\n")
    selection = _selection("text\n", "text")
    selection["source_sha256"] = selection["source_sha256"].upper()
    with pytest.raises(RewriteError) as caught:
        prepare_rewrite(
            workspace_root=tmp_path,
            report_path=report,
            action="polish",
            selection=selection,
            session_id="S1",
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


def test_prepare_rejects_selection_containing_only_a_protected_citation(tmp_path):
    body = "left[[1]](https://example.com/source)right\n"
    report, _ = _write_document(tmp_path, body)
    with pytest.raises(RewriteError) as caught:
        _prepare(
            tmp_path,
            report,
            "[[1]](https://example.com/source)",
            visible="[1]",
        )
    assert caught.value.code == "UNSUPPORTED_SELECTION"


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


def test_inference_manifest_path_requires_exact_link_href_match(tmp_path):
    body = "before [claim](infer/10) after\n"
    report, provenance = _write_document(tmp_path, body)
    provenance["inference_manifest"] = [{
        "id": "1",
        "path": "infer/1",
        "sha256": "a" * 64,
    }]
    report.with_suffix(".provenance.json").write_text(
        json.dumps(provenance), encoding="utf-8"
    )

    prepared = _prepare(
        tmp_path,
        report,
        "before [claim](infer/10) after",
        visible="before claim after",
    )

    assert prepared["units"][0]["slots"]


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


@pytest.mark.parametrize(
    "unsupported",
    [
        "> quoted gap",
        "| a | b |\n|---|---|\n| c | d |",
        "```text\ncode gap\n```",
    ],
)
def test_readonly_neighbors_do_not_cross_unsupported_gaps(tmp_path, unsupported):
    body = (
        f"previous\n\n{unsupported}\n\ntarget\n\n"
        f"{unsupported}\n\nnext\n"
    )
    report, _ = _write_document(tmp_path, body)

    prepared = _prepare(tmp_path, report, "target")

    assert prepared["readonly_context"] == {
        "previous_unit": None,
        "next_unit": None,
    }


def test_prepare_multi_unit_mapping_never_uses_tuple_index(tmp_path, monkeypatch):
    body = "\n\n".join(f"paragraph {index}" for index in range(200)) + "\n"
    report, _ = _write_document(tmp_path, body)
    real_build = rewrite_module.build_rewrite_map

    class NoIndexTuple(tuple):
        def index(self, *_args, **_kwargs):
            pytest.fail("selection mapping must not use tuple.index")

    def build_without_index(markdown):
        rewrite_map = real_build(markdown)
        return replace(rewrite_map, units=NoIndexTuple(rewrite_map.units))

    monkeypatch.setattr(rewrite_module, "build_rewrite_map", build_without_index)
    raw = body.rstrip("\n")
    visible = "\n".join(f"paragraph {index}" for index in range(200))

    prepared = _prepare(tmp_path, report, raw, visible=visible)

    assert len(prepared["units"]) == 200


def test_commit_accepts_exact_structured_unit_and_slot_output(tmp_path):
    original = "原句需要润色。[[1]](https://example.com/source)\n"
    report, provenance = _write_document(tmp_path, original)
    prepared = _prepare(tmp_path, report, "原句需要润色。")
    slot_id = prepared["units"][0]["slots"][0]["slot_id"]
    result = commit_rewrite(
        context_token=prepared["context_token"], session_id="S1",
        structured_result=_structured_payload(
            prepared, {slot_id: "这句话更加清晰。"}
        ),
    )
    child = Path(result["report_path"])
    assert report.read_text(encoding="utf-8") == original
    assert "这句话更加清晰。[[1]]" in child.read_text(encoding="utf-8")
    child_provenance = json.loads(Path(result["provenance_path"]).read_text(encoding="utf-8"))
    assert child_provenance["parent_revision_id"] == provenance["revision_id"]


@pytest.mark.parametrize(
    ("body", "raw", "replacement", "expected"),
    [
        ("原句，后文\n", "原句", "新句。", "新句，后文\n"),
        ("Original, tail\n", "Original", "Rewritten.", "Rewritten, tail\n"),
        ("原句，后文\n", "原句", "新句", "新句，后文\n"),
    ],
)
def test_commit_preserves_unselected_right_punctuation(
    tmp_path, body, raw, replacement, expected
):
    report, provenance = _write_document(tmp_path, body)
    prepared = _prepare(tmp_path, report, raw)
    slot_id = prepared["units"][0]["slots"][0]["slot_id"]

    result = commit_rewrite(
        context_token=prepared["context_token"],
        session_id="S1",
        structured_result=_structured_payload(prepared, {slot_id: replacement}),
    )

    child = Path(result["report_path"])
    child_bytes = child.read_bytes()
    assert child_bytes.decode("utf-8") == expected
    child_provenance = json.loads(
        Path(result["provenance_path"]).read_text(encoding="utf-8")
    )
    assert child_provenance["parent_revision_id"] == provenance["revision_id"]
    assert child_provenance["content_sha256"] == hashlib.sha256(child_bytes).hexdigest()


def test_commit_keeps_candidate_punctuation_when_selection_contains_boundary(tmp_path):
    body = "原句，后文\n"
    report, _ = _write_document(tmp_path, body)
    prepared = _prepare(tmp_path, report, "原句，")
    slot_id = prepared["units"][0]["slots"][0]["slot_id"]

    result = commit_rewrite(
        context_token=prepared["context_token"],
        session_id="S1",
        structured_result=_structured_payload(prepared, {slot_id: "新句。"}),
    )

    assert Path(result["report_path"]).read_text(encoding="utf-8") == "新句。后文\n"


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("**原句**，后文\n", "**新句**，后文\n"),
        (
            "[原句](https://ordinary.example)，后文\n",
            "[新句](https://ordinary.example)，后文\n",
        ),
    ],
)
def test_commit_preserves_unselected_right_punctuation_after_markdown_closing_syntax(
    tmp_path, body, expected
):
    report, _ = _write_document(tmp_path, body)
    prepared = _prepare(tmp_path, report, "原句")
    slot_id = prepared["units"][0]["slots"][0]["slot_id"]

    result = commit_rewrite(
        context_token=prepared["context_token"],
        session_id="S1",
        structured_result=_structured_payload(prepared, {slot_id: "新句。"}),
    )

    assert Path(result["report_path"]).read_text(encoding="utf-8") == expected


def test_commit_rejects_punctuation_only_boundary_replacement(tmp_path):
    report, _ = _write_document(tmp_path, "原句，后文\n")
    prepared = _prepare(tmp_path, report, "原句")
    slot_id = prepared["units"][0]["slots"][0]["slot_id"]

    with pytest.raises(RewriteError) as caught:
        commit_rewrite(
            context_token=prepared["context_token"],
            session_id="S1",
            structured_result=_structured_payload(prepared, {slot_id: "."}),
        )

    assert caught.value.code == "MODEL_OUTPUT_INVALID"


def test_commit_normalizes_boundary_punctuation_before_trailing_whitespace(tmp_path):
    report, _ = _write_document(tmp_path, "原句，后文\n")
    prepared = _prepare(tmp_path, report, "原句")
    slot_id = prepared["units"][0]["slots"][0]["slot_id"]

    result = commit_rewrite(
        context_token=prepared["context_token"],
        session_id="S1",
        structured_result=_structured_payload(prepared, {slot_id: "新句。 "}),
    )

    assert Path(result["report_path"]).read_text(encoding="utf-8") == "新句，后文\n"


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_unit",
        "extra_unit",
        "reordered_units",
        "duplicate_unit",
        "missing_slot",
        "extra_slot",
        "reordered_slots",
        "duplicate_slot",
    ],
)
def test_commit_rejects_non_exact_model_output_ids(tmp_path, mutation):
    body = "first **bold** tail\n\nsecond paragraph\n"
    report, _ = _write_document(tmp_path, body)
    prepared = _prepare(
        tmp_path,
        report,
        body.rstrip("\n"),
        visible="first bold tail\nsecond paragraph",
    )
    payload = _structured_payload(prepared)
    units = payload["units"]
    if mutation == "missing_unit":
        units.pop()
    elif mutation == "extra_unit":
        units.append({"unit_id": "extra", "slots": []})
    elif mutation == "reordered_units":
        units.reverse()
    elif mutation == "duplicate_unit":
        units[1] = dict(units[0], slots=list(units[0]["slots"]))
    elif mutation == "missing_slot":
        units[0]["slots"].pop()
    elif mutation == "extra_slot":
        units[0]["slots"].append({"slot_id": "extra", "text": "text"})
    elif mutation == "reordered_slots":
        units[0]["slots"].reverse()
    else:
        units[0]["slots"][1] = dict(units[0]["slots"][0])

    with pytest.raises(RewriteError) as caught:
        commit_rewrite(
            context_token=prepared["context_token"],
            session_id="S1",
            structured_result=payload,
        )

    assert caught.value.code == "MODEL_OUTPUT_INVALID"


@pytest.mark.parametrize(
    ("text", "facts_added"),
    [
        ("valid", True),
        ("", False),
        (" \t\n", False),
        ("x" * 24_001, False),
        ("visit https://example.com", False),
        ("clone ssh://git.example/repo", False),
        ("run javascript:alert(1)", False),
        ("run javascript: alert(1)", False),
        ("run vbscript: MsgBox(1)", False),
        ("payload data: text/html,x", False),
        ("email mailto:user@example.com", False),
        ("email mailto: user@example.com", False),
        ("call tel: 12345", False),
        ("message sms: 12345", False),
        ("name urn: isbn:123", False),
        ("[label](destination)", False),
        ("[label][reference]", False),
        ("[label]: destination", False),
        ("<span>text</span>", False),
        ("![alt](image.png)", False),
        ("#Inference:claim", False),
    ],
)
def test_commit_rejects_unsafe_or_invalid_model_output(
    tmp_path, text, facts_added
):
    report, _ = _write_document(tmp_path, "original\n")
    prepared = _prepare(tmp_path, report, "original")
    payload = _structured_payload(prepared)
    payload["facts_added"] = facts_added
    payload["units"][0]["slots"][0]["text"] = text

    with pytest.raises(RewriteError) as caught:
        commit_rewrite(
            context_token=prepared["context_token"],
            session_id="S1",
            structured_result=payload,
        )

    assert caught.value.code == "MODEL_OUTPUT_INVALID"


def test_commit_rejects_lone_surrogate_as_invalid_model_output(tmp_path):
    report, _ = _write_document(tmp_path, "original\n")
    prepared = _prepare(tmp_path, report, "original")
    payload = _structured_payload(prepared)
    payload["units"][0]["slots"][0]["text"] = "bad\ud800text"

    with pytest.raises(RewriteError) as caught:
        commit_rewrite(
            context_token=prepared["context_token"],
            session_id="S1",
            structured_result=payload,
        )

    assert caught.value.code == "MODEL_OUTPUT_INVALID"


def test_commit_counts_output_limit_in_utf8_bytes(tmp_path):
    report, _ = _write_document(tmp_path, "original\n")
    prepared = _prepare(tmp_path, report, "original")
    payload = _structured_payload(prepared)
    payload["units"][0]["slots"][0]["text"] = "中" * 8_001

    with pytest.raises(RewriteError) as caught:
        commit_rewrite(
            context_token=prepared["context_token"],
            session_id="S1",
            structured_result=payload,
        )

    assert caught.value.code == "MODEL_OUTPUT_INVALID"


@pytest.mark.parametrize(
    ("body", "raw", "replacement", "expected_fragment"),
    [
        ("plain text\n", "plain text", r"slash \ value", r"slash \\ value"),
        ("plain text\n", "plain text", "entity &copy;", r"entity \&copy\;"),
        (
            "plain text\n",
            "plain text",
            "punctuation * _ # [ ]",
            r"punctuation \* \_ \# \[ \]",
        ),
        ("plain text\n", "plain text", "**new**", r"\*\*new\*\*"),
        ("plain text\n", "plain text", "ratio A:B", r"ratio A\:B"),
        ("plain text\n", "plain text", r"C:\Temp\x", r"C\:\\Temp\\x"),
        (
            "**bold** [label](https://ordinary.example)\n",
            "label",
            r"# \ &copy; *",
            r"[\# \\ \&copy\; \*](https://ordinary.example)",
        ),
    ],
)
def test_commit_encodes_model_text_as_visible_markdown_literal(
    tmp_path, body, raw, replacement, expected_fragment
):
    report, _ = _write_document(tmp_path, body)
    prepared = _prepare(tmp_path, report, raw)
    slot_id = prepared["units"][0]["slots"][0]["slot_id"]

    result = commit_rewrite(
        context_token=prepared["context_token"],
        session_id="S1",
        structured_result=_structured_payload(prepared, {slot_id: replacement}),
    )

    child = Path(result["report_path"]).read_text(encoding="utf-8")
    assert expected_fragment in child
    reparsed = rewrite_module.build_rewrite_map(child)
    assert replacement in "".join(
        slot.text for unit in reparsed.units for slot in unit.slots
    )


def test_commit_rejects_newline_that_would_change_softbreak_topology(tmp_path):
    report, _ = _write_document(tmp_path, "original\n")
    prepared = _prepare(tmp_path, report, "original")
    slot_id = prepared["units"][0]["slots"][0]["slot_id"]

    with pytest.raises(RewriteError) as caught:
        commit_rewrite(
            context_token=prepared["context_token"],
            session_id="S1",
            structured_result=_structured_payload(prepared, {slot_id: "a\nb"}),
        )

    assert caught.value.code == "FORMAT_CONFLICT"


def test_commit_reconstructs_complex_units_and_preserves_protected_bytes(tmp_path):
    body = (
        "outside before\n\n"
        "## Market **grows** and *moves* ~~now~~ "
        "[source](https://ordinary.example/report \"title\") "
        "[[1]](https://example.com/source) hard  \n`code` tail\n\n"
        "- item one\n"
        "- item two\n\n"
        "outside after\n"
    )
    report, _ = _write_document(tmp_path, body)
    raw = body[body.index("Market") : body.index("item two") + len("item two")]
    visible = "Market grows and moves now source [1] hard\ncode tail\nitem one\nitem two"
    prepared = _prepare(tmp_path, report, raw, visible=visible)
    replacements = {}
    replacement_text = {
        "Market ": "Sector ",
        "grows": "expands",
        "moves": "shifts",
        "now": "today",
        "source": "report",
        " tail": " ending",
        "item one": "entry one",
        "item two": "entry two",
    }
    for unit in prepared["units"]:
        for slot in unit["slots"]:
            if slot["text"] in replacement_text:
                replacements[slot["slot_id"]] = replacement_text[slot["text"]]

    result = commit_rewrite(
        context_token=prepared["context_token"],
        session_id="S1",
        structured_result=_structured_payload(prepared, replacements),
    )

    child_bytes = Path(result["report_path"]).read_bytes()
    original_bytes = body.encode("utf-8")
    selection = _selection(body, raw, visible)
    assert child_bytes[: selection["start_byte"]] == original_bytes[: selection["start_byte"]]
    assert child_bytes[-len("\n\noutside after\n".encode()):] == b"\n\noutside after\n"
    child = child_bytes.decode("utf-8")
    assert "## Sector **expands** and *shifts* ~~today~~" in child
    assert "[report](https://ordinary.example/report \"title\")" in child
    assert "[[1]](https://example.com/source) hard  \n`code` ending" in child
    assert "- entry one\n- entry two" in child


def test_commit_preserves_unsupported_regions_outside_selection(tmp_path):
    body = "> untouched quote\n\ntarget text\n"
    report, _ = _write_document(tmp_path, body)
    prepared = _prepare(tmp_path, report, "target text")
    slot_id = prepared["units"][0]["slots"][0]["slot_id"]

    result = commit_rewrite(
        context_token=prepared["context_token"],
        session_id="S1",
        structured_result=_structured_payload(prepared, {slot_id: "changed text"}),
    )

    assert Path(result["report_path"]).read_text(encoding="utf-8") == (
        "> untouched quote\n\nchanged text\n"
    )


def test_commit_deletes_an_empty_strong_wrapper(tmp_path):
    body = "before **bold** after\n"
    report, _ = _write_document(tmp_path, body)
    prepared = _prepare(tmp_path, report, "bold** after", visible="bold after")
    replacements = {
        slot["slot_id"]: "" if slot["format"] == ["strong"] else " ending"
        for slot in prepared["units"][0]["slots"]
    }

    result = commit_rewrite(
        context_token=prepared["context_token"],
        session_id="S1",
        structured_result=_structured_payload(prepared, replacements),
    )

    assert Path(result["report_path"]).read_text(encoding="utf-8") == (
        "before  ending\n"
    )


@pytest.mark.parametrize(
    ("replacement", "code"),
    [
        ("new\n\n# heading", "FORMAT_CONFLICT"),
        ("> protected\n\nnew", "FORMAT_CONFLICT"),
    ],
)
def test_commit_rejects_reparsed_topology_conflicts(tmp_path, replacement, code):
    report, _ = _write_document(tmp_path, "original\n")
    prepared = _prepare(tmp_path, report, "original")
    slot_id = prepared["units"][0]["slots"][0]["slot_id"]

    with pytest.raises(RewriteError) as caught:
        commit_rewrite(
            context_token=prepared["context_token"],
            session_id="S1",
            structured_result=_structured_payload(prepared, {slot_id: replacement}),
        )

    assert caught.value.code == code


def test_commit_revalidates_final_result_hash(tmp_path):
    body = "claim [[1]](https://example.com/source)\n"
    report, provenance = _write_document(tmp_path, body)
    prepared = _prepare(
        tmp_path,
        report,
        "claim",
    )
    report.with_name(provenance["final_result_path"]).write_text(
        "{}", encoding="utf-8"
    )

    with pytest.raises(RewriteError) as caught:
        commit_rewrite(
            context_token=prepared["context_token"],
            session_id="S1",
            structured_result=_structured_payload(prepared),
        )

    assert caught.value.code == "REVISION_CONFLICT"


@pytest.mark.parametrize(
    "mutation",
    [
        "reserialize",
        "revision_id",
        "content_sha256",
        "final_result_path",
        "final_result_sha256",
    ],
)
def test_commit_rejects_provenance_sidecar_toctou(tmp_path, mutation):
    report, _ = _write_document(tmp_path, "original\n")
    prepared = _prepare(tmp_path, report, "original")
    sidecar = report.with_suffix(".provenance.json")
    provenance = json.loads(sidecar.read_text(encoding="utf-8"))
    if mutation == "reserialize":
        provenance["created_at"] = "2026-07-18T00:00:00+00:00"
    elif mutation == "revision_id":
        provenance[mutation] = "rev_changed"
    elif mutation == "content_sha256":
        provenance[mutation] = "1" * 64
    elif mutation == "final_result_path":
        provenance[mutation] = "other.final-result.json"
    else:
        provenance[mutation] = "2" * 64
    sidecar.write_text(json.dumps(provenance, sort_keys=True), encoding="utf-8")

    with pytest.raises(RewriteError) as caught:
        commit_rewrite(
            context_token=prepared["context_token"],
            session_id="S1",
            structured_result=_structured_payload(prepared),
        )

    assert caught.value.code == "REVISION_CONFLICT"


def test_commit_preserves_multiple_citation_identity_order_count(tmp_path):
    body = (
        "left [[1]](https://example.com/source) middle "
        "[[2]](https://second.example/source) right\n"
    )
    report, provenance = _write_document(tmp_path, body)
    second = {
        "id": 4,
        "reference_index": 2,
        "url": "https://second.example/source",
        "title": "Second",
        "content": "second evidence",
        "chunk": "second chunk",
        "source": "web",
    }
    first = json.loads(
        report.with_name(provenance["final_result_path"]).read_text(encoding="utf-8")
    )["citation_messages"]["data"][0]
    _set_snapshot_citations(report, provenance, [first, second])
    prepared = _prepare(
        tmp_path,
        report,
        body.rstrip("\n"),
        visible="left [1] middle [2] right",
    )
    replacements = {
        slot["slot_id"]: slot["text"].replace("left", "start").replace("right", "end")
        for slot in prepared["units"][0]["slots"]
    }

    result = commit_rewrite(
        context_token=prepared["context_token"],
        session_id="S1",
        structured_result=_structured_payload(prepared, replacements),
    )

    child = Path(result["report_path"]).read_text(encoding="utf-8")
    assert rewrite_module.CITATION_RE.findall(child) == [
        ("1", "https://example.com/source"),
        ("2", "https://second.example/source"),
    ]
    child_provenance = json.loads(
        Path(result["provenance_path"]).read_text(encoding="utf-8")
    )
    assert child_provenance["rewrite_history"][-1]["citation_ids"] == ["3", "4"]


@pytest.mark.parametrize(
    ("body", "raw", "visible", "unit_types"),
    [
        (
            "first paragraph\n\nsecond paragraph\n",
            "first paragraph\n\nsecond paragraph",
            "first paragraph\nsecond paragraph",
            ["paragraph", "paragraph"],
        ),
        (
            "- first item\n- second item\n- third item\n",
            "first item\n- second item\n- third item",
            "first item\nsecond item\nthird item",
            ["list_item", "list_item", "list_item"],
        ),
        (
            "## Old heading\n\nparagraph body\n\n- list item\n",
            "Old heading\n\nparagraph body\n\n- list item",
            "Old heading\nparagraph body\nlist item",
            ["heading", "paragraph", "list_item"],
        ),
    ],
)
def test_commit_rewrites_continuous_multi_units_without_changing_order(
    tmp_path, body, raw, visible, unit_types
):
    report, _ = _write_document(tmp_path, body)
    prepared = _prepare(tmp_path, report, raw, visible=visible)
    replacements = {
        slot["slot_id"]: f"rewritten {index}"
        for index, unit in enumerate(prepared["units"])
        for slot in unit["slots"]
    }

    result = commit_rewrite(
        context_token=prepared["context_token"],
        session_id="S1",
        structured_result=_structured_payload(prepared, replacements),
    )

    child_map = rewrite_module.build_rewrite_map(
        Path(result["report_path"]).read_text(encoding="utf-8")
    )
    assert [unit.unit_type for unit in child_map.units] == unit_types
    assert len(child_map.units) == len(prepared["units"])


@pytest.mark.parametrize(
    ("body", "raw", "visible"),
    [
        (
            "first\n\n> unsupported gap\n\nthird\n",
            "first\n\n> unsupported gap\n\nthird",
            "first\nthird",
        ),
        (
            "- outer\n  - nested\n- final\n",
            "outer\n  - nested\n- final",
            "outer\nnested\nfinal",
        ),
    ],
)
def test_prepare_rejects_noncontinuous_or_invalid_middle_multi_units(
    tmp_path, body, raw, visible
):
    report, _ = _write_document(tmp_path, body)
    with pytest.raises(RewriteError) as caught:
        prepare_rewrite(
            workspace_root=tmp_path,
            report_path=report,
            action="shorten",
            selection=_selection(body, raw, visible),
            session_id="S1",
        )
    assert caught.value.code == "UNSUPPORTED_SELECTION"


def test_prepare_rejects_partially_covered_middle_multi_unit(tmp_path, monkeypatch):
    body = "first\n\nmiddle\n\nlast\n"
    report, _ = _write_document(tmp_path, body)
    real_build = rewrite_module.build_rewrite_map

    def build_with_partial_middle(markdown):
        rewrite_map = real_build(markdown)
        middle = rewrite_map.units[1]
        slot = middle.slots[0]
        partial_slot = replace(
            slot,
            start_byte=0,
            visible_boundary_to_byte=(0, *slot.visible_boundary_to_byte[1:]),
        )
        return replace(
            rewrite_map,
            units=(
                rewrite_map.units[0],
                replace(middle, slots=(partial_slot,)),
                rewrite_map.units[2],
            ),
        )

    monkeypatch.setattr(rewrite_module, "build_rewrite_map", build_with_partial_middle)

    with pytest.raises(RewriteError) as caught:
        _prepare(
            tmp_path,
            report,
            body[1:].rstrip("\n"),
            visible="irst\nmiddle\nlast",
        )

    assert caught.value.code == "UNSUPPORTED_SELECTION"


def test_commit_repairs_unique_same_document_heading_anchor(tmp_path):
    body = "[跳转](#旧标题)\n\n## 旧标题\n"
    report, _ = _write_document(tmp_path, body)
    prepared = _prepare(tmp_path, report, "旧标题", occurrence=2)
    slot_id = prepared["units"][0]["slots"][0]["slot_id"]

    result = commit_rewrite(
        context_token=prepared["context_token"],
        session_id="S1",
        structured_result=_structured_payload(prepared, {slot_id: "新标题"}),
    )

    assert Path(result["report_path"]).read_text(encoding="utf-8") == (
        "[跳转](#新标题)\n\n## 新标题\n"
    )


@pytest.mark.parametrize(
    "body",
    [
        "[go](#old)\n\n## old\n",
        "[go][target]\n\n[target]: #old\n\n## old\n",
    ],
)
def test_commit_rejects_empty_new_heading_slug_when_old_target_is_linked(
    tmp_path, body
):
    report, _ = _write_document(tmp_path, body)
    heading_start = body.index("## old")
    occurrence = body[:heading_start].count("old") + 1
    prepared = _prepare(tmp_path, report, "old", occurrence=occurrence)
    slot_id = prepared["units"][0]["slots"][0]["slot_id"]

    with pytest.raises(RewriteError) as caught:
        commit_rewrite(
            context_token=prepared["context_token"],
            session_id="S1",
            structured_result=_structured_payload(prepared, {slot_id: "!!!"}),
        )

    assert caught.value.code == "FORMAT_CONFLICT"


def test_commit_maps_removed_unit_before_heading_anchor_repair(tmp_path):
    body = "**bold**\n\n## old\n"
    report, _ = _write_document(tmp_path, body)
    prepared = _prepare(
        tmp_path,
        report,
        "bold**\n\n## old",
        visible="bold\nold",
    )
    replacements = {
        slot["slot_id"]: "" if unit["type"] == "paragraph" else "new"
        for unit in prepared["units"]
        for slot in unit["slots"]
    }

    with pytest.raises(RewriteError) as caught:
        commit_rewrite(
            context_token=prepared["context_token"],
            session_id="S1",
            structured_result=_structured_payload(prepared, replacements),
        )

    assert caught.value.code == "STRUCTURE_CONFLICT"


@pytest.mark.parametrize(
    "body",
    [
        "[跳转](#旧标题)\n\n## 旧标题\n\n## 旧标题\n",
        "[跳转](#旧标题)\n\n## 旧标题\n\n## 新标题\n",
        "[甲](#旧标题) [乙](#旧标题)\n\n## 旧标题\n",
    ],
)
def test_commit_rejects_ambiguous_heading_anchor_repair(tmp_path, body):
    report, _ = _write_document(tmp_path, body)
    heading_start = body.index("## 旧标题")
    heading_occurrence = body[:heading_start].count("旧标题") + 1
    prepared = _prepare(tmp_path, report, "旧标题", occurrence=heading_occurrence)
    slot_id = prepared["units"][0]["slots"][0]["slot_id"]

    with pytest.raises(RewriteError) as caught:
        commit_rewrite(
            context_token=prepared["context_token"],
            session_id="S1",
            structured_result=_structured_payload(prepared, {slot_id: "新标题"}),
        )

    assert caught.value.code == "FORMAT_CONFLICT"


@pytest.mark.parametrize(
    "body",
    [
        "> [跳转](#旧标题)\n\n## 旧标题\n",
        "| 导航 |\n| --- |\n| [跳转](#旧标题) |\n\n## 旧标题\n",
    ],
)
def test_commit_repairs_unique_anchor_link_inside_unsupported_region(tmp_path, body):
    report, _ = _write_document(tmp_path, body)
    heading_start = body.index("## 旧标题")
    occurrence = body[:heading_start].count("旧标题") + 1
    prepared = _prepare(tmp_path, report, "旧标题", occurrence=occurrence)
    slot_id = prepared["units"][0]["slots"][0]["slot_id"]

    result = commit_rewrite(
        context_token=prepared["context_token"],
        session_id="S1",
        structured_result=_structured_payload(prepared, {slot_id: "新标题"}),
    )

    child = Path(result["report_path"]).read_text(encoding="utf-8")
    assert "[跳转](#新标题)" in child
    assert "#旧标题" not in child


@pytest.mark.parametrize(
    "body",
    [
        "> ## 旧标题\n\n[跳转](#旧标题)\n\n## 旧标题\n",
        "[跳转](#旧标题)\n\n## 旧标题\n\n新标题\n---\n",
        "> [甲](#旧标题)\n\n[乙](#旧标题)\n\n## 旧标题\n",
        (
            "| 导航 |\n| --- |\n| [甲](#旧标题) |\n\n"
            "[乙](#旧标题)\n\n## 旧标题\n"
        ),
        "[跳转][目标]\n\n[目标]: #旧标题\n\n## 旧标题\n",
    ],
)
def test_commit_rejects_global_anchor_ambiguity_across_unsupported_regions(
    tmp_path, body
):
    report, _ = _write_document(tmp_path, body)
    heading_start = body.index("## 旧标题", body.find("\n\n## 旧标题"))
    occurrence = body[:heading_start].count("旧标题") + 1
    prepared = _prepare(tmp_path, report, "旧标题", occurrence=occurrence)
    slot_id = prepared["units"][0]["slots"][0]["slot_id"]

    with pytest.raises(RewriteError) as caught:
        commit_rewrite(
            context_token=prepared["context_token"],
            session_id="S1",
            structured_result=_structured_payload(prepared, {slot_id: "新标题"}),
        )

    assert caught.value.code == "FORMAT_CONFLICT"


def test_commit_records_only_current_revision_visible_utf8_highlights(tmp_path):
    body = (
        "## **旧标题**\n\n"
        "普通段落\n\n"
        "- [旧标签](https://ordinary.example/path) 正文 "
        "[[1]](https://example.com/source) 结尾\n"
    )
    report, _ = _write_document(tmp_path, body)
    raw_start = body.index("旧标题")
    raw_end = body.index(" 结尾") + len(" 结尾")
    raw = body[raw_start:raw_end]
    prepared = _prepare(
        tmp_path,
        report,
        raw,
        visible="旧标题\n普通段落\n旧标签 正文 [1] 结尾",
    )
    replacements = {
        slot["slot_id"]: {
            "旧标题": "新标题",
            "普通段落": "新段落",
            "旧标签": "新标签",
            " 正文 ": " 新正文 ",
            " 结尾": " 新结尾",
        }.get(slot["text"], slot["text"])
        for unit in prepared["units"]
        for slot in unit["slots"]
    }

    result = commit_rewrite(
        context_token=prepared["context_token"],
        session_id="S1",
        structured_result=_structured_payload(prepared, replacements),
    )

    child = Path(result["report_path"])
    child_bytes = child.read_bytes()
    expected_ranges = []
    cursor = 0
    for visible, unit_type in (
        ("新标题", "heading"),
        ("新段落", "paragraph"),
        ("新标签", "list_item"),
        (" 新正文 ", "list_item"),
        (" 新结尾", "list_item"),
    ):
        start = child_bytes.index(visible.encode("utf-8"), cursor)
        end = start + len(visible.encode("utf-8"))
        expected_ranges.append({
            "start_byte": start,
            "end_byte": end,
            "unit_type": unit_type,
        })
        cursor = end
    child_provenance = json.loads(
        Path(result["provenance_path"]).read_text(encoding="utf-8")
    )
    assert child_provenance["rewrite_protocol_version"] == 2
    assert child_provenance["rewrite_highlights"] == {
        "revision_id": child_provenance["revision_id"],
        "offset_unit": "utf8_byte",
        "ranges": expected_ranges,
    }
    assert all(
        left["end_byte"] <= right["start_byte"]
        for left, right in zip(expected_ranges, expected_ranges[1:])
    )
    history = child_provenance["rewrite_history"][-1]
    assert set(history) == {
        "rewrite_protocol_version",
        "action",
        "parent_revision_id",
        "selection_sha256",
        "result_sha256",
        "unit_types",
        "citation_ids",
    }
    assert "旧标题" not in json.dumps(history, ensure_ascii=False)
    assert "新标题" not in json.dumps(history, ensure_ascii=False)


def test_commit_single_list_unit_highlight_records_its_actual_unit_type(tmp_path):
    body = "- old item\n"
    report, _ = _write_document(tmp_path, body)
    prepared = _prepare(tmp_path, report, "old item")
    slot_id = prepared["units"][0]["slots"][0]["slot_id"]

    result = commit_rewrite(
        context_token=prepared["context_token"],
        session_id="S1",
        structured_result=_structured_payload(prepared, {slot_id: "new item"}),
    )

    child = Path(result["report_path"])
    start = child.read_bytes().index(b"new item")
    child_provenance = json.loads(
        Path(result["provenance_path"]).read_text(encoding="utf-8")
    )
    assert child_provenance["rewrite_highlights"]["ranges"] == [{
        "start_byte": start,
        "end_byte": start + len(b"new item"),
        "unit_type": "list_item",
    }]


def test_commit_noop_rewrite_has_no_highlight_ranges(tmp_path):
    report, _ = _write_document(tmp_path, "unchanged text\n")
    prepared = _prepare(tmp_path, report, "unchanged text")

    result = commit_rewrite(
        context_token=prepared["context_token"],
        session_id="S1",
        structured_result=_structured_payload(prepared),
    )

    child_provenance = json.loads(
        Path(result["provenance_path"]).read_text(encoding="utf-8")
    )
    assert child_provenance["rewrite_highlights"]["ranges"] == []


def test_commit_highlights_changed_slot_but_not_noop_slot(tmp_path):
    body = "unchanged\n\nold text\n"
    report, _ = _write_document(tmp_path, body)
    prepared = _prepare(
        tmp_path,
        report,
        body.rstrip("\n"),
        visible="unchanged\nold text",
    )
    changed_slot = prepared["units"][1]["slots"][0]["slot_id"]

    result = commit_rewrite(
        context_token=prepared["context_token"],
        session_id="S1",
        structured_result=_structured_payload(
            prepared, {changed_slot: "new text"}
        ),
    )

    child = Path(result["report_path"])
    child_bytes = child.read_bytes()
    start = child_bytes.index(b"new text")
    child_provenance = json.loads(
        Path(result["provenance_path"]).read_text(encoding="utf-8")
    )
    assert child_provenance["rewrite_highlights"]["ranges"] == [
        {
            "start_byte": start,
            "end_byte": start + len(b"new text"),
            "unit_type": "paragraph",
        }
    ]


@pytest.mark.parametrize(
    ("unit_count", "expected_range_count"),
    [(4096, 4096), (4097, 0)],
)
def test_commit_bounds_rewrite_highlight_ranges_without_truncation(
    tmp_path, unit_count, expected_range_count
):
    body = "\n\n".join("a" for _ in range(unit_count)) + "\n"
    report, _ = _write_document(tmp_path, body)
    prepared = _prepare(
        tmp_path,
        report,
        body.rstrip("\n"),
        visible="\n".join("a" for _ in range(unit_count)),
    )
    replacements = {
        slot["slot_id"]: "b"
        for unit in prepared["units"]
        for slot in unit["slots"]
    }

    result = commit_rewrite(
        context_token=prepared["context_token"],
        session_id="S1",
        structured_result=_structured_payload(prepared, replacements),
    )

    child_provenance = json.loads(
        Path(result["provenance_path"]).read_text(encoding="utf-8")
    )
    ranges = child_provenance["rewrite_highlights"]["ranges"]
    assert len(ranges) == expected_range_count
    if ranges:
        assert {item["unit_type"] for item in ranges} == {"paragraph"}
        assert all(
            left["end_byte"] <= right["start_byte"]
            for left, right in zip(ranges, ranges[1:])
        )


def test_rewritten_child_can_be_rewritten_again_with_original_lineage(tmp_path):
    body = "first claim [[1]](https://example.com/source) tail\n\nsecond paragraph\n"
    report, provenance = _write_document(tmp_path, body)
    provenance["inference_manifest"] = [{"path": "infer/1", "sha256": "a" * 64}]
    provenance["chart_manifest"] = [{"path": "chart/1", "sha256": "b" * 64}]
    report.with_suffix(".provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False), encoding="utf-8"
    )
    ancestor_markdown = report.read_bytes()
    ancestor_provenance = report.with_suffix(".provenance.json").read_bytes()

    first_prepared = _prepare(
        tmp_path,
        report,
        "first claim [[1]](https://example.com/source) tail",
        visible="first claim [1] tail",
    )
    first_slot = first_prepared["units"][0]["slots"][0]["slot_id"]
    first_result = commit_rewrite(
        context_token=first_prepared["context_token"],
        session_id="S1",
        structured_result=_structured_payload(
            first_prepared, {first_slot: "rewritten claim "}
        ),
    )
    first_child = Path(first_result["report_path"])
    first_child_markdown = first_child.read_bytes()
    first_child_sidecar = Path(first_result["provenance_path"])
    first_child_provenance_bytes = first_child_sidecar.read_bytes()
    first_child_provenance = json.loads(first_child_provenance_bytes)

    second_prepared = _prepare(
        tmp_path,
        first_child,
        "second paragraph",
        action="polish",
    )
    second_slot = second_prepared["units"][0]["slots"][0]["slot_id"]
    second_result = commit_rewrite(
        context_token=second_prepared["context_token"],
        session_id="S1",
        structured_result=_structured_payload(
            second_prepared, {second_slot: "second generation"}
        ),
    )

    second_provenance = json.loads(
        Path(second_result["provenance_path"]).read_text(encoding="utf-8")
    )
    assert (
        second_provenance["parent_revision_id"]
        == first_child_provenance["revision_id"]
    )
    for key in (
        "final_result_path",
        "final_result_sha256",
        "inference_manifest",
        "chart_manifest",
    ):
        assert second_provenance[key] == provenance[key]
    assert len(second_provenance["rewrite_history"]) == 2
    assert (
        second_provenance["rewrite_highlights"]["revision_id"]
        == second_provenance["revision_id"]
    )
    assert (
        second_provenance["rewrite_highlights"]
        != first_child_provenance["rewrite_highlights"]
    )
    assert [
        item["unit_type"]
        for item in first_child_provenance["rewrite_highlights"]["ranges"]
    ] == ["paragraph"]
    assert [
        item["unit_type"]
        for item in second_provenance["rewrite_highlights"]["ranges"]
    ] == ["paragraph"]
    assert "[[1]](https://example.com/source)" in Path(
        second_result["report_path"]
    ).read_text(encoding="utf-8")
    assert report.read_bytes() == ancestor_markdown
    assert report.with_suffix(".provenance.json").read_bytes() == ancestor_provenance
    assert first_child.read_bytes() == first_child_markdown
    assert first_child_sidecar.read_bytes() == first_child_provenance_bytes


def test_commit_rejects_citation_missing_from_final_result_whitelist(tmp_path):
    body = "claim [[2]](https://missing.example/source) remains\n"
    report, _ = _write_document(tmp_path, body)
    prepared = _prepare(
        tmp_path,
        report,
        body.rstrip("\n"),
        visible="claim [2] remains",
    )

    with pytest.raises(RewriteError) as caught:
        commit_rewrite(
            context_token=prepared["context_token"],
            session_id="S1",
            structured_result=_structured_payload(prepared),
        )

    assert caught.value.code == "FORMAT_CONFLICT"


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_id",
        "bool_id",
        "missing_reference_index",
        "bad_reference_index",
        "missing_url",
        "empty_url",
        "duplicate_id",
        "duplicate_key",
    ],
)
def test_prepare_rejects_invalid_or_duplicate_citation_schema(tmp_path, mutation):
    report, provenance = _write_document(tmp_path, "claim\n")
    snapshot_path = report.with_name(provenance["final_result_path"])
    citations = json.loads(snapshot_path.read_text(encoding="utf-8"))[
        "citation_messages"
    ]["data"]
    first = dict(citations[0])
    if mutation == "missing_id":
        first.pop("id")
    elif mutation == "bool_id":
        first["id"] = True
    elif mutation == "missing_reference_index":
        first.pop("reference_index")
    elif mutation == "bad_reference_index":
        first["reference_index"] = []
    elif mutation == "missing_url":
        first.pop("url")
    elif mutation == "empty_url":
        first["url"] = ""
    elif mutation == "duplicate_id":
        citations.append(dict(first, reference_index=2, url="https://second.example"))
    else:
        citations.append(dict(first, id=4))
    citations[0] = first
    _set_snapshot_citations(report, provenance, citations)

    with pytest.raises(RewriteError) as caught:
        _prepare(tmp_path, report, "claim")

    assert caught.value.code == "DOCUMENT_NOT_FOUND"


def test_prepare_rejects_oversized_final_result_before_json_decode(
    tmp_path, monkeypatch
):
    report, _ = _write_document(tmp_path, "claim\n")
    monkeypatch.setattr(rewrite_module, "FINAL_RESULT_MAX_BYTES", 64, raising=False)

    with pytest.raises(RewriteError) as caught:
        _prepare(tmp_path, report, "claim")

    assert caught.value.code == "DOCUMENT_NOT_FOUND"


def test_prepare_rejects_oversized_citation_evidence_field(tmp_path, monkeypatch):
    report, provenance = _write_document(tmp_path, "claim\n")
    snapshot_path = report.with_name(provenance["final_result_path"])
    citation = json.loads(snapshot_path.read_text(encoding="utf-8"))[
        "citation_messages"
    ]["data"][0]
    citation["content"] = "x" * 65
    _set_snapshot_citations(report, provenance, [citation])
    monkeypatch.setattr(rewrite_module, "CITATION_FIELD_MAX_BYTES", 64, raising=False)

    with pytest.raises(RewriteError) as caught:
        _prepare(tmp_path, report, "claim")

    assert caught.value.code == "DOCUMENT_NOT_FOUND"


@pytest.mark.parametrize("loader", ["provenance", "final_result"])
def test_metadata_loaders_request_only_limit_plus_one_bytes(
    tmp_path, monkeypatch, loader
):
    limit = 8
    requested_sizes = []

    class ReadProbe:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self, size=-1):
            requested_sizes.append(size)
            return b"x" * (limit + 1)

    def open_probe(self, mode="r", *args, **kwargs):
        assert mode == "rb"
        return ReadProbe()

    monkeypatch.setattr(Path, "open", open_probe)
    report = tmp_path / "report.md"
    if loader == "provenance":
        monkeypatch.setattr(rewrite_module, "PROVENANCE_MAX_BYTES", limit)
        invoke = lambda: rewrite_module._load_provenance(report)
    else:
        monkeypatch.setattr(rewrite_module, "FINAL_RESULT_MAX_BYTES", limit)
        invoke = lambda: rewrite_module._load_final_result_citations(
            report,
            {
                "final_result_path": "report.final-result.json",
                "final_result_sha256": "0" * 64,
            },
            tmp_path,
        )

    with pytest.raises(RewriteError) as caught:
        invoke()

    assert caught.value.code == "DOCUMENT_NOT_FOUND"
    assert requested_sizes == [limit + 1]


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


def test_request_limits_are_checked_before_persistent_document_access(
    tmp_path, monkeypatch
):
    missing_report = tmp_path / "missing.md"
    selection = {
        "protocol_version": 2,
        "start_byte": 0,
        "end_byte": 1,
        "selected_text": "x" * 12001,
        "source_sha256": "0" * 64,
    }
    monkeypatch.setattr(
        rewrite_module,
        "_load_provenance",
        lambda _path: pytest.fail("persistent provenance must not be read"),
    )

    with pytest.raises(RewriteError) as caught:
        prepare_rewrite(
            workspace_root=tmp_path,
            report_path=missing_report,
            action="polish",
            selection=selection,
            session_id="S1",
        )

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
    payload = _structured_payload(prepared)
    payload["units"][0]["slots"][0]["text"] = "new"
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
            structured_result=_structured_payload(prepared),
        )
    assert caught.value.code == "CONTEXT_EXPIRED"


def test_context_store_sweeps_expired_entries_in_one_prepare(tmp_path):
    report, _ = _write_document(tmp_path, "text\n")
    with rewrite_module._CONTEXT_LOCK:
        rewrite_module._CONTEXTS.clear()
    expired_tokens = [
        _prepare(tmp_path, report, "text")["context_token"] for _ in range(4)
    ]
    with rewrite_module._CONTEXT_LOCK:
        for token in expired_tokens:
            rewrite_module._CONTEXTS[token].expires_at = 0

    active = _prepare(tmp_path, report, "text")["context_token"]

    with rewrite_module._CONTEXT_LOCK:
        assert set(rewrite_module._CONTEXTS) == {active}


def test_context_store_is_bounded_and_evicts_earliest_expiry(
    tmp_path, monkeypatch
):
    report, _ = _write_document(tmp_path, "text\n")
    monkeypatch.setattr(rewrite_module, "CONTEXT_CACHE_MAX", 3)
    with rewrite_module._CONTEXT_LOCK:
        rewrite_module._CONTEXTS.clear()

    tokens = [_prepare(tmp_path, report, "text")["context_token"] for _ in range(5)]

    with rewrite_module._CONTEXT_LOCK:
        assert len(rewrite_module._CONTEXTS) == 3
        assert set(rewrite_module._CONTEXTS) == set(tokens[-3:])
    with pytest.raises(RewriteError) as caught:
        rewrite_module._take_context(tokens[0], "S1")
    assert caught.value.code == "CONTEXT_EXPIRED"
    assert rewrite_module._take_context(tokens[-1], "S1").session_id == "S1"
    with pytest.raises(RewriteError) as caught:
        rewrite_module._take_context(tokens[-1], "S1")
    assert caught.value.code == "CONTEXT_EXPIRED"


def test_context_is_compact_under_large_citation_cache_saturation(
    tmp_path, monkeypatch
):
    body = "claim [[1]](https://example.com/source) remains\n"
    report, provenance = _write_document(tmp_path, body)
    snapshot_path = report.with_name(provenance["final_result_path"])
    citation = json.loads(snapshot_path.read_text(encoding="utf-8"))[
        "citation_messages"
    ]["data"][0]
    citation["content"] = "x" * 100_000
    _set_snapshot_citations(report, provenance, [citation])
    monkeypatch.setattr(rewrite_module, "CONTEXT_CACHE_MAX", 3)
    with rewrite_module._CONTEXT_LOCK:
        rewrite_module._CONTEXTS.clear()

    tokens = [_prepare(tmp_path, report, "claim")["context_token"] for _ in range(5)]

    with rewrite_module._CONTEXT_LOCK:
        assert set(rewrite_module._CONTEXTS) == set(tokens[-3:])
        for context in rewrite_module._CONTEXTS.values():
            assert not hasattr(context, "provenance")
            assert not hasattr(context, "protected_anchors")
            assert not hasattr(context, "instruction")
            assert not hasattr(context, "allowed_citations")
            assert isinstance(context.provenance_sha256, str)
            assert context.document_id == "doc_test"
            assert context.parent_revision_id == "rev_parent"


def test_commit_child_path_uses_full_revision_uuid(tmp_path):
    report, _ = _write_document(tmp_path, "original\n")
    prepared = _prepare(tmp_path, report, "original")

    result = commit_rewrite(
        context_token=prepared["context_token"],
        session_id="S1",
        structured_result=_structured_payload(prepared),
    )

    assert re.search(r"-rev-[0-9a-f]{32}\.md$", result["report_path"])
