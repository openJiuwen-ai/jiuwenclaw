# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for structured PDF page extraction (tables + reading order)."""

from __future__ import annotations

import pytest

from jiuwenswarm.common.pdf_layout import (
    cells_to_markdown,
    count_page_images,
    extract_page_content,
)
from tests.unit_tests.common.pdf_fixtures import (
    build_pdf,
    table_page_stream,
    two_column_page_stream,
)


def test_cells_to_markdown_matches_docx_table_format():
    markdown = cells_to_markdown([["Name", "Qty"], ["Widget", "3"]])
    assert markdown.splitlines()[:3] == [
        "| Name | Qty |",
        "| --- | --- |",
        "| Widget | 3 |",
    ]


def test_cells_to_markdown_escapes_pipes_and_pads_ragged_rows():
    markdown = cells_to_markdown([["a|b", "c"], ["only-one"]])
    assert r"a\|b" in markdown
    # The short row is padded so the table stays rectangular.
    assert "| only-one |  |" in markdown


@pytest.mark.parametrize(
    "rows",
    [None, [], [[]], [["", ""], ["", ""]], [["single"]]],
)
def test_cells_to_markdown_rejects_contentless_tables(rows):
    assert cells_to_markdown(rows) == ""


@pytest.fixture()
def table_page(tmp_path):
    pytest.importorskip("pdfplumber")
    import pdfplumber

    path = tmp_path / "table.pdf"
    path.write_bytes(build_pdf([table_page_stream()]))
    with pdfplumber.open(str(path)) as pdf:
        yield pdf.pages[0]


def test_ruled_table_becomes_markdown_in_document_order(table_page):
    content = extract_page_content(table_page)

    assert "| Name | Qty | Price |" in content
    assert "| Widget | 3 | 9.99 |" in content
    # Prose above and below the table keeps its position relative to it.
    lead = content.index("Quarterly inventory summary")
    table = content.index("| Name | Qty | Price |")
    trail = content.index("All prices are in USD")
    assert lead < table < trail


def test_table_cells_are_not_also_emitted_as_loose_prose(table_page):
    content = extract_page_content(table_page)
    # "Widget" must appear only inside the markdown row, never twice.
    assert content.count("Widget") == 1


def test_include_tables_false_falls_back_to_plain_text(table_page):
    content = extract_page_content(table_page, include_tables=False)
    assert "|" not in content
    assert "Widget" in content


def test_two_columns_are_read_in_order(tmp_path):
    pytest.importorskip("pdfplumber")
    import pdfplumber

    path = tmp_path / "columns.pdf"
    path.write_bytes(build_pdf([two_column_page_stream()]))
    with pdfplumber.open(str(path)) as pdf:
        content = extract_page_content(pdf.pages[0])

    # The left column must be finished before the right one starts. Bare
    # extract_text() interleaves them line by line, which is the bug this fixes.
    left_end = content.index("records every objection raised")
    right_start = content.index("Meanwhile the second column")
    assert left_end < right_start

    lines = [line for line in content.splitlines() if line.strip()]
    assert "The quick brown fox jumps" in lines  # not merged with the right column


def test_single_column_page_is_left_alone(tmp_path):
    pytest.importorskip("pdfplumber")
    import pdfplumber

    from tests.unit_tests.common.pdf_fixtures import text_at

    stream = "".join(text_at(72, 700 - 16 * i, f"Line number {i} of ordinary prose") for i in range(8))
    path = tmp_path / "plain.pdf"
    path.write_bytes(build_pdf([stream.encode("latin-1")]))
    with pdfplumber.open(str(path)) as pdf:
        content = extract_page_content(pdf.pages[0])

    assert "Line number 0 of ordinary prose" in content
    assert "Line number 7 of ordinary prose" in content


def _page_of(pdf_bytes: bytes):
    import io

    import pdfplumber

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        yield pdf.pages[0]


def test_count_page_images_reports_a_figure():
    pytest.importorskip("pdfplumber")
    from tests.unit_tests.common.pdf_fixtures import build_pdf_with_image

    page = next(_page_of(build_pdf_with_image()))
    assert count_page_images(page) == 1


def test_count_page_images_ignores_logo_sized_images():
    """A letterhead logo on every page must not flag every page as a figure."""
    pytest.importorskip("pdfplumber")
    from tests.unit_tests.common.pdf_fixtures import build_pdf_with_image

    page = next(_page_of(build_pdf_with_image(width=30, height=20)))
    assert count_page_images(page) == 0


def test_count_page_images_is_zero_without_images(table_page):
    assert count_page_images(table_page) == 0
