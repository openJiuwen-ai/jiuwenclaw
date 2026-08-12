# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Page-level PDF reading and rendering tools.

``read_pdf`` extracts the text layer with pdfplumber, rendering ruled tables as
Markdown and reading multi-column pages in order (see
``jiuwenswarm.common.pdf_layout``). Page ranges let large documents be read
incrementally.

``render_pdf_page`` rasterises pages to PNG. Scanned PDFs have no text layer at
all, so ``read_pdf`` can only report that they are empty — rendering them is what
makes them reachable by ``visual_question_answering``, which already performs OCR.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openjiuwen.core.foundation.tool import tool
from openjiuwen.core.sys_operation.cwd import get_cwd

from jiuwenswarm.common.pdf_layout import count_page_images, extract_page_content
from jiuwenswarm.common.utils import get_agent_workspace_dir

logger = logging.getLogger(__name__)

DEFAULT_MAX_CHARS = 50_000
_MAX_CHARS_CEILING = 200_000
_MAX_PAGES_PER_CALL = 100

DEFAULT_RENDER_DPI = 150
_RENDER_DPI_FLOOR = 75
_RENDER_DPI_CEILING = 300
_RENDERED_PAGES_DIRNAME = ".pdf_pages"


@dataclass(frozen=True)
class ReadPdfRequest:
    pdf_path: str
    pages: tuple[int, ...] | None  # 1-based page numbers; None = all pages
    max_chars: int = DEFAULT_MAX_CHARS
    include_tables: bool = True


@dataclass(frozen=True)
class RenderPdfRequest:
    pdf_path: str
    pages: tuple[int, ...] | None
    dpi: int = DEFAULT_RENDER_DPI


def _parse_page_ranges(value: Any) -> tuple[int, ...] | None:
    """Parse ``pages`` input into sorted unique 1-based page numbers.

    Accepts an int (single page), a list of ints, or a string such as
    ``"1-5"``, ``"1,3,8-10"``. Empty / None means all pages.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("pages must be a page number, list, or range string like '1-5,8'")
    if isinstance(value, int):
        if value < 1:
            raise ValueError(f"page numbers are 1-based, got: {value}")
        return (value,)
    if isinstance(value, (list, tuple)):
        pages: set[int] = set()
        for entry in value:
            parsed = _parse_page_ranges(entry)
            if parsed:
                pages.update(parsed)
        return tuple(sorted(pages)) or None

    text = str(value).strip()
    if not text:
        return None
    pages = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_text, _, end_text = part.partition("-")
            try:
                start, end = int(start_text), int(end_text)
            except ValueError as exc:
                raise ValueError(f"Invalid page range: {part!r}. Use forms like '1-5' or '3'.") from exc
            if start < 1 or end < start:
                raise ValueError(f"Invalid page range: {part!r} (pages are 1-based, start <= end)")
            pages.update(range(start, end + 1))
        else:
            try:
                page = int(part)
            except ValueError as exc:
                raise ValueError(f"Invalid page number: {part!r}") from exc
            if page < 1:
                raise ValueError(f"page numbers are 1-based, got: {page}")
            pages.add(page)
    return tuple(sorted(pages)) or None


def _sandbox_anchor() -> Path:
    """Base directory whose contents the calling agent can also read back."""
    cwd = Path(get_cwd())
    return cwd if cwd.is_dir() else get_agent_workspace_dir()


def _resolve_pdf_path(value: str) -> Path:
    """Resolve ``pdf_path`` like other always-on tools (see wiki_ingest).

    Relative paths anchor to the agent's working directory, then to the agent
    workspace, never to the process CWD. Absolute paths are accepted as-is —
    local file access is gated by the permission rail, matching read_file's
    trust model.
    """
    text = (value or "").strip()
    if not text:
        raise ValueError("pdf_path cannot be empty")
    path = Path(text).expanduser()
    if not path.is_absolute():
        candidate = (_sandbox_anchor() / path).resolve()
        # The workspace is still worth trying: agent-owned material (skills,
        # memory) lives there and is named relative to it.
        if not candidate.exists():
            fallback = (get_agent_workspace_dir() / path).resolve()
            if fallback.exists():
                candidate = fallback
        path = candidate
    path = path.resolve()
    if not path.exists():
        raise FileNotFoundError(f"PDF file does not exist: {path}")
    if not path.is_file():
        raise ValueError(f"pdf_path is not a file: {path}")
    if path.suffix.lower() != ".pdf":
        raise ValueError(f"read_pdf only accepts .pdf files, got: {path.name}")
    return path


def _coerce_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
        return default
    return bool(value)


def _normalize_request(inputs: dict[str, Any]) -> ReadPdfRequest:
    pdf_path = str(inputs.get("pdf_path", "") or inputs.get("path", "") or "").strip()
    if not pdf_path:
        raise ValueError("pdf_path cannot be empty.")
    pages = _parse_page_ranges(inputs.get("pages"))
    try:
        max_chars = int(inputs.get("max_chars", DEFAULT_MAX_CHARS))
    except (TypeError, ValueError):
        max_chars = DEFAULT_MAX_CHARS
    max_chars = max(1_000, min(max_chars, _MAX_CHARS_CEILING))
    include_tables = _coerce_bool(inputs.get("include_tables"), True)
    return ReadPdfRequest(
        pdf_path=pdf_path, pages=pages, max_chars=max_chars, include_tables=include_tables
    )


def _normalize_render_request(inputs: dict[str, Any]) -> RenderPdfRequest:
    pdf_path = str(inputs.get("pdf_path", "") or inputs.get("path", "") or "").strip()
    if not pdf_path:
        raise ValueError("pdf_path cannot be empty.")
    pages = _parse_page_ranges(inputs.get("pages"))
    try:
        dpi = int(inputs.get("dpi", DEFAULT_RENDER_DPI))
    except (TypeError, ValueError):
        dpi = DEFAULT_RENDER_DPI
    dpi = max(_RENDER_DPI_FLOOR, min(dpi, _RENDER_DPI_CEILING))
    return RenderPdfRequest(pdf_path=pdf_path, pages=pages, dpi=dpi)


def _read_pdf_sync(req: ReadPdfRequest) -> str:
    # Validate the path before importing pdfplumber so path errors surface
    # clearly even in environments where the dependency is missing.
    path = _resolve_pdf_path(req.pdf_path)

    import pdfplumber

    blocks: list[str] = []
    empty_pages: list[int] = []
    image_pages: list[int] = []
    with pdfplumber.open(str(path)) as pdf:
        total_pages = len(pdf.pages)
        if req.pages is not None:
            selected = [p for p in req.pages if p <= total_pages]
            out_of_range = [p for p in req.pages if p > total_pages]
        else:
            selected = list(range(1, total_pages + 1))
            out_of_range = []

        truncated_pages = selected[_MAX_PAGES_PER_CALL:]
        selected = selected[:_MAX_PAGES_PER_CALL]

        header = [f"PDF: {path.name} | total pages: {total_pages} | reading pages: "
                  + (_format_page_list(selected) if selected else "none")]
        if out_of_range:
            header.append(
                f"[Note: requested page(s) {_format_page_list(out_of_range)} exceed "
                f"total page count {total_pages} and were skipped]"
            )
        if truncated_pages:
            header.append(
                f"[Note: at most {_MAX_PAGES_PER_CALL} pages per call; "
                f"pages {_format_page_list(truncated_pages)} were not read — "
                "call read_pdf again with a narrower `pages` range]"
            )
        blocks.append("\n".join(header))

        chars_used = 0
        for idx, page_num in enumerate(selected):
            page = pdf.pages[page_num - 1]
            try:
                page_text = extract_page_content(page, include_tables=req.include_tables)
            except Exception:
                logger.warning("[read_pdf] page %s structured extraction failed", page_num, exc_info=True)
                page_text = (page.extract_text() or "").strip()
            image_count = count_page_images(page)
            if image_count:
                image_pages.append(page_num)
            if not page_text:
                empty_pages.append(page_num)
                blocks.append(f"--- Page {page_num} ---\n[no text layer on this page]")
                continue
            if image_count:
                # A figure extracts as nothing at all, so without this the page
                # looks complete while the answer sits in the pixels.
                page_text += (
                    f"\n\n[{image_count} embedded image(s) on this page — figures, charts or "
                    "diagrams whose content is not in the text layer above]"
                )
            remaining = req.max_chars - chars_used
            if remaining <= 0:
                unread = selected[idx:]
            else:
                truncated_here = len(page_text) > remaining
                if truncated_here:
                    page_text = page_text[:remaining] + "\n[... page truncated at max_chars ...]"
                chars_used += len(page_text)
                blocks.append(f"--- Page {page_num} ---\n{page_text}")
                if not truncated_here:
                    continue
                unread = selected[idx + 1:]
            note = f"[Truncated at max_chars={req.max_chars}"
            if unread:
                note += (
                    f"; unread pages: {_format_page_list(unread)} — "
                    "call read_pdf again with `pages` starting there"
                )
            note += "]"
            blocks.append(note)
            break

    if empty_pages:
        blocks.append(
            f"[Pages without extractable text: {_format_page_list(empty_pages)}. "
            "These are likely scanned images. Call render_pdf_page on those pages, "
            "then pass each returned PNG path to visual_question_answering, which "
            "performs OCR.]"
        )
    if image_pages:
        blocks.append(
            f"[Pages containing images: {_format_page_list(image_pages)}. To read a "
            "figure, chart or diagram, call render_pdf_page on that page and pass the "
            "returned PNG path to visual_question_answering. Vision tools take an "
            f"image file — passing {path.name} to one directly will fail.]"
        )
    return "\n\n".join(blocks)


def _render_pdf_sync(req: RenderPdfRequest) -> str:
    path = _resolve_pdf_path(req.pdf_path)

    import pdfplumber

    output_dir = _sandbox_anchor() / _RENDERED_PAGES_DIRNAME
    output_dir.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    with pdfplumber.open(str(path)) as pdf:
        total_pages = len(pdf.pages)
        if req.pages is not None:
            selected = [p for p in req.pages if p <= total_pages]
            out_of_range = [p for p in req.pages if p > total_pages]
        else:
            selected = list(range(1, total_pages + 1))
            out_of_range = []

        lines.append(
            f"PDF: {path.name} | total pages: {total_pages} | rendered at {req.dpi} DPI"
        )
        if out_of_range:
            lines.append(
                f"[Note: page(s) {_format_page_list(out_of_range)} exceed total "
                f"page count {total_pages} and were skipped]"
            )

        rendered = 0
        for page_num in selected:
            page = pdf.pages[page_num - 1]
            image_path = output_dir / f"{path.stem}__page_{page_num}.png"
            try:
                page.to_image(resolution=req.dpi).original.save(str(image_path))
            except Exception as exc:
                lines.append(f"- page {page_num}: [render failed: {exc}]")
                continue
            rendered += 1
            lines.append(f"- page {page_num}: {image_path}")

        if not rendered:
            lines.append("[No pages were rendered.]")
        else:
            lines.append(
                "Pass any of these paths to visual_question_answering to read the "
                "page contents (it performs OCR)."
            )

    return "\n".join(lines)


def _format_page_list(pages: list[int] | tuple[int, ...]) -> str:
    """Compress sorted page numbers into a compact range string, e.g. 1-3,7."""
    if not pages:
        return ""
    ordered = sorted(set(int(p) for p in pages))
    parts: list[str] = []
    start = prev = ordered[0]
    for page in ordered[1:]:
        if page == prev + 1:
            prev = page
            continue
        parts.append(str(start) if start == prev else f"{start}-{prev}")
        start = prev = page
    parts.append(str(start) if start == prev else f"{start}-{prev}")
    return ",".join(parts)


_PAGES_SCHEMA: dict[str, Any] = {
    "description": (
        "Pages to read (1-based). A number (3), a list ([1, 3]), or a range "
        "string ('1-5', '1,3,8-10'). Omit to read the whole document."
    ),
    "anyOf": [
        {"type": "integer", "minimum": 1},
        {"type": "string"},
        {"type": "array", "items": {"type": ["integer", "string"]}},
    ],
}

_READ_PDF_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "inputs": {
            "type": "object",
            "description": "read_pdf arguments.",
            "properties": {
                "pdf_path": {
                    "type": "string",
                    "description": (
                        "Path to the .pdf file. Relative paths resolve against the "
                        "working directory."
                    ),
                },
                "pages": _PAGES_SCHEMA,
                "max_chars": {
                    "type": "integer",
                    "description": (
                        f"Character budget for this call (default {DEFAULT_MAX_CHARS}, "
                        f"clamped to 1000-{_MAX_CHARS_CEILING})."
                    ),
                    "minimum": 1_000,
                    "maximum": _MAX_CHARS_CEILING,
                },
                "include_tables": {
                    "type": "boolean",
                    "description": (
                        "Render ruled tables as Markdown (default true). Set false "
                        "for raw text-layer output."
                    ),
                },
            },
            "required": ["pdf_path"],
        }
    },
    "required": ["inputs"],
}

_RENDER_PDF_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "inputs": {
            "type": "object",
            "description": "render_pdf_page arguments.",
            "properties": {
                "pdf_path": {
                    "type": "string",
                    "description": (
                        "Path to the .pdf file. Relative paths resolve against the "
                        "working directory."
                    ),
                },
                "pages": _PAGES_SCHEMA,
                "dpi": {
                    "type": "integer",
                    "description": (
                        f"Render resolution (default {DEFAULT_RENDER_DPI}, clamped "
                        f"to {_RENDER_DPI_FLOOR}-{_RENDER_DPI_CEILING}). Higher is slower and larger."
                    ),
                    "minimum": _RENDER_DPI_FLOOR,
                    "maximum": _RENDER_DPI_CEILING,
                },
            },
            "required": ["pdf_path"],
        }
    },
    "required": ["inputs"],
}


@tool(
    name="read_pdf",
    description=(
        "Read the text content of a PDF file, optionally limited to specific pages. "
        "Ruled tables come back as Markdown and multi-column pages are read in "
        "order. Use this tool when a PDF file path is available and its content is "
        "needed. For large documents, first read page 1 to learn the structure and "
        "total page count, then read subsequent chunks with the `pages` parameter. "
        "Input: pdf_path (local file path), optional pages (e.g. 3, '1-5' or "
        "'1,3,8-10'), optional max_chars (default 50000), optional include_tables "
        "(default true). Pages without a text layer are reported as likely scanned "
        "images, and pages carrying figures or charts are flagged — use "
        "render_pdf_page on those, then visual_question_answering on the PNG it "
        "returns. Never pass the .pdf path itself to a vision tool."
    ),
    input_params=_READ_PDF_SCHEMA,
)
async def read_pdf(inputs: dict[str, Any], **kwargs) -> str:
    _ = kwargs
    try:
        req = _normalize_request(inputs or {})
        logger.info("[read_pdf] path=%s pages=%s max_chars=%s", req.pdf_path, req.pages, req.max_chars)
        return await asyncio.to_thread(_read_pdf_sync, req)
    except Exception as exc:
        return f"[ERROR]: read_pdf failed: {exc}"


@tool(
    name="render_pdf_page",
    description=(
        "Render PDF pages to PNG images and return their file paths. Use this for "
        "scanned PDFs and for pages read_pdf reports as having no text layer, then "
        "pass each returned path to visual_question_answering, which performs OCR. "
        "Also useful when a page's figures or charts matter. Input: pdf_path (local "
        "file path), optional pages (e.g. 3, '1-5' or '1,3,8-10'), optional dpi "
        "(default 150)."
    ),
    input_params=_RENDER_PDF_SCHEMA,
)
async def render_pdf_page(inputs: dict[str, Any], **kwargs) -> str:
    _ = kwargs
    try:
        req = _normalize_render_request(inputs or {})
        logger.info("[render_pdf_page] path=%s pages=%s dpi=%s", req.pdf_path, req.pages, req.dpi)
        return await asyncio.to_thread(_render_pdf_sync, req)
    except Exception as exc:
        return f"[ERROR]: render_pdf_page failed: {exc}"
