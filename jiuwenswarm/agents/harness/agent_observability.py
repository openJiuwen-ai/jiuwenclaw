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
from contextvars import ContextVar
from typing import Any

from jiuwenswarm.common.config import get_config
from jiuwenswarm.common.utils import get_user_workspace_dir

logger = logging.getLogger(__name__)

# ── Single-Agent Observability ─────────────────────────────────
# Tracks whether observability is currently active so we can detect config
# toggles (enabled -> disabled or vice-versa) and init / shutdown accordingly
# on each single-agent request.
_agent_observability_active: bool = False

# Root spans of the runs currently in flight, keyed by session id.
#
# The per-request _team_span_ctx ContextVar can't reach the round tasks (agent
# execution runs in a session-setup supervisor task), so every SDK team-span
# lookup returns None there and the whole child-span machinery goes dark. The
# ContextVar stand-in installed below falls back to this registry, which works
# regardless of task/context boundary.
#
# Keyed rather than a single "current run" slot because sessions overlap: a
# process serves several chats at once, and a single slot made them fight over
# it. Whoever finished first cleared it, so a run still in progress silently
# lost its agent-tier spans from that moment on (its sub-agents landed flat
# under the dispatching agent) — and before that, whoever opened last owned the
# slot, so the other run's spans would have joined the wrong trace.
_ROOT_SPANS: dict[str, Any] = {}

# Name of the SDK-private ContextVar the fallback below rebinds.
#
# Held as a constant, and reached through getattr / setattr, so that the single
# place this package reaches into another package's module internals is
# explicit and greppable, instead of reading like an ordinary attribute access.
_SDK_TEAM_SPAN_CTX_ATTR = "_team_span_ctx"


def _is_recording(span: Any) -> bool:
    """Report whether *span* is still open, tolerating stubs without the API."""
    try:
        return bool(span is not None and span.is_recording())
    except Exception:
        return False


def _resolve_root_span() -> Any:
    """Return the root span of the run the calling task belongs to, or None.

    Resolution is by session id first: ``get_session_id`` is set by the SDK
    around agent execution, so it is readable from the tasks the ContextVar
    cannot reach — which is exactly where this fallback is needed.

    When no session id is in reach, a single run in flight is unambiguous and
    answers. Several in flight with no way to tell them apart returns None
    rather than a guess: attaching one run's spans to another run's trace is
    worse than the span being missing.
    """
    session_id = ""
    try:
        from openjiuwen.agent_teams.context import get_session_id

        session_id = get_session_id() or ""
    except Exception as exc:
        logger.debug("[AgentObservability] session id lookup failed: %s", exc)

    span = _ROOT_SPANS.get(session_id)
    if _is_recording(span):
        return span

    live = [candidate for candidate in list(_ROOT_SPANS.values()) if _is_recording(candidate)]
    if len(live) == 1:
        return live[0]
    return None


class _RootSpanFallbackContextVar:
    """Stand-in for the SDK's ``_team_span_ctx`` that falls back to the root span.

    ``span_context`` resolves the current team span in two ways, and both must
    see the single-agent root span:

    * Through ``get_team_span()`` — used by ``OtelCallbackHandler`` to pick the
      parent for llm/tool spans, and by ``ObservabilityRail``, which *returns
      early* when it is None (that is why the agent-tier spans, including the
      sub-agent ``agent.<type>.invoke`` ones, used to be missing).
    * By reading the ``_team_span_ctx`` ContextVar **directly**, inside
      ``ActiveSpanTracker._find_llm_span`` / ``close_llm_spans_by_parent`` —
      the lookups that resolve the trace before locating the already-open
      ``llm.call`` span. These are not reachable by wrapping a function.

    Missing the second path is not cosmetic: the llm.call span is created (its
    parent comes from the first path) but never found again, so no chunk / TTFT
    / completion / usage attribute is ever written to it and it is force-closed
    later by the tracker's orphan sweep — an LLM span with input but no output.

    Rebinding the ContextVar itself covers both paths at once, because every
    reader lives in ``span_context`` and resolves the module global at call
    time. Only ``get`` changes behavior; ``set`` / ``reset`` delegate to the
    real ContextVar so team mode keeps exact per-context semantics — its team
    span is ContextVar-visible, so the fallback never triggers there.

    Upstream offers a related seam as a supported API
    (``span_context.set_ambient_team_span`` / ``clear_ambient_team_span``,
    which also spares the root span from the flush by identity rather than by
    a ``team.`` name prefix), but it is NOT a drop-in replacement for this
    stand-in and swapping to it would regress two fixes:

    * It registers one process-wide slot, while :data:`_ROOT_SPANS` is keyed by
      session — overlapping chats in one process would fight over the slot.
    * It falls back only when the ContextVar holds None, whereas :meth:`get`
      below also overrides a binding that has already *ended* (the request
      coroutine's span outliving its run in a context-snapshotting task).

    Adopting it therefore needs those two behaviors upstream first; until then
    this stand-in stays.
    """

    def __init__(self, inner: ContextVar) -> None:
        self._inner = inner

    @property
    def name(self) -> str:
        """Return the wrapped ContextVar's name."""
        return self._inner.name

    def get(self, *default: Any) -> Any:
        """Return the context-local team span, or this run's root span.

        A binding that has already ended does not win: the request coroutine's
        span outlives its run in a task that snapshotted the context, and the
        callers here need the span of the run happening *now*. The stale
        binding is still returned as a last resort, for the close paths that
        only need its trace id.
        """
        span = self._inner.get(*default)
        if _is_recording(span):
            return span
        root_span = _resolve_root_span()
        if root_span is not None:
            return root_span
        return span

    def set(self, value: Any) -> Any:
        """Bind *value* in the current context and return the reset token."""
        return self._inner.set(value)

    def reset(self, token: Any) -> None:
        """Restore the binding this context had before its matching ``set``."""
        self._inner.reset(token)


def _install_team_span_global_fallback() -> None:
    """Swap the SDK's ``_team_span_ctx`` for the root-span-aware stand-in.

    Best-effort, idempotent (a second call sees the stand-in already in place),
    never raises — observability must never break a run.
    """
    try:
        from openjiuwen.agent_teams.observability import span_context
    except Exception as exc:
        logger.debug("[AgentObservability] skip team-span fallback install: %s", exc)
        return

    current = getattr(span_context, _SDK_TEAM_SPAN_CTX_ATTR, None)
    if current is None or isinstance(current, _RootSpanFallbackContextVar):
        return
    setattr(span_context, _SDK_TEAM_SPAN_CTX_ATTR, _RootSpanFallbackContextVar(current))


_install_team_span_global_fallback()
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
# Synthetic team name for the non-team run paths. Registered with
# ``set_team_span`` for the root span, and stamped on the agents themselves by
# :func:`mark_single_agent_team` — the observability rail keys its agent-tier
# spans off ``agent.team_name``.
SINGLE_AGENT_TEAM_NAME = "single-agent"


def mark_single_agent_team(agent: Any) -> None:
    """Stamp the synthetic team marker the observability rail keys off.

    ``ObservabilityRail.before_invoke`` returns early for an agent with no
    ``team_name``, and a single-round agent (``enable_task_loop=False``) gets
    its span from that hook alone — ``before_task_iteration`` never fires. A
    single agent has no team, so without this marker it produces **no
    agent-tier span at all**: its llm/tool spans and any sub-agent's
    ``agent.<type>.invoke`` span both attach straight to the run's root span,
    which is what flattens a task-tool sub-agent into the agent layer instead
    of nesting it under the dispatching agent.

    ``team_name`` is a plain attribute on DeepAgent. An agent that already
    carries one is a real team member and is left alone. Best-effort: tracing
    setup must never break a run.

    Args:
        agent: The DeepAgent instance about to run (main agent or sub-agent).
    """
    if agent is None:
        return
    if getattr(agent, "team_name", ""):
        return
    try:
        agent.team_name = SINGLE_AGENT_TEAM_NAME
    except Exception as exc:
        logger.debug("[AgentObservability] set team_name on agent failed: %s", exc)


def attach_subagent_observability(subagent: Any) -> None:
    """Give *subagent* its own agent-tier span for the run that dispatches it.

    Without a rail of its own a sub-agent produces no ``agent.<type>.invoke``
    span, so its llm/tool spans attach to the **dispatching** agent's span —
    the sub-agent's whole run then reads as if the parent had made those calls,
    with nothing under the ``task_tool`` span it actually ran inside.

    Attaching at build time is unreliable: the parent agent is constructed
    once, typically before observability is initialized, so
    ``maybe_observability_rail()`` would return None. By dispatch time
    observability is up, and ``add_rail`` still lands before the sub-agent's
    first ``_ensure_initialized()`` registers its hooks.

    Idempotent, and a no-op when observability is off or *subagent* lacks the
    DeepAgent rail API. Best-effort: tracing must never break a run.

    Args:
        subagent: The freshly created sub-agent DeepAgent.
    """
    if subagent is None:
        return
    try:
        from openjiuwen.agent_teams.observability.rail import (
            ObservabilityRail,
            maybe_observability_rail,
        )

        rail = maybe_observability_rail()
        if rail is None:
            return  # observability not initialized -> nothing to trace
        configured = subagent.configured_rails() if hasattr(subagent, "configured_rails") else []
        if any(isinstance(r, ObservabilityRail) for r in configured):
            return  # already attached — never add a second one
        if hasattr(subagent, "add_rail"):
            subagent.add_rail(rail)
    except Exception as exc:
        logger.debug("[AgentObservability] attach subagent rail failed: %s", exc)

    # Released openjiuwen guards ObservabilityRail.before_invoke with
    # ``if not team_name: return``, which no sub-agent can satisfy on its own.
    # Harmless on newer versions, where that guard is gone.
    mark_single_agent_team(subagent)


# Marker stamped on the wrapper below so a second install recognizes its own
# work and leaves it alone. The ``jiuwenswarm`` prefix is what keeps it from
# colliding with anything the SDK puts on the same function object, so the name
# carries no leading underscore: it is read from outside the wrapper.
_SUBAGENT_HOOK_MARKER_ATTR = "jiuwenswarm_observability_hooked"


def install_subagent_observability_hook() -> None:
    """Trace every sub-agent, whichever tool dispatched it.

    ``DeepAgent.create_subagent`` is the one point all dispatch paths share —
    the SDK's builtin ``task_tool``, this platform's custom agent tool, and
    background sub-agents. Wrapping it there is what makes tracing independent
    of the dispatcher; hooking a single tool covers only that tool (the
    ``/debug`` capture wrapper used to be the only place a rail was attached,
    so a normal run produced no sub-agent spans at all).

    Idempotent — a second call sees the wrapper already installed. Best-effort:
    never raises, and a failure only costs sub-agent spans.
    """
    try:
        from openjiuwen.harness.deep_agent import DeepAgent
    except Exception as exc:
        logger.debug("[AgentObservability] subagent hook install skipped: %s", exc)
        return

    original = getattr(DeepAgent, "create_subagent", None)
    if original is None or getattr(original, _SUBAGENT_HOOK_MARKER_ATTR, False):
        return

    def create_subagent_with_observability(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        """Create the sub-agent, then give it its own observability rail."""
        subagent = original(self, *args, **kwargs)
        attach_subagent_observability(subagent)
        return subagent

    setattr(create_subagent_with_observability, _SUBAGENT_HOOK_MARKER_ATTR, True)
    DeepAgent.create_subagent = create_subagent_with_observability


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
        if not _agent_observability_active:
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
        set_team_span(span, team_name=SINGLE_AGENT_TEAM_NAME)
        # Also register under this run's session: the supervisor task doesn't
        # inherit the ContextVar, so the fallback installed at import resolves
        # the root span from here for the rail and OtelCallbackHandler.
        _ROOT_SPANS[session_id or ""] = span
        logger.info("[AgentObservability] root span opened: name=%s", name)
        return span
    except Exception as exc:
        logger.warning("[AgentObservability] open root span failed: %s", exc)
        return None


def _stamp_run_output(handle: Any, output: str) -> None:
    """Write the run's final answer onto the root span as the trace output.

    Team mode fills the equivalent attribute on its ``team.<name>`` span from
    the leader's iteration result (``ObservabilityRail.after_task_iteration``),
    which keys off ``TeamRole.LEADER`` and therefore never fires for a single
    agent — leaving the Langfuse trace with an empty top-level output. The
    single-agent counterpart is the run's final answer, stamped here.

    Redaction follows the active ``ObservabilityConfig`` so ``redact_completions``
    covers this attribute exactly as it covers llm/agent span outputs.

    Args:
        handle: The still-recording root span.
        output: Final answer text; empty means nothing to stamp.
    """
    if not output:
        return
    from openjiuwen.agent_teams.observability.redaction import redact_completion
    from openjiuwen.agent_teams.observability.semconv import LANGFUSE_OBSERVATION_OUTPUT
    # Aliased: the module-level ``get_config`` is JiuwenSwarm's own settings
    # reader, and this SDK-side one returns the active ObservabilityConfig.
    from openjiuwen.agent_teams.observability.setup import get_config as get_observability_config

    config = get_observability_config()
    text = redact_completion(output, config) if config else output
    handle.set_attribute(LANGFUSE_OBSERVATION_OUTPUT, text)


def close_agent_run_span(handle: Any, *, session_id: str = "", output: str = "") -> None:
    """End the root span opened by :func:`open_agent_run_span` and clear it.

    Args:
        handle: Opaque handle from :func:`open_agent_run_span`; None is a no-op.
        session_id: Session the run belonged to; its registry entry is dropped.
        output: The run's final answer, stamped as the trace-level output.
            Empty (aborted / errored run) leaves the attribute unset.
    """
    # Drop this run's fallback entry — and only this run's. Sessions overlap,
    # so clearing whatever happens to be registered would blind a run that is
    # still going (its sub-agents would lose their spans mid-run).
    if _ROOT_SPANS.get(session_id or "") is handle:
        _ROOT_SPANS.pop(session_id or "", None)
    if handle is None:
        return
    try:
        from openjiuwen.agent_teams.observability.span_context import (
            cascade_close_children,
            clear_team_span,
            flush_child_spans,
        )

        try:
            _stamp_run_output(handle, output)
        except Exception as exc:
            logger.debug("[AgentObservability] stamp run output failed: %s", exc)

        # End any still-open child LLM/tool spans (e.g. run aborted mid-call).
        # Two nets are needed for the single-agent path:
        #   1. cascade_close_children — closes spans whose state was pushed on
        #      the _llm_span_stack / _tool_span_map ContextVars in THIS context.
        #   2. flush_child_spans — the SpanProcessor-backed safety net Team mode
        #      relies on (finalize_trace -> flush_child_spans via
        #      ActiveSpanTracker). The single-agent runner opens LLM spans inside
        #      its own child context, so their ContextVar state is not visible
        #      here; the tracker closes them by trace_id regardless of context.
        # Both must run BEFORE clear_team_span(): flush_child_spans reads the
        # team span ContextVar to resolve this trace's id, and scopes the close
        # to our trace only (flush_spans_for_trace), so concurrent runs are not
        # affected.
        #
        # Ordering note — the root span is ended BETWEEN the two nets, not after
        # them: ``flush_spans_for_trace`` spares only spans whose name starts
        # with ``team.`` (Team mode's root), so our ``agent.<mode>.<sid>`` root
        # would otherwise be swept up as a leaked child — reported as an ORPHAN
        # warning, force-ended by the tracker, and then re-ended here ("Calling
        # end() on an ended span"). Ending it first makes it non-recording, which
        # the tracker skips, so the root keeps its own end time and status while
        # the net still catches genuinely leaked children.
        try:
            cascade_close_children()
        except Exception as exc:
            logger.debug("[AgentObservability] cascade_close_children failed: %s", exc)
        try:
            handle.end()
        except Exception as exc:
            logger.debug("[AgentObservability] end root span failed: %s", exc)
        try:
            flush_child_spans()
        except Exception as exc:
            logger.debug("[AgentObservability] flush_child_spans failed: %s", exc)
        clear_team_span()
    except Exception as exc:
        logger.warning("[AgentObservability] close root span failed: %s", exc)
