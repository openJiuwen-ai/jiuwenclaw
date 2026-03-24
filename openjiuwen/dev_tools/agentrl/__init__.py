# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
RL training extension for openjiuwen.

This package provides:
- Data structures & config schemas for RL training
- Verl-based training executor (VerlTrainingExecutor extends RayPPOTrainer)
- MainTrainer for coordinating the training loop
- RLTrainerDaemon for multi-round rollout orchestration
- ParallelRuntimeExecutor for concurrent rollout generation
- A high-level `RLOptimizer` user entrypoint

"""

from openjiuwen.core.common.logging import logger

# ---------------------------------------------------------------------------
# Compatibility patch: LazyLogger.__getattr__ uses ``self._logger`` which
# triggers __getattr__ again when _logger is not yet in __dict__ (e.g. after
# unpickling in Ray workers), causing infinite recursion.  We replace
# __getattr__ with a version that uses object.__getattribute__ to bypass the
# descriptor protocol.
# ---------------------------------------------------------------------------


def _patch_lazy_logger():
    """Make LazyLogger.__getattr__ safe against recursion during unpickle."""
    try:
        from openjiuwen.core.common.logging import LazyLogger

        # Avoid double-patching
        if getattr(LazyLogger.__getattr__, "agent_rl_patched", False):
            return

        def _safe_getattr(self, name):
            try:
                _logger = object.__getattribute__(self, "_logger")
            except AttributeError:
                _logger = None

            if _logger is None:
                from openjiuwen.core.common.logging import _ensure_initialized
                _ensure_initialized()
                getter = object.__getattribute__(self, "_getter_func")
                _logger = getter()
                object.__setattr__(self, "_logger", _logger)
            return getattr(_logger, name)

        _safe_getattr.agent_rl_patched = True
        LazyLogger.__getattr__ = _safe_getattr
    except Exception as e:
        logger.info("agentrl: skip lazy_logger patch (core not available): %s", e)


_patch_lazy_logger()

# ---------------------------------------------------------------------------
# Lazy import for RLOptimizer to avoid pulling in heavy dependencies (ray,
# verl) when only sub-modules like rl_trainer_adaptor.base or
# runtime_and_sampler_adaptor are needed (e.g. in debug/test scripts).
# ---------------------------------------------------------------------------


def __getattr__(name):
    if name == "RLOptimizer":
        from openjiuwen.dev_tools.agentrl.optimizer.rl_optimizer import RLOptimizer
        return RLOptimizer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# Data models
from openjiuwen.dev_tools.agentrl.coordinator.schemas import (
    Rollout,
    RolloutMessage,
    RLTask,
    RolloutWithReward,
)
# Config
from openjiuwen.dev_tools.agentrl.config.schemas import RLConfig
# Reward
from openjiuwen.dev_tools.agentrl.reward.registry import RewardRegistry

__all__ = [
    "RLConfig",
    "RLOptimizer",
    "RewardRegistry",
    "RLTask",
    "Rollout",
    "RolloutMessage",
    "RolloutWithReward",
]
