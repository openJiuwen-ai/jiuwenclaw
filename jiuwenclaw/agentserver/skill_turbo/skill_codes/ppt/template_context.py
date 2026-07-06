from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from jiuwenclaw.agentserver.skill_turbo.plan_node import AbortError, PlanNode
from jiuwenclaw.agentserver.skill_turbo.skill_codes.ppt.ppt_common import PptCommon
from jiuwenclaw.agentserver.skill_turbo.skill_codes.ppt.utils.bash_utils import (
    BashExecError,
    fill_js_path,
    quote_path,
    run_bash,
)

logger = logging.getLogger(__name__)


class TemplateContextNode(PlanNode):
    """P3.5 — 模板叙事上下文预处理（条件执行，仅 style_mode == template_pack 时运行）。

    对应 1.1.18b template-context 子技能。
    读取模板包 template-spec.json 的叙事元数据，生成 template-narrative-context.json，
    供 Alice-1 (P4) prompt 注入。
    """

    def __init__(self) -> None:
        super().__init__(
            plan_name="p3_5_template_context",
            instruction=(
                "## P3.5 模板叙事上下文预处理（条件执行）\n"
                "\n"
                "### 节点职责\n"
                "1. 仅当 style_mode == template_pack 时执行\n"
                "2. 调用 fill.js narrative-context 读取模板包叙事元数据\n"
                "3. 生成 {output_dir}/temp/template-narrative-context.json\n"
                "4. 当 available=true 时赋值 template_narrative_context_json 供 P4 注入\n"
                "\n"
                "### 前置条件\n"
                "- style_mode == template_pack\n"
                "- pack_dir 已由 P2 收集\n"
                "- pptx_root 已由 P0 解析\n"
                "- bash / read_file 工具可用\n"
                "\n"
                "### 输入\n"
                "- `pack_dir`（必填）: 模板包目录绝对路径\n"
                "- `output_dir`（必填）: 工作目录\n"
                "- `pptx_root`（必填）: pptx-craft 根目录\n"
                "\n"
                "### 输出\n"
                "- `template_narrative_context_json`: 叙事上下文 JSON 字符串（available=true 时非空）\n"
                "- `template_narrative_available`: bool — 叙事框架是否可用\n"
                "\n"
                "### 执行流程\n"
                "1. 校验 style_mode == template_pack，否则跳过\n"
                "2. 创建 {output_dir}/temp/ 目录\n"
                "3. 运行 fill.js narrative-context {pack_dir} {output_dir}/temp/template-narrative-context.json\n"
                "4. 读取生成的 JSON 文件\n"
                "5. available=true → 赋值 template_narrative_context_json\n"
                "   available=false → 记录 reason，template_narrative_context_json 为空\n"
                "\n"
                "### 失败兜底\n"
                "- fill.js 执行失败：不阻塞 pipeline，template_narrative_context_json 为空\n"
                "- JSON 解析失败：同上\n"
                "- 非模板分支：直接跳过，不读取 template-spec.json\n"
            ),
        )

    async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        style_mode = str(inputs.get("style_mode") or "").strip()
        if style_mode != "template_pack":
            logger.info("[P3.5] style_mode=%s，非模板包模式，跳过", style_mode)
            return {
                "template_narrative_context_json": "",
                "template_narrative_available": False,
            }

        pack_dir = str(inputs.get("pack_dir") or "").strip()
        output_dir = str(inputs.get("output_dir") or "").strip()
        pptx_root = str(inputs.get("pptx_root") or "").strip()

        if not pack_dir or not output_dir or not pptx_root:
            logger.error(
                "[P3.5] 必填字段缺失 pack_dir=%s output_dir=%s pptx_root=%s",
                bool(pack_dir), bool(output_dir), bool(pptx_root),
            )
            return {
                "template_narrative_context_json": "",
                "template_narrative_available": False,
            }

        temp_dir = f"{output_dir}/temp"
        context_path = f"{temp_dir}/template-narrative-context.json"

        # 运行 fill.js narrative-context
        try:
            fill_cmd = (
                f"{fill_js_path(pptx_root)} narrative-context "
                f"{quote_path(pack_dir)} {quote_path(context_path)}"
            )
            await run_bash(
                self, fill_cmd,
                timeout_seconds=60, required=True, workdir=pptx_root,
            )
            logger.info("[P3.5] narrative-context 生成完成: %s", context_path)
        except BashExecError as e:
            logger.error("[P3.5] fill.js narrative-context 失败: %s", e)
            return {
                "template_narrative_context_json": "",
                "template_narrative_available": False,
            }
        except Exception as e:
            if isinstance(e, AbortError):
                raise
            logger.error("[P3.5] narrative-context 未知异常: %s", e)
            return {
                "template_narrative_context_json": "",
                "template_narrative_available": False,
            }

        # 读取生成的 JSON
        context_text = await PptCommon.read_file(
            self, context_path, label="template-narrative-context.json",
        )
        if not context_text:
            logger.warning("[P3.5] 叙事上下文文件为空或不存在")
            return {
                "template_narrative_context_json": "",
                "template_narrative_available": False,
            }

        try:
            context_data = json.loads(context_text)
        except json.JSONDecodeError as e:
            logger.warning("[P3.5] 叙事上下文 JSON 解析失败: %s", e)
            return {
                "template_narrative_context_json": "",
                "template_narrative_available": False,
            }

        available = bool(context_data.get("available"))
        if not available:
            reason = str(context_data.get("reason") or "")
            logger.info("[P3.5] 叙事框架不可用: %s", reason)
            return {
                "template_narrative_context_json": "",
                "template_narrative_available": False,
            }

        logger.info("[P3.5] 叙事框架可用，application_mode=%s", context_data.get("application_mode"))
        return {
            "template_narrative_context_json": context_text.strip(),
            "template_narrative_available": True,
        }

    async def _execute_stream(self, inputs: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        result = await self._execute(inputs)
        available = result.get("template_narrative_available")
        if inputs.get("style_mode") != "template_pack":
            msg = "非模板包模式，跳过模板叙事预处理"
        elif available:
            msg = "模板叙事上下文已生成，可供 Alice-1 注入"
        else:
            msg = "模板叙事上下文不可用，不阻塞 pipeline"
        yield {
            **result,
            "node": self.plan_name,
            "status": "ok",
            "message": msg,
        }
