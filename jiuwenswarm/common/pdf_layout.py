# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared PDF page extraction: ruled tables as Markdown, prose in reading order.

Imported by ``jiuwenswarm/agents/harness/common/tools/pdf_tools.py``, which backs
the ``read_pdf`` and ``render_pdf_page`` tools — the only rail that reads PDFs
now that the gateway hands the agent a path instead of parsed text.

Bare ``page.extract_text()`` loses the two things that cost the most answer
quality: table structure (cells come back as loose interleaved text) and reading
order on multi-column pages (pdfplumber emits words in raw char order, so two
columns interleave line by line). This module fixes both, and degrades to plain
``extract_text()`` whenever detection is not confident.

The Markdown table format deliberately matches
``openjiuwen…parser.word_parser._table_to_markdown`` so PDF and DOCX output agree.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# A gutter must be this wide (fraction of page width) to count as a column split.
_MIN_GUTTER_WIDTH_RATIO = 0.02
# Only look for a gutter in the middle of the page — margins are always empty.
_GUTTER_SEARCH_BAND = (0.30, 0.70)
# Below this many words, column detection is noise; keep single-column order.
_MIN_WORDS_FOR_COLUMNS = 40
# Each column must hold at least this share of the page's words.
_MIN_COLUMN_WORD_SHARE = 0.15
# Horizontal resolution of the occupancy scan.
_OCCUPANCY_BINS = 200
# Slack when deciding whether a word sits beside a table rather than inside it.
_SIDE_TEXT_MARGIN = 2.0
# An embedded image must cover this share of the page to be worth reporting.
# Below it sit logos, rules, bullet glyphs and signature scans — noise that
# would otherwise flag nearly every page as carrying a figure.
_MIN_IMAGE_AREA_RATIO = 0.02


def count_page_images(page: Any) -> int:
    """Number of embedded images on ``page`` large enough to carry content.

    The text layer says nothing about figures, charts and diagrams, so a page
    can extract perfectly and still be missing the part the user is asking
    about. Callers use this to say so explicitly, and to point at
    ``render_pdf_page`` — the only route from a PDF to something a vision model
    accepts.
    """
    try:
        images = page.images or []
        x0, top, x1, bottom = page.bbox
    except Exception:  # pragma: no cover - pdfminer edge cases
        logger.debug("image detection failed on a page", exc_info=True)
        return 0

    page_area = (x1 - x0) * (bottom - top)
    if page_area <= 0:
        return 0

    count = 0
    for image in images:
        try:
            area = (float(image["x1"]) - float(image["x0"])) * (
                float(image["bottom"]) - float(image["top"])
            )
        except (KeyError, TypeError, ValueError):
            continue
        if area / page_area >= _MIN_IMAGE_AREA_RATIO:
            count += 1
    return count


def cells_to_markdown(rows: list[list[Any]] | None) -> str:
    """Render extracted table cells as a Markdown table.

    Mirrors ``word_parser._table_to_markdown`` so PDF and DOCX tables look
    identical downstream. Returns ``""`` when there is nothing worth emitting.
    """
    if not rows:
        return ""

    cleaned: list[list[str]] = []
    for row in rows:
        if not row:
            continue
        cleaned.append([str(cell or "").strip().replace("|", r"\|").replace("\n", " ") for cell in row])
    if not cleaned:
        return ""
    # A table whose cells are all empty carries no information.
    if not any(any(cell for cell in row) for row in cleaned):
        return ""

    width = max(len(row) for row in cleaned)
    if width < 2:
        return ""
    padded = [row + [""] * (width - len(row)) for row in cleaned]

    lines = ["| " + " | ".join(padded[0]) + " |", "| " + " | ".join("---" for _ in range(width)) + " |"]
    lines.extend("| " + " | ".join(row) + " |" for row in padded[1:])
    lines.append("")  # Markdown tables need a blank line after them.
    return "\n".join(lines)


def _detect_column_bboxes(page: Any) -> list[tuple[float, float, float, float]] | None:
    """Return two column bboxes when the page has a clear vertical gutter.

    Conservative by design: returns ``None`` unless there is a wide, fully empty
    vertical band near the middle of the page with substantial text on both
    sides. Only the two-column case is handled — it covers papers, reports and
    slide exports, and anything more exotic falls back to single-column order.
    """
    try:
        words = page.extract_words()
    except Exception:  # pragma: no cover - pdfminer edge cases
        logger.debug("extract_words failed during column detection", exc_info=True)
        return None

    if len(words) < _MIN_WORDS_FOR_COLUMNS:
        return None

    x0, top, x1, bottom = page.bbox
    width = x1 - x0
    if width <= 0:
        return None

    # Mark every horizontal bin covered by at least one word.
    occupied = [False] * _OCCUPANCY_BINS
    for word in words:
        start = int((word["x0"] - x0) / width * _OCCUPANCY_BINS)
        end = int((word["x1"] - x0) / width * _OCCUPANCY_BINS)
        for index in range(max(0, start), min(_OCCUPANCY_BINS - 1, end) + 1):
            occupied[index] = True

    band_start = int(_GUTTER_SEARCH_BAND[0] * _OCCUPANCY_BINS)
    band_end = int(_GUTTER_SEARCH_BAND[1] * _OCCUPANCY_BINS)
    min_bins = max(1, int(_MIN_GUTTER_WIDTH_RATIO * _OCCUPANCY_BINS))

    # Longest empty run inside the search band.
    best_start = best_len = 0
    run_start = None
    for index in range(band_start, band_end):
        if not occupied[index]:
            if run_start is None:
                run_start = index
            run_len = index - run_start + 1
            if run_len > best_len:
                best_start, best_len = run_start, run_len
        else:
            run_start = None

    if best_len < min_bins:
        return None

    split_x = x0 + ((best_start + best_len / 2) / _OCCUPANCY_BINS) * width

    left = sum(1 for word in words if word["x1"] <= split_x)
    right = sum(1 for word in words if word["x0"] >= split_x)
    minimum = _MIN_COLUMN_WORD_SHARE * len(words)
    if left < minimum or right < minimum:
        return None

    return [(x0, top, split_x, bottom), (split_x, top, x1, bottom)]


def _crop(page: Any, bbox: tuple[float, float, float, float]) -> Any | None:
    """Crop ``page`` to ``bbox``, tolerating degenerate or out-of-bounds boxes."""
    x0, top, x1, bottom = bbox
    if x1 - x0 <= 1 or bottom - top <= 1:
        return None
    try:
        return page.crop((x0, top, x1, bottom))
    except Exception:
        try:
            return page.crop((x0, top, x1, bottom), strict=False)
        except Exception:
            logger.debug("crop failed for bbox=%s", bbox, exc_info=True)
            return None


def _extract_ordered_text(page: Any) -> str:
    """Extract text in reading order, splitting columns when one is detected."""
    columns = _detect_column_bboxes(page)
    if not columns:
        return (page.extract_text() or "").strip()

    parts: list[str] = []
    for bbox in columns:
        cropped = _crop(page, bbox)
        if cropped is None:
            continue
        text = (cropped.extract_text() or "").strip()
        if text:
            parts.append(text)
    if not parts:
        return (page.extract_text() or "").strip()
    return "\n\n".join(parts)


def _inside(obj: dict[str, Any], bbox: tuple[float, float, float, float]) -> bool:
    x0, top, x1, bottom = bbox
    return obj["x0"] >= x0 and obj["x1"] <= x1 and obj["top"] >= top and obj["bottom"] <= bottom


def _has_side_text(words: list[dict[str, Any]], bbox: tuple[float, float, float, float]) -> bool:
    """True when any word shares the table's vertical band but sits outside it.

    This is what decides how a table is emitted. When nothing is beside it, the
    page can be cut into horizontal bands, which keeps the table in document
    order relative to the prose above and below. When something *is* beside it
    (a sidebar, a figure caption, a second column), band-cutting would silently
    drop that text, so the table's objects are filtered out of the prose instead
    and its Markdown is appended at the end.
    """
    x0, top, x1, bottom = bbox
    for word in words:
        if word["bottom"] <= top or word["top"] >= bottom:
            continue  # entirely above or below the table
        if word["x1"] <= x0 - _SIDE_TEXT_MARGIN or word["x0"] >= x1 + _SIDE_TEXT_MARGIN:
            return True
    return False


def extract_page_content(page: Any, *, include_tables: bool = True) -> str:
    """Return one page's content: Markdown tables plus prose, in document order.

    Tables with nothing beside them split the page into horizontal bands, which
    keeps them in document order. Tables with text alongside are cut out of the
    prose and appended, so that side text is not lost and cells are not emitted
    twice.
    """
    if not include_tables:
        return _extract_ordered_text(page)

    banded: list[tuple[tuple[float, float, float, float], str]] = []
    inline: list[tuple[tuple[float, float, float, float], str]] = []
    try:
        words = page.extract_words()
        for table in page.find_tables():
            markdown = cells_to_markdown(table.extract())
            if not markdown:
                continue
            bbox = tuple(table.bbox)
            (inline if _has_side_text(words, bbox) else banded).append((bbox, markdown))
    except Exception:
        # Table detection is best-effort; never lose the page over it.
        logger.debug("find_tables failed on a page, falling back to text only", exc_info=True)
        banded, inline = [], []

    prose_page = page
    if inline:
        boxes = [bbox for bbox, _ in inline]
        try:
            prose_page = page.filter(lambda obj: not any(_inside(obj, box) for box in boxes))
        except Exception:
            logger.debug("filter failed for inline tables", exc_info=True)
            prose_page = page

    if not banded:
        segments = [_extract_ordered_text(prose_page)]
        segments.extend(markdown for _, markdown in inline)
        return "\n\n".join(segment for segment in segments if segment).strip()

    # Walk down the page, emitting the prose band above each table, then the
    # table itself.
    banded.sort(key=lambda item: item[0][1])
    px0, ptop, px1, pbottom = page.bbox
    segments: list[str] = []
    cursor = ptop
    for bbox, markdown in banded:
        if bbox[1] > cursor:
            band = _crop(prose_page, (px0, cursor, px1, bbox[1]))
            if band is not None:
                text = _extract_ordered_text(band)
                if text:
                    segments.append(text)
        segments.append(markdown)
        cursor = max(cursor, bbox[3])

    if cursor < pbottom:
        band = _crop(prose_page, (px0, cursor, px1, pbottom))
        if band is not None:
            text = _extract_ordered_text(band)
            if text:
                segments.append(text)

    segments.extend(markdown for _, markdown in inline)
    return "\n\n".join(segment for segment in segments if segment).strip()
