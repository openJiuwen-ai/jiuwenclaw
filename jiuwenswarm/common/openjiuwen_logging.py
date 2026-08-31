# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
"""Bootstrap openjiuwen file logging under ``agent/.logs/openjiuwen``.

Call after workspace is ready and before importing modules that may trigger
openjiuwen loggers (otherwise default ``./logs`` is created under the process
cwd, often ``~/.jiuwenswarm/logs``).
"""

from __future__ import annotations

import logging


def _pin_openjiuwen_log_path(log_root) -> None:
    """Pin openjiuwen log files under ``log_root`` (compat across openjiuwen versions)."""
    from openjiuwen.core.common.logging.log_config import (
        configure_log_config,
        get_log_config_snapshot,
    )

    target = str(log_root)
    try:
        from openjiuwen.core.common.logging.log_config import set_log_path

        set_log_path(log_root)
        return
    except ImportError:
        pass

    config = get_log_config_snapshot()
    if config.get("log_path") == target:
        return
    config["log_path"] = target
    configure_log_config(config)


def bootstrap_openjiuwen_logging() -> bool:
    """Optionally load logging.yaml, pin log_path, and set default levels.

    Returns:
        True if ``config/logging.yaml`` was loaded; False otherwise.
    """
    from openjiuwen.core.common.logging import LogManager
    from openjiuwen.core.common.logging.log_config import configure_log

    from jiuwenswarm.common.utils import get_logs_dir, get_root_dir

    logging_yaml = get_root_dir() / "config" / "logging.yaml"
    loaded_yaml = logging_yaml.is_file()
    if loaded_yaml:
        configure_log(str(logging_yaml))

    # Always override path so hosts never depend on cwd-relative ./logs/
    log_root = get_logs_dir() / "openjiuwen"
    log_root.mkdir(parents=True, exist_ok=True)
    _pin_openjiuwen_log_path(log_root)

    if not loaded_yaml:
        for logger in LogManager.get_all_loggers().values():
            logger.set_level(logging.INFO)

    return loaded_yaml
