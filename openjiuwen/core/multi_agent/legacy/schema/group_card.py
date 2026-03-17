# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import List, Dict

from pydantic import Field

from openjiuwen.core.common import BaseCard
from openjiuwen.core.single_agent import AgentCard


class GroupCard(BaseCard):
    agent_card: List[AgentCard] = Field(default_factory=list)
    topic: str = Field(default='')


class EventDrivenGroupCard(GroupCard):
    subscriptions: Dict[str, List[str]] = {}  # {"agent_id": [topic1, topic2, ...]}
