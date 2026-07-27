"""Tool tree indexing subsystem — progressive retrieval for registered tools.

Built on the same indexing + retrieval engine as the Skill (Symphony) pipeline.
The only adapter is ToolScanner, which reads from in-memory ToolCard dicts
instead of SKILL.md files.

.. note::
    ``api`` imports vendored indexing code (via ``dispatch_import_path``), so it
    is imported lazily.  ``ToolScanner`` and ``ScannedItem`` are always safe.
"""

from .scanner import ScannedItem, ToolInventory, ToolScanner


def _lazy_api():
    """Lazy-import the API module (pulls in vendored indexing code)."""
    from . import api as _api
    return _api


def build_tool_index(*args, **kwargs):
    return _lazy_api().build_tool_index(*args, **kwargs)


def tool_index_status(*args, **kwargs):
    return _lazy_api().tool_index_status(*args, **kwargs)


def tool_index_tree(*args, **kwargs):
    return _lazy_api().tool_index_tree(*args, **kwargs)


__all__ = [
    "ToolScanner",
    "ScannedItem",
    "ToolInventory",
    "build_tool_index",
    "tool_index_status",
    "tool_index_tree",
]
