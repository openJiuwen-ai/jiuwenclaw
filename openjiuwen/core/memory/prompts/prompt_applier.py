# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Prompt applier module for managing and applying prompt templates.

This module provides a singleton class PromptApplier that loads markdown
prompt templates, caches them to avoid frequent I/O, and applies variable
substitution using PromptTemplate.
"""

from pathlib import Path
from typing import Dict, Optional

from openjiuwen.core.common.utils.singleton import Singleton
from openjiuwen.core.foundation.prompt import PromptTemplate
from openjiuwen.core.common.logging import memory_logger


class PromptApplier(metaclass=Singleton):
    """
    Singleton class for managing and applying prompt templates.

    This class caches loaded markdown prompt templates to avoid frequent I/O
    operations, and provides a simple interface for applying variable substitution.

    Usage:
        prompt_applier = PromptApplier()
        result = prompt_applier.apply("user_memory_prompt", {"var": "value"})
    """

    def __init__(self):
        """Initialize the prompt applier (only once due to singleton)."""
        # Cache for loaded prompt templates: {file_prefix: PromptTemplate}
        self._prompt_cache: Dict[str, PromptTemplate] = {}

        # Base directory for prompt markdown files
        self._prompt_dir = Path(__file__).parent

        memory_logger.info("PromptApplier singleton initialized")

    def _get_prompt_file_path(self, file_prefix: str) -> Path:
        """
        Get the full path to a prompt markdown file.

        Args:
            file_prefix: Prefix of the prompt file (without extension)

        Returns:
            Path: Full path to the .md file
        """
        return self._prompt_dir / f"{file_prefix}.md"

    def _load_prompt_template(self, file_prefix: str) -> PromptTemplate:
        """
        Load a prompt template from markdown file, using cache if available.

        Args:
            file_prefix: Prefix of the prompt file (without extension)

        Returns:
            PromptTemplate: The loaded prompt template

        Raises:
            FileNotFoundError: If the prompt file doesn't exist
        """
        # Check cache first
        if file_prefix in self._prompt_cache:
            memory_logger.debug(f"Using cached prompt template: {file_prefix}")
            return self._prompt_cache[file_prefix]

        # Load from file
        file_path = self._get_prompt_file_path(file_prefix)
        if not file_path.exists():
            raise FileNotFoundError(f"Prompt file not found: {file_path}")

        content = file_path.read_text(encoding="utf-8")
        template = PromptTemplate(content=content)

        # Cache the template
        self._prompt_cache[file_prefix] = template
        memory_logger.info(f"Loaded and cached prompt template: {file_prefix}")
        return template

    def apply(self, file_prefix: str, variables: Dict[str, str]) -> str:
        """
        Apply variable substitution to a prompt template and return the result.

        Args:
            file_prefix: Prefix of the prompt file (without extension)
            variables: Dictionary of variable names to values for substitution

        Returns:
            str: The prompt content with variables substituted

        Raises:
            FileNotFoundError: If the prompt file doesn't exist
        """
        template = self._load_prompt_template(file_prefix)
        # Then apply variable substitution
        result = template.format(variables).content
        memory_logger.debug(f"Applied prompt template: {file_prefix}")
        return result

    def clear_cache(self, file_prefix: Optional[str] = None) -> None:
        """
        Clear cached prompt templates.

        Args:
            file_prefix: Specific file prefix to clear, or None to clear all cache
        """
        if file_prefix is None:
            self._prompt_cache.clear()
            memory_logger.info("Cleared all prompt template cache")
        elif file_prefix in self._prompt_cache:
            del self._prompt_cache[file_prefix]
            memory_logger.info(f"Cleared prompt template cache: {file_prefix}")

    def get_template(self, file_prefix: str) -> PromptTemplate:
        """
        Get a cached PromptTemplate without applying variables.

        Args:
            file_prefix: Prefix of the prompt file (without extension)

        Returns:
            PromptTemplate: The cached prompt template

        Raises:
            FileNotFoundError: If the prompt file doesn't exist
        """
        return self._load_prompt_template(file_prefix)
