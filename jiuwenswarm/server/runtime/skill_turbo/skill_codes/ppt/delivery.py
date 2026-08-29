from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from jiuwenswarm.server.runtime.skill_turbo.plan_node import AbortError, PlanNode
from jiuwenswarm.server.runtime.skill_turbo.skill_codes.ppt.ppt_common import (
    DELIVERY_STATUS_FAILED,
    DELIVERY_STATUS_SENT,
    DELIVERY_STATUS_UNAVAILABLE,
    PptCommon,
)
from jiuwenswarm.server.runtime.skill_turbo.skill_codes.ppt.utils.bash_utils import (
    normalize_tool_text,
)

logger = logging.getLogger(__name__)

_DEFAULT_STRUCTURAL_PAGES = 2

_SEND_FAIL_MARKERS = (
    "发送文件失败",
    "所有文件均不存在",
    "没有可发送的文件",
    "提交文件失败",
    "文件不存在，未发送",
)


def _looks_like_path(value: str) -> bool:
    return "/" in value or "\\" in value or value.endswith(".html")


class DeliveryNode(PlanNode):
    """P10 — 交付与验收（Phase 4 交付 / delivery-summary）。"""

    def __init__(self) -> None:
        super().__init__(
            plan_name="p10_delivery",
            instruction=(
                "## P10 交付与验收\n"
                "\n"
                "### 前置条件\n"
                "- P9 PPTX 导出已完成\n"
                "\n"
                "### 输入\n"
                "- `output_dir`（必填）: 会话产物目录\n"
                "- `pages_dir`（必填）: HTML 页面目录\n"
                "- `pptx_path`（必填）: P9 输出的 PPTX 文件路径\n"
                "- `pptx_filename`（必填）: PPTX 文件名\n"
                "- `export_status`（必填）: P9 导出状态\n"
                "- `page_count`（可选）: 预期页数\n"
                "\n"
                "### 输出\n"
                "- `delivery_status`: sent / unavailable / failed\n"
                "- `artifact_tag`: artifact 标记文本（供前端解析渲染预览）\n"
                "- `send_file_status`: sent / skipped / failed / unavailable\n"
                "- `summary`: 完成状态摘要\n"
                "- `pages_complete`: HTML 页是否齐全（明细，不再用 partial 作主枚举）\n"
                "\n"
                "### 执行流程\n"
                "1. 验证 PPTX 产物：pptx_path 存在且 export_status != failed\n"
                "2. 验证 HTML 页面：pages_dir 下文件数量与 page_count 一致\n"
                "3. 发送文件：优先调用 send_file_to_user 发送 PPTX\n"
                "4. 生成 artifact 标记（send_file_to_user 不可用时的 fallback）\n"
                "5. 汇总交付状态（sent / unavailable / failed）\n"
                "\n"
                "### 失败兜底\n"
                "- PPTX 不存在或 export_status=failed：delivery_status = failed\n"
                "- send_file_to_user 不可用：delivery_status = unavailable\n"
                "- 发送失败：delivery_status = failed\n"
                "- HTML 页不齐：记入 pages_complete=false / summary，不单独占用主枚举\n"
            ),
        )

    async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        output_dir = str(inputs.get("output_dir") or "").strip()
        pages_dir = str(inputs.get("pages_dir") or "").strip()
        pptx_path = str(inputs.get("pptx_path") or "").strip()
        pptx_filename = str(inputs.get("pptx_filename") or "").strip()
        export_status = str(inputs.get("export_status") or "failed").strip()
        page_count = int(inputs.get("page_count") or 0)
        total_pages = PptCommon.resolve_total_pages(
            page_count=page_count,
            total_pages=inputs.get("total_pages"),
            default_structural_pages=_DEFAULT_STRUCTURAL_PAGES,
        )

        if not pages_dir:
            logger.error("[P10] pages_dir 为空")
            return {
                "delivery_status": DELIVERY_STATUS_FAILED,
                "artifact_tag": "",
                "send_file_status": "skipped",
                "pages_complete": False,
                "summary": "交付失败：pages_dir 为空",
            }

        pptx_ok = bool(pptx_path) and export_status != "failed" and Path(pptx_path).is_file()
        if not pptx_ok:
            logger.error(
                "[P10] PPTX 产物异常 pptx_path=%s export_status=%s exists=%s",
                bool(pptx_path),
                export_status,
                bool(pptx_path and Path(pptx_path).is_file()),
            )

        pages_ok = await self._check_pages(pages_dir, total_pages)

        send_file_status = "skipped"
        if pptx_ok and pptx_path:
            send_file_status = await self._send_file(pptx_path)
        if send_file_status == "skipped":
            send_file_status = "unavailable"

        delivery_status = PptCommon.normalize_delivery_status(
            pptx_ok=pptx_ok,
            send_file_status=send_file_status,
        )

        # 仅当文件真实存在且工具确认发送成功时，才标记 task_completed。
        task_completed = send_file_status == "sent" and pptx_ok

        need_artifact = send_file_status != "sent"
        artifact_tag = f"<!-- artifact:pptx {pages_dir} -->" if need_artifact and pages_dir else ""

        style_id = str(inputs.get("style_id") or "").strip()
        style_constraints = str(inputs.get("style_constraints") or "").strip()
        validate_pptx_status = str(inputs.get("validate_pptx_status") or "").strip()
        speaker_notes_status = str(inputs.get("speaker_notes_status") or "skipped")

        summary = self._build_summary(
            delivery_status,
            pptx_filename,
            page_count,
            pages_dir,
            send_file_status,
            pages_ok=pages_ok,
            style_id=style_id,
            style_constraints=style_constraints,
            validate_pptx_status=validate_pptx_status,
            speaker_notes_status=speaker_notes_status,
        )

        logger.info(
            "[P10] 交付完成 status=%s send=%s pptx=%s pages_ok=%s task_completed=%s",
            delivery_status,
            send_file_status,
            pptx_filename,
            pages_ok,
            task_completed,
        )

        return {
            "delivery_status": delivery_status,
            "artifact_tag": artifact_tag,
            "send_file_status": send_file_status,
            "pages_complete": pages_ok,
            "summary": summary,
            "__artifact__": {
                "info": {
                    "delivery_status": delivery_status,
                    "send_file_status": send_file_status,
                    "pptx_filename": pptx_filename,
                    "task_completed": task_completed,
                    "pages_complete": pages_ok,
                    "style_id": style_id,
                    "style_constraints": style_constraints,
                    "validate_pptx_status": validate_pptx_status,
                    "speaker_notes_status": speaker_notes_status,
                },
                "files": [{"path": pptx_path}] if pptx_path else [],
            },
        }

    async def _send_file(self, pptx_path: str) -> str:
        if not self.has_tool("send_file_to_user"):
            logger.info("[P10] send_file_to_user 工具不可用，跳过文件发送")
            return "unavailable"

        if not Path(pptx_path).is_file():
            logger.error("[P10] send_file 前文件不存在: %s", pptx_path)
            return "failed"

        try:
            result = await self.call_tool(
                "send_file_to_user",
                abs_file_path_list=[pptx_path],
            )
            text = normalize_tool_text(result)
            if self._is_send_failure(text):
                logger.error("[P10] send_file_to_user 发送失败: %s", text[:500])
                return "failed"
            logger.info("[P10] send_file_to_user 发送成功: %s", pptx_path)
            return "sent"
        except Exception as e:
            if isinstance(e, AbortError):
                raise
            logger.warning("[P10] send_file_to_user 发送失败: %s", e)
            return "failed"

    @staticmethod
    def _is_send_failure(text: str) -> bool:
        if not text or not text.strip():
            # 空结果视为失败，避免“无回执当成功”
            return True
        lowered = text.strip()
        # 明确成功 / 会话内已投递过 → 不算失败
        if "成功发送" in lowered or "文件已在本次会话发送过" in lowered:
            return False
        return any(marker in lowered for marker in _SEND_FAIL_MARKERS)

    async def _check_pages(self, pages_dir: str, page_count: int) -> bool:
        files: list[str] = []
        if self.has_tool("list_dir"):
            try:
                result = await self.call_tool("list_dir", path=pages_dir)
                files = self._parse_listing(result)
            except Exception as e:
                if isinstance(e, AbortError):
                    raise
                files = []

        if not files and self.has_tool("glob"):
            try:
                result = await self.call_tool(
                    "glob",
                    pattern="page-*.pptx.html",
                    path=pages_dir,
                )
                files = self._parse_listing(result)
            except Exception as e:
                if isinstance(e, AbortError):
                    raise
                files = []

        page_files = [f for f in files if f.startswith("page-") and f.endswith(".pptx.html")]
        if page_count <= 0:
            return bool(page_files)
        return len(page_files) == page_count

    def _parse_listing(self, result: Any) -> list[str]:
        if result is None:
            return []
        if isinstance(result, list):
            return [self._basename(self._extract_path_from_item(x)) for x in result]
        if isinstance(result, dict):
            for key in ("entries", "files", "filenames", "items", "result", "matches", "paths"):
                v = result.get(key)
                if isinstance(v, list):
                    return [self._basename(self._extract_path_from_item(x)) for x in v]
            content = result.get("content")
            if isinstance(content, str):
                return [self._basename(line.strip()) for line in content.splitlines() if line.strip()]
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
                    return [self._basename(line.strip()) for line in content.splitlines() if line.strip()]
            if isinstance(data, str):
                return [self._basename(line.strip()) for line in data.splitlines() if line.strip()]
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
                    return [self._basename(line.strip()) for line in content.splitlines() if line.strip()]
            if isinstance(dumped, list):
                return [self._basename(self._extract_path_from_item(x)) for x in dumped]
            if isinstance(dumped, str):
                return [self._basename(line.strip()) for line in dumped.splitlines() if line.strip()]
        if isinstance(result, str):
            return [self._basename(line.strip()) for line in result.splitlines() if line.strip()]
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

    @staticmethod
    def _basename(path: str) -> str:
        path = path.replace("\\", "/").rstrip("/")
        return path.rsplit("/", 1)[-1] if "/" in path else path

    @staticmethod
    def _build_summary(
        status: str,
        pptx_filename: str,
        page_count: int,
        pages_dir: str,
        send_file_status: str,
        *,
        pages_ok: bool = True,
        style_id: str = "",
        style_constraints: str = "",
        validate_pptx_status: str = "",
        speaker_notes_status: str = "",
    ) -> str:
        design = style_id or "（未指定风格）"
        if style_constraints:
            design = f"{design}；约束：{style_constraints[:80]}"
        gates = validate_pptx_status or "n/a"
        notes = speaker_notes_status or "skipped"
        pages_hint = "" if pages_ok else "；HTML 页数告警"

        if status == DELIVERY_STATUS_FAILED:
            if send_file_status == "failed":
                return (
                    f"PPT 交付失败（发送失败），页数：{page_count}，文件：{pptx_filename or '未生成'}，"
                    f"设计口径：{design}，validate：{gates}，备注：{notes}{pages_hint}"
                )
            return (
                f"PPT 生成失败，HTML 页面目录：{pages_dir}，"
                f"设计口径：{design}，validate：{gates}，备注：{notes}"
            )
        if send_file_status == "sent" or status == DELIVERY_STATUS_SENT:
            return (
                f"PPT 已生成并发送给用户，页数：{page_count}，文件：{pptx_filename or '未生成'}，"
                f"设计口径：{design}，validate：{gates}，备注：{notes}{pages_hint}"
            )
        if status == DELIVERY_STATUS_UNAVAILABLE:
            return (
                f"PPT 已生成但发送通道不可用，页数：{page_count}，文件：{pptx_filename or '未生成'}，"
                f"设计口径：{design}，validate：{gates}，备注：{notes}{pages_hint}"
            )
        return (
            f"PPT 生成完成，页数：{page_count}，文件：{pptx_filename}，"
            f"设计口径：{design}，validate：{gates}，备注：{notes}{pages_hint}"
        )

    async def _execute_stream(self, inputs: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        result = await self._execute(inputs)
        status_map = {
            DELIVERY_STATUS_SENT: "ok",
            DELIVERY_STATUS_UNAVAILABLE: "warning",
            DELIVERY_STATUS_FAILED: "error",
            # 兼容旧枚举（若上游仍写入）
            "ok": "ok",
            "partial": "warning",
            "failed": "error",
        }
        yield {
            **result,
            "node": self.plan_name,
            "status": status_map.get(result.get("delivery_status", ""), "warning"),
            "message": result.get("summary", ""),
        }