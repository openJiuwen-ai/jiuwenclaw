from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import argparse
import html
import logging
import re
import unicodedata
import warnings

import markdown


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

        .mermaid {{
            min-width: max-content;
            text-align: center;
        }}

        .mermaid svg {{
            height: auto;
            display: block;
            margin: 0 auto;
            max-width: none !important;
        }}

        .timeline-notes {{
            margin: 10px 0 24px;
            padding: 12px 16px;
            border: 1px solid var(--border);
            border-radius: 10px;
            background: #fafafa;
            font-size: 0.96rem;
        }}

        .timeline-notes-title {{
            margin: 0 0 8px;
            font-weight: 600;
            color: var(--text);
        }}

        .timeline-notes ul {{
            margin: 0;
            padding-left: 1.4em;
        }}

        .timeline-notes li {{
            margin: 0.45em 0;
        }}

        .timeline-notes .date {{
            font-weight: 600;
        }}
    </style>
</head>
<body>
{content}

<script type="module">
    import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs";

    mermaid.initialize({{
        startOnLoad: true,
        theme: "default",
        securityLevel: "{mermaid_security_level}",
        fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans CJK SC", "Noto Sans SC", sans-serif',
        flowchart: {{
            htmlLabels: true
        }},
        themeCSS: `
            .mermaid text {{
                font-size: 14px !important;
            }}
        `
    }});
</script>
</body>
</html>
"""


LATIN_UNITS = [
    "TB/s", "GB/s", "MB/s", "KB/s",
    "TB", "GB", "MB", "KB",
    "PFLOPS", "TFLOPS", "GFLOPS",
    "FLOPS", "FLOP",
    "FP16", "FP8", "FP4", "NVFP4",
    "W", "kW", "MW", "GW", "V", "A",
    "Hz", "kHz", "MHz", "GHz",
    "nm", "μm", "mm",
    "GPU", "CPU", "DPU", "LPU",
    "Token", "token",
]

CHINESE_UNITS = [
    "万亿美元", "亿美元", "美元", "亿元",
    "太瓦时", "瓦时",
    "吉瓦", "兆瓦", "千瓦",
    "万人", "万台", "倍", "%",
]


def _compile_unit_pattern(units: list[str]) -> str:
    return "|".join(sorted(map(re.escape, units), key=len, reverse=True))


LATIN_UNITS_PATTERN = _compile_unit_pattern(LATIN_UNITS)
CHINESE_UNITS_PATTERN = _compile_unit_pattern(CHINESE_UNITS)

NUMBER_PATTERN = r"[-+]?(?:\d+(?:,\d{3})*|\d+|\.\d+)(?:\.\d+)?(?:[eE][-+]?\d+)?"
XYCHART_NUMBER_RE = re.compile(
    r"""
    [-+]?
    (?:
        (?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?
        |
        \.\d+
    )
    (?:[eE][-+]?\d+)?
    """,
    re.VERBOSE,
)

CITATION_RE = re.compile(
    r"\[\[(\d+)\]\]\((https?://[^\s)]+(?:\([^\s)]+\)[^\s)]*)*)\)"
)
REFERENCE_LINE_RE = re.compile(r"^(?P<indent>\s*)\[(\d+)\]\.\s+(.*)$", re.MULTILINE)
CENTER_CAPTION_RE = re.compile(
    r'<div\s+style="text-align:\s*center;?">',
    flags=re.IGNORECASE,
)
MERMAID_BLOCK_RE = re.compile(
    r"(?ms)^```[ \t]*mermaid[ \t]*\r?\n(.*?)\r?\n```[ \t]*$"
)
Y_AXIS_RE = re.compile(
    r"""
    ^
    (?P<indent>\s*)
    y-axis
    (?:\s+"(?P<label_quoted>[^"]*)")?
    (?:\s+(?P<label_bare>(?!-->)[^\s"][^\r\n]*?))?
    (?:\s+(?P<min>[-+]?(?:\d+(?:\.\d+)?|\.\d+)))?
    \s*-->?\s*
    (?P<max>[-+]?(?:\d+(?:\.\d+)?|\.\d+))
    \s*$
    """,
    re.VERBOSE,
)
EXTERNAL_LINK_RE = re.compile(
    r'<a\s+([^>]*?)href="(https?://[^"]+)"(?![^>]*\btarget=)([^>]*)>',
    flags=re.IGNORECASE,
)
CITATION_ANCHOR_RE = re.compile(
    r'(?<!<sup class="citation">)(<a\b[^>]*href="https?://[^"]+"[^>]*>\[(\d+)\]</a>)',
    flags=re.IGNORECASE,
)


@dataclass(slots=True)
class ConvertOptions:
    mermaid_security_level: str = "strict"
    timeline_max_label_len: int = 18
    scale_xychart: bool = True
    warn_on_invalid_number: bool = True
    title: str = "Document"


def read_text_with_fallback(path: Path) -> str:
    encodings = ["utf-8-sig", "utf-8", "gb18030", "gbk"]
    last_error: UnicodeDecodeError | None = None

    for enc in encodings:
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError as exc:
            last_error = exc

    raise UnicodeDecodeError(
        getattr(last_error, "encoding", "unknown"),
        getattr(last_error, "object", b""),
        getattr(last_error, "start", 0),
        getattr(last_error, "end", 0),
        f"无法正确解码文件：{path}",
    )


def normalize_whitespace_and_units(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    replacements = {
        "\u00a0": " ",
        "\u3000": " ",
        "端到-end": "端到端",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)

    text = re.sub(r"[ \t]+([，。；：！？、）】》])", r"\1", text)
    text = re.sub(r"([（【《])[ \t]+", r"\1", text)

    text = re.sub(
        rf"({NUMBER_PATTERN})\s*({LATIN_UNITS_PATTERN})\b",
        r"\1 \2",
        text,
    )
    text = re.sub(
        rf"({NUMBER_PATTERN})\s+({CHINESE_UNITS_PATTERN})",
        r"\1\2",
        text,
    )

    return text


def replace_citations(text: str) -> str:
    def _repl(match: re.Match[str]) -> str:
        idx, url = match.group(1), match.group(2).strip()
        safe_url = html.escape(url, quote=True)
        return (
            f'<sup class="citation">'
            f'<a href="{safe_url}" target="_blank" rel="noopener noreferrer">[{idx}]</a>'
            f"</sup>"
        )

    text = CITATION_RE.sub(_repl, text)
    text = re.sub(r"[ \t]+(<sup class=\"citation\">)", r"\1", text)
    return text


def normalize_reference_lines(text: str) -> str:
    lines = text.splitlines()
    result: list[str] = []

    in_reference_section = False

    for line in lines:
        stripped = line.strip().lower()
        if stripped in {
            "# 参考文献", "## 参考文献", "### 参考文献",
            "# references", "## references", "### references",
        }:
            in_reference_section = True
            result.append(line)
            continue

        if in_reference_section:
            m = REFERENCE_LINE_RE.match(line)
            if m:
                indent = m.group("indent")
                idx = m.group(2)
                content = m.group(3)
                result.append(f"{indent}- [{idx}] {content}")
                continue

            if stripped == "":
                result.append(line)
                continue

            if re.match(r"^\s*[-*]\s+", line):
                result.append(line)
                continue

            if re.match(r"^\s*#{1,6}\s+", line):
                in_reference_section = False

        result.append(line)

    return "\n".join(result)


def fix_center_caption_blocks(text: str) -> str:
    return CENTER_CAPTION_RE.sub(
        '<div class="figure-caption" markdown="1">',
        text,
    )


def looks_like_mermaid_timeline(lines: list[str]) -> bool:
    return any(line.strip().startswith("timeline") for line in lines)


def looks_like_mermaid_xychart(lines: list[str]) -> bool:
    return any(line.strip().startswith("xychart") for line in lines)


def smart_timeline_summary(text: str, max_len: int = 18) -> str:
    text = re.sub(r"\s+", " ", text.strip())
    if len(text) <= max_len:
        return text

    parts = [p.strip() for p in re.split(r"[，；。]", text) if p.strip()]
    if parts:
        first = parts[0]
        if len(first) <= max_len:
            return first
        text = first

    compact = (
        text.replace("以及", "/")
            .replace("及", "/")
            .replace("和", "/")
            .replace("与", "/")
            .replace("相关", "")
            .replace("方面", "")
    )

    if len(compact) <= max_len:
        return compact

    return compact[: max_len - 1].rstrip("，；：,. ") + "…"


def _should_skip_timeline_entry(stripped: str) -> bool:
    if not stripped or ":" not in stripped:
        return True
    return stripped.startswith("title ") or stripped.startswith("section ")


def preprocess_timeline_mermaid(code: str, *, max_len: int) -> tuple[str, str]:
    lines = code.splitlines()
    new_lines: list[str] = []
    notes: list[str] = []
    in_timeline = False

    for raw_line in lines:
        line = raw_line.rstrip()

        if line.strip().startswith("timeline"):
            in_timeline = True
            new_lines.append(line)
            continue

        if not in_timeline:
            new_lines.append(line)
            continue

        stripped = line.strip()

        if _should_skip_timeline_entry(stripped):
            new_lines.append(line)
            continue

        left, right = stripped.split(":", 1)
        date_text = left.strip()
        detail_text = right.strip()

        if not date_text or not detail_text:
            new_lines.append(line)
            continue

        short_text = smart_timeline_summary(detail_text, max_len=max_len)
        indent = re.match(r"^\s*", raw_line).group(0)
        new_lines.append(f"{indent}{date_text} : {short_text}")

        if short_text != detail_text:
            notes.append(
                f'<li><span class="date">{html.escape(date_text)}</span>：'
                f'{html.escape(detail_text)}</li>'
            )

    notes_html = ""
    if notes:
        notes_html = (
            '<div class="timeline-notes">'
            '<div class="timeline-notes-title">时间轴说明</div>'
            '<ul>'
            + "".join(notes)
            + "</ul></div>"
        )

    return "\n".join(new_lines), notes_html


def format_scaled_number(value: float) -> str:
    if abs(value - round(value)) < 1e-10:
        return str(int(round(value)))
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text if text else "0"


def parse_number_list(content: str, *, warn_on_invalid: bool = True) -> list[float]:
    values: list[float] = []
    invalid_items: list[str] = []
    last_end = 0

    for match in XYCHART_NUMBER_RE.finditer(content):
        separator = content[last_end:match.start()]
        invalid = separator.strip(" \t,")
        if invalid:
            invalid_items.append(invalid)

        normalized = match.group(0).replace(",", "")
        try:
            values.append(float(normalized))
        except ValueError:
            invalid_items.append(match.group(0).strip())

        last_end = match.end()

    tail = content[last_end:]
    invalid_tail = tail.strip(" \t,")
    if invalid_tail:
        invalid_items.append(invalid_tail)

    if invalid_items and warn_on_invalid:
        warnings.warn(
            f"xychart 数值解析失败，已跳过: {invalid_items}",
            stacklevel=2,
        )

    return values


def replace_number_list(line: str, new_values: list[float]) -> str:
    formatted = ", ".join(format_scaled_number(v) for v in new_values)
    return re.sub(r"\[[^\]]*\]", f"[{formatted}]", line, count=1)


def choose_engineering_scale(max_abs: float) -> int:
    if max_abs >= 1e12:
        return 12
    if max_abs >= 1e9:
        return 9
    if max_abs >= 1e6:
        return 6
    if max_abs >= 1e3:
        return 3
    return 0


def preprocess_xychart_mermaid(
    code: str,
    *,
    warn_on_invalid: bool = True,
) -> str:
    lines = code.splitlines()

    series_indexes: list[int] = []
    series_values: list[float] = []

    for idx, raw_line in enumerate(lines):
        stripped = raw_line.strip()
        if stripped.startswith(("line ", "bar ", "area ")):
            m = re.search(r"\[([^\]]+)\]", stripped)
            if not m:
                continue

            values = parse_number_list(
                m.group(1),
                warn_on_invalid=warn_on_invalid,
            )
            if not values:
                continue

            series_indexes.append(idx)
            series_values.extend(values)

    if not series_values:
        return code

    max_abs = max(abs(v) for v in series_values)
    scale_power = choose_engineering_scale(max_abs)
    if scale_power == 0:
        return code

    scale_factor = 10 ** scale_power
    new_lines = lines[:]

    for idx in series_indexes:
        stripped = new_lines[idx].strip()
        m = re.search(r"\[([^\]]+)\]", stripped)
        if not m:
            continue

        values = parse_number_list(
            m.group(1),
            warn_on_invalid=False,
        )
        if not values:
            continue

        scaled = [v / scale_factor for v in values]
        new_lines[idx] = replace_number_list(new_lines[idx], scaled)

    scaled_min = min(series_values) / scale_factor
    scaled_max = max(series_values) / scale_factor

    y_axis_updated = False
    for i, raw_line in enumerate(new_lines):
        stripped = raw_line.strip()
        if not stripped.startswith("y-axis"):
            continue

        m = Y_AXIS_RE.match(raw_line)
        if not m:
            continue

        indent = m.group("indent") or ""
        label = m.group("label_quoted") or m.group("label_bare") or ""
        axis_min = m.group("min")
        axis_max = m.group("max")

        label = label.strip()
        label = f"{label} (x1e{scale_power})" if label else f"x1e{scale_power}"

        if axis_min is not None and axis_max is not None:
            new_min = float(axis_min) / scale_factor
            new_max = float(axis_max) / scale_factor
        else:
            padding = (scaled_max - scaled_min) * 0.1 if scaled_max != scaled_min else max(abs(scaled_max) * 0.1, 1)
            new_min = scaled_min if scaled_min < 0 else 0
            new_max = scaled_max + padding

        new_lines[i] = (
            f'{indent}y-axis "{label}" '
            f'{format_scaled_number(new_min)} --> {format_scaled_number(new_max)}'
        )
        y_axis_updated = True
        break

    if not y_axis_updated:
        padding = (scaled_max - scaled_min) * 0.1 if scaled_max != scaled_min else max(abs(scaled_max) * 0.1, 1)
        auto_min = scaled_min if scaled_min < 0 else 0
        auto_max = scaled_max + padding
        new_lines.append(
            f'y-axis "x1e{scale_power}" {format_scaled_number(auto_min)} --> {format_scaled_number(auto_max)}'
        )

    return "\n".join(new_lines)


def preprocess_mermaid_code(code: str, options: ConvertOptions) -> tuple[str, str]:
    lines = code.splitlines()

    if looks_like_mermaid_timeline(lines):
        return preprocess_timeline_mermaid(
            code,
            max_len=options.timeline_max_label_len,
        )

    if looks_like_mermaid_xychart(lines) and options.scale_xychart:
        return preprocess_xychart_mermaid(
            code,
            warn_on_invalid=options.warn_on_invalid_number,
        ), ""

    return code, ""


def replace_mermaid_blocks(text: str, options: ConvertOptions) -> str:
    def _repl(match: re.Match[str]) -> str:
        mermaid_code = match.group(1).strip()
        mermaid_code, extra_html = preprocess_mermaid_code(mermaid_code, options)
        escaped = html.escape(mermaid_code)
        return (
            '\n<div class="mermaid-wrap"><div class="mermaid">'
            f"{escaped}</div></div>{extra_html}\n"
        )

    return MERMAID_BLOCK_RE.sub(_repl, text)


def preprocess_markdown(text: str, options: ConvertOptions) -> str:
    text = normalize_whitespace_and_units(text)
    text = replace_citations(text)
    text = normalize_reference_lines(text)
    text = fix_center_caption_blocks(text)
    text = replace_mermaid_blocks(text, options)
    return text


def postprocess_html(html_text: str) -> str:
    def _repl(match: re.Match[str]) -> str:
        before = match.group(1).rstrip()
        href = re.sub(r"\s+", "", match.group(2))
        after = match.group(3).rstrip()
        attrs = " ".join(part for part in [before, f'href="{href}"', after] if part)
        return f'<a {attrs} target="_blank" rel="noopener noreferrer">'

    def _wrap_citation(match: re.Match[str]) -> str:
        return f'<sup class="citation">{match.group(1)}</sup>'

    html_text = EXTERNAL_LINK_RE.sub(_repl, html_text)
    html_text = CITATION_ANCHOR_RE.sub(_wrap_citation, html_text)
    html_text = re.sub(r'[ \t]+(<sup class="citation">)', r"\1", html_text)
    return re.sub(r'(</sup>)[ \t]+(<sup class="citation">)', r"\1\2", html_text)


def convert_md_to_html(
    input_md: str | Path,
    output_html: str | Path,
    *,
    options: ConvertOptions | None = None,
) -> None:
    options = options or ConvertOptions()

    input_path = Path(input_md)
    output_path = Path(output_html)

    if not input_path.exists():
        raise FileNotFoundError(f"Markdown 文件不存在: {input_path}")

    if input_path.suffix.lower() != ".md":
        warnings.warn(
            f"输入文件看起来不是 .md 文件：{input_path.name}",
            stacklevel=2,
        )

    md_content = read_text_with_fallback(input_path)
    md_content = preprocess_markdown(md_content, options)

    html_body = markdown.markdown(
        md_content,
        extensions=[
            "extra",
            "toc",
            "md_in_html",
        ],
        output_format="html5",
    )

    full_html = HTML_TEMPLATE.format(
        title=html.escape(options.title, quote=True),
        content=postprocess_html(html_body),
        mermaid_security_level=html.escape(options.mermaid_security_level, quote=True),
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(full_html, encoding="utf-8", newline="\n")

    logger.info("HTML 生成成功: %s", output_path.resolve())
    logger.info("Mermaid 将在浏览器中直接渲染，无需安装 mermaid-cli")
    logger.info("Mermaid 安全级别: %s", options.mermaid_security_level)
    logger.info("已处理: 图注、引用、参考文献列表、单位显示、Mermaid 时间轴/图表")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="将 Markdown 转换为带样式和 Mermaid 支持的 HTML"
    )
    parser.add_argument("input", nargs="?", default="input.md", help="输入 Markdown 文件")
    parser.add_argument("output", nargs="?", default="output.html", help="输出 HTML 文件")
    parser.add_argument("--title", default="Document", help="HTML 文档标题")
    parser.add_argument(
        "--mermaid-security-level",
        default="strict",
        choices=["strict", "loose", "antiscript", "sandbox"],
        help="Mermaid 安全级别，默认 strict",
    )
    parser.add_argument(
        "--timeline-max-label-len",
        type=int,
        default=18,
        help="timeline 标签最大长度，默认 18",
    )
    parser.add_argument(
        "--no-scale-xychart",
        action="store_true",
        help="关闭 Mermaid xychart 数值缩放",
    )
    parser.add_argument(
        "--quiet-invalid-number-warning",
        action="store_true",
        help="关闭 xychart 非法数值警告",
    )
    return parser


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()

    options = ConvertOptions(
        mermaid_security_level=args.mermaid_security_level,
        timeline_max_label_len=args.timeline_max_label_len,
        scale_xychart=not args.no_scale_xychart,
        warn_on_invalid_number=not args.quiet_invalid_number_warning,
        title=args.title,
    )

    convert_md_to_html(args.input, args.output, options=options)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logger.exception("转换失败")
        raise
