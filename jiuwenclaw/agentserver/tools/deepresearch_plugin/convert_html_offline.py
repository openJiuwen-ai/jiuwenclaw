# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from __future__ import annotations

import argparse
import html
import logging
import re
import time
import uuid
import warnings
from dataclasses import dataclass
from pathlib import Path

import markdown
from jiuwenclaw.agentserver.tools.deepresearch_plugin.conversion_utils import (
    postprocess_html,
    preprocess_markdown_text,
    read_text_with_fallback,
    render_mermaid_supplement,
)
from jiuwenclaw.agentserver.tools.deepresearch_plugin.mermaid_offline import (
    ensure_mermaid_cli,
    load_svg_markup,
    render_mermaid_offline,
)
from jiuwenclaw.agentserver.tools.deepresearch_plugin.mermaid_preprocess import (
    MermaidRenderOptions,
    extract_xychart_metadata,
    looks_like_mermaid_xychart,
    normalize_whitespace_and_units,
    preprocess_mermaid_code,
)
from jiuwenclaw.agentserver.tools.deepresearch_plugin.xychart_value_labels import annotate_xychart_svg

logger = logging.getLogger(__name__)

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta http-equiv="Content-Type" content="text/html; charset=UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
        :root {{
            --text: #222;
            --muted: #666;
            --border: #e5e7eb;
            --bg-soft: #f6f8fa;
            --link: #2563eb;
        }}

        * {{
            box-sizing: border-box;
        }}

        html {{
            -webkit-text-size-adjust: 100%;
            text-rendering: optimizeLegibility;
        }}

        body {{
            max-width: 1200px;
            margin: 0 auto;
            padding: 32px 24px 64px;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
                         "Helvetica Neue", Arial, "PingFang SC", "Hiragino Sans GB",
                         "Microsoft YaHei", "Noto Sans CJK SC", "Noto Sans SC", sans-serif;
            line-height: 1.8;
            color: var(--text);
            background: #fff;
            word-break: break-word;
            overflow-wrap: anywhere;
        }}

        h1, h2, h3, h4, h5, h6 {{
            line-height: 1.35;
            margin-top: 1.6em;
            margin-bottom: 0.7em;
        }}

        h1 {{
            padding-bottom: 0.3em;
            border-bottom: 1px solid var(--border);
        }}

        p {{
            margin: 0.9em 0;
        }}

        a {{
            color: var(--link);
            text-decoration: none;
        }}

        a:hover {{
            text-decoration: underline;
        }}

        img {{
            max-width: 100%;
            height: auto;
            display: block;
            margin: 20px auto 12px;
        }}

        .figure-caption {{
            text-align: center;
            color: var(--muted);
            font-size: 0.95rem;
            margin: 0.2rem auto 1.4rem;
        }}

        .figure-caption p {{
            margin: 0.2rem 0;
        }}

        .citation {{
            vertical-align: super;
            font-size: 0.78em;
            line-height: 0;
            white-space: nowrap;
        }}

        .citation a {{
            color: var(--muted);
            text-decoration: none;
        }}

        .citation a:hover {{
            color: var(--link);
            text-decoration: underline;
        }}

        .citation + .citation {{
            margin-left: 0.18em;
        }}

        pre {{
            background: var(--bg-soft);
            padding: 16px;
            border-radius: 10px;
            overflow-x: auto;
            border: 1px solid var(--border);
        }}

        code {{
            font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
        }}

        p code, li code, td code, th code {{
            background: #f3f4f6;
            padding: 0.12em 0.35em;
            border-radius: 6px;
        }}

        table {{
            border-collapse: collapse;
            width: 100%;
            margin: 16px 0 24px;
            display: block;
            overflow-x: auto;
            white-space: nowrap;
        }}

        th, td {{
            border: 1px solid var(--border);
            padding: 10px 12px;
            text-align: left;
            vertical-align: top;
        }}

        th {{
            background: #f8fafc;
        }}

        ul, ol {{
            padding-left: 1.5em;
        }}

        blockquote {{
            margin: 1em 0;
            padding: 0.2em 1em;
            color: var(--muted);
            border-left: 4px solid var(--border);
            background: #fafafa;
        }}

        hr {{
            border: 0;
            border-top: 1px solid var(--border);
            margin: 2em 0;
        }}

        .mermaid-wrap {{
            width: 100%;
            overflow-x: auto;
            overflow-y: hidden;
            margin: 24px 0 12px;
            padding-bottom: 8px;
        }}

        .mermaid-rendered {{
            min-width: max-content;
            text-align: center;
        }}

        .mermaid-rendered svg {{
            height: auto;
            display: block;
            margin: 0 auto;
            max-width: 100%;
        }}

        .timeline-notes {{
            margin: 10px 0 24px;
            padding: 12px 16px;
            border: 1px solid var(--border);
            border-radius: 10px;
            background: #fafafa;
            font-size: 0.96rem;
        }}

        .timeline-notes p {{
            margin: 0 0 8px;
            font-weight: 600;
        }}

        .timeline-notes ul {{
            margin: 0;
            padding-left: 1.4em;
        }}

        .timeline-notes li {{
            margin: 0.45em 0;
        }}
    </style>
</head>
<body>
{content}
</body>
</html>
"""

MERMAID_BLOCK_RE = re.compile(
    r"(?ms)^```[ \t]*mermaid[ \t]*\r?\n(.*?)\r?\n```[ \t]*$"
)
MERMAID_BLOCK_WITH_ADJACENT_CAPTION_RE = re.compile(
    r'(?ms)^```[ \t]*mermaid[ \t]*\r?\n(.*?)\r?\n```[ \t]*'
    r'(?:'
    r'(?:\r?\n[ \t]*)+'
    r'<div\b(?=[^>]*\b(?:style="text-align:\s*center;?"|class="figure-caption"))[^>]*>'
    r'.*?'
    r'</div>[ \t]*'
    r')?'
)


@dataclass(slots=True)
class ConvertOptions:
    mermaid_security_level: str = "strict"
    timeline_max_label_len: int = 18
    scale_xychart: bool = True
    warn_on_invalid_number: bool = True
    show_xychart_value_labels: bool = True
    title: str = "Document"
    # Mermaid渲染预算
    max_mermaid_blocks: int = 50  # 最大Mermaid块数量
    max_single_mermaid_bytes: int = 100 * 1024  # 单个Mermaid块最大字节数
    max_mermaid_total_bytes: int = 5 * 1024 * 1024  # 所有Mermaid块总字节数上限
    max_render_time_seconds: int = 300  # 总渲染时间上限（秒）


def replace_mermaid_blocks(
    text: str,
    options: ConvertOptions,
    *,
    asset_dir: Path,
    asset_prefix: str,
    cleanup_paths: list[Path],
    debug_dir: Path,
    debug_stem: str,
) -> str:
    """Replace Mermaid code blocks in markdown text with pre-rendered SVG images.

    This function processes all mermaid code blocks (```mermaid) in the input text,
    pre-processes each diagram code, renders it offline using mermaid-cli to SVG,
    and replaces the original block with the rendered SVG markup wrapped in HTML divs.
    If mermaid-cli is unavailable or rendering fails, falls back to displaying the
    source code in a pre/code block.

    Args:
        text: The markdown text containing mermaid blocks to replace.
        options: Configuration options for mermaid rendering including security level,
            timeline label length, xychart scaling, and value label display.
        asset_dir: Directory path where rendered SVG files will be saved.
        asset_prefix: Prefix for generated asset filenames (e.g., ".tmp_output_abc123").
        cleanup_paths: List to collect paths of temporary files for cleanup after
            conversion completes.
        debug_dir: Directory for debug output files during mermaid rendering.
        debug_stem: Base filename stem for debug files.

    Returns:
        The markdown text with mermaid blocks replaced by rendered SVG HTML markup
        or fallback code blocks.
    """
    block_counter = 0
    total_mermaid_bytes = 0
    render_start_time = time.time()
    cli_status = ensure_mermaid_cli()

    if not cli_status.available:
        logger.warning(
            "Mermaid CLI is unavailable in offline HTML conversion; Mermaid source blocks will be removed. %s",
            cli_status.message,
        )
        return MERMAID_BLOCK_WITH_ADJACENT_CAPTION_RE.sub("\n", text)

    def _build_fallback_block(code: str, supplement_markdown: str = "") -> str:
        supplement_html = ""
        if supplement_markdown.strip():
            try:
                supplement_html = render_mermaid_supplement(supplement_markdown)
            except Exception as exc:
                logger.warning(
                    "Mermaid supplement rendering failed in offline HTML conversion; "
                    "keeping only the source block. error=%s",
                    exc,
                )
        escaped = html.escape(code)
        return f'\n<pre><code class="language-mermaid">{escaped}</code></pre>{supplement_html}\n'

    def _repl(match: re.Match[str]) -> str:
        nonlocal block_counter, total_mermaid_bytes
        raw_mermaid_code = match.group(1).strip()
        
        # 检查Mermaid块数量上限
        if block_counter >= options.max_mermaid_blocks:
            logger.warning(
                "Mermaid block count reached limit (%d), skipping remaining blocks.",
                options.max_mermaid_blocks,
            )
            return _build_fallback_block(raw_mermaid_code, "")

        # 检查单个Mermaid块大小上限
        if len(raw_mermaid_code) > options.max_single_mermaid_bytes:
            logger.warning(
                "Single Mermaid block exceeds size limit (%d bytes), skipping block %d.",
                options.max_single_mermaid_bytes,
                block_counter,
            )
            return _build_fallback_block(raw_mermaid_code[:1000], "")

        # 检查总Mermaid字节上限
        if total_mermaid_bytes + len(raw_mermaid_code) > options.max_mermaid_total_bytes:
            logger.warning(
                "Total Mermaid bytes reached limit (%d), skipping remaining blocks.",
                options.max_mermaid_total_bytes,
            )
            return _build_fallback_block(raw_mermaid_code, "")

        # 检查渲染时间上限
        elapsed = time.time() - render_start_time
        if elapsed > options.max_render_time_seconds:
            logger.warning(
                "Mermaid render time reached limit (%d seconds), skipping remaining blocks.",
                options.max_render_time_seconds,
            )
            return _build_fallback_block(raw_mermaid_code, "")

        block_id = block_counter
        block_counter += 1
        total_mermaid_bytes += len(raw_mermaid_code)
        debug_base_path = debug_dir / f"{debug_stem}_mermaid_{block_id}"
        fallback_code = raw_mermaid_code
        supplement_markdown = ""

        try:
            mermaid_code, supplement_markdown = preprocess_mermaid_code(
                raw_mermaid_code,
                MermaidRenderOptions(
                    timeline_max_label_len=options.timeline_max_label_len,
                    scale_xychart=options.scale_xychart,
                    warn_on_invalid_number=options.warn_on_invalid_number,
                    show_xychart_value_labels=options.show_xychart_value_labels,
                ),
            )
            fallback_code = mermaid_code

            xychart_metadata = None
            if options.show_xychart_value_labels and looks_like_mermaid_xychart(mermaid_code.splitlines()):
                xychart_metadata = extract_xychart_metadata(mermaid_code, warn_on_invalid=False)

            svg_path = asset_dir / f"{asset_prefix}_mermaid_{block_id}.svg"
            if render_mermaid_offline(
                mermaid_code,
                svg_path,
                output_format="svg",
                debug_base_path=debug_base_path,
            ):
                cleanup_paths.append(svg_path)
                svg_markup = load_svg_markup(svg_path)
                if xychart_metadata and xychart_metadata.series:
                    svg_markup = annotate_xychart_svg(svg_markup, xychart_metadata)
                supplement_html = render_mermaid_supplement(supplement_markdown)
                return (
                    '\n<div class="mermaid-wrap"><div class="mermaid-rendered">'
                    f"{svg_markup}</div></div>{supplement_html}\n"
                )

            logger.warning("Offline Mermaid rendering failed; keeping the source block in HTML output.")
        except Exception as exc:
            logger.warning(
                "Mermaid block processing failed in offline HTML conversion; "
                "keeping the source block. block=%s error=%s",
                block_id,
                exc,
            )

        return _build_fallback_block(fallback_code, supplement_markdown)

    return MERMAID_BLOCK_RE.sub(_repl, text)


def preprocess_markdown(
    text: str,
    options: ConvertOptions,
    *,
    asset_dir: Path,
    asset_prefix: str,
    cleanup_paths: list[Path],
    debug_dir: Path,
    debug_stem: str,
) -> str:
    """Preprocess markdown text before HTML conversion.

    Applies a series of preprocessing transformations to prepare markdown content
    for conversion to HTML: normalizes whitespace and units, applies general text
    preprocessing, and replaces mermaid diagram blocks with pre-rendered SVG images.

    Args:
        text: The raw markdown text to preprocess.
        options: Configuration options for mermaid rendering and preprocessing.
        asset_dir: Directory path where rendered SVG files will be saved.
        asset_prefix: Prefix for generated asset filenames.
        cleanup_paths: List to collect paths of temporary files for cleanup.
        debug_dir: Directory for debug output files during mermaid rendering.
        debug_stem: Base filename stem for debug files.

    Returns:
        The preprocessed markdown text ready for markdown-to-HTML conversion.
    """
    text = normalize_whitespace_and_units(text)
    text = preprocess_markdown_text(text)
    return replace_mermaid_blocks(
        text,
        options,
        asset_dir=asset_dir,
        asset_prefix=asset_prefix,
        cleanup_paths=cleanup_paths,
        debug_dir=debug_dir,
        debug_stem=debug_stem,
    )


def _sanitize_html_content(html_content: str) -> str:
    """净化HTML内容，移除危险元素和属性.

    Args:
        html_content: 原始HTML内容

    Returns:
        安全的HTML内容
    """
    # 移除 script 标签及其内容
    html_content = re.sub(
        r'<script[^>]*>.*?</script>',
        '',
        html_content,
        flags=re.IGNORECASE | re.DOTALL
    )

    # 移除危险标签: iframe, object, embed, applet, form
    dangerous_tags = ['iframe', 'object', 'embed', 'applet', 'form', 'meta', 'link', 'base']
    for tag in dangerous_tags:
        html_content = re.sub(
            rf'<{tag}[^>]*>.*?</{tag}>',
            '',
            html_content,
            flags=re.IGNORECASE | re.DOTALL
        )
        html_content = re.sub(
            rf'<{tag}[^>]*>',
            '',
            html_content,
            flags=re.IGNORECASE
        )

    html_content = re.sub(
        r'\s+on[a-z]+\s*=\s*["\'][^"\']*["\']',
        '',
        html_content,
        flags=re.IGNORECASE
    )
    html_content = re.sub(
        r'\s+on[a-z]+\s*=\s*[^\s>]+',
        '',
        html_content,
        flags=re.IGNORECASE
    )

    dangerous_schemes = ['javascript:', 'vbscript:', 'data:text/html']
    url_attrs_pattern = 'href|src|action|formaction|data|poster'
    for scheme in dangerous_schemes:
        html_content = re.sub(
            rf'({url_attrs_pattern})\s*=\s*["\']\s*{scheme}[^"\']*["\']',
            '',
            html_content,
            flags=re.IGNORECASE
        )
        # 不带引号的 URL
        html_content = re.sub(
            rf'({url_attrs_pattern})\s*=\s*{scheme}[^\s>]*',
            '',
            html_content,
            flags=re.IGNORECASE
        )

    return html_content


def convert_md_to_html(
    input_md: str | Path,
    output_html: str | Path,
    *,
    options: ConvertOptions | None = None,
) -> None:
    """Convert a Markdown file to styled HTML with offline Mermaid rendering.

    Reads the input markdown file, preprocesses the content (including offline
    rendering of mermaid diagrams to SVG), converts to HTML using the markdown
    library with extensions, applies postprocessing, and writes the result to
    an HTML file wrapped in a styled template. Temporary SVG files are cleaned
    up after conversion.

    Args:
        input_md: Path to the input Markdown file (.md).
        output_html: Path to the output HTML file. Parent directories will be
            created if they don't exist.
        options: Optional configuration for conversion including title, mermaid
            security level, timeline label length, xychart options. Defaults to
            ConvertOptions() if not provided.

    Raises:
        FileNotFoundError: If the input markdown file does not exist.
    """
    options = options or ConvertOptions()
    input_path = Path(input_md)
    output_path = Path(output_html)

    if not input_path.exists():
        raise FileNotFoundError(f"Markdown file does not exist: {input_path}")

    if input_path.suffix.lower() != ".md":
        warnings.warn(f"Input file does not look like Markdown: {input_path.name}", stacklevel=2)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    temp_prefix = f".tmp_{output_path.stem}_{uuid.uuid4().hex}"
    cleanup_paths: list[Path] = []
    try:
        md_content = read_text_with_fallback(input_path)
        md_content = preprocess_markdown(
            md_content,
            options,
            asset_dir=output_path.parent,
            asset_prefix=temp_prefix,
            cleanup_paths=cleanup_paths,
            debug_dir=output_path.parent,
            debug_stem=output_path.stem,
        )
        html_body = markdown.markdown(
            md_content,
            extensions=["extra", "toc", "md_in_html"],
            output_format="html",
        )
        # 净化HTML内容，移除危险元素和属性
        html_body = _sanitize_html_content(html_body)
        full_html = HTML_TEMPLATE.format(
            title=html.escape(options.title, quote=True),
            content=postprocess_html(html_body),
        )
        output_path.write_text(full_html, encoding="utf-8", newline="\n")
    finally:
        for path in cleanup_paths:
            path.unlink(missing_ok=True)

    logger.info("HTML generated successfully: %s", output_path.resolve())
    logger.info("Mermaid diagrams were pre-rendered offline with mermaid-cli when available.")


def build_arg_parser() -> argparse.ArgumentParser:
    """Build and configure the command-line argument parser.

    Creates an ArgumentParser with arguments for input/output file paths,
    HTML document title, and various mermaid rendering configuration options
    including security level, timeline label length, xychart scaling, and
    value label display.

    Returns:
        A configured argparse.ArgumentParser instance ready for parsing
        command-line arguments.
    """
    parser = argparse.ArgumentParser(
        description="Convert Markdown to styled HTML using offline Mermaid rendering."
    )
    parser.add_argument("input", nargs="?", default="input.md", help="Input Markdown file")
    parser.add_argument("output", nargs="?", default="output.html", help="Output HTML file")
    parser.add_argument("--title", default="Document", help="HTML document title")
    parser.add_argument(
        "--mermaid-security-level",
        default="strict",
        choices=["strict", "loose", "antiscript", "sandbox"],
        help="Retained for compatibility with the online converter options.",
    )
    parser.add_argument(
        "--timeline-max-label-len",
        type=int,
        default=18,
        help="Maximum display width for Mermaid timeline labels.",
    )
    parser.add_argument(
        "--no-scale-xychart",
        action="store_true",
        help="Disable Mermaid xychart value scaling.",
    )
    parser.add_argument(
        "--quiet-invalid-number-warning",
        action="store_true",
        help="Disable warnings for invalid xychart number items.",
    )
    parser.add_argument(
        "--no-xychart-value-labels",
        action="store_true",
        help="Disable SVG value label annotation for Mermaid xychart.",
    )
    return parser


def main() -> None:
    """Main entry point for command-line Markdown to HTML conversion.

    Parses command-line arguments, constructs ConvertOptions from the parsed
    arguments, and invokes convert_md_to_html to perform the conversion.
    This function is called when the script is executed directly.
    """
    parser = build_arg_parser()
    args = parser.parse_args()

    options = ConvertOptions(
        mermaid_security_level=args.mermaid_security_level,
        timeline_max_label_len=args.timeline_max_label_len,
        scale_xychart=not args.no_scale_xychart,
        warn_on_invalid_number=not args.quiet_invalid_number_warning,
        show_xychart_value_labels=not args.no_xychart_value_labels,
        title=args.title,
    )
    convert_md_to_html(args.input, args.output, options=options)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logger.exception("HTML conversion failed")
        raise
