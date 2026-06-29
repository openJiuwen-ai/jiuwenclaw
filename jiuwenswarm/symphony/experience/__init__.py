from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .bank import ExperienceBank
    from .distiller import TraceDistiller
    from .cluster import cluster_traces, ClusteredQuery
    from .collector import ExperienceBaseBuilder

from .evaluator import TraceEvaluator
from .models import TraceRecord, DistilledPattern
from .trace import (
    list_session_ids,
    parse_all_sessions,
    parse_session,
    parse_and_store,
    load_all_records,
    clear_store,
)

__all__ = [
    "TraceRecord",
    "DistilledPattern",
    "list_session_ids",
    "parse_session",
    "parse_all_sessions",
    "TraceEvaluator",
    "parse_and_store",
    "load_all_records",
    "clear_store",
    "ExperienceBank",
    "TraceDistiller",
    "cluster_traces",
    "ClusteredQuery",
    "ExperienceBaseBuilder"
]


def __getattr__(name):
    """Lazy import for modules that depend on optional packages (faiss, etc.)."""
    lazy = {
        "ExperienceBank": ".bank",
        "TraceDistiller": ".distiller",
        "cluster_traces": ".cluster",
        "ClusteredQuery": ".cluster",
        "ExperienceBaseBuilder": ".collector"
    }
    if name in lazy:
        import importlib
        mod = importlib.import_module(lazy[name], __package__)
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
