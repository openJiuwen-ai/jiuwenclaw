from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, NamedTuple

from jiuwenclaw.agentserver.skill_turbo.plan_node import AbortError

_JSON_FENCE_PATTERN = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)
_CAT_N_PREFIX_RE = re.compile(r"^[ \t]*\d+[ \t]", re.MULTILINE)
_OUTLINE_PAGE_HEADING_RE = re.compile(r"^### P(\d+):", re.MULTILINE)
_STRUCTURAL_REQUEST_SPLIT_RE = re.compile(r"[+,|/]+")

# 与 pptx-craft outline-planner「中间结构页触发与默认数量规则」对齐。
# P1 / P2 抽取 prompt 必须使用同一段文案，避免各写一套。
STRUCTURAL_PAGE_SLOT_PROMPT = """\
- structural_page_request: 默认 "none"。仅当用户明确要求独立结构页时提取。
  取值：none / agenda / section / chapter / agenda+section / agenda+chapter。
  类型选择（可叠加）："目录页/议程页"→agenda；"章节页/章节分隔页/分节页"→section；"PART/章首页/Chapter"→chapter。
  触发示例："加章节页""每章一个章节页""加 3 页章节分隔""需要目录页""保留我大纲里的章节页""加 PART 页""加章首页"。
  普通章节结构、素材中的标题层级、模型自己觉得需要分节，都不构成触发条件 -> "none"。
- structural_page_count: 用户指定的数量（整数；未指定或"每章一个"为 null）。
  同时要求目录和章节页时，此字段只记录章节页的指定数量。"""

PAGE_COUNT_STRUCTURAL_HINT = """\
  page_count 不含封面、结束页和用户明确要求的中间结构页。
  用户列出「封面、目录、内容主题、结束页」时，page_count 只计内容主题。
  「生成 N 页 PPT」表示总页数，默认 page_count = max(N - 2, 1)；用户明确要求目录/章节页时，再扣除这些结构页。"""


class StructuralPagePlan(NamedTuple):
    """大纲阶段使用的中间结构页计划（封面/结束页除外）。"""

    request: str
    need_agenda: bool
    agenda_count: int
    divider_type: str
    divider_count: int
    divider_count_mode: str
    total_middle: int

# ──────────────────────── 节点显示名映射 ────────────────────────
# 将内部 plan_name（如 p0_pipeline_init）映射为界面上展示的中文名称。
# 排序遵循 ppt_gen_root 节点 sub_plans 的执行顺序（Stage 1 → Stage N）。
# 仅影响前端展示，不改变内部 plan_name 标识。
NODE_DISPLAY_NAMES: dict[str, str] = {
    "p0_pipeline_init": "Stage 1: 流水线初始化",
    "p1_intent_classify": "Stage 2: 意图分类",
    "p3_document_parse": "Stage 3: 文档解析",
    "p2_requirement_collect": "Stage 4: 需求收集",
    "p3_5_template_context": "Stage 5: 模板上下文预处理",
    "p4_content_plan": "Stage 6: 内容策划",
    "p5_outline_review": "Stage 7: 大纲审阅",
    "p6_deep_research": "Stage 8: 深度研究",
    "p7_style_prepare": "Stage 9: 风格准备",
    "p6_5_image_prepare": "Stage 10: 图片准备",
    "p8_ppt_page_gen": "Stage 11: 幻灯片生成",
    "p9_ppt_export": "Stage 12: PPTX导出",
    "p11_speaker_notes": "Stage 13: 演讲备注",
    "p10_delivery": "Stage 14: 交付",
    "ppt_gen_root": "PPT生成",
}


def _strip_line_numbers(text: str) -> str:
    return _CAT_N_PREFIX_RE.sub("", text)


class PptCommon:
    """PPT skill_codes 公共工具：流水线 inputs 解析与 LLM JSON 提取。"""

    TEXT_SOURCE_KEYS = ("task", "user_request", "user_message", "query")
    QUERY_PREFIXES = (
        "你收到一条消息：\n",
        "You receive a new message:\n",
    )
    JSON_FENCE_PATTERN = _JSON_FENCE_PATTERN

    @classmethod
    def extract_plain_user_text(cls, raw: str) -> str:
        """从 build_user_prompt 包装或裸文本中提取用户原文 content。"""
        text = raw.strip()
        if not text:
            return ""

        for prefix in cls.QUERY_PREFIXES:
            if not text.startswith(prefix):
                continue
            json_part = text[len(prefix):]
            try:
                payload = json.loads(json_part)
            except json.JSONDecodeError:
                break
            if isinstance(payload, dict):
                content = payload.get("content")
                if isinstance(content, str) and content.strip():
                    return content.strip()
            break

        brace_index = text.find("{")
        if brace_index >= 0:
            try:
                payload = json.loads(text[brace_index:])
            except json.JSONDecodeError:
                return text
            if isinstance(payload, dict):
                content = payload.get("content")
                if isinstance(content, str) and content.strip():
                    return content.strip()

        return text

    @classmethod
    def collect_user_text(cls, inputs: dict[str, Any]) -> str:
        """合并 task / user_request / user_message / query 中的用户可见原文。"""
        parts: list[str] = []
        seen: set[str] = set()
        for key in cls.TEXT_SOURCE_KEYS:
            value = inputs.get(key)
            if not isinstance(value, str) or not value.strip():
                continue
            normalized = cls.extract_plain_user_text(value)
            if not normalized:
                continue
            dedupe_key = normalized.casefold()
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            parts.append(normalized)
        return "\n".join(parts)

    @classmethod
    def parse_json_payload(cls, raw: str) -> Any:
        """解析 LLM 返回的 JSON（支持 markdown fence 与正文中的 JSON 对象）。"""
        if not raw or not raw.strip():
            return None

        text = raw.strip()
        fence_match = cls.JSON_FENCE_PATTERN.search(text)
        if fence_match:
            text = fence_match.group(1).strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            object_match = re.search(r"\{[\s\S]*\}", text)
            if not object_match:
                return None
            try:
                return json.loads(object_match.group(0))
            except json.JSONDecodeError:
                return None

    @classmethod
    def parse_tool_file_content(cls, result: Any) -> str:
        """从 read_file / write_file 工具返回值中提取文本内容，并去掉 cat -n 行号前缀。"""
        if result is None:
            return ""
        if isinstance(result, str):
            text = result.strip()
            if text.startswith("success=False") or text.startswith("success= False"):
                return ""
            return _strip_line_numbers(text)
        if isinstance(result, dict):
            content = result.get("content", "")
            if isinstance(content, str):
                return _strip_line_numbers(content.strip())
            return _strip_line_numbers(str(content or "").strip())
        if hasattr(result, "data"):
            data = result.data
            if isinstance(data, dict):
                content = data.get("content", "")
                if isinstance(content, str):
                    return _strip_line_numbers(content.strip())
                return _strip_line_numbers(str(content or "").strip())
            if isinstance(data, str):
                return _strip_line_numbers(data.strip())
        return _strip_line_numbers(str(result).strip())

    @classmethod
    async def read_file(
        cls,
        node: Any,
        file_path: str | Path | None,
        *,
        max_chars: int | None = None,
        required: bool = False,
        label: str = "file",
        error_type: type[Exception] = RuntimeError,
    ) -> str:
        if not file_path:
            if required:
                raise error_type(f"缺少 {label} 路径")
            return ""
        path = Path(str(file_path)).expanduser().resolve()
        if not node.has_tool("read_file"):
            if required:
                raise error_type(f"read_file 工具不可用，无法读取 {label}")
            return ""
        try:
            result = await node.call_tool("read_file", file_path=str(path))
        except Exception as exc:
            if isinstance(exc, AbortError):
                raise
            if required:
                raise error_type(f"读取 {label} 失败: {path}: {exc}") from exc
            return ""
        text = cls.parse_tool_file_content(result)
        if not text:
            if required:
                raise error_type(f"{label} 为空或不存在: {path}")
            return ""
        if max_chars is not None and len(text) > max_chars:
            return text[:max_chars] + "\n\n...(内容已截断)"
        return text

    @classmethod
    async def write_file(
        cls,
        node: Any,
        file_path: str | Path,
        content: str,
        *,
        label: str = "file",
        error_type: type[Exception] = RuntimeError,
    ) -> Path:
        path = Path(str(file_path)).expanduser().resolve()
        normalized = content.strip() + "\n" if content.strip() else ""
        if not node.has_tool("write_file"):
            raise error_type(f"write_file 工具不可用，无法写入 {label}")
        try:
            await node.call_tool(
                "write_file",
                file_path=str(path),
                content=normalized,
            )
        except Exception as exc:
            if isinstance(exc, AbortError):
                raise
            raise error_type(f"写入 {label} 失败: {path}: {exc}") from exc
        return path

    @staticmethod
    def parse_positive_int(value: Any) -> int | None:
        """解析正整数；bool 与非数字一律视为未指定。"""
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, int) and value > 0:
            return value
        if isinstance(value, float) and value.is_integer() and value > 0:
            return int(value)
        if isinstance(value, str) and value.strip().isdigit():
            parsed = int(value.strip())
            return parsed if parsed > 0 else None
        return None

    @staticmethod
    def normalize_structural_page_request(raw: Any) -> str:
        """将抽取结果归一为官方类型：none / agenda / section / chapter / agenda+section / agenda+chapter。

        auto 按官方「章节页 → section」处理，不保留为独立类型。
        """
        if not isinstance(raw, str) or not raw.strip():
            return "none"
        parts = _STRUCTURAL_REQUEST_SPLIT_RE.split(raw.strip().lower().replace(" ", ""))
        tokens: list[str] = []
        for part in parts:
            token = part.strip()
            if not token or token == "none":
                continue
            if token == "auto":
                token = "section"
            if token in {"agenda", "section", "chapter"} and token not in tokens:
                tokens.append(token)
        if not tokens:
            return "none"
        need_agenda = "agenda" in tokens
        divider = "chapter" if "chapter" in tokens else ("section" if "section" in tokens else "")
        if need_agenda and divider:
            return f"agenda+{divider}"
        if need_agenda:
            return "agenda"
        return divider

    @staticmethod
    def default_section_page_count(page_count: Any) -> int:
        """官方：只说需要章节页且未指定数量时，≤5 为 1 页，≥6 为 ceil(page_count/4)。"""
        try:
            count = int(page_count)
        except (TypeError, ValueError):
            return 1
        if count <= 5:
            return 1
        return (count + 3) // 4

    @classmethod
    def resolve_structural_page_plan(
        cls,
        structural_page_request: Any = "none",
        structural_page_count: Any = None,
        page_count: Any = None,
    ) -> StructuralPagePlan:
        """按 pptx-craft 规则把抽取槽位编译为目录/章节页计划。

        目录与章节页分开计数：只要目录且未指定数量 → 1 张总目录，不套用章节页 ceil 公式。
        """
        request = cls.normalize_structural_page_request(structural_page_request)
        specified = cls.parse_positive_int(structural_page_count)
        need_agenda = request.startswith("agenda")
        if request in {"section", "agenda+section"}:
            divider_type = "section"
        elif request in {"chapter", "agenda+chapter"}:
            divider_type = "chapter"
        else:
            divider_type = ""

        if need_agenda and not divider_type:
            agenda_count = specified if specified is not None else 1
            return StructuralPagePlan(
                request=request,
                need_agenda=True,
                agenda_count=agenda_count,
                divider_type="",
                divider_count=0,
                divider_count_mode="none",
                total_middle=agenda_count,
            )

        if divider_type and not need_agenda:
            if specified is not None:
                divider_count = specified
                divider_mode = "specified"
            else:
                divider_count = cls.default_section_page_count(page_count)
                divider_mode = "default"
            return StructuralPagePlan(
                request=request,
                need_agenda=False,
                agenda_count=0,
                divider_type=divider_type,
                divider_count=divider_count,
                divider_count_mode=divider_mode,
                total_middle=divider_count,
            )

        if need_agenda and divider_type:
            agenda_count = 1
            if specified is not None:
                divider_count = specified
                divider_mode = "specified"
            else:
                divider_count = cls.default_section_page_count(page_count)
                divider_mode = "default"
            return StructuralPagePlan(
                request=request,
                need_agenda=True,
                agenda_count=agenda_count,
                divider_type=divider_type,
                divider_count=divider_count,
                divider_count_mode=divider_mode,
                total_middle=agenda_count + divider_count,
            )

        return StructuralPagePlan(
            request="none",
            need_agenda=False,
            agenda_count=0,
            divider_type="",
            divider_count=0,
            divider_count_mode="none",
            total_middle=0,
        )

    @staticmethod
    def resolve_total_pages(
        *,
        page_count: int = 0,
        total_pages: int | None = None,
        outline_text: str = "",
        outline_pages: dict[int, str] | None = None,
        default_structural_pages: int = 2,
    ) -> int:
        """从 outline 页码、上下文 total_pages 与 page_count 兜底推算总页数。

        含 agenda 等额外结构页时，``page_count + 2`` 会低估总页数；优先取 outline 最大页码。
        """
        candidates: list[int] = []
        if total_pages is not None:
            try:
                parsed = int(total_pages)
                if parsed > 0:
                    candidates.append(parsed)
            except (TypeError, ValueError):
                pass
        if outline_pages:
            candidates.append(max(outline_pages))
        if outline_text.strip():
            page_nums = [
                int(match.group(1))
                for match in _OUTLINE_PAGE_HEADING_RE.finditer(outline_text)
            ]
            if page_nums:
                candidates.append(max(page_nums))
        if page_count > 0:
            candidates.append(page_count + default_structural_pages)
        return max(candidates) if candidates else 0
