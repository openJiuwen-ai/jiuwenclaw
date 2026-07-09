#!/usr/bin/env python3
"""Lightweight mechanism coverage checker for doc/<module>/ requirements and tests."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

MODULE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
MECHANISM_KEYWORDS: dict[str, tuple[str, ...]] = {
    "timeout_cancel": ("超时", "取消", "timeout", "cancel"),
    "large_output": ("超大输出", "大输出", "persisted-output", "truncate", "preview"),
    "stream_merge": ("stdout", "stderr", "双流", "时序", "merge"),
    "process_tree": ("进程树", "killpg", "子进程", "孤儿进程"),
    "noninteractive": ("sudo", "非交互", "交互式", "password prompt"),
}
TEST_FILE_EXTS = {".py", ".ts", ".tsx", ".js", ".jsx"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check mechanism coverage by matching requirements and test content keywords."
    )
    parser.add_argument("--module", required=True, help="Module name under doc/")
    parser.add_argument("--repo-root", required=True, help="Repository root path")
    parser.add_argument(
        "--tests-root",
        default="",
        help="Optional tests directory relative to repo-root; defaults to <repo-root>/tests if exists",
    )
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
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="ignore")


def collect_test_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in TEST_FILE_EXTS:
            continue
        name = path.name.lower()
        if "test" not in name and "spec" not in name:
            continue
        files.append(path)
    return files


def contains_any(text: str, words: tuple[str, ...]) -> bool:
    lower = text.lower()
    return any(word.lower() in lower for word in words)


def main() -> None:
    args = parse_args()
    module = args.module.strip()
    validate_module_name(module)
    repo_root = resolve_repo_root(args.repo_root)

    requirements_path = repo_root / "doc" / module / "requirements.md"
    if not requirements_path.is_file():
        fail(f"未找到 requirements.md：{requirements_path.relative_to(repo_root)}")

    requirements_text = read_text(requirements_path)
    tests_root = (
        (repo_root / args.tests_root).resolve()
        if args.tests_root
        else (repo_root / "tests").resolve()
    )
    test_text = ""
    scanned_files: list[str] = []
    if tests_root.is_dir():
        test_files = collect_test_files(tests_root)
        scanned_files = [str(path.relative_to(repo_root)).replace("\\", "/") for path in test_files]
        test_text = "\n".join(read_text(path) for path in test_files)

    coverage: dict[str, dict[str, object]] = {}
    for key, words in MECHANISM_KEYWORDS.items():
        needed = contains_any(requirements_text, words)
        covered = contains_any(test_text, words) if needed else False
        coverage[key] = {
            "needed": needed,
            "covered": covered,
            "keywords": list(words),
        }

    needed_count = sum(1 for item in coverage.values() if item["needed"])
    covered_count = sum(1 for item in coverage.values() if item["needed"] and item["covered"])
    status = "PASS" if needed_count == 0 or covered_count == needed_count else "WARN"

    result = {
        "module": module,
        "requirements": str(requirements_path.relative_to(repo_root)).replace("\\", "/"),
        "tests_root": str(tests_root).replace("\\", "/"),
        "scanned_test_files": scanned_files,
        "mechanism_coverage": coverage,
        "summary": {
            "needed": needed_count,
            "covered": covered_count,
            "status": status,
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if status == "PASS":
        print("[OK] mechanism coverage check passed")
    else:
        print("[WARN] mechanism coverage has gaps")


if __name__ == "__main__":
    main()
