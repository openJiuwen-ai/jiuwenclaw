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
