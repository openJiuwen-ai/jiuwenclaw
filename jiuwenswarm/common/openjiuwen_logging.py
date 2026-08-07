# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
"""Bootstrap openjiuwen file logging under ``agent/.logs/openjiuwen``.

Call after workspace is ready and before importing modules that may trigger
openjiuwen loggers (otherwise default ``./logs`` is created under the process
cwd, often ``~/.jiuwenswarm/logs``).
"""

from __future__ import annotations

import logging


def bootstrap_openjiuwen_logging() -> bool:
    """Optionally load logging.yaml, pin log_path, and set default levels.

    Returns:
        True if ``config/logging.yaml`` was loaded; False otherwise.
    """
    from openjiuwen.core.common.logging import LogManager
    from openjiuwen.core.common.logging.log_config import configure_log, set_log_path

    from jiuwenswarm.common.utils import get_logs_dir, get_root_dir

    logging_yaml = get_root_dir() / "config" / "logging.yaml"
    loaded_yaml = logging_yaml.is_file()
    if loaded_yaml:
        configure_log(str(logging_yaml))

    # Always override path so hosts never depend on cwd-relative ./logs/
    set_log_path(get_logs_dir() / "openjiuwen")

    if not loaded_yaml:
        for logger in LogManager.get_all_loggers().values():
            logger.set_level(logging.INFO)

    return loaded_yaml
