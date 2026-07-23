from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from jiuwenclaw.agentserver.skill_turbo.plan_node import AbortError, PlanNode
from jiuwenclaw.agentserver.skill_turbo.skill_codes.ppt.ppt_common import PptCommon, _strip_line_numbers
from jiuwenclaw.agentserver.skill_turbo.skill_codes.ppt.utils.bash_utils import (
    BashExecError,
    cli_path,
    quote_path,
    run_bash,
)

logger = logging.getLogger(__name__)

_collect_user_text = PptCommon.collect_user_text

_DOC_RAW_NAME = "doc_raw.md"
_DOC_SUMMARY_NAME = "doc_summary.md"
_DOC_MANIFEST_NAME = "doc_manifest.json"
_MAX_PARSE_ATTEMPTS = 2
_DOC_EXCERPT_MAX_CHARS = 8000
_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})

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


def _is_image_path(path: str | Path) -> bool:
    return Path(path).suffix.casefold() in _IMAGE_EXTENSIONS


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
    """P3 — 条件文档解析：调 cli parse-docs 解析文档，产出 doc_raw.md / doc_summary.md / doc_manifest.json。

    预期输入（ctx / inputs）:
        必填（门控）: has_documents — false 时跳过本节点
        必填: doc_paths — P1 产出的待解析文件绝对路径列表
        必填: output_dir — P0.2 产出的会话目录
        必填: pptx_root — P0.1 解析的 skill 根目录
        可选: task | user_request | user_message | query — 主题推断时作为用户侧上下文
        可选: topic — 用户已指定主题时跳过 LLM 推断
        可选: failure_reason — 重试时附带的上次失败原因

    预期输出（写入同一 ctx）:
        doc_raw_path: str — {output_dir}/doc_raw.md 绝对路径
        doc_summary_path: str — {output_dir}/doc_summary.md 绝对路径
        doc_manifest_path: str — {output_dir}/doc_manifest.json 绝对路径
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
                "1. has_documents=True 时调 cli parse-docs 解析 doc_paths 中所有文档\n"
                "2. 产出三件套：doc_raw.md / doc_summary.md / doc_manifest.json\n"
                "3. 若用户未指定 topic，LLM 结合用户请求与 doc_raw 推断主题\n"
                "\n"
                "### 前置条件\n"
                "- `has_documents=True`（门控；False 时由根节点 skip）\n"
                "- `doc_paths` 非空（P1 产出，图片已分流）\n"
                "- `output_dir` 已由 P0 创建\n"
                "- `pptx_root` 已由 P0.1 解析\n"
                "- `bash` / `read_file` / `stream_llm` 工具可用\n"
                "\n"
                "### 输出\n"
                "- `doc_raw_path`: str — `{output_dir}/doc_raw.md` 绝对路径\n"
                "- `doc_summary_path`: str — `{output_dir}/doc_summary.md` 绝对路径\n"
                "- `doc_manifest_path`: str — `{output_dir}/doc_manifest.json` 绝对路径\n"
                "- `doc_parse_ok`: bool — 解析是否成功\n"
                "- `doc_parse_error`: str | None — 失败时的错误摘要\n"
                "- `topic`: str — 推断的主题\n"
                "- `topic_inferred`: bool — 是否由本节点 LLM 推断出主题\n"
                "\n"
                "### 失败兜底\n"
                "- cli parse-docs 失败：重试 1 次后仍失败则 doc_parse_ok=False\n"
                "- LLM 主题推断失败：topic='', topic_inferred=False\n"
            ),
        )

    async def _run_parse_docs(
        self,
        doc_paths: list[str],
        output_dir: str,
        pptx_root: str,
    ) -> tuple[bool, str | None]:
        """调 cli parse-docs 解析文档，产出三件套。"""
        last_error: str | None = None

        for attempt in range(_MAX_PARSE_ATTEMPTS):
            if attempt:
                logger.info("[P3] parse-docs 重试 (attempt %d)", attempt + 1)

            try:
                quoted_paths = " ".join(quote_path(p) for p in doc_paths)
                cmd = (
                    f"{cli_path('parse-docs', pptx_root)} "
                    f"--output-dir {quote_path(output_dir)} "
                    f"{quoted_paths}"
                )
                result = await run_bash(
                    self, cmd,
                    timeout_seconds=300, required=False, workdir=pptx_root,
                )
                if result.exit_code != 0:
                    last_error = result.stderr or result.stdout or f"exit_code={result.exit_code}"
                    logger.warning("[P3] parse-docs 失败 (attempt %d): %s", attempt + 1, last_error)
                    continue

                # 校验 doc_raw.md 存在且非空
                doc_raw_path = Path(output_dir) / _DOC_RAW_NAME
                text = await PptCommon.read_file(self, doc_raw_path, label=_DOC_RAW_NAME)
                if text.strip():
                    logger.info("[P3] parse-docs 完成，doc_raw.md 非空")
                    return True, None
                last_error = "doc_raw.md 不存在或内容为空"
            except BashExecError as exc:
                last_error = str(exc)
                logger.warning("[P3] parse-docs bash 失败 (attempt %d): %s", attempt + 1, exc)
            except Exception as exc:
                if isinstance(exc, AbortError):
                    raise
                last_error = f"{type(exc).__name__}: {exc}"
                logger.warning("[P3] parse-docs 异常 (attempt %d): %s", attempt + 1, exc)

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
        pptx_root = str(inputs.get("pptx_root") or "").strip()
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
        if not pptx_root:
            inputs["doc_parse_ok"] = False
            inputs["doc_parse_error"] = "缺少 pptx_root"
            inputs["topic"] = ""
            inputs["topic_inferred"] = False
            return inputs

        # 调 cli parse-docs 产出三件套
        ok, error = await self._run_parse_docs(doc_paths, str(output_dir), pptx_root)

        doc_raw_path = Path(str(output_dir)).expanduser().resolve() / _DOC_RAW_NAME
        doc_summary_path = Path(str(output_dir)).expanduser().resolve() / _DOC_SUMMARY_NAME
        doc_manifest_path = Path(str(output_dir)).expanduser().resolve() / _DOC_MANIFEST_NAME

        inputs["doc_raw_path"] = str(doc_raw_path)
        inputs["doc_summary_path"] = str(doc_summary_path)
        inputs["doc_manifest_path"] = str(doc_manifest_path)
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

        if ok and inputs.get("doc_raw_path"):
            inputs["__artifact__"] = {
                "files": [{"path": inputs["doc_raw_path"], "desc": "解析后文档原文"}],
                "info": {"topic_inferred": inputs.get("topic_inferred", False)},
            }
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
            "message": f"开始解析 {doc_count} 个文档附件...",
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
