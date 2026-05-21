# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Shared logging configuration for jiuwenbox."""

from __future__ import annotations

import logging

LOG_FORMAT = "[%(asctime)s] %(levelname)s %(name)s: %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
UVICORN_LOGGER_NAMES = ("uvicorn", "uvicorn.error", "uvicorn.access")


def _timestamp_formatter() -> logging.Formatter:
    return logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)


def _set_handler_formatters(logger: logging.Logger, formatter: logging.Formatter) -> None:
    for handler in logger.handlers:
        handler.setFormatter(formatter)


def configure_logging(level: int = logging.INFO) -> None:
    """Configure process logging with jiuwenbox's default timestamped format."""
    logging.basicConfig(level=level, format=LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
    formatter = _timestamp_formatter()
    _set_handler_formatters(logging.getLogger(), formatter)
    for logger_name in UVICORN_LOGGER_NAMES:
        _set_handler_formatters(logging.getLogger(logger_name), formatter)
