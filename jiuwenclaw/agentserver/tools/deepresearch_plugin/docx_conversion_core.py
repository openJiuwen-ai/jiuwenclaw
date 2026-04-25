# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import logging
import re
import uuid
from collections.abc import Callable
from pathlib import Path

import pypandoc
from jiuwenclaw.agentserver.tools.deepresearch_plugin.conversion_utils import (
    MermaidRenderStats,
    ensure_pandoc,
    make_safe_filename_component,
    normalize_docx_fonts,
    normalize_headings,
    preprocess_markdown_text_for_docx,
    read_text_with_fallback,
)
from jiuwenclaw.agentserver.tools.deepresearch_plugin.mermaid_preprocess import (
    MermaidRenderOptions,
    extract_xychart_metadata,
    looks_like_mermaid_xychart,
    preprocess_mermaid_code,
)

MERMAID_BLOCK_RE = re.compile(r"```mermaid\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
MERMAID_BLOCK_WITH_ADJACENT_CAPTION_RE = re.compile(
    r'(?is)```[ \t]*mermaid[ \t]*\r?\n(.*?)\r?\n```[ \t]*'
    r'(?:'
    r'(?:\r?\n[ \t]*)+'
    r'<div\b(?=[^>]*\b(?:style="text-align:\s*center;?"|class="figure-caption"))[^>]*>'
    r'.*?'
    r'</div>[ \t]*'
    r')?'
)

MAX_MERMAID_BLOCKS_FOR_DOCX = 30  # 最多渲染 30 个 Mermaid 块,防止资源耗尽

RenderMermaidPng = Callable[
    [str, str],
    bool,
]


def replace_mermaid_blocks(
    content: str,
    tmp_dir: Path,
    *,
    asset_prefix: str,
    cleanup_paths: list[Path],
    debug_dir: Path,
    debug_stem: str,
    render_mermaid_png,
    logger: logging.Logger,
    drop_mermaid_blocks: bool = False,
) -> tuple[str, MermaidRenderStats]:
    """Replace Mermaid code blocks in markdown content with rendered PNG images.

    Processes all Mermaid diagram blocks found in the markdown content, attempting
    to render each as a PNG image. Successfully rendered blocks are replaced with
    image references; failed renders retain the original code block. Also handles
    xy-chart metadata extraction for special chart types.

    Args:
        content: The markdown content containing Mermaid blocks to process.
        tmp_dir: Directory for storing temporary files and rendered images.
        asset_prefix: Prefix for generated image filenames (e.g., ".tmp_docname").
        cleanup_paths: List to append generated image paths for cleanup.
        debug_dir: Directory for debug output files during rendering.
        debug_stem: Base name for debug files (typically the output docx filename).
        render_mermaid_png: Callable that renders Mermaid code to PNG.
            Signature: (mermaid_code: str, output_path: str, **kwargs) -> bool.
        logger: Logger instance for warning and info messages.
        drop_mermaid_blocks: If True, remove all Mermaid blocks without rendering.
            Useful for quick conversion without diagram processing.

    Returns:
        A tuple containing:
        - str: Modified markdown content with rendered images or preserved blocks.
        - MermaidRenderStats: Statistics about rendering (total, success, failed).
    """
    stats = MermaidRenderStats()

    if drop_mermaid_blocks:
        matches = list(MERMAID_BLOCK_WITH_ADJACENT_CAPTION_RE.finditer(content))
        stats.total = len(matches)
        stats.failed = len(matches)
        return MERMAID_BLOCK_WITH_ADJACENT_CAPTION_RE.sub("\n", content), stats

    all_matches = list(MERMAID_BLOCK_WITH_ADJACENT_CAPTION_RE.finditer(content))
    if len(all_matches) > MAX_MERMAID_BLOCKS_FOR_DOCX:
        logger.warning(
            "[Security] Mermaid block count (%d) exceeds budget (%d). "
            "Excess blocks will be dropped to prevent resource exhaustion.",
            len(all_matches), MAX_MERMAID_BLOCKS_FOR_DOCX
        )
        # 保留前 MAX_MERMAID_BLOCKS_FOR_DOCX 个块,其余丢弃
        excess_start = MAX_MERMAID_BLOCKS_FOR_DOCX

    def repl(match: re.Match[str]) -> str:
        stats.total += 1
        if stats.total > MAX_MERMAID_BLOCKS_FOR_DOCX:
            stats.failed += 1
            return "\n"
        block_index = stats.total - 1
        try:
            raw_mermaid_code = match.group(1).strip()
            mermaid_code, supplement_markdown = preprocess_mermaid_code(
                raw_mermaid_code,
                MermaidRenderOptions(),
            )
            xychart_metadata = None
            if looks_like_mermaid_xychart(mermaid_code.splitlines()):
                xychart_metadata = extract_xychart_metadata(mermaid_code, warn_on_invalid=False)

            img_name = f"{asset_prefix}_mermaid_{block_index}.png"
            img_path = tmp_dir / img_name
            debug_base_path = debug_dir / f"{debug_stem}_mermaid_{block_index}"

            if render_mermaid_png(
                mermaid_code,
                str(img_path),
                debug_base_path=debug_base_path,
                xychart_metadata=xychart_metadata,
            ):
                cleanup_paths.append(img_path)
                stats.success += 1
                supplement = f"\n\n{supplement_markdown}\n" if supplement_markdown.strip() else ""
                return f"\n\n![diagram](<{img_name}>)\n{supplement}\n"

            logger.warning("Mermaid render failed; keeping the original code block.")
        except Exception as exc:
            logger.warning(
                "Mermaid block processing failed in DOCX conversion; "
                "keeping the original code block. block=%s error=%s",
                block_index,
                exc,
            )
        stats.failed += 1
        return match.group(0)

    return MERMAID_BLOCK_RE.sub(repl, content), stats


def convert_md_to_docx(
    md_path: str | Path,
    docx_path: str | Path,
    *,
    render_mermaid_png,
    logger: logging.Logger,
    drop_mermaid_blocks: bool = False,
) -> None:
    """Convert a Markdown file to a DOCX document with Mermaid diagram support.

    Performs a full conversion pipeline: normalizes headings, processes Mermaid
    blocks into PNG images, then uses pandoc to convert to DOCX format. Includes
    post-processing for font normalization. Temporary files are cleaned up after
    conversion regardless of success or failure.

    Args:
        md_path: Path to the source Markdown file (must exist).
        docx_path: Path for the output DOCX file (parent directory created if needed).
        render_mermaid_png: Callable that renders Mermaid code to PNG.
            Signature: (mermaid_code: str, output_path: str, **kwargs) -> bool.
        logger: Logger instance for progress and error messages.
        drop_mermaid_blocks: If True, skip Mermaid rendering and remove diagram blocks.
            Useful for quick conversion without diagram processing overhead.

    Returns:
        None. The DOCX file is written to docx_path.

    Raises:
        FileNotFoundError: If the source Markdown file does not exist.
        RuntimeError: If pandoc is not available (via ensure_pandoc check).
    """
    ensure_pandoc()

    md_file = Path(md_path).resolve()
    docx_file = Path(docx_path).resolve()
    if not md_file.exists():
        raise FileNotFoundError(f"Markdown file does not exist: {md_file}")

    docx_file.parent.mkdir(parents=True, exist_ok=True)

    safe_stem = make_safe_filename_component(docx_file.stem)
    temp_prefix = f".tmp_{safe_stem}_{uuid.uuid4().hex}"
    tmp_dir = docx_file.parent
    temp_md = tmp_dir / f"{temp_prefix}.md"
    cleanup_paths: list[Path] = [temp_md]

    try:
        content = read_text_with_fallback(md_file)
        content = preprocess_markdown_text_for_docx(content)
        content = normalize_headings(content)
        content, mermaid_stats = replace_mermaid_blocks(
            content,
            tmp_dir,
            asset_prefix=temp_prefix,
            cleanup_paths=cleanup_paths,
            debug_dir=docx_file.parent,
            debug_stem=docx_file.stem,
            render_mermaid_png=render_mermaid_png,
            logger=logger,
            drop_mermaid_blocks=drop_mermaid_blocks,
        )
        temp_md.write_text(content, encoding="utf-8")

        pypandoc.convert_file(
            str(temp_md),
            "docx",
            outputfile=str(docx_file),
            extra_args=[
                "--from=gfm",
                "--resource-path",
                str(tmp_dir),
            ],
        )
        normalize_docx_fonts(docx_file)
        logger.info("DOCX generated successfully: %s", docx_file)
        logger.info(
            "Mermaid render stats: total=%s success=%s failed=%s",
            mermaid_stats.total,
            mermaid_stats.success,
            mermaid_stats.failed,
        )
    finally:
        for path in cleanup_paths:
            path.unlink(missing_ok=True)
