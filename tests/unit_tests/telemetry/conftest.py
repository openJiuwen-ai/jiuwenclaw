# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared pytest fixtures for telemetry unit tests.

This conftest.py ensures OpenTelemetry providers are properly initialized
before any instrumentor modules are imported, preventing RecursionError
from module-level `_tracer = trace.get_tracer()` calls.

Key insight: OpenTelemetry's global _TRACER_PROVIDER can get into a broken
state between tests. We MUST always force a fresh TracerProvider for each test.
"""

from __future__ import annotations

import pytest
from opentelemetry import trace, metrics
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader


# Global state for fixtures
_INITIALIZED = False
_current_exporter = None
_current_reader = None


def _force_set_tracer_provider(tp):
    """Force set the global tracer provider, bypassing the warning."""
    import opentelemetry.trace as trace_mod
    trace_mod._TRACER_PROVIDER = tp  # pylint: disable=protected-access


def _force_set_meter_provider(mp):
    """Force set the global meter provider, bypassing the warning."""
    import opentelemetry.metrics as metrics_mod
    metrics_mod._METER_PROVIDER = mp  # pylint: disable=protected-access


@pytest.fixture(autouse=True, scope="function")
def _reset_otel_providers():
    """Auto-use fixture that ALWAYS resets OTel providers before each test.

    This is critical because:
    1. Some tests call tp.shutdown() which corrupts the global state
    2. OTel's ProxyTracerProvider can get into infinite recursion if _provider isn't set
    3. We need a fresh, valid TracerProvider for each test
    """
    # Create fresh providers for this test
    exporter = InMemorySpanExporter()
    tp = TracerProvider()
    tp.add_span_processor(SimpleSpanProcessor(exporter))

    reader = InMemoryMetricReader()
    mp = MeterProvider(metric_readers=[reader])

    # Force set the global providers (bypass the "Overriding is not allowed" warning)
    _force_set_tracer_provider(tp)
    _force_set_meter_provider(mp)

    global _INITIALIZED, _current_exporter, _current_reader
    _INITIALIZED = True
    _current_exporter = exporter
    _current_reader = reader

    yield


@pytest.fixture
def otel_providers(_reset_otel_providers):
    """Create and set up OTel providers for testing.

    Returns a tuple of (TracerProvider, MeterProvider, span_exporter, metric_reader).
    This fixture now depends on the autouse reset fixture.
    """
    # Providers are already reset by autouse fixture, use stored exporter/reader
    tp = trace.get_tracer_provider()
    mp = metrics.get_meter_provider()

    yield tp, mp, _current_exporter, _current_reader

    _current_exporter.clear()


@pytest.fixture
def span_exporter(_reset_otel_providers):
    """Provide an in-memory span exporter for testing."""
    yield _current_exporter
    _current_exporter.clear()
