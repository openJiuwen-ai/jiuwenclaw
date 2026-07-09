#!/usr/bin/env python3
# coding: utf-8
"""
bench 分支本地 Git 操作（upstream 只读 / fork 可写）。

- upstream：仅 ensure remote + fetch，拉取 parent_sha 等对象。
- fork（默认 origin）：建 bench-issue-N 并 push；禁止向 upstream remote 推送。

用法:
    python bench_git.py --repo-path D:/path/to/repo --parent-sha <sha> \\
        --branch bench-issue-1 --upstream-url https://gitcode.com/org/repo.git \\
        --push-remote origin --push
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple


class GitCommandError(RuntimeError):
    pass


_GIT_TIMEOUT_SEC = 60
_GIT_FETCH_TIMEOUT_SEC = 300


def _run(
    repo_path: str,
    args: List[str],
    *,
    check: bool = True,
) -> subprocess.CompletedProcess:
    cmd = ["git", "-C", repo_path] + args
    timeout = _GIT_FETCH_TIMEOUT_SEC if args and args[0] == "fetch" else _GIT_TIMEOUT_SEC
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise GitCommandError(
            f"git {' '.join(args)} timed out after {timeout}s"
        ) from None
    if check and proc.returncode != 0:
        raise GitCommandError(
            f"git {' '.join(args)} failed ({proc.returncode}):\n"
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )
    return proc


def ensure_upstream_remote(
    repo_path: str,
    upstream_url: str,
    remote_name: str = "upstream",
) -> List[str]:
    """确保 upstream remote 存在；返回执行过的操作说明。"""
    notes: List[str] = []
    remotes = _run(repo_path, ["remote"]).stdout.splitlines()
    if remote_name not in remotes:
        _run(
            repo_path,
            ["remote", "add", remote_name, upstream_url],
        )
        notes.append(f"added remote {remote_name}")
    else:
        current = _run(
            repo_path,
            ["remote", "get-url", remote_name],
        ).stdout.strip()
        if current != upstream_url:
            notes.append(
                f"remote {remote_name} exists as {current!r}, "
                f"expected {upstream_url!r} — not changed"
            )
    return notes


def fetch_upstream(repo_path: str, remote_name: str = "upstream") -> None:
    _run(repo_path, ["fetch", remote_name])


def create_bench_branch(
    repo_path: str,
    branch: str,
    parent_sha: str,
    *,
    checkout: bool = True,
) -> None:
    _run(repo_path, ["branch", branch, parent_sha])
    if checkout:
        _run(repo_path, ["checkout", branch])


FORBIDDEN_PUSH_REMOTES = frozenset({"upstream"})


def assert_push_targets_fork(
    push_remote: str,
    upstream_remote: str = "upstream",
) -> None:
    """bench 分支只能推到 fork；禁止误推 upstream。"""
    name = (push_remote or "").strip()
    up = (upstream_remote or "").strip()
    if not name:
        raise GitCommandError("push remote 不能为空")
    if name.lower() in FORBIDDEN_PUSH_REMOTES or name == up:
        raise GitCommandError(
            f"禁止向远程 {name!r} 推送 bench 分支；"
            f"请使用 fork 的 remote（通常为 origin，对应 gitcode-repo.json 的 fork.remote_name）"
        )


def push_branch(
    repo_path: str,
    branch: str,
    remote: str = "origin",
    *,
    set_upstream: bool = True,
    upstream_remote: str = "upstream",
) -> None:
    assert_push_targets_fork(remote, upstream_remote=upstream_remote)
    args = ["push"]
    if set_upstream:
        args.extend(["-u", remote, branch])
    else:
        args.extend([remote, branch])
    _run(repo_path, args)


def verify_clean_worktree(repo_path: str) -> Tuple[bool, str]:
    status = _run(repo_path, ["status", "--porcelain"]).stdout.strip()
    if status:
        return False, status
    return True, ""


def verify_file_contains(
    repo_path: str,
    rel_path: str,
    pattern: str,
) -> Tuple[bool, str]:
    """检查工作区文件中是否包含 pattern（正则）。"""
    full = os.path.join(repo_path, rel_path.replace("/", os.sep))
    if not os.path.isfile(full):
        return False, f"file not found: {rel_path}"
    with open(full, encoding="utf-8", errors="replace") as f:
        content = f.read()
    if re.search(pattern, content, re.MULTILINE):
        return True, "pattern matched"
    return False, "pattern not found in file"


def list_local_branches(repo_path: str) -> List[str]:
    out = _run(repo_path, ["branch", "--format=%(refname:short)"]).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="bench 分支 Git 辅助")
    p.add_argument("--repo-path", required=True, help="本地仓库绝对路径")
    p.add_argument("--parent-sha", required=True, help="bench 基点 commit")
    p.add_argument("--branch", required=True, help="分支名，如 bench-issue-1")
    p.add_argument(
        "--upstream-url",
        default="",
        help="upstream 远程 URL；提供则 ensure remote 并 fetch",
    )
    p.add_argument(
        "--upstream-remote",
        default="upstream",
        help="upstream remote 名称",
    )
    p.add_argument(
        "--push-remote",
        "--fork-remote",
        default="origin",
        dest="push_remote",
        help="推送目标 remote（fork，须与 gitcode-repo.json 的 fork.remote_name 一致）",
    )
    p.add_argument(
        "--push",
        action="store_true",
        help="创建后推送到 push-remote",
    )
    p.add_argument(
        "--verify-file",
        default="",
        help="相对路径：验证 bug 代码仍存在",
    )
    p.add_argument(
        "--verify-pattern",
        default="",
        help="与 --verify-file 联用的正则",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印将执行的步骤",
    )
    return p


def main() -> None:
    args = _build_parser().parse_args()
    repo = os.path.abspath(args.repo_path)
    if not os.path.isdir(os.path.join(repo, ".git")) and not os.path.isfile(
        os.path.join(repo, ".git")
    ):
        print(
            json.dumps({"error": f"not a git repo: {repo}"}, ensure_ascii=False),
            file=sys.stderr,
        )
        sys.exit(1)

    plan: Dict[str, Any] = {
        "repo_path": repo,
        "branch": args.branch,
        "parent_sha": args.parent_sha,
        "steps": [],
    }

    if args.dry_run:
        plan["steps"] = [
            "fetch upstream (if url)",
            f"branch {args.branch} @ {args.parent_sha}",
            "checkout",
            "push" if args.push else "skip push",
        ]
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return

    try:
        if args.upstream_url:
            notes = ensure_upstream_remote(
                repo,
                args.upstream_url,
                remote_name=args.upstream_remote,
            )
            plan["upstream_notes"] = notes
            fetch_upstream(repo, remote_name=args.upstream_remote)
            plan["steps"].append("fetched upstream")

        create_bench_branch(
            repo,
            args.branch,
            args.parent_sha,
            checkout=True,
        )
        plan["steps"].append(f"created and checked out {args.branch}")

        clean, dirty = verify_clean_worktree(repo)
        plan["worktree_clean"] = clean
        if not clean:
            plan["dirty_files"] = dirty

        if args.push:
            push_branch(
                repo,
                args.branch,
                remote=args.push_remote,
                upstream_remote=args.upstream_remote,
            )
            plan["steps"].append(f"pushed to fork remote {args.push_remote}")

        if args.verify_file and args.verify_pattern:
            ok, msg = verify_file_contains(
                repo,
                args.verify_file,
                args.verify_pattern,
            )
            plan["verify"] = {"ok": ok, "message": msg}

        head = _run(repo, ["log", "--oneline", "-1"]).stdout.strip()
        plan["head"] = head
        print(json.dumps(plan, ensure_ascii=False, indent=2))
    except GitCommandError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
