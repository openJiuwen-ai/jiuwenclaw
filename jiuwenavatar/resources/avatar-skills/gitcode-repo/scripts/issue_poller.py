#!/usr/bin/env python3
# coding: utf-8
"""
Issue 轮询守护脚本。

定期检查 GitCode upstream 仓库中分配给当前开发者的
新 issue 事件（assign/mention/标签），发现后通知开发者
或自动触发 gitcode-repo。

用法:
    # 前台运行
    python issue_poller.py --config gitcode-repo.json

    # 后台运行
    nohup python issue_poller.py \
        --config gitcode-repo.json &

    # 自动触发模式
    python issue_poller.py --config gitcode-repo.json \
        --auto-trigger
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Set

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from gitcode_client import GitCodeClient, GitCodeClientError
from config_loader import find_config_path, load_resolved_config, ConfigError

STATE_FILE = ".issue-poller-state.json"


class PollerState:
    """轮询状态管理，避免重复触发。"""

    def __init__(self, state_path: str):
        self._path = state_path
        self._data: Dict[str, Any] = {
            "last_poll_time": "",
            "processed_issue_ids": [],
            "processed_comment_ids": [],
        }
        self._load()

    def _load(self) -> None:
        """从文件加载状态。"""
        if os.path.exists(self._path):
            try:
                with open(
                    self._path, encoding="utf-8"
                ) as f:
                    self._data = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass

    def save(self) -> None:
        """保存状态到文件。"""
        self._data["last_poll_time"] = (
            datetime.now(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
        with open(
            self._path, "w", encoding="utf-8"
        ) as f:
            json.dump(
                self._data, f,
                ensure_ascii=False, indent=2,
            )

    @property
    def processed_issues(self) -> Set[int]:
        """已处理的 issue ID 集合。"""
        return set(
            self._data.get(
                "processed_issue_ids", []
            )
        )

    @property
    def processed_comments(self) -> Set[int]:
        """已处理的评论 ID 集合。"""
        return set(
            self._data.get(
                "processed_comment_ids", []
            )
        )

    def mark_issue(self, issue_id: int) -> None:
        """标记 issue 为已处理。"""
        ids = self._data.setdefault(
            "processed_issue_ids", []
        )
        if issue_id not in ids:
            ids.append(issue_id)

    def mark_comment(self, comment_id: int) -> None:
        """标记评论为已处理。"""
        ids = self._data.setdefault(
            "processed_comment_ids", []
        )
        if comment_id not in ids:
            ids.append(comment_id)


class IssuePoller:
    """Issue 轮询器。"""

    def __init__(
        self,
        client: GitCodeClient,
        config: Dict[str, Any],
        state: PollerState,
        auto_trigger: bool = False,
    ):
        self._client = client
        self._config = config
        self._state = state
        self._auto_trigger = auto_trigger

        poller_cfg = config.get("poller", {})
        self._interval = poller_cfg.get(
            "interval_seconds", 60
        )
        self._on_assign = poller_cfg.get(
            "trigger_on_assign", True
        )
        self._on_mention = poller_cfg.get(
            "trigger_on_mention", True
        )
        self._trigger_labels: List[str] = poller_cfg.get(
            "trigger_on_labels", ["auto-resolve"]
        )
        self._keywords: List[str] = poller_cfg.get(
            "mention_keywords", ["@bot-resolve"]
        )
        self._fork_owner = config.get(
            "fork", {}
        ).get("owner", "")

    def run(self) -> None:
        """启动轮询主循环。"""
        print(
            f"[issue-poller] 启动轮询，"
            f"间隔 {self._interval}s，"
            f"监听用户: {self._fork_owner}"
        )
        print(
            f"[issue-poller] 触发条件: "
            f"assign={self._on_assign} "
            f"mention={self._on_mention} "
            f"labels={self._trigger_labels}"
        )
        print()

        while True:
            try:
                self._poll_once()
            except GitCodeClientError as exc:
                print(
                    f"[issue-poller] API 错误: {exc}",
                    file=sys.stderr,
                )
            except Exception as exc:
                print(
                    f"[issue-poller] 异常: {exc}",
                    file=sys.stderr,
                )
            time.sleep(self._interval)

    def _poll_once(self) -> None:
        """执行一次轮询检查。"""
        events: List[Dict[str, Any]] = []

        if self._on_assign:
            events.extend(
                self._check_assigned_issues()
            )

        if self._trigger_labels:
            events.extend(
                self._check_labeled_issues()
            )

        if self._on_mention:
            events.extend(
                self._check_mentions()
            )

        for event in events:
            self._handle_event(event)

        self._state.save()

    def _check_assigned_issues(
        self,
    ) -> List[Dict[str, Any]]:
        """检查分配给自己的新 issue。"""
        if not self._fork_owner:
            return []

        issues = self._client.list_issues(
            state="open",
            assignee=self._fork_owner,
            per_page=50,
            target_project="upstream",
        )
        events = []
        for issue in issues:
            iid = issue.get("number", 0)
            if iid in self._state.processed_issues:
                continue
            events.append({
                "type": "assign",
                "issue_number": iid,
                "title": issue.get("title", ""),
            })
            self._state.mark_issue(iid)
        return events

    def _check_labeled_issues(
        self,
    ) -> List[Dict[str, Any]]:
        """检查被打上触发标签的 issue。"""
        events = []
        for label in self._trigger_labels:
            issues = self._client.list_issues(
                state="open",
                labels=label,
                per_page=50,
                target_project="upstream",
            )
            for issue in issues:
                iid = issue.get("number", 0)
                if iid in self._state.processed_issues:
                    continue
                events.append({
                    "type": "label",
                    "issue_number": iid,
                    "title": issue.get("title", ""),
                    "label": label,
                })
                self._state.mark_issue(iid)
        return events

    def _check_mentions(
        self,
    ) -> List[Dict[str, Any]]:
        """检查评论中的 mention 和关键词。"""
        if not self._fork_owner and not self._keywords:
            return []

        issues = self._client.list_issues(
            state="open", per_page=20,
            target_project="upstream",
        )
        events = []
        patterns = list(self._keywords)
        if self._fork_owner:
            patterns.append(f"@{self._fork_owner}")

        for issue in issues:
            iid = issue.get("number", 0)
            try:
                comments = (
                    self._client.get_issue_comments(
                        iid, per_page=50,
                        target_project="upstream",
                    )
                )
            except GitCodeClientError:
                continue

            for comment in comments:
                cid = comment.get("id", 0)
                if cid in self._state.processed_comments:
                    continue
                body = comment.get("body", "")
                matched = self._match_patterns(
                    body, patterns
                )
                if matched:
                    events.append({
                        "type": "mention",
                        "issue_number": iid,
                        "title": issue.get(
                            "title", ""
                        ),
                        "comment_id": cid,
                        "matched": matched,
                    })
                    self._state.mark_comment(cid)
        return events

    @staticmethod
    def _match_patterns(
        text: str,
        patterns: List[str],
    ) -> str:
        """检查文本是否匹配任一模式。

        Args:
            text: 待检查文本。
            patterns: 模式列表。

        Returns:
            匹配到的模式，未匹配返回空字符串。
        """
        for pattern in patterns:
            if re.search(
                re.escape(pattern), text, re.IGNORECASE
            ):
                return pattern
        return ""

    def _handle_event(
        self,
        event: Dict[str, Any],
    ) -> None:
        """处理发现的事件。"""
        number = event["issue_number"]
        title = event["title"]
        etype = event["type"]

        if etype == "assign":
            print(
                f"[issue-poller] "
                f"Issue #{number} 已分配给你: "
                f'"{title}"'
            )
        elif etype == "label":
            label = event.get("label", "")
            print(
                f"[issue-poller] "
                f"Issue #{number} 被标记为 "
                f'[{label}]: "{title}"'
            )
        elif etype == "mention":
            matched = event.get("matched", "")
            print(
                f"[issue-poller] "
                f"Issue #{number} 评论中提到 "
                f'{matched}: "{title}"'
            )

        cmd = f'claude "/gitcode-repo upstream {number}"'
        print(f"[issue-poller] 执行: {cmd}")
        print()

        if self._auto_trigger:
            self._trigger_resolver(number)

    @staticmethod
    def _trigger_resolver(number: int) -> None:
        """自动触发 gitcode-repo（upstream 主仓 Issue）。"""
        try:
            subprocess.Popen(
                [
                    "claude",
                    f"/gitcode-repo upstream {number}",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            print(
                f"[issue-poller] "
                f"已自动触发 issue #{number}"
            )
        except FileNotFoundError:
            print(
                "[issue-poller] "
                "错误: claude CLI 未找到，"
                "请确保在 PATH 中",
                file=sys.stderr,
            )


def _build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。"""
    parser = argparse.ArgumentParser(
        description="GitCode Issue 轮询守护脚本",
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
        "--auto-trigger",
        action="store_true",
        help="自动触发 claude gitcode-repo",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="只轮询一次（用于测试）",
    )
    return parser


def main() -> None:
    """CLI 入口。"""
    parser = _build_parser()
    args = parser.parse_args()

    config_path = find_config_path(args.config)

    try:
        config = load_resolved_config(
            config_path,
            args.workspace or None,
        )
        client = GitCodeClient.from_config(
            config_path or None,
            workspace_name=args.workspace or None,
        )
    except ConfigError as exc:
        print(
            f"[issue-poller] 初始化失败: {exc}",
            file=sys.stderr,
        )
        sys.exit(1)
    except Exception as exc:
        print(
            f"[issue-poller] 初始化失败: {exc}",
            file=sys.stderr,
        )
        sys.exit(1)

    state_dir = os.path.dirname(
        config_path
    ) if config_path else "."
    state_path = os.path.join(state_dir, STATE_FILE)
    state = PollerState(state_path)

    poller = IssuePoller(
        client=client,
        config=config,
        state=state,
        auto_trigger=args.auto_trigger,
    )

    if args.once:
        poller._poll_once()
        state.save()
        print("[issue-poller] 单次轮询完成")
    else:
        poller.run()


if __name__ == "__main__":
    main()
