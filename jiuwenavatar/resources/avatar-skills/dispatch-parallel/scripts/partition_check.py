#!/usr/bin/env python3
"""Validate Aidlc G4/G5 parallel groups and shard manifests."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# Gate 脚本：与 dev-leader/references/timeouts.md 一致
_GIT_TIMEOUT_SEC = 60

MODULE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
CHECKBOX_PATTERN = re.compile(r"^\s*-\s+\[(?P<mark>[ xX])\]\s+(?P<text>.*)$")
MANIFEST_PHASE_PATTERN = re.compile(r"(?m)^phase:\s*(g4|g5)\s*$")
TASK_ID_PATTERN = re.compile(r"^(?P<id>(?:\d+(?:\.\d+)*|[A-Za-z]+-\d+))\.?\s+")
PG_PATTERN = re.compile(
    r"^-\s*(?P<id>PG-\d+):\s*items\s*\[(?P<items>[^\]]*)\](?P<rest>.*)$"
)
BACKTICK_PATH_PATTERN = re.compile(r"`([^`]+)`")
INLINE_LIST_PATTERN = re.compile(r"\[(?P<items>[^\]]*)\]")


@dataclass(frozen=True)
class ParallelGroup:
    group_id: str
    items: tuple[str, ...]
    touch: tuple[str, ...]


@dataclass(frozen=True)
class ManifestShard:
    shard_id: str
    items: tuple[str, ...]
    touch_allow: tuple[str, ...]


def fail(message: str) -> None:
    print(f"[ERROR] {message}", file=sys.stderr)
    raise SystemExit(2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate Aidlc G4/G5 parallel groups and integration scope."
    )
    parser.add_argument("--module", required=True, help="Module name under doc/.")
    parser.add_argument("--repo-root", required=True, help="Repository root.")
    parser.add_argument(
        "--phase",
        required=True,
        choices=["g4", "g5", "integrate"],
        help="g4/g5 validates plan PG-* blocks; integrate validates manifest and diff scope.",
    )
    parser.add_argument(
        "--manifest",
        help="Manifest path. Defaults to doc/<module>/dispatch/manifest.yaml.",
    )
    parser.add_argument(
        "--diff-file",
        help="Optional file containing changed paths, one per line. Tests can use this instead of git diff.",
    )
    parser.add_argument("--max-shards", type=int, default=3)
    return parser.parse_args()


def resolve_root(repo_root: str) -> Path:
    root = Path(repo_root).expanduser().resolve()
    if not root.is_dir():
        fail(f"--repo-root is not a directory: {root}")
    return root


def normalize_path(path: str) -> str:
    return path.strip().strip("\"'").replace("\\", "/").strip("/")


def split_items(raw: str) -> tuple[str, ...]:
    items: list[str] = []
    for item in raw.split(","):
        cleaned = item.strip().strip("\"'")
        if cleaned:
            items.append(cleaned)
    return tuple(items)


def read_text(path: Path, root: Path) -> str:
    if not path.is_file():
        fail(f"File not found: {path.relative_to(root)}")
    return path.read_text(encoding="utf-8")


def extract_h2_section(content: str, heading: str) -> str:
    match = re.search(rf"(?m)^{re.escape(heading)}\s*$", content)
    if match is None:
        return ""
    start = content.find("\n", match.start())
    if start == -1:
        return ""
    tail = content[start + 1 :]
    next_heading = re.search(r"(?m)^##\s+", tail)
    return tail[: next_heading.start()] if next_heading else tail


def plan_file_for_phase(doc_dir: Path, phase: str) -> Path:
    return doc_dir / ("dev_plan.md" if phase == "g4" else "test_plan.md")


def heading_for_phase(phase: str) -> str:
    return "## 可并行组（G4）" if phase == "g4" else "## 可并行组（G5）"


def task_heading_for_phase(phase: str) -> str:
    return "## 开发任务" if phase == "g4" else "## 测试任务"


def extract_task_ids(content: str, phase: str) -> set[str]:
    return set(item_states_in_plan(content, phase).keys())


def item_states_in_plan(content: str, phase: str) -> dict[str, str]:
    section = extract_h2_section(content, task_heading_for_phase(phase))
    states: dict[str, str] = {}
    for line in section.splitlines():
        match = CHECKBOX_PATTERN.match(line)
        if not match:
            continue
        task_match = TASK_ID_PATTERN.match(match.group("text").strip())
        if not task_match:
            continue
        states[task_match.group("id")] = "done" if match.group("mark").lower() == "x" else "todo"
    return states


def manifest_phase_from_text(text: str) -> str:
    match = MANIFEST_PHASE_PATTERN.search(text)
    if match is None:
        fail("manifest missing phase: g4 or g5")
    return match.group(1)


def parse_parallel_groups(content: str, phase: str) -> list[ParallelGroup]:
    section = extract_h2_section(content, heading_for_phase(phase))
    if not section.strip() or "无（serial）" in section or "无(serial)" in section:
        return []

    groups: list[ParallelGroup] = []
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = PG_PATTERN.match(stripped)
        if match is None:
            if stripped.startswith("- PG-"):
                fail(f"Invalid PG line: {stripped}")
            continue
        touch = tuple(normalize_path(path) for path in BACKTICK_PATH_PATTERN.findall(match.group("rest")))
        groups.append(
            ParallelGroup(
                group_id=match.group("id"),
                items=split_items(match.group("items")),
                touch=tuple(path for path in touch if path),
            )
        )
    return groups


def validate_groups(groups: list[ParallelGroup], task_ids: set[str], max_shards: int) -> None:
    if not groups:
        print("[OK] No parallel groups; serial dispatch.")
        return
    if len(groups) > max_shards:
        fail(f"Too many parallel groups: {len(groups)} > max_shards={max_shards}")

    seen_touch: dict[str, str] = {}
    for group in groups:
        if not group.items:
            fail(f"{group.group_id} has empty items.")
        if not group.touch:
            fail(f"{group.group_id} has empty touch paths.")
        missing = [item for item in group.items if item not in task_ids]
        if missing:
            fail(f"{group.group_id} references unknown plan items: {', '.join(missing)}")
        for path in group.touch:
            owner = seen_touch.get(path)
            if owner is not None:
                fail(f"touch path overlap: {path} appears in {owner} and {group.group_id}")
            seen_touch[path] = group.group_id

    print(f"[OK] Validated {len(groups)} parallel groups.")


def parse_inline_list_value(line: str) -> tuple[str, ...]:
    match = INLINE_LIST_PATTERN.search(line)
    if match is None:
        return ()
    return split_items(match.group("items"))


def parse_manifest(path: Path, root: Path) -> list[ManifestShard]:
    text = read_text(path, root)
    shards: list[ManifestShard] = []
    current_id: str | None = None
    current_items: tuple[str, ...] = ()
    current_touch: list[str] = []
    in_touch_allow = False

    def flush() -> None:
        nonlocal current_id, current_items, current_touch
        if current_id is None:
            return
        shards.append(
            ManifestShard(
                shard_id=current_id,
                items=current_items,
                touch_allow=tuple(current_touch),
            )
        )
        current_id = None
        current_items = ()
        current_touch = []

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- id:"):
            flush()
            current_id = stripped.split(":", 1)[1].strip().strip("\"'")
            in_touch_allow = False
            continue
        if current_id is None:
            continue
        if stripped.startswith("items:"):
            current_items = parse_inline_list_value(stripped)
            in_touch_allow = False
            continue
        if stripped.startswith("touch_allow:"):
            inline = parse_inline_list_value(stripped)
            if inline:
                current_touch.extend(normalize_path(item) for item in inline)
            in_touch_allow = True
            continue
        if in_touch_allow and stripped.startswith("- "):
            current_touch.append(normalize_path(stripped[2:]))
            continue
        if not raw_line.startswith(" "):
            in_touch_allow = False

    flush()
    if not shards:
        fail(f"No shards found in manifest: {path.relative_to(root)}")
    return shards


def validate_manifest_shards(shards: list[ManifestShard], max_shards: int) -> None:
    if len(shards) > max_shards:
        fail(f"Too many manifest shards: {len(shards)} > max_shards={max_shards}")
    seen_touch: dict[str, str] = {}
    for shard in shards:
        if not shard.items:
            fail(f"{shard.shard_id} has empty items.")
        if not shard.touch_allow:
            fail(f"{shard.shard_id} has empty touch_allow.")
        for path in shard.touch_allow:
            owner = seen_touch.get(path)
            if owner is not None:
                fail(f"touch_allow overlap: {path} appears in {owner} and {shard.shard_id}")
            seen_touch[path] = shard.shard_id


def changed_paths_from_git(root: Path) -> set[str]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "diff", "--name-only"],
            capture_output=True,
            text=True,
            check=False,
            timeout=_GIT_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        fail(f"git diff --name-only timed out after {_GIT_TIMEOUT_SEC}s")
    if proc.returncode != 0:
        fail(f"git diff --name-only failed: {proc.stderr.strip()}")
    return {normalize_path(line) for line in proc.stdout.splitlines() if line.strip()}


def changed_paths_from_file(path: Path, root: Path) -> set[str]:
    return {normalize_path(line) for line in read_text(path, root).splitlines() if line.strip()}


def is_allowed_doc_path(path: str, module: str) -> bool:
    prefix = f"doc/{module}/"
    return (
        path in {f"{prefix}dev_plan.md", f"{prefix}test_plan.md"}
        or path.startswith(f"{prefix}dispatch/")
    )


def validate_manifest_items_done(
    root: Path,
    module: str,
    manifest_path: Path,
    shards: list[ManifestShard],
) -> None:
    phase = manifest_phase_from_text(read_text(manifest_path, root))
    plan_path = plan_file_for_phase(root / "doc" / module, phase)
    states = item_states_in_plan(read_text(plan_path, root), phase)
    open_items: list[str] = []
    for shard in shards:
        for item in shard.items:
            if states.get(item) != "done":
                open_items.append(f"{shard.shard_id}:{item}")
    if open_items:
        fail("Manifest items still todo in plan: " + ", ".join(open_items))


def validate_integration(
    root: Path,
    module: str,
    manifest_path: Path,
    diff_file: str | None,
    max_shards: int,
) -> None:
    shards = parse_manifest(manifest_path, root)
    validate_manifest_shards(shards, max_shards)
    validate_manifest_items_done(root, module, manifest_path, shards)
    allowed = {path for shard in shards for path in shard.touch_allow}
    changed = (
        changed_paths_from_file(Path(diff_file).expanduser().resolve(), root)
        if diff_file
        else changed_paths_from_git(root)
    )
    outside = sorted(
        path for path in changed if path not in allowed and not is_allowed_doc_path(path, module)
    )
    if outside:
        fail("Changed paths outside manifest touch_allow: " + ", ".join(outside))
    print(f"[OK] Integrated {len(shards)} shards; changed paths are within scope.")


def main() -> None:
    args = parse_args()
    if not MODULE_NAME_PATTERN.fullmatch(args.module):
        fail("Invalid module name.")

    root = resolve_root(args.repo_root)
    doc_dir = root / "doc" / args.module
    if args.phase in {"g4", "g5"}:
        plan_path = plan_file_for_phase(doc_dir, args.phase)
        content = read_text(plan_path, root)
        groups = parse_parallel_groups(content, args.phase)
        validate_groups(groups, extract_task_ids(content, args.phase), args.max_shards)
        return

    manifest_path = (
        Path(args.manifest).expanduser().resolve()
        if args.manifest
        else doc_dir / "dispatch" / "manifest.yaml"
    )
    validate_integration(root, args.module, manifest_path, args.diff_file, args.max_shards)


if __name__ == "__main__":
    main()
