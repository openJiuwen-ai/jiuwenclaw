# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Built-in rollout processors (classifier, validator, sampler)
and the ProcessorsRegistry that auto-discovers them.
"""

import copy
import importlib
import inspect
from typing import Callable, Dict, List, Optional, Tuple

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.dev_tools.agentrl.coordinator.schemas import RolloutWithReward


# ---------------------------------------------------------------------------
# Processor classes
# ---------------------------------------------------------------------------


class RolloutClassifier:
    """
    Provides rollout classification utilities that separate positive and negative
    rollout samples based on their reward values.
    """

    @staticmethod
    def default_classify_rollouts(
        mdp_list: List[RolloutWithReward],
    ) -> Tuple[List[RolloutWithReward], List[RolloutWithReward]]:
        """
        Classify rollout steps into positive and negative lists.
        """
        pos_rollouts: List[RolloutWithReward] = []
        neg_rollouts: List[RolloutWithReward] = []
        for mdp in mdp_list:
            if mdp.reward > 0:
                pos_rollouts.append(mdp)
            else:
                neg_rollouts.append(mdp)
        return pos_rollouts, neg_rollouts


class RolloutValidator:
    """
    Validation utilities for determining when rollout collection
    for a prompt should stop.
    """

    @staticmethod
    def default_validate_stop(
        pos_rollout_list: List[RolloutWithReward],
        neg_rollout_list: List[RolloutWithReward],
    ) -> bool:
        """
        Stop when at least two positive samples exist and
        one of them achieves reward >= 1.0.
        """
        if len(neg_rollout_list) > len(pos_rollout_list):
            return False
        if len(pos_rollout_list) < 2:
            return False
        for rollout in pos_rollout_list:
            if rollout.reward == 1.0:
                return True
        return False

    @staticmethod
    def validate_stop_balanced(
        pos_rollout_list: List[RolloutWithReward],
        neg_rollout_list: List[RolloutWithReward],
        final_keep_per_prompt: int = 8,
    ) -> bool:
        """
        Balanced stopping rule: stop only when both positive and negative
        counts meet their target amounts.
        """
        target_pos = final_keep_per_prompt // 2
        target_neg = final_keep_per_prompt - target_pos
        if len(pos_rollout_list) < target_pos:
            return False
        if len(neg_rollout_list) < target_neg:
            return False
        return True


class RolloutSampling:
    """
    Sampling strategies used to select subsets of rollout samples for training.
    """

    @staticmethod
    def default_sampling(
        pos_rollout_dict: Dict[str, List[RolloutWithReward]],
        neg_rollout_dict: Dict[str, List[RolloutWithReward]],
    ) -> Tuple[
        Dict[str, List[RolloutWithReward]],
        Dict[str, List[RolloutWithReward]],
    ]:
        """Return the dictionaries unchanged (identity sampling)."""
        pos_rollout = copy.deepcopy(pos_rollout_dict)
        neg_rollout = copy.deepcopy(neg_rollout_dict)
        return pos_rollout, neg_rollout

    @staticmethod
    def downsample_one_uid(
        pos_list: List[RolloutWithReward],
        neg_list: List[RolloutWithReward],
        target_total: int = 8,
    ) -> Tuple[List[RolloutWithReward], List[RolloutWithReward]]:
        """
        Downsample positive and negative samples for a single UID toward
        a balanced target total.
        """
        target_pos = min(target_total // 2, len(pos_list))
        target_neg = min(target_total - target_pos, len(neg_list))

        if target_pos + target_neg < target_total:
            remaining = target_total - (target_pos + target_neg)
            extra_pos_cap = len(pos_list) - target_pos
            if extra_pos_cap > 0:
                add = min(extra_pos_cap, remaining)
                target_pos += add
                remaining -= add
            if remaining > 0:
                extra_neg_cap = len(neg_list) - target_neg
                if extra_neg_cap > 0:
                    add = min(extra_neg_cap, remaining)
                    target_neg += add

        pos_selected = pos_list[:target_pos]
        neg_selected = neg_list[:target_neg]
        return pos_selected, neg_selected

    @staticmethod
    def sampling_ada(
        pos_rollout_dict: Dict[str, List[RolloutWithReward]],
        neg_rollout_dict: Dict[str, List[RolloutWithReward]],
        final_keep_per_prompt: int = 8,
    ) -> Tuple[
        Dict[str, List[RolloutWithReward]],
        Dict[str, List[RolloutWithReward]],
    ]:
        """
        Balanced adaptive sampling for each UID.
        """
        out_pos: Dict[str, List[RolloutWithReward]] = {}
        out_neg: Dict[str, List[RolloutWithReward]] = {}

        all_uids = set(pos_rollout_dict.keys()) | set(neg_rollout_dict.keys())
        for uid in all_uids:
            pos_list = pos_rollout_dict.get(uid, [])
            neg_list = neg_rollout_dict.get(uid, [])
            if not pos_list and not neg_list:
                continue
            pos_sel, neg_sel = RolloutSampling.downsample_one_uid(
                pos_list, neg_list, target_total=final_keep_per_prompt
            )
            out_pos[uid] = pos_sel
            out_neg[uid] = neg_sel

        return out_pos, out_neg


# ---------------------------------------------------------------------------
# ProcessorsRegistry
# ---------------------------------------------------------------------------


class ProcessorsRegistry:
    """Registry for rollout classifiers, validators, and samplers."""

    PROCESSORS_MODULE = "agentrl.coordinator.processors"
    PROCESSORS_MODULE_FALLBACK = (
        "openjiuwen.dev_tools.agentrl.coordinator.processors"
    )

    def __init__(self):
        self._classifiers: Dict[str, Callable] = {}
        self._validators: Dict[str, Callable] = {}
        self._samplers: Dict[str, Callable] = {}
        self._load_predefined_functions()

    # -- registration -------------------------------------------------------

    def register_classifier(self, name: Optional[str] = None):
        """Decorator to register a classifier function."""
        def decorator(func):
            key = name or func.__name__
            self._classifiers[key] = func
            return func
        return decorator

    def register_validator(self, name: Optional[str] = None):
        """Decorator to register a validator function."""
        def decorator(func):
            key = name or func.__name__
            self._validators[key] = func
            return func
        return decorator

    def register_sampler(self, name: Optional[str] = None):
        """Decorator to register a sampler function."""
        def decorator(func):
            key = name or func.__name__
            self._samplers[key] = func
            return func
        return decorator

    # -- retrieval ----------------------------------------------------------

    def get_classifier(self, name: str) -> Callable:
        """Return the classifier function registered under the given name."""
        if name not in self._classifiers:
            raise build_error(
                StatusCode.AGENT_RL_PROCESSOR_NOT_FOUND,
                processor_type="classifier",
                name=name,
                available=str(list(self._classifiers.keys())),
            )
        return self._classifiers[name]

    def get_validator(self, name: str) -> Callable:
        """Return the validator function registered under the given name."""
        if name not in self._validators:
            raise build_error(
                StatusCode.AGENT_RL_PROCESSOR_NOT_FOUND,
                processor_type="validator",
                name=name,
                available=str(list(self._validators.keys())),
            )
        return self._validators[name]

    def get_sampler(self, name: str) -> Callable:
        """Return the sampler function registered under the given name."""
        if name not in self._samplers:
            raise build_error(
                StatusCode.AGENT_RL_PROCESSOR_NOT_FOUND,
                processor_type="sampler",
                name=name,
                available=str(list(self._samplers.keys())),
            )
        return self._samplers[name]

    # -- auto-discovery -----------------------------------------------------

    def _load_predefined_functions(self):
        """Autodiscover processors from the built-in processors module."""
        module = None
        for mod_path in (self.PROCESSORS_MODULE, self.PROCESSORS_MODULE_FALLBACK):
            try:
                module = importlib.import_module(mod_path)
                break
            except ImportError:
                continue
        if module is None:
            logger.warning(
                "Unable to import processors module via %s or %s",
                self.PROCESSORS_MODULE,
                self.PROCESSORS_MODULE_FALLBACK,
            )
            return

        for _name, cls in inspect.getmembers(module, inspect.isclass):
            for attr_name, attr_value in inspect.getmembers(
                cls, predicate=inspect.isfunction
            ):
                if attr_name.startswith("_"):
                    continue
                if "classify" in attr_name.lower():
                    self._classifiers[attr_name] = attr_value
                elif "validate" in attr_name.lower() or "stop" in attr_name.lower():
                    self._validators[attr_name] = attr_value
                elif "sampl" in attr_name.lower():
                    self._samplers[attr_name] = attr_value

        logger.info(
            "Loaded processors: classifiers=%s, validators=%s, samplers=%s",
            list(self._classifiers.keys()),
            list(self._validators.keys()),
            list(self._samplers.keys()),
        )
