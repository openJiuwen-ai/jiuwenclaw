# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

import os
import json
from dataclasses import asdict
from typing import Any, Dict, Optional

from openjiuwen.agent_evolving.checkpointing.types import EvolveCheckpoint


def _to_json_compatible(obj: Any) -> Any:
    """Recursively convert objects to JSON-compatible types.

    Handles:
    - Pydantic models: use .model_dump()
    - Dataclasses: use asdict() recursively
    - Lists/tuples: serialize each element
    - Dicts: serialize each value
    - Primitives: return as-is
    """
    # Handle Pydantic models (BaseMessage, etc.)
    if hasattr(obj, "model_dump") and callable(obj.model_dump):
        dumped = obj.model_dump()
        return _to_json_compatible(dumped)

    # Handle dataclasses
    if hasattr(obj, "__dataclass_fields__"):
        return _to_json_compatible(asdict(obj))

    # Handle lists and tuples
    if isinstance(obj, (list, tuple)):
        return [_to_json_compatible(item) for item in obj]

    # Handle dictionaries
    if isinstance(obj, dict):
        return {k: _to_json_compatible(v) for k, v in obj.items()}

    # Primitives (str, int, float, bool, None) - return as-is
    return obj


class FileCheckpointStore:
    """
    Minimal usable checkpoint store: local JSON file.

    - Does not depend on core checkpointer (avoids polluting core lifecycle semantics)
    - Can run in any environment, convenient for debugging and auditing
    """

    def __init__(self, base_dir: str):
        self._base_dir = base_dir
        self._ensure_dir()

    def _ensure_dir(self) -> None:
        if self._base_dir is not None:
            os.makedirs(self._base_dir, exist_ok=True)

    def save_checkpoint(self, ckpt: EvolveCheckpoint, filename: str = "latest.json") -> Optional[str]:
        if self._base_dir is None:
            return None
        self._ensure_dir()
        path = os.path.join(self._base_dir, filename)
        with open(path, "w", encoding="utf-8") as f:
            serialized = _to_json_compatible(ckpt)
            json.dump(serialized, f, ensure_ascii=False, indent=2)
        return path

    def load_checkpoint(self, path: str) -> Optional[EvolveCheckpoint]:
        if self._base_dir is None:
            return None
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            raw: Dict[str, Any] = json.load(f)
        return EvolveCheckpoint(**raw)

    def load_state_dict(self, path: str) -> Optional[Dict[str, Dict[str, Any]]]:
        """
        Deep-learning style inference loader.

        A single, simple API for inference side that reads `operators_state` from a checkpoint JSON:

            state = store.load_state_dict(path)
            op.load_state(state[operator_id])
        """
        if self._base_dir is None:
            return None
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            raw: Dict[str, Any] = json.load(f)

        if "operators_state" not in raw:
            return None
        return raw.get("operators_state") or {}
