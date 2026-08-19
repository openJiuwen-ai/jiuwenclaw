# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Line-based body text scanning for artifact path detection (stdlib only)."""

from __future__ import annotations

import re
import threading

_BODY_SCAN_MAX_LINE_LEN = 8192
BODY_SCAN_MAX_LINE_LEN = _BODY_SCAN_MAX_LINE_LEN
_PATH_TRAILING_CHARS = "'\"`\\]\\}\\),.;:，。；、："

# 文件路径检测的正则表达式模式（仅用于正文回退扫描）
_FILE_PATH_PATTERNS = [
    re.compile(
        r'\{(?:workspace|output_dir)\}[/\\][^\s\]\}\)\,\'\"`<>，。；、：]+\.[a-zA-Z0-9]{1,10}',
        re.IGNORECASE,
    ),
    re.compile(
        r'(?:(?:[A-Za-z]:)?[/\\]|\.{1,2}[/\\])?[^\s\]\}\)\,\'\"`<>，。；、：]*'
        r'[/\\](?:workspace|output)[/\\][^\s\]\}\)\,\'\"`<>，。；、：]*'
        r'[^\s\]\}\)\,\'\"`<>，。；、：]+\.[a-zA-Z0-9]{1,10}',
        re.IGNORECASE,
    ),
]


def _clean_path_candidate(path_str: str) -> str:
    return path_str.strip().strip(_PATH_TRAILING_CHARS).strip()


def _path_identity(path_str: str) -> str:
    return path_str.replace("\\", "/").lower()


def scan_body_text_for_paths(
    result_text: str,
    *,
    cancel_event: threading.Event | None = None,
) -> tuple[list[str], int, int, int]:
    """Scan body text line-by-line for file path candidates.

    Returns:
        (unique_candidates, total_regex_matches, lines_scanned, lines_skipped)
    """
    candidates: list[str] = []
    seen: set[str] = set()
    total_regex_matches = 0
    lines_scanned = 0
    lines_skipped = 0

    for line in result_text.splitlines():
        if cancel_event is not None and cancel_event.is_set():
            break
        if len(line) > _BODY_SCAN_MAX_LINE_LEN:
            lines_skipped += 1
            continue
        lines_scanned += 1
        for pattern in _FILE_PATH_PATTERNS:
            matches = pattern.findall(line)
            total_regex_matches += len(matches)
            for match in matches:
                cleaned = _clean_path_candidate(match)
                if not cleaned:
                    continue
                identity = _path_identity(cleaned)
                if identity in seen:
                    continue
                seen.add(identity)
                candidates.append(cleaned)

    return candidates, total_regex_matches, lines_scanned, lines_skipped
