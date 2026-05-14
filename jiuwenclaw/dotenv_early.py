# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Early --dotenv/--name parsing for multi-instance isolation.

This module MUST be imported BEFORE any other jiuwenclaw modules,
because it sets JIUWENCLAW_DATA_DIR environment variable that affects
path resolution in jiuwenclaw.utils.

Usage in entry point files:
    from jiuwenclaw.dotenv_early import parse_dotenv_early
    parse_dotenv_early()

    # Now safe to import other jiuwenclaw modules
    from jiuwenclaw.common.utils import ...

The parsing happens before any jiuwenclaw imports:
- sys.argv is scanned for --dotenv <path> and --name <name>
- If --dotenv found: load that file
- If --name found (no --dotenv): load instance bootstrap .env
- JIUWENCLAW_DATA_DIR is injected into os.environ
- Then get_user_workspace_dir() returns the correct instance path

IMPORTANT: This ensures module-level code uses correct workspace path.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Early logger for startup diagnostics (outputs to stderr)
_early_logger = logging.getLogger("jiuwenclaw.early")
if not _early_logger.handlers:
    _early_logger.addHandler(logging.StreamHandler(sys.stderr))
    _early_logger.setLevel(logging.WARNING)


def _early_warning(component_name: str, message: str) -> None:
    """Log early warning message to stderr."""
    _early_logger.warning("[%s] %s", component_name, message)


def _early_error(component_name: str, message: str) -> None:
    """Log early error message to stderr."""
    _early_logger.error("[%s] %s", component_name, message)


def parse_dotenv_early(component_name: str = "jiuwenclaw") -> Path | None:
    """Parse --dotenv/--name arguments and load env before jiuwenclaw imports.

    This function scans sys.argv for '--dotenv <path>' and '--name <name>' patterns,
    and loads the appropriate .env file with override=True.

    NOTE: This function does NOT remove arguments from sys.argv.
    - argparse will still see and parse them normally
    - But JIUWENCLAW_DATA_DIR is set BEFORE module-level code executes

    Priority:
    1. --dotenv <path>: Use specified file directly
    2. --name <name>: Load instance bootstrap .env from instances.yaml

    Args:
        component_name: Name for warning messages (e.g., "jiuwenclaw-app")

    Returns:
        Path to the loaded .env file if found and loaded, None otherwise

    Usage:
        from jiuwenclaw.dotenv_early import parse_dotenv_early
        parse_dotenv_early("jiuwenclaw-app")

        # Now safe to import jiuwenclaw modules
        from jiuwenclaw.common.utils import get_user_workspace_dir
    """
    global _parsed_dotenv, _component_name
    _component_name = component_name
    dotenv_path = None
    name_value = None

    # Scan sys.argv for --dotenv and --name patterns (DO NOT remove)
    for i, arg in enumerate(sys.argv):
        if arg == "--dotenv" and i + 1 < len(sys.argv):
            dotenv_path = sys.argv[i + 1]
        elif arg == "--name" and i + 1 < len(sys.argv):
            name_value = sys.argv[i + 1]

    # Load .env file
    result: Path | None = None
    if dotenv_path is not None:
        # --dotenv takes priority
        dotenv_file = Path(dotenv_path).expanduser().resolve()
        if dotenv_file.exists():
            from dotenv import load_dotenv
            load_dotenv(dotenv_file, override=True)
            result = dotenv_file
        else:
            _early_warning(component_name, f"--dotenv file not found: {dotenv_file}")

    elif name_value is not None:
        # --name: load instance bootstrap .env
        result = _load_bootstrap_by_name_early(name_value, component_name)

    # Store result for get_parsed_dotenv()
    _parsed_dotenv = result
    return result


def _load_bootstrap_by_name_early(name: str, component_name: str) -> Path | None:
    """Load bootstrap .env for named instance during early parsing.

    This is called before any jiuwenclaw imports, so it needs to:
    1. Validate instance name (basic check, full validation later)
    2. Find instances.yaml and read instance workspace
    3. Load bootstrap .env if exists

    Args:
        name: Instance name
        component_name: Component name for error messages

    Returns:
        Path to loaded .env if successful, None otherwise
    """
    import os

    # Basic instance name validation (just check it's not empty/reserved)
    if not name or name.lower() in ("default", "config", "tmp"):
        _early_error(component_name, f"Invalid instance name '{name}'")
        return None

    # Find instances.yaml path (same logic as instance_manager but without imports)
    user_home = os.environ.get("JIUWENCLAW_HOME") or Path.home()
    yaml_path = Path(user_home) / ".jiuwenclaw" / "instances.yaml"

    if not yaml_path.exists():
        _early_error(component_name, f"instances.yaml not found: {yaml_path}")
        _early_error(component_name, f"Run 'jiuwenclaw-init --name {name}' to create it.")
        return None

    # Parse YAML to find instance workspace (minimal parsing without full imports)
    try:
        import yaml
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        instances = data.get("instances", {}) if data else {}

        if name not in instances:
            _early_error(component_name, f"Instance '{name}' not found in instances.yaml")
            _early_error(component_name, f"Run 'jiuwenclaw-init --name {name}' to create it.")
            return None

        inst_data = instances.get(name) or {}
        workspace_str = inst_data.get("workspace")

        if workspace_str:
            workspace = Path(workspace_str).expanduser().resolve()
        else:
            instances_dir = Path(user_home) / ".jiuwenclaw-instances"
            workspace = instances_dir / name

    except Exception as exc:
        _early_error(component_name, f"Failed to parse instances.yaml: {exc}")
        return None

    # Check workspace exists
    if not workspace.exists():
        _early_error(component_name, f"Workspace directory not found: {workspace}")
        _early_error(component_name, f"Run 'jiuwenclaw-init --name {name}' to create it.")
        return None

    # Load bootstrap .env
    bootstrap_env = workspace / ".env"
    if bootstrap_env.exists():
        from dotenv import load_dotenv
        load_dotenv(bootstrap_env, override=True)
        return bootstrap_env
    else:
        # Bootstrap .env doesn't exist - need to create it
        # Import bootstrap module (safe now that we're past early parsing)
        from jiuwenclaw.instance_manager.bootstrap import _create_basic_bootstrap_env
        _create_basic_bootstrap_env(name, workspace, component_name)
        if bootstrap_env.exists():
            from dotenv import load_dotenv
            load_dotenv(bootstrap_env, override=True)
            return bootstrap_env
        return None


# Self-contained early parsing (no function call needed)
# This is the simplest usage pattern: just import this module
# and the parsing happens automatically.
_parsed_dotenv: Path | None = None
_component_name: str = "jiuwenclaw"


def set_component_name(name: str) -> None:
    """Set the component name for warning messages.

    Call this before importing the module if you want custom warnings:
        from jiuwenclaw import dotenv_early
        dotenv_early.set_component_name("jiuwenclaw-app")
        # Now import triggers parsing with custom name

    However, the simpler pattern is to just call parse_dotenv_early() directly.
    """
    global _component_name
    _component_name = name


def get_parsed_dotenv() -> Path | None:
    """Get the path that was parsed, if any."""
    return _parsed_dotenv


def load_instance_bootstrap_by_name(name: str) -> Path | None:
    """Load bootstrap .env for a named instance after argparse parsing.

    This function is a wrapper that delegates to
    jiuwenclaw.instance_manager.bootstrap.load_instance_bootstrap_by_name.

    NOTE: This function is deprecated. Use the function from
    jiuwenclaw.instance_manager.bootstrap directly for new code.

    Args:
        name: Instance name (must exist in instances.yaml)

    Returns:
        Path to loaded .env if successful, None otherwise
    """
    from jiuwenclaw.instance_manager.bootstrap import (
        load_instance_bootstrap_by_name as _load_bootstrap,
    )
    return _load_bootstrap(name)


__all__ = [
    "parse_dotenv_early",
    "get_parsed_dotenv",
    "set_component_name",
    "load_instance_bootstrap_by_name",
]