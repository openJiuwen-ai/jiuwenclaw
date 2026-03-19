# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from pydantic import Field
from openjiuwen.core.single_agent.agents.react_agent import ReActAgentConfig


class ReActAgentCompConfig(ReActAgentConfig):
    """Configuration for ReAct agent workflow component"""
    pass  # May add workflow-specific configurations later