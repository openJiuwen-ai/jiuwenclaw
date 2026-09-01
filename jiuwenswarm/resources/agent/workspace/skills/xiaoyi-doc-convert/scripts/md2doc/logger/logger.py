"""Simple logger utility for md2doc skill."""
import logging
import logging.config
import os
import sys


def _load_log_config():
    """Load logging configuration directly from YAML file.

    直接读取 config/log.yaml，不通过 ConfigManager，避免循环依赖。
    """
    config_path = os.path.join(
        os.path.dirname(__file__), '..', '..', 'config', 'log.yaml'
    )

    if os.path.exists(config_path):
        try:
            import yaml
            with open(config_path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f)
        except Exception:
            pass
    return None


def setup_logger(name: str) -> logging.Logger:
    """Setup a logger with YAML configuration if available, otherwise use defaults."""
    config = _load_log_config()

    if config:
        logging.config.dictConfig(config)
    else:
        # Fallback: ensure basic configuration
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(name)s - %(levelname)s: %(message)s",
            handlers=[logging.StreamHandler(sys.stdout)]
        )

    return logging.getLogger(name)


def get_logger(name: str):
    """Get a logger with the given name."""
    return setup_logger(name)
