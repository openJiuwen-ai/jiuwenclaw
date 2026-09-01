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
4. Runtime prompt attachment fallback: the agent-core version locked by the
   dev-stable baseline does not project Dolores AgentLoop prompt attachments
   into the final model messages. Mirror only ``runtime.setting`` into the
   existing SystemPromptBuilder so model identity remains visible.
5. AgentLoop callback isolation: dev-stable owns one adapter per session and
   also creates warm-up adapters. Dolores historically used the fixed card id
   as every root loop's callback namespace, so agent-core's process-global
   callback registry accumulated rails from all old sessions. Give each root
   loop an instance namespace; explicitly named subagent runtimes are kept.
6. Runtime config baseline: Dolores's legacy config reader loads only the user
   YAML while dev-stable resolves that file on top of ``resources/config.yaml``.
   Route the Dolores adapter's read-only runtime snapshot through the stock
   loader so defaults such as ``react.max_iterations`` are not lost.
"""
import logging
import os
import uuid
from dataclasses import fields

logger = logging.getLogger(__name__)

_PATCH_APPLIED = False


def _dolores_create_adapter(
    sdk=None,
    *,
    mode="agent",
    workspace_dir=None,
    agent_id=None,
    service_id=None,
):
    """Create DoloresAdapter with the current stock factory signature.

    Dolores keeps resolving request/workspace context through its existing
    runtime path.  The extra keyword arguments are accepted here so newer
    jiuwenswarm callers can use the same factory contract without changing
    the Dolores adapter implementation.
    """
    from jiuwenswarm.extensions.dolores.server.runtime.agent_adapter.interface_deep import (
        DoloresAdapter,
    )

    class _DevStableCompatibleDoloresAdapter(DoloresAdapter):
        async def create_instance(
            self,
            config=None,
            *,
            mode="agent",
            sub_mode=None,
            config_base=None,
        ):
            # Dolores already resolves the active config through get_config().
            # Accept dev-stable's explicit snapshot without changing that
            # established initialization path.
            result = await super().create_instance(
                config,
                mode=mode,
                sub_mode=sub_mode,
            )
            deep_config = getattr(getattr(self, "_instance", None), "_deep_config", None)
            logger.info(
                "[DoloresAgent] effective runtime limits: max_iterations=%s "
                "completion_timeout=%s",
                getattr(deep_config, "max_iterations", None),
                getattr(deep_config, "completion_timeout", None),
            )
            return result

        async def prepare_session(
            self,
            *,
            session_id,
            channel_id,
            mode,
            project_dir=None,
        ):
            # The fork already has session-scoped adapters and starts their
            # interaction loop in this helper.  Reuse it as Dolores's native
            # equivalent of dev-stable's newer prepare_session contract.
            await self._get_or_create_session_adapter(session_id)

        async def cleanup(self):
            """Release the instance-local callbacks with the adapter lifecycle."""
            try:
                await super().cleanup()
            finally:
                instance = getattr(self, "_instance", None)
                callback_manager = getattr(
                    instance,
                    "_agent_callback_manager",
                    None,
                )
                if callback_manager is not None:
                    await callback_manager.clear()

        async def close(self):
            # dev-stable startup prewarm probes ``close`` for temporary agents.
            # Dolores exposes ``cleanup`` instead, so bridge the lifecycle here.
            await self.cleanup()

    adapter = _DevStableCompatibleDoloresAdapter()
    logger.info("[DoloresAgent] create_adapter -> DoloresAdapter (fork) instance created")
    return adapter


def _dolores_resolve_sdk_choice():
    return "dolores"


def _patch_dolores_runtime_config_baseline() -> None:
    """Use dev-stable's merged config snapshot when building Dolores agents.

    The Dolores fork's ``common.config.get_config`` predates the stock template
    merge and therefore returns only ``~/.jiuwenswarm/config/config.yaml``.
    A sparse user config has no ``react.max_iterations`` entry, so AgentLoop
    silently falls back to 15 even though dev-stable's effective value is 100.

    Patch only the symbol imported by the Dolores adapter.  Dolores's config
    mutation helpers and its core AgentLoop remain untouched.
    """
    from jiuwenswarm.common.config import get_config as get_stock_config
    from jiuwenswarm.extensions.dolores.server.runtime.agent_adapter import (
        interface_deep as dolores_interface,
    )

    if getattr(
        dolores_interface.get_config,
        "_dolores_uses_dev_stable_config_baseline",
        False,
    ):
        return

    def _get_merged_runtime_config():
        return get_stock_config()

    _get_merged_runtime_config._dolores_uses_dev_stable_config_baseline = True  # type: ignore[attr-defined]
    dolores_interface.get_config = _get_merged_runtime_config
    logger.info(
        "[DoloresAgent] runtime config reads use dev-stable merged baseline"
    )


def _patch_agent_loop_callback_namespace_isolation() -> None:
    """Give each root Dolores AgentLoop its own agent-core callback namespace.

    ``AgentCallbackManager`` registers rails in ``Runner.callback_framework``,
    which is process-global and indexes them by ``event_namespace``.  The
    merged dev-stable lifecycle creates startup and session-scoped adapters;
    reusing ``card.id == 'jiuwenswarm'`` for all of them therefore makes every
    model/tool event execute every rail left by every previous loop.

    Keep this as a compatibility shim instead of changing Dolores's loop.  A
    caller-provided runtime_id (notably the fork's deterministic subagent id)
    remains authoritative; only legacy root construction without one gets an
    instance-local namespace.
    """
    from jiuwenswarm.extensions.dolores.server.runtime.agent_adapter.agent_loop import (
        AgentLoop,
    )

    if getattr(AgentLoop.__init__, "_dolores_callback_namespace_isolation", False):
        return
    original_init = AgentLoop.__init__

    def _init_with_isolated_namespace(self, *args, **kwargs):
        if not kwargs.get("runtime_id"):
            card = kwargs.get("card")
            card_id = str(getattr(card, "id", "agent") or "agent")
            kwargs["runtime_id"] = f"{card_id}.instance.{uuid.uuid4().hex}"
        original_init(self, *args, **kwargs)

    _init_with_isolated_namespace._dolores_callback_namespace_isolation = True  # type: ignore[attr-defined]
    AgentLoop.__init__ = _init_with_isolated_namespace  # type: ignore[assignment]
    logger.info(
        "[DoloresAgent] AgentLoop callback namespaces isolated per root instance"
    )


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
    _StockResp = _src.AgentResponse
    _StockChunk = _src.AgentResponseChunk
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

    # dev-stable has more control/prewarm producers than the Dolores fork.
    # Some of those modules bind the stock response dataclasses before this
    # extension is registered.  Their instances are structurally identical to
    # the Dolores response classes, but gateway_normalize intentionally checks
    # exact class identity.  Coerce only those legacy-bound instances at the
    # protocol boundary, leaving both implementations and their core logic
    # untouched.
    import jiuwenswarm.common.e2a.gateway_normalize as _normalize
    import jiuwenswarm.common.e2a.wire_codec as _wire_codec

    _orig_response_normalize = _normalize.e2a_response_from_agent_response
    _orig_chunk_normalize = _normalize.e2a_response_from_agent_chunk

    def _copy_dataclass(value, target_cls):
        return target_cls(
            **{
                field.name: getattr(value, field.name)
                for field in fields(target_cls)
                if hasattr(value, field.name)
            }
        )

    def _compat_response_normalize(resp, **kwargs):
        if isinstance(resp, _StockResp) and not isinstance(resp, _DResp):
            resp = _copy_dataclass(resp, _DResp)
        return _orig_response_normalize(resp, **kwargs)

    def _compat_chunk_normalize(chunk, **kwargs):
        if isinstance(chunk, _StockChunk) and not isinstance(chunk, _DChunk):
            chunk = _copy_dataclass(chunk, _DChunk)
        return _orig_chunk_normalize(chunk, **kwargs)

    _normalize.e2a_response_from_agent_response = _compat_response_normalize
    _normalize.e2a_response_from_agent_chunk = _compat_chunk_normalize
    # wire_codec imported the normalizers by name before extension loading.
    _wire_codec.e2a_response_from_agent_response = _compat_response_normalize
    _wire_codec.e2a_response_from_agent_chunk = _compat_chunk_normalize


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


def _patch_runtime_setting_prompt_fallback() -> None:
    """Mirror Dolores runtime metadata when prompt attachments are unavailable.

    RuntimePromptRail already resolves the exact per-session model, available
    models, mode, language, and channel.  Its normal output path is a prompt
    attachment, but Dolores's custom AgentLoop on the dev-stable locked core
    has no attachment manager and therefore drops that section.  Reuse the
    rail's computed content at its existing chokepoint instead of duplicating
    any Dolores runtime/configuration logic.
    """
    from openjiuwen.harness.prompts import PromptSection
    from jiuwenswarm.extensions.dolores.agents.harness.common.rails.runtime_prompt_rail import (
        RuntimePromptRail,
    )

    _original_upsert = RuntimePromptRail._upsert_prompt_attachment

    async def _upsert_with_system_prompt_fallback(
        self,
        ctx,
        *,
        section,
        content,
        kind,
        priority,
    ):
        await _original_upsert(
            self,
            ctx,
            section=section,
            content=content,
            kind=kind,
            priority=priority,
        )
        if (
            section != "runtime.setting"
            or self.system_prompt_builder is None
            or self.attachment_manager is not None
        ):
            return

        self.system_prompt_builder.remove_section(section)
        self.system_prompt_builder.add_section(
            PromptSection(
                name=section,
                content={"cn": content, "en": content},
                priority=priority,
            )
        )

    RuntimePromptRail._upsert_prompt_attachment = (  # type: ignore[assignment]
        _upsert_with_system_prompt_fallback
    )
    logger.info(
        "[DoloresAgent] runtime.setting mirrored into system prompt "
        "for dev-stable agent-core compatibility"
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
    _patch_dolores_runtime_config_baseline()
    _patch_agent_loop_callback_namespace_isolation()
    _patch_stock_schema_to_dolores()
    _patch_skip_plan_mode_sync_for_dolores()
    _patch_runtime_setting_prompt_fallback()

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
