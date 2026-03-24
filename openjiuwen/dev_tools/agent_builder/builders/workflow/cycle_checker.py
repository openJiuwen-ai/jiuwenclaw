# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import asyncio
from typing import Tuple

from openjiuwen.core.common.logging import LogManager
from openjiuwen.core.foundation.llm import Model
from openjiuwen.core.foundation.llm import SystemMessage
from openjiuwen.core.common.security.json_utils import JsonUtils

from openjiuwen.dev_tools.agent_builder.builders.workflow.prompts import (
    CHECK_CYCLE_SYSTEM_PROMPT,
    CHECK_CYCLE_USER_PROMPT_TEMPLATE
)
from openjiuwen.dev_tools.agent_builder.utils.utils import extract_json_from_text

logger = LogManager.get_logger("agent_builder")


class CycleChecker:
    def __init__(self, llm: Model) -> None:
        self.llm: Model = llm

    def check_mermaid_cycle(self, mermaid_code: str) -> str:
        user_messages = CHECK_CYCLE_USER_PROMPT_TEMPLATE.format({
            "mermaid_code": mermaid_code,
        }).to_messages()
        user_prompt = user_messages[0].content
        return asyncio.run(self.llm.invoke([
            SystemMessage(content=CHECK_CYCLE_SYSTEM_PROMPT),
            SystemMessage(content=user_prompt),
        ])).content

    @staticmethod
    def parse_cycle_result_json(inputs: str) -> Tuple[bool, str]:
        json_str = extract_json_from_text(inputs)
        result_dict = JsonUtils.safe_json_loads(json_str)
        need_refined = result_dict.get("need_refined", False)
        loop_desc = result_dict.get("loop_desc", "")
        return bool(need_refined), str(loop_desc)

    def check_and_parse(self, mermaid_code: str) -> Tuple[bool, str]:
        cycle_result_json = self.check_mermaid_cycle(mermaid_code)
        return self.parse_cycle_result_json(cycle_result_json)
