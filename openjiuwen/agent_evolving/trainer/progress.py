# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Training progress and callbacks: Progress records epochs and scores, Callbacks provides train/epoch lifecycle hooks.
"""

from typing import Generator, List
from pydantic import BaseModel, Field

from openjiuwen.core.single_agent import BaseAgent
from openjiuwen.agent_evolving.dataset import EvaluatedCase
from openjiuwen.agent_evolving.constant import TuneConstant


class Progress(BaseModel):
    """
    Self-evolving training progress: current epoch, max epoch, best score,
    current epoch score, etc., with run_epoch/run_batch iteration.
    """

    start_epoch: int = Field(default=0, ge=0)
    current_epoch: int = Field(default=0, ge=0)
    max_epoch: int = Field(default=TuneConstant.default_iteration_num, ge=0)
    current_batch_iter: int = Field(default=0, ge=0)
    max_batch_iter: int = Field(default=1, ge=0)
    best_score: float = Field(default=0.0, ge=0.0, le=1.0)
    best_batch_score: float = Field(default=0.0, ge=0.0, le=1.0)
    current_epoch_score: float = Field(default=0.0, ge=0.0, le=1.0)

    def run_epoch(self) -> Generator[int, None, None]:
        """Iterate 1..max_epoch, update current_epoch each round and yield epoch number."""
        start = int(self.start_epoch) + 1
        for epoch in range(start, self.max_epoch + 1):
            self.current_epoch = epoch
            yield epoch
        if self.current_epoch < self.max_epoch:
            self.current_epoch = self.max_epoch

    def run_batch(self) -> Generator[int, None, None]:
        """
        Iterate batch steps, update current_batch_iter each step and yield
        step number; reset best_batch_score to 0 at start.
        """
        self.best_batch_score = 0
        for batch_iter in range(self.max_batch_iter):
            self.current_batch_iter = batch_iter
            yield batch_iter


class Callbacks:
    """Training lifecycle hooks; subclass can override to integrate logging, early stopping, metric reporting, etc."""

    def on_train_begin(self, agent: BaseAgent, progress: Progress, eval_info: List[EvaluatedCase]) -> None:
        """Training begin (validation baseline evaluation completed)."""
        pass

    def on_train_end(self, agent: BaseAgent, progress: Progress, eval_info: List[EvaluatedCase]) -> None:
        """Training end."""
        pass

    def on_train_epoch_begin(self, agent: BaseAgent, progress: Progress) -> None:
        """Single epoch training begins."""
        pass

    def on_train_epoch_end(self, agent: BaseAgent, progress: Progress, eval_info: List[EvaluatedCase]) -> None:
        """Single epoch training ends (best_score updated / parameters written back)."""
        pass
