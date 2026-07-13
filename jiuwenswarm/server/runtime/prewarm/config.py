# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Configuration for KV-cache prewarming, read from environment variables.

Env vars:
  JIUWENSWARM_PREWARM_ENABLED      (bool, default false) master switch
  JIUWENSWARM_PREWARM_SCENARIO_B   (bool, default true)  tool-call round prewarm (after_model_call)
  JIUWENSWARM_PREWARM_TIMEOUT      (float, default 10.0) per-request timeout in seconds
  JIUWENSWARM_PREWARM_DEBUG        (bool, default false) log cache-hit ratio per real LLM call
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class PrewarmConfig:
    enabled: bool = False
    scenario_b: bool = True
    timeout: float = 10.0
    debug: bool = False

    @classmethod
    def from_env(cls) -> "PrewarmConfig":
        return cls(
            enabled=_env_bool("JIUWENSWARM_PREWARM_ENABLED", False),
            scenario_b=_env_bool("JIUWENSWARM_PREWARM_SCENARIO_B", True),
            timeout=_env_float("JIUWENSWARM_PREWARM_TIMEOUT", 10.0),
            debug=_env_bool("JIUWENSWARM_PREWARM_DEBUG", False),
        )
