#!/usr/bin/env python3
"""Validate doc/<module>/requirements.md structure and analysis type marker."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


REQUIRED_SECTIONS = [
    "### 4.1 初始需求澄清",
    "### 4.4 功能影响列表",
    "### 4.5 系统分析列表",
    "### 4.6 协作讨论记录",
]

MODULE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
ANALYSIS_TYPES = {"Bug", "Feature", "Refactor", "Docs"}
OUTPUT_FILENAME = "requirements.md"
OVERVIEW_SUBSECTION_HEADING = "#### 4.1.1 需求概述"
IMPACT_SECTION_HEADING = "### 4.4 功能影响列表"
SYSTEM_SECTION_HEADING = "### 4.5 系统分析列表"
DISCUSSION_SECTION_HEADING = "### 4.6 协作讨论记录"
IMPACT_TABLE_KEY_HEADER = "功能模块"
SR_TABLE_KEY_HEADER = "需求编号"
NFR_SECTION_HEADING = "### 4.3 非功能性需求分析（可选）"
HTML_COMMENT_PATTERN = re.compile(r"<!--.*?-->", re.DOTALL)
FENCED_CODE_BLOCK_PATTERN = re.compile(r"(?ms)^```.*?^```[ \t]*\n?")
PLACEHOLDER_COMMENT_INNERS = {
    "主题",
    "原因",
    "答复摘要",
    "用户答复摘要",
    "已确认 / 待确认",
    "已确认 / 待确认 / 不适用",
    "A / B",
    "A / B 或文字说明",
    "模块名称",
    "高/中/低",
    "具体影响描述",
    "需求描述",
    "来源章节",
    "待确认/已确认",
}
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
PENDING_ITEMS_HEADING_PATTERN = re.compile(
    r"(?mi)^\s{0,3}(?:[-*]\s*)?(?:\*\*)?待确认事项(?:\*\*)?\s*[：:]\s*$"
)
PENDING_Q_ITEM_PATTERN = re.compile(
    r"(?mi)^\s*-\s+\[ \]\s*\*\*Q-\d+\*\*"
    r"|"
    r"^\s*[-*]\s*(?:\*\*)?Q(?:-\d+|\d+)(?:\*\*)?\s*[：:]"
)
MECHANISM_AC_KEYWORDS = (
    "超时",
    "取消",
    "输出保真",
    "进程",
    "进程树",
    "持久化",
    "persisted-output",
    "stdout",
    "stderr",
    "sudo",
    "交互",
    "非交互",
    "drain",
    "缓冲",
)
LAYER_MARKERS = ("L0", "L1", "L2", "L3")
INFRA_MARKERS = ("IO", "subprocess", "进程", "基础设施")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate doc/<module>/requirements.md under --repo-root."
    )
    parser.add_argument(
        "--module",
        required=True,
        help="Module name under doc/, e.g. user or order-center.",
    )
    parser.add_argument(
        "--type",
        required=True,
        choices=sorted(ANALYSIS_TYPES),
        help="Analysis type: Bug, Feature, Refactor, or Docs.",
    )
    parser.add_argument(
        "--repo-root",
        required=True,
        help="Repository root containing doc/ (project root with doc/<module>/).",
    )
    parser.add_argument(
        "--strict-layer",
        action="store_true",
        help="Enable strict validation for layer-alignment checks.",
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


def _find_all_heading_positions(content: str, heading: str) -> list[int]:
    positions: list[int] = []
    cursor = 0
    while True:
        pos = find_heading_pos(content, heading, cursor)
        if pos is None:
            break
        positions.append(pos)
        cursor = line_end_after(content, pos)
    return positions


def validate_required_sections(content: str) -> None:
    positions: list[int] = []
    for section in REQUIRED_SECTIONS:
        found = _find_all_heading_positions(content, section)
        if not found:
            fail(
                f"缺少必需章节或章节顺序错误：{section}"
                "（须为独立行标题，不得仅在正文中提及）"
            )
        if len(found) > 1:
            fail(f"章节标题重复：{section}（全文只能出现一次）")
        positions.append(found[0])
    for index in range(len(positions) - 1):
        if positions[index] >= positions[index + 1]:
            fail(
                f"缺少必需章节或章节顺序错误：{REQUIRED_SECTIONS[index + 1]}"
                "（须按 4.1 → 4.4 → 4.5 → 4.6 顺序排列）"
            )


UTF8_BOM = b"\xef\xbb\xbf"


def read_utf8_markdown(path: Path, root: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith(UTF8_BOM):
        fail(
            f"文档含 UTF-8 BOM：{path.relative_to(root)}。"
            "请用 UTF-8 无 BOM 重写（勿用 PowerShell 5 的 Set-Content -Encoding utf8）。"
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        fail(
            f"文档不是有效 UTF-8：{path.relative_to(root)}。"
            f"（{exc.reason}，offset {exc.start}）"
        )
    return text.strip()


def _strip_html_comments(text: str) -> str:
    return HTML_COMMENT_PATTERN.sub("", text)


def _strip_non_content_blocks(text: str) -> str:
    without_code = FENCED_CODE_BLOCK_PATTERN.sub("", text)
    lines = [line for line in without_code.splitlines() if not line.lstrip().startswith(">")]
    return "\n".join(lines)


def validate_analysis_type_marker(content: str, analysis_type: str) -> None:
    marker = f"本次分析类型：{analysis_type}"
    overview_body = extract_section_body(content, OVERVIEW_SUBSECTION_HEADING)
    visible = _strip_html_comments(overview_body)
    if marker not in visible:
        fail(
            f"{OVERVIEW_SUBSECTION_HEADING} 须包含可见的分析类型标记：{marker}"
            "（不可仅写在 HTML 注释中；须与 --type 一致）"
        )


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
        return not inner or inner in PLACEHOLDER_COMMENT_INNERS
    return False


def _is_placeholder_row(cells: list[str]) -> bool:
    return bool(cells) and all(_is_placeholder_cell(cell) for cell in cells)


def _collect_filled_table_rows(
    text: str,
    key_header: str,
    required_filled_headers: tuple[str, ...],
) -> list[list[str]]:
    data_rows: list[list[str]] = []
    for headers, rows in _iter_markdown_tables(text):
        if key_header not in headers:
            continue
        indices = []
        for header in required_filled_headers:
            if header not in headers:
                indices = []
                break
            indices.append(headers.index(header))
        if not indices:
            continue
        for row in rows:
            if _is_placeholder_row(row):
                continue
            if all(
                index < len(row) and not _is_placeholder_cell(row[index])
                for index in indices
            ):
                data_rows.append(row)
    return data_rows


def validate_impact_list_section(content: str) -> None:
    section_body = extract_section_body(content, IMPACT_SECTION_HEADING)
    data_rows = _collect_filled_table_rows(
        section_body,
        IMPACT_TABLE_KEY_HEADER,
        ("功能模块", "影响程度", "影响描述"),
    )
    if not data_rows:
        fail(
            f"{IMPACT_SECTION_HEADING} 须包含功能影响表，"
            "并填写至少一条实际影响记录（不可保留模板占位行）。"
        )


def validate_system_requirements_section(content: str) -> None:
    section_body = extract_section_body(content, SYSTEM_SECTION_HEADING)
    data_rows = _collect_filled_table_rows(
        section_body,
        SR_TABLE_KEY_HEADER,
        ("需求编号", "需求描述"),
    )
    if not data_rows:
        fail(
            f"{SYSTEM_SECTION_HEADING} 须在 4.5.2 最终系统需求列表中"
            "填写至少一条实际 SR（不可保留模板占位行）。"
        )


def _extract_discussion_item_blocks(body: str) -> list[tuple[str, bool, str]]:
    """Return (qid, checked, block_text) for each §4.6 checklist item."""
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
    section_body = extract_section_body(content, DISCUSSION_SECTION_HEADING)
    validate_collaboration_discussion(section_body, DISCUSSION_SECTION_HEADING)


def _slice_without_discussion_section(content: str) -> str:
    pos = find_heading_pos(content, DISCUSSION_SECTION_HEADING)
    if pos is None:
        return content
    body_start = line_end_after(content, pos)
    body_end = _next_heading_pos_at_or_above(content, body_start, 3)
    return content[:pos] + content[body_end:]


def validate_no_pending_items_outside_discussion(content: str) -> None:
    visible = _strip_non_content_blocks(
        _strip_html_comments(_slice_without_discussion_section(content))
    )
    if PENDING_ITEMS_HEADING_PATTERN.search(visible) or PENDING_Q_ITEM_PATTERN.search(visible):
        fail(
            "检测到「待确认事项/Qn」出现在 4.6 之外。"
            "这类取舍必须先由 Leader 向用户逐项确认，再在 §4.6 勾选 `- [x]` 并回填「用户决定」，"
            "并移除其它章节中的未闭环待确认清单。"
        )


def _section_exists(content: str, heading: str) -> bool:
    return find_heading_pos(content, heading) is not None


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def validate_layer_alignment(
    content: str,
    analysis_type: str,
    strict_layer: bool,
    warnings: list[str],
) -> None:
    if analysis_type != "Bug":
        return

    system_section = extract_section_body(content, SYSTEM_SECTION_HEADING)
    ac_section = extract_section_body(content, "### 3.3 验收标准")

    has_layer_marker = _contains_any(system_section, LAYER_MARKERS)
    has_evidence_marker = "证据来源" in system_section
    has_infra_marker = _contains_any(system_section, INFRA_MARKERS)
    has_mechanism_ac = _contains_any(ac_section, MECHANISM_AC_KEYWORDS) or (
        "不涉及机制 AC" in ac_section
    )
    has_agent_shell_table = _section_exists(content, NFR_SECTION_HEADING) and (
        "Agent/Shell 场景检查表" in content
    )

    if not has_layer_marker:
        message = "Bug 类型建议在 §4.5.2 中标注根因层级（L0/L1/L2/L3）。"
        if strict_layer:
            fail(message)
        warn(message, warnings)

    if not has_evidence_marker:
        message = "Bug 类型建议在 §4.5.2 中填写证据来源列（代码路径/日志）。"
        if strict_layer:
            fail(message)
        warn(message, warnings)

    if not has_mechanism_ac:
        message = "Bug 类型建议在 §3.3 包含至少一条机制行为 AC（或显式写“不涉及机制 AC”）。"
        if strict_layer:
            fail(message)
        warn(message, warnings)

    if has_infra_marker and not has_agent_shell_table:
        message = "检测到基础设施关键词，建议在 §4.3.3 填写 Agent/Shell 场景检查表。"
        if strict_layer:
            fail(message)
        warn(message, warnings)


def main() -> None:
    args = parse_args()
    module_name = args.module.strip()
    analysis_type = args.type
    validate_module_name(module_name)

    root = resolve_repo_root(args.repo_root)
    out_path = root / "doc" / module_name / OUTPUT_FILENAME

    if not out_path.is_file():
        fail(f"未找到需求文档：{out_path.relative_to(root)}。请先写入 doc/<module>/requirements.md。")

    content = read_utf8_markdown(out_path, root)
    if not content:
        fail(f"需求文档为空：{out_path.relative_to(root)}")

    warnings: list[str] = []

    validate_required_sections(content)
    validate_analysis_type_marker(content, analysis_type)
    validate_impact_list_section(content)
    validate_system_requirements_section(content)
    validate_collaboration_section(content)
    validate_no_pending_items_outside_discussion(content)
    validate_layer_alignment(content, analysis_type, args.strict_layer, warnings)

    print(f"[OK] Validated {out_path.relative_to(root)}")
    for item in warnings:
        print(f"[WARN] {item}")


if __name__ == "__main__":
    main()
