# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
InstructionOptimizer: Uses LLM to rewrite system/user prompts based on error cases and reflections.

- backward: Uses LLM to generate textual gradients for bad cases, writes to each TextualParameter.
- update: Uses LLM to generate optimized prompts, returns Updates (applied uniformly by Trainer).
"""

import re
from typing import List, Optional, Any, Tuple

import asyncio

from openjiuwen.agent_evolving.utils import TuneUtils
from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig, Model
from openjiuwen.core.foundation.prompt.assemble.assembler import PromptAssembler
from openjiuwen.agent_evolving.dataset import EvaluatedCase
from openjiuwen.agent_evolving.trajectory.types import Updates
from openjiuwen.agent_evolving.optimizer.base import TextualParameter
from openjiuwen.agent_evolving.optimizer.llm_call.base import LLMCallOptimizerBase
from openjiuwen.agent_evolving.optimizer.llm_call.templates import (
    PROMPT_INSTRUCTION_OPTIMIZE_TEMPLATE,
    PROMPT_INSTRUCTION_OPTIMIZE_BOTH_TEMPLATE,
    CREATE_PROMPT_TEXTUAL_GRADIENT_TEMPLATE,
    CREATE_BAD_CASE_TEMPLATE,
    PLACEHOLDER_RESTORE_TEMPLATE,
)


class InstructionOptimizer(LLMCallOptimizerBase):
    """
    Optimizes LLM prompts using textual gradients.

    Uses LLM to:
    1. backward(): Generate textual gradients explaining why prompts failed
    2. update(): Generate improved prompts based on gradients
    """
    def __init__(
        self,
        model_config: ModelRequestConfig,
        model_client_config: ModelClientConfig,
    ):
        """
        Initialize instruction optimizer.

        Args:
            model_config: LLM request configuration
            model_client_config: LLM client configuration
            targets: Parameters to optimize (default: ["system_prompt", "user_prompt"])
        """
        super().__init__()
        self._model = Model(model_client_config, model_config)

    def _backward(
        self,
        evaluated_cases: List[EvaluatedCase],
    ):
        """optimize Instruction"""
        for op_id, param in self._parameters.items():
            op = self._operators.get(op_id)
            if not op:
                continue
            textual_gradient = self._generate_textual_gradient(op)
            if not self._is_target_frozen(op, "system_prompt"):
                param.set_gradient("system_prompt", textual_gradient)
            if not self._is_target_frozen(op, "user_prompt"):
                param.set_gradient("user_prompt", textual_gradient)

    def _step(self) -> Optional[Updates]:
        """Generate optimized prompts from gradients."""
        updates: Updates = {}

        for op_id, param in self._parameters.items():
            op = self._operators.get(op_id)
            if not op:
                continue

            has_sys = "system_prompt" in self._targets and not self._is_target_frozen(op, "system_prompt")
            has_usr = "user_prompt" in self._targets and not self._is_target_frozen(op, "user_prompt")

            if has_sys and has_usr:
                sys_val, usr_val = self._optimize_both(op, param)
                if sys_val:
                    updates[(op_id, "system_prompt")] = sys_val
                if usr_val:
                    updates[(op_id, "user_prompt")] = usr_val
            elif has_sys:
                val = self._optimize_single(op, param, "system_prompt")
                if val:
                    updates[(op_id, "system_prompt")] = val
            elif has_usr:
                val = self._optimize_single(op, param, "user_prompt")
                if val:
                    updates[(op_id, "user_prompt")] = val

        return updates if updates else None

    def _generate_textual_gradient(self, op: Any) -> str:
        """Use LLM to analyze why the current prompt failed."""
        system_tpl = self._get_prompt_template(op, "system_prompt")
        user_tpl = self._get_prompt_template(op, "user_prompt")
        messages = CREATE_PROMPT_TEXTUAL_GRADIENT_TEMPLATE.format({
            "system_prompt": TuneUtils.get_content_string_from_template(system_tpl),
            "user_prompt": TuneUtils.get_content_string_from_template(user_tpl),
            "bad_cases": self._format_bad_cases(),
            "tools_description": "None",
        }).to_messages()
        raw_response = asyncio.run(self._model.invoke(messages)).content
        return raw_response if isinstance(raw_response, str) else str(raw_response)

    def _invoke_llm(self, messages) -> str:
        """Invoke LLM and return string content."""
        raw = asyncio.run(self._model.invoke(messages)).content
        return raw if isinstance(raw, str) else str(raw)

    def _optimize_both(self, op: Any, param: TextualParameter) -> Tuple[Optional[str], Optional[str]]:
        """Optimize both system and user prompts together."""
        system_tpl = self._get_prompt_template(op, "system_prompt")
        user_tpl = self._get_prompt_template(op, "user_prompt")
        gradient = param.get_gradient("system_prompt") or ""

        messages = PROMPT_INSTRUCTION_OPTIMIZE_BOTH_TEMPLATE.format({
            "system_prompt": TuneUtils.get_content_string_from_template(system_tpl),
            "user_prompt": TuneUtils.get_content_string_from_template(user_tpl),
            "bad_cases": self._format_bad_cases(),
            "reflections_on_bad_cases": gradient,
            "tools_description": "None",
        }).to_messages()

        raw_response = self._invoke_llm(messages)
        sys_prompt = self._extract_tag(raw_response, "SYSTEM_PROMPT_OPTIMIZED")
        usr_prompt = self._extract_tag(raw_response, "USER_PROMPT_OPTIMIZED")

        sys_prompt = self._restore_placeholders(
            TuneUtils.get_content_string_from_template(system_tpl),
            sys_prompt or "",
        ) if sys_prompt else None
        usr_prompt = self._restore_placeholders(
            TuneUtils.get_content_string_from_template(user_tpl),
            usr_prompt or "",
        ) if usr_prompt else None

        return sys_prompt, usr_prompt

    def _optimize_single(self, op: Any, param: TextualParameter, prompt_type: str) -> Optional[str]:
        """Optimize a single prompt (system or user)."""
        target_tpl = self._get_prompt_template(op, prompt_type)
        gradient = param.get_gradient(prompt_type) or ""

        messages = PROMPT_INSTRUCTION_OPTIMIZE_TEMPLATE.format({
            "prompt_instruction": TuneUtils.get_content_string_from_template(target_tpl),
            "bad_cases": self._format_bad_cases(),
            "reflections_on_bad_cases": gradient,
            "tools_description": "None",
        }).to_messages()

        raw_response = self._invoke_llm(messages)
        optimized = self._extract_tag(raw_response, "PROMPT_OPTIMIZED")

        if optimized:
            optimized = self._restore_placeholders(
                TuneUtils.get_content_string_from_template(target_tpl),
                optimized,
            )

        return optimized

    def _format_bad_cases(self) -> str:
        """Format bad cases for LLM prompts."""
        parts: List[str] = []
        for eval_case in self._bad_cases:
            formatted = CREATE_BAD_CASE_TEMPLATE.format({
                "question": str(eval_case.case.inputs),
                "label": str(eval_case.case.label),
                "answer": str(eval_case.answer),
                "reason": eval_case.reason,
            })
            content = formatted.content
            if isinstance(content, str):
                parts.append(content)
            elif content:
                parts.append(str(content))
        return "".join(parts)

    def _extract_tag(self, response: str, tag: str) -> Optional[str]:
        """Extract content between XML-like tags."""
        pattern = rf"<{tag}>(.*?)</{tag}>"
        match = re.search(pattern, response, re.DOTALL)
        if not match:
            return None

        content = match.group(1)
        return content.replace("<prompt_base>", "").replace("</prompt_base>", "")

    def _restore_placeholders(
        self,
        original_prompt: str,
        optimized_prompt: str,
    ) -> str:
        """Ensure optimized prompt has same placeholders as original."""
        original_keys = PromptAssembler(original_prompt).input_keys
        optimized_keys = PromptAssembler(optimized_prompt).input_keys

        missing = set(original_keys) - set(optimized_keys)

        if missing:
            messages = PLACEHOLDER_RESTORE_TEMPLATE.format({
                "original_prompt": original_prompt,
                "revised_prompt": optimized_prompt,
                "all_placeholders": str(list(original_keys)),
                "missing_placeholders": str(list(missing)),
            }).to_messages()

            raw = self._invoke_llm(messages)
            restored_keys = PromptAssembler(raw).input_keys

            still_missing = set(original_keys) - set(restored_keys)
            if still_missing:
                placeholder_text = "\n".join(f"{{{{{ph}}}}}" for ph in still_missing)
                raw = str(raw) + "\n" + placeholder_text
            return raw if isinstance(raw, str) else optimized_prompt

        return optimized_prompt
