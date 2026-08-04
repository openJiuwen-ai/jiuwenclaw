"""DoloresAgent extension.

When JIUWENSWARM_AGENT_KIND=dolores is set, routes the default main agent to the
self-contained Dolores fork living under this directory. Zero modification to any
original jiuwenswarm source file: pure additive wiring loaded by the extension
loader at AgentServer startup (app_agentserver.py load_all_extensions), which runs
before any adapter is created.

Two patches, both applied only when the switch is on:

1. `create_adapter` in `jiuwenswarm.server.runtime.agent_adapter.interface` →
   returns the Dolores adapter. (Patching the agent_adapters module attribute would
   NOT work because interface.py binds create_adapter by value.)

2. Protocol-class identity: the Dolores fork defines its OWN
   `AgentResponseChunk`/`AgentResponse`/`AgentRequest` (self-contained, no stock
   import). But stock's response path (facade `isinstance` gate at interface.py,
   E2A wire codec, agent_ws_server control chunks) checks/constructs these via
   names bound from `jiuwenswarm.common.schema.agent` — a DIFFERENT class object,
   so Dolores chunks would be silently dropped at `isinstance`. Patch the stock
   source schema + already-loaded consumer modules to use the Dolores classes, so
   class identity matches across the fork↔stock boundary while Dolores is active.
   Dolores code itself stays zero-dependency on stock classes.
"""
import os

_PATCH_APPLIED = False


def _dolores_create_adapter(sdk=None, *, mode="agent"):
    from jiuwenswarm.extensions.dolores.server.runtime.agent_adapter.interface_deep import (
        DoloresAdapter,
    )
    adapter = DoloresAdapter()
    # [DoloresAgent] diagnostic: fires when AgentServer instantiates the agent
    # for a channel. If you see this line, the Dolores fork is the active adapter.
    print("[DoloresAgent] create_adapter -> DoloresAdapter (fork) instance created", flush=True)
    return adapter


def _dolores_resolve_sdk_choice():
    return "dolores"


def _patch_stock_schema_to_dolores() -> None:
    """Make stock's response-path parsing use Dolores's protocol classes."""
    import sys
    # Dolores's own classes (self-contained).
    from jiuwenswarm.extensions.dolores.common.schema.agent import (
        AgentRequest as _DReq,
        AgentResponse as _DResp,
        AgentResponseChunk as _DChunk,
    )
    names = {"AgentRequest": _DReq, "AgentResponse": _DResp, "AgentResponseChunk": _DChunk}

    # Patch the stock SOURCE first. Local `from ...schema.agent import X` (e.g.
    # gateway_normalize.e2a_response_from_agent_chunk) re-executes on each call →
    # picks up the Dolores class; modules imported AFTER this bind Dolores too.
    import jiuwenswarm.common.schema.agent as _src  # noqa: F401  (force-load source)
    for n, v in names.items():
        setattr(_src, n, v)
    # Also patch the package re-export.
    _pkg = sys.modules.get("jiuwenswarm.common.schema")
    if _pkg is not None:
        for n, v in names.items():
            setattr(_pkg, n, v)

    # Patch already-loaded consumer modules whose module-level bound names would
    # otherwise still be the stock class. (Not-yet-loaded ones bind Dolores from
    # the patched source when they import.) Only the response-path producers/
    # checkers need this; others are harmless to skip.
    consumers = [
        "jiuwenswarm.common.e2a.gateway_normalize",
        "jiuwenswarm.common.e2a.wire_codec",
        "jiuwenswarm.server.runtime.agent_adapter.interface",
        "jiuwenswarm.server.agent_ws_server",
    ]
    for mod_path in consumers:
        m = sys.modules.get(mod_path)
        if m is None:
            continue
        for n, v in names.items():
            try:
                setattr(m, n, v)
            except Exception as exc:  # pragma: no cover
                print(
                    f"[DoloresAgent] schema patch: setattr {mod_path}.{n} failed: {exc!r}",
                    flush=True,
                )


async def register_extensions(registry):
    global _PATCH_APPLIED
    kind = os.getenv("JIUWENSWARM_AGENT_KIND", "").strip().lower()
    if kind != "dolores":
        return []  # switch off: behave exactly as the stock DeepAgent path
    if _PATCH_APPLIED:
        return []
    import jiuwenswarm.server.runtime.agent_adapter.interface as _iface
    _iface.create_adapter = _dolores_create_adapter
    _iface.resolve_sdk_choice = _dolores_resolve_sdk_choice
    _patch_stock_schema_to_dolores()
    _PATCH_APPLIED = True
    # [DoloresAgent] diagnostic: fires once at AgentServer startup. Confirms the
    # switch is armed — every agent will now be the Dolores fork, not DeepAgent.
    print("[DoloresAgent] switch ARMED: create_adapter + schema classes patched to Dolores fork", flush=True)
    return []
