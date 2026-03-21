# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
FileRolloutStore
----------------

File-based implementation of RolloutPersistence.

Training and validation rollouts are stored in separate sub-directories.
Files are split by step ranges controlled by ``flush_interval``.

Directory layout::

    save_path/
    ├── train/
    │   └── rollouts/
    │       ├── steps_000000_000099.jsonl
    │       └── ...
    ├── val/
    │   └── rollouts/
    │       ├── steps_000000_000099.jsonl
    │       └── ...
    └── step_summaries/
        └── steps_000000_000099.jsonl

Each ``.jsonl`` file contains one JSON object per line.
"""

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from openjiuwen.core.common.logging import logger
from openjiuwen.dev_tools.agentrl.coordinator.schemas import RolloutMessage
from openjiuwen.dev_tools.agentrl.rollout_store.base import RolloutPersistence


class FileRolloutStore(RolloutPersistence):
    """Persist rollout data to local JSONL files, grouped by step ranges."""

    def __init__(
        self,
        save_path: str,
        flush_interval: int = 100,
    ) -> None:
        """
        Args:
            save_path: Root directory for all rollout output files.
            flush_interval: Number of steps per file (e.g. 100 means
                steps 0-99 go into one file, 100-199 into the next, etc.).
        """
        self._save_path = Path(save_path)
        self._flush_interval = max(1, flush_interval)

        self._train_rollout_dir = self._save_path / "train" / "rollouts"
        self._val_rollout_dir = self._save_path / "val" / "rollouts"
        self._summary_dir = self._save_path / "step_summaries"

        for d in (
            self._train_rollout_dir,
            self._val_rollout_dir,
            self._summary_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)

        self._lock = threading.Lock()

        logger.info(
            "FileRolloutStore initialised: path=%s, flush_interval=%d",
            self._save_path, self._flush_interval,
        )

    def __getstate__(self):
        """Serialize for pickle; excludes non-picklable lock."""
        state = self.__dict__.copy()
        del state["_lock"]
        return state

    def __setstate__(self, state):
        """Restore from pickle; recreate lock and ensure directories exist."""
        self.__dict__.update(state)
        self._lock = threading.Lock()
        for d in (self._train_rollout_dir, self._val_rollout_dir, self._summary_dir):
            d.mkdir(parents=True, exist_ok=True)

    def _file_for_step(self, base_dir: Path, step: int) -> Path:
        """Return the JSONL file path corresponding to the given step."""
        lo = (step // self._flush_interval) * self._flush_interval
        hi = lo + self._flush_interval - 1
        return base_dir / f"steps_{lo:06d}_{hi:06d}.jsonl"

    def _append_jsonl(self, path: Path, obj: dict) -> None:
        """Append a single JSON line to a file (thread-safe)."""
        line = json.dumps(obj, ensure_ascii=False, default=str) + "\n"
        with self._lock:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)

    def _rollout_dir_for_phase(self, phase: str) -> Path:
        """Return the rollout directory for the given phase (train or val)."""
        if phase == "val":
            return self._val_rollout_dir
        return self._train_rollout_dir

    # -- interface implementation -------------------------------------------

    async def save_rollout(
        self, step: int, task_id: str, rollout: RolloutMessage,
        *, phase: str = "train",
    ) -> None:
        """Persist a rollout to train/val JSONL file based on step range."""
        doc = {
            "step": step,
            "task_id": task_id,
            "origin_task_id": rollout.origin_task_id,
            "rollout_id": rollout.rollout_id,
            "turns": [r.model_dump() for r in rollout.rollout_info],
            "reward_list": rollout.reward_list,
            "global_reward": rollout.global_reward,
            "turn_count": rollout.turn_count,
            "round_num": rollout.round_num,
            "start_time": rollout.start_time,
            "end_time": rollout.end_time,
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        }
        rollout_dir = self._rollout_dir_for_phase(phase)
        try:
            self._append_jsonl(self._file_for_step(rollout_dir, step), doc)
        except Exception as e:
            logger.warning("FileRolloutStore: failed to save %s rollout %s: %s", phase, task_id, e)



    async def save_step_summary(self, step: int, metrics: Dict[str, Any]) -> None:
        """Persist per-step training metrics to step_summaries JSONL file."""
        doc = {
            "step": step,
            "metrics": metrics,
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        }
        try:
            self._append_jsonl(self._file_for_step(self._summary_dir, step), doc)
        except Exception as e:
            logger.warning("FileRolloutStore: failed to save step summary: %s", e)

    async def query_rollouts(
        self, filters: Dict[str, Any], limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Scan rollout JSONL files (both train and val) and return matching entries."""
        results: List[Dict[str, Any]] = []
        for rollout_dir in (self._train_rollout_dir, self._val_rollout_dir):
            if not rollout_dir.exists():
                continue
            for fpath in sorted(rollout_dir.glob("*.jsonl"), reverse=True):
                with open(fpath, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            doc = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if all(doc.get(k) == v for k, v in filters.items()):
                            results.append(doc)
                            if len(results) >= limit:
                                return results
        return results

    async def close(self) -> None:
        """Release resources. No-op for file store; logs closure."""
        logger.info("FileRolloutStore closed (path=%s)", self._save_path)
