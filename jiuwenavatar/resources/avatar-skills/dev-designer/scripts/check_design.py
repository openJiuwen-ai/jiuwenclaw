#!/usr/bin/env python3
"""Validate doc/<module>/design.md structure and upstream requirements.md."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


REQUIRED_SECTIONS = [
    "# 设计文档",
    "## 概述",
    "## 架构",
    "## 组件和接口",
    "## 数据模型",
    "## 正确性属性",
    "## 错误处理",
    "## 测试策略",
    "## 实现注意事项",
]

MODULE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
OUTPUT_FILENAME = "design.md"
REQUIREMENTS_FILENAME = "requirements.md"
OVERVIEW_HEADING = "## 概述"
DISCUSSION_SECTION_HEADING = "### 协作讨论记录"
MECHANISM_DESIGN_HEADING = "## 机制设计（按需）"
IMPLEMENTATION_NOTES_HEADING = "## 实现注意事项"
MIN_DISCUSSION_ITEMS = 2
MAX_DISCUSSION_ITEMS = 3
DISCUSSION_ITEM_HEADING_PATTERN = re.compile(
    r"^\s*-\s+\[(?P<checked>[ xX])\]\s+\*\*(?P<qid>Q-\d+)\*\*\s*(?P<title>.*)$",
    re.MULTILINE,
)
USER_DECISION_PATTERN = re.compile(
    r"^\s+-\s+\*\*用户决定\*\*[：:][ \t]*(?P<decision>.*?)[ \t]*$",
    re.MULTILINE,
)
DISCUSSION_OPTION_PATTERN = re.compile(
    r"^\s+-\s+\*\*选项\*\*[：:][ \t]*(?P<value>.*?)[ \t]*$",
    re.MULTILINE,
)
DISCUSSION_DEFAULT_PATTERN = re.compile(
    r"^\s+-\s+\*\*建议默认\*\*[：:][ \t]*(?P<value>.*?)[ \t]*$",
    re.MULTILINE,
)
# 仅当「用户决定」整体就是一个占位/默认词时才视为 Agent 代填；
# 允许用户答复中出现「默认」等字样（例如「不采用默认决策，选 B」）。
AGENT_DEFAULT_DECISION_PLACEHOLDERS = {
    "默认决策",
    "采用默认决策",
    "按默认决策",
    "待填写",
    "待确认",
    "tbd",
    "n/a",
}
TABLE_ROW_PATTERN = re.compile(r"^\s*\|")
TABLE_SEPARATOR_PATTERN = re.compile(r"^\s*\|[\s\-:|]+\|\s*$")
PLACEHOLDER_CELL_PATTERN = re.compile(r"^\s*(?:<!--.*?-->|-+|—+|…|\.\.\.)?\s*$", re.DOTALL)
HTML_COMMENT_PATTERN = re.compile(r"<!--.*?-->", re.DOTALL)
FENCED_CODE_BLOCK_PATTERN = re.compile(r"(?ms)^```.*?^```[ \t]*\n?")
PENDING_ITEMS_HEADING_PATTERN = re.compile(
    r"(?mi)^\s{0,3}(?:[-*]\s*)?(?:\*\*)?待确认事项(?:\*\*)?\s*[：:]\s*$"
)
PENDING_Q_ITEM_PATTERN = re.compile(
    r"(?mi)^\s*-\s+\[ \]\s*\*\*Q-\d+\*\*"
    r"|"
    r"^\s*[-*]\s*(?:\*\*)?Q(?:-\d+|\d+)(?:\*\*)?\s*[：:]"
)
INFRA_KEYWORDS = ("L2", "L3", "IO", "subprocess", "进程", "基础设施")
PATH_LINE_PATTERN = re.compile(r"`[^`/\\]+(?:[/\\][^`/\\]+)+`")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate doc/<module>/design.md under --repo-root."
    )
    parser.add_argument(
        "--module",
        required=True,
        help="Module name under doc/, e.g. user or order-center.",
    )
    parser.add_argument(
        "--repo-root",
        required=True,
        help="Repository root containing doc/ (project root with doc/<module>/).",
    )
    parser.add_argument(
        "--strict-layer",
        action="store_true",
        help="Enable strict layer-alignment checks for infra-related design.",
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


def validate_required_sections(content: str) -> None:
    cursor = 0
    for section in REQUIRED_SECTIONS:
        pos = find_heading_pos(content, section, cursor)
        if pos is None:
            fail(
                f"缺少必需章节或章节顺序错误：{section}"
                "（须为独立行标题，不得仅在正文中提及；落盘格式见 references/design_template.md）"
            )
        cursor = line_end_after(content, pos)


def _next_heading_pos_at_or_above(content: str, start: int, max_level: int) -> int:
    for match in re.finditer(r"(?m)^(#{1,6})\s+\S", content[start:]):
        if len(match.group(1)) <= max_level:
            return start + match.start()
    return len(content)


def extract_section_body(content: str, heading: str, search_start: int = 0) -> str:
    pos = find_heading_pos(content, heading, search_start)
    if pos is None:
        fail(f"缺少必需章节：{heading}")
    level = _heading_level(heading)
    body_start = line_end_after(content, pos)
    body_end = _next_heading_pos_at_or_above(content, body_start, level)
    return content[body_start:body_end]


def _split_table_row(line: str) -> list[str]:
    row = line.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|"):
        row = row[:-1]
    return [cell.strip() for cell in row.split("|")]


def _parse_markdown_table(table_lines: list[str]) -> tuple[list[str], list[list[str]]] | None:
    if len(table_lines) < 2:
        return None
    if not TABLE_SEPARATOR_PATTERN.match(table_lines[1]):
        return None
    headers = _split_table_row(table_lines[0])
    rows = [_split_table_row(line) for line in table_lines[2:]]
    return headers, rows


def _iter_markdown_tables(text: str) -> list[tuple[list[str], list[list[str]]]]:
    tables: list[tuple[list[str], list[list[str]]]] = []
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        if not TABLE_ROW_PATTERN.match(lines[index]):
            index += 1
            continue
        block: list[str] = []
        while index < len(lines) and TABLE_ROW_PATTERN.match(lines[index]):
            block.append(lines[index])
            index += 1
        parsed = _parse_markdown_table(block)
        if parsed is not None:
            tables.append(parsed)
    return tables


def _is_placeholder_cell(cell: str) -> bool:
    value = cell.strip()
    if not value:
        return True
    if PLACEHOLDER_CELL_PATTERN.fullmatch(value):
        return True
    if value.startswith("<!--") and value.endswith("-->"):
        inner = value[4:-3].strip()
        return not inner or inner in {
            "主题",
            "原因",
            "答复摘要",
            "用户答复摘要",
            "已确认 / 待确认",
            "已确认 / 待确认 / 不适用",
            "A / B",
        }
    return False


def _is_placeholder_row(cells: list[str]) -> bool:
    return bool(cells) and all(_is_placeholder_cell(cell) for cell in cells)


def _extract_discussion_item_blocks(body: str) -> list[tuple[str, bool, str]]:
    matches = list(DISCUSSION_ITEM_HEADING_PATTERN.finditer(body))
    if not matches:
        return []
    blocks: list[tuple[str, bool, str]] = []
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        qid = match.group("qid")
        checked = match.group("checked").lower() == "x"
        blocks.append((qid, checked, body[start:end]))
    return blocks


def _extract_user_decision(block_text: str) -> str:
    match = USER_DECISION_PATTERN.search(block_text)
    if match is None:
        return ""
    return match.group("decision").strip()


def _is_agent_default_decision(decision: str) -> bool:
    normalized = decision.strip()
    if not normalized:
        return True
    if _is_placeholder_cell(normalized):
        return True
    return normalized.casefold() in AGENT_DEFAULT_DECISION_PLACEHOLDERS


def _is_placeholder_field(value: str) -> bool:
    normalized = value.strip()
    if not normalized:
        return True
    if _is_placeholder_cell(normalized):
        return True
    # 模板占位（如「A. … / B. …」）未填写时仍含省略号，视为未填。
    return "…" in normalized


def _validate_discussion_item_fields(qid: str, block_text: str, section_ref: str) -> None:
    for field_name, pattern in (
        ("选项", DISCUSSION_OPTION_PATTERN),
        ("建议默认", DISCUSSION_DEFAULT_PATTERN),
    ):
        match = pattern.search(block_text)
        if match is None or _is_placeholder_field(match.group("value")):
            fail(
                f"{section_ref} 讨论项 {qid} 缺少有效「{field_name}」；"
                "每条核心讨论项须给出可选项与建议默认。"
            )


def validate_collaboration_discussion(section_body: str, section_ref: str) -> None:
    body = section_body.strip()
    if not body:
        fail(
            f"{section_ref} 为空；须列出 {MIN_DISCUSSION_ITEMS}–{MAX_DISCUSSION_ITEMS} 条"
            " `- [ ] **Q-xxx**` 核心讨论项。"
        )

    discussion_items = _extract_discussion_item_blocks(body)
    item_count = len(discussion_items)
    if item_count < MIN_DISCUSSION_ITEMS or item_count > MAX_DISCUSSION_ITEMS:
        fail(
            f"{section_ref} 须包含 {MIN_DISCUSSION_ITEMS}–{MAX_DISCUSSION_ITEMS} 条核心讨论项，"
            f"当前为 {item_count} 条。"
        )

    for qid, checked, block_text in discussion_items:
        _validate_discussion_item_fields(qid, block_text, section_ref)

        if not checked:
            fail(
                f"{section_ref} 存在未决讨论项 {qid}："
                "仍为 `- [ ]`，须经 Leader 向用户澄清并勾选 `- [x]` 后再定稿。"
            )

        decision = _extract_user_decision(block_text)
        if _is_agent_default_decision(decision):
            fail(
                f"{section_ref} 讨论项 {qid} 已勾选但缺少有效「用户决定」，"
                "不允许 Agent 以「默认决策」代填；须回填 Leader 转述的用户答复。"
            )


def validate_collaboration_section(content: str) -> None:
    overview_pos = find_heading_pos(content, OVERVIEW_HEADING)
    if overview_pos is None:
        fail(f"缺少必需章节：{OVERVIEW_HEADING}")
    overview_body = extract_section_body(content, OVERVIEW_HEADING)
    discussion_pos = find_heading_pos(overview_body, DISCUSSION_SECTION_HEADING)
    if discussion_pos is None:
        fail(
            f"{OVERVIEW_HEADING} 下缺少 {DISCUSSION_SECTION_HEADING}；"
            f"须列出 {MIN_DISCUSSION_ITEMS}–{MAX_DISCUSSION_ITEMS} 条 `- [ ] **Q-xxx**` 核心讨论项。"
        )
    discussion_body = extract_section_body(overview_body, DISCUSSION_SECTION_HEADING)
    validate_collaboration_discussion(
        discussion_body,
        f"{OVERVIEW_HEADING} → {DISCUSSION_SECTION_HEADING}",
    )


def _strip_html_comments(text: str) -> str:
    return HTML_COMMENT_PATTERN.sub("", text)


def _strip_non_content_blocks(text: str) -> str:
    without_code = FENCED_CODE_BLOCK_PATTERN.sub("", text)
    lines = [line for line in without_code.splitlines() if not line.lstrip().startswith(">")]
    return "\n".join(lines)


def _slice_without_overview_discussion(content: str) -> str:
    overview_pos = find_heading_pos(content, OVERVIEW_HEADING)
    if overview_pos is None:
        return content
    overview_body = extract_section_body(content, OVERVIEW_HEADING)
    discussion_pos = find_heading_pos(overview_body, DISCUSSION_SECTION_HEADING)
    if discussion_pos is None:
        return content
    body_start = line_end_after(overview_body, discussion_pos)
    body_end = _next_heading_pos_at_or_above(overview_body, body_start, 3)
    trimmed_overview = overview_body[:discussion_pos] + overview_body[body_end:]
    overview_line_end = line_end_after(content, overview_pos)
    overview_end = _next_heading_pos_at_or_above(content, overview_line_end, 2)
    return content[:overview_line_end] + trimmed_overview + content[overview_end:]


def validate_no_pending_items_outside_discussion(content: str) -> None:
    visible = _strip_non_content_blocks(
        _strip_html_comments(_slice_without_overview_discussion(content))
    )
    if PENDING_ITEMS_HEADING_PATTERN.search(visible) or PENDING_Q_ITEM_PATTERN.search(visible):
        fail(
            "检测到「待确认事项/Qn」出现在「概述 → 协作讨论记录」之外。"
            "这类取舍必须先由 Leader 向用户逐项确认，再在 §协作讨论记录勾选 `- [x]` 并回填「用户决定」。"
        )


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def validate_layer_alignment(
    requirements_content: str,
    design_content: str,
    strict_layer: bool,
    warnings: list[str],
) -> None:
    infra_related = _contains_any(requirements_content, INFRA_KEYWORDS)
    if not infra_related:
        return

    if find_heading_pos(design_content, MECHANISM_DESIGN_HEADING) is None:
        message = (
            "requirements 疑似涉及基础设施层（L2/L3），design 缺少 `## 机制设计（按需）`。"
        )
        if strict_layer:
            fail(message)
        warn(message, warnings)
    else:
        mechanism_body = extract_section_body(design_content, MECHANISM_DESIGN_HEADING).strip()
        if not mechanism_body or mechanism_body == "<Bug/基础设施类必填：IO 模型、取消/超时语义、大输出策略、进程清理策略；不涉及可写“不涉及”>":
            message = "检测到基础设施需求，但 `## 机制设计（按需）` 仍为占位或空内容。"
            if strict_layer:
                fail(message)
            warn(message, warnings)

    impl_body = extract_section_body(design_content, IMPLEMENTATION_NOTES_HEADING)
    if "### 文件修改清单" not in impl_body or not PATH_LINE_PATTERN.search(impl_body):
        message = "基础设施相关设计建议在 `## 实现注意事项` 中补充带路径的“文件修改清单”。"
        if strict_layer:
            fail(message)
        warn(message, warnings)


def main() -> None:
    args = parse_args()
    module_name = args.module.strip()
    validate_module_name(module_name)

    root = resolve_repo_root(args.repo_root)
    doc_dir = root / "doc" / module_name
    req_path = doc_dir / REQUIREMENTS_FILENAME
    out_path = doc_dir / OUTPUT_FILENAME

    if not req_path.is_file():
        fail(f"未找到 requirements.md：{req_path.relative_to(root)}。请先为同一模块生成需求文档。")

    if not out_path.is_file():
        fail(f"未找到设计文档：{out_path.relative_to(root)}。请先写入 doc/<module>/design.md。")

    content = read_utf8_markdown(out_path, root)
    if not content:
        fail(f"设计文档为空：{out_path.relative_to(root)}")
    requirements_content = read_utf8_markdown(req_path, root)

    warnings: list[str] = []

    validate_required_sections(content)
    validate_collaboration_section(content)
    validate_no_pending_items_outside_discussion(content)
    validate_layer_alignment(requirements_content, content, args.strict_layer, warnings)

    print(f"[OK] Validated {out_path.relative_to(root)}")
    for item in warnings:
        print(f"[WARN] {item}")


if __name__ == "__main__":
    main()
