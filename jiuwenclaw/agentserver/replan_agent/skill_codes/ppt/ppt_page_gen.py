from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jiuwenclaw.agentserver.replan_agent.plan_node import AbortError, PlanNode
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


_CHART_CANDIDATE_TYPES = {"data", "comparison", "technology", "trend"}


def _extract_designer_section(text: str) -> str:
    """从 designer/SKILL.md 原文提取防溢出约束 + 语义区域指南章节。

    文件 IO 由 PrepareNode 通过 read_file 工具完成后传入 text，
    skill_code 中禁止直接做文件 IO（校验器禁止 open/read_text 等）。
    """
    if not text:
        return ""
    # 提取「防溢出硬性约束」章节到代码块结束
    start = text.find("### 防溢出硬性约束")
    if start == -1:
        return ""
    # 取到下一个 --- 分隔线或文件末尾
    end = text.find("\n---", start)
    if end == -1:
        end = len(text)
    css_section = text[start:end]

    # 提取「语义区域」相关内容（main 直接子元素规则）
    # 限定在 CSS 章节之前搜索，避免误匹配 CSS 章节之后的不相关内容
    search_end = start  # CSS 章节开始位置作为语义区域搜索上界
    sem_start = text.find("✅ 正确示例 - 单一主视觉页面可只有一个语义区域", 0, search_end)
    if sem_start == -1:
        sem_start = text.find("main 的直接子元素数量由页面叙事决定", 0, search_end)
    sem_end = text.find("---", sem_start) if sem_start != -1 else -1
    sem_section = ""
    if sem_start != -1 and sem_end != -1:
        sem_section = "\n\n### 语义区域划分指南\n" + text[sem_start:sem_end]

    return css_section + sem_section


_PRESET_STYLE_IDS = {"business-classic", "tech-minimal", "elegant-narrative", "industrial-tech"}
_DEFAULT_GEN_RETRY_ROUND = 1
_DEFAULT_DENSITY_RETRY_ROUND = 1


# 页面类型 → 模板 ID 默认映射（当 manifest 无 page_intents 时兜底）
_PAGE_TYPE_TO_TEMPLATE: dict[str, str] = {
    "cover": "cover-base",
    "intro": "cover-base",
    "agenda": "section-base",
    "chapter": "section-base",
    "section": "section-base",
    "conclusion": "section-base",
    "ending": "section-base",
    "data": "content-default",
    "trend": "content-default",
    "case": "content-cards",
    "comparison": "content-two-column",
    "technology": "content-default",
}

# 结构页类型集合（与 Charlie 分支一致）
_TEMPLATE_STRUCTURAL_TYPES = {
    "cover", "intro", "agenda", "chapter", "section", "conclusion", "ending",
}


def _looks_like_path(value: str) -> bool:
    return "/" in value or "\\" in value or value.endswith(".html")


_DEFAULT_STRUCTURAL_PAGES = 2

_CDN_HEAD_SNIPPET = (
    "### <head> CDN 引用（必须逐字使用，禁止替换为其他 CDN）\n"
    "```html\n"
    "<!-- Tailwind CSS（必选） -->\n"
    '<script src="https://cdn.digitalhumanai.top/slidagent/pptx-craft/assets/vendors/tailwind.js"></script>\n'
    "\n"
    "<!-- 字体引用 -->\n"
    '<link href="https://cdn.digitalhumanai.top/slidagent/pptx-craft/assets/css/fonts.css" rel="stylesheet" />\n'
    "\n"
    "<!-- FontAwesome 图标 -->\n"
    '<link href="https://cdn.digitalhumanai.top/slidagent/pptx-craft/assets/vendors/fontawesome/css/all.min.css"\n'
    ' rel="stylesheet" />\n'
    "\n"
    "<!-- ECharts 图表库 -->\n"
    '<script src="https://cdn.digitalhumanai.top/slidagent/pptx-craft/assets/vendors/echarts.min.js"></script>\n'
    "```\n"
    "⚠️ **禁止使用 cdn.tailwindcss.com、cdn.jsdelivr.net、cdnjs.cloudflare.com 等公共 CDN，"
    "必须使用上述 cdn.digitalhumanai.top 地址。**\n"
)


_DESIGN_RULES_DIGEST = (
    "### 视觉与布局硬约束（精选 17 条）\n"
    "1. 容器：`.ppt-slide { width:1280px; height:720px; overflow:hidden; box-sizing:border-box }`\n"
    "2. 安全区：`.content-safe { width:1220px; height:660px; margin:30px auto }`，主要内容必须放在安全区内；"
    "子元素禁止额外加 padding，否则导致双重边距\n"
    "3. 三级字号：标题 36-48px / 副标题 24-28px / 正文 16-20px\n"
    "4. 图表类型：时序数据→折线图(line)；对比数据→柱状图/分组柱状图(bar)；"
    "占比数据→饼图(pie)；多维能力对比→雷达图(radar)；禁止用图片占位\n"
    "4.1 图表渲染器（强制）：ECharts 必须用 `echarts.init(document.getElementById('xxx'), null, {renderer:'svg'})` "
    "单行初始化，禁止用变量赋值（如 `var chartDom=...; echarts.init(chartDom)`），"
    "禁止 Canvas 渲染器（会导致转 PPTX 后变位图）\n"
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
    "15. 内容页根节点必须同时携带 `class=\"ppt-slide\"`、`type=\"content\"` 与 `data-page-role=\"content\"`；"
    "`data-page-role` 不是旧 `type` 属性的替代品，两者并存\n"
    "16. 标题栏、页脚为跨页锚点片段（见风格文件「四、组件样式库」开头的「跨页锚点片段」说明），"
    "必须逐字复用 HTML 结构/class/间距，只改文字内容，禁止自行重新设计\n"
    "17. 标题、正文、图表标签、数据来源和数据卡片必须完整显示，禁止裁切或隐藏\n"
)


_HTML_SKELETON = (
    "### 标准 HTML 骨架（所有页面必须遵循，禁止改动结构）\n"
    "```html\n"
    '<div class="ppt-slide" type="content" data-page-role="content">\n'
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
    "- 根节点必须同时携带 `class=\"ppt-slide\"`、`type=\"content\"`、`data-page-role=\"content\"`\n"
    "- `content-safe` 用 `flex flex-col` 纵向排列 header/main/footer 三段\n"
    "- `main` 用 `grid grid-cols-2` 左右分列，恰好 2 个 `<section>` 直接子元素\n"
    "- 禁止把 header/footer 放进 main 内部；禁止 main 只有 1 个子元素\n"
)


_STRUCTURAL_DESIGN_RULES = (
    "### 视觉与布局硬约束（结构页精选 8 条）\n"
    "1. 容器：`.ppt-slide { width:1280px; height:720px; overflow:hidden; box-sizing:border-box }`\n"
    "2. 安全区：`.content-safe { width:1220px; height:660px; margin:30px auto }`\n"
    "3. 字号：封面标题 48-64px / 副标题 24-28px / 日期 18px；"
    "结束页标题 42-48px / 正文 22px\n"
    "4. 防溢出：单行文字不超容器宽度\n"
    "5. 配色与字体严格来自风格规范文件，禁止使用未定义的颜色或字体；"
    "页面背景色必须与风格规范一致（商务经典=白色 `bg-white`），禁止自行使用渐变或深色背景\n"
    "6. 布局：居中排列（`flex flex-col items-center justify-center`），"
    "不强制 grid-cols-2 双栏\n"
    "7. 留白：允许较高留白，不强制数据卡片、图表或数据来源页脚\n"
    "8. 全局禁止 `rounded-*` 类，所有元素 border-radius:0\n"
)

_STRUCTURAL_HTML_SKELETON = (
    "### 标准 HTML 骨架（结构页专用）\n"
    "```html\n"
    '<div class="ppt-slide">\n'
    '  <div class="content-safe flex flex-col items-center justify-center h-full">\n'
    "    <h1 class=\"text-center\">标题</h1>\n"
    "    <p class=\"text-center mt-4\">副标题</p>\n"
    "  </div>\n"
    '</div>\n'
    "```\n"
    "- 居中布局，不使用 grid-cols-2\n"
    "- 无需 header/main/footer 三段式，无需数据来源页脚\n"
)


_PAGE_TYPE_RE = re.compile(r"类型\*{0,2}[：:]\s*(\w+)", re.IGNORECASE)

_PAGE_LAYOUT_TEMPLATES = {
    "data": (
        "### 参考布局（data 类型，可根据内容调整布局比例和区域数量）\n"
        "```html\n"
        '<div class="content-safe flex flex-col gap-3 h-full">\n'
        '  <header class="flex-shrink-0">4-6 个关键数字卡片，grid grid-cols-6</header>\n'
        '  <main class="flex-1 min-h-0 grid grid-cols-[3fr_2fr] gap-3">\n'
        '    <section class="h-full min-h-0 overflow-hidden">6 个核心论点卡片，grid grid-cols-2 grid-rows-3 gap-2</section>\n'
        '    <section class="h-full min-h-0 overflow-hidden">ECharts 图表 + 对比表格</section>\n'
        '  </main>\n'
        '  <footer class="flex-shrink-0">数据来源汇总条</footer>\n'
        '</div>\n'
        "```\n"
        "- ECharts 图表类型根据数据形态选择（柱状图/饼图/雷达图）\n"
    ),
    "trend": (
        "### 参考布局（trend 类型，可根据内容调整布局比例和区域数量）\n"
        "```html\n"
        '<div class="content-safe flex flex-col gap-3 h-full">\n'
        '  <header class="flex-shrink-0">3 个关键数字卡片</header>\n'
        '  <main class="flex-1 min-h-0 flex gap-3 overflow-hidden">\n'
        '    <section class="flex-1 min-h-0 overflow-hidden">ECharts 折线图（趋势数据）</section>\n'
        '    <section class="w-[40%] min-h-0 overflow-hidden">4-6 个核心论点卡片，flex-col gap-2</section>\n'
        '  </main>\n'
        '  <footer class="flex-shrink-0">数据来源汇总条</footer>\n'
        '</div>\n'
        "```\n"
        "- ECharts 默认折线图(line)，数据形态更适合其他类型时可切换\n"
    ),
    "comparison": (
        "### 参考布局（comparison 类型，可根据内容调整布局比例和区域数量）\n"
        "```html\n"
        '<div class="content-safe flex flex-col gap-3 h-full">\n'
        '  <main class="flex-1 min-h-0 flex flex-col gap-3 overflow-hidden">\n'
        '    <div class="flex gap-3 flex-1 min-h-0">\n'
        '      <section class="flex-1 min-h-0 overflow-hidden">对比对象 A 的卡片（grid grid-cols-2 grid-rows-3）</section>\n'
        '      <section class="flex-1 min-h-0 overflow-hidden">对比对象 B 的卡片（grid grid-cols-2 grid-rows-3）</section>\n'
        '    </div>\n'
        '    <section class="flex-shrink-0">对比表格</section>\n'
        '  </main>\n'
        '  <footer class="flex-shrink-0">数据来源汇总条</footer>\n'
        '</div>\n'
        "```\n"
        "- ECharts 默认分组柱状图(grouped bar)，占比数据用饼图\n"
    ),
    "case": (
        "### 参考布局（case 类型，可根据内容调整布局比例和区域数量）\n"
        "```html\n"
        '<div class="content-safe flex flex-col gap-3 h-full">\n'
        '  <header class="flex-shrink-0">3 个关键数字卡片</header>\n'
        '  <main class="flex-1 min-h-0 grid grid-cols-[2fr_3fr] gap-3">\n'
        '    <section class="h-full min-h-0 overflow-hidden">6 个核心论点卡片，flex-col gap-2</section>\n'
        '    <section class="h-full min-h-0 overflow-hidden">ECharts 图表 + 关键数据表格</section>\n'
        '  </main>\n'
        '  <footer class="flex-shrink-0">案例素材详细描述 + 数据来源页脚</footer>\n'
        '</div>\n'
        "```\n"
    ),
    "technology": (
        "### 参考布局（technology 类型，可根据内容调整布局比例和区域数量）\n"
        "```html\n"
        '<div class="content-safe flex flex-col gap-3 h-full">\n'
        '  <header class="flex-shrink-0">4 个关键数字卡片</header>\n'
        '  <main class="flex-1 min-h-0 flex flex-col gap-3 overflow-hidden">\n'
        '    <section class="flex-1 min-h-0 overflow-hidden">ECharts 图表 + 对比表格</section>\n'
        '    <section class="flex-1 min-h-0 overflow-hidden">6 个核心论点卡片，grid grid-cols-3 grid-rows-2 gap-2</section>\n'
        '  </main>\n'
        '  <footer class="flex-shrink-0">数据来源汇总条</footer>\n'
        '</div>\n'
        "```\n"
        "- ECharts 图表选型（与 skill charts.md 数据类型表一致，直接按数据形态选）："
        "比较类别→柱状图(bar)；时间序列→折线图(line)；类别占比→饼图(pie)；"
        "多维数据比较→雷达图(radar)；两变量关系→散点图(scatter)；"
        "单一变量分布→直方图(histogram)；数据分布/离群值→箱线图(boxplot)；"
        "层次结构→树状图(treemap)；矩阵数据→热力图(heatmap)\n"
    ),
    "cover": (
        "### 推荐布局（cover 类型，封面页）\n"
        "```html\n"
        '<div class="content-safe flex flex-col items-center justify-center h-full">\n'
        '  <h1 class="text-[48px] font-bold text-center">演示标题</h1>\n'
        '  <p class="text-[24px] text-center mt-4">副标题</p>\n'
        '  <p class="text-[18px] text-center mt-2">日期</p>\n'
        '</div>\n'
        "```\n"
        "- 低密度页面，允许较高留白，不要求 grid-cols-2、数据卡片或图表\n"
    ),
    "ending": (
        "### 推荐布局（ending 类型，结束页）\n"
        "```html\n"
        '<div class="content-safe flex flex-col items-center justify-center h-full">\n'
        '  <h2 class="text-[42px] font-bold text-center">感谢聆听</h2>\n'
        '  <p class="text-[22px] text-center mt-4">联系方式（可选）</p>\n'
        '</div>\n'
        "```\n"
        "- 低密度页面，允许较高留白，不要求 grid-cols-2、数据卡片或图表\n"
    ),
}


def _detect_page_type(outline_page: str) -> str:
    if not outline_page:
        return ""
    match = _PAGE_TYPE_RE.search(outline_page)
    if match:
        return match.group(1).strip().lower()
    return ""


_STRUCTURAL_DENSITY_CHECKLIST = (
    "### 结构页密度检查（4 项，全部必须通过）\n"
    "1. 完整显示：核心内容未被裁切、滚动、折叠或省略\n"
    "2. 无大段文字：无连续 > 100 字段落\n"
    "3. 视觉层级：标题 → 副标题 → 正文 层级清晰\n"
    "4. 留白质量：留白服务于视觉聚焦，非空洞\n"
)

_DENSITY_CHECKLIST_DIGEST = (
    "### 内容密度检查（11 项，全部必须通过）\n"
    "1. 数据可视化：≥1 个 ECharts 图表 或 ≥3 个数据卡片（no_search 模式且页面为'数据有限'时可降至 2 个数据卡片）\n"
    "2. 核心要点：6-10 个列表项或卡片\n"
    "3. 装饰图标：≥3 个 FontAwesome 图标（class 含 `fa-`）\n"
    "4. 空白率：估算 < 30%\n"
    "5. 数据来源：页脚有标注（机构名 / 资料名）\n"
    "6. 无大段文字：无连续 > 100 字段落\n"
    "7. 视觉层级：标题 → 副标题 → 正文 → 注释 层级清晰\n"
    "8. 布局正确：main 元素采用双区域布局（如 `grid grid-cols-2`、`grid grid-cols-[3fr_2fr]`、"
    "`flex gap-4` 等），且恰好 2 个直接子元素（`<section>` 或 `<div>`）；"
    "禁止所有页面使用相同布局，需根据内容叙事选择不同布局比例和方向\n"
    "9. 完整显示：核心内容未使用 line-clamp、省略号、滚动或折叠隐藏\n"
    "10. 内容完整：标题、正文、图表标签、数据来源和数据卡片全部完整显示，无裁切\n"
    "11. ECharts SVG 检查：所有 echarts.init 调用必须包含 `{renderer:'svg'}` 参数，"
    "且使用 `document.getElementById('xxx')` 直接传参，禁止变量赋值\n"
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


# 匹配 <h1>/<h2> 中「第X页」占位符（X 为数字或中文数字）
_PLACEHOLDER_HEADING_RE = re.compile(
    r'(<(h[12])[^>]*>)\s*第\s*([\d一二三四五六七八九十]+)\s*页\s*(</\2>)',
    re.IGNORECASE,
)


def _extract_title_from_outline(outline_page: str) -> str:
    """从 outline 片段中提取页面标题，用于替换「第X页」占位符。

    outline 片段格式示例：
      ### P3: 类型*data | 标题*xxx | 研究需求*✅
      ### P3: xxx标题
    """
    if not outline_page:
        return ""
    for line in outline_page.splitlines():
        stripped = line.strip()
        if not stripped.startswith("### P"):
            continue
        # 去掉 "### P{N}:" 前缀
        rest = stripped.split(":", 1)[-1].strip() if ":" in stripped else ""
        if not rest:
            continue
        # 格式1: "类型*data | 标题*xxx | 研究需求*✅"
        if "标题" in rest:
            for seg in rest.split("|"):
                seg = seg.strip()
                if seg.startswith("标题"):
                    val = seg.split("*", 1)[-1].strip() if "*" in seg else seg.split("：", 1)[-1].strip()
                    val = val.strip("*").strip()
                    if val and val != "标题":  # 排除空值和独立"标题"segment
                        return val
            # 标题字段存在但值为空或字面量"标题"，格式异常，跳过格式2 fallback
            continue
        # 格式2: 直接是标题文本
        if rest and not rest.startswith("类型"):
            return rest
    return ""


def _replace_placeholder_headings(html: str, outline_page: str) -> str:
    """后置校验：将 <h1>/<h2> 中的「第X页」占位符替换为 outline 中的实际标题。"""
    title = _extract_title_from_outline(outline_page)
    if not title:
        return html

    def _replacer(m: re.Match) -> str:
        return f"{m.group(1)}{title}{m.group(4)}"

    return _PLACEHOLDER_HEADING_RE.sub(_replacer, html)


# 匹配 echarts.init(xxx) 未带 renderer 参数的单参数调用
# 支持两种传参：变量名 或 document.getElementById('xxx') 直接传参
# 多参数调用（含 renderer 等）天然不匹配，无需额外排除
_ECHARTS_INIT_NO_SVG_RE = re.compile(
    r"echarts\.init\(\s*"
    r"(?:"
    r"(\w+)"                                              # 形式1: 变量名
    r"|(document\.getElementById\(\s*['\"][^'\"]+['\"]\s*\))"  # 形式2: getElementById
    r")\s*\)"
)


def _fix_echarts_svg_renderer(html: str) -> str:
    """后置校验：确保所有 echarts.init 调用使用 SVG 渲染器。

    匹配两种单参数调用：变量名 或 document.getElementById('xxx')，
    自动补充 {renderer:'svg'} 参数。
    已有 renderer 参数或多参数调用不处理。
    """
    def _replacer(m: re.Match) -> str:
        arg = (m.group(1) or m.group(2) or "").strip()
        return f"echarts.init({arg}, null, {{renderer:'svg'}})"

    return _ECHARTS_INIT_NO_SVG_RE.sub(_replacer, html)


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
    "内容被隐藏": "移除 line-clamp、text-overflow:ellipsis、overflow:auto/scroll、max-height 限制等隐藏手段，确保核心内容完整可见",
    "核心内容缺失": "检查标题、正文、图表标签、数据来源和数据卡片是否全部完整显示，补充缺失的内容元素",
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


# 按图片数量选择布局模板（精简自 SKILL.md 图片布局规范）
_IMAGE_LAYOUT_TEMPLATES: dict[int, str] = {
    1: (
        "### 图片布局（1 张图）\n"
        "- `usage=cover` → 全幅背景图，文字用 `z-10` 叠加\n"
        "- `usage=content` → 单图占一侧，另一侧文字\n"
        "```html\n"
        '<img src="..." class="w-full h-full object-contain" />\n'
        "```\n"
    ),
    2: (
        "### 图片布局（2 张图）\n"
        "- 推荐左右对半分或一大一小\n"
        "```html\n"
        '<div class="grid grid-cols-2 gap-3 flex-1 min-h-0">\n'
        '  <img src="..." class="w-full h-full object-contain" />\n'
        '  <img src="..." class="w-full h-full object-contain" />\n'
        '</div>\n'
        "```\n"
    ),
    3: (
        "### 图片布局（3 张图）\n"
        "- 推荐「左 1 右 2」：左侧大图，右侧上下两小图\n"
        "```html\n"
        '<div class="grid grid-cols-3 gap-3 flex-1 min-h-0">\n'
        '  <div class="col-span-2"><img src="..." class="w-full h-full object-contain" /></div>\n'
        '  <div class="flex flex-col gap-2">\n'
        '    <img src="..." class="w-full flex-1 min-h-0 object-contain" />\n'
        '    <img src="..." class="w-full flex-1 min-h-0 object-contain" />\n'
        '  </div>\n'
        '</div>\n'
        "```\n"
    ),
    4: (
        "### 图片布局（4 张图）\n"
        "- 推荐 2×2 宫格\n"
        "```html\n"
        '<div class="grid grid-cols-2 grid-rows-2 gap-3 flex-1 min-h-0">\n'
        '  <img src="..." class="w-full h-full object-contain" />\n'
        '  <img src="..." class="w-full h-full object-contain" />\n'
        '  <img src="..." class="w-full h-full object-contain" />\n'
        '  <img src="..." class="w-full h-full object-contain" />\n'
        '</div>\n'
        "```\n"
    ),
}

_IMAGE_LAYOUT_TEMPLATE_MANY = (
    "### 图片布局（{n} 张图）\n"
    "- 推荐网格布局，每行 3-4 张\n"
    "```html\n"
    '<div class="grid grid-cols-{cols} gap-3 flex-1 min-h-0">\n'
    '  <!-- {n} 张图片，每张 object-contain -->\n'
    '</div>\n'
    "```\n"
)


def _build_image_section(image_map_page: str) -> str:
    """根据本页图片素材描述和图片数量，构造图片素材 section。"""
    if not image_map_page:
        return ""
    # 统计图片数量（每行一个 "- path:" 开头）
    img_count = image_map_page.count("- path:")
    layout = _IMAGE_LAYOUT_TEMPLATES.get(img_count)
    if layout is None:
        cols = 4 if img_count >= 7 else 3
        layout = _IMAGE_LAYOUT_TEMPLATE_MANY.format(n=img_count, cols=cols)

    return (
        "\n### 图片素材（必须使用）\n"
        f"{image_map_page}\n"
        "- `usage=cover` → 用作全幅背景图："
        "`<img src=\"...\" class=\"absolute inset-0 w-full h-full object-cover\">`，"
        "文字内容用 `z-10` 叠加在上\n"
        "- `usage=content` → 用作内容配图："
        "`<img src=\"...\" class=\"w-full h-full object-contain\">`\n"
        "- 使用 `<img>` 标签引用 `path` 字段指定的路径（相对路径，直接使用）\n"
        "- 图片容器用 `min-h-0 overflow-hidden` 防溢出\n"
        f"\n{layout}"
    )


def _build_page_prompt(
    page_number: int,
    style_id: str,
    style_text: str,
    outline_page: str,
    research_page: str,
    *,
    designer_md_text: str = "",
    charts_md_text: str = "",
    outline_is_full: bool = False,
    research_is_full: bool = False,
    rewrite_hint: str = "",
    original_html: str = "",
    image_map_page: str = "",
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

    page_type = _detect_page_type(outline_page)
    # 与 skill SKILL.md「页面研究契约」一致：用研究需求字段判断是否结构页
    # 大纲中格式为「✅ 页研究查询: ...」或「✅ 数据需求: ...」，有则为内容页
    # 无上述字段则为结构页（仅依据大纲）
    has_research_need = "✅" in outline_page and (
        "页研究查询" in outline_page or "数据需求" in outline_page or "研究需求" in outline_page
    )
    is_structural = not has_research_need

    if no_outline:
        outline_label = "大纲（未提供，请根据重写指引和搜索补充数据自行推断页面类型与布局）"
    if is_structural:
        research_label = "（结构页，无需研究素材，仅依据大纲内容生成）"
    elif no_research:
        research_label = "研究报告（未提供，请根据重写指引和搜索补充数据自行生成内容）"

    if is_structural:
        fusion_rules = (
            "- 本页为结构页，内容仅从大纲提取标题、副标题等\n"
            "- 无需研究报告、搜索补充或数据可视化\n"
            "- 保持低密度，允许较高留白\n"
        )
    elif outline_is_full or research_is_full:
        fusion_rules = (
            f"- 以下素材为完整文档，你**仅负责第 {page_number} 页**，"
            f"请从全文中定位 `### P{page_number}:` 章节，仅使用该页内容\n"
            "- 大纲提供页面类型与数据需求，决定页面布局和内容方向\n"
            "- 研究报告提供核心论点、关键数据、案例素材，决定页面具体内容\n"
            "- 严禁将其他页面的内容混入本页\n"
        )
    elif no_outline or no_research:
        fusion_rules = (
            "- 部分素材缺失，请根据重写指引和搜索补充数据生成内容\n"
            "- 严格遵循视觉风格规范和布局硬约束\n"
            "- 确保所有文字为真实内容，禁止占位文本\n"
        )
    else:
        fusion_rules = (
            "- 大纲提供页面类型与数据需求，决定页面布局和内容方向\n"
            "- 研究报告提供核心论点、关键数据、案例素材，决定页面具体内容\n"
            "- 上述大纲 + 研究报告中的全部信息点都必须体现\n"
        )

    layout_template = _PAGE_LAYOUT_TEMPLATES.get(page_type, "")

    density_checklist = _STRUCTURAL_DENSITY_CHECKLIST if is_structural else _DENSITY_CHECKLIST_DIGEST
    design_rules = _STRUCTURAL_DESIGN_RULES if is_structural else _DESIGN_RULES_DIGEST
    html_skeleton = _STRUCTURAL_HTML_SKELETON if is_structural else _HTML_SKELETON

    # 注入 skill designer 规范（防溢出 CSS 约束 + 语义区域指南）
    # 文件内容由 PrepareNode 通过 read_file 工具读取后传入
    designer_section = ""
    if not is_structural and designer_md_text:
        designer_md = _extract_designer_section(designer_md_text)
        if designer_md:
            designer_section = f"\n### skill designer 约束（必须遵守）\n{designer_md}\n"

    # 图表候选页注入 charts.md（独立判断，不依赖 SKILL.md 读取结果）
    if not is_structural and page_type in _CHART_CANDIDATE_TYPES and charts_md_text:
        designer_section += f"\n### ECharts 图表编码规范（必须遵守）\n{charts_md_text}\n"

    # 布局多样性约束：禁止连续两页相同布局
    diversity_rule = ""
    if not is_structural:
        diversity_rule = (
            "\n### 布局多样性约束\n"
            "- 禁止连续两页使用完全相同的 main 布局结构（grid 列数、子元素数量、比例分配）\n"
            "- 主动使用不同的布局比例（如 `flex-[3]_flex-[2]`、`grid-cols-[3fr_2fr]`、`flex-[5]_flex-[4]` 等）\n"
            "- 根据内容叙事选择布局，而非机械套用模板\n"
        )

    return (
        "## 0. 输出要求（最高优先级）\n"
        f"- 输出**第 {page_number} 页**完整 HTML（含 <!DOCTYPE>、<html>、<head>、<body>）\n"
        "- 严禁任何解释、注释、Markdown 代码块包裹，只输出 HTML 原文\n"
        "- 页面尺寸严格 1280×720px\n"
        '- 必须包含 `<div class="ppt-slide">` 容器\n'
        "- 禁止在思考过程中反复计算像素或纠结布局，参考下方布局示例并根据内容调整\n"
        "- 一次性输出完整 HTML，禁止输出'final code''truly final'等反复确认语句\n"
        "\n"
        "## 1. 视觉风格规范（强制遵守）\n"
        f"{style_text}\n"
        f"{preset_clause}"
        "\n"
        f"{_CDN_HEAD_SNIPPET}"
        "\n"
        f"{design_rules}"
        f"{designer_section}"
        f"{diversity_rule}"
        "\n"
        f"{html_skeleton}"
        "\n"
        f"{layout_template}"
        "\n"
        f"{density_checklist}"
        "\n"
        "## 2. 内容素材\n"
        "\n"
        f"### {outline_label}\n"
        f"{outline_page}\n"
        "\n"
        f"### {research_label}\n"
        f"{research_page}\n"
        f"{_build_image_section(image_map_page)}"
        "\n"
        "## 3. 内容融合规则\n"
        f"{fusion_rules}"
        f"{rewrite_section}"
        "\n"
        "## 4. 页面内容预算（写 HTML 前必须先完成）\n"
        "- 逐项识别核心结论、关键数据、必要论据和可舍弃的辅助细节\n"
        "- 制定预算：页面类型、密度、标题行数、区域比例、卡片/要点上限、正文行数、最小字号、目标留白区间\n"
        "- 预留至少 8% 的垂直缓冲，用于字体差异、图表标签和 PPTX 转换误差\n"
        "- 若核心内容超过预算，先提炼与重排；仍无法容纳时拆页，禁止裁切或持续缩小字号\n"
        "\n"
        "## 5. 任务\n"
        f"你负责生成**第 {page_number} 页** HTML。仅生成该页，直接输出 HTML 原文。"
        "先产出可运行 HTML，再按密度检查清单做小步修正；禁止在写文件前反复做像素级完整规划。"
        "生成时必须同时满足上述「内容密度检查（11 项）」全部要求，"
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
    image_map_page: str  # 本页图片素材描述（空串=无图）
    designer_md_text: str  # designer/SKILL.md 原文（由 PrepareNode 通过 read_file 读取）
    charts_md_text: str  # designer/charts.md 原文（由 PrepareNode 通过 read_file 读取）


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
                "- `outline.md` / `research-P{N}.md` / 风格文件均已落盘\n"
                "\n"
                "### 输入\n"
                "- `page_count`（必填）: N 页\n"
                "- `output_dir`（必填）: 工作目录（用于读 outline/research-P{N}.md）\n"
                "- `style_file_path`（必填）: 风格文件绝对路径\n"
                "\n"
                "### 输出\n"
                "- `prepare_status`: ok / failed\n"
                "- `outline_pages`: 按页拆分的 {页码: 片段}（拆分失败为空 dict，下游回退全文）\n"
                "- `research_pages`: 逐页读取的 {页码: research-P{N}.md 内容}（文件缺失时该页缺失）\n"
                "- `outline_text` / `style_text`: 全文（供下游回退与重写复用）\n"
                "- `all_pages`: 1..N 页码列表\n"
                "\n"
                "### 执行流程\n"
                "1. 读取 outline.md / style_file_path（任一失败 → prepare_status=failed）\n"
                "2. 按 `### P{N}:` 章节拆分 outline，每页只取对应片段；拆分失败时回退全文\n"
                "3. 逐页读取 research-P{N}.md（1..page_count），文件缺失时该页 research_pages 缺失\n"
                "4. 返回共享只读数据，供 P8.1 per-page worker 复用\n"
                "\n"
                "### 失败兜底\n"
                "- 读 outline/style 失败：prepare_status=failed，根节点直接终止，不进入 P8.1\n"
                "- 某页 research-P{N}.md 缺失：该页 research_pages 缺失，下游 worker 仅依据 outline 生成\n"
            ),
        )

    async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        page_count = int(inputs.get("page_count") or 0)
        total_pages = int(
            inputs.get("total_pages") or (page_count + _DEFAULT_STRUCTURAL_PAGES)
        )
        output_dir = str(inputs.get("output_dir") or "").strip()
        style_file_path = str(inputs.get("style_file_path") or "").strip()

        outline_text = await self._read_file(f"{output_dir}/outline.md")
        style_text = await self._read_file(style_file_path)

        if not outline_text or not style_text:
            logger.error(
                "[P8.0] 资料读取失败 outline=%d style=%d",
                len(outline_text),
                len(style_text),
            )
            return {
                "prepare_status": "failed",
                "outline_pages": {},
                "research_pages": {},
                "outline_text": outline_text,
                "style_text": style_text,
                "all_pages": list(range(1, total_pages + 1)) if total_pages > 0 else [],
            }

        outline_pages = _split_md_pages(outline_text)
        if not outline_pages:
            logger.warning("[P8.0] outline.md 未拆分到任何页面章节，下游回退全文")

        # 逐页读取 research-P{N}.md（不再读取单文件 research.md）
        # 遍历 total_pages（含结构页），❌ 页无 research 文件会跳过
        all_pages = list(range(1, total_pages + 1)) if total_pages > 0 else sorted(outline_pages.keys())
        research_pages: dict[int, str] = {}
        for p in all_pages:
            research_path = f"{output_dir}/research-P{p}.md"
            research_text_p = await self._read_file(research_path)
            # 校验内容是有效的 research 片段（以 ### P 开头），过滤 read_file 错误消息
            if research_text_p and research_text_p.lstrip().startswith("### P"):
                research_pages[p] = research_text_p
            else:
                logger.warning("[P8.0] research-P%d.md 不存在或内容无效", p)

        # 读取 image_map.json（P6.5 产出，供 P8 注入图片素材）
        image_map_path = str(inputs.get("image_map_path") or "").strip()
        image_map: dict[str, Any] = {}
        if image_map_path:
            raw = await self._read_file(image_map_path)
            if raw:
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict):
                        # 只保留页码 key（过滤 metadata），转为 {str(page_num): [img, ...]}
                        for key, value in parsed.items():
                            if key != "metadata" and isinstance(value, list):
                                image_map[key] = value
                except Exception as e:
                    if isinstance(e, AbortError):
                        raise
                    logger.warning("[P8.0] image_map.json 解析失败: %s", e)

        # 读取 skill designer 规范文件（通过 read_file 工具，skill_code 禁止直接 IO）
        pptx_root = str(inputs.get("pptx_root") or _PPT_DIR)
        designer_md_text = await self._read_file(f"{pptx_root}/designer/SKILL.md")
        charts_md_text = await self._read_file(f"{pptx_root}/designer/charts.md")

        logger.info(
            "[P8.0] 预处理完成 outline_pages=%d research_pages=%d image_map_pages=%d",
            len(outline_pages),
            len(research_pages),
            len(image_map),
        )
        return {
            "prepare_status": "ok",
            "outline_pages": outline_pages,
            "research_pages": research_pages,
            "outline_text": outline_text,
            "style_text": style_text,
            "all_pages": all_pages,
            "image_map": image_map,
            "designer_md_text": designer_md_text,
            "charts_md_text": charts_md_text,
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
                "- `outline_text` / `style_text`（来自 P8.0）: 全文，拆分失败时回退\n"
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
                "- `outline_text` / `style_text`（透传给 P8.2）\n"
                "\n"
                "### 执行流程（per-page 闭环，N 页 asyncio.gather 并发）\n"
                "对每一页独立执行：\n"
                "1. 生成阶段：用该页 outline 片段 + research 片段 + 风格规范 + 视觉与布局硬约束构造 prompt，"
                "调 LLM 生成 HTML；剥 ```html 包裹 → 校验（含 <!DOCTYPE> + ppt-slide 容器）→ write_file 落盘\n"
                "   - 失败按 gen_retry_round 重试（仅本页）\n"
                "   - 重试后仍失败 → 进 missing_pages，该页闭环终止\n"
                "2. 密度判定阶段：调 LLM 做 11 项密度检查（受控 JSON 输出），叠加程序化后置校验（echarts/card 计数）\n"
                "   - 检查项：数据可视化 / 核心要点 / 装饰图标 / 空白率 / 数据来源 / 大段文字 / 视觉层级 / 布局正确 / 完整显示 / 内容完整\n"
                "   - 数据可视化阈值：≥1 个 ECharts 图表 或 ≥3 个数据卡片（no_search 模式降至 2 个）\n"
                "3. 不通过 → 修复阶段（按 density_retry_round 轮）：\n"
                "   a. 分析缺失项，判断是否需要搜索补充数据\n"
                "   b. 若缺数据可视化/缺案例/缺数据来源 → 调用 `web_search` 搜索补充：\n"
                "      - 缺数据可视化：搜索 `\"{主题} 市场规模 数据\"` / `\"{主题} 增长率 统计\"`，获取可图表化的数据点\n"
                "      - 缺案例：搜索 `\"{主题} 应用案例 实践\"`，获取真实案例\n"
                "      - 缺数据来源：搜索 `\"{主题} 行业报告\"`，获取权威机构名称\n"
                "      - 搜索优先获取最近 1-2 年数据，优先权威来源\n"
                "      - 数据来源标注使用 research-P{N}.md 中的来源\n"
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
                "| 多维能力对比（≥3 维度） | 雷达图(radar) |\n"
                "| 两变量相关性 | 散点图(scatter) |\n"
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
        style_text = str(inputs.get("style_text") or "")
        all_pages: list[int] = list(inputs.get("all_pages") or [])
        image_map: dict[str, Any] = inputs.get("image_map") or {}
        designer_md_text = str(inputs.get("designer_md_text") or "")
        charts_md_text = str(inputs.get("charts_md_text") or "")

        if not pages_dir or not all_pages:
            logger.error("[P8.1] 必填输入缺失，跳过生成")
            return {
                "page_files": [],
                "missing_pages": list(all_pages),
                "low_density_pages": [],
                "density_report": {},
                "outline_text": outline_full,
                "style_text": style_text,
            }

        tasks = [
            self._run_page_pipeline(
                page_num=p,
                pages_dir=pages_dir,
                style_id=style_id,
                style_text=style_text,
                outline_page=outline_pages.get(p, outline_full),
                research_page=research_pages.get(p, ""),
                outline_is_full=p not in outline_pages,
                research_is_full=False,
                search_mode=search_mode,
                topic=topic,
                gen_retry_round=gen_retry_round,
                density_retry_round=density_retry_round,
                image_map=image_map,
                designer_md_text=designer_md_text,
                charts_md_text=charts_md_text,
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
        image_map: dict[str, Any],
        designer_md_text: str = "",
        charts_md_text: str = "",
    ) -> dict[str, Any]:
        """单页闭环：生成(含重试) → 密度判定 → 搜索补充+重写(含重试)。"""
        path = f"{pages_dir}/page-{page_num}.pptx.html"

        # 从 image_map 中提取本页图片素材描述
        page_images = image_map.get(str(page_num), [])
        image_map_page = ""
        if page_images:
            lines = []
            for img in page_images:
                path_val = str(img.get("path", ""))
                lines.append(
                    f"- path: {path_val}, usage: {img.get('usage', 'content')}, "
                    f"description: {img.get('description', '')}, type: {img.get('type', '')}"
                )
            image_map_page = "\n".join(lines)

        ctx = PageGenContext(
            page_num=page_num,
            style_id=style_id,
            style_text=style_text,
            outline_page=outline_page,
            research_page=research_page,
            outline_is_full=outline_is_full,
            research_is_full=research_is_full,
            image_map_page=image_map_page,
            designer_md_text=designer_md_text,
            charts_md_text=charts_md_text,
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
        # 与 _build_page_prompt 一致：基于「页研究查询/数据需求」字段判定结构页
        has_research_need = "✅" in outline_page and (
            "页研究查询" in outline_page or "数据需求" in outline_page or "研究需求" in outline_page
        )
        is_structural = not has_research_need
        total_rounds = max(density_retry_round + 1, 1)
        for round_idx in range(total_rounds):
            current = await self._read_file(path)
            if not current:
                logger.warning("[P8.1] 页面 %d 重检时读取失败，保守判通过", page_num)
                break
            report = await self._check_one(page_num, current, search_mode, outline_page)
            if report.get("pass", True):
                break
            if round_idx == total_rounds - 1:
                low_density = True
                logger.info("[P8.1] 页面 %d 密度重试用尽，进 low_density", page_num)
                break

            if is_structural:
                supplement = ""
            else:
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
                    image_map_page=ctx.image_map_page,
                    designer_md_text=ctx.designer_md_text,
                    charts_md_text=ctx.charts_md_text,
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
        # 后置校验：替换「第X页」标题占位符为 outline 中的实际标题
        html = _replace_placeholder_headings(html, ctx.outline_page)
        html = _fix_echarts_svg_renderer(html)
        return html

    async def _check_one(
        self,
        page_num: int,
        html: str,
        search_mode: str,
        outline_page: str = "",
    ) -> dict[str, Any]:
        """单页密度判定（LLM + 程序化后置校验）。LLM 异常保守判通过。"""
        # 与 _build_page_prompt 一致：基于「页研究查询/数据需求」字段判定结构页
        has_research_need = outline_page and "✅" in outline_page and (
            "页研究查询" in outline_page or "数据需求" in outline_page or "研究需求" in outline_page
        )
        is_structural = not has_research_need

        if is_structural:
            checklist = _STRUCTURAL_DENSITY_CHECKLIST
            failed_enum = "内容被裁切 / 大段文字 / 视觉层级混乱 / 空白失衡"
        else:
            checklist = _DENSITY_CHECKLIST_DIGEST
            no_search_hint = ""
            if search_mode == "no_search":
                no_search_hint = (
                    "\n注意：当前为 no_search 模式，标注'数据有限'的页面，"
                    "数据可视化阈值降至 ≥2 个数据卡片。\n"
                )
            failed_enum = (
                "缺数据可视化 / 核心要点不足 / 缺装饰图标 / 空白率过高 / "
                "缺数据来源 / 大段文字 / 视觉层级混乱 / 布局错误 / "
                "内容被隐藏 / 核心内容缺失"
            )

        prompt = (
            "请对以下 PPT 单页 HTML 做内容密度检查，按清单逐项判定，仅输出 JSON。\n\n"
            f"{checklist}"
            f"{no_search_hint if not is_structural else ''}"
            "\nHTML 内容：\n"
            "```html\n"
            f"{html}\n"
            "```\n\n"
            "输出 JSON（受控字段）：\n"
            '{"pass": true/false, "failed_items": [...], "reason": "简要说明"}\n'
            f"failed_items 仅可从以下取值：\n{failed_enum}\n"
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
            if not is_structural:
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
                    image_map_page=ctx.image_map_page,
                    designer_md_text=ctx.designer_md_text,
                    charts_md_text=ctx.charts_md_text,
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
        # 后置校验：替换「第X页」标题占位符为 outline 中的实际标题
        html = _replace_placeholder_headings(html, ctx.outline_page)
        html = _fix_echarts_svg_renderer(html)
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
    """P8.2 — 完整性检查 + cli.js fix（对应 SKILL Stage 7.5）。"""

    def __init__(self) -> None:
        super().__init__(
            plan_name="p8_2_qa_fix",
            instruction=(
                "## P8.2 QA 与自动修复\n"
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
        total_pages = int(
            inputs.get("total_pages") or (page_count + _DEFAULT_STRUCTURAL_PAGES)
        )

        if not pages_dir:
            logger.error("[P8.2] pages_dir 为空")
            return {
                "qa_status": "failed",
                "final_page_files": [],
                "fix_report": "pages_dir empty",
            }

        completeness_ok, page_files = await self._check_completeness(pages_dir, total_pages)
        qa_status = "ok" if completeness_ok else "partial"

        fix_report = ""
        try:
            pptx_root = str(inputs.get("pptx_root") or _PPT_DIR)
            # 按页并发 fix（1.1.19a+ 支持 --pages 参数）
            page_nums = [int(f.replace("page-", "").replace(".pptx.html", ""))
                         for f in page_files if f.startswith("page-") and f.endswith(".pptx.html")]
            page_nums.sort()
            if page_nums:
                sem = asyncio.Semaphore(10)

                async def _fix_one(pn: int) -> tuple[int, bool, str]:
                    async with sem:
                        cmd = f"{cli_path('fix', pptx_root)} {quote_path(pages_dir + '/')} --fix --pages {pn}"
                        r = await run_bash(self, cmd, timeout_seconds=300, required=False, workdir=pptx_root)
                        out = combined_output(r)[:500]
                        ok = r.exit_code == 0
                        if not ok:
                            logger.warning("[P8.2] page-%d fix 失败 exit=%d", pn, r.exit_code)
                        return pn, ok, out
                results = await asyncio.gather(*[_fix_one(p) for p in page_nums], return_exceptions=True)
                failed_pages = [
                    r[0] for r in results
                    if isinstance(r, tuple) and not r[1]
                ] if results else []
                # 处理异常情况
                exc_pages = [page_nums[i] for i, r in enumerate(results) if isinstance(r, Exception)]
                if exc_pages:
                    logger.error("[P8.2] fix 异常页: %s", exc_pages)
                    qa_status = "partial"
                if failed_pages:
                    logger.warning("[P8.2] fix 失败页: %s", failed_pages)
                    qa_status = "partial"
                else:
                    logger.info("[P8.2] cli.js fix 完成 (per-page 并发 %d 页)", len(page_nums))
                fix_parts = []
                for r in results:
                    if isinstance(r, tuple):
                        pn, ok, _ = r
                    else:
                        pn, ok = 0, False
                    fix_parts.append(f"page-{pn}: {'ok' if ok else 'fail'}")
                fix_report = "; ".join(fix_parts)
            else:
                fix_report = "no pages to fix"
        except BashExecError as e:
            logger.error("[P8.2] cli.js fix 异常: %s", e)
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
            "[P8.2] _check_completeness start pages_dir=%s page_count=%d has_list_dir=%s has_glob=%s",
            pages_dir,
            page_count,
            self.has_tool("list_dir"),
            self.has_tool("glob"),
        )
        if self.has_tool("list_dir"):
            try:
                result = await self.call_tool("list_dir", path=pages_dir)
                files = self._parse_listing(result)
                logger.debug("[P8.2] list_dir 解析结果 files=%d", len(files))
            except Exception as e:
                if isinstance(e, AbortError):
                    raise
                logger.warning("[P8.2] list_dir 失败，回退 glob: %s", e)
                files = []

        if not files and self.has_tool("glob"):
            try:
                result = await self.call_tool(
                    "glob",
                    pattern="page-*.pptx.html",
                    path=pages_dir,
                )
                files = self._parse_listing(result)
                logger.debug("[P8.2] glob 解析结果 files=%d", len(files))
            except Exception as e:
                if isinstance(e, AbortError):
                    raise
                logger.warning("[P8.2] glob 失败: %s", e)
                files = []

        page_files = sorted(
            {f for f in files if f.startswith("page-") and f.endswith(".pptx.html")}
        )

        if page_count <= 0:
            return bool(page_files), page_files

        completeness_ok = len(page_files) == page_count
        if not completeness_ok:
            logger.warning(
                "[P8.2] 完整性不足 actual=%d expected=%d",
                len(page_files),
                page_count,
            )
        return completeness_ok, page_files

    def _parse_listing(self, result: Any) -> list[str]:
        if result is None:
            return []
        logger.debug(
            "[P8.2] _parse_listing input type=%s repr=%.500s",
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
            "[P8.2] _parse_listing 无法解析 result type=%s repr=%.300s",
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
                "1. 把 outline.md + research-P{N}.md + 风格文件转成 N 个 page-{N}.pptx.html\n"
                "2. 三阶段串行编排：预处理 → per-page 闭环生成 → QA 与自动修复\n"
                "   - per-page 闭环内部 N 页 asyncio.gather 并发，单页内生成→密度判定→搜索补充→重写串行\n"
                "3. 不区分单 Agent 模式，LLM 并发度由框架 semaphore 控制\n"
                "\n"
                "### 输入\n"
                "- `output_dir`（必填）: 工作目录（含 outline.md / research-P{N}.md）\n"
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
                "4. 调用 P8.2 QAFixNode → qa_status / final_page_files / fix_report\n"
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
        # 模板包分支优先判断：style_mode == template_pack 时走 template-filler 流程
        style_mode = str(inputs.get("style_mode") or "").strip()
        if style_mode == "template_pack":
            # template_pack 分支只需要 pack_dir，不需要 style_file_path
            pack_dir = str(inputs.get("pack_dir") or "").strip()
            if not pack_dir:
                logger.error("[P8] template_pack 分支必填字段 pack_dir 为空")
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
            total_pages = int(
                inputs.get("total_pages") or (page_count + _DEFAULT_STRUCTURAL_PAGES)
            )
            return await self._execute_template_pack(inputs, page_count, total_pages)

        # 非 template_pack 分支：需要 style_file_path 等字段
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

        total_pages = int(
            inputs.get("total_pages") or (page_count + _DEFAULT_STRUCTURAL_PAGES)
        )

        prep_result = await self.execute_subplan(self.sub_plans[0], inputs)
        if not isinstance(prep_result, dict) or prep_result.get("prepare_status") != "ok":
            logger.error("[P8] P8.0 预处理失败，终止生成")
            return {
                "pages_dir": str(inputs.get("pages_dir") or ""),
                "page_files": [],
                "missing_pages": list(range(1, total_pages + 1)),
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
            "__artifact__": {
                "info": {
                    "ppt_gen_status": ppt_gen_status,
                    "page_count": len(final_page_files),
                    "missing_count": len(missing_pages),
                },
                "files": [{"path": f, "desc": "PPT页面"} for f in final_page_files] if final_page_files else [],
            },
        }

    async def _execute_template_pack(
        self,
        inputs: dict[str, Any],
        page_count: int,
        total_pages: int,
    ) -> dict[str, Any]:
        """模板包分支：调用 template-filler 脚本 + LLM 填充生成页面。

        流程：preflight → 逐页(seed → LLM 填充) → check
        """
        import json as _json

        pack_dir = str(inputs.get("pack_dir") or "").strip()
        output_dir = str(inputs.get("output_dir") or "").strip()
        pages_dir = str(inputs.get("pages_dir") or "").strip()
        pptx_root = str(inputs.get("pptx_root") or _PPT_DIR).strip()

        if not pack_dir or not pages_dir:
            logger.error("[P8-TP] pack_dir 或 pages_dir 为空")
            return {
                "pages_dir": pages_dir,
                "page_files": [],
                "missing_pages": list(range(1, total_pages + 1)),
                "low_density_pages": [],
                "ppt_gen_status": "failed",
            }

        # 1. preflight 预检
        try:
            preflight_cmd = (
                f"{fill_js_path(pptx_root)} preflight "
                f"{quote_path(pack_dir)} {quote_path(output_dir)} {quote_path(pages_dir)}"
            )
            await run_bash(
                self, preflight_cmd,
                timeout_seconds=60, required=True, workdir=pptx_root,
            )
            logger.info("[P8-TP] preflight 通过")
        except BashExecError as e:
            logger.error("[P8-TP] preflight 失败: %s", e)
            return {
                "pages_dir": pages_dir,
                "page_files": [],
                "missing_pages": list(range(1, total_pages + 1)),
                "low_density_pages": [],
                "ppt_gen_status": "failed",
            }

        # 2. 读取 outline.md 并按页拆分
        outline_text = await self._read_file(f"{output_dir}/outline.md")
        if not outline_text:
            logger.error("[P8-TP] outline.md 读取失败")
            return {
                "pages_dir": pages_dir,
                "page_files": [],
                "missing_pages": list(range(1, total_pages + 1)),
                "low_density_pages": [],
                "ppt_gen_status": "failed",
            }
        outline_pages = _split_md_pages(outline_text)

        # 3. 读取 template-manifest.json 获取模板列表
        manifest = await self._load_template_manifest(pack_dir, pptx_root)

        # 4. 逐页 seed + LLM 填充（并发）
        all_pages = list(range(1, total_pages + 1))
        tasks = [
            self._template_fill_one(
                page_num=p,
                pack_dir=pack_dir,
                pages_dir=pages_dir,
                pptx_root=pptx_root,
                outline_page=outline_pages.get(p, ""),
                output_dir=output_dir,
                manifest=manifest,
            )
            for p in all_pages
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        missing_pages: list[int] = []
        page_files: list[str] = []
        for p, r in zip(all_pages, results):
            if isinstance(r, Exception):
                logger.warning("[P8-TP] 页面 %d 填充异常: %s", p, r)
                missing_pages.append(p)
                continue
            if r:
                page_files.append(f"page-{p}.pptx.html")
            else:
                missing_pages.append(p)

        # 5. check 自检 + 失败恢复循环（最多 2 轮）
        check_ok = False
        for retry_round in range(2):
            check_ok = True
            try:
                check_cmd = (
                    f"{fill_js_path(pptx_root)} check "
                    f"{quote_path(pack_dir)} {quote_path(pages_dir)}"
                )
                check_result = await run_bash(
                    self, check_cmd,
                    timeout_seconds=300, required=False, workdir=pptx_root,
                )
                if check_result.exit_code != 0:
                    check_ok = False
                    check_output = check_result.stdout + "\n" + check_result.stderr
                    logger.warning("[P8-TP] fill.js check 第 %d 轮失败 exit=%d", retry_round + 1, check_result.exit_code)

                    # 解析失败的页码（跳过 manifest 声明类误报，re-seed 无法修复）
                    # check 输出格式：page 行（含 page-N）后跟 HARD/WARN 行（不含 page-N），
                    # 需用"当前页面"追踪方式把 HARD 行关联到最近的 page 行
                    failed_pages: list[int] = []
                    manifest_decl_pages: set[int] = set()
                    current_page: int | None = None
                    for line in check_output.splitlines():
                        m = re.search(r'page-(\d+)', line)
                        if m:
                            current_page = int(m.group(1))
                        if "HARD" not in line.upper():
                            continue
                        if current_page is None:
                            continue
                        p = current_page
                        # "template-id 未在 manifest 中声明" 是 manifest 声明问题，
                        # re-seed 不会修复（换模板也可能不在 manifest 中），跳过
                        if "manifest" in line.lower() and "声明" in line:
                            manifest_decl_pages.add(p)
                            continue
                        if p not in failed_pages:
                            failed_pages.append(p)

                    # 只报 manifest 声明问题的页不算失败
                    manifest_only = manifest_decl_pages - set(failed_pages)
                    if manifest_only:
                        logger.info(
                            "[P8-TP] 页面 %s 仅有 manifest 声明类警告，跳过 re-seed",
                            sorted(manifest_only),
                        )

                    if not failed_pages:
                        if manifest_decl_pages:
                            # 所有 HARD 错误都是 manifest 声明类，内容本身没问题
                            logger.info("[P8-TP] check 仅剩 manifest 声明类警告，视为通过")
                            check_ok = True
                            break
                        # 无法解析失败页，取所有已生成页重试
                        failed_pages = [p for p in all_pages if f"page-{p}.pptx.html" in page_files]

                    if not failed_pages:
                        logger.error("[P8-TP] check 失败但无法定位失败页，放弃重试")
                        break

                    logger.info("[P8-TP] 第 %d 轮恢复：重新 seed+填充 %d 个失败页 %s",
                                retry_round + 1, len(failed_pages), failed_pages)

                    # 重新 seed + 填充失败页（用 content-base 兜底模板）
                    retry_tasks = [
                        self._template_fill_one(
                            page_num=p,
                            pack_dir=pack_dir,
                            pages_dir=pages_dir,
                            pptx_root=pptx_root,
                            outline_page=outline_pages.get(p, ""),
                            output_dir=output_dir,
                            manifest=manifest,
                            force_template_id="content-base",
                        )
                        for p in failed_pages
                    ]
                    retry_results = await asyncio.gather(*retry_tasks, return_exceptions=True)
                    for p, r in zip(failed_pages, retry_results):
                        if isinstance(r, Exception) or not r:
                            logger.warning("[P8-TP] 页面 %d 恢复失败", p)
                        else:
                            logger.info("[P8-TP] 页面 %d 恢复成功", p)
                else:
                    logger.info("[P8-TP] fill.js check 第 %d 轮通过", retry_round + 1)
                    break
            except BashExecError as e:
                logger.error("[P8-TP] fill.js check 异常: %s", e)
                check_ok = False
                break

        ppt_gen_status = "ok"
        if missing_pages:
            ppt_gen_status = "partial"
        if not check_ok:
            ppt_gen_status = "partial" if page_files else "failed"

        logger.info(
            "[P8-TP] 模板填充完成 status=%s success=%d/%d",
            ppt_gen_status, len(page_files), total_pages,
        )

        return {
            "pages_dir": pages_dir,
            "page_files": page_files,
            "missing_pages": missing_pages,
            "low_density_pages": [],
            "fix_report": "template-filler check " + ("passed" if check_ok else "failed"),
            "ppt_gen_status": ppt_gen_status,
            "__artifact__": {
                "info": {
                    "ppt_gen_status": ppt_gen_status,
                    "page_count": len(page_files),
                    "missing_count": len(missing_pages),
                },
                "files": [{"path": f, "desc": "PPT页面"} for f in page_files] if page_files else [],
            },
        }

    async def _read_file(self, path: str) -> str:
        """读取文件内容（PPTPageGenNode 自身用，模板分支）。"""
        if not path:
            return ""
        if not self.has_tool("read_file"):
            logger.warning("[P8-TP] read_file 工具不可用 %s", path)
            return ""
        try:
            result = await self.call_tool("read_file", file_path=path)
            content = PptCommon.parse_tool_file_content(result)
            return content
        except Exception as e:
            if isinstance(e, AbortError):
                raise
            logger.warning("[P8-TP] 读取文件失败 %s: %s", path, e)
            return ""

    async def _write_file(self, path: str, content: str) -> bool:
        """写入文件内容（PPTPageGenNode 自身用，模板分支）。"""
        if not self.has_tool("write_file"):
            logger.error("[P8-TP] write_file 工具不可用 %s", path)
            return False
        try:
            await self.call_tool("write_file", file_path=path, content=content)
            return True
        except Exception as e:
            if isinstance(e, AbortError):
                raise
            logger.error("[P8-TP] 写入文件失败 %s: %s", path, e)
            return False

    async def _load_template_manifest(
        self, pack_dir: str, pptx_root: str,
    ) -> dict[str, Any]:
        """读取模板包的 template-manifest.json。"""
        manifest_path = f"{pack_dir}/template-manifest.json"
        content = await self._read_file(manifest_path)
        if not content:
            logger.warning("[P8-TP] template-manifest.json 不存在或为空")
            return {}
        try:
            import json as _json
            return _json.loads(content)
        except Exception as e:
            if isinstance(e, AbortError):
                raise
            logger.warning("[P8-TP] template-manifest.json 解析失败: %s", e)
            return {}

    @staticmethod
    def _select_template_id(
        page_type: str,
        manifest: dict[str, Any],
        outline_page: str = "",
        research_page: str = "",
    ) -> str:
        """根据页面类型 + 内容形状选择模板 ID（容量感知）。

        原版 skill 让 LLM 根据内容形状选模板，这里用 Python 做内容形状检测：
        - 结构页（cover/agenda/chapter/conclusion）→ 固定映射
        - 内容页：分析 research/outline 中的并列项数、对比模式、数据量
          - 2 项对比 → content-two-column
          - 3-5 并列 → content-cards
          - 单主题 → content-default
          - 超容量（>5 项或大量数据）→ content-base（自由排版）
        """
        # 构建 manifest 中已声明的 template_id 集合（layouts + bases）
        valid_template_ids: set[str] = set()
        for layout in (manifest.get("layouts") or []):
            if isinstance(layout, dict):
                tid = layout.get("template_id") or ""
                if tid:
                    valid_template_ids.add(tid)
        for base in (manifest.get("bases") or []):
            if isinstance(base, dict):
                tid = base.get("template_id") or base.get("page_role") or ""
                if tid:
                    valid_template_ids.add(tid)

        def _find_in_manifest(intent: str, fallback_id: str) -> str:
            """从 manifest.page_intents 查找模板，找不到则用 fallback。

            如果 page_intents 指向的 template 不在 layouts/bases 已声明集合中，
            尝试按 page_role 找一个已声明的 layout 替代。
            """
            page_intents = manifest.get("page_intents") or []
            selected = ""
            selected_file = ""
            if isinstance(page_intents, list):
                for entry in page_intents:
                    if not isinstance(entry, dict):
                        continue
                    if entry.get("intent") == intent:
                        selected = entry.get("template") or ""
                        selected_file = entry.get("file") or ""
                        break
            if not selected:
                return fallback_id

            # 已在 manifest 声明集合中，直接返回
            if selected in valid_template_ids:
                return selected

            # 不在已声明集合中（base 模板未声明 template_id），
            # 尝试按 page_role 找一个已声明的 layout 替代
            base_page_role = ""
            for base in (manifest.get("bases") or []):
                if isinstance(base, dict) and base.get("file") == selected_file:
                    base_page_role = base.get("page_role") or ""
                    break

            if base_page_role:
                for layout in (manifest.get("layouts") or []):
                    if isinstance(layout, dict) and layout.get("page_role") == base_page_role:
                        alt_tid = layout.get("template_id") or ""
                        if alt_tid and alt_tid in valid_template_ids:
                            logger.info(
                                "[P8-TP] 模板 %s 未在 manifest 声明，按 page_role=%s 替换为 %s",
                                selected, base_page_role, alt_tid,
                            )
                            return alt_tid

            # 找不到替代，保留原模板（fill.js seed 仍可使用，check 会报 manifest 声明警告）
            logger.debug("[P8-TP] 模板 %s 未在 manifest layouts 中声明，保留使用", selected)
            return selected

        # 结构页：固定映射，不走内容感知
        if page_type in _TEMPLATE_STRUCTURAL_TYPES:
            type_to_intent = {
                "intro": "cover", "cover": "cover",
                "agenda": "toc",
                "chapter": "section", "section": "section",
                "conclusion": "closing", "ending": "closing",
            }
            target_intent = type_to_intent.get(page_type, "section")
            return _find_in_manifest(
                target_intent,
                _PAGE_TYPE_TO_TEMPLATE.get(page_type, "section-base"),
            )

        # 内容页：内容形状检测
        content_text = (research_page or "") + "\n" + (outline_page or "")

        # 检测对比模式
        comparison_patterns = [" vs ", "对比", "相较", " versus ", "compared to", " VS "]
        is_comparison = any(p in content_text for p in comparison_patterns)

        # 统计并列项数
        bullet_count = 0
        for line in content_text.splitlines():
            stripped = line.strip()
            if stripped.startswith("- ") or stripped.startswith("• ") or stripped.startswith("* "):
                bullet_count += 1
            elif stripped and stripped[0].isdigit() and "." in stripped[:3]:
                bullet_count += 1

        # 检测数据密度
        numbers = re.findall(r'\d+\.?\d*%?', content_text)
        data_density = len(numbers)

        # 内容形状 → 模板选择
        if is_comparison and bullet_count >= 2:
            shape = "comparison"
        elif bullet_count >= 6 or data_density >= 15:
            shape = "overflow"
        elif 3 <= bullet_count <= 5:
            shape = "cards"
        else:
            shape = "single"

        shape_to_intent = {
            "comparison": "comparison",
            "overflow": "general",
            "cards": "image_text",
            "single": "general",
        }
        shape_to_fallback = {
            "comparison": "content-two-column",
            "overflow": "content-base",
            "cards": "content-cards",
            "single": "content-default",
        }
        target_intent = shape_to_intent.get(shape, "general")
        fallback_id = shape_to_fallback.get(shape, "content-default")
        return _find_in_manifest(target_intent, fallback_id)

    async def _template_fill_one(
        self,
        *,
        page_num: int,
        pack_dir: str,
        pages_dir: str,
        pptx_root: str,
        outline_page: str,
        output_dir: str,
        manifest: dict[str, Any],
        force_template_id: str = "",
    ) -> bool:
        """单页模板填充：seed → LLM 填充 → write_file。返回是否成功。"""
        # 检测页面类型
        page_type = _detect_page_type(outline_page)

        # 3. 读取本页 research（提前读取，供选模板和填充使用）
        research_path = f"{output_dir}/research-P{page_num}.md"
        research_text = await self._read_file(research_path)

        # 容量感知选模板：根据内容形状选模板（或使用强制兜底模板）
        if force_template_id:
            template_id = force_template_id
        else:
            template_id = self._select_template_id(
                page_type, manifest,
                outline_page=outline_page,
                research_page=research_text,
            )

        # 1. seed 种子化
        page_path = f"{pages_dir}/page-{page_num}.pptx.html"
        try:
            seed_cmd = (
                f"{fill_js_path(pptx_root)} seed "
                f"{quote_path(pack_dir)} {template_id} {quote_path(page_path)} copy"
            )
            await run_bash(
                self, seed_cmd,
                timeout_seconds=60, required=True, workdir=pptx_root,
            )
        except BashExecError as e:
            logger.error("[P8-TP] 页面 %d seed 失败 template=%s: %s", page_num, template_id, e)
            return False
        except Exception as e:
            if isinstance(e, AbortError):
                raise
            logger.error("[P8-TP] 页面 %d seed 异常: %s", page_num, e)
            return False

        # 2. 读取种子 HTML
        seed_html = await self._read_file(page_path)
        if not seed_html:
            logger.error("[P8-TP] 页面 %d 种子 HTML 读取失败", page_num)
            return False

        # 4. LLM 填充内容
        is_structural = page_type in _TEMPLATE_STRUCTURAL_TYPES
        filled_html = await self._llm_fill_template(
            page_num=page_num,
            seed_html=seed_html,
            outline_page=outline_page,
            research_page=research_text,
            is_structural=is_structural,
        )
        if not filled_html:
            logger.error("[P8-TP] 页面 %d LLM 填充失败", page_num)
            return False

        # 5. 写入填充后的 HTML
        ok = await self._write_file(page_path, filled_html)
        if not ok:
            logger.error("[P8-TP] 页面 %d 写入失败", page_num)
            return False

        logger.info("[P8-TP] 页面 %d 填充完成 template=%s", page_num, template_id)
        return True

    async def _llm_fill_template(
        self,
        *,
        page_num: int,
        seed_html: str,
        outline_page: str,
        research_page: str,
        is_structural: bool,
    ) -> str:
        """调用 LLM 填充模板 HTML 中的 data-slot 占位文字。"""
        research_section = ""
        if research_page and not is_structural:
            research_section = f"\n### 本页研究素材（research-P{page_num}.md）\n{research_page}\n"
        elif is_structural:
            research_section = "\n（结构页，无需研究素材，仅依据大纲内容填充标题/副标题）\n"

        prompt = (
            f"你是 PPT 模板填充师。请将以下模板种子 HTML 中的 data-slot 占位文字替换为真实内容，并做顺势增强。\n\n"
            f"### 大纲 — 本页规划\n{outline_page}\n"
            f"{research_section}\n"
            "### 填充规则（必须遵守）\n"
            "1. **保住 DNA**：不动 :root CSS 变量（配色/字号）、--font-*、.template-bg-image、.content-layer 骨架\n"
            "2. **守容量**：文字长度/行数照模板该槽位的容量约束（max_chars/max_lines）\n"
            "3. **安全写入**：内容中的 &、<、>、引号等特殊字符不得破坏 HTML 标签结构\n"
            "4. **禁止改模板结构**：不得改 grid-template-columns 列数、不得删 overflow:hidden、不得降字号到 14px 以下\n"
            "5. **所有文字必须是真实内容**，禁止占位文本（TODO、xxx 等）\n"
            "6. **结构性页面必须填标题**：封面/章节/结尾页的 data-slot=\"title\" 必须写入大纲给出的标题\n"
            "\n"
            "### 顺势增强（内容页必做，结构页跳过）\n"
            "在填满 data-slot 后，如果内容区仍有留白，按以下顺序增强（仅用 :root 变量和模板已有 CSS class）：\n"
            "1. **从 research 多挖细节**填进现有槽位（研究通常比框能装的多——挖，不编造）\n"
            "2. **加总结框**：主色左边框的小框，标题「关键洞察/核心总结」，对已有要点 1-2 句概括重述（非新事实），≤2 行\n"
            "3. **内容→视觉转换**（轻量、纯 HTML/CSS、守 DNA）：\n"
            "   - 一组数字/KPI → 大数字卡 / KPI 横条\n"
            "   - 并列要点 → 图标 + 文字列表\n"
            "   - 结论/金句 → 引用块 blockquote\n"
            "   - 对比 → 左右对照卡\n"
            "   - 流程/时序 → CSS 节点+连线 / 时间线\n"
            "4. 仍空 → 把该页内容区用足，确保内容分散占据 .content-layer 高度（不要全堆到上半屏）\n"
            "\n"
            "### 超容量决策树（内容超出模板槽位时，按序处理）\n"
            "1. 精简措辞压进槽位 → 2. 只保留核心要点，次要内容移到注释 → 3. 实在塞不下就少填，不要硬塞导致溢出\n"
            "禁止：改 grid-template-columns 列数、调高 -webkit-line-clamp、删 overflow:hidden、降字号到 14px 以下\n"
            "\n"
            "### 内容页留白契约\n"
            "- 内容必须分散占据 .content-layer 高度，不要全堆到顶部 30%\n"
            "- 正文最小字号 14px；来源/注脚允许 12px 但必须用含 note/source/footnote 的 class\n"
            "- 至少 8% 的垂直缓冲\n"
            "\n"
            "直接输出完整的填充后 HTML，不要输出解释或代码块包裹。\n\n"
            f"### 模板种子 HTML\n{seed_html}"
        )
        try:
            result = await self.stream_llm_collect(
                prompt=prompt,
                system_prompt="你是 PPT 模板填充师，直接输出填充后的完整 HTML 原文，不输出任何解释。",
                node_name=f"p8_tp_page_{page_num}",
                concurrent=True,
            )
            html = _strip_html_fence(result or "")
            if not html or len(html) < 200:
                logger.warning("[P8-TP] 页面 %d LLM 填充产物过短", page_num)
                return ""
            return html
        except Exception as e:
            if isinstance(e, AbortError):
                raise
            logger.warning("[P8-TP] 页面 %d LLM 填充失败: %s", page_num, e)
            return ""

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