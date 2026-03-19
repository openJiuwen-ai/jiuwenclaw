# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Simple in-memory registry for reward functions.
"""

from typing import Any, Callable, Dict, List

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger

RewardCallable = Callable[..., Any]


class RewardRegistry:
    """Global registry mapping reward names to callables."""

    def __init__(self) -> None:
        self._registry: Dict[str, RewardCallable] = {}

    def register(self, name: str, fn: RewardCallable) -> None:
        """Register a reward function by name."""
        if not name:
            raise build_error(
                StatusCode.AGENT_RL_REWARD_NAME_INVALID,
                error_msg="reward name must be non-empty",
            )
        logger.info("register reward function: %s", name)
        self._registry[name] = fn

    def get(self, name: str) -> RewardCallable:
        """Look up a reward function by name. Raises if not found."""
        if name not in self._registry:
            raise build_error(
                StatusCode.AGENT_RL_REWARD_NOT_FOUND,
                name=name,
            )
        return self._registry[name]

    def list(self) -> List[str]:
        """Return the list of all registered reward names."""
        return list(self._registry.keys())


# module-level default registry
reward_registry = RewardRegistry()


def register_reward(name: str) -> Callable[[RewardCallable], RewardCallable]:
    """Decorator to register a reward function by name."""

    def decorator(fn: RewardCallable) -> RewardCallable:
        reward_registry.register(name, fn)
        return fn

    return decorator
