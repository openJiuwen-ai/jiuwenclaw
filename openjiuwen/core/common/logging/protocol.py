# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import logging
from typing import (
    Any,
    Dict,
    Protocol,
    runtime_checkable,
)


@runtime_checkable
class LoggerProtocol(Protocol):
    """Logger protocol defining methods all logger implementations must provide"""

    def debug(self, msg: str, *args, **kwargs) -> None:
        """Log debug level message"""
        ...

    def info(self, msg: str, *args, **kwargs) -> None:
        """Log info level message"""
        ...

    def warning(self, msg: str, *args, **kwargs) -> None:
        """Log warning level message"""
        ...

    def error(self, msg: str, *args, **kwargs) -> None:
        """Log error level message"""
        ...

    def critical(self, msg: str, *args, **kwargs) -> None:
        """Log critical level message"""
        ...

    def exception(self, msg: str, *args, **kwargs) -> None:
        """Log exception with stack trace"""
        ...

    def log(self, level: int, msg: str, *args, **kwargs) -> None:
        """Generic log method"""
        ...

    def set_level(self, level: int) -> None:
        """Set log level"""
        ...

    def add_handler(self, handler: logging.Handler) -> None:
        """Add log handler"""
        ...

    def logger(self):
        """Return inner logger"""
        pass

    def remove_handler(self, handler: logging.Handler) -> None:
        """Remove log handler"""
        ...

    def add_filter(self, filter) -> None:
        """Add log filter"""
        ...

    def remove_filter(self, filter) -> None:
        """Remove log filter"""
        ...

    def get_config(self) -> Dict[str, Any]:
        """Get logger config"""
        ...

    def reconfigure(self, config: Dict[str, Any]) -> None:
        """Reconfigure logger"""
        ... 
