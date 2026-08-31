"""Apply injected trace header exporters at upstream model call boundaries."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable
from typing import Any, Protocol

from openjiuwen.core.foundation.llm import Model

from .models import InvocationContext, TraceContext
from .runtime import get_current_invocation_context
from .billing_trace import begin_trace_id, mark_model_call, schedule_marker_call


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

    def _with_trace_headers(self, kwargs: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
        """返回 (注入 trace 头后的 kwargs, 需补发 begin 虚拟调用的 core 或 None)。"""
        custom_headers = dict(kwargs.get("custom_headers") or {})
        # 调用方显式携带 x-hag-trace-id 时不覆盖、不改写（计费 begin/终态虚拟调用等；
        # 与 model-proxy 的 hasTraceId 语义一致）
        if any(
            key.lower() == "x-hag-trace-id" and str(value).strip()
            for key, value in custom_headers.items()
        ):
            kwargs["custom_headers"] = custom_headers
            return kwargs, None
        trace_headers = export_trace_headers(
            get_current_invocation_context(),
            self._trace_header_exporters,
        )
        if not trace_headers:
            return kwargs, None
        # 临时计费标记（billing_trace.py，最终方案上线即拆）：真实调用一律携带
        # 裸前缀 xiaoyi-work-<core>；某 core 首次登记时由调用方在真实调用前
        # 补发一次 begin 虚拟调用（见 _fire_begin_marker）。
        core = trace_headers.get("x-hag-trace-id")
        begin_core = None
        if core:
            marked, is_first = mark_model_call(core)
            trace_headers["x-hag-trace-id"] = marked
            if is_first:
                begin_core = core
        kwargs["custom_headers"] = {**custom_headers, **trace_headers}
        return kwargs, begin_core

    def _fire_begin_marker(self, core: str, kwargs: dict[str, Any]) -> bool:
        """一轮 query 首次模型调用登记：派发 begin 虚拟调用（fire-and-forget）。

        返回是否成功派发。派发失败（无运行中事件循环等）时兜底退回旧形态——
        本次真实调用自身携带 begin 前缀，保住轮次起点事件。
        """
        begin = begin_trace_id(core)
        if schedule_marker_call(self, begin):
            return True
        headers = dict(kwargs.get("custom_headers") or {})
        headers["x-hag-trace-id"] = begin
        kwargs["custom_headers"] = headers
        return False

    async def _prepare_call(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """invoke/stream 公共入口：注入 trace 头 + 首呼前派发 begin 虚拟调用。"""
        kwargs, begin_core = self._with_trace_headers(kwargs)
        if begin_core is not None and self._fire_begin_marker(begin_core, kwargs):
            # 让权一次事件循环使 begin 标记先于真实首呼起跑（尽力保序，不阻塞；
            # 严格时序非计费归集前提——各请求均携带同 core 段）
            await asyncio.sleep(0)
        return kwargs

    async def invoke(self, *args: Any, **kwargs: Any) -> Any:
        return await super().invoke(*args, **await self._prepare_call(kwargs))

    async def stream(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        async for chunk in super().stream(*args, **await self._prepare_call(kwargs)):
            yield chunk


__all__ = [
    "TraceAwareModel",
    "TraceHeaderExporter",
    "export_trace_headers",
    "export_trace_headers_for_name",
    "register_trace_header_exporter",
]
