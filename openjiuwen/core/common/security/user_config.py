# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

import os
import threading
import configparser
from pathlib import Path
from typing import Optional, Dict, Any

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error

DEFAULT_SENSITIVE_PATH_STR = ("/etc/passwd,/etc/shadow,/etc/hosts,/etc/hostname,/etc/ssh/,"
                              "C:\\Windows\\System32\\,C:\\Windows\\SysWOW64\\,C:\\Windows\\System\\")
DEFAULT_SENSITIVE_PATHS = ["/etc/passwd", "/etc/shadow", "/etc/hosts", "/etc/hostname", "/etc/ssh/",
                            "C:\\Windows\\System32\\", "C:\\Windows\\SysWOW64\\", "C:\\Windows\\System\\"]


class UserConfig:
    _instance: Optional["UserConfig"] = None
    _lock = threading.Lock()

    DEFAULT: Dict[str, Any] = {"settings":
                                   {"is_sensitive": True,
                                    "sensitive_paths": DEFAULT_SENSITIVE_PATH_STR}}

    def __init__(self, config_path: Optional[Path] = None):
        self._cfg = configparser.ConfigParser()
        if config_path and config_path.is_file():
            try:
                self._cfg.read(config_path, encoding="utf-8")
            except Exception:
                self._cfg.read_dict(self.DEFAULT)
        else:
            self._cfg.read_dict(self.DEFAULT)
        self.is_sensitive: bool = self._cfg.getboolean("settings", "is_sensitive")
        self._sensitive_paths: Optional[list] = None

    @classmethod
    def set_config_path(cls, path: Path) -> None:
        """set config file path"""
        if cls._instance is not None:
            raise build_error(
                StatusCode.COMMON_USER_CONFIG_PROCESS_ERROR,
                error_msg="config already initialized"
            )
        cls._user_path = cls._resolve_and_check(path)

    @classmethod
    def get_config(cls) -> "UserConfig":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    user_path = getattr(cls, "_user_path", None)
                    cls._instance = cls(user_path)
        return cls._instance

    @classmethod
    def is_sensitive(cls) -> bool:
        result = os.getenv("IS_SENSITIVE", "true").lower() == "false"
        if result:
            return False

        return cls.get_config().is_sensitive

    @classmethod
    def get_sensitive_paths(cls) -> list:
        """get sensitive paths"""
        return cls.get_config().get_sensitive_paths_list()

    @classmethod
    def set_is_sensitive(cls, is_sensitive: bool = True):
        """set is_sensitive flag"""
        config = cls.get_config()
        with cls._lock:
            config.is_sensitive = is_sensitive

    @staticmethod
    def _resolve_and_check(path: Path) -> Path:
        path = Path(os.path.expandvars(path.expanduser())).resolve()

        root = Path.cwd()
        try:
            path.relative_to(root)
        except ValueError as e:
            raise build_error(
                StatusCode.COMMON_USER_CONFIG_PROCESS_ERROR,
                error_msg="config file must inside root"
            ) from e

        return path

    def get_sensitive_paths_list(self) -> list:
        if self._sensitive_paths is None:
            try:
                sensitive_paths_str = self._cfg.get("settings", "sensitive_paths", fallback="")
                if sensitive_paths_str:
                    self._sensitive_paths = [path.strip() for path in sensitive_paths_str.split(",") if path.strip()]
                else:
                    self._sensitive_paths = DEFAULT_SENSITIVE_PATHS
            except Exception:
                self._sensitive_paths = DEFAULT_SENSITIVE_PATHS
        return self._sensitive_paths.copy()
