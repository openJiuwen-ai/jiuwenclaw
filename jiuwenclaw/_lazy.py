# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Lazy re-export helper for package ``__init__`` modules.

Avoids eager import of heavy submodules during package import. Each
``__init__.py`` declares which names should resolve lazily and calls
:func:`install_lazy_attrs`; lookups are deferred to first access and then
cached on the module ``__dict__``.
"""

from __future__ import annotations

import importlib
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType


def install_lazy_attrs(
    module: ModuleType,
    lazy_attrs: dict[str, tuple[str, str]],
) -> None:
    package = module.__name__

    def __getattr__(name: str):
        entry = lazy_attrs.get(name)
        if entry is None:
            raise AttributeError(f"module {package!r} has no attribute {name!r}")
        sub_path, attr = entry
        # sub_path may be absolute ("pkg.mod") or relative (".mod"); the
        # latter is resolved against this package via the second arg.
        imported = importlib.import_module(sub_path, package)
        value = getattr(imported, attr)
        setattr(module, name, value)
        return value

    def __dir__():
        return [*lazy_attrs]

    module.__getattr__ = __getattr__
    module.__dir__ = __dir__
    # Merge with any __all__ the caller already declared, so callers can
    # keep an explicit ``__all__ = [...]`` (visible to static analyzers)
    # rather than relying on this function to seed it.
    base_all = list(getattr(module, "__all__", []))
    base_all.extend(lazy_attrs)
    module.__all__ = base_all
