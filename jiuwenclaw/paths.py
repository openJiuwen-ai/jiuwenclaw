# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Path management for JiuWenClaw.

Handles path resolution for both source and package installations:
- Source: Use project root directory for config and workspace
- Package (whl): Use user home directory (~/.jiuwenclaw) as runtime data root
"""

import importlib.util
import logging
import shutil
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

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
    """Detect if running from a package installation (whl)."""
    global _is_package
    if _is_package is not None:
        return _is_package

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
    """Find the package root directory containing config and workspace."""
    # In package mode (whl), __file__ is at site-packages/jiuwenclaw/paths.py
    # So parent is site-packages/jiuwenclaw/, which contains config and workspace
    current = Path(__file__).resolve().parent

    # Check if config and workspace exist at this level
    if (current / "config").exists() and (current / "workspace").exists():
        return current

    return None


def init_user_workspace(overwrite: bool = True) -> Path:
    """Initialize ~/.jiuwenclaw from package resources.

    Args:
        overwrite: When True, overwrite existing files with package defaults.

    Returns:
        Path to user workspace root (~/.jiuwenclaw).

    Raises:
        RuntimeError: If not in package mode or package resources are missing.
    """
    if not _detect_installation_mode():
        raise RuntimeError("jiuwenclaw-init is only available in package installation mode")

    package_root = _find_package_root()
    if not package_root:
        raise RuntimeError("package root not found")

    USER_WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)

    config_src = package_root / "config"
    config_dest = USER_WORKSPACE_DIR / "config"
    if not config_src.exists():
        raise RuntimeError(f"config source not found: {config_src}")
    if overwrite:
        shutil.copytree(config_src, config_dest, dirs_exist_ok=True)
    elif not config_dest.exists():
        shutil.copytree(config_src, config_dest)

    workspace_src = package_root / "workspace"
    workspace_dest = USER_WORKSPACE_DIR / "workspace"
    if not workspace_src.exists():
        raise RuntimeError(f"workspace source not found: {workspace_src}")
    if overwrite:
        shutil.copytree(workspace_src, workspace_dest, dirs_exist_ok=True)
    elif not workspace_dest.exists():
        shutil.copytree(workspace_src, workspace_dest)

    env_template_src = package_root / ".env.template"
    env_dest = USER_WORKSPACE_DIR / ".env"
    if not env_template_src.exists():
        raise RuntimeError(f"env template source not found: {env_template_src}")
    if overwrite or not env_dest.exists():
        shutil.copy2(env_template_src, env_dest)

    return USER_WORKSPACE_DIR


def _resolve_paths() -> None:
    """Resolve and cache all paths."""
    global _initialized, _config_dir, _workspace_dir, _root_dir

    if _initialized:
        return

    if _detect_installation_mode():
        # Package mode
        package_root = _find_package_root()
        if package_root:
            _root_dir = USER_WORKSPACE_DIR
            _config_dir = USER_WORKSPACE_DIR / "config"
            _workspace_dir = USER_WORKSPACE_DIR / "workspace"
        else:
            logger.warning("Could not find package root, falling back to source mode")
            source_root = _find_source_root()
            _root_dir = source_root
            _config_dir = source_root / "config"
            _workspace_dir = source_root / "workspace"
    else:
        # Source mode
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


def _get_config_module() -> Any:
    """Get config module from correct location.

    This function dynamically loads the config module from either:
    - User workspace: ~/.jiuwenclaw/config
    - Source mode: project root config directory

    Returns:
        The loaded config module with get_config function

    Raises:
        ImportError: If config module cannot be loaded
    """
    # Check for user workspace
    config_dir = Path.home() / ".jiuwenclaw" / "config"
    if not config_dir.exists():
        # Source mode: use relative path
        config_dir = get_config_dir()

    # Import config as a module
    spec = importlib.util.spec_from_file_location("config_module", str(config_dir / "config.py"))
    if spec and spec.loader:
        module = importlib.util.module_from_spec(spec)
        sys.modules["config_module"] = module
        spec.loader.exec_module(module)
        return module
    raise ImportError(f"Cannot load config module from {config_dir}")
