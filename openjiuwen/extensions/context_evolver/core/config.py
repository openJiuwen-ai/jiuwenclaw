# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Configuration loader for context_evolver.

Loads configuration from:
1. .env file (for sensitive credentials like API keys)
2. config.yaml file (for algorithm and other settings)

Provides a simple get/set_value interface similar to os.getenv.
"""

import os
import yaml
from dotenv import load_dotenv


_config = {}
_config_loaded = False


def _convert_value(value):
    """Convert string values to appropriate types.

    Args:
        value: String value from .env file.

    Returns:
        Converted value (bool, int, float, or original string).
    """
    if not isinstance(value, str):
        return value

    # Handle boolean values
    if value.lower() in ('true', 'yes', '1'):
        return True
    if value.lower() in ('false', 'no', '0'):
        return False

    # Handle numeric values
    try:
        if '.' in value:
            return float(value)
        return int(value)
    except ValueError:
        pass

    return value


def load(config_path=None, env_path=None):
    """Load configuration from .env and YAML files.

    Args:
        config_path: Path to the YAML config file. If None, defaults to
                     config.yaml in the context_evolver root directory.
        env_path: Path to the .env file. If None, defaults to
                  .env in the context_evolver root directory.
    """
    global _config, _config_loaded

    # Get the context_evolver root directory (parent of core/)
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # Load .env file first (for sensitive credentials)
    if env_path is None:
        env_path = os.path.join(root_dir, ".env")

    if os.path.exists(env_path):
        load_dotenv(env_path, override=True)
        # Load .env values into _config with type conversion
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    _config[key.strip()] = _convert_value(value.strip())

    # Load config.yaml (for algorithm and other settings)
    if config_path is None:
        config_path = os.path.join(root_dir, "config.yaml")

    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            yaml_config = yaml.safe_load(f) or {}
            # YAML config values don't override .env values
            for key, value in yaml_config.items():
                if key not in _config:
                    _config[key] = value

    _config_loaded = True


def get(key, default=None):
    """Get a configuration value.

    Checks in order:
    1. Loaded config (from .env and config.yaml)
    2. Environment variables
    3. Default value

    Args:
        key: Configuration key.
        default: Default value if key is not found.

    Returns:
        The configuration value, or default if not found.
    """
    if not _config_loaded:
        load()

    # First check loaded config
    if key in _config:
        return _config[key]

    # Then check environment variables
    env_value = os.environ.get(key)
    if env_value is not None:
        return _convert_value(env_value)

    return default


def set_value(key, value):
    """Set a configuration value (useful for testing overrides).

    Args:
        key: Configuration key.
        value: Value to set.
    """
    if not _config_loaded:
        load()
    _config[key] = value


def delete(key):
    """Delete a configuration value (useful for testing).

    Args:
        key: Configuration key to remove.
    """
    if not _config_loaded:
        load()
    _config.pop(key, None)


def snapshot():
    """Take a snapshot of the current configuration (for test save/restore).

    Returns:
        A copy of the current config dict.
    """
    if not _config_loaded:
        load()
    return dict(_config)


def restore(snap):
    """Restore configuration from a snapshot (for test save/restore).

    Args:
        snap: A config dict previously returned by snapshot().
    """
    global _config
    _config = dict(snap)


def reload():
    """Force reload configuration from files.

    Useful when config files have been modified.
    """
    global _config, _config_loaded
    _config = {}
    _config_loaded = False
    load()
