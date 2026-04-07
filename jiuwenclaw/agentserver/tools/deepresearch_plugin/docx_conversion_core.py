from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import logging
import re
import uuid

import pypandoc

from jiuwenclaw.agentserver.tools.deepresearch_plugin.conversion_utils import (
    MermaidRenderStats,
    ensure_pandoc,
    make_safe_filename_component,
    normalize_docx_fonts,
    normalize_headings,
    read_text_with_fallback,
)
from jiuwenclaw.agentserver.tools.deepresearch_plugin.mermaid_preprocess import (
    MermaidRenderOptions,
    extract_xychart_metadata,
    looks_like_mermaid_xychart,
    preprocess_mermaid_code,
)


MERMAID_BLOCK_RE = re.compile(r"```mermaid\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)

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
) -> tuple[str, MermaidRenderStats]:
    stats = MermaidRenderStats()

    def repl(match: re.Match[str]) -> str:
        stats.total += 1
        block_index = stats.total - 1
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
        stats.failed += 1
        return match.group(0)

    return MERMAID_BLOCK_RE.sub(repl, content), stats


def convert_md_to_docx(
    md_path: str | Path,
    docx_path: str | Path,
    *,
    render_mermaid_png,
    logger: logging.Logger,
) -> None:
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
