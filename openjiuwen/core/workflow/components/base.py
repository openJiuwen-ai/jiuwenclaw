# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ComponentAbility(Enum):
    """
    Capabilities that a component can support.

    Each ability represents a different I/O pattern for component execution.
    """
    INVOKE = ("invoke", "batch in, batch out")  # Synchronous batch processing
    STREAM = ("stream", "batch in, stream out")  # Streaming output from batch input
    COLLECT = ("collect", "stream in, batch out")  # Batch output from streaming input
    TRANSFORM = ("transform", "stream in, stream out")  # Streaming to streaming

    def __init__(self, name: str, desc: str):
        """
        Initialize a component ability.

        Args:
            name: Internal name of the ability
            desc: Human-readable description of the I/O pattern
        """
        self._name = name
        self._desc = desc

    @property
    def name(self) -> str:
        """Get the internal name of the ability."""
        return self._name

    @property
    def desc(self) -> str:
        """Get the description of the ability."""
        return self._desc


@dataclass
class WorkflowComponentMetadata:
    node_id: str
    node_type: str
    node_name: str


@dataclass
class ComponentConfig:
    metadata: Optional[WorkflowComponentMetadata] = field(default=None)


@dataclass
class ComponentState:
    comp_id: str
    status: Enum
