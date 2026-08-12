# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from pathlib import Path

import pytest

from jiuwenswarm.agents.harness.common.tools.pdf_tools import (
    DEFAULT_MAX_CHARS,
    DEFAULT_RENDER_DPI,
    _RENDER_DPI_CEILING,
    _RENDER_DPI_FLOOR,
    _format_page_list,
    _normalize_render_request,
    _normalize_request,
    _parse_page_ranges,
    read_pdf,
    render_pdf_page,
)
from tests.unit_tests.common.pdf_fixtures import build_pdf, table_page_stream


def _pdf_page_object(index: int, font_ref: int, with_stream: bool) -> bytes:
    """Serialize one /Page dictionary (stream object number is index-derived)."""
    contents = f" /Contents {4 + 2 * index} 0 R" if with_stream else ""
    return (
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]"
        f" /Resources << /Font << /F1 {font_ref} 0 R >> >>{contents} >>"
    ).encode("latin-1")


def _pdf_stream_object(text: str | None) -> bytes:
    """Serialize a content-stream object showing ``text`` (empty when None)."""
    if text is None:
        payload = b""
    else:
        shown = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        payload = f"BT /F1 12 Tf 72 720 Td ({shown}) Tj ET".encode("latin-1")
    return f"<< /Length {len(payload)} >>\nstream\n".encode("latin-1") + payload + b"\nendstream"


def _build_minimal_pdf(pages: list[str | None]) -> bytes:
    """Hand-assemble a tiny valid PDF fixture, written from the PDF 1.4 spec.

    Each ``pages`` entry is that page's text; ``None`` produces a page with no
    content stream (no text layer). Base-14 Helvetica keeps the text
    extractable by pdfplumber/pdfminer without embedded fonts. The xref table
    is computed so the document is fully well-formed.
    """
    font_ref = 3 + 2 * len(pages)
    kid_refs = " ".join(f"{3 + 2 * i} 0 R" for i in range(len(pages)))

    bodies: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kid_refs}] /Count {len(pages)} >>".encode("latin-1"),
    ]
    for i, text in enumerate(pages):
        bodies.append(_pdf_page_object(i, font_ref, with_stream=text is not None))
        bodies.append(_pdf_stream_object(text))
    bodies.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    chunks = [b"%PDF-1.4\n"]
    positions: list[int] = []
    cursor = len(chunks[0])
    for number, body in enumerate(bodies, start=1):
        positions.append(cursor)
        piece = f"{number} 0 obj\n".encode("latin-1") + body + b"\nendobj\n"
        chunks.append(piece)
        cursor += len(piece)

    # Cross-reference table: one fixed-width 20-byte line per object (spec 7.5.4).
    table_rows = ["0000000000 65535 f "]
    table_rows.extend(f"{position:010d} 00000 n " for position in positions)
    chunks.append(f"xref\n0 {len(table_rows)}\n".encode("latin-1"))
    chunks.append(("\n".join(table_rows) + "\n").encode("latin-1"))
    chunks.append(
        f"trailer\n<< /Size {len(table_rows)} /Root 1 0 R >>\n"
        f"startxref\n{cursor}\n%%EOF\n".encode("latin-1")
    )
    return b"".join(chunks)


def test_parse_page_ranges_variants():
    assert _parse_page_ranges(None) is None
    assert _parse_page_ranges("") is None
    assert _parse_page_ranges(3) == (3,)
    assert _parse_page_ranges("1-5") == (1, 2, 3, 4, 5)
    assert _parse_page_ranges("1,3,8-10") == (1, 3, 8, 9, 10)
    assert _parse_page_ranges("3, 1") == (1, 3)
    assert _parse_page_ranges([2, "4-5"]) == (2, 4, 5)


@pytest.mark.parametrize("bad", ["a", "5-2", "0", 0, -1, "1-", True])
def test_parse_page_ranges_rejects_invalid(bad):
    with pytest.raises(ValueError):
        _parse_page_ranges(bad)


def test_format_page_list_compresses_runs():
    assert _format_page_list([1, 2, 3, 7]) == "1-3,7"
    assert _format_page_list([5]) == "5"
    assert _format_page_list([3, 1, 2, 2]) == "1-3"
    assert _format_page_list([]) == ""


def test_normalize_request_defaults_and_clamping():
    req = _normalize_request({"pdf_path": "/tmp/a.pdf"})
    assert req.pages is None
    assert req.max_chars == DEFAULT_MAX_CHARS

    req = _normalize_request({"pdf_path": "/tmp/a.pdf", "pages": "2-3", "max_chars": 10})
    assert req.pages == (2, 3)
    assert req.max_chars == 1_000  # floor

    req = _normalize_request({"pdf_path": "/tmp/a.pdf", "max_chars": 10**9})
    assert req.max_chars == 200_000  # ceiling

    with pytest.raises(ValueError):
        _normalize_request({})


@pytest.mark.asyncio
async def test_read_pdf_extracts_pages_and_flags_blank(tmp_path: Path):
    pytest.importorskip("pdfplumber")
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(
        _build_minimal_pdf(["Hello page one", "Second page text", None])
    )

    result = await read_pdf.invoke({"inputs": {"pdf_path": str(pdf_path)}})
    assert "total pages: 3" in result
    assert "--- Page 1 ---" in result
    assert "Hello page one" in result
    assert "Second page text" in result
    assert "no text layer" in result
    assert "Pages without extractable text: 3" in result


@pytest.mark.asyncio
async def test_read_pdf_respects_page_selection(tmp_path: Path):
    pytest.importorskip("pdfplumber")
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(_build_minimal_pdf(["Alpha", "Bravo", "Charlie"]))

    result = await read_pdf.invoke({"inputs": {"pdf_path": str(pdf_path), "pages": "2"}})
    assert "Bravo" in result
    assert "Alpha" not in result
    assert "Charlie" not in result

    result = await read_pdf.invoke({"inputs": {"pdf_path": str(pdf_path), "pages": "2,9"}})
    assert "Bravo" in result
    assert "exceed" in result  # out-of-range note for page 9


@pytest.mark.asyncio
async def test_read_pdf_truncates_at_max_chars(tmp_path: Path):
    pytest.importorskip("pdfplumber")
    long_text = "word " * 500  # ~2500 chars on one page
    pdf_path = tmp_path / "long.pdf"
    pdf_path.write_bytes(_build_minimal_pdf([long_text.strip(), "Tail page"]))

    result = await read_pdf.invoke(
        {"inputs": {"pdf_path": str(pdf_path), "max_chars": 1000}}
    )
    assert "truncated at max_chars" in result
    assert "Tail page" not in result
    # Truncation must list the unread pages so the model can continue in chunks
    assert "unread pages: 2" in result


@pytest.mark.asyncio
async def test_read_pdf_relative_path_anchors_to_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    pytest.importorskip("pdfplumber")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "docs").mkdir()
    (workspace / "docs" / "note.pdf").write_bytes(_build_minimal_pdf(["Workspace anchored"]))
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.common.tools.pdf_tools.get_agent_workspace_dir",
        lambda: workspace,
    )

    result = await read_pdf.invoke({"inputs": {"pdf_path": "docs/note.pdf"}})
    assert "Workspace anchored" in result


@pytest.mark.asyncio
async def test_read_pdf_error_paths(tmp_path: Path):
    result = await read_pdf.invoke({"inputs": {"pdf_path": str(tmp_path / "missing.pdf")}})
    assert result.startswith("[ERROR]")

    not_pdf = tmp_path / "note.txt"
    not_pdf.write_text("hi", encoding="utf-8")
    result = await read_pdf.invoke({"inputs": {"pdf_path": str(not_pdf)}})
    assert result.startswith("[ERROR]")
    assert "only accepts .pdf" in result

    result = await read_pdf.invoke({"inputs": {}})
    assert result.startswith("[ERROR]")


def test_normalize_request_parses_include_tables():
    assert _normalize_request({"pdf_path": "/tmp/a.pdf"}).include_tables is True
    assert _normalize_request({"pdf_path": "/tmp/a.pdf", "include_tables": False}).include_tables is False
    assert _normalize_request({"pdf_path": "/tmp/a.pdf", "include_tables": "false"}).include_tables is False


def test_normalize_render_request_clamps_dpi():
    assert _normalize_render_request({"pdf_path": "/tmp/a.pdf"}).dpi == DEFAULT_RENDER_DPI
    assert _normalize_render_request({"pdf_path": "/tmp/a.pdf", "dpi": 5}).dpi == _RENDER_DPI_FLOOR
    assert (
        _normalize_render_request({"pdf_path": "/tmp/a.pdf", "dpi": 10_000}).dpi
        == _RENDER_DPI_CEILING
    )

    with pytest.raises(ValueError):
        _normalize_render_request({})


def test_tools_declare_a_real_parameter_schema():
    """An opaque ``{"inputs": {"type": "object"}}`` gives the model nothing to go on."""
    for card in (read_pdf.card, render_pdf_page.card):
        schema = card.input_params["properties"]["inputs"]
        assert schema["required"] == ["pdf_path"]
        assert "pages" in schema["properties"]
        assert schema["properties"]["pdf_path"]["type"] == "string"


@pytest.mark.asyncio
async def test_read_pdf_renders_tables_as_markdown(tmp_path: Path):
    pytest.importorskip("pdfplumber")
    pdf_path = tmp_path / "table.pdf"
    pdf_path.write_bytes(build_pdf([table_page_stream()]))

    result = await read_pdf.invoke({"inputs": {"pdf_path": str(pdf_path)}})
    assert "| Name | Qty | Price |" in result
    assert "| Widget | 3 | 9.99 |" in result

    raw = await read_pdf.invoke(
        {"inputs": {"pdf_path": str(pdf_path), "include_tables": False}}
    )
    # The header line carries pipes of its own, so look for table rows specifically.
    assert "| Name | Qty | Price |" not in raw
    assert "| --- |" not in raw
    assert "Widget" in raw


@pytest.mark.asyncio
async def test_blank_page_hint_names_a_tool_that_exists(tmp_path: Path):
    """The scanned-PDF fallback must point at render_pdf_page, not thin air."""
    pytest.importorskip("pdfplumber")
    pdf_path = tmp_path / "scanned.pdf"
    pdf_path.write_bytes(_build_minimal_pdf([None]))

    result = await read_pdf.invoke({"inputs": {"pdf_path": str(pdf_path)}})
    assert "render_pdf_page" in result
    assert "visual_question_answering" in result


@pytest.mark.asyncio
async def test_render_pdf_page_writes_pngs_in_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    pytest.importorskip("pdfplumber")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.common.tools.pdf_tools.get_cwd",
        lambda: str(cwd),
    )
    pdf_path = tmp_path / "doc.pdf"
    pdf_path.write_bytes(_build_minimal_pdf(["Alpha", "Bravo"]))

    result = await render_pdf_page.invoke(
        {"inputs": {"pdf_path": str(pdf_path), "pages": "2", "dpi": _RENDER_DPI_FLOOR}}
    )

    assert "- page 2:" in result
    assert "visual_question_answering" in result
    rendered = sorted((cwd / ".pdf_pages").glob("*.png"))
    assert [p.name for p in rendered] == ["doc__page_2.png"]
    assert rendered[0].stat().st_size > 0


@pytest.mark.asyncio
async def test_rendered_pages_stay_inside_the_fs_sandbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Regression: PNGs used to land in the agent workspace root.

    ``fs_operation`` admits only ``[workspace, project_root, cwd]``. The agent
    workspace root is the parent of all three, so ``read_file`` refused every
    page this tool rendered.
    """
    pytest.importorskip("pdfplumber")
    agent_workspace = tmp_path / "agent" / "workspace"
    sandbox_roots = [
        agent_workspace / "projects" / "session",
        agent_workspace / "sub_agents" / "helper",
    ]
    for root in sandbox_roots:
        root.mkdir(parents=True)
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.common.tools.pdf_tools.get_agent_workspace_dir",
        lambda: agent_workspace,
    )
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.common.tools.pdf_tools.get_cwd",
        lambda: str(sandbox_roots[0]),
    )
    pdf_path = tmp_path / "doc.pdf"
    pdf_path.write_bytes(_build_minimal_pdf(["Alpha"]))

    result = await render_pdf_page.invoke(
        {"inputs": {"pdf_path": str(pdf_path), "dpi": _RENDER_DPI_FLOOR}}
    )

    rendered = Path(result.rsplit(": ", 1)[-1].splitlines()[0])
    assert rendered.is_file()
    assert any(rendered.is_relative_to(root) for root in sandbox_roots)


@pytest.mark.asyncio
async def test_relative_pdf_path_resolves_against_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    pytest.importorskip("pdfplumber")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    (cwd / "doc.pdf").write_bytes(_build_minimal_pdf(["Alpha"]))
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.common.tools.pdf_tools.get_cwd",
        lambda: str(cwd),
    )

    result = await read_pdf.invoke({"inputs": {"pdf_path": "doc.pdf"}})

    assert "Alpha" in result


@pytest.mark.asyncio
async def test_render_pdf_page_error_paths(tmp_path: Path):
    result = await render_pdf_page.invoke({"inputs": {"pdf_path": str(tmp_path / "missing.pdf")}})
    assert result.startswith("[ERROR]")

    result = await render_pdf_page.invoke({"inputs": {}})
    assert result.startswith("[ERROR]")


@pytest.mark.asyncio
async def test_page_with_a_figure_is_flagged_and_routed(tmp_path: Path):
    """A page can extract perfectly and still hide the answer in a figure.

    Without this the model sees complete-looking prose, has only the .pdf path
    to offer a vision tool, and gets an opaque API error for its trouble.
    """
    pytest.importorskip("pdfplumber")
    from tests.unit_tests.common.pdf_fixtures import build_pdf_with_image

    pdf_path = tmp_path / "figure.pdf"
    pdf_path.write_bytes(build_pdf_with_image())

    result = await read_pdf.invoke({"inputs": {"pdf_path": str(pdf_path)}})

    # The text layer still comes back in full.
    assert "Figure 1 shows the architecture." in result
    # ... alongside the fact that it is not the whole page.
    assert "1 embedded image(s) on this page" in result
    assert "Pages containing images: 1" in result
    assert "render_pdf_page" in result
    # And the mistake that actually happened is named.
    assert "figure.pdf to one directly will fail" in result


@pytest.mark.asyncio
async def test_text_only_page_is_not_flagged_as_carrying_images(tmp_path: Path):
    pytest.importorskip("pdfplumber")
    pdf_path = tmp_path / "plain.pdf"
    pdf_path.write_bytes(_build_minimal_pdf(["Just prose here."]))

    result = await read_pdf.invoke({"inputs": {"pdf_path": str(pdf_path)}})
    assert "embedded image" not in result
    assert "Pages containing images" not in result
