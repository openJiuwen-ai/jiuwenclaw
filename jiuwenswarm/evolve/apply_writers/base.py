# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Abstract base for Apply writers."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from jiuwenswarm.evolve.models import ApplyRecord, Proposal

logger = logging.getLogger(__name__)


class ApplyWriter(ABC):
    """Write an accepted Proposal to its target store.

    Each subclass handles a specific target_type (skill, memory, training)
    and writes to the appropriate storage backend.
    """

    def __init__(self, name: str) -> None:
        self.name = name

    @abstractmethod
    async def apply(self, proposal: Proposal) -> ApplyRecord:
        """Write *proposal* to the target store.

        Only called for Proposals whose state is ``active``.

        Args:
            proposal: The accepted Proposal to write.

        Returns:
            An ApplyRecord documenting the outcome.
        """
        ...
