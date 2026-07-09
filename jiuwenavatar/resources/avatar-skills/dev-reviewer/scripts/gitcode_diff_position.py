#!/usr/bin/env python3
"""Resolve GitCode PR inline comment ``position`` from ``location`` and ``pr.diff``.

GitCode maps ``position`` / ``diff_position.start_new_line`` to the **line number in
the post-merge (new) file**, not to a line index inside the unified ``pr.diff`` text.

Common mistake: using the line number of a row inside ``pr.diff`` (e.g. 194) as
``position``, which anchors the comment on source line 194 instead of the intended
line (e.g. 965).
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Iterable

HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
LOCATION_RE = re.compile(
    r"^(?P<path>.+?):(?P<start>\d+)(?:-(?P<end>\d+))?$"
)
DiffIndex = dict[str, dict[str, Any]]


def normalize_repo_path(path: str) -> str:
    return path.strip().replace("\\", "/")


def parse_location(location: str) -> tuple[str, int, int | None]:
    """Parse ``path:line`` or ``path:start-end`` from a finding ``location``."""
    text = (location or "").strip()
    if not text:
        raise ValueError("empty location")
    match = LOCATION_RE.match(text)
    if not match:
        raise ValueError(f"invalid location format: {location!r}")
    path = normalize_repo_path(match.group("path"))
    start = int(match.group("start"))
    end = int(match.group("end")) if match.group("end") else None
    return path, start, end


def build_diff_index(diff_text: str) -> DiffIndex:
    """Index unified diff hunks once, keyed by normalized new-file path."""
    index: DiffIndex = {}
    current: str | None = None
    new_line = 0
    in_hunk = False

    for raw in diff_text.splitlines():
        if raw.startswith("diff --git "):
            current = None
            in_hunk = False
            continue
        if raw.startswith("+++ b/"):
            candidate = normalize_repo_path(raw[6:].strip())
            current = candidate if candidate != "/dev/null" else None
            in_hunk = False
            if current:
                index.setdefault(current, {"ranges": [], "added": set()})
            continue
        if current is None:
            continue
        if raw.startswith("@@"):
            match = HUNK_RE.match(raw)
            if not match:
                in_hunk = False
                continue
            new_line = int(match.group(3))
            new_count = int(match.group(4) or "1")
            in_hunk = True
            if new_count > 0:
                index[current]["ranges"].append((new_line, new_line + new_count - 1))
            continue
        if not in_hunk:
            continue
        if raw.startswith("+") and not raw.startswith("+++"):
            index[current]["added"].add(new_line)
            new_line += 1
        elif raw.startswith("-") and not raw.startswith("---"):
            continue
        elif raw.startswith(" "):
            new_line += 1
        elif raw.startswith("\\"):
            continue

    return index


def parse_hunk_new_ranges(diff_text: str, file_path: str) -> list[tuple[int, int]]:
    """Return inclusive [start, end] new-file line ranges covered by diff hunks."""
    target = normalize_repo_path(file_path)
    return list((build_diff_index(diff_text).get(target) or {}).get("ranges") or [])


def line_in_hunk_ranges(line: int, ranges: Iterable[tuple[int, int]]) -> bool:
    return any(start <= line <= end for start, end in ranges)


def collect_added_new_lines(diff_text: str, file_path: str) -> set[int]:
    """New-file line numbers that appear as ``+`` lines in the file diff."""
    target = normalize_repo_path(file_path)
    return set((build_diff_index(diff_text).get(target) or {}).get("added") or set())


def pick_line_for_range(
    start: int,
    end: int | None,
    ranges: list[tuple[int, int]],
    added_lines: set[int],
) -> int | None:
    """Choose the best new-file line for a comment inside ``start``/``end``."""
    candidates = [start] if end is None else list(range(start, end + 1))
    in_hunk = [line for line in candidates if line_in_hunk_ranges(line, ranges)]
    if not in_hunk:
        return None
    for line in in_hunk:
        if line in added_lines:
            return line
    return in_hunk[0]


def resolve_gitcode_position(
    diff_text: str,
    file_path: str,
    line: int,
    *,
    prefer_added: bool = True,
) -> int | None:
    """Resolve GitCode ``position`` for a new-file line, or ``None`` if out of diff."""
    ranges = parse_hunk_new_ranges(diff_text, file_path)
    if not line_in_hunk_ranges(line, ranges):
        return None
    if not prefer_added:
        return line
    added = collect_added_new_lines(diff_text, file_path)
    if line in added:
        return line
    return line


def resolve_position_from_location(
    diff_text: str,
    location: str,
    *,
    path: str = "",
    diff_index: DiffIndex | None = None,
) -> tuple[int | None, str, list[str]]:
    """Return ``(position, path, warnings)`` from a finding location string."""
    warnings: list[str] = []
    try:
        loc_path, start, end = parse_location(location)
    except ValueError as exc:
        return None, normalize_repo_path(path), [str(exc)]

    file_path = normalize_repo_path(path or loc_path)
    if path and normalize_repo_path(path) != loc_path:
        warnings.append(
            f"path ({path}) differs from location ({loc_path}); using path for diff lookup"
        )

    index = diff_index if diff_index is not None else build_diff_index(diff_text)
    entry = index.get(file_path) or {}
    ranges = list(entry.get("ranges") or [])
    if not ranges:
        warnings.append(f"no diff hunks for {file_path}")
        return None, file_path, warnings

    added = set(entry.get("added") or set())
    chosen = pick_line_for_range(start, end, ranges, added)
    if chosen is None:
        warnings.append(
            f"location {location} is outside diff hunks for {file_path}"
        )
        return None, file_path, warnings

    return chosen, file_path, warnings


def sync_finding_positions(
    review: dict[str, Any],
    diff_text: str,
    *,
    overwrite: bool = True,
) -> list[str]:
    """Fill ``position`` / ``path`` on findings from ``location`` + ``pr.diff``."""
    messages: list[str] = []
    findings = review.get("findings")
    if not isinstance(findings, dict):
        return messages

    diff_index = build_diff_index(diff_text)
    for bucket in ("must_fix", "should_fix"):
        items = findings.get(bucket)
        if not isinstance(items, list):
            continue
        for finding in items:
            if not isinstance(finding, dict):
                continue
            location = str(finding.get("location") or "").strip()
            if not location:
                continue
            old_pos = finding.get("position")
            resolved, path, warns = resolve_position_from_location(
                diff_text,
                location,
                path=str(finding.get("path") or ""),
                diff_index=diff_index,
            )
            finding_id = finding.get("id") or location
            for warn in warns:
                messages.append(f"{finding_id}: {warn}")
            if resolved is None:
                if overwrite and old_pos is not None:
                    messages.append(
                        f"{finding_id}: clearing stale position {old_pos} "
                        f"because {location} could not be resolved"
                    )
                    finding.pop("position", None)
                continue
            if not overwrite and old_pos is not None and old_pos == resolved:
                continue
            if old_pos is not None and old_pos != resolved:
                messages.append(
                    f"{finding_id}: position {old_pos} -> {resolved} "
                    f"(from location {location})"
                )
            finding["position"] = resolved
            if path:
                finding["path"] = path
    return messages


def command_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Sync GitCode PR comment positions in review/result.json from pr.diff."
    )
    parser.add_argument(
        "--diff",
        default="pr.diff",
        help="Unified diff path (default: pr.diff in --out-dir)",
    )
    parser.add_argument(
        "--result",
        default="result.json",
        help="Review JSON path (default: result.json in --out-dir)",
    )
    parser.add_argument(
        "--out-dir",
        required=True,
        help="Directory containing pr.diff and result.json",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print changes without writing result.json",
    )
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir).resolve()
    diff_path = Path(args.diff)
    if not diff_path.is_absolute():
        diff_path = out_dir / diff_path
    result_path = Path(args.result)
    if not result_path.is_absolute():
        result_path = out_dir / result_path

    if not diff_path.is_file():
        print(f"error: diff not found: {diff_path}", file=__import__("sys").stderr)
        return 1
    if not result_path.is_file():
        print(f"error: result not found: {result_path}", file=__import__("sys").stderr)
        return 1

    diff_text = diff_path.read_text(encoding="utf-8", errors="replace")
    review = json.loads(result_path.read_text(encoding="utf-8-sig"))
    messages = sync_finding_positions(review, diff_text)
    for line in messages:
        print(line)
    if not messages:
        print("no position updates")
    if args.dry_run:
        return 0
    result_path.write_text(
        json.dumps(review, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


def _self_test() -> None:
    sample = """diff --git a/pkg/a.py b/pkg/a.py
--- a/pkg/a.py
+++ b/pkg/a.py
@@ -10,3 +10,4 @@
 context
-old
+new line
 unchanged
"""
    pos, path, _ = resolve_position_from_location(sample, "pkg/a.py:12")
    assert path == "pkg/a.py"
    assert pos == 12
    bad, _, _ = resolve_position_from_location(sample, "pkg/a.py:99")
    assert bad is None
    print("gitcode_diff_position self-test ok")


if __name__ == "__main__":
    import sys as _sys

    if "--self-test" in _sys.argv:
        _self_test()
        raise SystemExit(0)
    raise SystemExit(command_main())
