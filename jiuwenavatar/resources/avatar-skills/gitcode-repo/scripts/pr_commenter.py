#!/usr/bin/env python3
# coding: utf-8
"""
Pull Request 评论与标签 CLI 工具。

在 GitCode fork/upstream 仓库的 PR/MR 中发表评论、添加标签。

用法:
    # 发表 PR 评论（讨论区）
    python pr_commenter.py --number 42 \
        --comment "## 审查结论\n\nLGTM"

    # 从文件读取评论
    python pr_commenter.py --number 42 \
        --comment-file pr-comment.md

    # 代码行评（可选 path / position）
    python pr_commenter.py --number 42 \
        --comment "此处需判空" \
        --path src/foo.py --position 12

    # 添加标签
    python pr_commenter.py --number 42 \
        --add-labels "needs-review"

    # 行评并标记为「需解决」（待闭环的检视意见）
    python pr_commenter.py --number 42 \
        --comment "[Must Fix][Code] 此处需判空" \
        --path src/foo.py --position 12 --need-to-resolve

    # 复检后标记某条检视意见已解决（闭环）；discussion_id 即评论 id
    python pr_commenter.py --number 42 \
        --resolve 97219c08d421e55cfa841deca16a30f5d7269e10 \
        --target-project upstream --config gitcode-repo.json

    # 检视意见全部闭环后，在评论区发 /approve 与 /lgtm
    python pr_commenter.py --number 42 \
        --approve --target-project upstream --config gitcode-repo.json

    # 主仓 PR
    python pr_commenter.py --number 42 \
        --comment "进展更新" \
        --target-project upstream \
        --config gitcode-repo.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Optional

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from config_loader import find_config_path
from gitcode_client import GitCodeClient, GitCodeClientError


def _looks_like_dev_review_comment(body: str) -> bool:
    markers = (
        "<!-- dev-reviewer:",
        "[CR-",
        "[Must Fix]",
        "[Should Fix]",
        "][Must Fix][",
        "][Should Fix][",
        "[严重][Must Fix]",
        "[建议][Should Fix]",
    )
    return any(marker in body for marker in markers)


def _validate_dev_review_comment_body(body: str) -> str:
    """Return an error message when a dev-reviewer comment is only a stub."""
    if "<!-- dev-reviewer:" not in body:
        return "dev-reviewer 检视意见必须使用 render-comments 生成的评论文件，正文缺少隐藏签名"
    visible_body = re.sub(r"<!--\s*dev-reviewer:[^>]+-->", "", body).strip()
    first_visible_line = next(
        (line.strip() for line in visible_body.splitlines() if line.strip()),
        "",
    )
    if first_visible_line.startswith("[CR-") and "|" in first_visible_line:
        return "dev-reviewer 检视意见疑似只提交了报告表格摘要，请使用 render-comments 生成的评论文件"
    paragraph_count = len([part for part in re.split(r"\n\s*\n", visible_body) if part.strip()])
    if paragraph_count < 4:
        return "dev-reviewer 检视意见正文结构过短，至少应包含标题、问题场景、影响和修复/验证建议"
    return ""


def post_comment(
    client: GitCodeClient,
    number: int,
    body: str,
    path: str = "",
    position: Optional[int] = None,
    position_type: str = "",
    target_project: str = "fork",
    need_to_resolve: Optional[bool] = None,
    allow_review_discussion_comment: bool = False,
) -> dict:
    """在 PR 中发表评论。"""
    result = client.create_pull_comment(
        number,
        body,
        path=path,
        position=position,
        position_type=position_type,
        target_project=target_project,
        need_to_resolve=need_to_resolve,
        allow_review_discussion_comment=allow_review_discussion_comment,
    )
    return {
        "success": True,
        "action": "comment",
        "pr_number": number,
        "comment_id": result.get("id"),
        "need_to_resolve": need_to_resolve,
        "html_url": result.get("html_url", ""),
    }


def resolve_comment(
    client: GitCodeClient,
    number: int,
    discussion_id: str,
    resolved: bool,
    target_project: str = "fork",
) -> dict:
    """修改某条检视意见（discussion）的解决状态。"""
    client.resolve_pull_comment(
        number,
        discussion_id,
        resolved=resolved,
        target_project=target_project,
    )
    return {
        "success": True,
        "action": "resolve" if resolved else "reopen",
        "pr_number": number,
        "discussion_id": discussion_id,
        "resolved": resolved,
    }


def approve_pr(
    client: GitCodeClient,
    number: int,
    target_project: str = "fork",
) -> dict:
    """检视意见全部闭环后，在评论区发 ``/lgtm`` 与 ``/approve``。"""
    posted = []
    for marker in ("/lgtm", "/approve"):
        result = client.create_pull_comment(
            number,
            marker,
            target_project=target_project,
        )
        posted.append(
            {"body": marker, "comment_id": result.get("id")}
        )
    return {
        "success": True,
        "action": "approve",
        "pr_number": number,
        "comments": posted,
    }


def add_labels(
    client: GitCodeClient,
    number: int,
    labels: list,
    target_project: str = "fork",
) -> dict:
    """为 PR 添加标签。"""
    result = client.add_pull_labels(
        number, labels, target_project=target_project
    )
    applied = []
    if isinstance(result, list):
        for label in result:
            if isinstance(label, dict):
                applied.append(label.get("name", ""))
            else:
                applied.append(str(label))
    return {
        "success": True,
        "action": "add_labels",
        "pr_number": number,
        "labels": [name for name in applied if name],
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="GitCode Pull Request 评论与标签工具",
    )
    parser.add_argument(
        "--number",
        type=int,
        required=True,
        help="PR 编号",
    )
    parser.add_argument(
        "--comment",
        default="",
        help="评论内容（支持 Markdown）",
    )
    parser.add_argument(
        "--comment-file",
        default="",
        help="从文件读取评论内容",
    )
    parser.add_argument(
        "--path",
        default="",
        help="行评文件相对路径（可选）",
    )
    parser.add_argument(
        "--position",
        type=int,
        default=None,
        help="行评行号（可选）",
    )
    parser.add_argument(
        "--position-type",
        default=None,
        choices=["text", "binary"],
        help="行评类型：text（默认行评）或 binary（文件级，忽略 position）",
    )
    parser.add_argument(
        "--need-to-resolve",
        action="store_true",
        help=(
            "随 --comment 创建评论时，标记为「需解决」的检视意见"
            "（need_to_resolve=true，成为待闭环 discussion）"
        ),
    )
    parser.add_argument(
        "--allow-review-discussion-comment",
        action="store_true",
        help=(
            "显式允许 dev-reviewer 检视意见不带 --path/--position，"
            "作为 PR 讨论区评论发布。默认禁止，以保证 Must/Should Fix 严格行评。"
        ),
    )
    parser.add_argument(
        "--resolve",
        default="",
        metavar="DISCUSSION_ID",
        help=(
            "标记指定 discussion 的检视意见为「已解决/闭环」"
            "（need_to_resolve=false）；ID 为评论 id（哈希串）"
        ),
    )
    parser.add_argument(
        "--reopen",
        default="",
        metavar="DISCUSSION_ID",
        help=(
            "标记指定 discussion 的检视意见为「待解决/重开」"
            "（need_to_resolve=true）"
        ),
    )
    parser.add_argument(
        "--approve",
        action="store_true",
        help="检视意见全部闭环后，在评论区发 /approve 与 /lgtm",
    )
    parser.add_argument(
        "--add-labels",
        default="",
        help="添加标签（逗号分隔）",
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
        "--target-project",
        default="fork",
        choices=["fork", "upstream"],
        help="PR 目标仓库（默认 fork）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅打印将要执行的操作，不调用写 API",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    comment_body = args.comment
    if args.comment_file:
        if not os.path.exists(args.comment_file):
            print(
                json.dumps(
                    {
                        "error": "评论文件不存在: "
                        f"{args.comment_file}"
                    },
                    ensure_ascii=False,
                )
            )
            sys.exit(1)
        with open(args.comment_file, encoding="utf-8") as f:
            comment_body = f.read()

    comment_body = (comment_body or "").strip()

    if args.number <= 0:
        parser.error("--number 须为正整数")

    pos_type = args.position_type or ""
    if pos_type != "binary":
        has_path = bool((args.path or "").strip())
        has_pos = args.position is not None
        if has_path != has_pos:
            parser.error(
                "行评须同时传 --path 与 --position，"
                "或传 --position-type binary"
            )

    has_action = any(
        [
            comment_body,
            args.add_labels,
            args.resolve.strip(),
            args.reopen.strip(),
            args.approve,
        ]
    )
    if not has_action:
        parser.error(
            "请指定 --comment/--comment-file、--add-labels、"
            "--resolve/--reopen 或 --approve 之一"
        )
    if args.need_to_resolve and not comment_body:
        parser.error("--need-to-resolve 仅在随 --comment/--comment-file 时生效")
    if (
        comment_body
        and _looks_like_dev_review_comment(comment_body)
        and not args.allow_review_discussion_comment
        and not ((args.path or "").strip() and args.position is not None)
    ):
        parser.error(
            "dev-reviewer 检视意见必须作为行评提交：请同时传 --path 与 --position；"
            "仅架构/文档类例外才可显式加 --allow-review-discussion-comment"
        )
    if comment_body and _looks_like_dev_review_comment(comment_body):
        review_body_error = _validate_dev_review_comment_body(comment_body)
        if review_body_error:
            parser.error(review_body_error)

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

    results = []
    try:
        if comment_body:
            results.append(
                post_comment(
                    client,
                    args.number,
                    comment_body,
                    path=args.path,
                    position=args.position,
                    position_type=args.position_type or "",
                    target_project=args.target_project,
                    need_to_resolve=(
                        True if args.need_to_resolve else None
                    ),
                    allow_review_discussion_comment=args.allow_review_discussion_comment,
                )
            )
        if args.resolve.strip():
            results.append(
                resolve_comment(
                    client,
                    args.number,
                    args.resolve.strip(),
                    resolved=True,
                    target_project=args.target_project,
                )
            )
        if args.reopen.strip():
            results.append(
                resolve_comment(
                    client,
                    args.number,
                    args.reopen.strip(),
                    resolved=False,
                    target_project=args.target_project,
                )
            )
        if args.approve:
            results.append(
                approve_pr(
                    client,
                    args.number,
                    target_project=args.target_project,
                )
            )
        if args.add_labels:
            label_list = [
                label.strip()
                for label in args.add_labels.split(",")
                if label.strip()
            ]
            if label_list:
                results.append(
                    add_labels(
                        client,
                        args.number,
                        label_list,
                        target_project=args.target_project,
                    )
                )
        print(json.dumps(results, ensure_ascii=False, indent=2))
    except (GitCodeClientError, ValueError) as exc:
        payload: dict = {"error": str(exc)}
        if isinstance(exc, GitCodeClientError):
            payload["status_code"] = exc.status_code
        print(json.dumps(payload, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()
