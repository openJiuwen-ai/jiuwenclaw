# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Triple Memory Data Model
"""


class TripleMemory:
    def __init__(self):
        self.included_triples = set()
        self.memory = []

    def __len__(self):
        return len(self.memory)

    @property
    def triples_str(self) -> str:
        """
        Return formatted string of triples in memory.
        """
        formatted_triples = []
        for triple in self.memory:
            formatted_triples.append(f"({' '.join(triple)})")
        return "\n".join(formatted_triples)

    @staticmethod
    def _tuple2str(new_triple: tuple[str, ...]) -> str:
        """Convert a triple in tuple format to str"""
        return " ".join([item.lower() for item in new_triple])

    def extend_memory(self, new_triple: tuple[str, ...]) -> None:
        """
        Updates the memory given a single `new_triple` as input.
        """
        new_triple_str = self._tuple2str(new_triple)
        if new_triple_str not in self.included_triples:
            self.included_triples.add(new_triple_str)
            self.memory.append(new_triple)

    def batch_extend_memory(self, new_triples: list[tuple[str, ...]]) -> None:
        """
        Updates the memory given a list of `new_triples` as input.
        """
        for tmp_triple in new_triples:
            self.extend_memory(new_triple=tmp_triple)
