# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Self-evolving training and evaluation framework.

Includes:
- Trainer, Progress, Callbacks: Training orchestration
- BaseEvaluator, DefaultEvaluator, MetricEvaluator: Evaluation interfaces
- BaseOptimizer, TextualParameter, InstructionOptimizer: Optimization
- Case, EvaluatedCase, CaseLoader: Dataset handling
- Trajectory, TrajectoryStep, ExecutionSpec: Execution trace types
- SingleDimUpdater, MultiDimUpdater: Update generation
- Checkpointing: State persistence
"""

# constants
from openjiuwen.agent_evolving.constant import TuneConstant

# checkpointing
from openjiuwen.agent_evolving.checkpointing import (
    EvolveCheckpoint,
    FileCheckpointStore,
    DefaultCheckpointManager,
    CheckpointManager,
)

# dataset
from openjiuwen.agent_evolving.dataset import Case, EvaluatedCase, CaseLoader

# dataset
from openjiuwen.agent_evolving.evaluator import (
    BaseEvaluator,
    DefaultEvaluator,
    MetricEvaluator,
    Metric,
    ExactMatchMetric,
    LLMAsJudgeMetric,
)

# optimizer
from openjiuwen.agent_evolving.optimizer import (
    BaseOptimizer,
    TextualParameter,
    InstructionOptimizer,
)

# trainer
from openjiuwen.agent_evolving.trainer import Trainer, Progress, Callbacks

# trajectory
from openjiuwen.agent_evolving.trajectory import (
    Trajectory,
    TrajectoryStep,
    UpdateKey,
    Updates,
    TracerTrajectoryExtractor,
)

# updater
from openjiuwen.agent_evolving.updater import Updater, SingleDimUpdater, MultiDimUpdater

_CONSTANTS = [
    "TuneConstant",
]

_CHECKPOINTING = [
    "EvolveCheckpoint",
    "FileCheckpointStore",
    "DefaultCheckpointManager",
    "CheckpointManager",
]

_DATASET = [
    "Case",
    "EvaluatedCase",
    "CaseLoader",
]

_EVALUATOR = [
    "BaseEvaluator",
    "DefaultEvaluator",
    "MetricEvaluator",
    "Metric",
    "ExactMatchMetric",
    "LLMAsJudgeMetric",
]

_OPTIMIZER = [
    "BaseOptimizer",
    "TextualParameter",
    "InstructionOptimizer",
]

_TRAINER = [
    "Trainer",
    "Progress",
    "Callbacks",
]

_TRAJECTORY = [
    "Trajectory",
    "TrajectoryStep",
    "UpdateKey",
    "Updates",
    "TracerTrajectoryExtractor",
]

_UPDATER = [
    "Updater",
    "SingleDimUpdater",
    "MultiDimUpdater",
]

__all__ = (
    _CONSTANTS
    + _CHECKPOINTING
    + _DATASET
    + _EVALUATOR
    + _OPTIMIZER
    + _TRAINER
    + _TRAJECTORY
    + _UPDATER
)
