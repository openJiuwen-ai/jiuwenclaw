"""Refactored context processors with explicit same-name registration."""

from importlib import import_module

from jiuwenswarm.extensions.dolores.server.runtime.context_engine import ContextEngine

_EXPORT_MODULES = {
    "CurrentRoundCompressor": ".compressor.current_round_compressor",
    "CurrentRoundCompressorConfig": ".compressor.current_round_compressor",
    "DialogueCompressor": ".compressor.dialogue_compressor",
    "DialogueCompressorConfig": ".compressor.dialogue_compressor",
    "MessageSummaryOffloader": ".offloader.message_offloader",
    "MessageSummaryOffloaderConfig": ".offloader.message_offloader",
    "RoundLevelCompressor": ".compressor.round_level_compressor",
    "RoundLevelCompressorConfig": ".compressor.round_level_compressor",
}

__all__ = list(_EXPORT_MODULES)


def activate() -> None:
    """Register the refactored processors under the official processor names."""
    for class_name in (
        "MessageSummaryOffloader",
        "DialogueCompressor",
        "CurrentRoundCompressor",
        "RoundLevelCompressor",
    ):
        module = import_module(_EXPORT_MODULES[class_name], __name__)
        processor_class = getattr(module, class_name)
        ContextEngine.register_processor()(processor_class)


def __getattr__(name: str):
    if name not in _EXPORT_MODULES:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    activate()
    module = import_module(_EXPORT_MODULES[name], __name__)
    value = getattr(module, name)
    globals()[name] = value
    return value
