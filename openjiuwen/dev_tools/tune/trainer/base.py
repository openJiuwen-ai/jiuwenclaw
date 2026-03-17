# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Generator, List
from pydantic import BaseModel, Field

from openjiuwen.core.single_agent.legacy import LegacyBaseAgent as BaseAgent
from openjiuwen.dev_tools.tune.base import TuneConstant, EvaluatedCase


class Progress(BaseModel):
    current_epoch: int = Field(default=0, ge=0)
    max_epoch: int = Field(default=TuneConstant.DEFAULT_ITERATION_NUM, ge=0)
    current_batch_iter: int = Field(default=0, ge=0)
    max_batch_iter: int = Field(default=1, ge=0)
    best_score: float = Field(default=0.0, ge=0.0, le=1.0)
    best_batch_score: float = Field(default=0.0, ge=0.0, le=1.0)
    current_epoch_score: float = Field(default=0.0, ge=0.0, le=1.0)

    def run_epoch(self) -> Generator:
        for epoch in range(1, self.max_epoch + 1):
            self.current_epoch = epoch
            yield epoch

    def run_batch(self) -> Generator:
        self.best_batch_score = 0
        for batch_iter in range(self.max_batch_iter):
            self.current_batch_iter = batch_iter
            yield batch_iter


class Callbacks:
    def on_train_begin(self, agent: BaseAgent, progress: Progress, eval_info: List[EvaluatedCase]) -> None:
        pass

    def on_train_end(self, agent: BaseAgent, progress: Progress, eval_info: List[EvaluatedCase]) -> None:
        pass

    def on_train_epoch_begin(self, agent: BaseAgent, progress: Progress) -> None:
        pass

    def on_train_epoch_end(self, agent: BaseAgent, progress: Progress, eval_info: List[EvaluatedCase]) -> None:
        pass