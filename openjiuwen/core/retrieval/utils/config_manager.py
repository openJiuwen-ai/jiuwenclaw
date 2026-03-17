# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Configuration Manager

Unified configuration management, supports loading and saving from files.
"""

import json
from pathlib import Path
from typing import Dict, Optional, Type, TypeVar

try:
    import yaml
except ImportError:
    yaml = None

from pydantic import BaseModel

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.retrieval.common.config import KnowledgeBaseConfig

T = TypeVar("T", bound=BaseModel)


class ConfigManager:
    """Configuration manager for unified configuration management"""

    def __init__(self, config_path: Optional[str] = None):
        self._configs: Dict[str, BaseModel] = {}
        if config_path:
            self.load_from_file(config_path)

    def load_from_file(self, path: str) -> None:
        """Load configuration from file (supports JSON and YAML)"""
        path_obj = Path(path)
        if not path_obj.exists():
            raise build_error(
                StatusCode.RETRIEVAL_UTILS_CONFIG_FILE_NOT_FOUND, error_msg=f"Configuration file does not exist: {path}"
            )

        suffix = path_obj.suffix.lower()
        if suffix == ".json":
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        elif suffix in [".yaml", ".yml"]:
            if yaml is None:
                raise build_error(
                    StatusCode.RETRIEVAL_UTILS_PYYAML_NOT_FOUND,
                    error_msg="PyYAML is required to support YAML configuration files",
                )
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        else:
            raise build_error(
                StatusCode.RETRIEVAL_UTILS_CONFIG_FORMAT_NOT_SUPPORT,
                error_msg=f"Unsupported configuration file format: {suffix}",
            )

        # Create configuration object based on data structure
        # Assuming knowledge base config here, can be extended as needed
        kb_config = KnowledgeBaseConfig(**data)
        self._configs["knowledge_base"] = kb_config

    def save_to_file(self, path: str) -> None:
        """Save configuration to file"""
        if "knowledge_base" not in self._configs:
            raise build_error(StatusCode.RETRIEVAL_UTILS_CONFIG_NOT_FOUND, error_msg="No configuration to save")

        kb_config: KnowledgeBaseConfig = self._configs["knowledge_base"]
        data = kb_config.model_dump()

        path_obj = Path(path)
        suffix = path_obj.suffix.lower()
        if suffix == ".json":
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        elif suffix in [".yaml", ".yml"]:
            if yaml is None:
                raise build_error(
                    StatusCode.RETRIEVAL_UTILS_PYYAML_NOT_FOUND,
                    error_msg="PyYAML is required to support YAML configuration files",
                )
            with open(path, "w", encoding="utf-8") as f:
                yaml.dump(data, f, allow_unicode=True, default_flow_style=False)
        else:
            raise build_error(
                StatusCode.RETRIEVAL_UTILS_CONFIG_FORMAT_NOT_SUPPORT,
                error_msg=f"Unsupported configuration file format: {suffix}",
            )

    def get_config(self, config_type: Type[T]) -> Optional[T]:
        """Get configuration of specified type"""
        for key, config in self._configs.items():
            if isinstance(config, config_type):
                return config
        return None

    def get_knowledge_base_config(self) -> KnowledgeBaseConfig:
        """Get knowledge base configuration"""
        config = self._configs.get("knowledge_base")
        if not config:
            raise build_error(
                StatusCode.RETRIEVAL_UTILS_CONFIG_PROCESS_ERROR, error_msg="Knowledge base configuration not loaded"
            )
        return config

    def update_config(self, config: BaseModel) -> None:
        """Update configuration"""
        type_name = type(config).__name__
        self._configs[type_name] = config
