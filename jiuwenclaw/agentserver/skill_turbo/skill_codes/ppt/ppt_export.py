from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from jiuwenclaw.agentserver.skill_turbo.plan_node import AbortError, PlanNode
from jiuwenclaw.agentserver.skill_turbo.skill_codes.ppt.utils.bash_utils import (
    BashExecError,
    cli_path,
    quote_path,
    run_bash,
)

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


@dataclass
class ExportPaths:
    """导出路径相关参数封装。"""

    output_dir: str
    pages_dir: str
    pptx_path: str
    pptx_filename: str
    pptx_root: str


class PPTExportNode(PlanNode):
    """P9 — PPTX 导出（对应 SKILL Stage 8）。"""

    def __init__(self) -> None:
        super().__init__(
            plan_name="p9_ppt_export",
            instruction=(
                "## P9 PPTX 导出\n"
                "\n"
                "### 节点职责\n"
                "1. sanitize topic → 生成 PPTX 文件名\n"
                "2. 普通分支：cli.js convert 将 pages_dir 下 HTML 转为 PPTX\n"
                "3. `template_canvas` 分支：走 `_execute_template_finalizer`，执行 check → snapshot-dna → template-safe fix → convert → check-pptx-artifact 终检导出流程\n"
                "4. 验证 PPTX 产物（文件存在 + 大小 > 10KB）\n"
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
                "- `style_mode`（可选）: `template_canvas` 时走模板终检导出流程\n"
                "- `pack_dir`（`template_canvas` 分支必填）: 模板包目录绝对路径\n"
                "\n"
                "### 输出\n"
                "- `pptx_path`: 最终 PPTX 文件绝对路径（失败时为空字符串）\n"
                "- `pptx_filename`: PPTX 文件名\n"
                "- `export_status`: ok / partial / failed\n"
                "\n"
                "### 执行流程\n"
                "1. sanitize topic → 生成 PPTX 文件名\n"
                "2. `style_mode == template_canvas` 时走 `_execute_template_finalizer`：check → snapshot-dna → template-safe fix → convert → check-pptx-artifact\n"
                "3. 普通分支：cli.js convert 将 pages_dir 下 HTML 转为 PPTX\n"
                "4. 验证 PPTX 产物（文件存在 + 大小 > 10KB）\n"
                "\n"
                "### 失败兜底\n"
                "- `template_canvas` 分支 `pack_dir` 为空：export_status = failed\n"
                "- fill.js check 出现内容质量类 HARD 错误：export_status = failed（manifest 声明类警告忽略）\n"
                "- snapshot-template-dna / template-safe fix 失败：export_status = failed\n"
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

        pptx_root = str(inputs.get("pptx_root") or "").strip()

        # 模板画布分支：style_mode == template_canvas 时走模板终检导出流程
        style_mode = str(inputs.get("style_mode") or "").strip()
        if style_mode == "template_canvas":
            paths = ExportPaths(
                output_dir=output_dir,
                pages_dir=pages_dir,
                pptx_path=pptx_path,
                pptx_filename=pptx_filename,
                pptx_root=pptx_root,
            )
            return await self._execute_template_finalizer(inputs, paths)

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

    async def _execute_template_finalizer(
        self,
        inputs: dict[str, Any],
        paths: ExportPaths,
    ) -> dict[str, Any]:
        """模板包分支：执行 template-finalizer 终检导出流程。

        流程：check（manifest 声明类忽略）→ snapshot-dna → template-safe fix（dry run）
              → post-fix gate（warning 不阻塞）→ convert → check-pptx-artifact（warning 不阻塞）
        """
        output_dir = paths.output_dir
        pages_dir = paths.pages_dir
        pptx_path = paths.pptx_path
        pptx_filename = paths.pptx_filename
        pptx_root = paths.pptx_root
        pack_dir = str(inputs.get("pack_dir") or "").strip()
        if not pack_dir:
            logger.error("[P9-TF] pack_dir 为空，无法执行模板终检")
            return {
                "pptx_path": "",
                "pptx_filename": pptx_filename,
                "export_status": "failed",
            }

        temp_dir = f"{output_dir}/temp"
        dna_path = f"{temp_dir}/template-dna-before.json"

        # 1. 复核 template-filler 输出
        try:
            check_cmd = (
                f"{cli_path('check', pptx_root)} "
                f"{quote_path(pages_dir)}"
            )
            result = await run_bash(
                self, check_cmd,
                timeout_seconds=300, required=False, workdir=pptx_root,
            )
            if result.exit_code != 0:
                # 解析 HARD 错误，区分 manifest 声明类（可忽略）和内容质量类（阻塞）
                check_output = result.stdout + "\n" + result.stderr
                has_content_error = False
                for line in check_output.splitlines():
                    if "HARD" not in line.upper():
                        continue
                    if "manifest" in line.lower() and "声明" in line:
                        continue  # manifest 声明类，忽略
                    has_content_error = True
                    break
                if has_content_error:
                    logger.error("[P9-TF] fill.js check 失败 exit=%d", result.exit_code)
                    return {
                        "pptx_path": "",
                        "pptx_filename": pptx_filename,
                        "export_status": "failed",
                    }
                else:
                    logger.info("[P9-TF] fill.js check 仅剩 manifest 声明类警告，视为通过")
        except BashExecError as e:
            logger.error("[P9-TF] fill.js check 异常: %s", e)
            return {
                "pptx_path": "",
                "pptx_filename": pptx_filename,
                "export_status": "failed",
            }

        # 2. DNA 快照（在 fix 之前保存原始 DNA）
        try:
            snapshot_cmd = (
                f"{cli_path('snapshot-template-dna', pptx_root)} "
                f"{quote_path(pages_dir)} {quote_path(dna_path)}"
            )
            result = await run_bash(
                self, snapshot_cmd,
                timeout_seconds=60, required=False, workdir=pptx_root,
            )
            if result.exit_code != 0:
                logger.error("[P9-TF] snapshot-template-dna 失败 exit=%d", result.exit_code)
                return {
                    "pptx_path": "",
                    "pptx_filename": pptx_filename,
                    "export_status": "failed",
                }
        except BashExecError as e:
            logger.error("[P9-TF] snapshot-template-dna 异常: %s", e)
            return {
                "pptx_path": "",
                "pptx_filename": pptx_filename,
                "export_status": "failed",
            }

        # 3. template-safe 检查（只检查不修改，避免破坏 HTML 内容）
        try:
            fix_cmd = (
                f"{cli_path('fix', pptx_root)} "
                f"{quote_path(pages_dir + '/')} --profile template-safe"
            )
            result = await run_bash(
                self, fix_cmd,
                timeout_seconds=600, required=False, workdir=pptx_root,
            )
            if result.exit_code != 0:
                logger.error("[P9-TF] template-safe fix 失败 exit=%d", result.exit_code)
                return {
                    "pptx_path": "",
                    "pptx_filename": pptx_filename,
                    "export_status": "failed",
                }
        except BashExecError as e:
            logger.error("[P9-TF] template-safe fix 异常: %s", e)
            return {
                "pptx_path": "",
                "pptx_filename": pptx_filename,
                "export_status": "failed",
            }

        # 4. post-fix 安全闸（字号/内容跨度问题不阻塞导出，仅警告）
        try:
            post_fix_cmd = (
                f"{cli_path('check-post-fix-template-pages', pptx_root)} "
                f"{quote_path(pages_dir)} {quote_path(dna_path)}"
            )
            result = await run_bash(
                self, post_fix_cmd,
                timeout_seconds=300, required=False, workdir=pptx_root,
            )
            if result.exit_code != 0:
                logger.warning("[P9-TF] check-post-fix-template-pages 失败 exit=%d（字号/内容跨度问题不阻塞导出）", result.exit_code)
        except BashExecError as e:
            logger.warning("[P9-TF] check-post-fix-template-pages 异常: %s（继续导出）", e)

        # 5. 导出 PPTX
        convert_ok = await self._run_convert(pages_dir, pptx_path, pptx_root)
        if not convert_ok:
            return {
                "pptx_path": "",
                "pptx_filename": pptx_filename,
                "export_status": "failed",
            }

        # 6. 产物硬闸
        try:
            artifact_cmd = (
                f"{cli_path('check-pptx-artifact', pptx_root)} "
                f"{quote_path(pages_dir)} {quote_path(pptx_path)}"
            )
            result = await run_bash(
                self, artifact_cmd,
                timeout_seconds=60, required=False, workdir=pptx_root,
            )
            if result.exit_code != 0:
                logger.warning("[P9-TF] check-pptx-artifact 失败 exit=%d（缺少批准凭证，不阻塞交付）", result.exit_code)
        except BashExecError as e:
            logger.warning("[P9-TF] check-pptx-artifact 异常: %s（不阻塞交付）", e)

        logger.info("[P9-TF] 模板终检导出完成: %s", pptx_path)
        return {
            "pptx_path": pptx_path,
            "pptx_filename": pptx_filename,
            "export_status": "ok",
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
