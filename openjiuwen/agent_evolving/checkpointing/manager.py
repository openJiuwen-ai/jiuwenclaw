# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

import uuid
from typing import Any, Dict, Optional, Protocol

from openjiuwen.agent_evolving.checkpointing.types import EvolveCheckpoint
from openjiuwen.core.operator import Operator


class CheckpointManager(Protocol):
    def should_save(self, *, epoch: int, improved: bool) -> bool:
        ...

    def build_checkpoint(
        self, *, agent: Any, progress: Any, updater_state: Optional[Dict[str, Any]] = None
    ) -> EvolveCheckpoint:
        ...

    def restore(self, *, agent: Any, checkpoint: EvolveCheckpoint) -> Dict[str, Any]:
        """Restore agent's operators_state, return progress_state (for Trainer progress recovery)."""
        ...


class DefaultCheckpointManager:
    """
    Default checkpoint manager:
    - Save timing: improved or every N epoch
    - Restore content: operators_state + progress best/epoch
    """

    def __init__(
        self,
        *,
        run_id: Optional[str] = None,
        checkpoint_version: str = "v1",
        save_every_n_epochs: int = 1,
        save_on_improve: bool = True,
    ):
        self._run_id = run_id or str(uuid.uuid4())
        self._ckpt_version = checkpoint_version
        self._save_every_n_epochs = max(int(save_every_n_epochs), 1)
        self._save_on_improve = bool(save_on_improve)

    @property
    def run_id(self) -> str:
        return self._run_id

    @staticmethod
    def _snapshot_operators_state(agent: Any) -> Dict[str, Dict[str, Any]]:
        """Snapshot state of all evolvable operators (operator_id -> state)."""
        out: Dict[str, Dict[str, Any]] = {}
        get_ops = getattr(agent, "get_operators", None)
        if not callable(get_ops):
            return out
        ops: Dict[str, Operator] = get_ops()
        if not isinstance(ops, dict) or not ops:
            return out
        for _, op in ops.items():
            out[op.operator_id] = op.get_state()
        return out

    @staticmethod
    def _restore_operators_state(agent: Any, operators_state: Dict[str, Dict[str, Any]]) -> None:
        """Restore state of all evolving operators."""
        get_ops = getattr(agent, "get_operators", None)
        if not callable(get_ops):
            return
        ops: Dict[str, Operator] = get_ops()
        if not isinstance(ops, dict) or not ops:
            return
        for operator_id, state in (operators_state or {}).items():
            op = ops.get(operator_id)
            if op is not None:
                op.load_state(state)

    def should_save(self, *, epoch: int, improved: bool) -> bool:
        if self._save_on_improve and improved:
            return True
        return (epoch % self._save_every_n_epochs) == 0

    def build_checkpoint(
        self,
        *,
        agent: Any,
        progress: Any,
        updater_state: Optional[Dict[str, Any]] = None,
    ) -> EvolveCheckpoint:
        operators_state = self._snapshot_operators_state(agent)
        step = {
            "epoch": int(getattr(progress, "current_epoch", 0)),
            "batch": int(getattr(progress, "current_batch_iter", 0)),
        }
        best = {
            "best_score": float(getattr(progress, "best_score", 0.0)),
        }
        seed = getattr(progress, "seed", None)
        return EvolveCheckpoint(
            version=self._ckpt_version,
            run_id=self._run_id,
            step=step,
            best=best,
            seed=seed,
            operators_state=operators_state,
            updater_state=updater_state or {},
            searcher_state={},
            last_metrics={
                "current_epoch_score": float(getattr(progress, "current_epoch_score", 0.0)),
            },
        )

    def restore(self, *, agent: Any, checkpoint: EvolveCheckpoint) -> Dict[str, Any]:
        self._restore_operators_state(agent, checkpoint.operators_state)
        # Return progress state recoverable by Trainer (doesn't tightly couple Progress type)
        return {
            "start_epoch": int((checkpoint.step or {}).get("epoch", 0)),
            "best_score": float((checkpoint.best or {}).get("best_score", 0.0)),
            "run_id": checkpoint.run_id,
        }
