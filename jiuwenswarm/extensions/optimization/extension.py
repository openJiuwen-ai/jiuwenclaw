"""Prompt optimizer extension RPC handlers.

Exposes the RLAF-P optimizer over the extension RPC bus, mirroring
:class:`jiuwenswarm.extensions.symphony.extension.SymphonyExtension`:

  * ``optimizer.optimize``    — run an optimization for a task, return the best prompt.
  * ``optimizer.status``      — report config + the latest run log.
  * ``optimizer.best_prompt`` — retrieve the best stored prompt for a similar task.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from typing import Any

from jiuwenswarm.extensions.sdk import BaseExtension
from jiuwenswarm.symphony.optimization.config import load_optimization_config
from jiuwenswarm.symphony.optimization.factory import OptimizerRuntimeFactory
from jiuwenswarm.symphony.optimization.models import TaskSpec
from jiuwenswarm.symphony.optimization.run_log import read_run_log
from jiuwenswarm.symphony.optimization.service import (
    default_run_log_path,
    optimize_prompt,
)

OPTIMIZER_OPTIMIZE = "optimizer.optimize"
OPTIMIZER_STATUS = "optimizer.status"
OPTIMIZER_BEST_PROMPT = "optimizer.best_prompt"

logger = logging.getLogger(__name__)


class PromptOptimizerExtension(BaseExtension):
    """Register prompt-optimizer RPC methods."""

    def __init__(self) -> None:
        self._registry = None
        self._run_guard = asyncio.Lock()
        self._active_task: asyncio.Task | None = None

    async def initialize(self, config) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    def register(self, registry) -> None:
        self._registry = registry
        registry.register_rpc_handler(OPTIMIZER_OPTIMIZE, self.optimize)
        registry.register_rpc_handler(OPTIMIZER_STATUS, self.status)
        registry.register_rpc_handler(OPTIMIZER_BEST_PROMPT, self.best_prompt)

    async def optimize(
        self,
        params: dict[str, Any] | None = None,
        request: Any = None,
    ) -> dict[str, Any]:
        del request
        params = params or {}
        config = load_optimization_config()
        if not config.enabled:
            return _disabled_payload()

        task = _task_from_params(params)
        if not task.objective:
            return {"success": False, "detail": "objective is required"}

        config = _apply_overrides(config, params)
        run_log = default_run_log_path(config)

        current_task = asyncio.current_task()
        async with self._run_guard:
            active = self._active_task
            if active is not None and active is not current_task and not active.done():
                return {
                    "success": False,
                    "detail": "已有一个提示词优化任务正在运行，请等待其完成。",
                }
            self._active_task = current_task

        try:
            result = await optimize_prompt(task, config=config, run_log_path=run_log)
        except Exception as exc:  # noqa: BLE001
            logger.exception("optimizer.optimize failed")
            return {"success": False, "detail": f"optimization failed: {exc}"}
        finally:
            async with self._run_guard:
                if self._active_task is current_task:
                    self._active_task = None

        return _compact_result(result)

    async def status(
        self,
        params: dict[str, Any] | None = None,
        request: Any = None,
    ) -> dict[str, Any]:
        del params, request
        config = load_optimization_config()

        def load() -> dict[str, Any]:
            return {
                "success": True,
                "enabled": config.enabled,
                "candidate_prompts": config.candidate_prompts,
                "max_iterations": config.max_iterations,
                "memory_enabled": config.memory_enabled,
                "memory_dir": str(config.resolved_memory_dir),
                "run_log": read_run_log(default_run_log_path(config)),
                "running": self._active_task is not None and not self._active_task.done(),
            }

        return await asyncio.to_thread(load)

    async def best_prompt(
        self,
        params: dict[str, Any] | None = None,
        request: Any = None,
    ) -> dict[str, Any]:
        del request
        params = params or {}
        config = load_optimization_config()
        if not config.enabled:
            return _disabled_payload()
        task = _task_from_params(params)
        if not task.objective:
            return {"success": False, "detail": "objective is required"}

        def search() -> dict[str, Any]:
            memory = OptimizerRuntimeFactory(config).memory()
            records = memory.search_similar(task, top_k=int(params.get("top_k") or 1))
            if not records:
                return {"success": True, "found": False, "objective": task.objective}
            best = max(records, key=lambda r: r.reward)
            return {
                "success": True,
                "found": True,
                "objective": task.objective,
                "prompt": best.prompt,
                "reward": round(best.reward, 4),
                "records": [
                    {"prompt": r.prompt, "reward": round(r.reward, 4)} for r in records
                ],
            }

        return await asyncio.to_thread(search)


async def register_extensions(registry):
    extension = PromptOptimizerExtension()
    extension.register(registry)
    return [extension]


def _disabled_payload() -> dict[str, Any]:
    return {
        "success": False,
        "disabled": True,
        "detail": "Prompt optimizer is disabled by config: symphony.optimization.enabled=false",
    }


def _task_from_params(params: dict[str, Any]) -> TaskSpec:
    if isinstance(params.get("task"), dict):
        return TaskSpec.from_dict(params["task"])
    return TaskSpec.from_dict(
        {
            "objective": params.get("objective", ""),
            "cases": params.get("cases", []),
            "constraints": params.get("constraints", []),
            "base_prompt": params.get("base_prompt", ""),
            "metadata": params.get("metadata", {}),
        }
    )


def _apply_overrides(config, params: dict[str, Any]):
    overrides: dict[str, Any] = {}
    if params.get("candidate_prompts"):
        try:
            overrides["candidate_prompts"] = max(1, int(params["candidate_prompts"]))
        except (TypeError, ValueError):
            pass
    if params.get("max_iterations"):
        try:
            overrides["max_iterations"] = max(1, int(params["max_iterations"]))
        except (TypeError, ValueError):
            pass
    return replace(config, **overrides) if overrides else config


def _compact_result(result) -> dict[str, Any]:
    iterations = [
        {"iteration": it.iteration, "best_score": round(it.best_score, 4)}
        for it in result.iterations
    ]
    total = result.token_usage.get("total") if isinstance(result.token_usage, dict) else None
    total_tokens = (
        int(total.get("total_tokens", 0)) if isinstance(total, dict) else 0
    )
    return {
        "success": result.success,
        "best_prompt": result.best_prompt,
        "best_score": round(result.best_score, 4),
        "converged": result.converged,
        "convergence_reason": result.convergence_reason,
        "iterations": iterations,
        "iteration_count": len(result.iterations),
        "total_tokens": total_tokens,
        "run_id": result.run_id,
        "detail": result.detail,
    }


__all__ = ["PromptOptimizerExtension", "register_extensions"]
