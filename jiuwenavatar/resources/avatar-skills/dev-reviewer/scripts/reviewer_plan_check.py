#!/usr/bin/env python3
"""Read-only checklist status for dev_plan.md / test_plan.md (reviewer only)."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

MODULE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
CHECKBOX_PATTERN = re.compile(r"^(?P<indent>\s*)-\s+\[(?P<mark>[ xX])\]\s+(?P<text>.*)$")
TASK_ID_PATTERN = re.compile(r"^(?P<id>(?:\d+(?:\.\d+)*|[A-Za-z]+-\d+))\.?\s+")

PLAN_CONFIG = {
    "dev": {"filename": "dev_plan.md", "task_heading": "## 开发任务"},
    "test": {"filename": "test_plan.md", "task_heading": "## 测试任务"},
}


@dataclass(frozen=True)
class PlanItem:
    index: int
    line: int
    indent: int
    state: str
    text: str
    item_id: str | None


def fail(message: str) -> None:
    print(f"[ERROR] {message}", file=sys.stderr)
    raise SystemExit(2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Query checklist status in dev_plan.md / test_plan.md (reviewer, read-only)."
    )
    parser.add_argument("--module", required=True, help="Module name under doc/, e.g. user.")
    parser.add_argument("--repo-root", required=True, help="Repository root containing doc/<module>/.")

    status = parser.add_subparsers(dest="command", required=True).add_parser(
        "status", help="Print unfinished and completed plan items."
    )
    status.add_argument("--plan", choices=["dev", "test", "both"], default="both")
    status.add_argument("--state", choices=["all", "todo", "done"], default="all")
    status.add_argument("--format", choices=["text", "json"], default="text")

    return parser.parse_args()


def validate_module_name(module: str) -> None:
    if not MODULE_NAME_PATTERN.fullmatch(module):
        fail("模块名只能包含英文字母、数字、下划线或短横线，例如：user、order-center。")


def resolve_repo_root(repo_root: str) -> Path:
    root = Path(repo_root.strip()).expanduser().resolve()
    if not root.is_dir():
        fail(f"--repo-root 不是有效目录：{root}")
    return root


def plan_path(root: Path, module: str, plan: str) -> Path:
    return root / "doc" / module / PLAN_CONFIG[plan]["filename"]


def read_lines(path: Path) -> list[str]:
    if not path.is_file():
        fail(f"未找到计划文件：{path}")
    return path.read_text(encoding="utf-8-sig").lstrip("\ufeff").splitlines()


def task_section_bounds(lines: list[str], heading: str) -> tuple[int, int]:
    start = None
    for index, line in enumerate(lines):
        if line.strip() == heading:
            start = index
            break
    if start is None:
        fail(f"缺少任务章节：{heading}")
    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if line.startswith("# ") or line.startswith("## "):
            end = index
            break
    return start + 1, end


def extract_item_id(text: str) -> str | None:
    match = TASK_ID_PATTERN.match(text.strip())
    if not match:
        return None
    return match.group("id")


def parse_items(lines: list[str], plan: str) -> list[PlanItem]:
    heading = PLAN_CONFIG[plan]["task_heading"]
    start, end = task_section_bounds(lines, heading)
    items: list[PlanItem] = []
    for index in range(start, end):
        match = CHECKBOX_PATTERN.match(lines[index])
        if not match:
            continue
        mark = match.group("mark").lower()
        text = match.group("text").strip()
        items.append(
            PlanItem(
                index=index,
                line=index + 1,
                indent=len(match.group("indent")),
                state="done" if mark == "x" else "todo",
                text=text,
                item_id=extract_item_id(text),
            )
        )
    return items


def item_to_dict(item: PlanItem) -> dict[str, object]:
    return {
        "line": item.line,
        "state": item.state,
        "id": item.item_id,
        "text": item.text,
        "indent": item.indent,
    }


def filter_items(items: list[PlanItem], state_filter: str) -> list[PlanItem]:
    if state_filter == "all":
        return items
    return [item for item in items if item.state == state_filter]


def render_text_status(results: dict[str, list[PlanItem]], state_filter: str) -> None:
    for plan, items in results.items():
        print(f"# {plan}_plan")
        groups = [
            ("未完成", [item for item in items if item.state == "todo"]),
            ("已完成", [item for item in items if item.state == "done"]),
        ]
        for title, group_items in groups:
            if state_filter == "todo" and title != "未完成":
                continue
            if state_filter == "done" and title != "已完成":
                continue
            print(f"## {title} ({len(group_items)})")
            if not group_items:
                print("- none")
                continue
            for item in group_items:
                item_id = f" id={item.item_id}" if item.item_id else ""
                print(f"- line={item.line}{item_id} {item.text}")


def command_status(args: argparse.Namespace, root: Path, module: str) -> None:
    plans = ["dev", "test"] if args.plan == "both" else [args.plan]
    results: dict[str, list[PlanItem]] = {}
    for plan in plans:
        path = plan_path(root, module, plan)
        items = parse_items(read_lines(path), plan)
        results[plan] = filter_items(items, args.state)

    if args.format == "json":
        payload = {
            plan: {
                "todo": [item_to_dict(item) for item in items if item.state == "todo"],
                "done": [item_to_dict(item) for item in items if item.state == "done"],
            }
            for plan, items in results.items()
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    render_text_status(results, args.state)


def main() -> None:
    args = parse_args()
    if args.command != "status":
        fail("reviewer 仅支持 status 查询，不得修改 checklist。")

    module = args.module.strip()
    validate_module_name(module)
    root = resolve_repo_root(args.repo_root)
    command_status(args, root, module)


if __name__ == "__main__":
    main()
