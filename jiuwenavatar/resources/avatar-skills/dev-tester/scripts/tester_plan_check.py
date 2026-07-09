#!/usr/bin/env python3
"""Inspect doc/<module>/test_plan.md checklist: status (query), verify (validate), set (legacy/manual)."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

MODULE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
CHECKBOX_PATTERN = re.compile(r"^(?P<indent>\s*)-\s+\[(?P<mark>[ xX])\]\s+(?P<text>.*)$")
DASH_NOT_CHECKBOX_PATTERN = re.compile(r"^\s*-\s+(?!\[)")
TASK_ID_PATTERN = re.compile(r"^(?P<id>(?:\d+(?:\.\d+)*|[A-Za-z]+-\d+))\.?\s+")
PLAN_FILENAME = "test_plan.md"
TASK_HEADING = "## 测试任务"


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
        description=(
            "test_plan checklist tools for tester: status=query, verify=validate after md edits, "
            "set=legacy/manual (agents must edit test_plan.md directly)."
        )
    )
    parser.add_argument("--module", required=True, help="Module name under doc/, e.g. user.")
    parser.add_argument("--repo-root", required=True, help="Repository root containing doc/<module>/.")

    subparsers = parser.add_subparsers(dest="command", required=True)

    status = subparsers.add_parser("status", help="Query unfinished and completed test plan items.")
    status.add_argument("--state", choices=["all", "todo", "done"], default="all")
    status.add_argument("--format", choices=["text", "json"], default="text")

    set_state = subparsers.add_parser(
        "set",
        help="(Legacy/manual) Mark exactly one checklist item done or todo. Agents should edit test_plan.md directly.",
    )
    set_state.add_argument("--state", choices=["todo", "done"], required=True)
    selector = set_state.add_mutually_exclusive_group(required=True)
    selector.add_argument("--item", help="Task id or unique text fragment, e.g. 1.2.")
    selector.add_argument("--line", type=int, help="1-based line number of the checklist item.")
    set_state.add_argument(
        "--allow-parent",
        action="store_true",
        help="Allow marking a parent item done while descendant items remain unfinished.",
    )

    verify = subparsers.add_parser(
        "verify",
        help="Validate test_plan.md checklist format and bidirectional parent/child consistency after agent edits.",
    )
    verify.add_argument("--format", choices=["text", "json"], default="text")
    verify.add_argument(
        "--allow-parent",
        action="store_true",
        help=(
            "Skip parent-done vs unfinished-child checks. Use only when Leader pre-approved "
            "the exception; record rationale and line/id in Gate Evidence."
        ),
    )

    return parser.parse_args()


def validate_module_name(module: str) -> None:
    if not MODULE_NAME_PATTERN.fullmatch(module):
        fail("模块名只能包含英文字母、数字、下划线或短横线，例如：user、order-center。")


def resolve_repo_root(repo_root: str) -> Path:
    root = Path(repo_root.strip()).expanduser().resolve()
    if not root.is_dir():
        fail(f"--repo-root 不是有效目录：{root}")
    return root


def plan_path(root: Path, module: str) -> Path:
    return root / "doc" / module / PLAN_FILENAME


def read_lines(path: Path) -> list[str]:
    if not path.is_file():
        fail(f"未找到计划文件：{path}")
    return path.read_text(encoding="utf-8-sig").lstrip("\ufeff").splitlines()


def write_lines(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def task_section_bounds(lines: list[str]) -> tuple[int, int]:
    start = None
    for index, line in enumerate(lines):
        if line.strip() == TASK_HEADING:
            start = index
            break
    if start is None:
        fail(f"缺少任务章节：{TASK_HEADING}")
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


def parse_items(lines: list[str]) -> list[PlanItem]:
    start, end = task_section_bounds(lines)
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


def command_status(args: argparse.Namespace, root: Path, module: str) -> None:
    path = plan_path(root, module)
    items = filter_items(parse_items(read_lines(path)), args.state)

    if args.format == "json":
        payload = {
            "test": {
                "todo": [item_to_dict(item) for item in items if item.state == "todo"],
                "done": [item_to_dict(item) for item in items if item.state == "done"],
            }
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    print("# test_plan")
    groups = [
        ("未完成", [item for item in items if item.state == "todo"]),
        ("已完成", [item for item in items if item.state == "done"]),
    ]
    for title, group_items in groups:
        if args.state == "todo" and title != "未完成":
            continue
        if args.state == "done" and title != "已完成":
            continue
        print(f"## {title} ({len(group_items)})")
        if not group_items:
            print("- none")
            continue
        for item in group_items:
            item_id = f" id={item.item_id}" if item.item_id else ""
            print(f"- line={item.line}{item_id} {item.text}")


def select_item(items: list[PlanItem], item_selector: str | None, line: int | None) -> PlanItem:
    if line is not None:
        matches = [item for item in items if item.line == line]
    else:
        assert item_selector is not None
        selector = item_selector.strip().rstrip(".")
        matches = [
            item
            for item in items
            if item.item_id == selector or selector.lower() in item.text.lower()
        ]
    if not matches:
        fail("未找到匹配的 checklist 项。请用 status 查看 line/id 后重试。")
    if len(matches) > 1:
        lines = ", ".join(str(item.line) for item in matches)
        fail(f"匹配到多个 checklist 项（line: {lines}）。请改用 --line 精确指定。")
    return matches[0]


def has_unfinished_children(items: list[PlanItem], selected: PlanItem) -> bool:
    for item in items:
        if item.index <= selected.index:
            continue
        if item.indent <= selected.indent:
            return False
        if item.state == "todo":
            return True
    return False


def has_descendant_checkboxes(items: list[PlanItem], selected: PlanItem) -> bool:
    for item in items:
        if item.index <= selected.index:
            continue
        if item.indent <= selected.indent:
            return False
        return True
    return False


def all_descendants_done(items: list[PlanItem], selected: PlanItem) -> bool:
    has_descendant = False
    for item in items:
        if item.index <= selected.index:
            continue
        if item.indent <= selected.indent:
            break
        has_descendant = True
        if item.state == "todo":
            return False
    return has_descendant


def update_line_state(line: str, state: str) -> str:
    mark = "x" if state == "done" else " "
    updated = CHECKBOX_PATTERN.sub(
        lambda match: f"{match.group('indent')}- [{mark}] {match.group('text')}", line
    )
    if updated == line and not CHECKBOX_PATTERN.match(line):
        fail("目标行不是 checklist 项。")
    return updated


def collect_task_section_format_errors(lines: list[str]) -> list[str]:
    start, end = task_section_bounds(lines)
    errors: list[str] = []
    for index in range(start, end):
        stripped = lines[index].rstrip("\n\r")
        if not stripped.strip():
            continue
        if CHECKBOX_PATTERN.match(stripped):
            continue
        if DASH_NOT_CHECKBOX_PATTERN.match(stripped):
            errors.append(
                f"line {index + 1}: 无法解析的列表行 {stripped!r}；"
                "任务须使用 - [ ] / - [x] <编号> <标题> 格式。"
            )
    return errors


def collect_verify_errors(
    lines: list[str], items: list[PlanItem], *, allow_parent: bool = False
) -> list[str]:
    errors = collect_task_section_format_errors(lines)
    if not items:
        errors.append("任务区未找到可解析的 checklist 项（`- [ ]` / `- [x]`）。")
    for item in items:
        if (
            not allow_parent
            and item.state == "done"
            and has_unfinished_children(items, item)
        ):
            errors.append(
                f"line {item.line}: 已勾选但存在未完成子任务（id={item.item_id or 'n/a'}）。"
            )
        if (
            item.state == "todo"
            and has_descendant_checkboxes(items, item)
            and all_descendants_done(items, item)
        ):
            errors.append(
                f"line {item.line}: 全部子任务已完成，父项仍待办（id={item.item_id or 'n/a'}）；"
                "请勾选父项。"
            )
    return errors


def command_verify(args: argparse.Namespace, root: Path, module: str) -> None:
    path = plan_path(root, module)
    lines = read_lines(path)
    items = parse_items(lines)
    errors = collect_verify_errors(lines, items, allow_parent=args.allow_parent)
    todo = [item for item in items if item.state == "todo"]
    done = [item for item in items if item.state == "done"]

    if args.format == "json":
        payload = {
            "ok": not errors,
            "path": str(path),
            "done": len(done),
            "todo": len(todo),
            "allow_parent": args.allow_parent,
            "errors": errors,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        if errors:
            raise SystemExit(2)
        return

    if errors:
        for message in errors:
            print(f"[ERROR] {message}", file=sys.stderr)
        raise SystemExit(2)

    print(f"[OK] {path}: {len(done)} done, {len(todo)} todo")


def command_set(args: argparse.Namespace, root: Path, module: str) -> None:
    path = plan_path(root, module)
    lines = read_lines(path)
    items = parse_items(lines)
    item = select_item(items, args.item, args.line)

    if args.state == item.state:
        print(f"[OK] No change: test_plan line {item.line} already {args.state}.")
        return

    if args.state == "done" and not args.allow_parent and has_unfinished_children(items, item):
        fail("目标项存在未完成子任务；请先完成子任务，或确认后加 --allow-parent。")

    lines[item.index] = update_line_state(lines[item.index], args.state)
    write_lines(path, lines)
    print(f"[OK] Updated {path}: line {item.line} -> {args.state}")


def main() -> None:
    args = parse_args()
    module = args.module.strip()
    validate_module_name(module)
    root = resolve_repo_root(args.repo_root)

    if args.command == "status":
        command_status(args, root, module)
    elif args.command == "set":
        command_set(args, root, module)
    elif args.command == "verify":
        command_verify(args, root, module)
    else:
        fail(f"未知命令：{args.command}")


if __name__ == "__main__":
    main()
