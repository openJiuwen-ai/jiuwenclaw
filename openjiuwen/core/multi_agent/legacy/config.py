# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved

"""AgentGroup Configuration"""

from dataclasses import dataclass, field
from typing import Optional, Any, Dict


@dataclass
class AgentGroupConfig:
    """AgentGroup Configuration"""
    group_id: str
    max_agents: int = 10
    max_concurrent_messages: int = 100
    message_timeout: float = 30.0

