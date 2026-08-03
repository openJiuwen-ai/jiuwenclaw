from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from jiuwenclaw.agentserver.skill_turbo.plan_node import AbortError, PlanNode
from jiuwenclaw.agentserver.skill_turbo.skill_codes.ppt.ppt_common import PptCommon

logger = logging.getLogger(__name__)


class TemplateContextNode(PlanNode):
    """P3.5 — 模板叙事上下文预处理（条件执行，仅 style_mode == template_canvas 时运行）。

    读取模板包 template-spec.json 的 narrative_framework 字段，
    作为软约束传给 P4 outline-planner，不再落盘 JSON 中间文件。
    """

    def __init__(self) -> None:
        super().__init__(
            plan_name="p3_5_template_context",
            instruction=(
                "## P3.5 模板叙事上下文预处理（条件执行）\n"
                "\n"
                "### 节点职责\n"
                "1. 仅当 style_mode == template_canvas 时执行\n"
                "2. 读取模板包 template-spec.json 的 narrative_framework 字段\n"
                "3. 将 narrative_framework 作为软约束传给 P4 outline-planner\n"
                "\n"
                "### 前置条件\n"
                "- style_mode == template_canvas\n"
                "- pack_dir 已由 P2 收集\n"
                "- read_file 工具可用\n"
                "\n"
                "### 输入\n"
                "- `pack_dir`（必填）: 模板包目录绝对路径\n"
                "- `output_dir`（必填）: 工作目录\n"
                "\n"
                "### 输出\n"
                "- `narrative_framework`: 叙事框架字符串（可用时非空）\n"
                "- `template_narrative_available`: bool — 叙事框架是否可用\n"
                "\n"
                "### 执行流程\n"
                "1. 校验 style_mode == template_canvas，否则跳过\n"
                "2. read_file 读取 {pack_dir}/template-spec.json\n"
                "3. 解析 narrative_framework 字段\n"
                "4. 合法 → 赋值 narrative_framework\n"
                "   不合法/不存在 → narrative_framework 为空，记录 reason\n"
                "\n"
                "### 失败兜底\n"
                "- template-spec.json 不存在：不阻塞 pipeline，narrative_framework 为空\n"
                "- JSON 解析失败：同上\n"
                "- 非模板分支：直接跳过\n"
            ),
        )

    async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        style_mode = str(inputs.get("style_mode") or "").strip()
        if style_mode != "template_canvas":
            logger.info("[P3.5] style_mode=%s，非模板画布模式，跳过", style_mode)
            return {
                "narrative_framework": "",
                "template_narrative_available": False,
            }

        pack_dir = str(inputs.get("pack_dir") or "").strip()
        output_dir = str(inputs.get("output_dir") or "").strip()

        if not pack_dir or not output_dir:
            logger.error(
                "[P3.5] 必填字段缺失 pack_dir=%s output_dir=%s",
                bool(pack_dir), bool(output_dir),
            )
            return {
                "narrative_framework": "",
                "template_narrative_available": False,
            }

        spec_path = f"{pack_dir}/template-spec.json"

        # 读取 template-spec.json
        spec_text = await PptCommon.read_file(
            self, spec_path, label="template-spec.json",
        )
        if not spec_text:
            logger.warning("[P3.5] template-spec.json 为空或不存在: %s", spec_path)
            return {
                "narrative_framework": "",
                "template_narrative_available": False,
            }

        try:
            spec_data = json.loads(spec_text)
        except json.JSONDecodeError as e:
            logger.warning("[P3.5] template-spec.json JSON 解析失败: %s", e)
            return {
                "narrative_framework": "",
                "template_narrative_available": False,
            }

        narrative = spec_data.get("narrative_framework")
        if not narrative:
            logger.info("[P3.5] template-spec.json 中无 narrative_framework 字段或为空")
            return {
                "narrative_framework": "",
                "template_narrative_available": False,
            }

        # narrative_framework 可以是字符串或对象，统一转为字符串传递
        if isinstance(narrative, (dict, list)):
            narrative_str = json.dumps(narrative, ensure_ascii=False)
        else:
            narrative_str = str(narrative).strip()

        if not narrative_str:
            logger.info("[P3.5] narrative_framework 为空")
            return {
                "narrative_framework": "",
                "template_narrative_available": False,
            }

        logger.info("[P3.5] 叙事框架可用: %s", spec_path)
        return {
            "narrative_framework": narrative_str,
            "template_narrative_available": True,
        }

    async def _execute_stream(self, inputs: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        result = await self._execute(inputs)
        available = result.get("template_narrative_available")
        if inputs.get("style_mode") != "template_canvas":
            msg = "非模板画布模式，跳过模板叙事预处理"
        elif available:
            msg = "模板叙事框架已读取，可供 P4 注入"
        else:
            msg = "模板叙事框架不可用，不阻塞 pipeline"
        yield {
            **result,
            "node": self.plan_name,
            "status": "ok",
            "message": msg,
        }
