# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import os
import threading
from pathlib import Path
from typing import Union, Optional, Set


class PathChecker:
    _instance: Optional["PathChecker"] = None
    _lock = threading.Lock()
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._sensitive_paths: Set[str] = set()
        self._load_config()
        self._initialized = True

    def _load_config(self) -> None:
        self._sensitive_paths.clear()

        try:
            from openjiuwen.core.common.security.user_config import UserConfig
            sensitive_paths = UserConfig.get_sensitive_paths()
        except Exception:
            sensitive_paths = [
                "/etc/passwd", "/etc/shadow", "/etc/hosts", "/etc/hostname",
                "/etc/ssh/", "/proc/", "/sys/", "/dev/",
                "C:\\Windows\\System32\\", "C:\\Windows\\SysWOW64\\", "C:\\Windows\\System\\"
            ]

        for path in sensitive_paths:
            if not path or not isinstance(path, str):
                continue

            try:
                normalized_path = os.path.realpath(os.path.expanduser(path.strip()))
                self._sensitive_paths.add(normalized_path)
            except (OSError, ValueError) as e:
                self._sensitive_paths.add(path.strip())

    def is_sensitive_path(self, path: Union[str, Path, None]) -> bool:
        if not path or not isinstance(path, (str, Path)):
            return False

        try:
            normalized_path = os.path.abspath(os.path.expanduser(str(path)))
            for sensitive_path in self._sensitive_paths:
                if normalized_path.startswith(sensitive_path):
                    return True
            return False
        except (OSError, ValueError):
            return True


def is_sensitive_path(path):
    """check path if sensitive"""
    return PathChecker().is_sensitive_path(path)
