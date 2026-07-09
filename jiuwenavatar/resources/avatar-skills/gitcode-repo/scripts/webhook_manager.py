#!/usr/bin/env python3
# coding: utf-8
"""GitCode 仓库 Webhook 管理 CLI。

用于把 GitCode 事件推送到 JiuwenAvatar Gateway Webhook endpoint。

示例：
    python scripts/webhook_manager.py --list --config gitcode-repo.json --workspace demo
    python scripts/webhook_manager.py --create \
        --url https://example.ngrok-free.app/webhook/gitcode/pr-assigned \
        --events pull_request \
        --secret "$WEBHOOK_SECRET"
    python scripts/webhook_manager.py --delete 123
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from gitcode_client import GitCodeClient, GitCodeClientError


def _json_dump(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _parse_events(raw_events: List[str]) -> List[str]:
    events: List[str] = []
    for item in raw_events:
        for part in str(item or "").split(","):
            value = part.strip()
            if value:
                events.append(value)
    return events


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="GitCode 仓库 Webhook 管理工具")
    parser.add_argument("--config", default="", help="gitcode-repo.json 路径")
    parser.add_argument("--workspace", default="", help="workspaces[].name，多工作区时必填")
    parser.add_argument(
        "--source",
        choices=["upstream", "fork"],
        default="upstream",
        help="Webhook 所属仓库，默认 upstream",
    )
    parser.add_argument("--dry-run", action="store_true", help="只打印将要执行的动作")

    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--list", action="store_true", help="列出仓库 Webhook")
    action.add_argument("--create", action="store_true", help="创建仓库 Webhook")
    action.add_argument("--delete", metavar="HOOK_ID", help="删除仓库 Webhook")

    parser.add_argument("--url", default="", help="创建 Webhook 的公网 URL")
    parser.add_argument(
        "--secret",
        default="",
        help="Webhook 密钥；会作为 GitCode hook password 传入，也用于 JiuwenAvatar 触发器 webhook_secret",
    )
    parser.add_argument(
        "--events",
        action="append",
        default=[],
        help="GitCode 事件名，可多次传入或逗号分隔，如 pull_request,issue,push",
    )
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    try:
        client = GitCodeClient.from_config(
            args.config or None,
            args.workspace or None,
            dry_run=args.dry_run,
        )
        if args.list:
            _json_dump(client.list_hooks(target_project=args.source))
            return 0

        if args.create:
            if not args.url:
                parser.error("--create 需要 --url")
            result = client.create_hook(
                url=args.url,
                password=args.secret,
                events=_parse_events(args.events),
                target_project=args.source,
            )
            _json_dump(result)
            return 0

        if args.delete:
            result = client.delete_hook(args.delete, target_project=args.source)
            _json_dump(result)
            return 0

        parser.error("必须指定操作")
        return 2
    except (GitCodeClientError, ValueError) as exc:
        _json_dump({
            "error": str(exc),
            "status_code": getattr(exc, "status_code", None),
            "response_body": getattr(exc, "response_body", None),
        })
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
