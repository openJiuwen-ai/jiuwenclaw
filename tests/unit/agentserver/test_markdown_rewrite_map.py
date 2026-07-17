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
        return re.sub(r"\n+(?:[-+*]\s+)?", "\n", normalized)
    raise AssertionError(f"accepted fixture uses unknown convention: {convention}")


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

    assert fixture["protocol_version"] == 2
    assert fixture["offset_encoding"] == "utf-8-bytes"
    assert fixture["range_semantics"] == "half-open"
    case_ids = [case["id"] for case in fixture["cases"]]
    assert len(case_ids) == len(set(case_ids))

    required = {
        "id",
        "markdown",
        "start_byte",
        "end_byte",
        "selected_text",
        "source_sha256",
        "accepted",
    }
    for case in fixture["cases"]:
        assert required <= case.keys(), case["id"]
        assert ("expected_units" in case) != ("error_code" in case), case["id"]
        table = Utf8BoundaryTable(case["markdown"])
        table.require_byte_boundary(case["start_byte"])
        table.require_byte_boundary(case["end_byte"])
        raw_bytes = case["markdown"].encode("utf-8")[
            case["start_byte"] : case["end_byte"]
        ]
        assert raw_bytes.decode("utf-8") == case["raw_selection"], case["id"]
        assert hashlib.sha256(raw_bytes).hexdigest() == case["source_sha256"], case["id"]
        assert sha256_byte_range(
            case["markdown"], case["start_byte"], case["end_byte"]
        ) == case["source_sha256"], case["id"]

        if case["accepted"]:
            assert case["expected_units"], case["id"]
            assert _visible_selection(
                case["raw_selection"], case["visible_text_convention"]
            ) == case["selected_text"], case["id"]
        else:
            assert case["error_code"], case["id"]
