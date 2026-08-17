"""Apply injected trace header exporters at upstream model call boundaries."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from typing import Any, Protocol

from openjiuwen.core.foundation.llm import Model

from .models import InvocationContext, TraceContext
from .runtime import get_current_invocation_context


class TraceHeaderExporter(Protocol):
    """Platform adapter that exports approved headers for one invocation."""

    def supports(self, invocation: InvocationContext) -> bool: ...

    def export(self, trace: TraceContext) -> dict[str, str]: ...


_REGISTERED_TRACE_HEADER_EXPORTERS: dict[str, TraceHeaderExporter] = {}


def register_trace_header_exporter(name: str, exporter: TraceHeaderExporter) -> None:
    """Register one platform adapter at the server composition boundary."""
    normalized = str(name or "").strip().lower()
    if not normalized:
        raise ValueError("exporter name is required")
    _REGISTERED_TRACE_HEADER_EXPORTERS[normalized] = exporter


def export_trace_headers_for_name(
    trace: TraceContext | None,
    exporter_name: str | None,
) -> dict[str, str]:
    """Export headers through the named platform adapter, if registered."""
    exporter = _REGISTERED_TRACE_HEADER_EXPORTERS.get(
        str(exporter_name or "").strip().lower()
    )
    return dict(exporter.export(trace)) if trace is not None and exporter is not None else {}


def export_trace_headers(
    invocation: InvocationContext | None,
    exporters: Iterable[TraceHeaderExporter],
) -> dict[str, str]:
    """Export the active trace through exporters selected by composition."""
    if invocation is None or invocation.trace is None:
        return {}
    for exporter in exporters:
        if exporter.supports(invocation):
            return dict(exporter.export(invocation.trace))
    return {}


class TraceAwareModel(Model):
    """Model subclass that adds headers from explicitly injected exporters."""

    def __init__(
        self,
        *args: Any,
        trace_header_exporters: Iterable[TraceHeaderExporter] = (),
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._trace_header_exporters = tuple(trace_header_exporters)

    def _with_trace_headers(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        trace_headers = export_trace_headers(
            get_current_invocation_context(),
            self._trace_header_exporters,
        )
        if not trace_headers:
            return kwargs
        custom_headers = dict(kwargs.get("custom_headers") or {})
        kwargs["custom_headers"] = {**custom_headers, **trace_headers}
        return kwargs

    async def invoke(self, *args: Any, **kwargs: Any) -> Any:
        return await super().invoke(*args, **self._with_trace_headers(kwargs))

    async def stream(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        async for chunk in super().stream(*args, **self._with_trace_headers(kwargs)):
            yield chunk


__all__ = [
    "TraceAwareModel",
    "TraceHeaderExporter",
    "export_trace_headers",
    "export_trace_headers_for_name",
    "register_trace_header_exporter",
]
