#!/usr/bin/env python3
# coding: utf-8
"""
从 upstream 已合入 PR 提取 bench 基准信息（修复提交、父提交、文件变更）。

本脚本对 GitCode 仅使用 upstream 的只读 GET（pulls / commits / files），
不创建分支、不创建 Issue。写操作须走 fork：bench_git.py（push origin）、
gitcode-repo issue_fetcher.py（--source fork）。

用法:
    python bench_from_pr.py --pr 1467 --config ../../gitcode-repo/gitcode-repo.json
    python bench_from_pr.py --pr 1467 --workspace agent-core --format json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any, Dict, List, Tuple

from bench_gitcode import (
    ConfigError,
    GitCodeClient,
    GitCodeClientError,
    exit_on_config_error,
    find_config_path,
)


def _pull_base_path(client: GitCodeClient, pr_number: int) -> str:
    return (
        f"/repos/{client.upstream_owner}"
        f"/{client.upstream_repo}/pulls/{pr_number}"
    )


def fetch_pull_request(
    client: GitCodeClient,
    pr_number: int,
) -> Dict[str, Any]:
    """获取 PR 详情（upstream）。"""
    return client._request("GET", _pull_base_path(client, pr_number))


def fetch_pull_commits(
    client: GitCodeClient,
    pr_number: int,
) -> List[Dict[str, Any]]:
    """获取 PR 提交列表。"""
    data = client._request(
        "GET",
        f"{_pull_base_path(client, pr_number)}/commits",
    )
    if isinstance(data, list):
        return data
    return []


def fetch_pull_files(
    client: GitCodeClient,
    pr_number: int,
) -> List[Dict[str, Any]]:
    """获取 PR 文件变更列表。"""
    data = client._request(
        "GET",
        f"{_pull_base_path(client, pr_number)}/files",
    )
    if isinstance(data, list):
        return data
    return []


def fetch_commit(
    client: GitCodeClient,
    sha: str,
) -> Dict[str, Any]:
    """获取单个 commit 详情（含 parents）。"""
    path = (
        f"/repos/{client.upstream_owner}"
        f"/{client.upstream_repo}/commits/{sha}"
    )
    return client._request("GET", path)


def _commit_sha(entry: Dict[str, Any]) -> str:
    if entry.get("sha"):
        return str(entry["sha"])
    commit = entry.get("commit") or {}
    return str(commit.get("sha") or "")


def pick_fix_and_parent(
    client: GitCodeClient,
    commits: List[Dict[str, Any]],
    fix_sha: str = "",
    parent_sha: str = "",
) -> Tuple[str, str, List[str]]:
    """推断修复提交与基准父提交。

    默认取 PR 提交列表中的最后一个为 fix；其父提交（Git parents[0]）为 bench 基点。
    可通过 fix_sha / parent_sha 覆盖。
    """
    warnings: List[str] = []
    shas = [_commit_sha(c) for c in commits if _commit_sha(c)]

    fix = (fix_sha or "").strip()
    if not fix:
        if not shas:
            raise ValueError("PR 无提交记录，请用 --fix-sha 手动指定")
        fix = shas[-1]
        if len(shas) > 1:
            warnings.append(
                f"PR 含 {len(shas)} 个提交，默认取最后一个为修复提交: {fix[:12]}…"
            )

    parent = (parent_sha or "").strip()
    if not parent:
        detail = fetch_commit(client, fix)
        parents = detail.get("parents") or []
        if not parents:
            if len(shas) >= 2:
                parent = shas[-2]
                warnings.append("commit 无 parents 字段，回退为 PR 提交列表中的前一个")
            else:
                raise ValueError(
                    "无法解析父提交，请用 --parent-sha 指定修复前的基准 SHA"
                )
        else:
            parent = str(parents[0].get("sha") or "")
        if not parent:
            raise ValueError("父提交 SHA 为空，请用 --parent-sha 指定")

    return fix, parent, warnings


def suggest_bench_branch_name(index: int = 0) -> str:
    if index < 1:
        index = 1
    return f"bench-issue-{index}"


def next_bench_index_from_branches(branch_names: List[str]) -> int:
    """根据已有 bench-issue-N 分支名推断下一个序号。"""
    pattern = re.compile(r"^bench-issue-(\d+)$")
    max_n = 0
    for name in branch_names:
        m = pattern.match(name.strip())
        if m:
            max_n = max(max_n, int(m.group(1)))
    return max_n + 1


def build_context(
    client: GitCodeClient,
    pr_number: int,
    fix_sha: str = "",
    parent_sha: str = "",
    bench_index: int = 0,
) -> Dict[str, Any]:
    """汇总 PR → bench 上下文。"""
    pr = fetch_pull_request(client, pr_number)
    commits = fetch_pull_commits(client, pr_number)
    files = fetch_pull_files(client, pr_number)
    fix, parent, warnings = pick_fix_and_parent(
        client, commits, fix_sha=fix_sha, parent_sha=parent_sha
    )

    index = bench_index if bench_index > 0 else 1
    branch = suggest_bench_branch_name(index)

    simplified_files = []
    for f in files:
        simplified_files.append({
            "filename": f.get("filename", ""),
            "status": f.get("status", ""),
            "additions": f.get("additions"),
            "deletions": f.get("deletions"),
            "patch": str(f.get("patch") or "")[:2000],
        })

    return {
        "pr_number": pr_number,
        "pr_title": pr.get("title", ""),
        "pr_state": pr.get("state", ""),
        "pr_html_url": pr.get("html_url", ""),
        "merged": pr.get("merged", pr.get("merge_commit_sha") is not None),
        "fix_sha": fix,
        "parent_sha": parent,
        "commit_count": len(commits),
        "files": simplified_files,
        "suggested_branch": branch,
        "bench_index": index,
        "warnings": warnings,
        "upstream": f"{client.upstream_owner}/{client.upstream_repo}",
        "fork": f"{client.fork_owner}/{client.fork_repo}",
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="从已合入 PR 提取 bench 基准提交与变更摘要",
    )
    parser.add_argument(
        "--pr",
        type=int,
        required=True,
        help="upstream PR 编号",
    )
    parser.add_argument(
        "--fix-sha",
        default="",
        help="覆盖：修复提交 SHA（默认 PR 最后一笔 commit）",
    )
    parser.add_argument(
        "--parent-sha",
        default="",
        help="覆盖：修复前基准 SHA（默认 fix 的 git parent）",
    )
    parser.add_argument(
        "--bench-index",
        type=int,
        default=0,
        help="bench-issue-N 中的 N；0 表示不写入建议分支名中的序号",
    )
    parser.add_argument(
        "--config",
        default="",
        help="gitcode-repo.json 路径",
    )
    parser.add_argument(
        "--workspace",
        default="",
        help="工作区名称（多工作区时必填）",
    )
    parser.add_argument(
        "--format",
        choices=("json", "text"),
        default="json",
        help="输出格式",
    )
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    config_path = find_config_path(args.config)
    if not config_path:
        print(
            json.dumps(
                {
                    "error": "未找到 gitcode-repo.json，"
                    "请用 --config 指定 gitcode-repo.json 路径",
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        client = GitCodeClient.from_config(
            config_path,
            workspace_name=args.workspace or None,
        )
        ctx = build_context(
            client,
            args.pr,
            fix_sha=args.fix_sha,
            parent_sha=args.parent_sha,
            bench_index=args.bench_index,
        )
    except ConfigError as exc:
        exit_on_config_error(exc)
    except (GitCodeClientError, ValueError) as exc:
        print(
            json.dumps({"error": str(exc)}, ensure_ascii=False),
            file=sys.stderr,
        )
        sys.exit(1)

    if args.format == "json":
        print(json.dumps(ctx, ensure_ascii=False, indent=2))
        return

    print(f"PR #{ctx['pr_number']}: {ctx['pr_title']}")
    print(f"  fix:    {ctx['fix_sha']}")
    print(f"  parent: {ctx['parent_sha']}  ← bench 分支基点")
    print(f"  branch: {ctx['suggested_branch']}")
    if ctx.get("warnings"):
        for w in ctx["warnings"]:
            print(f"  ⚠ {w}")


if __name__ == "__main__":
    main()
