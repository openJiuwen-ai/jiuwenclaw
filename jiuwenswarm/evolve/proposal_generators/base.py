# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Abstract base for Proposal generators."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from jiuwenswarm.evolve.models import Proposal, TraceBatch

logger = logging.getLogger(__name__)


class ProposalGenerator(ABC):
    """Generate Proposals by analysing traces in a :class:`TraceBatch`.

    Subclasses implement the analysis logic (LLM-based, rule-based, etc.)
    and return a list of :class:`Proposal` objects.
    """

    def __init__(self, name: str, trace_reader: object | None = None) -> None:
        self.name = name
        self._trace_reader = trace_reader

    @abstractmethod
    async def generate(self, batch: TraceBatch) -> list[Proposal]:
        """Analyse *batch* and return zero or more Proposals.

        Args:
            batch: The trace batch to analyse (contains trace_ids, not span data).

        Returns:
            List of Proposal objects (may be empty if nothing was found).
        """
        ...
