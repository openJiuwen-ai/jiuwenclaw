# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Set


class KvPrefixRegistry:
    """
    Registry for managing KV store key prefixes used by memory modules.
    
    This registry allows memory modules to register their key prefixes,
    enabling KV migrator to dynamically detect which prefixes are in use
    without hardcoding them. This ensures that when modules add or remove prefixes
    during version evolution, migrator can automatically adapt.
    
    Key Features:
    - Support multiple prefixes per module (current and legacy)
    
    Usage:
        # Register current prefix
        kv_prefix_registry.register_current("UMD_NEW")
        
        # Register legacy prefix (for migration detection)
        kv_prefix_registry.register_legacy("UMD")
        
        # Get all prefixes (both current and legacy)
        all_prefixes = kv_prefix_registry.get_all_prefixes()
    """
    
    def __init__(self) -> None:
        self._all_prefixes: Set[str] = set()
        self._current_prefixes: Set[str] = set()
    
    def register_current(self, prefix: str) -> None:
        """
        Register a current (active) key prefix used by a memory module.
        
        Args:
            prefix: The current key prefix used by module

        Raises:
            ValueError: If prefix is empty or contains only whitespace characters
        """
        if not prefix or not prefix.strip():
            raise ValueError(f"Prefix cannot be empty or contain only whitespace characters: '{prefix}'")
        if prefix not in self._current_prefixes:
            self._current_prefixes.add(prefix)
            self._all_prefixes.add(prefix)
    
    def register_legacy(self, prefix: str) -> None:
        """
        Register a legacy (deprecated) key prefix for migration detection.
        
        Args:
            prefix: The legacy key prefix that may still exist in data

        Raises:
            ValueError: If prefix is empty or contains only whitespace characters
        """
        if not prefix or not prefix.strip():
            raise ValueError(f"Prefix cannot be empty or contain only whitespace characters: '{prefix}'")
        if prefix not in self._all_prefixes:
            self._all_prefixes.add(prefix)
    
    def get_all_prefixes(self) -> Set[str]:
        """
        Get all registered prefixes (both current and legacy).
        
        This is used by migrator to detect any data that needs migration.
        
        Returns:
            Set[str]: A set containing all registered prefixes
        """
        return self._all_prefixes.copy()

    def unregister(self, prefix: str) -> None:
        """
        Unregister a prefix from both current and all prefixes.

        Args:
            prefix: The prefix to unregister
        """
        self._all_prefixes.discard(prefix)
        self._current_prefixes.discard(prefix)


kv_prefix_registry = KvPrefixRegistry()
