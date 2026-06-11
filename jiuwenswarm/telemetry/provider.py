# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""TracerProvider and MeterProvider initialization with OTLP/Console/SQLite exporters."""

from __future__ import annotations

from dataclasses import dataclass

from opentelemetry import trace, metrics
from opentelemetry.sdk.resources import Resource, SERVICE_NAME
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter, SimpleSpanProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    AggregationTemporality,
    ConsoleMetricExporter,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.metrics._internal.instrument import (
    Counter,
    Histogram,
    ObservableCounter,
    ObservableGauge,
    ObservableUpDownCounter,
    UpDownCounter,
)

_CUMULATIVE_TEMPORALITY = {
    Counter: AggregationTemporality.CUMULATIVE,
    UpDownCounter: AggregationTemporality.CUMULATIVE,
    Histogram: AggregationTemporality.CUMULATIVE,
    ObservableCounter: AggregationTemporality.CUMULATIVE,
    ObservableUpDownCounter: AggregationTemporality.CUMULATIVE,
    ObservableGauge: AggregationTemporality.CUMULATIVE,
}

from jiuwenswarm.telemetry.config import TelemetryConfig


@dataclass
class ProviderBundle:
    tracer_provider: TracerProvider | None = None
    meter_provider: MeterProvider | None = None
    resource: Resource | None = None


def init_providers(cfg: TelemetryConfig) -> ProviderBundle:
    """Initialize OTel providers."""
    bundle = build_default_providers(cfg)
    install_providers(bundle)
    return bundle


def build_default_providers(cfg: TelemetryConfig) -> ProviderBundle:
    """Build the default OTel TracerProvider + MeterProvider bundle."""
    resource_attrs = {
        SERVICE_NAME: cfg.service_name,
        "service.version": "0.1.0",
    }

    resource = Resource(resource_attrs)

    tracer_provider = _build_tracer_provider(cfg, resource)
    meter_provider = _build_meter_provider(cfg, resource)
    return ProviderBundle(
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
        resource=resource,
    )


def install_providers(bundle: ProviderBundle) -> None:
    """Install OTel providers into the global SDK state."""
    if bundle.tracer_provider is not None:
        trace.set_tracer_provider(bundle.tracer_provider)
    if bundle.meter_provider is not None:
        metrics.set_meter_provider(bundle.meter_provider)


def _build_tracer_provider(cfg: TelemetryConfig, resource: Resource) -> TracerProvider:
    tracer_provider = TracerProvider(resource=resource)
    exporter = cfg.traces_exporter

    if exporter == "otlp":
        span_exporter = _create_otlp_span_exporter(cfg, signal="traces")
        tracer_provider.add_span_processor(
            BatchSpanProcessor(span_exporter, max_export_batch_size=256)
        )
    elif exporter == "sqlite":
        from jiuwenswarm.telemetry.sqlite_exporter import SQLiteSpanExporter
        tracer_provider.add_span_processor(
            BatchSpanProcessor(
                SQLiteSpanExporter(db_path=cfg.sqlite_db_path),
                max_export_batch_size=256,
            )
        )
    elif exporter == "console":
        tracer_provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
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
            PeriodicExportingMetricReader(ConsoleMetricExporter(), export_interval_millis=3000)
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
        return OTLPMetricExporter(
            endpoint=f"{endpoint}/v1/metrics",
            headers=headers,
            preferred_temporality=_CUMULATIVE_TEMPORALITY,
        )
    else:
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
        return OTLPMetricExporter(
            endpoint=endpoint,
            headers=headers,
            preferred_temporality=_CUMULATIVE_TEMPORALITY,
        )
