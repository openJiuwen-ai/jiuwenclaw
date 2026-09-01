"""Config loader for md2doc skill."""
import os
from typing import Dict, Any

import yaml

from logger import get_logger
from config.path_info import CONFIG_PATH

LOG = get_logger("Config")


class ConfigManager:
    """Configuration manager singleton."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._config = {}
            cls._instance._load_configs()
        return cls._instance

    def _load_configs(self):
        """Load all configuration files."""
        # Get the skill config directory
        # Load md2doc config
        md2doc_path = os.path.join(CONFIG_PATH, 'md2doc.yaml')
        if os.path.exists(md2doc_path):
            with open(md2doc_path, 'r', encoding='utf-8') as f:
                self._config['md2doc'] = yaml.safe_load(f)
                LOG.info(f"Loaded config from {md2doc_path}")
        else:
            self._config['md2doc'] = {}
            LOG.warning(f"Config file not found: {md2doc_path}")

    @property
    def md2doc_config(self) -> Dict[str, Any]:
        """Get md2doc configuration."""
        return self._config.get('md2doc', {})

    def get(self, key: str, default=None):
        """Get config value by key."""
        return self.md2doc_config.get(key, default)


# Global config instance
CONFIG = ConfigManager()
