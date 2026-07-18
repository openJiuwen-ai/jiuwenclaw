import hashlib
import json
import re
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from jiuwenclaw.agentserver.tools.deepresearch_plugin import (
    markdown_rewrite_map as rewrite_map_module,
)
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


def test_rewrite_map_public_api_exists():
    assert hasattr(rewrite_map_module, "RewriteUnit")
    assert hasattr(rewrite_map_module, "UnsupportedRegion")
    assert hasattr(rewrite_map_module, "MarkdownRewriteMap")
    assert hasattr(rewrite_map_module, "build_rewrite_map")


def test_build_rewrite_map_classifies_supported_blocks_and_preserves_raw_ranges():
    markdown = (
        "# First\n\n"
        "## Second\n\n"
        "paragraph line one\nparagraph line two\n\n"
        "- alpha\n- beta\n\n"
        "1. one\n2. two\n"
    )

    rewrite_map = rewrite_map_module.build_rewrite_map(markdown)

    assert rewrite_map.source == markdown
    assert rewrite_map.unsupported_regions == ()
    assert [unit.unit_type for unit in rewrite_map.units] == [
        "heading",
        "heading",
        "paragraph",
        "list_item",
        "list_item",
        "list_item",
        "list_item",
    ]
    assert [unit.level for unit in rewrite_map.units] == [1, 2, None, None, None, None, None]
    assert [unit.list_depth for unit in rewrite_map.units] == [
        None,
        None,
        None,
        0,
        0,
        0,
        0,
    ]
    assert [unit.list_marker for unit in rewrite_map.units] == [
        None,
        None,
        None,
        "-",
        "-",
        "1.",
        "2.",
    ]
    source_bytes = markdown.encode("utf-8")
    assert [source_bytes[unit.start_byte : unit.end_byte].decode() for unit in rewrite_map.units] == [
        "# First",
        "## Second",
        "paragraph line one\nparagraph line two",
        "- alpha",
        "- beta",
        "1. one",
        "2. two",
    ]
    assert [unit.unit_id for unit in rewrite_map.units] == [
        f"{unit.unit_type}_{ordinal}_{unit.start_byte}_{unit.end_byte}"
        for ordinal, unit in enumerate(rewrite_map.units)
    ]


def test_build_rewrite_map_uses_utf8_byte_offsets_after_unicode_prefix():
    markdown = "前言\n\n# 标题\n"

    rewrite_map = rewrite_map_module.build_rewrite_map(markdown)

    assert [(unit.start_byte, unit.end_byte) for unit in rewrite_map.units] == [
        (0, len("前言".encode("utf-8"))),
        (len("前言\n\n".encode("utf-8")), len("前言\n\n# 标题".encode("utf-8"))),
    ]


def test_build_rewrite_map_keeps_paragraph_with_inline_image_for_later_slot_validation():
    rewrite_map = rewrite_map_module.build_rewrite_map(
        "text ![a](a.png) ![b](b.png) remains text\n"
    )

    assert [unit.unit_type for unit in rewrite_map.units] == ["paragraph"]
    assert rewrite_map.unsupported_regions == ()


def test_build_rewrite_map_rejects_multiple_images_separated_only_by_whitespace():
    markdown = "![a](a.png) ![b](b.png)\n"

    rewrite_map = rewrite_map_module.build_rewrite_map(markdown)

    assert rewrite_map.units == ()
    assert [
        (region.kind, region.start_byte, region.end_byte)
        for region in rewrite_map.unsupported_regions
    ] == [("image_only", 0, len(markdown.rstrip("\n").encode("utf-8")))]


def test_build_rewrite_map_rejects_list_item_of_images_separated_only_by_whitespace():
    markdown = "- ![a](a.png) ![b](b.png)\n"

    rewrite_map = rewrite_map_module.build_rewrite_map(markdown)

    assert rewrite_map.units == ()
    assert [
        (region.kind, region.start_byte, region.end_byte)
        for region in rewrite_map.unsupported_regions
    ] == [("image_only", 0, len(markdown.rstrip("\n").encode("utf-8")))]


@pytest.mark.parametrize(
    ("kind", "markdown", "raw_region"),
    [
        ("blockquote", "> quoted", "> quoted"),
        ("table", "| a | b |\n|---|---|\n| c | d |", "| a | b |\n|---|---|\n| c | d |"),
        ("fenced_code", "```python\nx = 1\n```", "```python\nx = 1\n```"),
        ("indented_code", "    x = 1", "    x = 1"),
        ("html_block", "<div>content</div>", "<div>content</div>"),
        ("image_only", "![alt](image.png)", "![alt](image.png)"),
        ("nested_list", "- outer\n  - inner", "- outer\n  - inner"),
        (
            "compound_list_item",
            "- first paragraph\n\n  second paragraph",
            "- first paragraph\n\n  second paragraph",
        ),
    ],
)
def test_build_rewrite_map_marks_unsupported_blocks_without_paragraph_fallback(
    kind, markdown, raw_region
):
    rewrite_map = rewrite_map_module.build_rewrite_map(markdown + "\n")

    assert rewrite_map.units == ()
    assert len(rewrite_map.unsupported_regions) == 1
    region = rewrite_map.unsupported_regions[0]
    assert region.kind == kind
    assert markdown.encode("utf-8")[region.start_byte : region.end_byte].decode() == raw_region


@pytest.mark.parametrize(
    ("class_name", "args", "attribute"),
    [
        ("RewriteUnit", ("paragraph_0_0_4", "paragraph", 0, 4, None, None, None), "start_byte"),
        ("UnsupportedRegion", ("blockquote", 0, 7), "kind"),
        ("MarkdownRewriteMap", ("text", (), ()), "source"),
    ],
)
def test_rewrite_map_types_are_frozen_and_slotted(class_name, args, attribute):
    instance = getattr(rewrite_map_module, class_name)(*args)
    with pytest.raises(FrozenInstanceError):
        setattr(instance, attribute, getattr(instance, attribute))
    assert not hasattr(instance, "__dict__")


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
