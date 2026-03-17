# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from enum import Enum


class InvokeType(Enum):
    """
    Agent Invoke Type
    """
    PROMPT = "prompt"
    LLM = "llm"
    PLUGIN = "plugin"
    WORKFLOW = "workflow"
    CHAIN = "chain"
    RETRIEVER = "retriever"
    EVALUATOR = "evaluator"


class NodeStatus(Enum):
    """
    Workflow Node Status For Message
    """
    START = "start"
    FINISH = "finish"
    RUNNING = "running"
    INTERRUPTED = "interrupted"
    ERROR = "error"