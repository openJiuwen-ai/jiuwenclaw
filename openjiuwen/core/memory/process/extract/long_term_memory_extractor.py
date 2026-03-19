# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import json
from typing import Dict, Any
from openjiuwen.core.foundation.llm import JsonOutputParser
from openjiuwen.core.memory.process.extract.common import ExtractMemoryParams
from openjiuwen.core.memory.prompts.prompt_applier import PromptApplier
from openjiuwen.core.common.logging import memory_logger
from openjiuwen.core.common.logging.events import LogEventType


class LongTermMemoryExtractor:
    def __init__(self) -> None:
        pass

    @staticmethod
    async def extract_long_term_memory(
        extract_memory_paras: ExtractMemoryParams,
        timestamp: str,
        user_define: dict[str, str] = None,
        retries: int = 3,
    ) -> Dict[str, Any]:
        reference_str = ""
        input_msg_str = ""
        for msg in extract_memory_paras.history_messages:
            reference_str += f"{msg.name or msg.role}: {msg.content}\n"
        for msg in extract_memory_paras.messages:
            reference_str += f"{msg.name or msg.role}: {msg.content}\n"
            if msg.role == "user":
                input_msg_str += f"{msg.name or msg.role}: {msg.content}\n"

        prompt_content = PromptApplier().apply(
            "fragment_memory_prompt",
            {
                "conversation_time": timestamp,
                "input_messages": input_msg_str,
                "reference_messages": reference_str,
            },
        )
        model_input = [{"role": "user", "content": prompt_content}]
        parser = JsonOutputParser()
        for attempt in range(retries):
            try:
                response = await extract_memory_paras.base_chat_model.invoke(messages=model_input)
                result = await parser.parse(response.content)
                if isinstance(result, dict):
                    return result
            except json.JSONDecodeError as e:
                if attempt < retries - 1:
                    continue
                memory_logger.error(
                    "Long term memory extractor model output format error",
                    event_type=LogEventType.MEMORY_PROCESS,
                    exception=str(e)
                )
        return {}
