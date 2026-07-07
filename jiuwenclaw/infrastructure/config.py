# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""jiuwenclaw 基础设施配置（默认读取仓库根 ``.env``）。"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _resolve_env_files() -> tuple[str | Path, ...]:
    """解析可用的 .env 路径（优先 cwd，兼容 venv 安装布局）。"""
    candidates: list[Path] = [Path.cwd() / ".env"]
    here = Path(__file__).resolve()
    for depth in (2, 3):
        candidates.append(here.parents[depth] / ".env")
    return tuple(p for p in candidates if p.is_file())


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_resolve_env_files() or None,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    gateway_log_masking_enabled: bool = Field(
        default=True,
        validation_alias="GATEWAY_LOG_MASKING_ENABLED",
    )
    agent_runtime: str = Field(default="", validation_alias="AGENT_RUNTIME")


settings = Settings()
