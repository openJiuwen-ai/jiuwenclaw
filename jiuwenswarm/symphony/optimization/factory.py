"""Default-implementation factory for the prompt optimizer.

Mirrors :class:`jiuwenswarm.symphony.build.ScoreBuildRuntimeFactory`: one place that
constructs the default Policy / Environment / RewardModel / DriftJudge / Memory /
compressor / convergence detector from :class:`OptimizationConfig`, so callers can
override exactly one collaborator and inherit sensible defaults for the rest.
"""

from __future__ import annotations

import logging

from jiuwenswarm.symphony.optimization.config import OptimizationConfig
from jiuwenswarm.symphony.optimization.convergence import ConvergenceDetector
from jiuwenswarm.symphony.optimization.drift.base import DriftJudge, NullDriftJudge
from jiuwenswarm.symphony.optimization.drift.llm_judge import LLMDriftJudge
from jiuwenswarm.symphony.optimization.environment.base import PromptEnvironment
from jiuwenswarm.symphony.optimization.environment.llm_env import LLMEnvironment
from jiuwenswarm.symphony.optimization.llm_support import build_client
from jiuwenswarm.symphony.optimization.memory.base import (
    JsonlPromptMemory,
    NullPromptMemory,
    PromptMemory,
)
from jiuwenswarm.symphony.optimization.models import TaskSpec
from jiuwenswarm.symphony.optimization.policy.base import PromptPolicy
from jiuwenswarm.symphony.optimization.policy.history import HistoryCompressor
from jiuwenswarm.symphony.optimization.policy.llm_policy import LLMPromptPolicy
from jiuwenswarm.symphony.optimization.reward.base import RewardModel
from jiuwenswarm.symphony.optimization.reward.components import (
    CompletenessReward,
    CorrectnessReward,
    CorrectnessRewardWithExpected,
    CostReward,
    LatencyReward,
    TokenUsageReward,
)
from jiuwenswarm.symphony.optimization.reward.composite import CompositeReward

LOGGER = logging.getLogger(__name__)


class OptimizerRuntimeFactory:
    """Build default optimizer collaborators from an :class:`OptimizationConfig`."""

    def __init__(self, config: OptimizationConfig) -> None:
        self._config = config
        self._policy_client = None
        self._environment_client = None
        self._judge_client = None

    # -- lazily-built shared clients -----------------------------------------

    def policy_client(self):
        if self._policy_client is None:
            self._policy_client = build_client(
                self._config.models.policy_model,
                temperature=self._config.policy_temperature,
            )
        return self._policy_client

    def environment_client(self):
        if self._environment_client is None:
            self._environment_client = build_client(self._config.models.environment_model)
        return self._environment_client

    def judge_client(self):
        if self._judge_client is None:
            self._judge_client = build_client(self._config.models.judge_model)
        return self._judge_client

    # -- collaborators --------------------------------------------------------

    def policy(self) -> PromptPolicy:
        return LLMPromptPolicy(self.policy_client())

    def environment(self) -> PromptEnvironment:
        return LLMEnvironment(
            self.environment_client(),
            parallel=self._config.parallel_execution,
            max_concurrency=self._config.candidate_prompts,
        )

    def reward_model(self, task: TaskSpec) -> RewardModel:
        weights = self._config.reward_weights
        judge = self.judge_client()
        max_concurrency = max(1, self._config.candidate_prompts)
        has_expected = any(c.expected for c in task.cases)
        correctness = (
            CorrectnessRewardWithExpected(judge, task, max_concurrency=max_concurrency)
            if has_expected
            else CorrectnessReward(judge, max_concurrency=max_concurrency)
        )
        components = [
            correctness,
            CompletenessReward(),
            LatencyReward(),
            TokenUsageReward(),
            CostReward(),
        ]
        return CompositeReward(
            components,
            weights,
            min_correctness=self._config.min_correctness,
            drift_penalty=self._config.drift_penalty,
            normalize=False,
        )

    def drift_judge(self) -> DriftJudge:
        if self._config.drift_penalty <= 0:
            return NullDriftJudge()
        return LLMDriftJudge(self.judge_client())

    def history_compressor(self) -> HistoryCompressor:
        return HistoryCompressor(self.policy_client())

    def convergence(self) -> ConvergenceDetector:
        return ConvergenceDetector(
            threshold=self._config.convergence_threshold,
            window=self._config.convergence_window,
        )

    def memory(self) -> PromptMemory:
        if not self._config.memory_enabled:
            return NullPromptMemory()
        directory = self._config.resolved_memory_dir
        embedding = self._config.embedding
        if embedding.base_url or embedding.model_name:
            backend = self._try_experience_backend(directory)
            if backend is not None:
                return backend
        return JsonlPromptMemory(directory)

    def _try_experience_backend(self, directory) -> PromptMemory | None:
        try:
            from jiuwenswarm.symphony.experience.bank import ExperienceBank
            from jiuwenswarm.symphony.experience.embed import EmbeddingClient
            from jiuwenswarm.symphony.optimization.memory.experience_backend import (
                ExperienceBankPromptMemory,
            )

            embedding = self._config.embedding
            embedder = EmbeddingClient(
                base_url=embedding.base_url or None,
                api_key=embedding.api_key,
                model=embedding.model,
                model_name=embedding.model_name,
                dimension=embedding.dimension,
            )
            bank = ExperienceBank(directory / "bank", embedder)
            return ExperienceBankPromptMemory(bank, directory)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning(
                "OptimizerRuntimeFactory: FAISS prompt memory unavailable, "
                "falling back to JSONL memory: %s",
                exc,
            )
            return None


__all__ = ["OptimizerRuntimeFactory"]
