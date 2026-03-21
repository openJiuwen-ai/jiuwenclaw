# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import json
from typing import List, Tuple
from pydantic import BaseModel, Field

from openjiuwen.core.foundation.llm import Model, JsonOutputParser
from openjiuwen.core.foundation.llm.schema.message import BaseMessage
from openjiuwen.core.memory.config.config import AgentMemoryConfig
from openjiuwen.core.common.logging import memory_logger
from openjiuwen.core.common.logging.events import LogEventType
from openjiuwen.core.memory.prompts.prompt_applier import PromptApplier


class VariableResult(BaseModel):
    variable_key: str = Field(default="", description="variable key")
    variable_value: str = Field(default="", description="variable value")


class MemoryAnalyzerResult(BaseModel):
    has_key_information: bool = Field(default=False)
    variables: List[VariableResult] = Field(default=[])
    summary: str = Field(default="")


class MemoryAnalyzer:
    def __init__(self):
        pass

    @staticmethod
    async def analyze(
            messages: List[BaseMessage],
            history_messages: List[BaseMessage],
            base_chat_model: Model,
            memory_config: AgentMemoryConfig,
            summary_max_token: int,
            *,
            forbidden_variables: str = "",
            retries: int = 3
    ) -> MemoryAnalyzerResult | None:
        if len(messages) == 0:
            memory_logger.warning(
                "No messages to analyze",
                event_type=LogEventType.MEMORY_PROCESS,
                metadata={"messages_len": len(messages)}
            )
            return None

        history = ""
        conversation = ""
        for msg in history_messages:
            history += f"{msg.role}: {msg.content}\n"
        for msg in messages:
            conversation += f"{msg.role}: {msg.content}\n"

        variables_description = []
        variables_output_format = []
        for param in memory_config.mem_variables:
            variables_description.append({
                "variable_key": param.name,
                "variable_value": param.description
            })

            variables_output_format.append({
                "variable_key": param.name,
                "variable_value": ""
            })
        variables_description_json = json.dumps(variables_description, ensure_ascii=False)
        variables_output_format_json = json.dumps(variables_output_format, ensure_ascii=False)
        has_variable = (len(memory_config.mem_variables) > 0)
        forbidden_variables = "None" if forbidden_variables == "" else forbidden_variables
        prompt_content = PromptApplier().apply(
            "memory_analysis_prompt",
            {
                "history": history,
                "conversation": conversation,
                "has_variable": has_variable,
                "variables_define_template": variables_description_json,
                "variables_output_template": variables_output_format_json,
                "forbidden_variables": forbidden_variables,
                "max_message_token": summary_max_token,
            },
        )
        model_input = [{"role": "user", "content": prompt_content}]
        parser = JsonOutputParser()
        for attempt in range(retries):
            try:
                response = await base_chat_model.invoke(messages=model_input)
                res = await parser.parse(response.content)
                analyze_result = MemoryAnalyzerResult.model_validate(res)
                if not memory_config.enable_long_term_mem or not memory_config.enable_summary_memory:
                    analyze_result.summary = ""
                return analyze_result
            except json.JSONDecodeError as e:
                if attempt < retries - 1:
                    continue
                memory_logger.error(
                    "Categories model output format error",
                    event_type=LogEventType.MEMORY_PROCESS,
                    exception=str(e)
                )

        return MemoryAnalyzerResult()
