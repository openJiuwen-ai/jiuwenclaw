import hashlib
import json
import re
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from jiuwenclaw.agentserver.tools.deepresearch_plugin.markdown_rewrite_map import (
    RewriteMapError,
    Utf8BoundaryTable,
    sha256_byte_range,
)


FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "deepresearch_rewrite_protocol_v2.json"
)
EXPECTED_CASE_IDS = {
    "duplicate_text_second_occurrence",
    "emoji_before_selection",
    "cjk_selection",
    "combining_character_selection",
    "lf_document",
    "crlf_document",
    "strong_content_only",
    "emphasis_content_only",
    "ordinary_link_label_only",
    "multiple_citations_between_endpoints",
    "soft_break_normalizes_to_space",
    "hard_break_between_endpoints",
    "heading_partial",
    "paragraph_partial",
    "same_level_list_items",
    "mixed_heading_paragraph_list",
    "partial_outer_units_complete_middle",
    "selected_text_mismatch_rejected",
    "nested_list_rejected",
    "table_rejected",
    "fenced_code_rejected",
    "html_block_rejected",
    "image_rejected",
    "inference_rejected",
}
STABLE_ERROR_CODES = {
    "INFERENCE_NOT_REWRITABLE",
    "PROTECTED_ANCHOR_ENDPOINT",
    "SELECTION_MAPPING_CONFLICT",
    "UNSUPPORTED_BLOCK_KIND",
    "UNSUPPORTED_NESTED_LIST",
}
VISIBLE_TEXT_CONVENTIONS = {
    "citation_link_labels",
    "hard_break_as_newline",
    "image_alt_text",
    "literal",
    "soft_break_as_space",
    "table_cells",
    "unit_separator_newline",
}
COMMON_CASE_KEYS = {
    "id",
    "markdown",
    "start_byte",
    "end_byte",
    "selected_text",
    "source_sha256",
    "accepted",
    "raw_selection",
    "visible_text_convention",
}
COMMON_UNIT_KEYS = {"kind", "index", "coverage"}


def _visible_selection(raw_selection: str, convention: str) -> str:
    if convention == "literal":
        return raw_selection
    if convention == "citation_link_labels":
        return re.sub(r"\[\[(\d+)\]\]\([^)]+\)", r"[\1]", raw_selection)
    if convention == "soft_break_as_space":
        return raw_selection.replace("\n", " ")
    if convention == "hard_break_as_newline":
        return raw_selection.replace("  \n", "\n")
    if convention == "unit_separator_newline":
        normalized = raw_selection.replace("\r\n", "\n")
        return re.sub(r"\n+(?:\s*[-+*]\s+)?", "\n", normalized)
    if convention == "table_cells":
        return re.sub(r"\s*\|\s*", " ", raw_selection)
    if convention == "image_alt_text":
        return raw_selection
    raise AssertionError(f"fixture uses unknown convention: {convention}")


def test_utf8_boundary_table_maps_codepoints_and_byte_boundaries():
    table = Utf8BoundaryTable("A😀中e\u0301")

    assert table.codepoint_to_byte == (0, 1, 5, 8, 9, 11)
    assert table.require_byte_boundary(5) == 2


def test_utf8_boundary_table_is_frozen_and_slotted():
    table = Utf8BoundaryTable("text")

    with pytest.raises(FrozenInstanceError):
        table.text = "changed"
    assert not hasattr(table, "__dict__")


@pytest.mark.parametrize("offset", [2, 3, 4])
def test_utf8_boundary_table_rejects_offsets_inside_multibyte_codepoint(offset):
    table = Utf8BoundaryTable("A😀中")

    with pytest.raises(RewriteMapError) as caught:
        table.require_byte_boundary(offset)

    assert caught.value.code == "SELECTION_MAPPING_CONFLICT"


@pytest.mark.parametrize("offset", [-1, 9, 1.0, True])
def test_utf8_boundary_table_rejects_out_of_range_offsets(offset):
    table = Utf8BoundaryTable("A😀中")

    with pytest.raises(RewriteMapError) as caught:
        table.require_byte_boundary(offset)

    assert caught.value.code == "SELECTION_MAPPING_CONFLICT"


def test_sha256_byte_range_hashes_utf8_half_open_range():
    assert sha256_byte_range("A😀中", 1, 8) == hashlib.sha256(
        "😀中".encode("utf-8")
    ).hexdigest()


def test_sha256_byte_range_allows_empty_boundary_aligned_range():
    assert sha256_byte_range("A😀中", 5, 5) == hashlib.sha256(b"").hexdigest()


@pytest.mark.parametrize(
    ("start_byte", "end_byte"),
    [(5, 1), (-1, 1), (0, 9), (2, 8), (1, 7)],
)
def test_sha256_byte_range_rejects_invalid_ranges(start_byte, end_byte):
    with pytest.raises(RewriteMapError) as caught:
        sha256_byte_range("A😀中", start_byte, end_byte)

    assert caught.value.code == "SELECTION_MAPPING_CONFLICT"


def test_protocol_v2_fixture_uses_real_utf8_ranges_and_hashes():
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    assert set(fixture) == {
        "protocol_version",
        "offset_encoding",
        "range_semantics",
        "visible_text_normalization",
        "cases",
    }
    assert fixture["protocol_version"] == 2
    assert fixture["offset_encoding"] == "utf-8-bytes"
    assert fixture["range_semantics"] == "half-open"
    assert fixture["visible_text_normalization"] == {
        "soft_break": "space",
        "hard_break": "newline",
        "unit_separator": "newline",
        "citation": "visible link label",
    }
    assert type(fixture["cases"]) is list
    case_ids = [case["id"] for case in fixture["cases"]]
    assert set(case_ids) == EXPECTED_CASE_IDS
    assert len(case_ids) == len(EXPECTED_CASE_IDS)

    for case in fixture["cases"]:
        case_id = case["id"]
        assert type(case_id) is str and case_id, case_id
        for field_name in (
            "markdown",
            "selected_text",
            "raw_selection",
            "visible_text_convention",
        ):
            assert type(case[field_name]) is str, case_id
        assert case["visible_text_convention"] in VISIBLE_TEXT_CONVENTIONS, case_id
        assert type(case["accepted"]) is bool, case_id
        assert type(case["start_byte"]) is int, case_id
        assert type(case["end_byte"]) is int, case_id
        assert type(case["source_sha256"]) is str, case_id
        assert re.fullmatch(r"[0-9a-f]{64}", case["source_sha256"]), case_id

        markdown_bytes = case["markdown"].encode("utf-8")
        assert 0 <= case["start_byte"] <= case["end_byte"] <= len(markdown_bytes), case_id

        if case["accepted"]:
            assert set(case) == COMMON_CASE_KEYS | {"expected_units"}, case_id
            assert type(case["expected_units"]) is list and case["expected_units"], case_id
            for unit in case["expected_units"]:
                assert type(unit) is dict, case_id
                assert unit["kind"] in {"heading", "paragraph", "list_item"}, case_id
                assert unit["coverage"] in {"full", "partial"}, case_id
                assert type(unit["index"]) is int and unit["index"] >= 0, case_id
                if unit["kind"] == "heading":
                    assert set(unit) == COMMON_UNIT_KEYS | {"level"}, case_id
                    assert type(unit["level"]) is int and 1 <= unit["level"] <= 6, case_id
                elif unit["kind"] == "list_item":
                    assert set(unit) == COMMON_UNIT_KEYS | {"depth", "marker"}, case_id
                    assert type(unit["depth"]) is int and unit["depth"] >= 0, case_id
                    assert type(unit["marker"]) is str, case_id
                    assert re.fullmatch(r"(?:[-+*]|\d+[.)])", unit["marker"]), case_id
                else:
                    assert set(unit) == COMMON_UNIT_KEYS, case_id
        else:
            expected_keys = COMMON_CASE_KEYS | {"error_code"}
            if case_id == "selected_text_mismatch_rejected":
                expected_keys |= {"mismatch_kind"}
                assert case["mismatch_kind"] == "visible_text", case_id
                assert case["error_code"] == "SELECTION_MAPPING_CONFLICT", case_id
            else:
                assert "mismatch_kind" not in case, case_id
            assert set(case) == expected_keys, case_id
            assert type(case["error_code"]) is str, case_id
            assert case["error_code"] in STABLE_ERROR_CODES, case_id

        table = Utf8BoundaryTable(case["markdown"])
        table.require_byte_boundary(case["start_byte"])
        table.require_byte_boundary(case["end_byte"])
        raw_bytes = markdown_bytes[case["start_byte"] : case["end_byte"]]
        assert raw_bytes.decode("utf-8") == case["raw_selection"], case["id"]
        assert hashlib.sha256(raw_bytes).hexdigest() == case["source_sha256"], case["id"]
        assert sha256_byte_range(
            case["markdown"], case["start_byte"], case["end_byte"]
        ) == case["source_sha256"], case["id"]

        computed_visible = _visible_selection(
            case["raw_selection"], case["visible_text_convention"]
        )
        if (
            case.get("error_code") == "SELECTION_MAPPING_CONFLICT"
            and case.get("mismatch_kind") == "visible_text"
        ):
            assert computed_visible != case["selected_text"], case["id"]
        else:
            assert computed_visible == case["selected_text"], case["id"]

    partial_outer = next(
        case
        for case in fixture["cases"]
        if case["id"] == "partial_outer_units_complete_middle"
    )
    assert partial_outer["accepted"] is True
    assert [unit["coverage"] for unit in partial_outer["expected_units"]] == [
        "partial",
        "full",
        "partial",
    ]
