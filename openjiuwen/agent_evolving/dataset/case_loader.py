# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Case container supporting iteration, shuffle, and split operations.
"""

from typing import List, Tuple, Iterator
import random

from openjiuwen.agent_evolving.dataset.case import Case


def shuffle_cases(cases: List[Case], seed: int = 0) -> List[Case]:
    """Shuffle Case list with optional seed.

    Args:
        cases: Cases to shuffle
        seed: Random seed for reproducibility

    Returns:
        New shuffled list (original unchanged)
    """
    rnd = random.Random(seed)
    shuffled = cases.copy()
    rnd.shuffle(shuffled)
    return shuffled


def split_cases(cases: List[Case], ratio: float) -> Tuple[List[Case], List[Case]]:
    """Split Case list by ratio.

    Args:
        cases: Cases to split
        ratio: Split ratio in [0.0, 1.0]

    Returns:
        Tuple of (first_half, second_half)

    Raises:
        ValueError: if ratio is not in [0.0, 1.0]
    """
    if not 0.0 <= ratio <= 1.0:
        raise ValueError(f"ratio must be in [0.0, 1.0], got {ratio}")
    cut = int(len(cases) * ratio)
    return cases[:cut], cases[cut:]


class CaseLoader:
    """Container for Case list with iteration and split support.

    Attributes:
        cases: Internal case list
    """

    def __init__(self, cases: List[Case]):
        """Initialize with case list.

        Args:
            cases: List of Cases to wrap
        """
        self._cases = cases

    def __len__(self) -> int:
        """Return number of cases."""
        return len(self._cases)

    def __iter__(self) -> Iterator[Case]:
        """Iterate over cases."""
        return iter(self._cases)

    def get_cases(self) -> List[Case]:
        """Get copy of cases list.

        Returns:
            Copy of internal case list
        """
        return self._cases.copy()

    def split(self, ratio: float, seed: int = 0) -> Tuple["CaseLoader", "CaseLoader"]:
        """Split samples into two parts by ratio.

        Args:
            ratio: Split ratio in [0.0, 1.0]
            seed: Random seed for reproducible shuffle

        Returns:
            Tuple of (first_half, second_half) CaseLoaders

        Raises:
            ValueError: if ratio is not in [0.0, 1.0] range
        """
        if not 0.0 <= ratio <= 1.0:
            raise ValueError(f"ratio must be in [0.0, 1.0], got {ratio}")

        shuffled = shuffle_cases(self._cases, seed)
        cut = int(len(shuffled) * ratio)
        return CaseLoader(shuffled[:cut]), CaseLoader(shuffled[cut:])
