# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""jiuwenswarm 基础设施配置（优先读环境变量 / 仓库根 ``.env`` 已由进程加载）。"""

from __future__ import annotations

import os


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    text = raw.strip().lower()
    if text in ("0", "false", "no", "off"):
        return False
    if text in ("1", "true", "yes", "on"):
        return True
    return default


class Settings:
    """轻量 settings：实例化时读取当前环境变量（便于单测 monkeypatch）。"""

    def __init__(self) -> None:
        self.gateway_log_masking_enabled: bool = _env_bool(
            "GATEWAY_LOG_MASKING_ENABLED", default=True
        )
        self.agent_runtime: str = os.getenv("AGENT_RUNTIME", "").strip()


settings = Settings()
