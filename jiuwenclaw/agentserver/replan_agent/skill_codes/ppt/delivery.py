from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

from openjiuwen.core.runner.callback import AbortError

from jiuwenclaw.agentserver.replan_agent.plan_node import PlanNode

logger = logging.getLogger(__name__)

_DEFAULT_STRUCTURAL_PAGES = 2


def _looks_like_path(value: str) -> bool:
    return "/" in value or "\\" in value or value.endswith(".html")


class DeliveryNode(PlanNode):
    """P10 — 交付与验收（对应 SKILL Stage 9）。"""

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
                "- `delivery_status`: ok / partial / failed\n"
                "- `artifact_tag`: artifact 标记文本（供前端解析渲染预览）\n"
                "- `send_file_status`: sent / skipped / failed\n"
                "- `summary`: 完成状态摘要\n"
                "\n"
                "### 执行流程\n"
                "1. 验证 PPTX 产物：pptx_path 存在且 export_status != failed\n"
                "2. 验证 HTML 页面：pages_dir 下文件数量与 page_count 一致\n"
                "3. 发送文件：优先调用 send_file_to_user 发送 PPTX\n"
                "4. 生成 artifact 标记（send_file_to_user 不可用时的 fallback）\n"
                "5. 汇总交付状态\n"
                "\n"
                "### 失败兜底\n"
                "- PPTX 不存在或 export_status=failed：delivery_status = failed\n"
                "- HTML 页面不完整：delivery_status = partial\n"
                "- pages_dir 为空：delivery_status = failed\n"
                "- send_file_to_user 不可用或失败：fallback 到 artifact_tag\n"
            ),
        )

    async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        output_dir = str(inputs.get("output_dir") or "").strip()
        pages_dir = str(inputs.get("pages_dir") or "").strip()
        pptx_path = str(inputs.get("pptx_path") or "").strip()
        pptx_filename = str(inputs.get("pptx_filename") or "").strip()
        export_status = str(inputs.get("export_status") or "failed").strip()
        page_count = int(inputs.get("page_count") or 0)
        total_pages = int(
            inputs.get("total_pages") or (page_count + _DEFAULT_STRUCTURAL_PAGES)
        )

        if not pages_dir:
            logger.error("[P10] pages_dir 为空")
            return {
                "delivery_status": "failed",
                "artifact_tag": "",
                "send_file_status": "skipped",
                "summary": "交付失败：pages_dir 为空",
            }

        pptx_ok = bool(pptx_path) and export_status != "failed"
        if not pptx_ok:
            logger.error(
                "[P10] PPTX 产物异常 pptx_path=%s export_status=%s",
                bool(pptx_path),
                export_status,
            )

        pages_ok = await self._check_pages(pages_dir, total_pages)

        send_file_status = "skipped"
        if pptx_ok and pptx_path:
            send_file_status = await self._send_file(pptx_path)

        if not pptx_ok:
            delivery_status = "failed"
        elif not pages_ok:
            delivery_status = "partial"
        else:
            delivery_status = "ok"

        need_artifact = send_file_status != "sent"
        artifact_tag = f"<!-- artifact:pptx {pages_dir} -->" if need_artifact and pages_dir else ""

        summary = self._build_summary(
            delivery_status, pptx_filename, page_count, pages_dir, send_file_status
        )

        logger.info(
            "[P10] 交付完成 status=%s send=%s pptx=%s",
            delivery_status,
            send_file_status,
            pptx_filename,
        )

        return {
            "delivery_status": delivery_status,
            "artifact_tag": artifact_tag,
            "send_file_status": send_file_status,
            "summary": summary,
        }

    async def _send_file(self, pptx_path: str) -> str:
        if not self.has_tool("send_file_to_user"):
            logger.info("[P10] send_file_to_user 工具不可用，跳过文件发送")
            return "skipped"

        try:
            await self.call_tool(
                "send_file_to_user",
                abs_file_path_list=[pptx_path],
            )
            logger.info("[P10] send_file_to_user 发送成功: %s", pptx_path)
            return "sent"
        except Exception as e:
            if isinstance(e, AbortError):
                raise
            logger.warning("[P10] send_file_to_user 发送失败: %s", e)
            return "failed"

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
    ) -> str:
        if status == "failed":
            return f"PPT 生成失败，HTML 页面目录：{pages_dir}"
        if status == "partial":
            return f"PPT 部分完成，页数：{page_count}，文件：{pptx_filename or '未生成'}"
        send_info = ""
        if send_file_status == "sent":
            send_info = "，已发送给用户"
        elif send_file_status == "failed":
            send_info = "，文件发送失败"
        return f"PPT 生成完成，页数：{page_count}，文件：{pptx_filename}{send_info}"

    async def _execute_stream(self, inputs: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        result = await self._execute(inputs)
        status_map = {"ok": "ok", "partial": "warning", "failed": "error"}
        yield {
            **result,
            "node": self.plan_name,
            "status": status_map.get(result.get("delivery_status", ""), "warning"),
            "message": result.get("summary", ""),
        }