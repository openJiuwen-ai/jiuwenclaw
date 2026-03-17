# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import List, Tuple
import random
import copy

from openjiuwen.core.common.logging import logger
from openjiuwen.dev_tools.tune.base import Case


CASE_ID_PREFIX: str = "case_"


class CaseLoader:
    def __init__(self,
                 cases: List[Case]
                 ):
        self._cases = cases
        self._assign_case_id()

    def __len__(self):
        return len(self._cases)

    def __iter__(self):
        for case in self._cases:
            yield case

    def shuffle(self, random_seed: int = 0):
        random.seed(random_seed)
        random.shuffle(self._cases)
        self._assign_case_id()

    def size(self) -> int:
        return len(self._cases)

    def get_cases(self) -> List[Case]:
        return self._cases

    def split(self, ratio: float = 0.5) -> Tuple["CaseLoader", "CaseLoader"]:
        if ratio < 0.0 or ratio > 1.0:
            logger.error(f"ratio must be between 0.0 and 1.0, got {ratio}, using default 0.5")
            ratio = 0.5
        shuffled_cases = copy.deepcopy(self._cases)
        random.shuffle(shuffled_cases)
        cut = int(len(self._cases) * ratio)
        return CaseLoader(shuffled_cases[:cut]), CaseLoader(shuffled_cases[cut:])

    def _assign_case_id(self):
        for i, case in enumerate(self._cases):
            case.case_id = f"{CASE_ID_PREFIX}{i}"