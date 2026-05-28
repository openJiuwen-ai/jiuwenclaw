# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from __future__ import annotations

import logging
import uuid
from pathlib import Path

import markdown
from docx import Document
from jiuwenclaw.agentserver.tools.deepresearch_plugin.conversion_utils import (
    make_safe_filename_component,
    normalize_docx_fonts,
    normalize_docx_tables,
    normalize_headings,
    postprocess_html,
    preprocess_markdown_text_for_docx,
    read_text_with_fallback,
)
from jiuwenclaw.agentserver.tools.deepresearch_plugin.word_utils import (
    html_to_doc,
    set_global_styles,
)

DOCX_HTML_TEMPLATE = """<!DOCTYPE html>
<head>
    <meta charset="UTF-8">
    <meta http-equiv="Content-Type" content="text/html; charset=UTF-8">
</head>
<body>
{content}
</body>
</html>
"""
DOCX_STYLE_MAP = {
    "heading1": "Heading 1",
    "heading2": "Heading 2",
    "heading3": "Heading 3",
    "heading4": "Heading 4",
    "heading5": "Heading 5",
    "heading6": "Heading 6",
    "heading7": "Heading 7",
    "heading8": "Heading 8",
    "heading9": "Heading 9",
    "paragraph": "Normal",
    "table": "Table Grid",
    "default": "Normal",
}


def convert_md_to_docx(
    md_path: str | Path,
    docx_path: str | Path,
    *,
    logger: logging.Logger,
) -> None:
    """Convert a Markdown file to a DOCX document without pandoc."""
    md_file = Path(md_path).resolve()
    docx_file = Path(docx_path).resolve()
    if not md_file.exists():
        raise FileNotFoundError(f"Markdown file does not exist: {md_file}")

    docx_file.parent.mkdir(parents=True, exist_ok=True)

    safe_stem = make_safe_filename_component(docx_file.stem)
    temp_prefix = f".tmp_{safe_stem}_{uuid.uuid4().hex}"
    temp_html = docx_file.parent / f"{temp_prefix}.html"

    try:
        content = read_text_with_fallback(md_file)
        content = preprocess_markdown_text_for_docx(content)
        content = normalize_headings(content)
        html_body = markdown.markdown(
            content,
            extensions=["extra", "toc", "md_in_html"],
            output_format="html5",
        )
        html_text = DOCX_HTML_TEMPLATE.format(content=postprocess_html(html_body))
        temp_html.write_text(html_text, encoding="utf-8", newline="\n")

        document = Document()
        set_global_styles(document)
        html_to_doc(document, html_text, DOCX_STYLE_MAP, base_path=docx_file.parent)
        document.save(docx_file)

        normalize_docx_fonts(docx_file)
        normalize_docx_tables(docx_file)
        logger.info("DOCX generated successfully: %s", docx_file)
    finally:
        temp_html.unlink(missing_ok=True)
