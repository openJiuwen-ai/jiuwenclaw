from __future__ import annotations

import logging
import uuid
from pathlib import Path

from jiuwenclaw.agentserver.tools.deepresearch_plugin.conversion_utils import enhance_image
from jiuwenclaw.agentserver.tools.deepresearch_plugin.docx_conversion_core import (
    convert_md_to_docx as convert_md_to_docx_core,
    replace_mermaid_blocks,
)
from jiuwenclaw.agentserver.tools.deepresearch_plugin.mermaid_common import load_svg_markup
from jiuwenclaw.agentserver.tools.deepresearch_plugin.mermaid_online import render_mermaid_online
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
    output_file = Path(output_path)
    success = render_mermaid_online(
        code,
        output_file,
        output_format="png",
        debug_base_path=debug_base_path,
    )
    if not success:
        return False

    enhance_image(str(output_file))

    if not xychart_metadata or not xychart_metadata.series:
        return True

    svg_path = output_file.parent / f".tmp_{output_file.stem}_{uuid.uuid4().hex}.svg"
    try:
        if not render_mermaid_online(
            code,
            svg_path,
            output_format="svg",
            debug_base_path=debug_base_path,
        ):
            return True

        svg_markup = load_svg_markup(svg_path)
        overlay_xychart_value_labels_on_png(
            str(output_file),
            svg_markup,
            xychart_metadata,
        )
    finally:
        svg_path.unlink(missing_ok=True)

    return True


def convert_md_to_docx(md_path: str | Path, docx_path: str | Path) -> None:
    convert_md_to_docx_core(
        md_path,
        docx_path,
        render_mermaid_png=render_mermaid_png,
        logger=logger,
    )


if __name__ == "__main__":
    try:
        convert_md_to_docx("input.md", "output.docx")
    except Exception:
        logger.exception("DOCX conversion failed")
        raise
