#!/usr/bin/env python3
"""Validate doc/<module>/dev_plan.md or test_plan.md structure and checklist format."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


MODULE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
# 与 skills/dev-coder|dev-tester/scripts/*_plan_check.py 保持一致，确保 G3 通过后可被下游解析
CHECKBOX_PATTERN = re.compile(r"^(?P<indent>\s*)-\s+\[(?P<mark>[ xX])\]\s+(?P<text>.*)$")
DASH_NOT_CHECKBOX_PATTERN = re.compile(r"^\s*-\s+(?!\[)")
PATH_TOKEN_PATTERN = re.compile(r"`([^`/\\]+(?:[/\\][^`/\\]+)+)`")
PG_PATTERN = re.compile(r"^-\s*(PG-\d+):\s*items\s*\[([^\]]*)\](.*)$")

PLAN_CONFIG = {
    "dev": {
        "filename": "dev_plan.md",
        "sections": ["# 开发计划", "## 概述", "## 开发任务", "## 注意事项"],
        "task_section": "## 开发任务",
        "required_top_level_titles": [
            "项目结构和依赖",
            "数据模型和枚举",
            "基础设施层",
            "核心服务层",
            "业务逻辑层",
            "API 或对外交互层",
            "输入验证",
            "错误处理",
            "日志、监控和配置",
            "开发检查点",
            "最终交付检查点",
        ],
    },
    "test": {
        "filename": "test_plan.md",
        "sections": ["# 测试计划", "## 概述", "## 测试任务", "## 注意事项"],
        "task_section": "## 测试任务",
        "required_top_level_titles": [
            "测试环境和测试数据",
            "单元测试",
            "属性测试",
            "集成测试",
            "API 或端到端测试",
            "异常、边界和回归测试",
            "机制测试",
            "日志、监控和可观测性验证",
            "测试检查点",
            "最终验收检查点",
        ],
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate doc/<module>/dev_plan.md or test_plan.md under --repo-root."
    )
    parser.add_argument(
        "--module",
        required=True,
        help="Module name under doc/, e.g. user or order-center.",
    )
    parser.add_argument(
        "--plan",
        required=True,
        choices=sorted(PLAN_CONFIG),
        help="Plan type to validate: dev or test.",
    )
    parser.add_argument(
        "--repo-root",
        required=True,
        help="Repository root containing doc/ (project root with doc/<module>/).",
    )
    return parser.parse_args()


def fail(message: str) -> None:
    print(f"[ERROR] {message}", file=sys.stderr)
    raise SystemExit(2)


def warn(message: str, warnings: list[str]) -> None:
    warnings.append(message)


def resolve_repo_root(repo_root: str) -> Path:
    root = Path(repo_root.strip()).expanduser().resolve()
    if not root.is_dir():
        fail(f"--repo-root 不是有效目录：{root}")
    return root


def validate_module_name(module_name: str) -> None:
    if not MODULE_NAME_PATTERN.fullmatch(module_name):
        fail("模块名只能包含英文字母、数字、下划线或短横线，例如：user、order-center。")


def validate_required_inputs(doc_dir: Path, root: Path) -> None:
    requirements_path = doc_dir / "requirements.md"
    design_path = doc_dir / "design.md"

    if not requirements_path.is_file():
        fail(f"未找到 requirements.md：{requirements_path.relative_to(root)}。请先为同一模块生成需求文档。")
    if not design_path.is_file():
        fail(f"未找到 design.md：{design_path.relative_to(root)}。请先为同一模块生成设计文档。")


def _heading_level(heading: str) -> int:
    level = 0
    for ch in heading:
        if ch == "#":
            level += 1
        else:
            break
    return level if level > 0 and level < len(heading) and heading[level] == " " else 0


def find_heading_pos(content: str, heading: str, start: int = 0) -> int | None:
    """Line-anchored heading position; H1 allows trailing ：title."""
    level = _heading_level(heading)
    if level == 0:
        return None
    title = heading[level:].strip()
    marker = "#" * level
    if level == 1:
        pattern = rf"(?m)^{re.escape(marker)}\s*{re.escape(title)}(?:\s*[：:][^\n]*)?\s*$"
    else:
        pattern = rf"(?m)^{re.escape(marker)}\s*{re.escape(title)}\s*$"
    match = re.search(pattern, content[start:])
    if match is None:
        return None
    return start + match.start()


def line_end_after(content: str, pos: int) -> int:
    newline = content.find("\n", pos)
    return len(content) if newline == -1 else newline + 1


UTF8_BOM = b"\xef\xbb\xbf"


def read_utf8_markdown(path: Path, root: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith(UTF8_BOM):
        fail(
            f"文档含 UTF-8 BOM：{path.relative_to(root)}。"
            "请用 UTF-8 无 BOM 重写（勿用 PowerShell 5 的 Set-Content -Encoding utf8）。"
        )
    return raw.decode("utf-8").strip()


def validate_required_sections(content: str, sections: list[str]) -> None:
    cursor = 0
    for section in sections:
        pos = find_heading_pos(content, section, cursor)
        if pos is None:
            fail(
                f"缺少必需章节或章节顺序错误：{section}"
                "（须为独立行标题，不得仅在正文中提及；结构见 references/*_plan_template.md）"
            )
        cursor = line_end_after(content, pos)


def extract_h2_section(content: str, heading: str) -> str:
    pos = find_heading_pos(content, heading, 0)
    if pos is None:
        return ""
    body_start = line_end_after(content, pos)
    tail = content[body_start:]
    match = re.search(r"(?m)^#{1,2}\s", tail)
    if match:
        return tail[: match.start()]
    return tail


def validate_unfinished_checkboxes(content: str, task_heading: str) -> None:
    section = extract_h2_section(content, task_heading)
    if not section.strip():
        return
    if re.search(r"(?im)^\s*-\s*\[x\]\s+", section):
        fail(
            f"{task_heading} 中需要完成的任务必须使用 [ ]，不得使用 [x]。"
            "（其它章节中的示例 checklist 不受此限制）"
        )


def validate_task_heading_for_plan_check(content: str, task_heading: str) -> None:
    """下游 plan_check 用 line.strip() == heading 定位任务区，标题须精确匹配。"""
    if not any(line.strip() == task_heading for line in content.splitlines()):
        fail(
            f"须存在独立行标题且 strip 后精确为 {task_heading!r}，"
            "以便下游 coder_plan_check / tester_plan_check 定位任务区（勿在标题后追加文字或多余空格）。"
        )


def validate_parseable_task_checkboxes(content: str, task_heading: str) -> None:
    """任务区须含至少一条可解析的 - [ ] 行；以 - 开头但非 checklist 的行视为格式错误。"""
    section = extract_h2_section(content, task_heading)
    if not section.strip():
        fail(f"{task_heading} 不能为空，且至少包含一条 checklist 项（- [ ] ...）。")

    checkbox_count = 0
    for line in section.splitlines():
        stripped = line.rstrip("\n\r")
        if not stripped.strip():
            continue
        if CHECKBOX_PATTERN.match(stripped):
            checkbox_count += 1
            continue
        if DASH_NOT_CHECKBOX_PATTERN.match(stripped):
            fail(
                f"{task_heading} 中存在无法被 plan_check 解析的列表行：{stripped!r}。"
                "任务须使用 - [ ] <编号> <标题> 格式（属性块可用 - [ ] **属性 N: ...**）。"
            )

    if checkbox_count == 0:
        fail(
            f"{task_heading} 中未找到可解析的 checklist 项（须为 - [ ] ...）。"
            "下游 plan_check 脚本将无法查询或勾选任务。"
        )


def validate_required_task_categories(
    content: str, task_heading: str, required_titles: list[str]
) -> None:
    section = extract_h2_section(content, task_heading)
    normalized_titles: list[str] = []
    for line in section.splitlines():
        match = re.match(r"^-\s+\[\s\]\s+(.*)$", line.strip())
        if not match:
            continue
        title = match.group(1).strip()
        # 兼容历史文档：允许不同编号格式（如 1. / 1) / 无编号）与可选后缀。
        title = re.sub(r"^\d+\s*[\.\)]\s*", "", title)
        title = re.sub(r"\s+\*可选\*\s*$", "", title)
        normalized_titles.append(title.strip())

    for idx, required_title in enumerate(required_titles, start=1):
        if not any(
            title == required_title or title.startswith(f"{required_title} ")
            for title in normalized_titles
        ):
            fail(
                f"{task_heading} 缺少模板必需顶层任务：`{required_title}`（建议示例：`- [ ] {idx}. {required_title}`）。"
                "可使用不同编号格式，但需保留该分类语义。"
            )


def _extract_backticked_paths(text: str) -> set[str]:
    paths: set[str] = set()
    for match in PATH_TOKEN_PATTERN.finditer(text):
        paths.add(match.group(1).replace("\\", "/"))
    return paths


def _extract_numbered_checkbox_titles(section: str) -> list[tuple[int, str]]:
    items: list[tuple[int, str]] = []
    for line in section.splitlines():
        match = re.match(r"^-\s+\[\s\]\s+(\d+)\.\s+(.*)$", line.strip())
        if not match:
            continue
        items.append((int(match.group(1)), match.group(2).strip()))
    return items


def _extract_plan_item_ids(section: str) -> set[str]:
    ids: set[str] = set()
    for line in section.splitlines():
        match = CHECKBOX_PATTERN.match(line.rstrip())
        if not match:
            continue
        text = match.group("text").strip()
        item_match = re.match(r"^((?:\d+(?:\.\d+)*|[A-Za-z]+-\d+))\.?\s+", text)
        if item_match:
            ids.add(item_match.group(1))
    return ids


def _split_pg_items(raw: str) -> list[str]:
    return [item.strip().strip("\"'") for item in raw.split(",") if item.strip()]


def validate_parallel_groups(
    content: str,
    plan_kind: str,
    task_heading: str,
    warnings: list[str],
) -> None:
    pg_heading = "## 可并行组（G4）" if plan_kind == "dev" else "## 可并行组（G5）"
    section = extract_h2_section(content, pg_heading)
    if not section.strip():
        warn(f"{pg_heading} 缺失；G4/G5 将按 serial 派发。", warnings)
        return
    if "无（serial）" in section or "无(serial)" in section:
        return

    task_ids = _extract_plan_item_ids(extract_h2_section(content, task_heading))
    seen_paths: dict[str, str] = {}
    pg_count = 0
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = PG_PATTERN.match(stripped)
        if match is None:
            if stripped.startswith("- PG-"):
                warn(f"{pg_heading} 中存在无法解析的 PG 行：{stripped}", warnings)
            continue
        pg_count += 1
        group_id, raw_items, rest = match.groups()
        items = _split_pg_items(raw_items)
        paths = [path.replace("\\", "/") for path in PATH_TOKEN_PATTERN.findall(rest)]
        if not items:
            warn(f"{group_id} 缺少 items，Leader 将降级 serial。", warnings)
        if not paths:
            warn(f"{group_id} 缺少 touch 路径，Leader 将降级 serial。", warnings)
        missing = [item for item in items if item not in task_ids]
        if missing:
            warn(f"{group_id} 引用了不存在的任务编号：{', '.join(missing)}。", warnings)
        for path in paths:
            owner = seen_paths.get(path)
            if owner is not None:
                warn(f"{path} 同时出现在 {owner} 与 {group_id}，Leader 将降级 serial。", warnings)
            seen_paths[path] = group_id
    if pg_count > 3:
        warn(f"{pg_heading} 声明 {pg_count} 个 PG，超过默认 max_shards=3。", warnings)


def validate_dev_plan_alignment(
    content: str,
    design_content: str,
    warnings: list[str],
) -> None:
    dev_section = extract_h2_section(content, "## 开发任务")
    design_paths = _extract_backticked_paths(design_content)
    dev_paths = _extract_backticked_paths(dev_section)

    if design_paths:
        covered = sum(1 for path in design_paths if path in dev_paths)
        ratio = covered / len(design_paths)
        if ratio < 0.8:
            warn(
                "dev_plan 对 design 文件清单的路径覆盖率低于 80%，建议补充任务映射。",
                warnings,
            )

    numbered = _extract_numbered_checkbox_titles(dev_section)
    infra_idx = next(
        (idx for idx, title in numbered if "基础设施层" in title),
        None,
    )
    tool_idx = next(
        (
            idx
            for idx, title in numbered
            if ("Tool" in title or "tool" in title or "API 或对外交互层" in title)
        ),
        None,
    )
    if infra_idx is not None and tool_idx is not None and infra_idx > tool_idx:
        warn("基础设施层任务建议排在 Tool/API 相关任务之前。", warnings)


def main() -> None:
    args = parse_args()
    module_name = args.module.strip()
    validate_module_name(module_name)

    root = resolve_repo_root(args.repo_root)
    doc_dir = root / "doc" / module_name
    validate_required_inputs(doc_dir, root)

    config = PLAN_CONFIG[args.plan]
    out_path = doc_dir / config["filename"]
    design_path = doc_dir / "design.md"

    if not out_path.is_file():
        fail(f"未找到计划文件：{out_path.relative_to(root)}。请先写入 doc/<module>/{config['filename']}。")

    content = read_utf8_markdown(out_path, root)
    if not content:
        fail(f"计划文件为空：{out_path.relative_to(root)}")

    warnings: list[str] = []

    validate_required_sections(content, config["sections"])
    validate_task_heading_for_plan_check(content, config["task_section"])
    validate_unfinished_checkboxes(content, config["task_section"])
    validate_parseable_task_checkboxes(content, config["task_section"])
    validate_required_task_categories(
        content, config["task_section"], config["required_top_level_titles"]
    )
    if args.plan == "dev":
        design_content = read_utf8_markdown(design_path, root)
        validate_dev_plan_alignment(content, design_content, warnings)
    validate_parallel_groups(content, args.plan, config["task_section"], warnings)

    print(f"[OK] Validated {out_path.relative_to(root)}")
    for item in warnings:
        print(f"[WARN] {item}")


if __name__ == "__main__":
    main()
