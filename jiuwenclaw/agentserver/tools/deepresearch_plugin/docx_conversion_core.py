# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from __future__ import annotations

import logging
import uuid
from pathlib import Path

import markdown
import pypandoc
from jiuwenclaw.agentserver.tools.deepresearch_plugin.conversion_utils import (
    ensure_pandoc,
    make_safe_filename_component,
    normalize_docx_fonts,
    normalize_headings,
    preprocess_markdown_text_for_docx,
    read_text_with_fallback,
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


def convert_md_to_docx(
    md_path: str | Path,
    docx_path: str | Path,
    *,
    logger: logging.Logger,
) -> None:
    """Convert a Markdown file to a DOCX document."""
    ensure_pandoc()

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
        temp_html.write_text(
            DOCX_HTML_TEMPLATE.format(content=html_body),
            encoding="utf-8",
            newline="\n",
        )

        pypandoc.convert_file(
            str(temp_html),
            "docx",
            outputfile=str(docx_file),
            extra_args=[
                "--from=html",
                "--resource-path",
                str(docx_file.parent),
            ],
        )
        normalize_docx_fonts(docx_file)
        logger.info("DOCX generated successfully: %s", docx_file)
    finally:
        temp_html.unlink(missing_ok=True)
