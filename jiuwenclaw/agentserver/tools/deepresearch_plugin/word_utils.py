# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from __future__ import annotations

import base64
import io
import re
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import unquote, urlparse

from bs4 import BeautifulSoup, NavigableString
from docx.enum.text import WD_LINE_SPACING
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.opc.constants import RELATIONSHIP_TYPE
from docx.shared import Pt

MAX_HTML_BLOCK_DEPTH = 100
HEADING_TAGS = frozenset(f"h{i}" for i in range(1, 10))
REMOTE_IMAGE_SCHEMES = frozenset({"http", "https"})
HTML_FORMATTING_WHITESPACE_RE = re.compile(r"[ \t]*\n[ \t]*")
HEADING_STYLE_SIZES = {
    "Heading 1": 22,
    "Heading 2": 18,
    "Heading 3": 16,
    "Heading 4": 14,
    "Heading 5": 12,
    "Heading 6": 11,
    "Heading 7": 11,
    "Heading 8": 11,
    "Heading 9": 11,
}


@dataclass(frozen=True)
class HtmlToDocContext:
    style_dict: dict[str, str]
    base_path: Path | None = None
    max_image_width: int | None = None
    max_depth: int = MAX_HTML_BLOCK_DEPTH
    style_r_fonts: object | None = None
    current_run: object | None = None
    superscript: bool = False


def _get_style_by_tag(tag_name: str, style_dict: dict[str, str], doc):
    key = tag_name
    if tag_name in HEADING_TAGS:
        key = f"heading{tag_name[1:]}"
    elif tag_name == "table":
        key = "table"
    elif tag_name not in style_dict:
        key = "paragraph"

    style_name = style_dict.get(key) or style_dict.get("default") or "Normal"
    try:
        return doc.styles[style_name]
    except KeyError:
        return doc.styles["Normal"]


def _apply_style_font_on_run(run, style_r_fonts) -> None:
    if style_r_fonts is None:
        return
    r_pr = run.element.get_or_add_rPr()
    r_fonts = r_pr.rFonts
    if r_fonts is None:
        r_fonts = OxmlElement("w:rFonts")
        r_pr.append(r_fonts)
    for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        val = style_r_fonts.get(qn(attr))
        if val:
            r_fonts.set(qn(attr), val)


def _apply_inline_style(run, tag_name: str) -> None:
    if tag_name in ("strong", "b"):
        run.bold = True
    elif tag_name in ("em", "i"):
        run.italic = True
    elif tag_name == "u":
        run.underline = True
    elif tag_name == "sup":
        run.font.superscript = True
    elif tag_name == "code":
        run.font.name = "Consolas"


def _add_text_run(paragraph, text: str, context: HtmlToDocContext):
    run = context.current_run
    if run is None:
        run = paragraph.add_run(text)
    else:
        run.add_text(text)
    _apply_style_font_on_run(run, context.style_r_fonts)
    if context.superscript:
        run.font.superscript = True
    return run


def _apply_r_fonts_to_r_pr(r_pr, style_r_fonts) -> None:
    if style_r_fonts is None:
        return
    r_fonts = OxmlElement("w:rFonts")
    for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        val = style_r_fonts.get(qn(attr))
        if val:
            r_fonts.set(qn(attr), val)
    r_pr.append(r_fonts)


def _append_child_to_paragraph(paragraph, child_element) -> None:
    getattr(paragraph, "_p").append(child_element)


def _add_hyperlink(paragraph, url: str, text: str, context: HtmlToDocContext) -> None:
    r_id = paragraph.part.relate_to(url, RELATIONSHIP_TYPE.HYPERLINK, is_external=True)
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), r_id)

    run = OxmlElement("w:r")
    r_pr = OxmlElement("w:rPr")
    _apply_r_fonts_to_r_pr(r_pr, context.style_r_fonts)

    color = OxmlElement("w:color")
    color.set(qn("w:val"), "0000FF")
    r_pr.append(color)

    underline = OxmlElement("w:u")
    underline.set(qn("w:val"), "single")
    r_pr.append(underline)

    if context.superscript:
        vert_align = OxmlElement("w:vertAlign")
        vert_align.set(qn("w:val"), "superscript")
        r_pr.append(vert_align)

    run.append(r_pr)
    text_element = OxmlElement("w:t")
    text_element.text = text
    run.append(text_element)
    hyperlink.append(run)
    _append_child_to_paragraph(paragraph, hyperlink)


def _is_relative_to(path: Path, base_path: Path) -> bool:
    try:
        path.relative_to(base_path)
        return True
    except ValueError:
        return False


def _resolve_local_image(src: str, base_path: Path | None) -> Path | None:
    if base_path is None or not src:
        return None

    parsed = urlparse(src)
    if parsed.scheme in REMOTE_IMAGE_SCHEMES or parsed.scheme == "data":
        return None

    raw_path = unquote(parsed.path or src)
    image_path = Path(raw_path)
    base_resolved = base_path.resolve()
    if image_path.is_absolute():
        candidate = image_path.resolve()
    else:
        candidate = (base_resolved / image_path).resolve()

    if not _is_relative_to(candidate, base_resolved):
        return None
    return candidate if candidate.exists() else None


def _fit_inline_shape_to_width(inline_shape, max_width) -> None:
    if max_width is None or inline_shape.width <= max_width:
        return
    if inline_shape.width == 0:
        inline_shape.width = max_width
        return
    scale = max_width / inline_shape.width
    inline_shape.width = max_width
    inline_shape.height = int(inline_shape.height * scale)


def _process_image(paragraph, src: str, context: HtmlToDocContext) -> None:
    if src.startswith("data:image/") and ";base64," in src:
        _, b64data = src.split(",", 1)
        image_bytes = base64.b64decode(b64data)
        run = paragraph.add_run()
        inline_shape = run.add_picture(io.BytesIO(image_bytes))
        _fit_inline_shape_to_width(inline_shape, context.max_image_width)
        return

    image_path = _resolve_local_image(src, context.base_path)
    if image_path is None:
        return

    run = paragraph.add_run()
    inline_shape = run.add_picture(str(image_path))
    _fit_inline_shape_to_width(inline_shape, context.max_image_width)


def _process_inline(paragraph, node, context: HtmlToDocContext) -> None:
    if isinstance(node, NavigableString):
        text = str(node)
        if not text:
            return
        if "\n" in text:
            if not text.strip():
                return
            text = HTML_FORMATTING_WHITESPACE_RE.sub(" ", text)
        _add_text_run(paragraph, text, context)
        return

    if node.name == "br":
        paragraph.add_run().add_break()
        return

    if node.name == "img":
        _process_image(paragraph, node.get("src") or "", context)
        return

    if node.name == "a":
        href = node.get("href")
        text = node.get_text(strip=False)
        if href and text:
            _add_hyperlink(paragraph, href, text, context)
        return

    if node.name == "sup":
        for child in node.contents:
            _process_inline(paragraph, child, replace(context, superscript=True))
        return

    if node.name in ("strong", "b", "em", "i", "u", "code"):
        run = context.current_run or paragraph.add_run()
        _apply_style_font_on_run(run, context.style_r_fonts)
        _apply_inline_style(run, node.name)
        for child in node.contents:
            _process_inline(paragraph, child, replace(context, current_run=run))
        return

    for child in node.contents:
        _process_inline(paragraph, child, context)


def _add_para_and_apply_style(doc, element, context: HtmlToDocContext) -> None:
    style = _get_style_by_tag(element.name, context.style_dict, doc)
    if element.name == "p" and any(getattr(child, "name", None) == "img" for child in element.contents):
        _add_paragraph_with_split_images(doc, element, context, style)
        return

    paragraph = doc.add_paragraph(style=style)
    style_r_fonts = style.element.get_or_add_rPr().find(qn("w:rFonts"))
    for child in element.contents:
        _process_inline(paragraph, child, replace(context, style_r_fonts=style_r_fonts))


def _add_paragraph_with_split_images(doc, element, context: HtmlToDocContext, style) -> None:
    style_r_fonts = style.element.get_or_add_rPr().find(qn("w:rFonts"))
    paragraph = None

    def _paragraph():
        nonlocal paragraph
        if paragraph is None:
            paragraph = doc.add_paragraph(style=style)
        return paragraph

    for child in element.contents:
        if getattr(child, "name", None) == "img":
            image_paragraph = doc.add_paragraph(style=style)
            _process_inline(image_paragraph, child, replace(context, style_r_fonts=style_r_fonts))
            paragraph = None
            continue

        if isinstance(child, NavigableString) and not str(child).strip() and paragraph is None:
            continue
        if getattr(child, "name", None) == "br" and paragraph is None:
            continue

        _process_inline(_paragraph(), child, replace(context, style_r_fonts=style_r_fonts))


def _add_text_paragraph(doc, text: str, context: HtmlToDocContext) -> None:
    if text.strip():
        paragraph_style = _get_style_by_tag("p", context.style_dict, doc)
        doc.add_paragraph(text.strip(), style=paragraph_style)


def _add_list_item(doc, li_element, context: HtmlToDocContext, *, ordered: bool) -> None:
    style_name = "List Number" if ordered else "List Bullet"
    try:
        style = doc.styles[style_name]
    except KeyError:
        style = _get_style_by_tag("p", context.style_dict, doc)

    paragraph = doc.add_paragraph(style=style)
    paragraph_style = _get_style_by_tag("p", context.style_dict, doc)
    style_r_fonts = paragraph_style.element.get_or_add_rPr().find(qn("w:rFonts"))
    child_context = replace(context, style_r_fonts=style_r_fonts)

    for child in li_element.contents:
        if getattr(child, "name", None) in ("ul", "ol"):
            _process_block_element(doc, child, context)
        else:
            _process_inline(paragraph, child, child_context)


def _add_html_table_to_doc(doc, element, context: HtmlToDocContext) -> None:
    rows = element.find_all("tr")
    if not rows:
        return

    max_cols = max(len(row.find_all(["th", "td"], recursive=False)) for row in rows)
    if max_cols == 0:
        return

    table = doc.add_table(rows=len(rows), cols=max_cols)
    table_style_name = context.style_dict.get("table")
    if table_style_name and table_style_name in doc.styles:
        table.style = doc.styles[table_style_name]

    paragraph_style = _get_style_by_tag("p", context.style_dict, doc)
    style_r_fonts = paragraph_style.element.get_or_add_rPr().find(qn("w:rFonts"))
    cell_context = replace(context, style_r_fonts=style_r_fonts)

    for row_index, row in enumerate(rows):
        cells = row.find_all(["th", "td"], recursive=False)
        for col_index, cell in enumerate(cells[:max_cols]):
            target_cell = table.cell(row_index, col_index)
            paragraph = target_cell.paragraphs[0]
            paragraph.style = paragraph_style
            for child in cell.contents:
                _process_inline(paragraph, child, cell_context)


def _process_pre_block(doc, element, context: HtmlToDocContext) -> None:
    paragraph_style = _get_style_by_tag("p", context.style_dict, doc)
    paragraph = doc.add_paragraph(style=paragraph_style)
    run = paragraph.add_run(element.get_text())
    run.font.name = "Consolas"


def _process_block_element(doc, element, context: HtmlToDocContext, depth: int = 0) -> None:
    if isinstance(element, NavigableString):
        _add_text_paragraph(doc, str(element), context)
        return
    if element.name is None:
        return
    if depth >= context.max_depth:
        _add_text_paragraph(doc, element.get_text(strip=True), context)
        return

    if element.name in HEADING_TAGS or element.name in ("p", "blockquote"):
        _add_para_and_apply_style(doc, element, context)
        return

    if element.name == "pre":
        _process_pre_block(doc, element, context)
        return

    if element.name == "table":
        _add_html_table_to_doc(doc, element, context)
        return

    if element.name in ("ul", "ol"):
        ordered = element.name == "ol"
        for li_element in element.find_all("li", recursive=False):
            _add_list_item(doc, li_element, context, ordered=ordered)
        return

    if element.name == "hr":
        doc.add_paragraph("")
        return

    if element.name in ("div", "section", "article", "body", "html"):
        for child in element.children:
            _process_block_element(doc, child, context, depth + 1)
        return

    _add_para_and_apply_style(doc, element, context)


def _get_available_page_width(doc) -> int:
    section = doc.sections[0]
    return section.page_width - section.left_margin - section.right_margin


def html_to_doc(doc, html: str, style_dict: dict[str, str], base_path: str | Path | None = None) -> None:
    """Convert HTML content into an existing python-docx document."""
    soup = BeautifulSoup(html, "html.parser")
    container = soup.find("div", class_="report-container")
    if container is None:
        container = soup.body or soup

    context = HtmlToDocContext(
        style_dict=style_dict,
        base_path=Path(base_path).resolve() if base_path is not None else None,
        max_image_width=_get_available_page_width(doc),
    )
    for element in container.children:
        _process_block_element(doc, element, context)


def set_global_styles(doc, font_name: str = "微软雅黑", font_size: int = 11, line_spacing: float = 1.15) -> None:
    """Set readable global fonts and compact paragraph spacing for a DOCX document."""
    for style in doc.styles:
        if getattr(style, "type", None) != 1:
            continue

        style.font.name = font_name
        style.font.size = Pt(HEADING_STYLE_SIZES.get(style.name, font_size))
        if style.name in HEADING_STYLE_SIZES:
            style.font.bold = True
        style.element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), font_name)

        paragraph_format = style.paragraph_format
        paragraph_format.line_spacing_rule = WD_LINE_SPACING.MULTIPLE
        paragraph_format.line_spacing = line_spacing
        paragraph_format.space_after = Pt(6)
