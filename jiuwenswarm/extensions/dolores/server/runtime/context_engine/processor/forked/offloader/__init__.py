"""Lazy exports for the refactored offloader implementation."""

from importlib import import_module

_EXPORT_MODULES = {
    "MessageSummaryOffloader": ".message_offloader",
    "MessageSummaryOffloaderConfig": ".message_offloader",
}

__all__ = list(_EXPORT_MODULES)


def __getattr__(name: str):
    if name not in _EXPORT_MODULES:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(_EXPORT_MODULES[name], __name__)
    value = getattr(module, name)
    globals()[name] = value
    return value
