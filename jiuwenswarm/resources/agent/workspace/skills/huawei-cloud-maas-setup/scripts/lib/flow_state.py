#!/usr/bin/env python3
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""流程状态机与日志辅助。

purchase_flow 跨子命令持久化已成功的步骤结果，
避免单步失败后整流程回滚。
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from lib.cdp_client import emit, output_json


# 状态文件目录
_STATE_DIR_NAME = ".huawei_maas_state"


def _state_dir() -> Path:
    """状态文件存放目录，跨进程持久化。"""
    base = Path.home() / ".jiuwenswarm" / "agent" / "workspace"
    target = base / _STATE_DIR_NAME
    target.mkdir(parents=True, exist_ok=True)
    return target


def _state_path(flow_id: str) -> Path:
    return _state_dir() / f"{flow_id}.json"


def new_flow_id() -> str:
    """生成新的流程 ID。"""
    return f"maas_{int(time.time())}_{uuid.uuid4().hex[:8]}"


@dataclass
class FlowState:
    """购买流程的跨子命令状态。"""

    flow_id: str = ""
    cdp_url: str = ""
    region: str = "cn-southwest-2"
    auth_done: bool = False
    auth_skipped_reason: Optional[str] = None
    api_key: str = ""
    models_opened: list[str] = field(default_factory=list)
    models_already_opened: list[str] = field(default_factory=list)
    models_failed: list[dict[str, str]] = field(default_factory=list)
    start_time: float = 0.0
    last_update: float = 0.0
    current_stage: str = "init"

    def touch(self, stage: str) -> None:
        self.current_stage = stage
        self.last_update = time.time()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def save(self) -> Path:
        """持久化到磁盘，供后续子命令读取。"""
        path = _state_path(self.flow_id)
        path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    @classmethod
    def load(cls, flow_id: str) -> Optional["FlowState"]:
        """从磁盘恢复状态。"""
        path = _state_path(flow_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        return cls(
            flow_id=data.get("flow_id", flow_id),
            cdp_url=data.get("cdp_url", ""),
            region=data.get("region", "cn-southwest-2"),
            auth_done=bool(data.get("auth_done", False)),
            auth_skipped_reason=data.get("auth_skipped_reason"),
            api_key=data.get("api_key", ""),
            models_opened=list(data.get("models_opened") or []),
            models_already_opened=list(data.get("models_already_opened") or []),
            models_failed=list(data.get("models_failed") or []),
            start_time=float(data.get("start_time", 0.0)),
            last_update=float(data.get("last_update", 0.0)),
            current_stage=data.get("current_stage", "init"),
        )

    def clear(self) -> None:
        """清理磁盘状态文件。"""
        path = _state_path(self.flow_id)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def make_failure(stage: str, message: str, **extra: Any) -> dict[str, Any]:
    """生成统一格式的失败 JSON。"""
    payload: dict[str, Any] = {
        "ok": False,
        "stage": stage,
        "error": message,
    }
    payload.update(extra)
    return payload


def make_success(stage: str, **extra: Any) -> dict[str, Any]:
    """生成统一格式的成功 JSON。"""
    payload: dict[str, Any] = {"ok": True, "stage": stage}
    payload.update(extra)
    return payload
