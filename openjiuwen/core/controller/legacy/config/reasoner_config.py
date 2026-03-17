# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved

from dataclasses import dataclass, field
from typing import Dict, Any, List
from pydantic import BaseModel, Field

from openjiuwen.core.foundation.llm import SystemMessage, UserMessage
from openjiuwen.core.foundation.prompt import PromptTemplate

DEFAULT_SYSTEM_PROMPT = """你是一个意图分类助手，擅长判断用户的输入属于哪个分类。
当用户输入没有明确意图或者你无法判断用户输入意图时请选择 {{default_class}}。
以下是给定的意图分类列表：
{{category_list}}
{{example_content}}
请根据上述要求判断用户输入意图分类，输出要求如下：
直接以JSON格式输出分类ID，不进行任何解释。JSON格式如下：
 {"result": int}"""

DEFAULT_USER_PROMPT = """用户与助手的对话历史：
{{chat_history}}
当前输入：
{{input}}"""


def get_default_template():
    return PromptTemplate(
                content=[
                    SystemMessage(content=DEFAULT_SYSTEM_PROMPT),
                    UserMessage(content=DEFAULT_USER_PROMPT),
                ]
            )

class IntentDetectionConfig(BaseModel):
    """config of Intent Detection Component"""
    category_info: str = Field(default='')
    category_list: List[str] = Field(default_factory=list)
    intent_detection_template: PromptTemplate = field(default_factory=get_default_template)
    user_prompt: str = Field(default=DEFAULT_USER_PROMPT)
    chat_history_max_turn: int = Field(default=100)
    default_class: str = Field(default='分类0')
    enable_history: bool = Field(default=False)
    enable_input: bool = Field(default=True)
    example_content: List[str] = Field(default_factory=list)


class PlannerConfig:
    """config of Planner Component"""
    pass


class ProactiveIdentifierConfig:
    """config of Proactive Identifier Component"""
    pass


class ReflectorConfig:
    """config of Reflector Component"""
    pass


@dataclass
class ReasonerConfig:
    """config of Resoner Componet - using sub module configuration"""
    intent_detection: IntentDetectionConfig = field(default_factory=IntentDetectionConfig)
    planner: PlannerConfig = field(default_factory=PlannerConfig)
    proactive_identifier: ProactiveIdentifierConfig = field(default_factory=ProactiveIdentifierConfig)
    reflector: ReflectorConfig = field(default_factory=ReflectorConfig)

    # Global config
    enable_metrics: bool = True
    enable_logging: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)
