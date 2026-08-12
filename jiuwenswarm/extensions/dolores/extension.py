"""DoloresAgent extension.

Routes the default main agent to the self-contained Dolores fork when
JIUWENSWARM_AGENT_KIND=dolores is set. Pure additive wiring loaded by the
extension loader at AgentServer startup (before any adapter is created);
zero modification to any stock jiuwenswarm source file.

Patches (dolores switch on only):
1. create_adapter / resolve_sdk_choice in the stock adapter interface -> the
   Dolores adapter.
2. Schema class identity: the fork defines its own AgentResponseChunk /
   AgentResponse / AgentRequest. Stock's response path binds these names from
   jiuwenswarm.common.schema.agent (a different class object), so Dolores
   chunks would be silently dropped at isinstance. Patch the stock source +
   already-loaded consumers to use the Dolores classes while Dolores is active.
3. Dev file download: stock vite has no /file-api/download handler, so the
   relative download_url falls to the vite SPA fallback (browser saves
   index.html as the artifact -> corruption). Start a fork HTTP server
   (dev_file_server.py) + rewrite download_url to it at both the
   generate_download_url and the push/wire chokepoints.
"""
import logging
import os

logger = logging.getLogger(__name__)

_PATCH_APPLIED = False


def _dolores_create_adapter(sdk=None, *, mode="agent"):
    from jiuwenswarm.extensions.dolores.server.runtime.agent_adapter.interface_deep import (
        DoloresAdapter,
    )
    adapter = DoloresAdapter()
    logger.info("[DoloresAgent] create_adapter -> DoloresAdapter (fork) instance created")
    return adapter


def _dolores_resolve_sdk_choice():
    return "dolores"


def _patch_stock_schema_to_dolores() -> None:
    """Make stock's response-path parsing use Dolores's protocol classes."""
    import sys
    from jiuwenswarm.extensions.dolores.common.schema.agent import (
        AgentRequest as _DReq,
        AgentResponse as _DResp,
        AgentResponseChunk as _DChunk,
    )
    names = {"AgentRequest": _DReq, "AgentResponse": _DResp, "AgentResponseChunk": _DChunk}

    # Patch the stock source first; local `from ...schema.agent import X` re-binds
    # from here on each call, and modules imported after this pick up Dolores too.
    import jiuwenswarm.common.schema.agent as _src  # noqa: F401
    for n, v in names.items():
        setattr(_src, n, v)
    _pkg = sys.modules.get("jiuwenswarm.common.schema")
    if _pkg is not None:
        for n, v in names.items():
            setattr(_pkg, n, v)

    # Already-loaded consumer modules keep a bound stock class; patch their names too.
    consumers = [
        "jiuwenswarm.common.e2a.gateway_normalize",
        "jiuwenswarm.common.e2a.wire_codec",
        "jiuwenswarm.server.runtime.agent_adapter.interface",
        "jiuwenswarm.server.agent_ws_server",
        # build_server_push_wire constructs the send_push AgentResponseChunk here;
        # without patching it the isinstance(Dolores) gate fails and chat.file
        # never reaches the frontend.
        "jiuwenswarm.server.gateway_push.wire",
    ]
    for mod_path in consumers:
        m = sys.modules.get(mod_path)
        if m is None:
            continue
        for n, v in names.items():
            try:
                setattr(m, n, v)
            except Exception as exc:  # pragma: no cover
                logger.warning(
                    "[DoloresAgent] schema patch: setattr %s.%s failed: %r",
                    mod_path,
                    n,
                    exc,
                )


def _patch_download_url_to_dev_server(port: int) -> None:
    """Point file download_url at the Dolores dev file server (absolute URL)."""
    try:
        import jiuwenswarm.agents.harness.common.tools.web_file_download as _wfd
    except Exception as exc:  # pragma: no cover
        logger.warning(
            "[DoloresAgent] download_url patch: import web_file_download failed: %r",
            exc,
        )
        return

    _base = f"http://127.0.0.1:{port}/file-api/download?token="

    @staticmethod
    def _fork_generate_download_url(token: str) -> str:  # type: ignore[override]
        return f"{_base}{token}"

    _wfd.WebFileDownloadManager.generate_download_url = (  # type: ignore[assignment]
        _fork_generate_download_url
    )


def _patch_wire_to_rewrite_download_url(port: int) -> None:
    """Rewrite file download_url -> absolute fork-server URL at the push/wire layer.

    send_file_to_user may run in a skill subprocess that does not inherit the
    generate_download_url patch above, so it still emits the stock relative
    /file-api/download?token=... URL. build_server_push_wire runs in this
    (patched) AgentServer process, so it is the reliable chokepoint to rewrite
    any stock-relative download_url to the absolute fork-server URL before the
    frame reaches the frontend. Already-absolute URLs are skipped (no double
    rewrite). Never raises into the push path.
    """
    import sys
    import jiuwenswarm.server.gateway_push.wire as _wire

    _orig = _wire.build_server_push_wire
    _prefix = "/file-api/download?token="
    _base = f"http://127.0.0.1:{port}/file-api/download?token="

    def _walk(obj):
        if isinstance(obj, dict):
            du = obj.get("download_url")
            if isinstance(du, str) and du.startswith(_prefix):
                obj["download_url"] = _base + du[len(_prefix):]
            for v in obj.values():
                _walk(v)
        elif isinstance(obj, list):
            for v in obj:
                _walk(v)

    def _rewire(msg):
        try:
            if isinstance(msg, dict):
                _walk(msg.get("payload"))
                _walk(msg.get("body"))
        except Exception:  # pragma: no cover - never break push on rewrite failure
            pass
        return _orig(msg)

    _wire.build_server_push_wire = _rewire  # type: ignore[assignment]
    # agent_ws_server imports the name at top level; patch its bound name too.
    for _mod_path in (
        "jiuwenswarm.server.agent_ws_server",
        "jiuwenswarm.server.gateway_push",
    ):
        _m = sys.modules.get(_mod_path)
        if _m is not None and getattr(_m, "build_server_push_wire", None) is _orig:
            setattr(_m, "build_server_push_wire", _rewire)
    logger.info(
        "[DoloresAgent] wire download_url rewrite ARMED -> http://127.0.0.1:%s/file-api/download",
        port,
    )


def _patch_skip_plan_mode_sync_for_dolores() -> None:
    """Skip the DeepAgent plan/code-mode state machine for DoloresAgent.

    DoloresAgent is a separate line from DeepAgent and does not use the
    plan/normal code-mode state machine. The merged stock agent_ws_server
    ``_ensure_code_mode_state`` calls ``deep_agent.load_state(session).plan_mode``
    on every agent chat turn; the fork's ``load_state`` is a deliberate stub
    (state persistence is deferred), so that path would crash. No-op the whole
    method so the stub is never reached and DoloresAgent runs on its own line
    without touching load_state or any mainline file.
    """
    from jiuwenswarm.server.agent_ws_server import AgentWebSocketServer

    async def _noop_ensure_code_mode_state(self, *args, **kwargs):  # type: ignore[override]
        return False

    AgentWebSocketServer._ensure_code_mode_state = (  # type: ignore[assignment]
        _noop_ensure_code_mode_state
    )
    logger.info("[DoloresAgent] plan/code-mode state sync skipped (separate line from DeepAgent)")


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
    _patch_skip_plan_mode_sync_for_dolores()

    try:
        from jiuwenswarm.extensions.dolores.server.dev_file_server import (
            start as _start_dev_file_server,
        )
        _file_port = _start_dev_file_server()
        if _file_port:
            _patch_download_url_to_dev_server(_file_port)
            _patch_wire_to_rewrite_download_url(_file_port)
        else:
            logger.warning(
                "[DoloresAgent] dev file server NOT started (port bind failed); "
                "download_url stays stock -> dev download will be broken"
            )
    except Exception as exc:  # pragma: no cover
        logger.warning("[DoloresAgent] dev file server setup failed: %r", exc)

    _PATCH_APPLIED = True
    logger.info(
        "[DoloresAgent] switch ARMED: create_adapter + schema classes patched to Dolores fork"
    )
    return []
