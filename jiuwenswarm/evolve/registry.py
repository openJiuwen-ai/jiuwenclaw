# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Generic registry for pluggable components.

All evolution pipeline pluggable interfaces (ProposalGenerator,
DecisionPolicy, ApplyWriter, TraceSampler) are registered here via
a simple name-to-class mapping.
"""

from __future__ import annotations

from typing import Generic, TypeVar

T = TypeVar("T")


class Registry(Generic[T]):
    """Generic registry for pluggable component classes.

    Usage::

        proposal_generators = Registry[ProposalGenerator]()

        @proposal_generators.register("llm_proposer")
        class LLMProposer(ProposalGenerator):
            ...
    """

    def __init__(self) -> None:
        self._items: dict[str, type[T]] = {}

    def register(self, name: str, cls: type[T] | None = None):
        """Register a component class under *name*.

        Supports both direct calls and decorator usage::

            # Direct
            reg.register("my_gen", MyGen)

            # Decorator
            @reg.register("my_gen")
            class MyGen:
                ...
        """
        if cls is not None:
            # Direct call: register(name, cls)
            if name in self._items:
                raise ValueError(
                    f"Duplicate registration: '{name}' is already registered"
                )
            self._items[name] = cls
            return cls

        # Decorator usage: @register("name")
        def _decorator(klass: type[T]) -> type[T]:
            if name in self._items:
                raise ValueError(
                    f"Duplicate registration: '{name}' is already registered"
                )
            self._items[name] = klass
            return klass

        return _decorator

    def get(self, name: str) -> type[T]:
        """Look up a component class by *name*.

        Raises:
            KeyError: If *name* is not registered.
        """
        if name not in self._items:
            raise KeyError(
                f"Unknown component: '{name}'. "
                f"Available: {sorted(self._items)}"
            )
        return self._items[name]

    def list(self) -> list[str]:
        """Return all registered component names."""
        return sorted(self._items)

    def __contains__(self, name: str) -> bool:
        return name in self._items


# Global registries for each pluggable dimension.
proposal_generators: Registry = Registry()
decision_policies: Registry = Registry()
apply_writers: Registry = Registry()
trace_samplers: Registry = Registry()
