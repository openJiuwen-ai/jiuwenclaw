# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from __future__ import annotations

import logging
import re
from copy import deepcopy
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = {
    "theme": "base",
    "look": "classic",
    "themeVariables": {
        "background": "#ffffff",
        "primaryTextColor": "#111827",
        "secondaryTextColor": "#111827",
        "tertiaryTextColor": "#111827",
        "lineColor": "#374151",
        "textColor": "#111827",
        "mainBkg": "#ffffff",
        "secondBkg": "#f9fafb",
        "tertiaryColor": "#ffffff",
        "xyChart": {
            "plotColorPalette": "#4338ca, #b91c1c, #047857, #b45309, #6d28d9"
        },
    },
}

_FRONTMATTER_RE = re.compile(r"^\s*---\s*\n(.*?)\n---\s*\n?", re.DOTALL)

try:
    import yaml

    YAML_AVAILABLE = True
except Exception:
    YAML_AVAILABLE = False


def _deep_merge(base: dict, override: dict) -> dict:
    """Deep merge two dictionaries, recursively combining nested dicts.

    For nested dictionary values, performs recursive merge. For non-dict values
    or mismatched types, override values replace base values completely.

    Args:
        base: The base dictionary to merge into.
        override: The dictionary containing values to override/merge.

    Returns:
        A new dictionary containing the merged result. Neither input dict is modified.
    """
    result = deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _extract_frontmatter(code: str) -> tuple[str, str]:
    """Extract YAML frontmatter from Mermaid diagram code.

    Parses code that may contain YAML frontmatter delimited by '---' markers,
    separating it from the actual Mermaid diagram body.

    Args:
        code: The Mermaid code string, possibly containing frontmatter.

    Returns:
        A tuple of (frontmatter_content, diagram_body). If no frontmatter is
        present, returns ("", stripped_code).
    """
    match = _FRONTMATTER_RE.match(code.strip())
    if match:
        return match.group(1), code.strip()[match.end():].strip()
    return "", code.strip()


def _dump_frontmatter(config_dict: dict) -> str:
    """Serialize a config dictionary to Mermaid YAML frontmatter format.

    Outputs the config wrapped in '---' delimiters suitable for Mermaid diagrams.
    Uses PyYAML if available for proper serialization; otherwise falls back to
    a hardcoded template format.

    Args:
        config_dict: The configuration dictionary to serialize, typically containing
            'theme', 'look', and 'themeVariables' keys.

    Returns:
        A YAML frontmatter string with '---' delimiters, ready to prepend to
        Mermaid diagram code.
    """
    if YAML_AVAILABLE:
        text = yaml.safe_dump(
            {"config": config_dict},
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        ).strip()
        return f"---\n{text}\n---\n"

    theme_variables = config_dict.get("themeVariables", {})
    xychart = theme_variables.get("xyChart", {})
    return (
        "---\n"
        "config:\n"
        f"  theme: {config_dict.get('theme', 'base')}\n"
        f"  look: {config_dict.get('look', 'classic')}\n"
        "  themeVariables:\n"
        f"    background: '{theme_variables.get('background', '#ffffff')}'\n"
        f"    primaryTextColor: '{theme_variables.get('primaryTextColor', '#111827')}'\n"
        f"    secondaryTextColor: '{theme_variables.get('secondaryTextColor', '#111827')}'\n"
        f"    tertiaryTextColor: '{theme_variables.get('tertiaryTextColor', '#111827')}'\n"
        f"    lineColor: '{theme_variables.get('lineColor', '#374151')}'\n"
        f"    textColor: '{theme_variables.get('textColor', '#111827')}'\n"
        f"    mainBkg: '{theme_variables.get('mainBkg', '#ffffff')}'\n"
        f"    secondBkg: '{theme_variables.get('secondBkg', '#f9fafb')}'\n"
        f"    tertiaryColor: '{theme_variables.get('tertiaryColor', '#ffffff')}'\n"
        "    xyChart:\n"
        f"      plotColorPalette: '{xychart.get('plotColorPalette', '#4338ca, #b91c1c, #047857, #b45309, #6d28d9')}'\n"
        "---\n"
    )


def _build_merged_frontmatter(frontmatter: str, body: str) -> str:
    """Build merged frontmatter by combining existing frontmatter with defaults.

    Parses existing frontmatter (if present), merges it with DEFAULT_CONFIG using
    deep merge, ensures required fields exist, and serializes the result.

    Args:
        frontmatter: The extracted frontmatter string (may be empty).
        body: The Mermaid diagram code body.

    Returns:
        Complete Mermaid code with properly formatted frontmatter prepended to
        the diagram body. Uses default config if parsing fails or frontmatter
        is empty.
    """
    if not frontmatter:
        return _dump_frontmatter(DEFAULT_CONFIG) + body.strip()

    if not YAML_AVAILABLE:
        logger.warning("PyYAML is unavailable; keeping existing Mermaid frontmatter.")
        return f"---\n{frontmatter.strip()}\n---\n{body.strip()}"

    try:
        parsed = yaml.safe_load(frontmatter) or {}
        if not isinstance(parsed, dict):
            parsed = {}
    except Exception as exc:
        logger.warning("Failed to parse Mermaid frontmatter, using defaults: %s", exc)
        return _dump_frontmatter(DEFAULT_CONFIG) + body.strip()

    if "config" in parsed and isinstance(parsed["config"], dict):
        existing_config = parsed["config"]
    else:
        existing_config = parsed if isinstance(parsed, dict) else {}

    merged_config = _deep_merge(DEFAULT_CONFIG, existing_config)
    merged_config.setdefault("theme", "base")
    merged_config.setdefault("look", "classic")
    merged_config.setdefault("themeVariables", {})
    merged_config["themeVariables"].setdefault(
        "xyChart",
        {"plotColorPalette": "#4338ca, #b91c1c, #047857, #b45309, #6d28d9"},
    )
    merged_config["themeVariables"]["xyChart"].setdefault(
        "plotColorPalette",
        "#4338ca, #b91c1c, #047857, #b45309, #6d28d9",
    )
    return _dump_frontmatter(merged_config) + body.strip()


def clean_mermaid_code(code: str) -> str:
    """Clean and normalize Mermaid diagram code with proper frontmatter.

    Extracts existing frontmatter, merges it with default configuration settings,
    and returns normalized Mermaid code with consistent styling configuration.

    Args:
        code: Raw Mermaid diagram code, possibly with frontmatter.

    Returns:
        Cleaned Mermaid code with merged frontmatter configuration, stripped
        of leading/trailing whitespace.
    """
    frontmatter, body = _extract_frontmatter(code.strip())
    return _build_merged_frontmatter(frontmatter, body).strip()


def save_failed_mermaid_source(
    code: str,
    debug_base_path: Path,
    *,
    extra_text: str = "",
) -> None:
    """Save failed Mermaid source code and optional error details for debugging.

    Creates debug files when Mermaid rendering fails, preserving the source code
    and any error messages for troubleshooting.

    Args:
        code: The Mermaid source code that failed to render.
        debug_base_path: Base path for debug files. A '.mmd' file will be created
            for the source code, and '.error.txt' for error details if provided.
        extra_text: Optional error message or additional context to save in a
            separate '.error.txt' file.

    Returns:
        None. Files are written and log warnings are emitted.
    """
    debug_base_path.parent.mkdir(parents=True, exist_ok=True)
    failed_src = debug_base_path.with_suffix(".mmd")
    failed_src.write_text(code, encoding="utf-8")
    logger.warning("Saved Mermaid source for debugging: %s", failed_src)
    if extra_text:
        failed_log = debug_base_path.with_suffix(".error.txt")
        failed_log.write_text(extra_text, encoding="utf-8")
        logger.warning("Saved Mermaid error details: %s", failed_log)


def load_svg_markup(svg_path: str | Path) -> str:
    """Load SVG file and strip XML declaration and DOCTYPE for clean markup.

    Reads an SVG file and removes XML preamble (<?xml...?>) and DOCTYPE
    declarations, returning clean SVG markup suitable for embedding.

    Args:
        svg_path: Path to the SVG file (string or Path object).

    Returns:
        Clean SVG markup string without XML declaration or DOCTYPE,
        ready for HTML embedding.
    """
    svg_text = Path(svg_path).read_text(encoding="utf-8")
    svg_text = re.sub(r"^\s*<\?xml[^>]*>\s*", "", svg_text, flags=re.IGNORECASE)
    svg_text = re.sub(r"^\s*<!DOCTYPE[^>]*>\s*", "", svg_text, flags=re.IGNORECASE)
    return svg_text.strip()
