# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""OutputSchemaRail — 输出 Schema 强制。

框架贡献：给科研 agent 注入结构化输出的 JSON Schema 约束，并在 post_run
校验最终输出是否满足 schema。防止 agent 输出自由文本、破坏下游解析。

- before_model_call：把 schema 以 PromptSection 注入。
- after_invoke：校验最终输出是否满足 schema；不满足时记录 schema_violations
  （不擅自重写，交由调用方/Reviewer 修复循环处理）。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts import PromptSection
from openjiuwen.harness.rails.base import DeepAgentRail

logger = logging.getLogger(__name__)


class OutputSchemaRail(DeepAgentRail):
    """强制最终输出满足指定 JSON Schema。"""

    priority: int = 150

    def __init__(self, schema: dict[str, Any], *, prompt_section: bool = True) -> None:
        super().__init__()
        self.schema = schema
        self._prompt_section = prompt_section
        self.schema_violations: list[str] = []

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        if not self._prompt_section:
            return
        builder = getattr(
            getattr(self, "_deep_agent", None) or ctx.agent,
            "system_prompt_builder",
            None,
        )
        if builder is None:
            return
        schema_text = json.dumps(self.schema, ensure_ascii=False, indent=1)
        prompt = (
            "[Output Schema]\n"
            "最终输出必须是合法 JSON，且只包含以下键，禁止多余字段或自由文本：\n"
            + schema_text
        )
        builder.add_section(PromptSection(
            name="output_schema",
            content={getattr(builder, "language", "cn") or "cn": prompt},
            priority=self.priority,
        ))

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        final = self._extract_final(ctx)
        if not final:
            return
        self.validate(final)

    def validate(self, text: str) -> list[str]:
        """校验文本是否为合法 JSON 且含全部必填键，返回违规列表。"""
        self.schema_violations = []
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            self.schema_violations.append("invalid JSON")
            return self.schema_violations
        if not isinstance(data, dict):
            self.schema_violations.append("root must be object")
            return self.schema_violations
        required = set(self.schema.get("required", []))
        missing = required - set(data.keys())
        if missing:
            self.schema_violations.append(f"missing keys: {sorted(missing)}")
        if self.schema.get("additionalProperties", True) is False:
            allowed = set(self.schema.get("properties", {}).keys())
            extra = set(data.keys()) - allowed
            if extra:
                self.schema_violations.append(f"unexpected keys: {sorted(extra)}")
        if self.schema_violations:
            logger.info("[OutputSchemaRail] schema 违规: %s", self.schema_violations)
        return self.schema_violations

    # -- 防御性访问 -----------------------------------------------------------

    def _extract_final(self, ctx: AgentCallbackContext) -> str:
        for candidate in ("final_answer", "answer", "final_text"):
            value = getattr(ctx, candidate, None)
            if value is None:
                continue
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, dict):
                return str(value.get("content") or value)
        return ""
