# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""JiuwenClaw Skills Online Self-Evolution Module.

Provides SkillCallOperator, SkillOptimizer, SkillEvolutionManager, SignalDetector and other components,
implementing the complete pipeline from conversation signals to Skill content evolution.

Does not depend on openJiuwen core's agent_evolving framework, runs independently.

Design aligns with openJiuwen v2's Operator/Optimizer interface,
but implemented independently within JiuwenClaw_v2 without modifying core code.
"""
from jiuwenclaw.evolution.schema import (
    EvolutionChange,
    EvolutionEntry,
    EvolutionFile,
    EvolutionSignal,
)
from jiuwenclaw.evolution.signal_detector import SignalDetector
from jiuwenclaw.evolution.manager import SkillEvolutionManager
from jiuwenclaw.evolution.skill_call_operator import SkillCallOperator
from jiuwenclaw.evolution.skill_optimizer import SkillOptimizer

__all__ = [
    "EvolutionChange",
    "EvolutionEntry",
    "EvolutionFile",
    "EvolutionSignal",
    "SignalDetector",
    "SkillEvolutionManager",
    "SkillCallOperator",
    "SkillOptimizer",
]
