# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Path management for JiuWenClaw.

Handles path resolution for both source and package installations:

- When a user workspace exists (~/.jiuwenclaw), it is always used as the
  runtime root for config, workspace and .env (for both source and package).
- Before the user workspace is initialized:
  - Source: Use project root directory for config and workspace.
  - Package (whl): Use user home directory (~/.jiuwenclaw) as planned
    runtime root, but templates are copied from installed package resources.
"""

import importlib.util
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from logging.handlers import RotatingFileHandler

# User home directory
USER_HOME = Path.home()
USER_WORKSPACE_DIR = USER_HOME / ".jiuwenclaw"

# Cache for resolved paths
_config_dir: Path | None = None
_workspace_dir: Path | None = None
_root_dir: Path | None = None
_is_package: bool | None = None
_initialized: bool = False


def _detect_installation_mode() -> bool:
    """Detect if running from a package installation (whl) or PyInstaller bundle."""
    global _is_package
    if _is_package is not None:
        return _is_package

    # PyInstaller 打包后使用用户工作区路径
    if getattr(sys, "frozen", False):
        _is_package = True
        return True

    # Check if module is in site-packages
    module_file = Path(__file__).resolve()

    # Check if module file is in any site-packages directory
    for path in sys.path:
        site_packages = Path(path)
        if "site-packages" in str(site_packages) and site_packages in module_file.parents:
            _is_package = True
            return True

    _is_package = False
    return False


def _find_source_root() -> Path:
    """Find the source code root directory for development mode."""
    current = Path(__file__).resolve().parent.parent

    # Check if config and workspace exist at this level
    if (current / "config").exists() and (current / "workspace").exists():
        return current

    # Check parent directory
    parent = current.parent
    if (parent / "config").exists() and (parent / "workspace").exists():
        return parent

    return current


def _find_package_root() -> Path | None:
    """Best-effort detection of the jiuwenclaw package root.

    In package mode (whl), __file__ is at site-packages/jiuwenclaw/paths.py,
    so parent is site-packages/jiuwenclaw/.
    In editable / source mode, __file__ is at <project>/jiuwenclaw/paths.py,
    so parent is <project>/jiuwenclaw/.
    """
    current = Path(__file__).resolve().parent
    return current


def prepare_workspace(overwrite: bool = True):
    package_root = _find_package_root()
    if not package_root:
        raise RuntimeError("package root not found")

    USER_WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)

    # ----- config: copy config.yaml -----
    resources_dir = package_root / "resources"
    config_yaml_src_candidates = [
        resources_dir / "config.yaml",
        package_root / "config" / "config.yaml",
    ]

    config_yaml_src = next((p for p in config_yaml_src_candidates if p.exists()), None)

    if not config_yaml_src:
        raise RuntimeError(
            "config.yaml template not found; tried: "
            + ", ".join(str(p) for p in config_yaml_src_candidates)
        )

    config_dest_dir = USER_WORKSPACE_DIR / "config"
    config_dest_dir.mkdir(parents=True, exist_ok=True)
    config_yaml_dest = config_dest_dir / "config.yaml"

    if overwrite or not config_yaml_dest.exists():
        shutil.copy2(config_yaml_src, config_yaml_dest)

    # ----- workspace: copy tree -----
    workspace_src_candidates = [
        package_root / "workspace",
        package_root.parent / "workspace",
    ]
    workspace_src = next((p for p in workspace_src_candidates if p.exists()), None)
    if not workspace_src:
        raise RuntimeError(
            "workspace source not found; tried: "
            + ", ".join(str(p) for p in workspace_src_candidates)
        )
    workspace_dest = USER_WORKSPACE_DIR / "workspace"
    if overwrite:
        if workspace_dest.exists():
            shutil.rmtree(workspace_dest)
        shutil.copytree(workspace_src, workspace_dest, dirs_exist_ok=True)
    elif not workspace_dest.exists():
        shutil.copytree(workspace_src, workspace_dest)

    # ----- .env: copy from template -----
    env_template_src_candidates = [
        resources_dir / ".env.template",
        package_root / ".env.template",
    ]
    env_template_src = next((p for p in env_template_src_candidates if p.exists()), None)
    if not env_template_src:
        raise RuntimeError(
            "env template source not found; tried: "
            + ", ".join(str(p) for p in env_template_src_candidates)
        )
    env_dest = USER_WORKSPACE_DIR / ".env"
    if overwrite or not env_dest.exists():
        shutil.copy2(env_template_src, env_dest)


def init_user_workspace(overwrite: bool = True) -> Path:
    """Initialize ~/.jiuwenclaw from package or source resources.

    资源布局（新）:
    - 模板配置:   <package_root>/resources/config.yaml
    - .env 模板: <package_root>/resources/.env.template
    - workspace: 优先 <package_root>/workspace，其次 <package_root>/../workspace

    上述内容会被复制到:
    - ~/.jiuwenclaw/config/config.yaml
    - ~/.jiuwenclaw/.env
    - ~/.jiuwenclaw/workspace/...

    无论是通过 pip/whl 安装还是源码目录直接运行，效果保持一致。
    """
    if USER_WORKSPACE_DIR.exists():
        # Warn user about data loss and ask for confirmation
        print("[jiuwenclaw-init] WARNING: This will delete all historical configuration and memory information.")
        print("[jiuwenclaw-init] This action cannot be undone.")
        confirmation = input("[jiuwenclaw-init] Do you want to confirm reinitialization? (yes/no): ").strip().lower()

        if confirmation not in ("yes", "y"):
            print("[jiuwenclaw-init] Initialization cancelled. Exiting.")
            return "cancelled"

    prepare_workspace(overwrite)

    return USER_WORKSPACE_DIR


def _resolve_paths() -> None:
    """Resolve and cache all paths."""
    global _initialized, _config_dir, _workspace_dir, _root_dir

    if _initialized:
        return

    # 优先使用已初始化的用户工作区 (~/.jiuwenclaw)，
    # 保证源码运行与安装包运行后的读写路径完全一致。
    user_config_dir = USER_WORKSPACE_DIR / "config"
    user_workspace_dir = USER_WORKSPACE_DIR / "workspace"
    if user_config_dir.exists():
        _root_dir = USER_WORKSPACE_DIR
        _config_dir = user_config_dir
        _workspace_dir = user_workspace_dir
    else:
        if _detect_installation_mode():
            # Package mode（尚未执行 init），计划根目录仍是用户家目录，
            # 但 config/workspace 可能还不存在。
            package_root = _find_package_root()
            if package_root:
                _root_dir = USER_WORKSPACE_DIR
                _config_dir = USER_WORKSPACE_DIR / "config"
                _workspace_dir = USER_WORKSPACE_DIR / "workspace"
            else:
                source_root = _find_source_root()
                _root_dir = source_root
                _config_dir = source_root / "config"
                _workspace_dir = source_root / "workspace"
        else:
            # 纯源码模式且未初始化用户工作区：直接使用工程根目录。
            source_root = _find_source_root()
            _root_dir = source_root
            _config_dir = source_root / "config"
            _workspace_dir = source_root / "workspace"

    _initialized = True


def get_config_dir() -> Path:
    """Get the config directory path."""
    _resolve_paths()
    return _config_dir


def get_workspace_dir() -> Path:
    """Get the workspace directory path."""
    _resolve_paths()
    return _workspace_dir


def get_root_dir() -> Path:
    """Get the root directory path."""
    _resolve_paths()
    return _root_dir


def get_agent_workspace_dir() -> Path:
    """Get the agent workspace directory path (workspace/agent)."""
    return get_workspace_dir() / "agent"


def get_config_file() -> Path:
    """Get the config.yaml file path."""
    return get_config_dir() / "config.yaml"


def is_package_installation() -> bool:
    """Check if running from package installation."""
    return _detect_installation_mode()


def setup_logger(log_level: str = "INFO") -> logging.Logger:
    """Setup logger with console and file handlers."""
    project_root = get_root_dir()
    logs_root = project_root / "logs"
    logs_root.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("jiuwenclaw.app")
    logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    logger.propagate = False
    logger.handlers.clear()

    formatter = logging.Formatter(
        fmt="%(asctime)s.%(msecs)03d %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    file_handler = RotatingFileHandler(
        filename=logs_root / "app.log",
        maxBytes=20 * 1024 * 1024,
        backupCount=20,
        encoding="utf-8"
    )
    file_handler.setFormatter(formatter)

    logger.addHandler(stream_handler)
    logger.addHandler(file_handler)
    return logger

logger = setup_logger(os.getenv("LOG_LEVEL", "INFO"))
