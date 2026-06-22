# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Defer openjiuwen.core.memory.long_term_memory import to first use.

openjiuwen's `openjiuwen/core/memory/__init__.py` eagerly imports
`LongTermMemory`, which transitively pulls in alembic migration machinery
(~200ms) and sqlalchemy.ext.asyncio + sql_db_store (~315ms). This chain
contributes ~560ms to AgentServer startup, but `LongTermMemory` is only
used by memory_rail / memory_provider — none of which sit on the AgentServer
startup critical path.

This patch pre-registers `openjiuwen.core.memory` as a stub package that:
- eagerly loads only the cheap `config` submodule (~12ms)
- exposes `LongTermMemory` as a module-level attribute that defers the
  heavy chain until first access

Must be called BEFORE any code triggers the real package import
(i.e. very early in app_agentserver.py, before other openjiuwen imports).
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
import types
from typing import Any


def apply_lazy_memory_patch() -> None:
    pkg_name = "openjiuwen.core.memory"
    if pkg_name in sys.modules:
        # Already imported - too late to patch safely.
        return

    # Locate the package without triggering its __init__.
    try:
        spec = importlib.util.find_spec(pkg_name)
    except (ImportError, ValueError):
        return
    if spec is None or spec.submodule_search_locations is None:
        return

    # Install an empty stub package so that submodule imports
    # (`openjiuwen.core.memory.config`, etc.) skip running the real __init__.
    stub = types.ModuleType(pkg_name)
    # Mark as a package: __path__ must be a list of strings.
    stub.__path__ = list(spec.submodule_search_locations)
    stub.__spec__ = spec
    stub.__package__ = pkg_name
    stub.__all__ = [
        "MemoryEngineConfig",
        "MemoryScopeConfig",
        "AgentMemoryConfig",
        "LongTermMemory",
    ]

    sys.modules[pkg_name] = stub

    # Eagerly load the cheap config submodule so its classes are accessible
    # via the package. config.py itself only imports pydantic + lightweight
    # openjiuwen foundation modules — none of the heavy migration machinery.
    config = importlib.import_module(f"{pkg_name}.config")
    stub.MemoryEngineConfig = config.MemoryEngineConfig
    stub.MemoryScopeConfig = config.MemoryScopeConfig
    stub.AgentMemoryConfig = config.AgentMemoryConfig

    # `LongTermMemory` (and any other attribute defined only in the real
    # __init__) is resolved lazily via __getattr__. Once resolved, the
    # value is written into stub.__dict__ so subsequent accesses bypass
    # __getattr__ entirely — same pattern as jiuwenclaw/_lazy.py.
    #
    # The stub replaces the real package __init__, so any attribute the
    # real __init__ exports that we don't explicitly handle here is
    # unreachable. Fail loudly with context so the next dev doesn't have
    # to reverse-engineer why `from openjiuwen.core.memory import X`
    # silently breaks after the patch is applied.
    def __getattr__(name: str) -> Any:
        if name == "LongTermMemory":
            cached = stub.__dict__.get(name)
            if cached is not None:
                return cached
            ltm_module = importlib.import_module(f"{pkg_name}.long_term_memory")
            setattr(stub, name, ltm_module.LongTermMemory)
            return stub.__dict__[name]
        raise AttributeError(
            f"module {pkg_name!r} has no attribute {name!r}: the package is "
            f"served by a lazy stub that skips the real __init__. If "
            f"{name!r} is defined in openjiuwen.core.memory.__init__, add "
            f"it to apply_lazy_memory_patch or import the submodule "
            f"directly (e.g. `from openjiuwen.core.memory.X import ...`)."
        )

    stub.__getattr__ = __getattr__
