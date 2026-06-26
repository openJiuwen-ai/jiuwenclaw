from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openjiuwen.core.runner.callback import AbortError

from jiuwenclaw.agentserver.replan_agent.plan_node import PlanNode
from jiuwenclaw.agentserver.replan_agent.skill_codes.ppt.ppt_common import PptCommon
from jiuwenclaw.agentserver.replan_agent.skill_codes.ppt.utils.bash_utils import (
    BashExecError,
    cli_path,
    combined_output,
    quote_path,
    run_bash,
)

_PPT_DIR = str(Path(__file__).resolve().parent)

logger = logging.getLogger(__name__)


_PRESET_STYLE_IDS = {"business-classic", "tech-minimal", "elegant-narrative", "industrial-tech"}
_DEFAULT_GEN_RETRY_ROUND = 1
_DEFAULT_DENSITY_RETRY_ROUND = 1


def _looks_like_path(value: str) -> bool:
    return "/" in value or "\\" in value or value.endswith(".html")


_DESIGN_RULES_DIGEST = (
    "### 视觉与布局硬约束（精选 14 条）\n"
    "1. 容器：`.ppt-slide { width:1280px; height:720px; overflow:hidden; box-sizing:border-box }`\n"
    "2. 安全区：`.content-safe { width:1220px; height:660px; margin:30px auto }`，主要内容必须放在安全区内\n"
    "3. 三级字号：标题 36-48px / 副标题 24-28px / 正文 16-20px\n"
    "4. 图表类型：时序数据→柱状图(bar)；趋势数据→折线图(line)；对比数据→分组柱状图(grouped bar)；占比数据→饼图(pie)；禁止混用，禁止用图片占位\n"
    "5. 步骤/流程页 → 用 HTML/CSS 绘制节点+连线+文字，禁止纯文字描述\n"
    "6. 关键数字必须有放大数字卡片，结论必须有摘要高亮\n"
    "7. 防溢出：单行文字不超容器宽度；连续段落 ≤ 100 字（超过必须拆列表）\n"
    "8. 布局结构：严格遵循标准 HTML 骨架——main 用 `grid grid-cols-2 gap-3`，"
    "恰好 2 个 `<section>` 子元素；header/main/footer 纵向排列在 content-safe 内\n"
    "9. grid 子元素：必须 `h-full min-h-0 overflow-hidden`\n"
    "10. flex-col 子元素：必须 `flex-1 min-h-0 overflow-hidden`\n"
    "11. 配色与字体严格来自风格规范文件，禁止使用未定义的颜色或字体\n"
    "12. 页脚：底部必须有数据来源汇总条（如'数据来源：央行、财政部、...'），即使卡片内已有来源标注也必须保留页脚，禁止纯数字编号\n"
    "13. 布局实现：所有区域用 `flex-1 min-h-0` 自动分配高度，禁止手动计算 px 值；子元素用 `h-full min-h-0 overflow-hidden` 防溢出，信任 flex/grid 自动布局\n"
    "14. 全局禁止 `rounded-*` 类，所有元素 border-radius:0（饼图/环形图的圆形不受此限制）\n"
)


_HTML_SKELETON = (
    "### 标准 HTML 骨架（所有页面必须遵循，禁止改动结构）\n"
    "```html\n"
    '<div class="ppt-slide">\n'
    '  <div class="content-safe flex flex-col gap-3 h-full">\n'
    '    <header class="flex-shrink-0">标题区</header>\n'
    '    <main class="flex-1 min-h-0 grid grid-cols-2 gap-3">\n'
    '      <section class="h-full min-h-0 overflow-hidden">左侧内容</section>\n'
    '      <section class="h-full min-h-0 overflow-hidden">右侧内容</section>\n'
    '    </main>\n'
    '    <footer class="flex-shrink-0">数据来源页脚</footer>\n'
    '  </div>\n'
    '</div>\n'
    "```\n"
    "规则：\n"
    "- `content-safe` 用 `flex flex-col` 纵向排列 header/main/footer 三段\n"
    "- `main` 用 `grid grid-cols-2` 左右分列，恰好 2 个 `<section>` 直接子元素\n"
    "- 禁止把 header/footer 放进 main 内部；禁止 main 只有 1 个子元素\n"
)


_PAGE_TYPE_RE = re.compile(r"类型[：:]\s*(\w+)", re.IGNORECASE)

_PAGE_LAYOUT_TEMPLATES = {
    "data": (
        "### 推荐布局（data 类型，直接套用标准骨架）\n"
        "```html\n"
        '<div class="content-safe flex flex-col gap-3 h-full">\n'
        '  <header class="flex-shrink-0">4-6 个关键数字卡片，grid grid-cols-6</header>\n'
        '  <main class="flex-1 min-h-0 grid grid-cols-2 gap-3">\n'
        '    <section class="h-full min-h-0 overflow-hidden">6 个核心论点卡片，grid grid-cols-2 grid-rows-3 gap-2</section>\n'
        '    <section class="h-full min-h-0 overflow-hidden">ECharts 柱状图 + 对比表格</section>\n'
        '  </main>\n'
        '  <footer class="flex-shrink-0">数据来源汇总条</footer>\n'
        '</div>\n'
        "```\n"
        "- ECharts 用柱状图(bar)，禁止用折线图\n"
    ),
    "trend": (
        "### 推荐布局（trend 类型，直接套用标准骨架）\n"
        "```html\n"
        '<div class="content-safe flex flex-col gap-3 h-full">\n'
        '  <header class="flex-shrink-0">3 个关键数字卡片</header>\n'
        '  <main class="flex-1 min-h-0 grid grid-cols-2 gap-3">\n'
        '    <section class="h-full min-h-0 overflow-hidden">ECharts 折线图（趋势数据）</section>\n'
        '    <section class="h-full min-h-0 overflow-hidden">4-6 个核心论点卡片，flex-col gap-2</section>\n'
        '  </main>\n'
        '  <footer class="flex-shrink-0">数据来源汇总条</footer>\n'
        '</div>\n'
        "```\n"
        "- ECharts 用折线图(line)，禁止用柱状图\n"
    ),
    "comparison": (
        "### 推荐布局（comparison 类型，直接套用标准骨架）\n"
        "```html\n"
        '<div class="content-safe flex flex-col gap-3 h-full">\n'
        '  <main class="flex-1 min-h-0 grid grid-cols-2 gap-3">\n'
        '    <section class="h-full min-h-0 overflow-hidden">对比对象 A 的卡片（grid grid-cols-2 grid-rows-3）</section>\n'
        '    <section class="h-full min-h-0 overflow-hidden">对比对象 B 的卡片（grid grid-cols-2 grid-rows-3）</section>\n'
        '  </main>\n'
        '  <footer class="flex-shrink-0">对比表格 + 数据来源汇总条</footer>\n'
        '</div>\n'
        "```\n"
        "- ECharts 用分组柱状图(grouped bar)\n"
    ),
    "case": (
        "### 推荐布局（case 类型，直接套用标准骨架）\n"
        "```html\n"
        '<div class="content-safe flex flex-col gap-3 h-full">\n'
        '  <header class="flex-shrink-0">3 个关键数字卡片</header>\n'
        '  <main class="flex-1 min-h-0 grid grid-cols-2 gap-3">\n'
        '    <section class="h-full min-h-0 overflow-hidden">6 个核心论点卡片，grid grid-cols-2 grid-rows-3</section>\n'
        '    <section class="h-full min-h-0 overflow-hidden">ECharts 图表 + 关键数据表格</section>\n'
        '  </main>\n'
        '  <footer class="flex-shrink-0">案例素材详细描述 + 数据来源页脚</footer>\n'
        '</div>\n'
        "```\n"
    ),
    "technology": (
        "### 推荐布局（technology 类型，直接套用标准骨架）\n"
        "```html\n"
        '<div class="content-safe flex flex-col gap-3 h-full">\n'
        '  <header class="flex-shrink-0">4 个关键数字卡片</header>\n'
        '  <main class="flex-1 min-h-0 grid grid-cols-2 gap-3">\n'
        '    <section class="h-full min-h-0 overflow-hidden">6 个核心论点卡片，grid grid-cols-2 grid-rows-3</section>\n'
        '    <section class="h-full min-h-0 overflow-hidden">ECharts 图表 + 对比表格</section>\n'
        '  </main>\n'
        '  <footer class="flex-shrink-0">数据来源汇总条</footer>\n'
        '</div>\n'
        "```\n"
    ),
}


def _detect_page_type(outline_page: str) -> str:
    if not outline_page:
        return ""
    match = _PAGE_TYPE_RE.search(outline_page)
    if match:
        return match.group(1).strip().lower()
    return ""


_DENSITY_CHECKLIST_DIGEST = (
    "### 内容密度检查（8 项，全部必须通过）\n"
    "1. 数据可视化：≥1 个 ECharts 图表 或 ≥3 个数据卡片（no_search 模式且页面为'数据有限'时可降至 2 个数据卡片）\n"
    "2. 核心要点：6-10 个列表项或卡片\n"
    "3. 装饰图标：≥3 个 FontAwesome 图标（class 含 `fa-`）\n"
    "4. 空白率：估算 < 30%\n"
    "5. 数据来源：页脚有标注（机构名 / 资料名）\n"
    "6. 无大段文字：无连续 > 100 字段落\n"
    "7. 视觉层级：标题 → 副标题 → 正文 → 注释 层级清晰\n"
    "8. 布局正确：main 元素 class 含 `grid grid-cols-2`，且恰好 2 个直接子元素（`<section>` 或 `<div>`）\n"
)


def _strip_html_fence(text: str) -> str:
    """剥掉 LLM 偶尔加的 ```html ... ``` 包裹。"""
    if not text:
        return ""
    s = text.strip()
    if not s.startswith("```"):
        return s
    lines = s.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _is_valid_html(text: str) -> bool:
    if not text or len(text) < 200:
        return False
    lower = text.lower()
    return ("<html" in lower or "<!doctype html" in lower) and "ppt-slide" in lower


def _post_check_data_viz(html: str, failed_items: list[str], search_mode: str) -> list[str]:
    """程序化后置校验：对 LLM 判定的'缺数据可视化'做二次确认，移除误判。"""
    if "缺数据可视化" not in failed_items:
        return failed_items
    has_echarts = "echarts" in html.lower()
    card_count = html.lower().count("card")
    threshold = 2 if search_mode == "no_search" else 3
    if has_echarts or card_count >= threshold:
        failed_items = [x for x in failed_items if x != "缺数据可视化"]
    return failed_items


_SEARCH_NEEDED_ITEMS = frozenset({"缺数据可视化", "缺案例", "缺数据来源"})

_SEARCH_QUERY_TEMPLATES: dict[str, list[str]] = {
    "缺数据可视化": [
        "{topic} 市场规模 数据",
        "{topic} 增长率 百分比 统计",
        "{topic} 渗透率 市场份额 报告",
    ],
    "缺案例": [
        "{topic} 应用案例 实践",
        "{topic} 成功案例 最佳实践",
    ],
    "缺数据来源": [
        "{topic} 行业报告",
        "{topic} 研究 数据 来源",
    ],
}


_REWRITE_ACTIONS = {
    "缺数据可视化": (
        "在页面底部（footer 之前）插入一个 ECharts 图表，按以下规则选择图表类型："
        "时间序列数据用折线图，占比/构成数据用饼图，对比/排名数据用柱状图。"
        "直接使用页面中已有的数字作为数据点，不要修改现有卡片和布局结构"
    ),
    "核心要点不足": "将段落拆分为 6-10 个列表项或卡片，每条 1-2 行加图标",
    "缺装饰图标": "为每个核心要点/卡片添加相关 FontAwesome 图标（class 含 fa-）",
    "空白率过高": "添加总结框（1-2 句概括性陈述），其次添加分隔线、引用块、背景装饰",
    "缺数据来源": "在页脚标注'数据来源：XXX'（机构名或资料名）",
    "大段文字": "拆分为多个列表项/小节，添加小标题",
    "视觉层级混乱": "调整字号梯度，建立明确的标题→副标题→正文→注释层级",
    "布局错误": "main 改为 `grid grid-cols-2 gap-3`，恰好 2 个 `<section>` 子元素；header/footer 放在 main 外部的 content-safe 内",
}


def _build_rewrite_hint(failed_items: list[str]) -> str:
    if not failed_items:
        return ""
    lines = ["不通过项与补救动作："]
    for item in failed_items:
        action = _REWRITE_ACTIONS.get(item, "针对性优化该项")
        lines.append(f"- {item} → {action}")
    return "\n".join(lines)


def _extract_page_keywords(research_page: str) -> list[str]:
    if not research_page:
        return []
    keywords: list[str] = []
    for line in research_page.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            header = stripped.lstrip("#").strip()
            if ":" in header:
                header = header.split(":", 1)[1].strip()
            if header and len(header) <= 30:
                keywords.append(header)
            continue
        if any(stripped.startswith(prefix) for prefix in ("- 核心论点", "- 关键数据", "- 案例", "- 标题")):
            content = stripped.lstrip("- ").split("：", 1)[-1].strip()
            if content and len(content) <= 30:
                keywords.append(content)
    if keywords:
        return keywords[:3]
    if len(research_page) > 20:
        first_line = research_page.splitlines()[0].strip().lstrip("#").strip()
        if first_line and len(first_line) <= 30:
            return [first_line]
    return []


def _build_search_queries(
    templates: list[str],
    *,
    topic: str,
    page_keywords: list[str],
) -> list[str]:
    queries: list[str] = []
    if page_keywords:
        for kw in page_keywords[:2]:
            for tpl in templates[:1]:
                queries.append(tpl.format(topic=kw))
    if topic:
        for tpl in templates[:1]:
            q = tpl.format(topic=topic)
            if q not in queries:
                queries.append(q)
    return queries


def _extract_search_text(result: Any) -> str:
    if result is None:
        return ""
    if isinstance(result, str):
        return result[:2000]
    if isinstance(result, list):
        parts: list[str] = []
        for item in result[:5]:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                for key in ("snippet", "content", "text", "title"):
                    v = item.get(key)
                    if isinstance(v, str) and v.strip():
                        parts.append(v)
                        break
        return "\n".join(parts)[:2000]
    if isinstance(result, dict):
        for key in ("results", "items", "data"):
            v = result.get(key)
            if isinstance(v, list):
                return _extract_search_text(v)
        content = result.get("content") or result.get("text") or ""
        if isinstance(content, str):
            return content[:2000]
    return str(result)[:2000]


_PAGE_HEADING_RE = re.compile(r"^###\s+P(\d+)\s*:", re.MULTILINE)


def _split_md_pages(text: str) -> dict[int, str]:
    """按 `### P{N}:` 章节拆分 Markdown，返回 {页码: 该页片段}。"""
    matches = list(_PAGE_HEADING_RE.finditer(text))
    if not matches:
        return {}
    pages: dict[int, str] = {}
    for idx, match in enumerate(matches):
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        page_num = int(match.group(1))
        pages[page_num] = text[start:end].strip()
    return pages


def _build_page_prompt(
    page_number: int,
    style_id: str,
    style_text: str,
    outline_page: str,
    research_page: str,
    *,
    outline_is_full: bool = False,
    research_is_full: bool = False,
    rewrite_hint: str = "",
    original_html: str = "",
) -> str:
    preset_clause = ""
    if style_id in _PRESET_STYLE_IDS:
        preset_clause = (
            "\n**强制性设计规范**：当前为预设风格，禁止自由发挥配色和字体，"
            "必须严格遵循风格文件中的所有定义。\n"
        )

    rewrite_section = ""
    if rewrite_hint:
        original_section = ""
        if original_html:
            original_section = (
                "\n### 上次产物（原始 HTML）\n"
                "```html\n"
                f"{original_html}\n"
                "```\n"
            )
        rewrite_constraints = (
            "⚠️ **重写约束**：\n"
            "- 仅修复上述不通过项，不要改动其他正常部分\n"
            "- 布局结构（grid/flex-col、子元素数量）如果上次已正确，严禁改动\n"
            "- 已通过的检查项对应的代码不要修改\n"
            "- 只在不通过项相关的代码区域做修改，其余部分保持原样\n"
        )
        if original_html:
            rewrite_constraints += "- 必须基于上次产物做针对性修改，不要从零重新生成\n"
        rewrite_section = (
            "\n## 重写指引（必须修复的问题）\n"
            f"{rewrite_hint}\n"
            f"{rewrite_constraints}"
            f"{original_section}"
        )

    outline_label = "大纲 — 全文（请从中定位 ### P{N}: 章节）" if outline_is_full else "大纲 — 本页规划"
    research_label = "研究报告 — 全文（请从中定位 ### P{N}: 章节）" if research_is_full else "研究报告 — 本页素材"

    no_outline = not outline_page.strip()
    no_research = not research_page.strip()

    if no_outline:
        outline_label = "大纲（未提供，请根据重写指引和搜索补充数据自行推断页面类型与布局）"
    if no_research:
        research_label = "研究报告（未提供，请根据重写指引和搜索补充数据自行生成内容）"

    fusion_rules = (
        "- 大纲提供页面类型与数据需求，决定页面布局和内容方向\n"
        "- 研究报告提供核心论点、关键数据、案例素材，决定页面具体内容\n"
        "- 上述大纲 + 研究报告中的全部信息点都必须体现\n"
    )
    if outline_is_full or research_is_full:
        fusion_rules = (
            f"- 以下素材为完整文档，你**仅负责第 {page_number} 页**，"
            f"请从全文中定位 `### P{page_number}:` 章节，仅使用该页内容\n"
            "- 大纲提供页面类型与数据需求，决定页面布局和内容方向\n"
            "- 研究报告提供核心论点、关键数据、案例素材，决定页面具体内容\n"
            "- 严禁将其他页面的内容混入本页\n"
        )
    if no_outline or no_research:
        fusion_rules = (
            "- 部分素材缺失，请根据重写指引和搜索补充数据生成内容\n"
            "- 严格遵循视觉风格规范和布局硬约束\n"
            "- 确保所有文字为真实内容，禁止占位文本\n"
        )

    page_type = _detect_page_type(outline_page)
    layout_template = _PAGE_LAYOUT_TEMPLATES.get(page_type, "")

    return (
        "## 0. 输出要求（最高优先级）\n"
        f"- 输出**第 {page_number} 页**完整 HTML（含 <!DOCTYPE>、<html>、<head>、<body>）\n"
        "- 严禁任何解释、注释、Markdown 代码块包裹，只输出 HTML 原文\n"
        "- 页面尺寸严格 1280×720px\n"
        '- 必须包含 `<div class="ppt-slide">` 容器\n'
        "- 禁止在思考过程中反复计算像素或纠结布局，直接套用下方推荐布局模板\n"
        "- 一次性输出完整 HTML，禁止输出'final code''truly final'等反复确认语句\n"
        "\n"
        "## 1. 视觉风格规范（强制遵守）\n"
        f"{style_text}\n"
        f"{preset_clause}"
        "\n"
        f"{_DESIGN_RULES_DIGEST}"
        "\n"
        f"{_HTML_SKELETON}"
        "\n"
        f"{layout_template}"
        "\n"
        f"{_DENSITY_CHECKLIST_DIGEST}"
        "\n"
        "## 2. 内容素材\n"
        "\n"
        f"### {outline_label}\n"
        f"{outline_page}\n"
        "\n"
        f"### {research_label}\n"
        f"{research_page}\n"
        "\n"
        "## 3. 内容融合规则\n"
        f"{fusion_rules}"
        f"{rewrite_section}"
        "\n"
        "## 4. 任务\n"
        f"你负责生成**第 {page_number} 页** HTML。仅生成该页，直接输出 HTML 原文。"
        "生成时必须同时满足上述「内容密度检查（8 项）」全部要求，"
        "确保首次生成即通过密度检查，避免后续重写。"
    )


@dataclass
class PageGenContext:
    """单页生成上下文——_generate_one / _rewrite_one 共享的只读参数。"""

    page_num: int
    style_id: str
    style_text: str
    outline_page: str
    research_page: str
    outline_is_full: bool
    research_is_full: bool


class PrepareNode(PlanNode):
    """P8.0 — 读取素材并按页拆分，产出共享只读数据供 per-page worker 复用。"""

    def __init__(self) -> None:
        super().__init__(
            plan_name="p8_0_prepare",
            instruction=(
                "## P8.0 素材预处理\n"
                "\n"
                "### 前置条件\n"
                "- `read_file` 工具可用\n"
                "- `outline.md` / `research.md` / 风格文件均已落盘\n"
                "\n"
                "### 输入\n"
                "- `page_count`（必填）: N 页\n"
                "- `output_dir`（必填）: 工作目录（用于读 outline/research）\n"
                "- `style_file_path`（必填）: 风格文件绝对路径\n"
                "\n"
                "### 输出\n"
                "- `prepare_status`: ok / failed\n"
                "- `outline_pages` / `research_pages`: 按页拆分的 {页码: 片段}（拆分失败为空 dict，下游回退全文）\n"
                "- `outline_text` / `research_text` / `style_text`: 全文（供下游回退与重写复用）\n"
                "- `all_pages`: 1..N 页码列表\n"
                "\n"
                "### 执行流程\n"
                "1. 一次性读取 outline.md / research.md / style_file_path（任一失败 → prepare_status=failed）\n"
                "2. 按 `### P{N}:` 章节拆分 outline 和 research，每页只取对应片段；拆分失败时回退全文\n"
                "3. 返回共享只读数据，供 P8.1 per-page worker 复用\n"
                "\n"
                "### 失败兜底\n"
                "- 读资料失败：prepare_status=failed，根节点直接终止，不进入 P8.1\n"
                "- 拆分失败：outline_pages/research_pages 为空，下游 worker 回退全文\n"
            ),
        )

    async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        page_count = int(inputs.get("page_count") or 0)
        output_dir = str(inputs.get("output_dir") or "").strip()
        style_file_path = str(inputs.get("style_file_path") or "").strip()

        outline_text = await self._read_file(f"{output_dir}/outline.md")
        research_text = await self._read_file(f"{output_dir}/research.md")
        style_text = await self._read_file(style_file_path)

        if not outline_text or not research_text or not style_text:
            logger.error(
                "[P8.0] 资料读取失败 outline=%d research=%d style=%d",
                len(outline_text),
                len(research_text),
                len(style_text),
            )
            return {
                "prepare_status": "failed",
                "outline_pages": {},
                "research_pages": {},
                "outline_text": outline_text,
                "research_text": research_text,
                "style_text": style_text,
                "all_pages": list(range(1, page_count + 1)) if page_count > 0 else [],
            }

        outline_pages = _split_md_pages(outline_text)
        research_pages = _split_md_pages(research_text)
        if not outline_pages:
            logger.warning("[P8.0] outline.md 未拆分到任何页面章节，下游回退全文")
        if not research_pages:
            logger.warning("[P8.0] research.md 未拆分到任何页面章节，下游回退全文")

        logger.info(
            "[P8.0] 预处理完成 outline_pages=%d research_pages=%d",
            len(outline_pages),
            len(research_pages),
        )
        return {
            "prepare_status": "ok",
            "outline_pages": outline_pages,
            "research_pages": research_pages,
            "outline_text": outline_text,
            "research_text": research_text,
            "style_text": style_text,
            "all_pages": list(range(1, page_count + 1)),
        }

    async def _read_file(self, path: str) -> str:
        if not path:
            return ""
        if not self.has_tool("read_file"):
            logger.warning("[P8.0] read_file 工具不可用 %s", path)
            return ""
        try:
            result = await self.call_tool("read_file", file_path=path)
            content = PptCommon.parse_tool_file_content(result)
            return content
        except Exception as e:
            if isinstance(e, AbortError):
                raise
            logger.warning("[P8.0] 读取文件失败 %s: %s", path, e)
            return ""

    async def _execute_stream(self, inputs: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        result = await self._execute(inputs)
        ok = result.get("prepare_status") == "ok"
        yield {
            **result,
            "node": self.plan_name,
            "status": "ok" if ok else "error",
            "message": "素材预处理完成" if ok else "素材读取失败",
        }


class PageWorkerNode(PlanNode):
    """P8.1 — per-page 闭环：生成→校验→密度判定→搜索补充→重写，N 页并发。"""

    def __init__(self) -> None:
        super().__init__(
            plan_name="p8_1_page_worker",
            instruction=(
                "## P8.1 per-page 闭环生成（合并原 P8.1 生成 + P8.2 密度检查与重写）\n"
                "\n"
                "### 前置条件\n"
                "- `read_file` / `write_file` 工具可用\n"
                "- `web_search` 工具可用（搜索补充，不可用时降级为纯重写）\n"
                "- P8.0 已产出共享只读数据（outline_pages/research_pages/全文/style_text）\n"
                "- `pages_dir` 已存在\n"
                "\n"
                "### 输入\n"
                "- `page_count`（必填）: N 页\n"
                "- `pages_dir`（必填）: HTML 输出目录绝对路径\n"
                "- `style_id`（必填）: 用于判定是否预设风格强约束\n"
                "- `outline_pages` / `research_pages`（来自 P8.0）: 按页拆分片段\n"
                "- `outline_text` / `research_text` / `style_text`（来自 P8.0）: 全文，拆分失败时回退\n"
                "- `all_pages`（来自 P8.0）: 1..N 页码列表\n"
                "- `topic`（可选）: PPT 主题，搜索补充关键词用\n"
                "- `search_mode`（可选，影响数据可视化阈值）\n"
                "- `gen_retry_round`（可选，默认 1）\n"
                "- `density_retry_round`（可选，默认 1）\n"
                "\n"
                "### 输出\n"
                "- `page_files`: 实际产出的 page-*.pptx.html 列表\n"
                "- `missing_pages`: 仍缺失的页码（用于上层标 partial）\n"
                "- `low_density_pages`: 重写后仍未通过的页码\n"
                "- `density_report`: 每页检查结果摘要\n"
                "- `outline_text` / `research_text` / `style_text`（透传给 P8.3）\n"
                "\n"
                "### 执行流程（per-page 闭环，N 页 asyncio.gather 并发）\n"
                "对每一页独立执行：\n"
                "1. 生成阶段：用该页 outline 片段 + research 片段 + 风格规范 + 视觉与布局硬约束构造 prompt，"
                "调 LLM 生成 HTML；剥 ```html 包裹 → 校验（含 <!DOCTYPE> + ppt-slide 容器）→ write_file 落盘\n"
                "   - 失败按 gen_retry_round 重试（仅本页）\n"
                "   - 重试后仍失败 → 进 missing_pages，该页闭环终止\n"
                "2. 密度判定阶段：调 LLM 做 8 项密度检查（受控 JSON 输出），叠加程序化后置校验（echarts/card 计数）\n"
                "   - 检查项：数据可视化 / 核心要点 / 装饰图标 / 空白率 / 数据来源 / 大段文字 / 视觉层级 / 布局正确\n"
                "   - 数据可视化阈值：≥1 个 ECharts 图表 或 ≥3 个数据卡片（no_search 模式降至 2 个）\n"
                "3. 不通过 → 修复阶段（按 density_retry_round 轮）：\n"
                "   a. 分析缺失项，判断是否需要搜索补充数据\n"
                "   b. 若缺数据可视化/缺案例/缺数据来源 → 调用 `web_search` 搜索补充：\n"
                "      - 缺数据可视化：搜索 `\"{主题} 市场规模 数据\"` / `\"{主题} 增长率 统计\"`，获取可图表化的数据点\n"
                "      - 缺案例：搜索 `\"{主题} 应用案例 实践\"`，获取真实案例\n"
                "      - 缺数据来源：搜索 `\"{主题} 行业报告\"`，获取权威机构名称\n"
                "      - 搜索优先获取最近 1-2 年数据，优先权威来源\n"
                "      - 数据来源标注使用 research.md 中的来源\n"
                "   c. 将搜索结果 + 原有素材 + 不通过项提示词 + 上次产物构造重写 prompt，调 LLM 重新生成 HTML\n"
                "   d. 若无需搜索（如缺装饰图标/大段文字/视觉层级/布局错误），直接用原有素材 + 不通过项提示词重写\n"
                "   e. 重写产物校验通过 → 落盘覆盖 → 复检；仍不通过进 low_density_pages\n"
                "\n"
                "### 内容要求（每页 HTML 必须满足）\n"
                "- 所有文字必须是真实内容，禁止占位文本（TODO、xxx 等）\n"
                "- 该页 outline + research 中的全部信息点都必须体现\n"
                "- 数据/对比/趋势页 → 使用 ECharts 绘制实际图表，禁止图片占位\n"
                "- 步骤/流程页 → 绘制完整节点 + 连线 + 文字标注，禁止纯文字描述\n"
                "- 关键数字必须有放大数字卡片 + 说明注释\n"
                "- 结论必须有摘要高亮\n"
                "- 视觉精细化：三级字体体系（标题 36-48px、副标题 24-28px、正文 16-20px）\n"
                "- 装饰增强：页面边缘/背景层加轻量几何装饰\n"
                "\n"
                "### 数据转换规则（搜索补充后）\n"
                "| 获取内容 | 转换方式 |\n"
                "|---|---|\n"
                "| 时间序列数据（≥3 点） | 折线图（趋势）或柱状图（对比） |\n"
                "| 类别占比数据（总和 100%） | 饼图/环形图 |\n"
                "| 对比数据（2-3 类别） | 条形图或对比卡片 |\n"
                "| 多类别比较（≥4 类别） | 柱状图 |\n"
                "| 关键观点 | 带图标的列表项 |\n"
                "| 真实案例 | 案例卡片（公司名 + 数据 + 效果） |\n"
                "\n"
                "### 失败兜底\n"
                "- 生成 LLM 调用 raise / 返回空 / HTML 校验失败：进 missing_pages\n"
                "- 首次落盘 write_file 异常：进 missing_pages\n"
                "- 检查 LLM 调用失败（含网络/超时/JSON 解析等任意异常）：保守视为通过（避免假阳性触发无意义重写）\n"
                "- 重检时 read_file 失败：保守判通过，保留当前 HTML\n"
                "- web_search 不可用或搜索失败：跳过搜索补充，仅用原有素材重写\n"
                "- 重写 LLM 调用失败 / 重写产物校验失败 / 重写产物落盘失败：保留当前 HTML，进 low_density_pages\n"
                "- density_retry_round 后仍不通过：保留当前 HTML 供人工排查\n"
                "- 重试后仍缺失：透传给根节点\n"
            ),
        )

    async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        pages_dir = str(inputs.get("pages_dir") or "").strip()
        style_id = str(inputs.get("style_id") or "").strip()
        search_mode = str(inputs.get("search_mode") or "auto").strip()
        topic = str(inputs.get("topic") or "").strip()
        gen_retry_round = int(inputs.get("gen_retry_round") or _DEFAULT_GEN_RETRY_ROUND)
        density_retry_round = int(inputs.get("density_retry_round") or _DEFAULT_DENSITY_RETRY_ROUND)

        outline_pages: dict[int, str] = inputs.get("outline_pages") or {}
        research_pages: dict[int, str] = inputs.get("research_pages") or {}
        outline_full = str(inputs.get("outline_text") or "")
        research_full = str(inputs.get("research_text") or "")
        style_text = str(inputs.get("style_text") or "")
        all_pages: list[int] = list(inputs.get("all_pages") or [])

        if not pages_dir or not all_pages:
            logger.error("[P8.1] 必填输入缺失，跳过生成")
            return {
                "page_files": [],
                "missing_pages": list(all_pages),
                "low_density_pages": [],
                "density_report": {},
                "outline_text": outline_full,
                "research_text": research_full,
                "style_text": style_text,
            }

        tasks = [
            self._run_page_pipeline(
                page_num=p,
                pages_dir=pages_dir,
                style_id=style_id,
                style_text=style_text,
                outline_page=outline_pages.get(p, outline_full),
                research_page=research_pages.get(p, research_full),
                outline_is_full=p not in outline_pages,
                research_is_full=p not in research_pages,
                search_mode=search_mode,
                topic=topic,
                gen_retry_round=gen_retry_round,
                density_retry_round=density_retry_round,
            )
            for p in all_pages
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        missing_pages: list[int] = []
        low_density_pages: list[int] = []
        density_report: dict[int, dict[str, Any]] = {}
        for p, r in zip(all_pages, results):
            if isinstance(r, Exception):
                logger.warning("[P8.1] 页面 %d 闭环异常: %s", p, r)
                missing_pages.append(p)
                continue
            if r.get("missing"):
                missing_pages.append(p)
            if r.get("low_density"):
                low_density_pages.append(p)
            if r.get("report"):
                density_report[p] = r["report"]

        successful_pages = [p for p in all_pages if p not in missing_pages]
        page_files = [f"page-{p}.pptx.html" for p in successful_pages]

        logger.info(
            "[P8.1] per-page 闭环完成 success=%d/%d missing=%d low_density=%d",
            len(successful_pages),
            len(all_pages),
            len(missing_pages),
            len(low_density_pages),
        )
        return {
            "page_files": page_files,
            "missing_pages": missing_pages,
            "low_density_pages": low_density_pages,
            "density_report": density_report,
            "outline_text": outline_full,
            "research_text": research_full,
            "style_text": style_text,
        }

    async def _run_page_pipeline(
        self,
        *,
        page_num: int,
        pages_dir: str,
        style_id: str,
        style_text: str,
        outline_page: str,
        research_page: str,
        outline_is_full: bool,
        research_is_full: bool,
        search_mode: str,
        topic: str,
        gen_retry_round: int,
        density_retry_round: int,
    ) -> dict[str, Any]:
        """单页闭环：生成(含重试) → 密度判定 → 搜索补充+重写(含重试)。"""
        path = f"{pages_dir}/page-{page_num}.pptx.html"
        ctx = PageGenContext(
            page_num=page_num,
            style_id=style_id,
            style_text=style_text,
            outline_page=outline_page,
            research_page=research_page,
            outline_is_full=outline_is_full,
            research_is_full=research_is_full,
        )

        html = ""
        for attempt in range(max(gen_retry_round + 1, 1)):
            if attempt > 0:
                logger.info("[P8.1] 页面 %d 第 %d 轮生成重试", page_num, attempt + 1)
            html = await self._generate_one(ctx)
            if html:
                break
        if not html:
            return {"missing": True, "low_density": False, "report": {}}

        ok = await self._write_file(path, html)
        if not ok:
            return {"missing": True, "low_density": False, "report": {}}

        report: dict[str, Any] = {"pass": True}
        low_density = False
        total_rounds = max(density_retry_round + 1, 1)
        for round_idx in range(total_rounds):
            current = await self._read_file(path)
            if not current:
                logger.warning("[P8.1] 页面 %d 重检时读取失败，保守判通过", page_num)
                break
            report = await self._check_one(page_num, current, search_mode)
            if report.get("pass", True):
                break
            if round_idx == total_rounds - 1:
                low_density = True
                logger.info("[P8.1] 页面 %d 密度重试用尽，进 low_density", page_num)
                break

            supplement = await self._search_supplement_one(
                page_num, report, research_page, topic,
            )
            rewritten = await self._rewrite_one(
                ctx,
                report.get("failed_items") or [], current, supplement,
            )
            if not rewritten:
                low_density = True
                logger.info("[P8.1] 页面 %d 重写失败，保留当前 HTML，进 low_density", page_num)
                break
            write_ok = await self._write_file(path, rewritten)
            if not write_ok:
                low_density = True
                logger.info("[P8.1] 页面 %d 重写产物落盘失败，保留当前 HTML，进 low_density", page_num)
                break

        return {
            "missing": False,
            "low_density": low_density,
            "report": report,
        }

    async def _generate_one(self, ctx: PageGenContext) -> str:
        """生成单页 HTML，返回校验通过的 html 或空串。"""
        try:
            result = await self.stream_llm_collect(
                prompt=_build_page_prompt(
                    ctx.page_num,
                    style_id=ctx.style_id,
                    style_text=ctx.style_text,
                    outline_page=ctx.outline_page,
                    research_page=ctx.research_page,
                    outline_is_full=ctx.outline_is_full,
                    research_is_full=False,
                ),
                system_prompt="你是资深演示文稿设计师，直接输出完整 HTML 原文，不输出任何解释。",
                node_name=f"p8_1_page_{ctx.page_num}",
                concurrent=True,
            )
        except Exception as e:
            if isinstance(e, AbortError):
                raise
            logger.warning("[P8.1] 页面 %d 生成 LLM 失败: %s", ctx.page_num, e)
            return ""
        html = _strip_html_fence(result or "")
        if not _is_valid_html(html):
            logger.warning("[P8.1] 页面 %d HTML 校验失败", ctx.page_num)
            return ""
        return html

    async def _check_one(
        self,
        page_num: int,
        html: str,
        search_mode: str,
    ) -> dict[str, Any]:
        """单页密度判定（LLM + 程序化后置校验）。LLM 异常保守判通过。"""
        no_search_hint = ""
        if search_mode == "no_search":
            no_search_hint = "\n注意：当前为 no_search 模式，标注'数据有限'的页面，数据可视化阈值降至 ≥2 个数据卡片。\n"

        prompt = (
            "请对以下 PPT 单页 HTML 做内容密度检查，按 8 项清单逐项判定，仅输出 JSON。\n\n"
            f"{_DENSITY_CHECKLIST_DIGEST}"
            f"{no_search_hint}"
            "\nHTML 内容：\n"
            "```html\n"
            f"{html}\n"
            "```\n\n"
            "输出 JSON（受控字段）：\n"
            '{"pass": true/false, "failed_items": ["缺数据可视化","缺装饰图标"...], "reason": "简要说明"}\n'
            "failed_items 仅可从以下取值：\n"
            "缺数据可视化 / 核心要点不足 / 缺装饰图标 / 空白率过高 / 缺数据来源 / 大段文字 / 视觉层级混乱 / 布局错误\n"
            "全部通过则 failed_items: []。只输出 JSON，不输出其他内容。"
        )
        try:
            llm_result = await self.stream_llm_collect(
                prompt=prompt,
                system_prompt="你是 PPT 内容密度检查助手，只输出 JSON。",
                node_name=f"p8_2_density_check_page_{page_num}",
                concurrent=True,
            )
            check = self.extract_json(llm_result, expected_type=dict)
            if not isinstance(check, dict):
                return {"pass": True, "reason": "json_parse_failed"}
            failed_items = list(check.get("failed_items") or [])
            llm_failed = list(failed_items)
            failed_items = _post_check_data_viz(html, failed_items, search_mode)
            if len(failed_items) < len(llm_failed):
                removed = [x for x in llm_failed if x not in failed_items]
                logger.info(
                    "[P8.1] 页面 %d 程序化后置校验移除误判项: %s",
                    page_num,
                    removed,
                )
            return {
                "pass": bool(check.get("pass", False)) and not failed_items,
                "failed_items": failed_items,
                "reason": str(check.get("reason") or ""),
            }
        except Exception as e:
            if isinstance(e, AbortError):
                raise
            logger.warning("[P8.1] 页面 %d 密度检查 LLM 失败（保守通过）: %s", page_num, e)
            return {"pass": True, "reason": f"llm_error: {e}"}

    async def _search_supplement_one(
        self,
        page_num: int,
        report: dict[str, Any],
        research_page: str,
        topic: str,
    ) -> str:
        """单页搜索补充（原 _search_supplement 的单页版，随 per-page 并发）。"""
        if not self.has_tool("web_search"):
            return ""
        failed_items = set(report.get("failed_items") or [])
        search_items = failed_items & _SEARCH_NEEDED_ITEMS
        if not search_items:
            return ""

        page_keywords = _extract_page_keywords(research_page) if research_page else []
        snippets: list[str] = []
        for item in search_items:
            templates = _SEARCH_QUERY_TEMPLATES.get(item, [])
            queries = _build_search_queries(templates, topic=topic, page_keywords=page_keywords)
            if not queries:
                continue
            for query in queries[:2]:
                try:
                    raw = await self.call_tool("web_search", query=query)
                    snippet = _extract_search_text(raw)
                    if snippet:
                        snippets.append(f"[{item}] 搜索 \"{query}\" 结果：\n{snippet}")
                        break
                except Exception as e:
                    if isinstance(e, AbortError):
                        raise
                    logger.warning("[P8.1] 页面 %d 搜索补充失败 item=%s query=%s: %s", page_num, item, query, e)
        return "\n\n".join(snippets)

    async def _rewrite_one(
        self,
        ctx: PageGenContext,
        failed_items: list[str],
        original_html: str,
        supplement: str,
    ) -> str:
        """单页重写，返回校验通过的新 html 或空串。"""
        hint = _build_rewrite_hint(failed_items)
        if supplement:
            hint = f"{hint}\n\n### 搜索补充数据\n{supplement}" if hint else f"### 搜索补充数据\n{supplement}"
        try:
            result = await self.stream_llm_collect(
                prompt=_build_page_prompt(
                    ctx.page_num,
                    style_id=ctx.style_id,
                    style_text=ctx.style_text,
                    outline_page=ctx.outline_page,
                    research_page=ctx.research_page,
                    outline_is_full=ctx.outline_is_full,
                    research_is_full=ctx.research_is_full,
                    rewrite_hint=hint,
                    original_html=original_html,
                ),
                system_prompt="你是资深演示文稿设计师，直接输出完整 HTML 原文，不输出任何解释。",
                node_name=f"p8_2_page_{ctx.page_num}",
                concurrent=True,
            )
        except Exception as e:
            if isinstance(e, AbortError):
                raise
            logger.warning("[P8.1] 页面 %d 重写 LLM 失败: %s", ctx.page_num, e)
            return ""
        html = _strip_html_fence(result or "")
        if not _is_valid_html(html):
            logger.warning("[P8.1] 页面 %d 重写产物 HTML 校验失败", ctx.page_num)
            return ""
        return html

    async def _read_file(self, path: str) -> str:
        if not path:
            return ""
        if not self.has_tool("read_file"):
            logger.warning("[P8.1] read_file 工具不可用 %s", path)
            return ""
        try:
            result = await self.call_tool("read_file", file_path=path)
            content = PptCommon.parse_tool_file_content(result)
            return content
        except Exception as e:
            if isinstance(e, AbortError):
                raise
            logger.warning("[P8.1] 读取文件失败 %s: %s", path, e)
            return ""

    async def _write_file(self, path: str, content: str) -> bool:
        if not self.has_tool("write_file"):
            logger.error("[P8.1] write_file 工具不可用 %s", path)
            return False
        try:
            await self.call_tool("write_file", file_path=path, content=content)
            return True
        except Exception as e:
            if isinstance(e, AbortError):
                raise
            logger.error("[P8.1] 写入文件失败 %s: %s", path, e)
            return False

    async def _execute_stream(self, inputs: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        result = await self._execute(inputs)
        missing = result.get("missing_pages", [])
        low = result.get("low_density_pages", [])
        status = "ok" if not missing and not low else "warning"
        yield {
            **result,
            "node": self.plan_name,
            "status": status,
            "message": (
                f"per-page 闭环完成，成功 {len(result.get('page_files', []))} 页，"
                f"缺失 {len(missing)} 页，低密度 {len(low)} 页"
            ),
        }


class QAFixNode(PlanNode):
    """P8.3 — 完整性检查 + cli.js fix（对应 SKILL Stage 7.5）。"""

    def __init__(self) -> None:
        super().__init__(
            plan_name="p8_3_qa_fix",
            instruction=(
                "## P8.3 QA 与自动修复\n"
                "\n"
                "### 前置条件\n"
                "- `bash` 工具可用\n"
                "- `list_dir` / `glob` 工具可用（用于完整性检查）\n"
                "- pages_dir 已存在\n"
                "\n"
                "### 输入\n"
                "- `pages_dir` / `page_count`\n"
                "\n"
                "### 输出\n"
                "- `qa_status`: ok / partial / failed\n"
                "- `final_page_files`: 修复后的最终文件清单\n"
                "- `fix_report`: cli.js fix 输出摘要\n"
                "\n"
                "### 执行流程\n"
                "1. 完整性检查：列 pages_dir 下 page-*.pptx.html，比对数量与 page_count\n"
                "2. 自动修复：node cli.js fix {pages_dir}/ --fix（标签校验、布局修复、图表修复、CDN 依赖补充）\n"
                "\n"
                "### 失败兜底\n"
                "- bash 不可用：跳过 fix，仅做完整性检查\n"
                "- cli.js fix 报错：qa_status = failed，page_files 仍返回\n"
                "- list_dir 不可用：completeness_ok = unknown，不阻塞\n"
            ),
        )

    async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        pages_dir = str(inputs.get("pages_dir") or "").strip()
        page_count = int(inputs.get("page_count") or 0)

        if not pages_dir:
            logger.error("[P8.3] pages_dir 为空")
            return {
                "qa_status": "failed",
                "final_page_files": [],
                "fix_report": "pages_dir empty",
            }

        completeness_ok, page_files = await self._check_completeness(pages_dir, page_count)
        qa_status = "ok" if completeness_ok else "partial"

        fix_report = ""
        try:
            pptx_root = str(inputs.get("pptx_root") or _PPT_DIR)
            fix_cmd = f"{cli_path('fix', pptx_root)} {quote_path(pages_dir + '/')} --fix"
            fix_result = await run_bash(
                self, fix_cmd, timeout_seconds=600, required=False,
                workdir=pptx_root,
            )
            fix_report = combined_output(fix_result)[:2000]
            if fix_result.exit_code != 0:
                logger.error(
                    "[P8.3] cli.js fix 失败 exit=%d output=%s",
                    fix_result.exit_code, fix_report,
                )
                qa_status = "partial"
            else:
                logger.info("[P8.3] cli.js fix 完成")
        except BashExecError as e:
            logger.error("[P8.3] cli.js fix 异常: %s", e)
            qa_status = "failed"
            fix_report = f"bash_error: {e}"

        return {
            "qa_status": qa_status,
            "final_page_files": page_files,
            "fix_report": fix_report,
        }

    async def _check_completeness(
        self,
        pages_dir: str,
        page_count: int,
    ) -> tuple[bool, list[str]]:
        files: list[str] = []
        logger.debug(
            "[P8.3] _check_completeness start pages_dir=%s page_count=%d has_list_dir=%s has_glob=%s",
            pages_dir,
            page_count,
            self.has_tool("list_dir"),
            self.has_tool("glob"),
        )
        if self.has_tool("list_dir"):
            try:
                result = await self.call_tool("list_dir", path=pages_dir)
                files = self._parse_listing(result)
                logger.debug("[P8.3] list_dir 解析结果 files=%d", len(files))
            except Exception as e:
                if isinstance(e, AbortError):
                    raise
                logger.warning("[P8.3] list_dir 失败，回退 glob: %s", e)
                files = []

        if not files and self.has_tool("glob"):
            try:
                result = await self.call_tool(
                    "glob",
                    pattern="page-*.pptx.html",
                    path=pages_dir,
                )
                files = self._parse_listing(result)
                logger.debug("[P8.3] glob 解析结果 files=%d", len(files))
            except Exception as e:
                if isinstance(e, AbortError):
                    raise
                logger.warning("[P8.3] glob 失败: %s", e)
                files = []

        page_files = sorted(
            {f for f in files if f.startswith("page-") and f.endswith(".pptx.html")}
        )

        if page_count <= 0:
            return bool(page_files), page_files

        completeness_ok = len(page_files) == page_count
        if not completeness_ok:
            logger.warning(
                "[P8.3] 完整性不足 actual=%d expected=%d",
                len(page_files),
                page_count,
            )
        return completeness_ok, page_files

    def _parse_listing(self, result: Any) -> list[str]:
        if result is None:
            return []
        logger.debug(
            "[P8.3] _parse_listing input type=%s repr=%.500s",
            type(result).__name__,
            repr(result),
        )
        if isinstance(result, list):
            return [self._basename(self._extract_path_from_item(x)) for x in result]
        if isinstance(result, dict):
            for key in ("entries", "files", "filenames", "items", "result", "matches", "paths"):
                v = result.get(key)
                if isinstance(v, list):
                    return [self._basename(self._extract_path_from_item(x)) for x in v]
            content = result.get("content")
            if isinstance(content, str):
                return self._parse_listing_text(content)
        if hasattr(result, "data"):
            data = result.data
            if isinstance(data, list):
                return [self._basename(self._extract_path_from_item(x)) for x in data]
            if isinstance(data, dict):
                for key in ("entries", "files", "filenames", "items", "result", "matches", "paths"):
                    v = data.get(key)
                    if isinstance(v, list):
                        return [self._basename(self._extract_path_from_item(x)) for x in v]
                content = data.get("content")
                if isinstance(content, str):
                    return self._parse_listing_text(content)
            if isinstance(data, str):
                return self._parse_listing_text(data)
        if hasattr(result, "model_dump"):
            dumped = result.model_dump(mode="json")
            if isinstance(dumped, dict):
                for key in ("entries", "files", "filenames", "items", "result", "data", "matches", "paths"):
                    v = dumped.get(key)
                    if isinstance(v, list):
                        return [self._basename(self._extract_path_from_item(x)) for x in v]
                    if isinstance(v, dict):
                        for sub_key in ("entries", "files", "filenames", "items", "result", "matches", "paths"):
                            sv = v.get(sub_key)
                            if isinstance(sv, list):
                                return [self._basename(self._extract_path_from_item(x)) for x in sv]
                content = dumped.get("content")
                if isinstance(content, str):
                    return self._parse_listing_text(content)
            if isinstance(dumped, list):
                return [self._basename(self._extract_path_from_item(x)) for x in dumped]
            if isinstance(dumped, str):
                return self._parse_listing_text(dumped)
        if isinstance(result, str):
            return self._parse_listing_text(result)
        logger.warning(
            "[P8.3] _parse_listing 无法解析 result type=%s repr=%.300s",
            type(result).__name__,
            repr(result),
        )
        return []

    @staticmethod
    def _extract_path_from_item(item: Any) -> str:
        if isinstance(item, str):
            return item
        if isinstance(item, dict):
            for key in ("path", "name", "file", "filename", "filepath", "href", "url"):
                v = item.get(key)
                if isinstance(v, str) and v:
                    return v
            for v in item.values():
                if isinstance(v, str) and _looks_like_path(v):
                    return v
        return str(item)

    def _parse_listing_text(self, text: str) -> list[str]:
        return [self._basename(line.strip()) for line in text.splitlines() if line.strip()]

    @staticmethod
    def _basename(path: str) -> str:
        path = path.replace("\\", "/").rstrip("/")
        return path.rsplit("/", 1)[-1] if "/" in path else path

    async def _execute_stream(self, inputs: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        result = await self._execute(inputs)
        status_map = {"ok": "ok", "partial": "warning", "failed": "error"}
        yield {
            **result,
            "node": self.plan_name,
            "status": status_map.get(result.get("qa_status", ""), "warning"),
            "message": f"QA 完成 status={result.get('qa_status')}",
        }


def _extract_page_number(filename: str) -> int:
    m = re.search(r"page-(\d+)\.pptx\.html$", filename)
    if not m:
        return 0
    try:
        return int(m.group(1))
    except ValueError:
        return 0


class PPTPageGenNode(PlanNode):
    """P8 — 幻灯片生成根节点。"""

    def __init__(self) -> None:
        super().__init__(
            plan_name="p8_ppt_page_gen",
            instruction=(
                "## P8 幻灯片生成\n"
                "\n"
                "### 节点职责\n"
                "1. 把 outline.md + research.md + 风格文件转成 N 个 page-{N}.pptx.html\n"
                "2. 三阶段串行编排：预处理 → per-page 闭环生成 → QA 与自动修复\n"
                "   - per-page 闭环内部 N 页 asyncio.gather 并发，单页内生成→密度判定→搜索补充→重写串行\n"
                "3. 不区分单 Agent 模式，LLM 并发度由框架 semaphore 控制\n"
                "\n"
                "### 输入\n"
                "- `output_dir`（必填）: 工作目录（含 outline.md / research.md）\n"
                "- `pages_dir`（必填）: HTML 输出目录\n"
                "- `style_file_path`（必填）: P7 落盘的风格文件\n"
                "- `style_id`（必填）: 用于预设风格强约束\n"
                "- `page_count`（必填）: 大纲页数 N\n"
                "- `topic`（可选）: PPT 主题，密度检查搜索补充用\n"
                "- `search_mode`（可选）: 密度阈值放宽依据\n"
                "- `gen_retry_round`（可选，默认 1）\n"
                "- `density_retry_round`（可选，默认 1）\n"
                "\n"
                "### 输出\n"
                "```json\n"
                '{\n'
                '  "pages_dir": "...",\n'
                '  "page_files": ["page-1.pptx.html", ...],\n'
                '  "missing_pages": [],\n'
                '  "low_density_pages": [],\n'
                '  "ppt_gen_status": "ok | partial | failed"\n'
                '}\n'
                "```\n"
                "\n"
                "### 执行流程\n"
                "1. 输入校验：必填字段任一空 → failed\n"
                "2. 调用 P8.0 PrepareNode → 读资料 + 按页拆分，产出共享只读数据；prepare_status=failed → 直接 failed\n"
                "3. 调用 P8.1 PageWorkerNode → per-page 闭环"
                "（生成→密度判定→搜索补充→重写）"
                "→ page_files / missing_pages / low_density_pages\n"
                "4. 调用 P8.3 QAFixNode → qa_status / final_page_files / fix_report\n"
                "5. 汇总状态：missing 空 + low 空 + qa=ok → ok；qa=failed → failed；其余 partial\n"
                "\n"
                "### 失败兜底\n"
                "- 必填校验失败：直接返回 failed，不进入子节点\n"
                "- P8.0 prepare_status=failed：直接返回 failed，不进入 P8.1\n"
                "- 子节点透传错误，根节点不阻塞，按汇总规则归并状态\n"
            ),
            sub_plans=[
                PrepareNode(),
                PageWorkerNode(),
                QAFixNode(),
            ],
        )

    async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        required_fields = (
            "output_dir",
            "pages_dir",
            "style_file_path",
            "style_id",
        )
        for field in required_fields:
            if not str(inputs.get(field) or "").strip():
                logger.error("[P8] 必填字段 %s 为空，无法生成幻灯片", field)
                return {
                    "pages_dir": str(inputs.get("pages_dir") or ""),
                    "page_files": [],
                    "missing_pages": [],
                    "low_density_pages": [],
                    "ppt_gen_status": "failed",
                }

        page_count = int(inputs.get("page_count") or 0)
        if page_count <= 0:
            logger.error("[P8] page_count 非法 (%s)", inputs.get("page_count"))
            return {
                "pages_dir": str(inputs.get("pages_dir") or ""),
                "page_files": [],
                "missing_pages": [],
                "low_density_pages": [],
                "ppt_gen_status": "failed",
            }

        prep_result = await self.execute_subplan(self.sub_plans[0], inputs)
        if not isinstance(prep_result, dict) or prep_result.get("prepare_status") != "ok":
            logger.error("[P8] P8.0 预处理失败，终止生成")
            return {
                "pages_dir": str(inputs.get("pages_dir") or ""),
                "page_files": [],
                "missing_pages": list(range(1, page_count + 1)),
                "low_density_pages": [],
                "ppt_gen_status": "failed",
            }

        worker_inputs = {**inputs, **prep_result}
        worker_result = await self.execute_subplan(self.sub_plans[1], worker_inputs)
        qa_inputs = {**worker_inputs, **worker_result} if isinstance(worker_result, dict) else worker_inputs
        missing_pages = (
            list(worker_result.get("missing_pages") or [])
            if isinstance(worker_result, dict)
            else []
        )
        low_density_pages = (
            list(worker_result.get("low_density_pages") or [])
            if isinstance(worker_result, dict)
            else []
        )
        page_files = (
            list(worker_result.get("page_files") or [])
            if isinstance(worker_result, dict)
            else []
        )

        qa_result = await self.execute_subplan(self.sub_plans[2], qa_inputs)
        qa_status = "ok"
        final_page_files = page_files
        fix_report = ""
        if isinstance(qa_result, dict):
            qa_status = str(qa_result.get("qa_status") or "ok")
            final_page_files = list(qa_result.get("final_page_files") or page_files)
            fix_report = str(qa_result.get("fix_report") or "")

        if qa_status == "failed":
            ppt_gen_status = "failed"
        elif missing_pages or low_density_pages or qa_status == "partial":
            ppt_gen_status = "partial"
        else:
            ppt_gen_status = "ok"

        logger.info(
            "[P8] 完成 status=%s page=%d missing=%d low_density=%d",
            ppt_gen_status,
            len(final_page_files),
            len(missing_pages),
            len(low_density_pages),
        )

        return {
            "pages_dir": str(inputs.get("pages_dir") or ""),
            "page_files": final_page_files,
            "missing_pages": missing_pages,
            "low_density_pages": low_density_pages,
            "fix_report": fix_report,
            "ppt_gen_status": ppt_gen_status,
        }

    async def _execute_stream(self, inputs: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        result = await self._execute(inputs)
        status_map = {"ok": "ok", "partial": "warning", "failed": "error"}
        yield {
            **result,
            "node": self.plan_name,
            "status": status_map.get(result.get("ppt_gen_status", ""), "warning"),
            "message": (
                f"PPT 生成完成 status={result.get('ppt_gen_status')} "
                f"成功 {len(result.get('page_files', []))} 页"
            ),
        }