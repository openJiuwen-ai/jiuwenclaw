#!/usr/bin/env python3
# coding: utf-8
"""Pull Request / MR 获取、查询与创建 CLI。

通过 GitCode API v5（api.gitcode.com，/repos/{owner}/{repo}/pulls）
获取、列出、创建 PR/MR。勿使用 gitcode.com 上的 GitLab 风格
/projects/.../merge_requests 路径。

用法:
    # 获取单个 PR（含评论与标签，默认 fork 仓）
    python pr_creator.py --number 42 --config gitcode-repo.json

    # 连通性：列出 open PR（默认 fork 仓）
    python pr_creator.py --list --per-page 1 --config gitcode-repo.json

    # 按 head 过滤（同仓用分支名；跨仓用 fork_owner:branch）
    python pr_creator.py --list --head feature/my-branch

    # 按关键词搜索
    python pr_creator.py --list --search "web-config"

    # 在 fork 内创建 MR（feature -> develop）
    python pr_creator.py --create \\
        --title "feat: example" \\
        --head feature/my-branch \\
        --base develop \\
        --body-file pr-body.md \\
        --config gitcode-repo.json

    # 向 upstream 创建跨仓 MR（head 可只写分支名，会自动加 fork_owner: 前缀）
    python pr_creator.py --create \\
        --title "feat: example" \\
        --head feature/my-branch \\
        --base develop \\
        --target-project upstream \\
        --config gitcode-repo.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from config_loader import ConfigError, find_config_path, load_resolved_config
from gitcode_client import GitCodeClient, GitCodeClientError
from integration_guard import run_check


def _branch_label(ref: Any) -> str:
    if isinstance(ref, dict):
        return str(ref.get("ref") or ref.get("label") or "")
    return str(ref or "")


def _extract_label_names(pr: Dict[str, Any]) -> List[str]:
    labels: List[str] = []
    for label in pr.get("labels", []) or []:
        if isinstance(label, dict):
            labels.append(str(label.get("name", "")))
        else:
            labels.append(str(label))
    return [name for name in labels if name]


def _format_pr(pr: Dict[str, Any]) -> Dict[str, Any]:
    """提取 PR 关键字段。"""
    user = pr.get("user") or {}
    return {
        "number": pr.get("number"),
        "title": pr.get("title", ""),
        "state": pr.get("state", ""),
        "labels": _extract_label_names(pr),
        "head": _branch_label(pr.get("head")),
        "base": _branch_label(pr.get("base")),
        "body": pr.get("body", ""),
        "html_url": pr.get("html_url", ""),
        "created_at": pr.get("created_at", ""),
        "updated_at": pr.get("updated_at", ""),
        "user": user.get("login", "") if isinstance(user, dict) else "",
        "merged": pr.get("merged", False),
        "unresolved_discussions_count": pr.get(
            "unresolved_discussions_count"
        ),
    }


def _format_pr_comment(comment: Dict[str, Any]) -> Dict[str, Any]:
    user = comment.get("user") or {}
    formatted: Dict[str, Any] = {
        "id": comment.get("id"),
        # discussion_id 是哈希串，修改检视意见解决状态（--resolve/--reopen）
        # 必须用它，而不是数字 id，否则 GitCode 报 "discussion not found"。
        "discussion_id": comment.get("discussion_id"),
        "author": user.get("login", "") if isinstance(user, dict) else "",
        "body": comment.get("body", ""),
        "created_at": comment.get("created_at", ""),
    }
    if comment.get("resolved") is not None:
        formatted["resolved"] = comment.get("resolved")
    if comment.get("path"):
        formatted["path"] = comment.get("path")
    if comment.get("position") is not None:
        formatted["position"] = comment.get("position")
    if comment.get("position_type"):
        formatted["position_type"] = comment.get("position_type")
    return formatted


def fetch_pull_request(
    client: GitCodeClient,
    number: int,
    target_project: str = "fork",
) -> Dict[str, Any]:
    """获取单个 PR 详情（含评论与标签）。"""
    pr = client.get_pull_request(number, target_project=target_project)
    result = _format_pr(pr)
    try:
        label_rows = client.get_pull_labels(
            number, target_project=target_project
        )
        if label_rows:
            names: List[str] = []
            for row in label_rows:
                if isinstance(row, dict):
                    names.append(str(row.get("name", "")))
                else:
                    names.append(str(row))
            result["labels"] = [n for n in names if n]
    except GitCodeClientError as exc:
        result["warnings"] = [
            f"单独拉取标签失败: {exc}",
        ]
    comments_raw = client.get_all_pull_request_comments(
        number, target_project=target_project
    )
    result["comments"] = [
        _format_pr_comment(c) for c in comments_raw
    ]
    return result


def normalize_head(
    client: GitCodeClient,
    head: str,
    target_project: str = "fork",
) -> str:
    """规范化 PR head 参数。

    - 已含 ``owner:branch`` 时原样返回。
    - ``target_project=upstream`` 时补全为 ``fork_owner:branch``（跨仓 MR）。
    - ``target_project=fork`` 时保留分支名（同仓 MR，符合 GitCode 文档）。
    """
    head_clean = (head or "").strip()
    if not head_clean:
        raise ValueError("PR head 不能为空")
    if ":" in head_clean:
        return head_clean
    target = (target_project or "fork").strip().lower()
    if target == "upstream":
        fork_owner = (client.fork_owner or "").strip()
        if (
            not fork_owner
            or (
                getattr(client, "has_explicit_fork_owner", False)
                is False
            )
        ):
            raise ValueError(
                "向 upstream 创建跨仓 MR 需要配置 fork.owner，"
                "或显式传入 --head fork_owner:branch_name"
            )
        return f"{fork_owner}:{head_clean}"
    return head_clean


def _filter_pulls_by_search(
    pulls: List[Dict[str, Any]],
    search: str,
) -> List[Dict[str, Any]]:
    needle = (search or "").strip().lower()
    if not needle:
        return pulls
    filtered: List[Dict[str, Any]] = []
    for pr in pulls:
        haystack = (
            f"{pr.get('title', '')} {pr.get('body', '')}"
        ).lower()
        if needle in haystack:
            filtered.append(pr)
    return filtered


def _filter_pulls_by_author(
    pulls: List[Dict[str, Any]],
    author: str,
) -> List[Dict[str, Any]]:
    """按作者 login 精确过滤 PR（大小写不敏感）。

    用于「只检视某人提交的 PR」场景，避免误把他人（含本账号自己 fork
    提交的跨仓 MR）的 PR 计入。``_format_pr`` 已将作者归一到 ``user``
    字段（GitCode 的 ``user.login``）。
    """
    target = (author or "").strip().lower()
    if not target:
        return pulls
    return [
        pr
        for pr in pulls
        if str(pr.get("user", "")).strip().lower() == target
    ]


def list_pull_requests(
    client: GitCodeClient,
    state: str = "open",
    head: str = "",
    base: str = "",
    search: str = "",
    page: int = 1,
    per_page: int = 20,
    target_project: str = "fork",
    author: str = "",
) -> List[Dict[str, Any]]:
    """列出 PR/MR。

    ``author`` 非空时，仅返回作者 login 与之精确匹配（大小写不敏感）的 PR，
    用于「只检视某人提交的 PR」场景。
    """
    head_filter = head.strip()
    if head_filter and ":" not in head_filter:
        target = (target_project or "fork").strip().lower()
        if target == "upstream":
            head_filter = normalize_head(client, head_filter, "upstream")

    search_clean = (search or "").strip()
    list_kwargs = dict(
        state=state,
        head=head_filter,
        base=base,
        page=page,
        per_page=per_page,
        target_project=target_project,
    )

    def _list_without_search() -> List[Dict[str, Any]]:
        pulls = client.list_pull_requests(**list_kwargs, search="")
        return [_format_pr(p) for p in pulls]

    try:
        pulls = client.list_pull_requests(
            **list_kwargs, search=search_clean
        )
        formatted = [_format_pr(p) for p in pulls]
    except GitCodeClientError:
        if not search_clean:
            raise
        formatted = _filter_pulls_by_search(
            _list_without_search(), search_clean
        )
        return _filter_pulls_by_author(formatted, author)

    if search_clean and not formatted:
        formatted = _filter_pulls_by_search(
            _list_without_search(), search_clean
        )
    return _filter_pulls_by_author(formatted, author)


def create_pull_request(
    client: GitCodeClient,
    title: str,
    head: str,
    base: str = "",
    body: str = "",
    target_project: str = "fork",
    check_open_duplicate: bool = True,
    issue: Optional[int] = None,
) -> Dict[str, Any]:
    """创建 PR/MR。"""
    head_norm = normalize_head(client, head, target_project)
    head_sent, fork_path_sent = client.resolve_pull_request_head(
        head_norm, target_project
    )
    result = client.create_pull_request(
        title=title,
        head=head_norm,
        base=base,
        body=body,
        check_open_duplicate=check_open_duplicate,
        target_project=target_project,
        issue=issue,
        resolved_head=head_sent,
        resolved_fork_path=fork_path_sent,
    )
    formatted = _format_pr(result)
    formatted["head_sent"] = head_sent
    formatted["target_project"] = (target_project or "fork").strip().lower()
    owner, repo = client.pull_request_repo(target_project)
    formatted["repo"] = f"{owner}/{repo}"
    if fork_path_sent:
        formatted["fork_path_sent"] = fork_path_sent
    if issue is not None:
        formatted["issue"] = issue
    return formatted


def _resolve_repo_root(args: argparse.Namespace, config_path: str) -> tuple[str, str]:
    explicit = (args.repo_root or "").strip()
    if explicit:
        return explicit, ""
    if not config_path:
        return "", ""
    try:
        cfg = load_resolved_config(config_path, workspace_name=args.workspace or None)
    except ConfigError as exc:
        return "", str(exc)
    except Exception as exc:
        return "", f"解析配置失败: {exc}"
    local = cfg.get("local_repo") or {}
    return str(local.get("path") or "").strip(), ""


def _run_integration_guard(args: argparse.Namespace, client: GitCodeClient) -> None:
    if args.skip_integration_check or not args.create_mode:
        return
    module = (args.module or "").strip()
    if not module:
        print(
            json.dumps(
                {
                    "error": "合入校验需要 --module（Aidlc G7b 必填）",
                },
                ensure_ascii=False,
            )
        )
        sys.exit(1)
    config_path = find_config_path(args.config) or ""
    repo_root, resolve_error = _resolve_repo_root(args, config_path)
    if not repo_root or not os.path.isdir(repo_root):
        detail = (
            f"（配置解析错误: {resolve_error}）" if resolve_error else ""
        )
        print(
            json.dumps(
                {
                    "error": (
                        "合入校验需要 --repo-root 或 gitcode-repo.json local_repo.path"
                        f"{detail}"
                    ),
                },
                ensure_ascii=False,
            )
        )
        sys.exit(1)
    base = (args.base or client.base_branch or "").strip()
    if not base:
        print(json.dumps({"error": "合入校验需要 --base 或配置 upstream.base_branch"}, ensure_ascii=False))
        sys.exit(1)
    result = run_check(
        repo_root=Path(repo_root).resolve(),
        head=args.head,
        integration_base=base,
        branch_base=(args.branch_base or "").strip() or None,
        module=module,
        check_scope=not args.no_scope_check,
    )
    if not result["ok"]:
        print(json.dumps({"integration_check": result}, ensure_ascii=False, indent=2))
        sys.exit(1)


def _read_body(body: str, body_file: str) -> str:
    if body_file:
        if not os.path.exists(body_file):
            raise FileNotFoundError(f"正文文件不存在: {body_file}")
        with open(body_file, encoding="utf-8") as f:
            return f.read()
    return body or ""


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="GitCode Pull Request / MR 工具",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--number",
        type=int,
        dest="number",
        help="获取指定编号的 PR/MR（含评论与标签）",
    )
    mode.add_argument(
        "--list",
        action="store_true",
        dest="list_mode",
        help="列出 PR/MR",
    )
    mode.add_argument(
        "--create",
        action="store_true",
        dest="create_mode",
        help="创建 PR/MR",
    )
    parser.add_argument(
        "--title",
        default="",
        help="PR 标题（创建时必填）",
    )
    parser.add_argument(
        "--head",
        default="",
        help="源分支；同仓可只写分支名，跨仓到 upstream 可只写分支名（自动加 fork_owner:）",
    )
    parser.add_argument(
        "--base",
        default="",
        help="目标分支（默认取配置 upstream.base_branch）",
    )
    parser.add_argument(
        "--body",
        default="",
        help="PR 描述（Markdown）",
    )
    parser.add_argument(
        "--body-file",
        default="",
        help="从文件读取 PR 描述（推荐）",
    )
    parser.add_argument(
        "--state",
        default="open",
        choices=["open", "closed", "all"],
        help="PR 状态过滤（列表，默认 open）",
    )
    parser.add_argument(
        "--search",
        default="",
        help="列表关键词搜索",
    )
    parser.add_argument(
        "--author",
        default="",
        help=(
            "仅列出指定作者 login 提交的 PR（大小写不敏感，--list 生效）；"
            "用于「只检视某人提交的 PR」，避免误检他人或本账号自己的 PR"
        ),
    )
    parser.add_argument(
        "--target-project",
        default="fork",
        choices=["fork", "upstream"],
        help="PR 所在仓库：fork（默认）或 upstream",
    )
    parser.add_argument(
        "--issue",
        type=int,
        default=None,
        help="关联的 Issue 编号（可选）",
    )
    parser.add_argument(
        "--no-duplicate-check",
        action="store_true",
        help="创建前不检查是否已有相同 head 的 open PR",
    )
    parser.add_argument(
        "--page",
        type=int,
        default=1,
        help="页码（默认 1）",
    )
    parser.add_argument(
        "--per-page",
        type=int,
        default=20,
        help="每页数量（默认 20）",
    )
    parser.add_argument(
        "--config",
        default="",
        help="配置文件路径",
    )
    parser.add_argument(
        "--workspace",
        default="",
        help="工作区名称（workspaces[].name；多条时必填）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅打印将要执行的操作，不调用写 API",
    )
    parser.add_argument(
        "--repo-root",
        default="",
        help="业务仓路径（合入校验；默认取 gitcode-repo.json local_repo.path）",
    )
    parser.add_argument(
        "--module",
        default="",
        help="Aidlc 模块名（合入 scope 校验，读 doc/<module>/review.md）",
    )
    parser.add_argument(
        "--branch-base",
        default="",
        help="G7a 分叉点 ref（foreign commit 校验；默认与 --base 相同）",
    )
    parser.add_argument(
        "--skip-integration-check",
        action="store_true",
        help="跳过合入校验（integration_guard）",
    )
    parser.add_argument(
        "--no-scope-check",
        action="store_true",
        help="合入校验时不比对 review scope 文件",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    config_path = find_config_path(args.config)

    try:
        client = GitCodeClient.from_config(
            config_path or None,
            workspace_name=args.workspace or None,
            dry_run=args.dry_run,
        )
    except Exception as exc:
        print(
            json.dumps(
                {"error": f"初始化失败: {exc}"},
                ensure_ascii=False,
            )
        )
        sys.exit(1)

    try:
        if args.number is not None:
            if args.number <= 0:
                parser.error("--number 须为正整数")
            result = fetch_pull_request(
                client,
                args.number,
                target_project=args.target_project,
            )
        elif args.list_mode:
            result = list_pull_requests(
                client,
                state=args.state,
                head=args.head,
                base=args.base,
                search=args.search,
                page=args.page,
                per_page=args.per_page,
                target_project=args.target_project,
                author=args.author,
            )
        else:
            if not args.title:
                parser.error("创建 PR 时必须指定 --title")
            if not args.head:
                parser.error(
                    "创建 PR 时必须指定 --head（源分支名）；"
                    "可用 git -C <local_repo.path> branch --show-current 查看当前分支"
                )
            if args.issue is not None and args.issue <= 0:
                parser.error("--issue 须为正整数")
            _run_integration_guard(args, client)
            body = _read_body(args.body, args.body_file)
            result = create_pull_request(
                client,
                title=args.title,
                head=args.head,
                base=args.base,
                body=body,
                target_project=args.target_project,
                check_open_duplicate=not args.no_duplicate_check,
                issue=args.issue,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except GitCodeClientError as exc:
        payload: Dict[str, Any] = {
            "error": str(exc),
            "status_code": exc.status_code,
        }
        if exc.response_body:
            payload["response_body"] = exc.response_body[:2000]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        sys.exit(1)
    except (ValueError, FileNotFoundError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        sys.exit(1)


if __name__ == "__main__":
    main()
