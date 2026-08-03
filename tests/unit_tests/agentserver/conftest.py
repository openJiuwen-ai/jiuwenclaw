# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared fixtures and import stubs for agentserver unit tests.

The installed ``openjiuwen`` package (0.1.10) is missing several submodules
that the codebase now imports (``qa_artifact``, ``qa_block``, ``todo_resume``,
etc.).  Without these stubs, collection of most test files fails with
``ModuleNotFoundError``.  This is a pre-existing environment gap unrelated to
any individual feature change.
"""

from __future__ import annotations

import importlib.abc
import importlib.machinery
import sys
import types
from enum import Enum
from importlib.util import spec_from_file_location, module_from_spec
from pathlib import Path

import pytest

# Patch TaskStatus onto openjiuwen.harness.schema.task when the installed build omits it.
try:
    import openjiuwen as _openjiuwen

    _task_py = (
        Path(_openjiuwen.__file__).resolve().parent
        / "harness"
        / "schema"
        / "task.py"
    )
    if _task_py.is_file():
        _spec = spec_from_file_location("openjiuwen.harness.schema.task", _task_py)
        if _spec and _spec.loader:
            _task_schema = module_from_spec(_spec)
            _spec.loader.exec_module(_task_schema)
            if not hasattr(_task_schema, "TaskStatus"):

                class TaskStatus(str, Enum):
                    PENDING = "pending"
                    IN_PROGRESS = "in_progress"
                    COMPLETED = "completed"
                    FAILED = "failed"

                _task_schema.TaskStatus = TaskStatus
            sys.modules["openjiuwen.harness.schema.task"] = _task_schema
except Exception:
    import logging

    logging.getLogger(__name__).debug(
        "optional openjiuwen.harness.schema.task TaskStatus patch skipped",
        exc_info=True,
    )


# ---------------------------------------------------------------------------
# Stub finder for missing openjiuwen.* submodules
# ---------------------------------------------------------------------------


class _Sentinel:
    """Generic stand-in for missing classes / functions / constants."""

    def __init__(self, *a, **kw):
        pass

    def __call__(self, *a, **kw):
        return self

    @staticmethod
    def __getattr__(name):
        return _Sentinel()

    @staticmethod
    def __bool__():
        return False

    @staticmethod
    def __iter__():
        return iter([])


def _stub_module_getattr(name):
    return _Sentinel


class _OpenJiuwenStubLoader(importlib.abc.Loader):
    """Loader that creates stub modules with auto-attribute access."""

    def create_module(self, spec):
        mod = types.ModuleType(spec.name)
        mod.__path__ = []
        mod.__loader__ = self
        mod.__spec__ = spec
        setattr(mod, "__getattr__", _stub_module_getattr)
        return mod

    def exec_module(self, module):
        pass  # stub needs no execution


class _OpenJiuwenStubFinder(importlib.abc.MetaPathFinder):
    """Meta path finder of last resort for openjiuwen.* submodules.

    Placed at the *end* of ``sys.meta_path`` so real modules are found first.
    Only kicks in when no other finder can locate the module.
    """

    _loader = _OpenJiuwenStubLoader()

    def find_spec(self, fullname, path, target=None):
        if not fullname.startswith("openjiuwen."):
            return None
        if fullname in sys.modules:
            return None  # already loaded (real or stub)
        return importlib.machinery.ModuleSpec(
            fullname, self._loader, is_package=True
        )


if not any(isinstance(f, _OpenJiuwenStubFinder) for f in sys.meta_path):
    sys.meta_path.append(_OpenJiuwenStubFinder())


@pytest.fixture(autouse=True)
def _reset_request_scoped_tenant_bindings():
    """Prevent ContextVar leakage across agentserver unit tests."""
    from jiuwenclaw.agentserver.tenant_context import clear_tenant_bindings

    try:
        from jiuwenclaw.agentserver.tools.memory_tools import clear_memory_workspace_binding
    except (ImportError, ModuleNotFoundError):
        def clear_memory_workspace_binding() -> None:
            return None

    clear_tenant_bindings()
    clear_memory_workspace_binding()
    yield
    clear_tenant_bindings()
    clear_memory_workspace_binding()
