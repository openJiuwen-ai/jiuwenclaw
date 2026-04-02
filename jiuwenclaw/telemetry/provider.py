# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""TracerProvider and MeterProvider initialization with OTLP/Console exporters."""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any

from opentelemetry import trace, metrics
from opentelemetry.sdk.resources import Resource, SERVICE_NAME
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    ConsoleMetricExporter,
    PeriodicExportingMetricReader,
)

from jiuwenclaw.telemetry.attributes import JIUWENCLAW_CLAW_ID
from jiuwenclaw.telemetry.config import TelemetryConfig


@dataclass
class ProviderBundle:
    tracer_provider: TracerProvider | None = None
    meter_provider: MeterProvider | None = None


def init_providers(cfg: TelemetryConfig) -> ProviderBundle:
    """Initialize OTel providers using default config or a custom provider factory."""
    if cfg.provider_factory:
        factory = load_provider_factory(cfg.provider_factory)
        bundle = _coerce_provider_bundle(factory())
        if bundle.tracer_provider is None and bundle.meter_provider is None:
            raise ValueError("Custom provider factory must return at least one provider")
    else:
        bundle = build_default_providers(cfg)

    install_providers(bundle)
    return bundle


def build_default_providers(cfg: TelemetryConfig) -> ProviderBundle:
    """Build the default OTel TracerProvider + MeterProvider bundle."""
    resource_attrs = {
        SERVICE_NAME: cfg.service_name,
        "service.version": "0.1.5",
    }
    if cfg.claw_id is not None:
        resource_attrs[JIUWENCLAW_CLAW_ID] = cfg.claw_id

    resource = Resource.create(resource_attrs)

    tracer_provider = _build_tracer_provider(cfg, resource)
    meter_provider = _build_meter_provider(cfg, resource)
    return ProviderBundle(
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
    )


def install_providers(bundle: ProviderBundle) -> None:
    """Install OTel providers into the global SDK state."""
    if bundle.tracer_provider is not None:
        trace.set_tracer_provider(bundle.tracer_provider)
    if bundle.meter_provider is not None:
        metrics.set_meter_provider(bundle.meter_provider)


def load_provider_factory(path: str):
    """Load a custom provider factory from `module:function` path."""
    module_name, sep, attr_name = path.rpartition(":")
    if not sep or not module_name or not attr_name:
        raise ValueError(
            f"Invalid OTEL_PROVIDER_FACTORY '{path}', expected format 'module:function'"
        )

    module = importlib.import_module(module_name)
    factory = getattr(module, attr_name, None)
    if not callable(factory):
        raise TypeError(f"Provider factory '{path}' is not callable")
    return factory


def _coerce_provider_bundle(value: Any) -> ProviderBundle:
    if isinstance(value, ProviderBundle):
        return value

    tracer_provider = getattr(value, "tracer_provider", None)
    meter_provider = getattr(value, "meter_provider", None)
    if tracer_provider is None and meter_provider is None:
        raise TypeError("Provider factory must return a ProviderBundle-like object")
    return ProviderBundle(
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
    )


def _build_tracer_provider(cfg: TelemetryConfig, resource: Resource) -> TracerProvider:
    tracer_provider = TracerProvider(resource=resource)
    exporter = cfg.traces_exporter

    if exporter == "otlp":
        span_exporter = _create_otlp_span_exporter(cfg, signal="traces")
        tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
    elif exporter == "console":
        tracer_provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    elif exporter != "none":
        raise ValueError(f"Unsupported traces exporter: {exporter}")

    return tracer_provider


def _build_meter_provider(cfg: TelemetryConfig, resource: Resource) -> MeterProvider:
    metric_readers = []
    exporter = cfg.metrics_exporter

    if exporter == "otlp":
        metric_exporter = _create_otlp_metric_exporter(cfg, signal="metrics")
        metric_readers.append(
            PeriodicExportingMetricReader(metric_exporter, export_interval_millis=30000)
        )
    elif exporter == "console":
        metric_readers.append(
            PeriodicExportingMetricReader(ConsoleMetricExporter(), export_interval_millis=30000)
        )
    elif exporter != "none":
        raise ValueError(f"Unsupported metrics exporter: {exporter}")

    return MeterProvider(resource=resource, metric_readers=metric_readers)


def _create_otlp_span_exporter(cfg: TelemetryConfig, signal: str = "traces"):
    """Create OTLP span exporter based on protocol config."""
    protocol = getattr(cfg, f"{signal}_protocol")
    endpoint = getattr(cfg, f"{signal}_endpoint")
    headers = getattr(cfg, f"{signal}_headers")
    if protocol == "http":
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        return OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces", headers=headers)
    else:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        return OTLPSpanExporter(endpoint=endpoint, headers=headers)


def _create_otlp_metric_exporter(cfg: TelemetryConfig, signal: str = "metrics"):
    """Create OTLP metric exporter based on protocol config."""
    protocol = getattr(cfg, f"{signal}_protocol")
    endpoint = getattr(cfg, f"{signal}_endpoint")
    headers = getattr(cfg, f"{signal}_headers")
    if protocol == "http":
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        return OTLPMetricExporter(endpoint=f"{endpoint}/v1/metrics", headers=headers)
    else:
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
        return OTLPMetricExporter(endpoint=endpoint, headers=headers)
