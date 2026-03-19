# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
RL training extension for openjiuwen (v2).

This package provides:
- Data structures & config schemas for RL training
- Verl-based training executor (VerlTrainingExecutor extends RayPPOTrainer)
- MainTrainer for coordinating the training loop
- RLTrainerDaemon for multi-round rollout orchestration
- ParallelRuntimeExecutor for concurrent rollout generation
- A high-level `RLOptimizer` user entrypoint

Service-oriented logic (Flask proxy, FastAPI server) has been removed.
The system directly uses Verl's RayPPOTrainer with Ray actors.
"""

from openjiuwen.core.common.logging import logger

# ---------------------------------------------------------------------------
# Compatibility patch: core's BaseAgent.add_workflows(None) calls len(None)
# and crashes.  We monkey-patch add_workflows on the class so it tolerates
# None / empty lists, regardless of import order or which caller triggers it.
# ---------------------------------------------------------------------------


def _patch_add_workflows():
    """Make BaseAgent.add_workflows tolerant of workflows=None."""
    try:
        from openjiuwen.core.single_agent.legacy.agent import BaseAgent

        _original = BaseAgent.add_workflows

        # Avoid double-patching
        if getattr(_original, "agent_rl_patched", False):
            return

        def _safe_add_workflows(self, workflows):
            if not workflows:
                return None
            return _original(self, workflows)

        _safe_add_workflows.agent_rl_patched = True
        BaseAgent.add_workflows = _safe_add_workflows
    except Exception as e:
        logger.info("agentrl: skip add_workflows patch (core not available): %s", e)


_patch_add_workflows()

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
# Compatibility patch: MessageHandlerUtils.add_tool_result only checks for a
# single OutputSchema instance, but _post_task_completion passes a *list* of
# OutputSchema.  When tool_result is a list, str(list) produces ugly repr
# like ``[OutputSchema(type='plugin_final', ...)]`` instead of the clean
# tool output text.  We monkey-patch add_tool_result to properly extract the
# output text from lists of OutputSchema.
# ---------------------------------------------------------------------------


def _patch_add_tool_result():
    """Make add_tool_result handle list[OutputSchema] properly."""
    try:
        from openjiuwen.core.controller.legacy.utils import MessageHandlerUtils
        from openjiuwen.core.session.stream.base import OutputSchema
        from openjiuwen.core.workflow.base import WorkflowOutput

        _original = MessageHandlerUtils.add_tool_result

        if getattr(_original, "agent_rl_patched", False):
            return

        @staticmethod
        async def _safe_add_tool_result(event, context_engine, session):
            if not event:
                return
            from openjiuwen.core.foundation.llm.schema.message import ToolMessage
            from openjiuwen.core.common.security.json_utils import JsonUtils

            agent_context = context_engine.get_context(
                session_id=session.session_id()
            )
            tool_result = event.content.task_result.output

            # --- patched: handle list of OutputSchema ---
            if isinstance(tool_result, list):
                parts = []
                for item in tool_result:
                    if isinstance(item, OutputSchema):
                        payload = item.payload
                        if isinstance(payload, dict):
                            parts.append(str(payload.get("output", "")))
                        else:
                            parts.append(str(payload))
                    else:
                        parts.append(str(item))
                tool_result = "\n".join(parts) if parts else ""
            elif isinstance(tool_result, OutputSchema):
                payload = tool_result.payload
                if isinstance(payload, dict):
                    tool_result = payload.get("output", "")
            elif isinstance(tool_result, WorkflowOutput):
                tool_result = tool_result.result
            # --- end patch ---

            content = JsonUtils.safe_json_dumps(
                tool_result, str(tool_result), ensure_ascii=False
            )
            tool_message = ToolMessage(
                content=content, tool_call_id=event.context.task_id
            )
            await agent_context.add_messages(tool_message)

        _safe_add_tool_result.agent_rl_patched = True
        MessageHandlerUtils.add_tool_result = _safe_add_tool_result
    except Exception as e:
        logger.info("agentrl: skip add_tool_result patch (core not available): %s", e)


_patch_add_tool_result()

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
