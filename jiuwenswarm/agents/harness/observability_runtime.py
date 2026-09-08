# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared process-level observability and trajectory processor access."""

from __future__ import annotations

from collections.abc import Mapping
import logging
import threading
from typing import Any

from jiuwenswarm.common.openjiuwen_rail_compat import filter_unsupported_kwargs

logger = logging.getLogger(__name__)

_RUNTIMES = frozenset({"agent", "team"})
_ACTIVE_RUNTIMES: set[str] = set()
_DEMAND_LOCK = threading.RLock()
# Only shut down a provider that this coordinator created. A provider may be
# initialized by an RL collector or another SDK entry point before either
# runtime acquires a demand; those callers retain ownership of its lifecycle.
_PROVIDER_OWNED = False
_TRAJECTORY_SPAN_PROCESSOR: Any | None = None
_TRAJECTORY_UNAVAILABLE = False


def get_trajectory_span_processor() -> Any:
    """Return the trajectory processor shared by all JiuwenClaw runtimes.

    Returns ``None`` when the installed openjiuwen SDK does not ship
    ``TrajectorySpanProcessor``. Callers treat that as optional capture.
    """
    global _TRAJECTORY_SPAN_PROCESSOR, _TRAJECTORY_UNAVAILABLE
    with _DEMAND_LOCK:
        if _TRAJECTORY_UNAVAILABLE:
            return None
        if _TRAJECTORY_SPAN_PROCESSOR is not None:
            return _TRAJECTORY_SPAN_PROCESSOR

        try:
            from openjiuwen.agent_evolving.trajectory.processor import (
                TrajectorySpanProcessor,
            )
        except ModuleNotFoundError:
            _TRAJECTORY_UNAVAILABLE = True
            logger.warning(
                "openjiuwen trajectory processor is unavailable; "
                "evolution span capture is disabled"
            )
            return None

        _TRAJECTORY_SPAN_PROCESSOR = TrajectorySpanProcessor()
        return _TRAJECTORY_SPAN_PROCESSOR


def ensure_trajectory_span_processor_attached() -> bool:
    """Attach the shared trajectory processor to the active OTel provider.

    Idempotent: ``ObservabilityRuntime.add_span_processors`` skips processors
    already registered by object identity. Safe to call from both the unified
    telemetry path and the legacy ``acquire_observability_demand`` path.
    Returns ``True`` when a processor is present on an initialized provider.
    """
    processor = get_trajectory_span_processor()
    if processor is None:
        return False

    from openjiuwen.agent_teams.observability.setup import (
        get_config,
        init_observability,
        is_initialized,
    )

    if not is_initialized():
        return False

    config = get_config()
    if config is None:
        return False

    init_kwargs = {"additional_span_processors": (processor,)}
    compatible_kwargs = filter_unsupported_kwargs(init_observability, init_kwargs)
    dropped = set(init_kwargs) - set(compatible_kwargs)
    if dropped:
        logger.warning(
            "openjiuwen init_observability does not accept %s; "
            "trajectory capture cannot attach on unified path",
            ", ".join(sorted(dropped)),
        )
        return False

    init_observability(config, **compatible_kwargs)
    return True


def build_observability_config(
    config: Mapping[str, Any],
    *,
    service_name: str,
    default_exporter: str = "otlp_grpc",
    default_endpoint: str = "http://localhost:4317",
    traces_dir: str,
) -> Any:
    """Build the SDK config for one runtime without initializing the provider."""
    from openjiuwen.agent_teams.observability import ObservabilityConfig

    return ObservabilityConfig(
        enabled=True,
        service_name=config.get("service_name", service_name),
        exporter=config.get("exporter", default_exporter),
        endpoint=config.get("endpoint", default_endpoint),
        sample_rate=config.get("sample_rate", 1.0),
        attribute_value_max_length=config.get("attribute_value_max_length", 10240),
        redact_prompts=config.get("redact_prompts", False),
        redact_completions=config.get("redact_completions", False),
        langfuse_public_key=config.get("langfuse_public_key", ""),
        langfuse_secret_key=config.get("langfuse_secret_key", ""),
        traces_dir=config.get("traces_dir") or traces_dir,
        file_retention_days=config.get("file_retention_days", 7),
    )


def acquire_observability_demand(
    runtime: str,
    *,
    observability_config: Any,
) -> bool:
    """Acquire one runtime's demand for the shared provider.

    Initialization errors are deliberately propagated so the caller can
    distinguish an optional tracing failure from a required evolution capture
    failure.
    """
    global _PROVIDER_OWNED
    with _DEMAND_LOCK:
        if runtime not in _RUNTIMES:
            raise ValueError(f"unknown observability runtime: {runtime}")

        from openjiuwen.agent_teams.observability import is_initialized

        if runtime in _ACTIVE_RUNTIMES and is_initialized():
            # Provider may have been created by unified telemetry without the
            # trajectory processor; re-ensure attachment on every demand.
            ensure_trajectory_span_processor_attached()
            return True

        from openjiuwen.agent_teams.observability import init_observability

        provider_existed = is_initialized()
        processor = get_trajectory_span_processor()
        extra_processors = (processor,) if processor is not None else ()
        init_kwargs = {"additional_span_processors": extra_processors}
        compatible_kwargs = filter_unsupported_kwargs(init_observability, init_kwargs)
        dropped = set(init_kwargs) - set(compatible_kwargs)
        if dropped:
            logger.warning(
                "openjiuwen init_observability does not accept %s; skipping",
                ", ".join(sorted(dropped)),
            )
        init_observability(observability_config, **compatible_kwargs)
        # When the provider already existed (e.g. unified telemetry), the kwargs
        # above may have been a no-op on older SDKs; force an explicit attach.
        if provider_existed:
            ensure_trajectory_span_processor_attached()
        if not is_initialized():
            raise RuntimeError(
                f"{runtime} observability initialization did not create a provider"
            )
        if not provider_existed:
            _PROVIDER_OWNED = True
        _ACTIVE_RUNTIMES.add(runtime)
        return provider_existed


def release_observability_demand(runtime: str) -> None:
    """Release a runtime demand during process shutdown."""
    global _PROVIDER_OWNED
    with _DEMAND_LOCK:
        if runtime not in _RUNTIMES:
            raise ValueError(f"unknown observability runtime: {runtime}")
        _ACTIVE_RUNTIMES.discard(runtime)
        if not _ACTIVE_RUNTIMES and _PROVIDER_OWNED:
            from openjiuwen.agent_teams.observability import (
                is_initialized,
                shutdown_observability,
            )

            if is_initialized():
                shutdown_observability()
            _PROVIDER_OWNED = False


def reset_observability_demands() -> None:
    """Reset demand bookkeeping for isolated tests."""
    global _PROVIDER_OWNED, _TRAJECTORY_UNAVAILABLE
    with _DEMAND_LOCK:
        _ACTIVE_RUNTIMES.clear()
        _PROVIDER_OWNED = False
        _TRAJECTORY_UNAVAILABLE = False
