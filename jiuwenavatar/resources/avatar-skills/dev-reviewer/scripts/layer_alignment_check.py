#!/usr/bin/env python3
"""Heuristic layer-alignment checker for reviewer/leader/bench usage."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

_GIT_TIMEOUT_SEC = 60

MODULE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
INFRA_KEYWORDS = ("L2", "L3", "IO", "subprocess", "进程", "基础设施")
TOOL_HINTS = ("_tool.py", "tool.py", "/tool/", "\\tool\\")
PATH_TOKEN_PATTERN = re.compile(r"`([^`/\\]+(?:[/\\][^`/\\]+)+)`")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check layer alignment between docs and diff.")
    parser.add_argument("--module", required=True)
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--diff-base", default="")
    parser.add_argument("--diff-head", default="")
    return parser.parse_args()


def fail(message: str) -> None:
    print(f"[ERROR] {message}", file=sys.stderr)
    raise SystemExit(2)


def resolve_repo_root(repo_root: str) -> Path:
    root = Path(repo_root.strip()).expanduser().resolve()
    if not root.is_dir():
        fail(f"--repo-root 不是有效目录：{root}")
    return root


def validate_module_name(module_name: str) -> None:
    if not MODULE_NAME_PATTERN.fullmatch(module_name):
        fail("模块名只能包含英文字母、数字、下划线或短横线。")


def read_text(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def run_git_diff_files(repo_root: Path, base: str, head: str) -> list[str]:
    if base and head:
        cmd = ["git", "diff", "--name-only", f"{base}...{head}"]
    else:
        cmd = ["git", "diff", "--name-only"]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_GIT_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        fail(f"git diff --name-only timed out after {_GIT_TIMEOUT_SEC}s")
    if proc.returncode != 0:
        return []
    return [line.strip().replace("\\", "/") for line in proc.stdout.splitlines() if line.strip()]


def extract_paths(text: str) -> set[str]:
    return {m.group(1).replace("\\", "/") for m in PATH_TOKEN_PATTERN.finditer(text)}


def contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(k.lower() in lowered for k in keywords)


def main() -> None:
    args = parse_args()
    module = args.module.strip()
    validate_module_name(module)
    repo_root = resolve_repo_root(args.repo_root)

    doc_dir = repo_root / "doc" / module
    requirements = read_text(doc_dir / "requirements.md")
    design = read_text(doc_dir / "design.md")
    dev_plan = read_text(doc_dir / "dev_plan.md")
    diff_files = run_git_diff_files(repo_root, args.diff_base, args.diff_head)

    evidence: dict[str, object] = {
        "requirements_has_infra_markers": contains_any(requirements, INFRA_KEYWORDS),
        "design_file_list": sorted(extract_paths(design)),
        "dev_plan_file_list": sorted(extract_paths(dev_plan)),
        "diff_files": diff_files,
    }

    warnings: list[str] = []
    layer_alignment = "PASS"
    patch_risk = "none"

    req_infra = bool(evidence["requirements_has_infra_markers"])
    design_paths = set(evidence["design_file_list"])  # type: ignore[arg-type]
    diff_path_set = set(diff_files)
    diff_is_tool_only = bool(diff_path_set) and all(
        contains_any(path, TOOL_HINTS) for path in diff_path_set
    )

    if req_infra and diff_is_tool_only:
        layer_alignment = "FAIL"
        patch_risk = "confirmed"
        warnings.append("requirements 指向基础设施层，但当前 diff 仅体现 Tool 层改动。")
    elif req_infra and design_paths and not any(path in diff_path_set for path in design_paths):
        layer_alignment = "FAIL"
        patch_risk = "suspected"
        warnings.append("design 文件清单与 diff 未对齐，疑似层级错位。")
    elif diff_is_tool_only and req_infra:
        patch_risk = "suspected"
        warnings.append("检测到 Tool 层集中改动，请确认是否掩盖机制问题。")

    result = {
        "module": module,
        "layer_alignment": layer_alignment,
        "patch_risk": patch_risk,
        "warnings": warnings,
        "evidence": evidence,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if layer_alignment == "PASS":
        print("[OK] layer alignment check passed")
    else:
        print("[WARN] layer alignment check failed")


if __name__ == "__main__":
    main()
