# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from dataclasses import dataclass
from typing import List, Tuple
from openjiuwen.core.foundation.llm import BaseMessage
from openjiuwen.core.foundation.llm import Model


@dataclass
class ExtractMemoryParams:
    user_id: str
    scope_id: str
    messages: list[BaseMessage]
    history_messages: list[BaseMessage]
    base_chat_model: Tuple[str, Model]
