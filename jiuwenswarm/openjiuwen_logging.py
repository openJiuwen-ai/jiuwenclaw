# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
"""Route openjiuwen log files under JiuwenSwarm's service-level log directory."""

from __future__ import annotations

import logging

logger = logging.getLogger("jiuwenswarm.app")


def configure_openjiuwen_logging_under_jiuwenswarm(subdir: str = "openjiuwen") -> None:
    """Route openjiuwen log files under JiuwenSwarm's service-level log directory.

    openjiuwen defaults to ``./logs/``, which depends on the process working
    directory. JiuwenSwarm owns a stable log root via ``get_logs_dir()``; this
    helper keeps openjiuwen's existing run/interface/performance layout while
    moving that root to ``<jiuwenswarm logs>/<subdir>``.
    """
    try:
        from openjiuwen.core.common.logging.log_config import (
            configure_log_config,
            get_log_config_snapshot,
        )
        from jiuwenswarm.common.utils import get_logs_dir

        log_root = get_logs_dir() / subdir
        log_root.mkdir(parents=True, exist_ok=True)

        config = get_log_config_snapshot()
        target = str(log_root)
        if config.get("log_path") == target:
            return

        config["log_path"] = target
        configure_log_config(config)
    except Exception as exc:
        logger.warning(
            "Failed to route openjiuwen logs under JiuwenSwarm log dir: %s",
            exc,
        )
