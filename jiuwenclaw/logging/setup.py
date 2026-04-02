# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""日志设置模块

提供日志系统配置功能。
"""

import logging
from pathlib import Path
from typing import Optional

from jiuwenclaw.logging.formatter import UserVisibleFormatter
from jiuwenclaw.logging.handler import SafeRotatingFileHandler, _LOG_FILE_BACKUP_COUNT, _LOG_FILE_MAX_BYTES
from jiuwenclaw.logging.levels import _ComponentNameFilter, _resolve_logging_levels


def setup_logger(log_level: Optional[str] = None) -> logging.Logger:
    """配置 ``jiuwenclaw`` 根日志：控制台 + 分组件文件 + 汇总 full.log。

    各模块应使用 ``logging.getLogger(__name__)``，分文件规则：
    - ``jiuwenclaw.channel.*`` → channel.log
    - ``jiuwenclaw.agentserver.*`` → agent_server.log
    - 其余 ``jiuwenclaw.*``（含 ``jiuwenclaw.app``、gateway、evolution、utils 等）→ gateway.log

    所有分类日志同时写入 ``full.log``。输出目录：``~/.jiuwenclaw/.logs/``。

    级别由 ``config.yaml`` 的 ``logging`` 段控制；环境变量 ``LOG_LEVEL`` 仅覆盖**控制台**级别
    （``log_level`` 参数为 ``None`` 时）。若传入 ``log_level``（如单测），则控制台与各文件级别均为该值。

    Args:
        log_level: 可选的日志级别覆盖值

    Returns:
        logging.Logger: 配置好的根日志记录器
    """
    # 延迟导入避免循环依赖
    from jiuwenclaw.utils import get_logs_dir

    logs_root = get_logs_dir()
    logs_root.mkdir(parents=True, exist_ok=True)

    levels = _resolve_logging_levels(log_level)

    root = logging.getLogger("jiuwenclaw")
    root.setLevel(levels.logger)
    root.propagate = False
    for handler in root.handlers[:]:
        handler.close()
        root.removeHandler(handler)

    formatter = UserVisibleFormatter(
        fmt="%(asctime)s.%(msecs)03d %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    def _add_rotating(
        filename: str,
        level: int,
        name_filter: Optional[_ComponentNameFilter] = None,
    ) -> None:
        h = SafeRotatingFileHandler(
            filename=logs_root / filename,
            maxBytes=_LOG_FILE_MAX_BYTES,
            backupCount=_LOG_FILE_BACKUP_COUNT,
            encoding="utf-8",
        )
        h.setLevel(level)
        h.setFormatter(formatter)
        if name_filter is not None:
            h.addFilter(name_filter)
        root.addHandler(h)

    _add_rotating("gateway.log", levels.gateway, _ComponentNameFilter("gateway"))
    _add_rotating("channel.log", levels.channel, _ComponentNameFilter("channel"))
    _add_rotating("agent_server.log", levels.agent_server, _ComponentNameFilter("agent_server"))
    _add_rotating("full.log", levels.full, None)

    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(levels.console)
    stream_handler.setFormatter(formatter)
    root.addHandler(stream_handler)
    return root
