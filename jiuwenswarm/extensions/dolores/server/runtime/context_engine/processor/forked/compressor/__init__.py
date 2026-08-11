"""Lazy exports for refactored compressor implementations."""

from importlib import import_module

_EXPORT_MODULES = {
    "CurrentRoundCompressor": ".current_round_compressor",
    "CurrentRoundCompressorConfig": ".current_round_compressor",
    "DialogueCompressor": ".dialogue_compressor",
    "DialogueCompressorConfig": ".dialogue_compressor",
    "RoundLevelCompressor": ".round_level_compressor",
    "RoundLevelCompressorConfig": ".round_level_compressor",
}

__all__ = list(_EXPORT_MODULES)


def __getattr__(name: str):
    if name not in _EXPORT_MODULES:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(_EXPORT_MODULES[name], __name__)
    value = getattr(module, name)
    globals()[name] = value
    return value
