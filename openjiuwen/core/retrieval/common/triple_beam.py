# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Triple Beam Data Model
"""

from typing import Iterator, List

from openjiuwen.core.retrieval.common.retrieval_result import RetrievalResult


class TripleBeam:
    def __init__(self, nodes: List[RetrievalResult], score: float) -> None:
        self._beam = nodes
        self._exist_triples = {x.text for x in self._beam}
        self._score = score

    def __getitem__(self, idx) -> RetrievalResult:
        return self._beam[idx]

    def __len__(self) -> int:
        return len(self._beam)

    def __contains__(self, triple: RetrievalResult) -> bool:
        return triple.text in self._exist_triples

    def __iter__(self) -> Iterator[RetrievalResult]:
        return iter(self._beam)

    @property
    def triples(self) -> List[RetrievalResult]:
        return self._beam

    @property
    def score(self) -> float:
        return self._score
