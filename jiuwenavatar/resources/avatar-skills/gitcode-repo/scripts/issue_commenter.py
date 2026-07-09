#!/usr/bin/env python3
# coding: utf-8
"""
Issue 评论与标签 CLI 工具。

在 GitCode fork/upstream 仓库的 Issue 中发表评论、添加标签。

用法:
    # 发表评论
    python issue_commenter.py --number 42 \
        --comment "## 分析结果\n\n问题定位在..."

    # 添加标签
    python issue_commenter.py --number 42 \
        --add-labels "in-progress"

    # 同时操作
    python issue_commenter.py --number 42 \
        --comment "修复中" --add-labels "in-progress"

    # 从文件读取评论内容
    python issue_commenter.py --number 42 \
        --comment-file analysis.md

    # 指定配置文件
    python issue_commenter.py --number 42 \
        --comment "test" --config gitcode-repo.json
"""

import argparse
import json
import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from gitcode_client import GitCodeClient, GitCodeClientError
from config_loader import find_config_path


def post_comment(
    client: GitCodeClient,
    number: int,
    body: str,
    source: str = "fork",
) -> dict:
    """在 Issue 中发表评论。

    Args:
        client: GitCode API 客户端。
        number: Issue 编号。
        body: 评论内容。
        source: Issue 目标仓库（fork/upstream）。

    Returns:
        创建的评论详情。
    """
    result = client.create_comment(
        number, body, target_project=source
    )
    return {
        "success": True,
        "action": "comment",
        "issue_number": number,
        "comment_id": result.get("id"),
        "html_url": result.get("html_url", ""),
    }


def add_labels(
    client: GitCodeClient,
    number: int,
    labels: list,
    source: str = "fork",
) -> dict:
    """为 Issue 添加标签。

    Args:
        client: GitCode API 客户端。
        number: Issue 编号。
        labels: 标签名称列表。
        source: Issue 目标仓库（fork/upstream）。

    Returns:
        操作结果。
    """
    result = client.add_labels(
        number, labels, target_project=source
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
        "issue_number": number,
        "labels": applied,
    }


def _build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。"""
    parser = argparse.ArgumentParser(
        description="GitCode Issue 评论与标签工具",
    )
    parser.add_argument(
        "--number",
        type=int,
        required=True,
        help="Issue 编号",
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
        "--source",
        default="fork",
        choices=["fork", "upstream"],
        help="Issue 目标仓库（默认 fork）",
    )
    return parser


def main() -> None:
    """CLI 入口。"""
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
        with open(
            args.comment_file, encoding="utf-8"
        ) as f:
            comment_body = f.read()

    if not comment_body and not args.add_labels:
        parser.error(
            "请指定 --comment/--comment-file "
            "或 --add-labels"
        )

    config_path = find_config_path(args.config)

    try:
        client = GitCodeClient.from_config(
            config_path or None,
            workspace_name=args.workspace or None,
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
                    client, args.number, comment_body,
                    source=args.source,
                )
            )
        if args.add_labels:
            label_list = [
                l.strip()
                for l in args.add_labels.split(",")
                if l.strip()
            ]
            if label_list:
                results.append(
                    add_labels(
                        client,
                        args.number,
                        label_list,
                        source=args.source,
                    )
                )
        print(
            json.dumps(
                results,
                ensure_ascii=False,
                indent=2,
            )
        )
    except (GitCodeClientError, ValueError) as exc:
        payload: dict = {"error": str(exc)}
        if isinstance(exc, GitCodeClientError):
            payload["status_code"] = exc.status_code
        print(
            json.dumps(
                payload,
                ensure_ascii=False,
            )
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
