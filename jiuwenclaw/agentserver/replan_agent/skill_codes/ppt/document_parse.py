from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from jiuwenclaw.agentserver.replan_agent.plan_node import AbortError, PlanNode
from jiuwenclaw.agentserver.replan_agent.skill_codes.ppt.ppt_common import PptCommon, _strip_line_numbers

_collect_user_text = PptCommon.collect_user_text

_DOC_RAW_NAME = "doc_raw.md"
_MAX_PARSE_ATTEMPTS = 2
_DOC_EXCERPT_MAX_CHARS = 8000
_PDF_BATCH_SIZE = 10
# Keep in sync with agent-core ReadFileTool.MAX_PDF_SIZE_BYTES_WITHOUT_PAGES (10 MB).
_PDF_LARGE_FILE_BYTES = 10 * 1024 * 1024
_PDF_MAX_AUTO_PARSE_PAGES = 200
_PDF_TRUNCATION_MARKER = (
    "\n\n---\n"
    "[文档解析说明] 该 PDF 超过自动解析上限（{cap} 页），"
    "本次仅解析前 {read_pages} 页。"
    "如需完整内容，请拆分 PDF 后重新上传。\n"
)
_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})
_PDF_EXTENSIONS = frozenset({".pdf"})
_IMAGE_OCR_QUESTION = (
    "请完整转写图片中的所有可见文字，保留段落与标题结构。"
    "只输出转写文本；若无文字则回复 No text found。"
)

_TOPIC_LLM_SYSTEM_PROMPT = """你是 PPT 主题推断助手。根据用户请求与文档原文，推断最适合作为演示文稿标题的主题。

规则：
1. 主题应简洁（通常 6~30 字），适合作为 PPT 标题。
2. 仅根据文档与用户请求推断，不要编造文档中不存在的主线。
3. 若文档内容过少、过于杂乱或无法判断合适主题，topic 必须为空字符串。
4. 用户已在请求中明确给出主题时，优先采用用户主题（可略作规范化）。

必须只输出 JSON：
{"topic": "推断的主题或空字符串"}"""


class DocumentParseError(RuntimeError):
    """P3 文档解析失败。"""


def _normalize_tool_text(result: Any) -> str:
    if result is None:
        return ""
    if isinstance(result, str):
        return _strip_line_numbers(result)
    if isinstance(result, dict):
        for key in ("content", "output", "result", "stdout", "text", "answer"):
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                return _strip_line_numbers(value)
        data = result.get("data")
        if isinstance(data, dict):
            for key in ("ocr_text", "answer", "text", "content"):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    return _strip_line_numbers(value)
        if result.get("success") is False:
            error = result.get("error") or result.get("message")
            if isinstance(error, str):
                return f"[ERROR]: {error}"
    if hasattr(result, "data"):
        data_attr = result.data
        if isinstance(data_attr, dict):
            for key in ("content", "ocr_text", "answer", "text", "output", "result"):
                value = data_attr.get(key)
                if isinstance(value, str) and value.strip():
                    return _strip_line_numbers(value)
        if isinstance(data_attr, str) and data_attr.strip():
            return _strip_line_numbers(data_attr)
    return _strip_line_numbers(str(result))


def _extract_vqa_ocr_section(text: str) -> str:
    if not text:
        return ""
    marker = "OCR results:"
    if marker in text:
        after = text.split(marker, 1)[1]
        vqa_marker = "VQA result:"
        if vqa_marker in after:
            return after.split(vqa_marker, 1)[0].strip()
        return after.strip()
    return text.strip()


def _is_image_path(path: str | Path) -> bool:
    return Path(path).suffix.casefold() in _IMAGE_EXTENSIONS


def _is_pdf_path(path: str | Path) -> bool:
    return Path(path).suffix.casefold() in _PDF_EXTENSIONS


def _merge_doc_raw_sections(parts: list[tuple[str, str]]) -> str:
    blocks: list[str] = []
    for filename, content in parts:
        blocks.append(f"# {filename}\n\n{content.rstrip()}\n")
    if not blocks:
        return ""
    return ("\n---\n\n").join(blocks) + "\n"


def _user_has_topic(inputs: dict[str, Any]) -> bool:
    topic = inputs.get("topic")
    return isinstance(topic, str) and bool(topic.strip())


def _build_topic_inference_prompt(user_text: str, doc_excerpt: str) -> str:
    parts = ["请根据以下信息推断 PPT 主题。\n"]
    if user_text:
        parts.append(f"用户请求：\n{user_text}\n")
    if doc_excerpt:
        parts.append(f"文档原文（doc_raw.md）：\n{doc_excerpt}\n")
    parts.append("按 JSON 格式返回 topic 字段；无法推断则 topic 为空字符串。")
    return "\n".join(parts)


def _parse_topic_from_llm_response(raw: str) -> str:
    payload = PptCommon.parse_json_payload(raw)
    if not isinstance(payload, dict):
        return ""

    topic = payload.get("topic")
    if not isinstance(topic, str):
        return ""
    return topic.strip()


class DocumentParseNode(PlanNode):
    """P3 — 条件文档解析：读附件原文写入 doc_raw.md（下游按路径读取，不传 doc_content）。

    预期输入（ctx / inputs）:
        必填（门控）: has_documents — false 时跳过本节点
        必填: doc_paths — P1 产出的待解析文件绝对路径列表
        必填: output_dir — P0.2 产出的会话目录
        可选: task | user_request | user_message | query — 主题推断时作为用户侧上下文
        可选: topic — 用户已指定主题时跳过 LLM 推断
        可选: failure_reason — 重试时附带的上次失败原因

    预期输出（写入同一 ctx）:
        doc_raw_path: str — {output_dir}/doc_raw.md 绝对路径
        doc_parse_ok: bool — 解析是否成功（doc_raw 存在且非空）
        doc_parse_error: str | None — 失败时的错误摘要
        topic: str — 推断的主题；无法推断或未推断时为空字符串
        topic_inferred: bool — 是否由本节点 LLM 从 doc_raw 推断出非空主题
    """

    def __init__(self) -> None:
        super().__init__(
            plan_name="p3_document_parse",
            instruction=(
                "## P3 条件文档解析\n"
                "\n"
                "### 节点职责\n"
                "1. has_documents=True 时按 doc_paths 逐个读取附件，合并写入 doc_raw.md\n"
                "2. 若用户未指定 topic，LLM 结合用户请求与 doc_raw 推断主题\n"
                "3. 不在 ctx 中传递 doc_content，下游从 doc_raw_path 读取\n"
                "\n"
                "### 前置条件\n"
                "- `has_documents=True`（门控；False 时由根节点 skip，不执行本节点）\n"
                "- `doc_paths` 非空（P1 产出的文件绝对路径列表）\n"
                "- `output_dir` 已由 P0 创建\n"
                "- `read_file` / `image_ocr` / `visual_question_answering` / `stream_llm` 工具可用\n"
                "\n"
                "### 输入\n"
                "- `has_documents`（必填，门控）: false 时根节点 skip 本节点\n"
                "- `doc_paths`（必填）: 待解析文件绝对路径列表\n"
                "- `output_dir`（必填）: 会话目录\n"
                "- `topic`（可选）: 用户已指定主题时跳过 LLM 推断\n"
                "- `task` | `user_request` | `user_message` | `query`（可选）: 主题推断时的用户侧上下文\n"
                "- `failure_reason`（可选）: 重试时附带的上次失败原因\n"
                "\n"
                "### 输出\n"
                "- `doc_raw_path`: str — `{output_dir}/doc_raw.md` 绝对路径（文件存在且非空）\n"
                "- `doc_parse_ok`: bool — 解析是否成功（doc_raw 存在且非空为 True）\n"
                "- `doc_parse_error`: str | None — 失败时的错误摘要（成功时为 None）\n"
                "- `topic`: str — 推断的主题（无法推断时为空字符串）\n"
                "- `topic_inferred`: bool — 是否由本节点 LLM 从 doc_raw 推断出非空主题\n"
                "\n"
                "has_documents=False 时由根节点 skip，\n"
                "产出: doc_parse_ok=False, doc_parse_error=None, topic='', topic_inferred=False\n"
                "\n"
                "### 执行流程\n"
                "1. 按 doc_paths 逐个读取附件：文本类用 read_file，图片类优先 image_ocr、其次 visual_question_answering\n"
                "2. 合并所有附件内容 → write_file 落盘 `{output_dir}/doc_raw.md`\n"
                "3. 校验 doc_raw.md 非空\n"
                "4. 若 topic 未指定 → call_llm 结合用户请求与 doc_raw 推断主题\n"
                "\n"
                "### 失败兜底\n"
                "- 所有附件均解析失败: raise DocumentParseError\n"
                "- doc_raw.md 为空: doc_parse_ok=False, doc_parse_error 记录错误摘要\n"
                "- LLM 主题推断失败: topic='', topic_inferred=False（不 raise，空主题由 P2 处理）\n"
            ),
        )

    async def _read_text_file(self, path: Path) -> str:
        if not self.has_tool("read_file"):
            raise DocumentParseError("read_file 工具未注册")
        raw = await self.call_tool("read_file", file_path=str(path))
        text = _normalize_tool_text(raw).strip()
        if text.startswith("[ERROR]"):
            raise DocumentParseError(text)
        if not text:
            raise DocumentParseError(f"read_file 返回空内容: {path}")
        return text

    async def _read_pdf_page_batch(self, path: Path, start: int, end: int) -> str | None:
        """Read one PDF page batch. Returns None when the range is past the document end."""
        if not self.has_tool("read_file"):
            raise DocumentParseError("read_file 工具未注册")

        pages = f"{start}-{end}"
        raw = await self.call_tool("read_file", file_path=str(path), pages=pages)
        text = _normalize_tool_text(raw).strip()

        if "CODE=PDF_OUTPUT_TOKEN_EXCEEDED" in text:
            if start < end:
                mid = start + (end - start) // 2
                left = await self._read_pdf_page_batch(path, start, mid)
                right = await self._read_pdf_page_batch(path, mid + 1, end)
                parts = [part for part in (left, right) if part]
                if parts:
                    return "\n\n".join(parts)
            raise DocumentParseError(text)

        if "CODE=PDF_PAGE_RANGE_OUT_OF_BOUNDS" in text:
            return None

        if text.startswith("[ERROR]") or text.startswith("[PDF_READ_ERROR]"):
            raise DocumentParseError(text)
        return text

    async def _read_large_pdf_file(self, path: Path) -> str:
        parts: list[str] = []
        for start in range(1, _PDF_MAX_AUTO_PARSE_PAGES + 1, _PDF_BATCH_SIZE):
            end = start + _PDF_BATCH_SIZE - 1
            text = await self._read_pdf_page_batch(path, start, end)

            if text is None:
                if parts:
                    break
                raise DocumentParseError(
                    f"read_file 返回页范围越界: {path} pages={start}-{end}"
                )
            if text:
                parts.append(text)
        else:
            merged = "\n\n".join(parts)
            merged += _PDF_TRUNCATION_MARKER.format(
                cap=_PDF_MAX_AUTO_PARSE_PAGES,
                read_pages=_PDF_MAX_AUTO_PARSE_PAGES,
            )
            if not parts:
                raise DocumentParseError(f"read_file 返回空内容: {path}")
            return merged

        if not parts:
            raise DocumentParseError(f"read_file 返回空内容: {path}")
        return "\n\n".join(parts)

    async def _read_image_file(self, path: Path) -> str:
        path_str = str(path)
        if self.has_tool("image_ocr"):
            raw = await self.call_tool("image_ocr", image_path_or_url=path_str)
            text = _normalize_tool_text(raw).strip()
            if text and not text.startswith("[ERROR]"):
                return text
        if self.has_tool("visual_question_answering"):
            raw = await self.call_tool(
                "visual_question_answering",
                image_path_or_url=path_str,
                question=_IMAGE_OCR_QUESTION,
            )
            text = _extract_vqa_ocr_section(_normalize_tool_text(raw)).strip()
            if text and not text.startswith("[ERROR]"):
                return text
        raise DocumentParseError(
            "图片解析失败：未注册 image_ocr / visual_question_answering，或 vision 调用失败"
        )

    async def _read_single_document(self, path: Path) -> tuple[str, str | None]:
        if not path.is_file():
            return path.name, f"[读取失败: 文件不存在 {path}]"

        try:
            if _is_image_path(path):
                content = await self._read_image_file(path)
            elif _is_pdf_path(path) and path.stat().st_size > _PDF_LARGE_FILE_BYTES:
                content = await self._read_large_pdf_file(path)
            else:
                content = await self._read_text_file(path)
            return path.name, content
        except DocumentParseError as exc:
            return path.name, f"[读取失败: {exc}]"
        except Exception as exc:
            if isinstance(exc, AbortError):
                raise
            return path.name, f"[读取失败: {type(exc).__name__}: {exc}]"

    async def _build_doc_raw(
        self, doc_paths: list[str]
    ) -> tuple[str, int, list[str]]:
        sections: list[tuple[str, str]] = []
        errors: list[str] = []
        success_count = 0

        for raw_path in doc_paths:
            path = Path(raw_path).expanduser()
            filename, content = await self._read_single_document(path)
            if content.startswith("[读取失败"):
                errors.append(content)
            else:
                success_count += 1
            sections.append((filename, content))

        merged = _merge_doc_raw_sections(sections)
        if success_count == 0:
            raise DocumentParseError("所有附件读取失败或内容为空")
        return merged, success_count, errors

    async def _parse_with_retry(
        self,
        inputs: dict[str, Any],
        doc_paths: list[str],
        doc_raw_path: Path,
    ) -> tuple[bool, str | None]:
        last_error: str | None = None

        for attempt in range(_MAX_PARSE_ATTEMPTS):
            if attempt:
                inputs["failure_reason"] = last_error or "doc_raw.md 无效"

            try:
                merged, _success_count, _read_errors = await self._build_doc_raw(doc_paths)
                await PptCommon.write_file(
                    self,
                    doc_raw_path,
                    merged,
                    label=_DOC_RAW_NAME,
                    error_type=DocumentParseError,
                )
                text = await PptCommon.read_file(
                    self,
                    doc_raw_path,
                    required=True,
                    label=_DOC_RAW_NAME,
                    error_type=DocumentParseError,
                )
                if text.strip():
                    return True, None
                last_error = "doc_raw.md 不存在或内容为空"
            except DocumentParseError as exc:
                last_error = str(exc)
            except OSError as exc:
                last_error = f"写入 doc_raw.md 失败: {exc}"

        return False, last_error

    async def _infer_topic_from_doc(
        self,
        inputs: dict[str, Any],
        doc_raw_path: Path,
    ) -> str:
        user_text = _collect_user_text(inputs)
        doc_excerpt = await PptCommon.read_file(
            self,
            doc_raw_path,
            max_chars=_DOC_EXCERPT_MAX_CHARS,
            error_type=DocumentParseError,
        )
        if not user_text and not doc_excerpt:
            return ""

        response = await self.stream_llm_collect(
            _build_topic_inference_prompt(user_text, doc_excerpt),
            system_prompt=_TOPIC_LLM_SYSTEM_PROMPT,
        )
        return _parse_topic_from_llm_response(response)

    async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        if not inputs.get("has_documents"):
            inputs["doc_parse_ok"] = False
            inputs["doc_parse_error"] = None
            inputs["topic"] = ""
            inputs["topic_inferred"] = False
            return inputs

        output_dir = inputs.get("output_dir")
        doc_paths = inputs.get("doc_paths")
        if not output_dir:
            inputs["doc_parse_ok"] = False
            inputs["doc_parse_error"] = "缺少 output_dir"
            inputs["topic"] = ""
            inputs["topic_inferred"] = False
            return inputs
        if not isinstance(doc_paths, list) or not doc_paths:
            inputs["doc_parse_ok"] = False
            inputs["doc_parse_error"] = "缺少 doc_paths"
            inputs["topic"] = ""
            inputs["topic_inferred"] = False
            return inputs

        doc_raw_path = Path(str(output_dir)).expanduser().resolve() / _DOC_RAW_NAME
        ok, error = await self._parse_with_retry(inputs, doc_paths, doc_raw_path)

        inputs["doc_raw_path"] = str(doc_raw_path)
        inputs["doc_parse_ok"] = ok
        inputs["doc_parse_error"] = error if not ok else None

        if ok and not _user_has_topic(inputs):
            inferred = await self._infer_topic_from_doc(inputs, doc_raw_path)
            if inferred:
                inputs["topic"] = inferred
                inputs["topic_inferred"] = True
            else:
                inputs["topic"] = ""
                inputs["topic_inferred"] = False
        elif not _user_has_topic(inputs):
            inputs["topic"] = ""
            inputs["topic_inferred"] = False

        return inputs

    async def _execute_stream(self, inputs: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        if not inputs.get("has_documents"):
            result = await self._execute(inputs)
            yield {
                **result,
                "node": self.plan_name,
                "status": "ok",
                "message": "未检测到待解析文档，跳过文档解析",
            }
            return

        doc_paths = inputs.get("doc_paths")
        doc_count = len(doc_paths) if isinstance(doc_paths, list) else 0
        yield {
            "node": self.plan_name,
            "status": "progress",
            "message": f"开始解析 {doc_count} 个文档/图片附件...",
        }

        result = await self._execute(inputs)
        status = "ok" if result.get("doc_parse_ok") else "warning"
        message = "文档解析完成" if result.get("doc_parse_ok") else f"文档解析未完成：{result.get('doc_parse_error') or '未知原因'}"
        yield {
            **result,
            "node": self.plan_name,
            "status": status,
            "message": message,
        }
