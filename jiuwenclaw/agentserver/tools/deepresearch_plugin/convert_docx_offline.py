# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from __future__ import annotations

import logging
import uuid
from pathlib import Path

from jiuwenclaw.agentserver.tools.deepresearch_plugin.conversion_utils import enhance_image
from jiuwenclaw.agentserver.tools.deepresearch_plugin.docx_conversion_core import (
    convert_md_to_docx as convert_md_to_docx_core,
)
from jiuwenclaw.agentserver.tools.deepresearch_plugin.mermaid_common import load_svg_markup
from jiuwenclaw.agentserver.tools.deepresearch_plugin.mermaid_offline import (
    ensure_mermaid_cli,
    render_mermaid_offline,
)
from jiuwenclaw.agentserver.tools.deepresearch_plugin.xychart_value_labels import (
    overlay_xychart_value_labels_on_png,
)

logger = logging.getLogger(__name__)


def render_mermaid_png(
    code: str,
    output_path: str,
    *,
    debug_base_path: Path | None = None,
    xychart_metadata=None,
) -> bool:
    """Render Mermaid diagram code to a PNG file using offline CLI.

    This function renders Mermaid code to PNG format using the offline Mermaid CLI,
    applies image enhancement, and optionally overlays value labels for xychart
    diagrams. Errors are handled gracefully with logging, and the function returns
    a success/failure status rather than raising exceptions.

    Args:
        code: Mermaid diagram source code to render.
        output_path: Path where the rendered PNG file will be saved.
        debug_base_path: Optional directory path for debug artifacts. If provided,
            intermediate files may be saved here for debugging purposes.
        xychart_metadata: Optional metadata for xychart diagrams. If provided with
            a 'series' attribute, SVG rendering is performed to extract label
            positions, and value labels are overlaid on the final PNG.

    Returns:
        True if PNG rendering succeeded (with or without label overlay), False if
        the initial PNG rendering failed. Label overlay failures are logged but
        do not affect the return value.
    """
    output_file = Path(output_path)
    try:
        success = render_mermaid_offline(
            code,
            output_file,
            output_format="png",
            debug_base_path=debug_base_path,
        )
    except Exception as exc:
        logger.warning(
            "Offline Mermaid PNG rendering raised; keeping the original Mermaid block. error=%s",
            exc,
        )
        return False
    if not success:
        return False

    try:
        enhance_image(str(output_file))
    except Exception as exc:
        logger.warning(
            "Offline Mermaid PNG post-processing failed; using the raw rendered image. error=%s",
            exc,
        )

    if not xychart_metadata or not xychart_metadata.series:
        return True

    svg_path = output_file.parent / f".tmp_{output_file.stem}_{uuid.uuid4().hex}.svg"
    try:
        try:
            rendered_svg = render_mermaid_offline(
                code,
                svg_path,
                output_format="svg",
                debug_base_path=debug_base_path,
            )
        except Exception as exc:
            logger.warning(
                "Offline Mermaid SVG overlay rendering failed; using the PNG without labels. error=%s",
                exc,
            )
            return True
        if not rendered_svg:
            return True

        try:
            svg_markup = load_svg_markup(svg_path)
            overlay_xychart_value_labels_on_png(
                str(output_file),
                svg_markup,
                xychart_metadata,
            )
        except Exception as exc:
            logger.warning(
                "Offline Mermaid PNG label overlay failed; using the PNG without labels. error=%s",
                exc,
            )
    finally:
        svg_path.unlink(missing_ok=True)

    return True


def convert_md_to_docx(md_path: str | Path, docx_path: str | Path) -> None:
    """Convert a Markdown file to a DOCX file using offline Mermaid rendering.

    This function checks for Mermaid CLI availability before conversion. If the
    CLI is unavailable, Mermaid source blocks will be removed from the output
    rather than rendered. The actual conversion is delegated to the core
    conversion function with appropriate parameters.

    Args:
        md_path: Path to the input Markdown file. Can be a string or Path object.
        docx_path: Path where the output DOCX file will be saved. Can be a string
            or Path object.

    Returns:
        None. The function performs the conversion in-place and logs warnings
        if Mermaid CLI is unavailable.
    """
    cli_status = ensure_mermaid_cli()
    if not cli_status.available:
        logger.warning(
            "Mermaid CLI is unavailable in offline DOCX conversion; Mermaid source blocks will be removed. %s",
            cli_status.message,
        )
    convert_md_to_docx_core(
        md_path,
        docx_path,
        render_mermaid_png=render_mermaid_png,
        logger=logger,
        drop_mermaid_blocks=not cli_status.available,
    )


if __name__ == "__main__":
    try:
        convert_md_to_docx("input.md", "output.docx")
    except Exception:
        logger.exception("DOCX conversion failed")
        raise
