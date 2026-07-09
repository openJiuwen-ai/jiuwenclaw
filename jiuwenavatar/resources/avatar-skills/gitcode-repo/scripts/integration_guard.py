#!/usr/bin/env python3
# coding: utf-8
"""合入前校验：特性分支不得夹带非本次 first-parent 线上的 commit / 超 scope 文件。

核心规则（通用，不绑定 bench）：
1. foreign：branch_base..head 中凡不在 first-parent 线上的 commit → 视为 merge/rebase 夹带外来历史
2. scope（可选）：integration_base...head 变更文件须落在 review scope（result.json 或 review.md）+ doc/<module>/ 内

用法:
    python integration_guard.py \\
        --repo-root D:/bench/agent-studio \\
        --head fix/my-branch \\
        --base develop \\
        --branch-base bench-issue-2 \\
        --module <module>
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

FRONTMATTER_RE = re.compile(
    r"\A---\s*\r?\n(.*?)\r?\n---\s*(?:\r?\n|$)", re.DOTALL
)

_GIT_TIMEOUT_SEC = 60


def git(repo: Path, *args: str) -> str:
    cmd = ["git", "-C", str(repo), *args]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_GIT_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"git {' '.join(args)} timed out after {_GIT_TIMEOUT_SEC}s"
        ) from None
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"git {' '.join(args)} failed: {err}")
    return proc.stdout.strip()


def resolve_ref(repo: Path, ref: str) -> str:
    return git(repo, "rev-parse", "--verify", ref)


def merge_commits_on_first_parent(
    repo: Path, branch_base: str, head: str
) -> list[dict[str, str]]:
    base_sha = resolve_ref(repo, branch_base)
    head_sha = resolve_ref(repo, head)
    out = git(
        repo,
        "log",
        "--first-parent",
        "--merges",
        "--format=%H %s",
        f"{base_sha}..{head_sha}",
    )
    rows: list[dict[str, str]] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        sha, _, subject = line.partition(" ")
        rows.append({"sha": sha[:12], "subject": subject})
    return rows


def foreign_commits(repo: Path, branch_base: str, head: str) -> list[dict[str, str]]:
    """Commits in branch_base..head not on the first-parent line (= merge 夹带)."""
    base_sha = resolve_ref(repo, branch_base)
    head_sha = resolve_ref(repo, head)
    all_out = git(repo, "rev-list", f"{base_sha}..{head_sha}")
    fp_out = git(repo, "rev-list", "--first-parent", f"{base_sha}..{head_sha}")
    fp_set = {line.strip() for line in fp_out.splitlines() if line.strip()}
    foreign_shas = [s for s in all_out.splitlines() if s.strip() and s.strip() not in fp_set]
    if not foreign_shas:
        return []
    log_out = git(
        repo,
        "log",
        "--no-walk",
        "--reverse",
        "--format=%H %s",
        *foreign_shas,
    )
    rows: list[dict[str, str]] = []
    for line in log_out.splitlines():
        line = line.strip()
        if not line:
            continue
        sha, _, subject = line.partition(" ")
        rows.append({"sha": sha[:12], "subject": subject})
    return rows


def diff_files(repo: Path, integration_base: str, head: str) -> list[str]:
    base_sha = resolve_ref(repo, integration_base)
    head_sha = resolve_ref(repo, head)
    out = git(repo, "diff", "--name-only", f"{base_sha}...{head_sha}")
    return [p for p in out.splitlines() if p.strip()]


AFFECTED_FILES_RE = re.compile(r"^- Affected files:\s*(.+)$", re.MULTILINE)


def scope_from_review_data(data: dict[str, Any]) -> set[str]:
    files: set[str] = set()
    if isinstance(data.get("affected_files"), list):
        files.update(str(x).replace("\\", "/") for x in data["affected_files"])
    summary = data.get("summary")
    if isinstance(summary, dict) and isinstance(summary.get("affected_files"), list):
        files.update(str(x).replace("\\", "/") for x in summary["affected_files"])
    return files


def scope_from_review_md_body(text: str) -> set[str]:
    match = AFFECTED_FILES_RE.search(text)
    if not match:
        return set()
    raw = match.group(1).strip()
    if not raw or raw.lower() in {"none recorded", "none collected"}:
        return set()
    return {part.strip().replace("\\", "/") for part in raw.split(",") if part.strip()}


def parse_review_scope(repo_root: Path, module: str) -> set[str]:
    mod = module.strip("/\\")
    result_path = repo_root / "doc" / mod / "review" / "result.json"
    if result_path.exists():
        try:
            data = json.loads(result_path.read_text(encoding="utf-8-sig", errors="replace"))
        except json.JSONDecodeError:
            data = {}
        if isinstance(data, dict):
            scope = scope_from_review_data(data)
            if scope:
                return scope

    review_path = repo_root / "doc" / mod / "review.md"
    if not review_path.exists():
        return set()
    text = review_path.read_text(encoding="utf-8-sig", errors="replace")
    match = FRONTMATTER_RE.match(text)
    if match:
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError:
            data = {}
        if isinstance(data, dict):
            scope = scope_from_review_data(data)
            if scope:
                return scope
    return scope_from_review_md_body(text)


def doc_module_prefix(module: str) -> str:
    return f"doc/{module.strip('/\\')}/"


def out_of_scope(diff_paths: list[str], scope: set[str], module: str) -> list[str]:
    if not scope and not module:
        return []
    doc_prefix = doc_module_prefix(module) if module else ""
    extras: list[str] = []
    scope_norm = {s.replace("\\", "/") for s in scope}
    for path in diff_paths:
        norm = path.replace("\\", "/")
        if doc_prefix and norm.startswith(doc_prefix):
            continue
        if norm in scope_norm:
            continue
        extras.append(norm)
    return sorted(extras)


def run_check(
    repo_root: Path,
    head: str,
    integration_base: str,
    branch_base: str | None = None,
    module: str = "",
    check_scope: bool = True,
) -> dict[str, Any]:
    branch_base = branch_base or integration_base
    result: dict[str, Any] = {
        "ok": True,
        "repo_root": str(repo_root),
        "head": head,
        "integration_base": integration_base,
        "branch_base": branch_base,
        "module": module or None,
        "errors": [],
        "warnings": [],
        "foreign_commits": [],
        "merge_commits": [],
        "diff_files": [],
        "out_of_scope_files": [],
        "scope_check": "disabled",
    }

    if not repo_root.is_dir():
        result["ok"] = False
        result["errors"].append(f"repo-root 不存在: {repo_root}")
        return result

    try:
        merges = merge_commits_on_first_parent(repo_root, branch_base, head)
        result["merge_commits"] = merges
        if merges:
            result["ok"] = False
            result["errors"].append(
                f"first-parent 线上有 {len(merges)} 个 merge commit（禁止 merge 夹带外来历史）"
            )

        foreign = foreign_commits(repo_root, branch_base, head)
        result["foreign_commits"] = foreign
        if foreign:
            result["ok"] = False
            result["errors"].append(
                f"夹带 {len(foreign)} 个非 first-parent commit（疑似 merge/rebase 外来历史）"
            )

        diff_paths = diff_files(repo_root, integration_base, head)
        result["diff_files"] = diff_paths

        if check_scope and module:
            result["scope_check"] = "enabled"
            scope = parse_review_scope(repo_root, module)
            extras = out_of_scope(diff_paths, scope, module)
            if not scope:
                result["ok"] = False
                result["errors"].append(
                    "review scope 缺少有效 affected_files（result.json 或 review.md Review Summary）"
                )
                # scope 缺失时不输出 out_of_scope_files，避免被误解为已通过 scope 校验。
                result["scope_check"] = "failed_missing_scope"
            else:
                result["out_of_scope_files"] = extras
                if extras:
                    result["ok"] = False
                    result["errors"].append(
                        f"相对 {integration_base} 有 {len(extras)} 个文件超出 review scope"
                    )
                    result["scope_check"] = "failed_out_of_scope"
                else:
                    result["scope_check"] = "passed"
        elif check_scope and not module:
            result["scope_check"] = "skipped_missing_module"
            result["warnings"].append(
                "未传 --module，已跳过 scope 文件校验"
            )

        base_sha = resolve_ref(repo_root, integration_base)
        bb_sha = resolve_ref(repo_root, branch_base)
        if bb_sha != base_sha:
            n_branch = int(git(repo_root, "rev-list", "--count", f"{bb_sha}..{resolve_ref(repo_root, head)}"))
            n_base = int(git(repo_root, "rev-list", "--count", f"{base_sha}..{resolve_ref(repo_root, head)}"))
            if n_branch > n_base + 2:
                result["warnings"].append(
                    f"branch_base..head ({n_branch} commits) 远大于 integration_base..head ({n_base})；"
                    "若 PR base 用 branch_base 会导致 diff 膨胀"
                )
    except RuntimeError as exc:
        result["ok"] = False
        result["errors"].append(str(exc))

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="合入前校验：禁止夹带非本次 commit")
    parser.add_argument("--repo-root", required=True, help="业务仓根目录")
    parser.add_argument("--head", required=True, help="特性分支 ref")
    parser.add_argument(
        "--base",
        required=True,
        help="PR 合入目标分支（integration_base，如 develop）",
    )
    parser.add_argument(
        "--branch-base",
        default="",
        help="G7a 分叉点；默认与 --base 相同。bench 场景传 bench-issue-N",
    )
    parser.add_argument(
        "--module",
        default="",
        help="模块名；读取 doc/<module>/review/result.json 或 review.md 的 affected_files 做 scope 校验",
    )
    parser.add_argument(
        "--no-scope",
        action="store_true",
        help="跳过文件 scope 校验（仍检查 foreign commits）",
    )
    args = parser.parse_args()

    result = run_check(
        repo_root=Path(args.repo_root).resolve(),
        head=args.head,
        integration_base=args.base,
        branch_base=args.branch_base or None,
        module=args.module,
        check_scope=not args.no_scope,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
