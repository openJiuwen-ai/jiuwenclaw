# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import re
from typing import Dict, Any

from openjiuwen.dev_tools.agent_builder.builders.llm_agent.prompts import (
    REFINE_INTENTION_SYSTEM_PROMPT,
    USER_INTENTION_PROMPT_TEMPLATE
)
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import ApplicationError
from openjiuwen.core.common.security.json_utils import JsonUtils
from openjiuwen.core.foundation.llm import SystemMessage, UserMessage


class IntentionDetector:
    def __init__(self, llm):
        self.llm = llm

    @staticmethod
    def extract_intent(inputs: str) -> Dict[str, Any]:
        json_pattern = r"```json\n(.*?)```"
        json_match = re.search(json_pattern, inputs, re.DOTALL)
        result = json_match.group(1) if json_match else inputs
        return JsonUtils.safe_json_loads(result)

    def detect_refine_intent(self, query, agent_config_info) -> bool:
        try:
            if not query:
                return False
            
            system_msg = SystemMessage(content=REFINE_INTENTION_SYSTEM_PROMPT)
            user_messages = USER_INTENTION_PROMPT_TEMPLATE.format({
                'query': query,
                'agent_config_info': agent_config_info
            }).to_messages()
            
            import asyncio
            model_response = asyncio.run(self.llm.invoke([system_msg] + user_messages)).content
            json_response = self.extract_intent(model_response)
            return json_response.get("need_refined", False)
        except Exception as e:
            from openjiuwen.dev_tools.agent_builder.utils.enums import ErrorCode
            raise ApplicationError(
                StatusCode.ERROR,
                msg=f"NL2LLM Agent意图检测出现异常: {str(e)}",
                details={
                    "error": str(e),
                    "error_code": ErrorCode.LLM_AGENT_STATE_ERROR.value,
                },
                cause=e,
            ) from e
