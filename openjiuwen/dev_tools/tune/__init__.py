# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Prompt builder module"""

from openjiuwen.dev_tools.tune.base import (
    Case,
    EvaluatedCase
)
from openjiuwen.dev_tools.tune.dataset.case_loader import CaseLoader
from openjiuwen.dev_tools.tune.optimizer.instruction_optimizer import InstructionOptimizer
from openjiuwen.dev_tools.tune.optimizer.example_optimizer import ExampleOptimizer
from openjiuwen.dev_tools.tune.optimizer.joint_optimizer import JointOptimizer
from openjiuwen.dev_tools.tune.chat_agent.chat_agent import (
    ChatAgent,
    ChatAgentConfig,
    create_chat_agent_config,
    create_chat_agent
)
from openjiuwen.dev_tools.tune.evaluator.evaluator import (
    DefaultEvaluator
)
from openjiuwen.dev_tools.tune.trainer.trainer import Trainer

_CASE_LOADER_CLASSES = [
    "Case",
    "EvaluatedCase",
    "CaseLoader"
]


_OPTIMIZER_CLASSES = [
    "InstructionOptimizer",
    "ExampleOptimizer",
    "JointOptimizer"
]


_EVALUATOR_CLASSES = [
    "DefaultEvaluator"
]


_TRAINER_CLASSES = [
    "Trainer"
]


_CHAT_AGENT_CLASSES_AND_METHODS = [
    "ChatAgent",
    "ChatAgentConfig",
    "create_chat_agent_config",
    "create_chat_agent"
]


__all__ = (
    _CASE_LOADER_CLASSES +
    _OPTIMIZER_CLASSES +
    _CHAT_AGENT_CLASSES_AND_METHODS +
    _EVALUATOR_CLASSES +
    _TRAINER_CLASSES
)
