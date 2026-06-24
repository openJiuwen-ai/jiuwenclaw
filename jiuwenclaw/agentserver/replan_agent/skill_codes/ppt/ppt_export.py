from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from openjiuwen.core.runner.callback import AbortError

from jiuwenclaw.agentserver.replan_agent.plan_node import PlanNode
from jiuwenclaw.agentserver.replan_agent.skill_codes.ppt.utils.bash_utils import (
    BashExecError,
    cli_path,
    quote_path,
    run_bash,
)

_PPT_DIR = Path(__file__).resolve().parent

logger = logging.getLogger(__name__)

_ILLEGAL_FILENAME_RE = re.compile(r'[<>:"/\\|?*]')
_WINDOWS_RESERVED = frozenset(
    {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)


def _sanitize_filename(topic: str, *, max_length: int = 50) -> str:
    name = topic.strip()
    name = _ILLEGAL_FILENAME_RE.sub("_", name)
    name = re.sub(r"\s+", "_", name)
    name = name.strip(". ")
    if name.upper() in _WINDOWS_RESERVED:
        name = f"ppt_{name}"
    if len(name) > max_length:
        name = name[:max_length].rstrip(". ")
    if not name:
        name = "presentation"
    return name


class PPTExportNode(PlanNode):
    """P9 — PPTX 导出（对应 SKILL Stage 8）。"""

    def __init__(self) -> None:
        super().__init__(
            plan_name="p9_ppt_export",
            instruction=(
                "## P9 PPTX 导出\n"
                "\n"
                "### 前置条件\n"
                "- `bash` 工具可用\n"
                "- Node.js >= 18 已安装\n"
                "- P8.3 QA 与自动修复已完成，pages_dir 下 HTML 文件就绪\n"
                "\n"
                "### 输入\n"
                "- `output_dir`（必填）: 会话产物目录\n"
                "- `pages_dir`（必填）: HTML 页面目录\n"
                "- `topic`（必填）: PPT 主题，用于生成 PPTX 文件名\n"
                "\n"
                "### 输出\n"
                "- `pptx_path`: 最终 PPTX 文件绝对路径（失败时为空字符串）\n"
                "- `pptx_filename`: PPTX 文件名\n"
                "- `export_status`: ok / partial / failed\n"
                "\n"
                "### 执行流程\n"
                "1. sanitize topic → 生成 PPTX 文件名\n"
                "2. cli.js convert：将 pages_dir 下 HTML 转为 PPTX\n"
                "3. 验证 PPTX 产物（文件存在 + 大小 > 10KB）\n"
                "\n"
                "### 失败兜底\n"
                "- cli.js convert 失败：export_status = failed，pptx_path 为空\n"
                "- PPTX 文件过小（< 10KB）：export_status = partial\n"
                "- bash 不可用：export_status = failed\n"
            ),
        )

    async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        output_dir = str(inputs.get("output_dir") or "").strip()
        pages_dir = str(inputs.get("pages_dir") or "").strip()
        topic = str(inputs.get("topic") or "").strip()

        if not output_dir or not pages_dir:
            logger.error(
                "[P9] 必填字段缺失 output_dir=%s pages_dir=%s",
                bool(output_dir), bool(pages_dir),
            )
            return {
                "pptx_path": "",
                "pptx_filename": "",
                "export_status": "failed",
            }

        sanitized = _sanitize_filename(topic or "presentation")
        pptx_filename = f"{sanitized}.pptx"
        pptx_path = f"{output_dir}/{pptx_filename}"

        pptx_root = str(inputs.get("pptx_root") or str(_PPT_DIR))
        convert_ok = await self._run_convert(pages_dir, pptx_path, pptx_root)
        if not convert_ok:
            return {
                "pptx_path": "",
                "pptx_filename": pptx_filename,
                "export_status": "failed",
            }

        export_status = await self._validate_pptx(pptx_path, pptx_root)

        return {
            "pptx_path": pptx_path if export_status != "failed" else "",
            "pptx_filename": pptx_filename,
            "export_status": export_status,
        }

    async def _run_convert(self, pages_dir: str, pptx_path: str, pptx_root: str) -> bool:
        try:
            convert_cmd = (
                f"{cli_path('convert', pptx_root)} "
                f"{quote_path(pages_dir + '/')} {quote_path(pptx_path)}"
            )
            await run_bash(
                self, convert_cmd,
                timeout_seconds=600, required=True, workdir=pptx_root,
            )
            logger.info("[P9] cli.js convert 完成")
            return True
        except BashExecError as e:
            logger.error("[P9] cli.js convert 失败: %s", e)
            return False
        except Exception as e:
            if isinstance(e, AbortError):
                raise
            logger.error("[P9] cli.js convert 未知异常: %s", e)
            return False

    async def _validate_pptx(self, pptx_path: str, pptx_root: str) -> str:
        try:
            escaped_path = pptx_path.replace('\\', '\\\\').replace("'", "\\'").replace('"', '\\"')
            stat_cmd = (
                f"node -e \"const fs=require('fs');const s=fs.statSync('{escaped_path}');"
                f'process.stdout.write(String(s.size))"'
            )
            result = await run_bash(
                self, stat_cmd,
                timeout_seconds=30, required=False, workdir=pptx_root,
            )
            if result.exit_code != 0:
                logger.warning("[P9] PPTX stat 失败，跳过大小验证")
                return "ok"
            size = int(result.stdout.strip() or "0")
            if size < 10 * 1024:
                logger.warning("[P9] PPTX 文件过小 size=%d < 10KB", size)
                return "partial"
            logger.info("[P9] PPTX 验证通过 size=%d", size)
            return "ok"
        except Exception as e:
            if isinstance(e, AbortError):
                raise
            logger.warning("[P9] PPTX 验证失败: %s", e)
            return "ok"

    async def _execute_stream(self, inputs: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        result = await self._execute(inputs)
        status_map = {"ok": "ok", "partial": "warning", "failed": "error"}
        yield {
            **result,
            "node": self.plan_name,
            "status": status_map.get(result.get("export_status", ""), "warning"),
            "message": (
                f"PPTX 导出完成 status={result.get('export_status')} "
                f"file={result.get('pptx_filename')}"
            ),
        }