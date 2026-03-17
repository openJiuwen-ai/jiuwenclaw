# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Trajectory-related type definitions: Trajectory, TrajectoryStep, ExecutionSpec.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Literal


# --- Common update types ---
UpdateKey = Tuple[str, str]  # (operator_id, target)
Updates = Dict[UpdateKey, Any]

# --- Step types ---
StepKind = Literal["llm", "tool", "memory", "workflow", "agent"]


@dataclass(frozen=True)
class ExecutionSpec:
    """Single execution configuration."""
    case_id: str
    execution_id: str
    seed: Optional[int] = None
    tags: Optional[Dict[str, Any]] = None


@dataclass
class TrajectoryStep:
    """Single step in execution."""
    kind: StepKind
    operator_id: Optional[str]
    agent_id: Optional[str]
    role: Optional[str]
    node_id: Optional[str]
    inputs: Any
    outputs: Any
    error: Optional[Dict[str, Any]]
    start_time_ms: Optional[int]
    end_time_ms: Optional[int]
    meta: Dict[str, Any]


@dataclass
class Trajectory:
    """Complete execution trajectory."""
    case_id: str
    execution_id: str
    trace_id: Optional[str]
    steps: List[TrajectoryStep]
    edges: Optional[List[Tuple[int, int]]] = None
