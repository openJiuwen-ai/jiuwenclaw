# -*- coding: UTF-8 -*-
"""Runtime context for storing execution state during operation execution."""

from typing import Any, Dict, Optional


class RuntimeContext:
    """Context object passed between operations in a flow.

    This stores intermediate results and allows operations to communicate
    with each other during execution.
    """

    def __init__(self):
        """Initialize empty context."""
        self._data: Dict[str, Any] = {}

    def get(self, key: str, default: Any = None) -> Any:
        """Get a value from context.

        Args:
            key: Context key
            default: Default value if key not found

        Returns:
            Value from context or default
        """
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set a value in context.

        Args:
            key: Context key
            value: Value to store
        """
        self._data[key] = value

    def __getattr__(self, name: str) -> Any:
        """Allow attribute-style access to context values.

        Args:
            name: Attribute name

        Returns:
            Value from context

        Raises:
            AttributeError: If key not found
        """
        if name.startswith("_"):
            raise AttributeError(f"'{type(self).__name__}' has no attribute '{name}'")

        try:
            return self._data[name]
        except KeyError:
            raise AttributeError(f"Context has no attribute '{name}'") from None

    def __setattr__(self, name: str, value: Any) -> None:
        """Allow attribute-style setting of context values.

        Args:
            name: Attribute name
            value: Value to store
        """
        if name.startswith("_"):
            # Allow setting private attributes normally
            super().__setattr__(name, value)
        else:
            self._data[name] = value

    def to_dict(self) -> Dict[str, Any]:
        """Convert context to dictionary.

        Returns:
            Dictionary representation of context
        """
        return self._data.copy()

    def __repr__(self) -> str:
        """String representation of context."""
        return f"RuntimeContext({self._data})"
