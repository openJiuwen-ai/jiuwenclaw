# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Abstract base for Decision policies."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from jiuwenswarm.evolve.models import DecisionResult, Proposal

logger = logging.getLogger(__name__)


class DecisionPolicy(ABC):
    """Evaluate a :class:`Proposal` and return a :class:`DecisionResult`.

    Subclasses implement the evaluation logic (rules, eval heuristics,
    LLM judging, etc.).  Policies NEVER mutate the Proposal — they only
    produce a DecisionResult.
    """

    def __init__(self, name: str, version: str = "1.0") -> None:
        self.name = name
        self.version = version

    @abstractmethod
    async def evaluate(self, proposal: Proposal) -> DecisionResult:
        """Evaluate *proposal* and return a scored decision.

        Args:
            proposal: The Proposal to evaluate.

        Returns:
            A DecisionResult with score, suggestion, and blocking flag.
        """
        ...
