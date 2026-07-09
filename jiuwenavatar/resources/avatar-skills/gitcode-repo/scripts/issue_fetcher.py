#!/usr/bin/env python3
# coding: utf-8
"""Issue 获取 CLI 工具。

从 GitCode fork/upstream 仓库获取 Issue 详情或列表，
输出结构化 JSON 供 Claude 解析。

用法:
    # 获取单个 issue（含评论，默认从 fork）
    python issue_fetcher.py --number 42
    # 同上（--issue 为 --number 别名，勿与 pr_creator 的 --issue 关联编号混淆）
    python issue_fetcher.py --issue 42

    # 列出 open issues
    python issue_fetcher.py --list --state open

    # 按标签过滤
    python issue_fetcher.py --list --labels bug

    # 按指派人过滤
    python issue_fetcher.py --list --assignee SnapeK

    # 按关键词搜索
    python issue_fetcher.py --list --search "error"

    # 创建 Issue（默认 fork；推荐用文件传递复杂 Markdown 正文）
    python issue_fetcher.py --create --title "Bug: 简要描述" \
        --body-file issue.md --labels "bug"

    # 指定 issue 来源和配置文件
    python issue_fetcher.py --number 42 --source upstream --config gitcode-repo.json
"""

import argparse
import json
import os
import sys

# 支持从 scripts/ 目录或项目根目录运行
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from gitcode_client import GitCodeClient, GitCodeClientError
from config_loader import find_config_path

_BODY_REJECT_MARKERS = (
    "body",
    "正文",
    "description",
    "content",
    "markdown",
    "too long",
    "invalid",
)


def _is_body_rejected_error(exc: GitCodeClientError) -> bool:
    """判断 400 是否更像「正文字段」被拒，而非其它校验失败。"""
    if exc.status_code != 400:
        return False
    text = f"{exc} {exc.response_body or ''}".lower()
    return any(marker in text for marker in _BODY_REJECT_MARKERS)


def _client_error_summary(exc: GitCodeClientError) -> str:
    """生成可安全写入 JSON 的简短错误摘要。"""
    parts = [str(exc)]
    if exc.status_code is not None:
        parts.append(f"(HTTP {exc.status_code})")
    return " ".join(parts)


def _format_issue(issue: dict) -> dict:
    """提取 Issue 关键字段，输出精简结构。

    Args:
        issue: GitCode API 返回的原始 Issue 数据。

    Returns:
        精简后的 Issue 字典。
    """
    labels = []
    for label in issue.get("labels", []):
        if isinstance(label, dict):
            labels.append(label.get("name", ""))
        else:
            labels.append(str(label))

    assignee = issue.get("assignee")
    assignee_name = ""
    if isinstance(assignee, dict):
        assignee_name = assignee.get("login", "")

    return {
        "number": issue.get("number"),
        "title": issue.get("title", ""),
        "state": issue.get("state", ""),
        "labels": labels,
        "assignee": assignee_name,
        "body": issue.get("body", ""),
        "created_at": issue.get("created_at", ""),
        "updated_at": issue.get("updated_at", ""),
        "html_url": issue.get("html_url", ""),
    }


def _format_comment(comment: dict) -> dict:
    """提取评论关键字段。

    Args:
        comment: GitCode API 返回的原始评论数据。

    Returns:
        精简后的评论字典。
    """
    user = comment.get("user", {})
    return {
        "id": comment.get("id"),
        "author": user.get("login", "") if user else "",
        "body": comment.get("body", ""),
        "created_at": comment.get("created_at", ""),
    }


def fetch_issue(
    client: GitCodeClient,
    number: int,
    source: str = "fork",
) -> dict:
    """获取单个 Issue 详情（含评论）。

    Args:
        client: GitCode API 客户端。
        number: Issue 编号。
        source: Issue 来源仓库（fork/upstream）。

    Returns:
        包含 Issue 详情和评论的字典。
    """
    issue = client.get_issue(number, target_project=source)
    result = _format_issue(issue)

    comments_raw = client.get_all_issue_comments(
        number, target_project=source
    )
    result["comments"] = [
        _format_comment(c) for c in comments_raw
    ]
    return result


def list_issues(
    client: GitCodeClient,
    state: str = "open",
    labels: str = "",
    assignee: str = "",
    search: str = "",
    page: int = 1,
    per_page: int = 20,
    source: str = "fork",
) -> list:
    """获取 Issue 列表。

    Args:
        client: GitCode API 客户端。
        state: Issue 状态过滤。
        labels: 标签过滤。
        assignee: 指派人过滤。
        search: 关键词搜索。
        page: 页码。
        per_page: 每页数量。
        source: Issue 来源仓库（fork/upstream）。

    Returns:
        精简后的 Issue 列表。
    """
    issues = client.list_issues(
        state=state,
        labels=labels,
        assignee=assignee,
        search=search,
        page=page,
        per_page=per_page,
        target_project=source,
    )
    return [_format_issue(i) for i in issues]


def create_issue(
    client: GitCodeClient,
    title: str,
    body: str = "",
    labels: str = "",
    source: str = "fork",
) -> dict:
    """创建新 Issue。

    Args:
        client: GitCode API 客户端。
        title: Issue 标题。
        body: Issue 描述。
        labels: 逗号分隔的标签。
        source: Issue 目标仓库（fork/upstream）。

    Returns:
        创建的 Issue 详情。
    """
    label_list = [
        l.strip() for l in labels.split(",") if l.strip()
    ] if labels else []
    warnings = []

    try:
        result = client.create_issue(
            title=title, body=body, target_project=source
        )
    except GitCodeClientError as exc:
        if not body or not _is_body_rejected_error(exc):
            raise
        result = client.create_issue(
            title=title, target_project=source
        )
        warnings.append(
            "Issue 已用标题创建，但正文创建失败；"
            "请检查正文内容后用评论或更新接口补充。"
        )

    issue = _format_issue(result)
    number = issue.get("number")

    if label_list:
        if not number:
            warnings.append("创建结果缺少 Issue 编号，无法自动添加标签。")
        else:
            try:
                label_result = client.add_labels(
                    number, label_list, target_project=source
                )
                applied = []
                for label in label_result:
                    if isinstance(label, dict):
                        applied.append(label.get("name", ""))
                    else:
                        applied.append(str(label))
                issue["labels"] = [label for label in applied if label]
            except GitCodeClientError as exc:
                warnings.append(
                    "Issue 已创建，但添加标签失败: "
                    f"{_client_error_summary(exc)}"
                )

    if warnings:
        issue["warnings"] = warnings
    return issue


def _build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。"""
    parser = argparse.ArgumentParser(
        description="GitCode Issue 获取工具",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--number",
        "--issue",
        type=int,
        dest="number",
        help="获取指定编号的 Issue（含评论）；--issue 为 --number 的别名",
    )
    mode.add_argument(
        "--list",
        action="store_true",
        dest="list_mode",
        help="列出 Issue",
    )
    mode.add_argument(
        "--create",
        action="store_true",
        dest="create_mode",
        help="创建新 Issue",
    )
    parser.add_argument(
        "--title",
        default="",
        help="Issue 标题（用于创建）",
    )
    parser.add_argument(
        "--body",
        default="",
        help="Issue 描述（用于创建）",
    )
    parser.add_argument(
        "--body-file",
        default="",
        help="从文件读取 Issue 描述（推荐用于复杂 Markdown）",
    )
    parser.add_argument(
        "--state",
        default="open",
        choices=["open", "closed", "all"],
        help="Issue 状态过滤（默认 open）",
    )
    parser.add_argument(
        "--labels",
        default="",
        help="按标签过滤（逗号分隔）或创建时指定标签",
    )
    parser.add_argument(
        "--assignee",
        default="",
        help="按指派人过滤",
    )
    parser.add_argument(
        "--search",
        default="",
        help="按关键词搜索（搜索标题和内容）",
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
        "--source",
        default="fork",
        choices=["fork", "upstream"],
        help="Issue 目标仓库（默认 fork；--number/--list/--create 均生效）",
    )
    return parser


def main() -> None:
    """CLI 入口。"""
    parser = _build_parser()
    args = parser.parse_args()

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

    try:
        if args.number:
            result = fetch_issue(
                client, args.number, source=args.source
            )
        elif args.create_mode:
            if not args.title:
                parser.error("创建 Issue 时必须指定 --title")
            body = args.body
            if args.body_file:
                if not os.path.exists(args.body_file):
                    print(
                        json.dumps(
                            {
                                "error": "正文文件不存在: "
                                f"{args.body_file}"
                            },
                            ensure_ascii=False,
                        )
                    )
                    sys.exit(1)
                with open(
                    args.body_file, encoding="utf-8"
                ) as f:
                    body = f.read()
            result = create_issue(
                client,
                title=args.title,
                body=body,
                labels=args.labels,
                source=args.source,
            )
        else:
            result = list_issues(
                client,
                state=args.state,
                labels=args.labels,
                assignee=args.assignee,
                search=args.search,
                page=args.page,
                per_page=args.per_page,
                source=args.source,
            )
        print(
            json.dumps(
                result,
                ensure_ascii=False,
                indent=2,
            )
        )
    except GitCodeClientError as exc:
        print(
            json.dumps(
                {
                    "error": str(exc),
                    "status_code": exc.status_code,
                },
                ensure_ascii=False,
            )
        )
        sys.exit(1)
    except ValueError as exc:
        print(
            json.dumps(
                {"error": str(exc)},
                ensure_ascii=False,
            )
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
