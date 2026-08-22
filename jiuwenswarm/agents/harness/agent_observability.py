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
from dataclasses import dataclass, field
from threading import Event, Lock
from typing import TYPE_CHECKING, Any

from openjiuwen.harness.rails.base import DeepAgentRail

from jiuwenswarm.common.config import get_config
from jiuwenswarm.common.utils import get_user_workspace_dir

if TYPE_CHECKING:
    from jiuwenswarm.telemetry.request_context import (
        TraceBindingHandle,
        TraceBindingRegistry,
    )
    from jiuwenswarm.telemetry.runtime import TelemetryRuntime

logger = logging.getLogger(__name__)

# ── Single-Agent Observability ─────────────────────────────────
# Tracks whether observability is currently active so we can detect config
# toggles (enabled -> disabled or vice-versa) and init / shutdown accordingly
# on each single-agent request.
_agent_observability_active: bool = False

# get_team_span bindings already wrapped by _install_team_span_registry_fallback,
# keyed on the wrapper itself (it becomes the next lookup's orig) so repeat
# calls stay idempotent without stamping an attribute on the function object.
_team_span_patched: set[Any] = set()


class AgentTraceBindingRail(DeepAgentRail):
    """Restore the request root inside the long-lived agent supervisor task."""

    @staticmethod
    def _bind(ctx: Any) -> None:
        session = getattr(ctx, "session", None)
        getter = getattr(session, "get_session_id", None)
        if not callable(getter):
            return
        try:
            session_id = str(getter() or "")
            binding = _get_unified_runtime().trace_bindings.resolve_session(session_id)
            if binding is None:
                return
            root_span = binding.root_span
            is_recording = getattr(root_span, "is_recording", None)
            if callable(is_recording) and not is_recording():
                return
            from openjiuwen.agent_teams.observability.span_context import (
                set_team_span,
            )

            set_team_span(root_span, team_name="single-agent")
        except Exception as exc:
            logger.debug("[AgentObservability] trace binding rail skipped: %s", exc)

    async def before_task_iteration(self, ctx: Any) -> None:
        self._bind(ctx)

    async def before_invoke(self, ctx: Any) -> None:
        self._bind(ctx)


def _get_unified_runtime() -> TelemetryRuntime:
    from jiuwenswarm.telemetry import get_telemetry_runtime

    return get_telemetry_runtime()


def _install_team_span_registry_fallback() -> None:
    """Patch AgentCore consumers to resolve cross-task roots by session.

    Each SDK consumer did ``from span_context import get_team_span``, so each has
    its own binding that must be rebound separately:
      * callback_handler — creates llm/tool spans (parent lookup).
      * rail (ObservabilityRail) — creates the agent.<type>.invoke spans; it
        *returns early* when get_team_span() is None, which is why the agent-tier
        spans (incl. sub-agent's agent.<type>.invoke) were missing.
      * monitor_handler — team-only (harmless to patch; team_span is ContextVar-visible
        there so the fallback never triggers).
    Team mode is unaffected: its team_span is ContextVar-visible, so the original
    lookup returns non-None and the fallback never triggers. The centralized
    registry isolates concurrent sessions and uses compare-and-remove cleanup.
    Best-effort, idempotent, never raises.
    """
    import importlib

    for mod_path in (
        "openjiuwen.agent_teams.observability.callback_handler",
        "openjiuwen.agent_teams.observability.rail",
        "openjiuwen.agent_teams.observability.monitor_handler",
    ):
        try:
            mod = importlib.import_module(mod_path)
        except Exception as exc:
            logger.debug(
                "[AgentObservability] skip team-span fallback patch for %s: %s",
                mod_path,
                exc,
            )
            continue
        orig = getattr(mod, "get_team_span", None)
        if orig is None or orig in _team_span_patched:
            continue

        def _get_team_span_with_registry(team_name=None, _orig=orig):  # type: ignore[no-untyped-def]
            span = _orig(team_name)
            if span is not None:
                return span
            try:
                from openjiuwen.agent_teams.context import get_session_id

                session_id = str(get_session_id() or "")
                if not session_id:
                    return None
                binding = _get_unified_runtime().trace_bindings.resolve_session(
                    session_id
                )
                if binding is None:
                    return None
                root_span = binding.root_span
                # The single-agent supervisor task is created before any
                # request, so it cannot inherit the request ContextVar. Cache
                # the registry fallback in this task after AGENT_*_INPUT; the
                # later LLM/tool callbacks can then parent spans without a
                # process-global current-request shortcut.
                from openjiuwen.agent_teams.observability.span_context import (
                    set_team_span,
                )

                set_team_span(root_span, team_name="single-agent")
                return root_span
            except Exception as exc:
                logger.debug("[AgentObservability] session root lookup failed: %s", exc)
                return None

        _team_span_patched.add(_get_team_span_with_registry)
        mod.get_team_span = _get_team_span_with_registry


_install_team_span_registry_fallback()
# True only when THIS module called ``init_observability()`` and therefore owns
# the shared global TracerProvider. When the team subsystem (or a prior run)
# already initialized it, this is False and shutdown must leave it intact.
_agent_owns_provider: bool = False
_runtime_managed_agent_observability: bool = False
# Sticky flag: once any single-agent request has force-enabled observability
# (e.g. a ``/debug`` run with ``debug_trace.<mode>.otel_enabled``), we never
# auto-teardown the provider for the rest of the process. OTel allows only one
# global TracerProvider and re-init after shutdown is fragile, so a /debug
# toggle must not churn init/shutdown across alternating requests. The normal
# config-gated path (agent_observability.enabled hot-reload) is unaffected
# unless force was ever used.
_force_ever_enabled: bool = False


def sync_agent_observability(
    *,
    force: bool = False,
    config_base: dict[str, Any] | None = None,
) -> None:
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
    global _agent_observability_active, _agent_owns_provider
    global _force_ever_enabled, _runtime_managed_agent_observability

    try:
        unified_active = bool(_get_unified_runtime().is_unified_active())
    except Exception as exc:
        logger.debug("[AgentObservability] unified runtime lookup failed: %s", exc)
        unified_active = False
    if unified_active:
        _agent_observability_active = True
        _agent_owns_provider = False
        _runtime_managed_agent_observability = True
        return
    if force:
        _force_ever_enabled = True
    if _runtime_managed_agent_observability:
        _agent_observability_active = False
        _runtime_managed_agent_observability = False

    if config_base is None:
        config_base = get_config()
    cfg = config_base.get("agent_observability", {}) or {}
    want_enabled = bool(cfg.get("enabled", False)) or force

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
                traces_dir=cfg.get("traces_dir")
                or str(get_user_workspace_dir() / ".trace"),
                file_retention_days=cfg.get("file_retention_days", 7),
            )
            init_observability(obs_cfg)
            _agent_observability_active = True
            _agent_owns_provider = True
            if obs_cfg.exporter == "file":
                logger.info(
                    "[AgentObservability] enabled: exporter=%s traces_dir=%s",
                    obs_cfg.exporter,
                    obs_cfg.traces_dir,
                )
            else:
                logger.info(
                    "[AgentObservability] enabled: exporter=%s endpoint=%s",
                    obs_cfg.exporter,
                    obs_cfg.endpoint,
                )
        except Exception as exc:
            logger.warning("[AgentObservability] init failed: %s", exc)

    elif not want_enabled and _agent_observability_active and not _force_ever_enabled:
        shutdown_agent_observability()


def shutdown_agent_observability() -> None:
    """Shutdown single-agent observability (on disable or process exit)."""
    global _agent_observability_active, _agent_owns_provider
    global _runtime_managed_agent_observability
    if not _agent_observability_active:
        return

    if _runtime_managed_agent_observability:
        _agent_observability_active = False
        _agent_owns_provider = False
        _runtime_managed_agent_observability = False
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
@dataclass(frozen=True)
class AgentRunSpanHandle:
    """Root span plus its compare-and-remove registry ownership."""

    root_span: Any
    binding: TraceBindingHandle | None
    trace_bindings: TraceBindingRegistry | None
    unified: bool
    _close_lock: Lock = field(default_factory=Lock, repr=False, compare=False)
    _closed: Event = field(default_factory=Event, repr=False, compare=False)

    def get_span_context(self) -> Any:
        """Preserve the legacy opaque handle's span-context convenience."""
        return self.root_span.get_span_context()

    def claim_close(self) -> bool:
        """Atomically claim the one allowed close for this root span."""
        with self._close_lock:
            if self._closed.is_set():
                return False
            self._closed.set()
            return True


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


def open_agent_run_span(
    *,
    session_id: str = "",
    request_id: str = "",
    channel_id: str = "",
    mode: str = "",
) -> AgentRunSpanHandle | None:
    """Open a root team span around a single-agent run.

    Returns an opaque handle to pass to :func:`close_agent_run_span`, or
    ``None`` when observability is not initialized (in which case closing is
    a no-op).
    """
    span: Any | None = None
    try:
        from opentelemetry.trace import SpanKind

        from openjiuwen.agent_teams.observability import get_tracer, is_initialized
        from openjiuwen.agent_teams.observability.semconv import LANGFUSE_SESSION_ID
        from openjiuwen.agent_teams.observability.span_context import set_team_span
        from jiuwenswarm.extensions.identity_provider import IdentityStore
        from jiuwenswarm.telemetry.attributes import (
            APP_ID,
            DOMAIN_ID,
            GEN_AI_CONVERSATION_ID,
            JIUWENCLAW_APP_ID,
            JIUWENCLAW_CHANNEL_ID,
            JIUWENCLAW_DOMAIN_ID,
            JIUWENCLAW_REQUEST_ID,
            JIUWENCLAW_SESSION_ID,
            JIUWENCLAW_USER_ID,
            USER_ID,
        )

        runtime = _get_unified_runtime()
        unified = bool(runtime.is_unified_active())
        if unified:
            provider = runtime.tracer_provider
            if provider is None:
                return None
            tracer = provider.get_tracer("jiuwenswarm.agent")
        else:
            if not is_initialized() or not _agent_observability_active:
                return None
            tracer = get_tracer("jiuwenswarm.agent")

        name = _build_run_span_name(mode=mode, session_id=session_id)
        span = tracer.start_span(name=name, kind=SpanKind.SERVER)
        try:
            identity = IdentityStore.get_identity()
        except Exception:
            identity = None
        attributes = {
            LANGFUSE_SESSION_ID: session_id or "",
            GEN_AI_CONVERSATION_ID: session_id or "",
            JIUWENCLAW_SESSION_ID: session_id or "",
            JIUWENCLAW_REQUEST_ID: request_id or "",
            JIUWENCLAW_CHANNEL_ID: channel_id or "",
            "jiuwenswarm.mode": mode or "",
        }
        for primary, alias, value in (
            (USER_ID, JIUWENCLAW_USER_ID, getattr(identity, "user_id", None)),
            (DOMAIN_ID, JIUWENCLAW_DOMAIN_ID, getattr(identity, "domain_id", None)),
            (APP_ID, JIUWENCLAW_APP_ID, getattr(identity, "app_id", None)),
        ):
            if value not in (None, ""):
                attributes[primary] = value
                attributes[alias] = value
        for key, value in attributes.items():
            try:
                span.set_attribute(key, value)
            except (TypeError, ValueError) as exc:
                logger.debug(
                    "[AgentObservability] root attribute rejected: key=%s error=%s",
                    key,
                    exc,
                )
        # Register as the team span so OtelCallbackHandler's parent lookup
        # (get_team_span fallback) finds it for LLM/tool span creation.
        set_team_span(span, team_name="single-agent")
        trace_bindings = runtime.trace_bindings
        try:
            binding = trace_bindings.bind(session_id, request_id, span)
        except Exception as exc:
            logger.warning("[AgentObservability] root binding failed: %s", exc)
            binding = None
        span_registry = runtime.span_registry if unified else None
        if span_registry is not None:
            try:
                context = span.get_span_context()
                span_registry.bind_trace_attributes(context.trace_id, attributes)
            except Exception as exc:
                logger.debug(
                    "[AgentObservability] trace attribute binding failed: %s", exc
                )
        logger.debug("[AgentObservability] root span opened: name=%s", name)
        return AgentRunSpanHandle(
            root_span=span,
            binding=binding,
            trace_bindings=trace_bindings,
            unified=unified,
        )
    except Exception as exc:
        if span is not None:
            try:
                from openjiuwen.agent_teams.observability.span_context import (
                    clear_team_span,
                    get_team_span,
                )

                if get_team_span() is span:
                    clear_team_span()
            except Exception as cleanup_error:
                logger.debug(
                    "[AgentObservability] failed root span context cleanup failed: %s",
                    cleanup_error,
                )
            try:
                span.end()
            except Exception as cleanup_error:
                logger.debug(
                    "[AgentObservability] end failed root span failed: %s",
                    cleanup_error,
                )
        logger.warning("[AgentObservability] open root span failed: %s", exc)
        return None


def close_agent_run_span(handle: Any, *, session_id: str = "") -> None:
    """End the root span opened by :func:`open_agent_run_span` and clear it."""
    if handle is None:
        return
    if isinstance(handle, AgentRunSpanHandle) and not handle.claim_close():
        return
    root_span = handle.root_span if isinstance(handle, AgentRunSpanHandle) else handle
    binding = handle.binding if isinstance(handle, AgentRunSpanHandle) else None
    trace_bindings = (
        handle.trace_bindings if isinstance(handle, AgentRunSpanHandle) else None
    )
    if binding is not None and trace_bindings is not None:
        try:
            trace_bindings.remove(binding)
        except Exception as exc:
            logger.debug("[AgentObservability] trace binding remove failed: %s", exc)
    try:
        from openjiuwen.agent_teams.observability.span_context import (
            cascade_close_children,
            clear_team_span,
            flush_child_spans,
            get_team_span,
        )
    except Exception as exc:
        logger.warning(
            "[AgentObservability] close helpers unavailable: session_id=%s error=%s",
            session_id,
            exc,
        )
        try:
            root_span.end()
        except Exception as end_error:
            logger.debug(
                "[AgentObservability] fallback root span end failed: %s",
                end_error,
            )
        return

    # End any still-open child LLM/tool spans (e.g. run aborted mid-call).
    # Two nets are needed for the single-agent path:
    #   1. cascade_close_children — closes spans whose state was pushed on
    #      the _llm_span_stack / _tool_span_map ContextVars in THIS context.
    #   2. flush_child_spans — the SpanProcessor-backed safety net Team mode
    #      relies on (finalize_trace -> flush_child_spans via
    #      ActiveSpanTracker). The single-agent runner opens LLM spans inside
    #      its own child context, so their ContextVar state is not visible
    #      here; the tracker closes them by trace_id regardless of context.
    # Flush by the handle's explicit trace id before ending the root so one
    # request can never drain another concurrent request's child spans.
    try:
        owns_current_context = get_team_span() is root_span
    except Exception as exc:
        logger.debug("[AgentObservability] current root lookup failed: %s", exc)
        owns_current_context = False
    if owns_current_context:
        try:
            cascade_close_children()
        except Exception as exc:
            logger.debug("[AgentObservability] cascade_close_children failed: %s", exc)
    try:
        span_context = root_span.get_span_context()
        flush_child_spans(trace_id=span_context.trace_id)
    except Exception as exc:
        logger.debug("[AgentObservability] flush_child_spans failed: %s", exc)
    try:
        root_span.end()
    except Exception as exc:
        logger.debug("[AgentObservability] end root span failed: %s", exc)
    if owns_current_context:
        try:
            clear_team_span()
        except Exception as exc:
            logger.debug("[AgentObservability] clear root span failed: %s", exc)
