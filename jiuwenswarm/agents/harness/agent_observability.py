# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Single-agent / coding-agent observability lifecycle.

This is the non-team counterpart of the team observability adapter in
``jiuwenswarm.agents.harness.team.team_manager`` (``sync_team_observability``
/ ``shutdown_team_observability``). It is kept in a **separate file with its
own state and config section** on purpose, so the existing team scenario is
not affected.

Once ``openjiuwen.agent_teams.observability.init_observability`` has run, the
generic ``OtelCallbackHandler`` is registered against the **global**
``Runner.callback_framework``. LLM and tool events are emitted from the shared
foundation layer (``core/foundation/llm/model.py`` /
``core/foundation/tool/base.py``) for *every* agent, team or not — so simply
ensuring the provider is initialized before ``Runner.run_agent_streaming`` /
``Runner.run_agent`` gives single-agent and coding-agent runs automatic
LLM/tool span tracing. The team-only ``OtelTeamMonitorHandler`` (team/member/
task/message spans) is intentionally never attached here.

Shared-provider caveat (important):
    OpenTelemetry allows exactly ONE global ``TracerProvider`` per process,
    and ``init_observability`` is a no-op if already initialized. In a process
    where BOTH team and agent observability are enabled, whichever runs first
    wins; the other silently reuses it (its exporter/endpoint/service_name are
    ignored). To stay safe in that case we track ``_agent_owns_provider``:
    agent shutdown only tears down the provider when the agent actually
    created it, and never tears down a provider the team subsystem depends on.
"""

from __future__ import annotations

import logging
from typing import Any

from jiuwenswarm.common.config import get_config
from jiuwenswarm.common.utils import get_user_workspace_dir

logger = logging.getLogger(__name__)

# ── Single-Agent Observability ─────────────────────────────────
# Tracks whether observability is currently active so we can detect config
# toggles (enabled -> disabled or vice-versa) and init / shutdown accordingly
# on each single-agent request.
_agent_observability_active: bool = False
# True only when THIS module called ``init_observability()`` and therefore owns
# the shared global TracerProvider. When the team subsystem (or a prior run)
# already initialized it, this is False and shutdown must leave it intact.
_agent_owns_provider: bool = False
# Sticky flag: once any single-agent request has force-enabled observability
# (e.g. a ``/debug`` run with ``debug_trace.<mode>.otel_enabled``), we never
# auto-teardown the provider for the rest of the process. OTel allows only one
# global TracerProvider and re-init after shutdown is fragile, so a /debug
# toggle must not churn init/shutdown across alternating requests. The normal
# config-gated path (agent_observability.enabled hot-reload) is unaffected
# unless force was ever used.
_force_ever_enabled: bool = False


def sync_agent_observability(*, force: bool = False) -> None:
    """Synchronize single-agent observability state with current config.

    Called before each ``Runner.run_agent_streaming`` / ``Runner.run_agent`` so
    that hot-reloading the ``agent_observability.enabled`` flag takes effect
    immediately:

    * disabled -> enabled : ``init_observability()`` (or reuse if already up)
    * enabled -> disabled : ``shutdown_agent_observability()``
    * unchanged           : no-op

    ``force=True`` (set by a ``/debug`` run when ``debug_trace.<mode>.otel_enabled``
    is true) treats ``want_enabled`` as true regardless of config, so a debug
    request can pull up OTel even when ``agent_observability.enabled`` is false.
    Once force is ever used, the provider stays up for the process (sticky — see
    ``_force_ever_enabled``) to avoid init/shutdown churn across alternating
    requests; the normal config hot-reload teardown is unchanged otherwise.
    """
    global _agent_observability_active, _agent_owns_provider, _force_ever_enabled

    cfg = get_config().get("agent_observability", {}) or {}
    want_enabled = bool(cfg.get("enabled", False)) or force
    if force:
        _force_ever_enabled = True

    if want_enabled and not _agent_observability_active:
        try:
            from openjiuwen.agent_teams.observability import (
                ObservabilityConfig,
                init_observability,
                is_initialized,
            )

            if is_initialized():
                # Another subsystem (e.g. team) already owns the provider.
                # Reuse it so the global OtelCallbackHandler keeps emitting
                # LLM/tool spans for this single agent too — do NOT re-init.
                _agent_observability_active = True
                _agent_owns_provider = False
                logger.info(
                    "[AgentObservability] reusing existing observability provider "
                    "(owned by another subsystem)"
                )
                return

            obs_cfg = ObservabilityConfig(
                enabled=True,
                service_name=cfg.get("service_name", "jiuwenswarm-agent"),
                exporter=cfg.get("exporter", "otlp_grpc"),
                endpoint=cfg.get("endpoint", "http://localhost:4317"),
                sample_rate=cfg.get("sample_rate", 1.0),
                attribute_value_max_length=cfg.get("attribute_value_max_length", 10240),
                redact_prompts=cfg.get("redact_prompts", False),
                redact_completions=cfg.get("redact_completions", False),
                langfuse_public_key=cfg.get("langfuse_public_key", ""),
                langfuse_secret_key=cfg.get("langfuse_secret_key", ""),
                traces_dir=cfg.get("traces_dir") or str(get_user_workspace_dir() / ".trace"),
                file_retention_days=cfg.get("file_retention_days", 7),
            )
            init_observability(obs_cfg)
            _agent_observability_active = True
            _agent_owns_provider = True
            if obs_cfg.exporter == "file":
                logger.info(
                    "[AgentObservability] enabled: exporter=%s traces_dir=%s",
                    obs_cfg.exporter, obs_cfg.traces_dir,
                )
            else:
                logger.info(
                    "[AgentObservability] enabled: exporter=%s endpoint=%s",
                    obs_cfg.exporter, obs_cfg.endpoint,
                )
        except Exception as exc:
            logger.warning("[AgentObservability] init failed: %s", exc)

    elif not want_enabled and _agent_observability_active and not _force_ever_enabled:
        shutdown_agent_observability()


def shutdown_agent_observability() -> None:
    """Shutdown single-agent observability (on disable or process exit)."""
    global _agent_observability_active, _agent_owns_provider
    if not _agent_observability_active:
        return

    if not _agent_owns_provider:
        # Provider is owned by the team subsystem (or another run); tearing it
        # down here would break team tracing. Just drop our activation flag.
        _agent_observability_active = False
        logger.info(
            "[AgentObservability] disabled (provider owned elsewhere, left intact)"
        )
        return

    try:
        from openjiuwen.agent_teams.observability import shutdown_observability

        shutdown_observability()
        _agent_observability_active = False
        _agent_owns_provider = False
        logger.info("[AgentObservability] disabled")
    except Exception as exc:
        logger.warning("[AgentObservability] shutdown failed: %s", exc)


# ── Per-run root span ───────────────────────────────────────────
# openjiuwen's OtelCallbackHandler skips LLM/tool span creation when no parent
# span exists (``get_team_span`` / ``get_current_agent_span`` both None — see
# callback_handler._get_parent_context_for_llm_tool). Single-agent runs set
# neither, so without a root span zero spans are produced even after a clean
# ``init_observability``. These helpers open a root span and register it via
# ``set_team_span`` — the exact mechanism team mode uses internally
# (team_runner._maybe_attach_observability → get_or_create_team_span). LLM/tool
# spans then nest under it and are exported.
#
# Usage (must be paired, in the same coroutine so the ContextVar propagates
# into the runner's LLM calls):
#     handle = open_agent_run_span(session_id=sid)
#     try:
#         ... Runner.run_agent_streaming / Runner.run_agent ...
#     finally:
#         close_agent_run_span(handle)
def _build_run_span_name(*, mode: str, session_id: str) -> str:
    """Build a hierarchical OTel span name: ``agent.<mode>.<session_id>``.

    ``mode`` is the JiuwenSwarm request mode, shaped ``<category>.<submode>``
    (e.g. ``agent.plan`` / ``agent.fast`` / ``code.normal`` / ``code.plan``),
    so it yields the hierarchy directly:

        agent.plan  -> agent.agent.plan.<session_id>
        code.normal -> agent.code.normal.<session_id>

    Falls back gracefully when either component is empty.
    """
    m = (mode or "").strip()
    sid = (session_id or "").strip()
    if not m:
        return f"agent.run.{sid}" if sid else "agent.run"
    if not sid:
        return f"agent.{m}.run"
    return f"agent.{m}.{sid}"


def open_agent_run_span(*, session_id: str = "", mode: str = "") -> Any:
    """Open a root team span around a single-agent run.

    Returns an opaque handle to pass to :func:`close_agent_run_span`, or
    ``None`` when observability is not initialized (in which case closing is
    a no-op).
    """
    try:
        from opentelemetry.trace import SpanKind

        from openjiuwen.agent_teams.observability import (
            get_tracer,
            is_initialized,
        )
        from openjiuwen.agent_teams.observability.semconv import LANGFUSE_SESSION_ID
        from openjiuwen.agent_teams.observability.span_context import set_team_span

        if not is_initialized():
            return None

        tracer = get_tracer("jiuwenswarm.agent")
        name = _build_run_span_name(mode=mode, session_id=session_id)
        span = tracer.start_span(name=name, kind=SpanKind.SERVER)
        span.set_attribute(LANGFUSE_SESSION_ID, session_id or "")
        # Tag the mode so traces can be filtered in Langfuse without parsing
        # the span name.
        span.set_attribute("jiuwenswarm.mode", mode or "")
        # Register as the team span so OtelCallbackHandler's parent lookup
        # (get_team_span fallback) finds it for LLM/tool span creation.
        set_team_span(span, team_name="single-agent")
        logger.info("[AgentObservability] root span opened: name=%s", name)
        return span
    except Exception as exc:
        logger.warning("[AgentObservability] open root span failed: %s", exc)
        return None


def close_agent_run_span(handle: Any) -> None:
    """End the root span opened by :func:`open_agent_run_span` and clear it."""
    if handle is None:
        return
    try:
        from openjiuwen.agent_teams.observability.span_context import (
            cascade_close_children,
            clear_team_span,
            flush_child_spans,
        )

        # End any still-open child LLM/tool spans (e.g. run aborted mid-call).
        # Two nets are needed for the single-agent path:
        #   1. cascade_close_children — closes spans whose state was pushed on
        #      the _llm_span_stack / _tool_span_map ContextVars in THIS context.
        #   2. flush_child_spans — the SpanProcessor-backed safety net Team mode
        #      relies on (finalize_trace -> flush_child_spans via
        #      ActiveSpanTracker). The single-agent runner opens LLM spans inside
        #      its own child context, so their ContextVar state is not visible
        #      here; the tracker closes them by trace_id regardless of context.
        # Must run BEFORE handle.end()/clear_team_span(): flush_child_spans reads
        # the team span ContextVar to resolve this trace's id, and scopes the
        # close to our trace only (flush_spans_for_trace), so concurrent runs are
        # not affected.
        try:
            cascade_close_children()
        except Exception as exc:
            logger.debug("[AgentObservability] cascade_close_children failed: %s", exc)
        try:
            flush_child_spans()
        except Exception as exc:
            logger.debug("[AgentObservability] flush_child_spans failed: %s", exc)
        try:
            handle.end()
        except Exception as exc:
            logger.debug("[AgentObservability] end root span failed: %s", exc)
        clear_team_span()
    except Exception as exc:
        logger.warning("[AgentObservability] close root span failed: %s", exc)
