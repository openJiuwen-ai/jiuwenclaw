# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from __future__ import annotations

import re
import warnings
from dataclasses import dataclass

import unicodedata

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

TIMELINE_NOTES_TITLE = "时间轴说明"
ELLIPSIS = "…"
TIMELINE_SPLIT_RE = re.compile(r"[，；、,;]")


def _compile_unit_pattern(units: list[str]) -> str:
    """Compile a regex pattern string from a list of unit strings.

    Escapes each unit for regex safety and joins them with alternation (|),
    sorting by length (longest first) to ensure longer units are matched before
    shorter ones (e.g., "GB/s" before "GB").

    Args:
        units: List of unit strings to compile into a regex alternation pattern.

    Returns:
        A regex pattern string suitable for use in regex matching.
    """
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


@dataclass(slots=True)
class MermaidRenderOptions:
    timeline_max_label_len: int = 18
    scale_xychart: bool = True
    warn_on_invalid_number: bool = True
    show_xychart_value_labels: bool = True


@dataclass(slots=True)
class XyChartSeriesMetadata:
    index: int
    kind: str
    values: list[float]
    display_values: list[str]


@dataclass(slots=True)
class XyChartMetadata:
    series: list[XyChartSeriesMetadata]
    chart_orientation: str = "vertical"


def normalize_whitespace_and_units(text: str) -> str:
    """Normalize whitespace and spacing around numeric units in text.

    Performs Unicode NFC normalization, converts line endings to LF,
    replaces various whitespace characters with standard spaces, and
    adjusts spacing between numbers and units (adds space for Latin units,
    removes space for Chinese units).

    Args:
        text: Input text to normalize.

    Returns:
        Normalized text with consistent whitespace and unit spacing.
    """
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    replacements = {
        "\u00a0": " ",
        "\u3000": " ",
        "端到-end": "端到端",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)

    text = re.sub(r"[ \t]+([，。；：！？、）》】])", r"\1", text)
    text = re.sub(r"([（《【])[ \t]+", r"\1", text)

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


def looks_like_mermaid_timeline(lines: list[str]) -> bool:
    """Check if the Mermaid code appears to be a timeline diagram.

    Args:
        lines: List of lines from the Mermaid code.

    Returns:
        True if any line starts with 'timeline', False otherwise.
    """
    return any(line.strip().startswith("timeline") for line in lines)


def looks_like_mermaid_xychart(lines: list[str]) -> bool:
    """Check if the Mermaid code appears to be an xychart diagram.

    Args:
        lines: List of lines from the Mermaid code.

    Returns:
        True if any line starts with 'xychart', False otherwise.
    """
    return any(line.strip().startswith("xychart") for line in lines)


def _display_width(text: str) -> int:
    """Calculate the display width of text considering East Asian character widths.

    Full-width (F) and wide (W) East Asian characters are counted as 2 display
    columns, while all other characters count as 1.

    Args:
        text: Text string to measure display width.

    Returns:
        The display width in columns.
    """
    width = 0
    for ch in text:
        if unicodedata.east_asian_width(ch) in {"F", "W"}:
            width += 2
        else:
            width += 1
    return width


def _trim_to_display_width(text: str, max_width: int) -> str:
    """Trim text to fit within a maximum display width.

    Truncates the text character by character, respecting East Asian character
    widths, until the display width is at or below max_width.

    Args:
        text: Text string to trim.
        max_width: Maximum display width in columns. If <= 0, returns empty string.

    Returns:
        Truncated text that fits within max_width display columns.
    """
    if max_width <= 0:
        return ""

    parts: list[str] = []
    width = 0
    for ch in text:
        ch_width = 2 if unicodedata.east_asian_width(ch) in {"F", "W"} else 1
        if width + ch_width > max_width:
            break
        parts.append(ch)
        width += ch_width
    return "".join(parts)


def _compact_timeline_text(text: str) -> str:
    """Compact timeline text by replacing verbose Chinese connectors.

    Replaces Chinese conjunction words ("以及", "及", "和", "与") with "/",
    removes filler words ("相关", "方面"), and collapses whitespace.

    Args:
        text: Timeline text to compact.

    Returns:
        Compacted text with shorter connectors and normalized whitespace.
    """
    compact = (
        text.replace("以及", "/")
        .replace("及", "/")
        .replace("和", "/")
        .replace("与", "/")
        .replace("相关", "")
        .replace("方面", "")
    )
    return re.sub(r"\s+", " ", compact).strip()


def smart_timeline_summary(text: str, max_len: int = 18) -> str:
    """Generate a smart summary for timeline labels within display width limit.

    Attempts multiple strategies to shorten text: direct truncation, splitting
    by separators, compacting Chinese connectors, and finally trimming with
    ellipsis. Respects East Asian character widths throughout.

    Args:
        text: Original timeline text to summarize.
        max_len: Maximum display width in columns (default 18).

    Returns:
        Shortened text within max_len display columns, possibly with ellipsis.
    """
    text = re.sub(r"\s+", " ", text.strip())
    if not text:
        return ""

    if _display_width(text) <= max_len:
        return text

    parts = [part.strip() for part in TIMELINE_SPLIT_RE.split(text) if part.strip()]
    candidate = parts[0] if parts else text
    if _display_width(candidate) <= max_len:
        return candidate

    compact = _compact_timeline_text(candidate)
    if _display_width(compact) <= max_len:
        return compact

    budget = max(max_len - _display_width(ELLIPSIS), 1)
    summary = _trim_to_display_width(compact, budget).rstrip("，；,. ")
    return f"{summary or _trim_to_display_width(text, budget)}{ELLIPSIS}"


def _build_timeline_notes(notes: list[tuple[str, str]]) -> str:
    """Build formatted notes section for timeline entries that were shortened.

    Creates a Markdown-formatted section with title and bullet list of
    date-detail pairs for entries that exceeded the display width limit.

    Args:
        notes: List of (date_text, detail_text) tuples for shortened entries.

    Returns:
        Formatted Markdown string, or empty string if no notes provided.
    """
    if not notes:
        return ""

    lines = [f"**{TIMELINE_NOTES_TITLE}**"]
    lines.append("")
    for date_text, detail_text in notes:
        lines.append(f"- {date_text}: {detail_text}")
    return "\n".join(lines)


def _should_skip_timeline_entry(stripped: str) -> bool:
    """Determine if a timeline entry line should be skipped from processing.

    Skips empty lines, lines without colon separator, and lines that are
    timeline metadata (title or section declarations).

    Args:
        stripped: Stripped content of the timeline line.

    Returns:
        True if the line should be skipped, False if it should be processed.
    """
    if not stripped or ":" not in stripped:
        return True
    return stripped.startswith("title ") or stripped.startswith("section ")


def preprocess_timeline_mermaid(code: str, *, max_len: int) -> tuple[str, str]:
    """Preprocess Mermaid timeline code to shorten long labels.

    Iterates through timeline entries, applies smart_timeline_summary to
    compress detail text to fit within max_len display width. Collects
    original text for shortened entries into a notes section.

    Args:
        code: Raw Mermaid timeline code string.
        max_len: Maximum display width for timeline labels in columns.

    Returns:
        A tuple of (processed_code, notes_markdown) where processed_code
        contains shortened labels and notes_markdown contains full text
        for entries that were truncated.
    """
    lines = code.splitlines()
    new_lines: list[str] = []
    notes: list[tuple[str, str]] = []
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
        detail_text = re.sub(r"\s+", " ", right.strip())
        if not date_text or not detail_text:
            new_lines.append(line)
            continue

        short_text = smart_timeline_summary(detail_text, max_len=max_len)
        indent = re.match(r"^\s*", raw_line).group(0)
        new_lines.append(f"{indent}{date_text} : {short_text}")

        if short_text != detail_text:
            notes.append((date_text, detail_text))

    return "\n".join(new_lines), _build_timeline_notes(notes)


def format_scaled_number(value: float) -> str:
    """Format a scaled number for display, removing unnecessary decimals.

    Returns integer string if the value is effectively an integer (within
    floating-point tolerance). Otherwise, formats with up to 6 decimal places
    and strips trailing zeros and unnecessary decimal points.

    Args:
        value: Numeric value to format.

    Returns:
        Formatted string representation of the number.
    """
    if abs(value - round(value)) < 1e-10:
        return str(int(round(value)))
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text if text else "0"


def parse_number_list(content: str, *, warn_on_invalid: bool = True) -> list[float]:
    """Parse a comma-separated list of numbers from xychart content.

    Extracts numeric values using regex, handles comma separators, and
    optionally warns about invalid/non-numeric items that are skipped.

    Args:
        content: String containing comma-separated numbers (e.g., "[1, 2, 3").
        warn_on_invalid: If True, emit warnings for skipped invalid items.

    Returns:
        List of parsed float values from the content.
    """
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

    invalid_tail = content[last_end:].strip(" \t,")
    if invalid_tail:
        invalid_items.append(invalid_tail)

    if invalid_items and warn_on_invalid:
        warnings.warn(f"xychart 数值解析失败，已跳过: {invalid_items}", stacklevel=2)

    return values


def replace_number_list(line: str, new_values: list[float]) -> str:
    """Replace the first number list bracket in a line with new values.

    Finds the first [...] bracket in the line and replaces its contents
    with formatted values from new_values.

    Args:
        line: Original line containing a number list bracket.
        new_values: List of float values to insert into the bracket.

    Returns:
        Modified line with the bracket contents replaced.
    """
    formatted = ", ".join(format_scaled_number(value) for value in new_values)
    return re.sub(r"\[[^\]]*\]", f"[{formatted}]", line, count=1)


def choose_engineering_scale(max_abs: float) -> int:
    """Choose an appropriate engineering scale (power of 10) for a maximum value.

    Selects scale based on magnitude: 12 (tera) for >= 1e12, 9 (giga) for >= 1e9,
    6 (mega) for >= 1e6, 3 (kilo) for >= 1e3, or 0 (no scaling) otherwise.

    Args:
        max_abs: Maximum absolute value in the dataset.

    Returns:
        Power of 10 for scaling (12, 9, 6, 3, or 0).
    """
    if max_abs >= 1e12:
        return 12
    if max_abs >= 1e9:
        return 9
    if max_abs >= 1e6:
        return 6
    if max_abs >= 1e3:
        return 3
    return 0


def extract_xychart_metadata(
    code: str,
    *,
    warn_on_invalid: bool = True,
) -> XyChartMetadata:
    """Extract metadata from Mermaid xychart code including series data.

    Parses xychart code to identify series (line, bar, area), extract their
    numeric values, and determine chart orientation.

    Args:
        code: Mermaid xychart code string.
        warn_on_invalid: If True, warn on invalid number parsing.

    Returns:
        XyChartMetadata containing series information and chart orientation.
    """
    chart_orientation = "horizontal" if is_horizontal_xychart(code) else "vertical"
    series: list[XyChartSeriesMetadata] = []

    for raw_line in code.splitlines():
        stripped = raw_line.strip()
        kind = next(
            (candidate for candidate in ("line", "bar", "area") if stripped.startswith(f"{candidate} ")),
            None,
        )
        if kind is None:
            continue

        match = re.search(r"\[([^\]]+)\]", stripped)
        if not match:
            continue

        values = parse_number_list(match.group(1), warn_on_invalid=warn_on_invalid)
        if not values:
            continue

        series.append(
            XyChartSeriesMetadata(
                index=len(series),
                kind=kind,
                values=values,
                display_values=[format_scaled_number(value) for value in values],
            )
        )

    return XyChartMetadata(series=series, chart_orientation=chart_orientation)


def is_horizontal_xychart(code: str) -> bool:
    """Determine if an xychart is configured as horizontal orientation.

    Checks for 'horizontal' keyword in the xychart declaration line or
    in YAML frontmatter with 'horizontal: true'.

    Args:
        code: Mermaid xychart code string.

    Returns:
        True if chart is horizontal, False for vertical.
    """
    lines = code.splitlines()
    for line in lines:
        stripped = line.strip().lower()
        if stripped.startswith("xychart") and " horizontal" in f" {stripped}":
            return True

    frontmatter_match = re.match(r"^\s*---\s*\n(.*?)\n---\s*\n?", code.strip(), flags=re.DOTALL)
    if not frontmatter_match:
        return False

    frontmatter = frontmatter_match.group(1)
    return bool(re.search(r"^\s*horizontal\s*:\s*true\s*$", frontmatter, flags=re.IGNORECASE | re.MULTILINE))


def preprocess_xychart_mermaid(
    code: str,
    *,
    warn_on_invalid: bool = True,
) -> str:
    """Preprocess Mermaid xychart code to scale large numeric values.

    Identifies series data (line, bar, area), determines appropriate engineering
    scale, divides values by the scale factor, and updates y-axis labels to
    reflect the scaling (e.g., "x1e6" for million-scale).

    Args:
        code: Mermaid xychart code string.
        warn_on_invalid: If True, warn on invalid number parsing.

    Returns:
        Preprocessed xychart code with scaled values and updated y-axis.
        Returns original code if no scaling is needed or no series found.
    """
    lines = code.splitlines()

    series_indexes: list[int] = []
    series_values: list[float] = []

    for idx, raw_line in enumerate(lines):
        stripped = raw_line.strip()
        if not stripped.startswith(("line ", "bar ", "area ")):
            continue

        match = re.search(r"\[([^\]]+)\]", stripped)
        if not match:
            continue

        values = parse_number_list(match.group(1), warn_on_invalid=warn_on_invalid)
        if not values:
            continue

        series_indexes.append(idx)
        series_values.extend(values)

    if not series_values:
        return code

    max_abs = max(abs(value) for value in series_values)
    scale_power = choose_engineering_scale(max_abs)
    if scale_power == 0:
        return code

    scale_factor = 10 ** scale_power
    new_lines = lines[:]

    for idx in series_indexes:
        match = re.search(r"\[([^\]]+)\]", new_lines[idx].strip())
        if not match:
            continue

        values = parse_number_list(match.group(1), warn_on_invalid=False)
        if not values:
            continue

        scaled = [value / scale_factor for value in values]
        new_lines[idx] = replace_number_list(new_lines[idx], scaled)

    scaled_min = min(series_values) / scale_factor
    scaled_max = max(series_values) / scale_factor

    y_axis_updated = False
    for index, raw_line in enumerate(new_lines):
        stripped = raw_line.strip()
        if not stripped.startswith("y-axis"):
            continue

        match = Y_AXIS_RE.match(raw_line)
        if not match:
            continue

        indent = match.group("indent") or ""
        label = match.group("label_quoted") or match.group("label_bare") or ""
        axis_min = match.group("min")
        axis_max = match.group("max")

        label = label.strip()
        label = f"{label} (x1e{scale_power})" if label else f"x1e{scale_power}"

        if axis_min is not None and axis_max is not None:
            new_min = float(axis_min) / scale_factor
            new_max = float(axis_max) / scale_factor
        else:
            padding = (
                (scaled_max - scaled_min) * 0.1
                if scaled_max != scaled_min
                else max(abs(scaled_max) * 0.1, 1)
            )
            new_min = scaled_min if scaled_min < 0 else 0
            new_max = scaled_max + padding

        new_lines[index] = (
            f'{indent}y-axis "{label}" '
            f'{format_scaled_number(new_min)} --> {format_scaled_number(new_max)}'
        )
        y_axis_updated = True
        break

    if not y_axis_updated:
        padding = (
            (scaled_max - scaled_min) * 0.1
            if scaled_max != scaled_min
            else max(abs(scaled_max) * 0.1, 1)
        )
        auto_min = scaled_min if scaled_min < 0 else 0
        auto_max = scaled_max + padding
        new_lines.append(
            f'y-axis "x1e{scale_power}" {format_scaled_number(auto_min)} --> {format_scaled_number(auto_max)}'
        )

    return "\n".join(new_lines)


def preprocess_mermaid_code(
    code: str,
    options: MermaidRenderOptions | None = None,
) -> tuple[str, str]:
    """Preprocess Mermaid code based on diagram type and render options.

    Dispatches to appropriate preprocessing functions based on detected
    diagram type (timeline or xychart). Timeline preprocessing shortens
    labels and generates notes. Xychart preprocessing scales large values.

    Args:
        code: Mermaid code string to preprocess.
        options: Render options controlling preprocessing behavior.
            If None, uses default MermaidRenderOptions.

    Returns:
        A tuple of (processed_code, notes_or_empty) where:
        - processed_code: Preprocessed Mermaid code
        - notes_or_empty: Timeline notes markdown, or empty string for other diagrams
    """
    options = options or MermaidRenderOptions()
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
