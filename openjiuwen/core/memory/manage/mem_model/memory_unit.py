# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class MemoryType(Enum):
    USER_PROFILE = "user_profile"
    SEMANTIC_MEMORY = "semantic_memory"
    EPISODIC_MEMORY = "episodic_memory"
    VARIABLE = "variable"
    SUMMARY = "summary"
    UNKNOWN = "unknown"


class SupportMemoryType(Enum):
    USER_PROFILE = "user_profile"
    SUMMARY = "summary"


@dataclass
class BaseMemoryUnit:
    """a single memory data item"""
    mem_type: MemoryType
    mem_id: str


@dataclass
class FragmentMemoryUnit(BaseMemoryUnit):
    content: str
    message_mem_id: Optional[str] = None  # Corresponding Message ID
    timestamp: str = ""


@dataclass
class VariableUnit(BaseMemoryUnit):
    mem_type: MemoryType = field(default=MemoryType.VARIABLE, init=False)
    mem_id: str = field(default="", init=False)
    variable_name: str
    variable_mem: str


@dataclass
class SummaryUnit(BaseMemoryUnit):
    mem_type: MemoryType = field(default=MemoryType.SUMMARY, init=False)
    summary: str
    message_mem_id: Optional[str] = None  # Corresponding Message ID
    timestamp: str = ""
