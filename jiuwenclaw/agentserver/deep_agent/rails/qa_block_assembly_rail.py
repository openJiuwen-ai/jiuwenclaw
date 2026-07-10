# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Assemble QA catalog + selector preload + hydrate before the first model call (plan mode)."""

from __future__ import annotations

import logging
import time
from typing import Any

from openjiuwen.core.context_engine.qa_artifact.store import QAArtifactStore
from openjiuwen.core.context_engine.qa_artifact.assembly_state import clear_assembly_qa_artifact_state
from openjiuwen.core.context_engine.qa_artifact.window import make_processor_ctx
from openjiuwen.core.context_engine.qa_block.messages import (
    is_other_qa_message,
)
from openjiuwen.core.context_engine.qa_block.catalog import (
    build_catalog_section,
    build_catalog_text,
    maybe_compact_catalog_l1,
)
from openjiuwen.core.context_engine.qa_block.config import QABlockConfig
from openjiuwen.core.context_engine.qa_block.freezer import allocate_qa_id
from openjiuwen.core.context_engine.qa_block.layer import QABlockLayer
from openjiuwen.core.context_engine.qa_block.reconcile import reconcile_orphan_l0_blocks
from openjiuwen.core.context_engine.qa_block.registry import load_registry, save_registry
from openjiuwen.core.context_engine.qa_block.selector import (
    QABlockSelector,
    extract_next_user_query,
    fallback_rule_last_n,
    resolve_selector_model,
)
from openjiuwen.core.context_engine.qa_block.schema import QABlockEntry, QABlockRegistry
from openjiuwen.core.context_engine.qa_block.store import QABlockStore
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, InvokeInputs
from openjiuwen.harness.rails.base import DeepAgentRail

from jiuwenclaw.agentserver.deep_agent.plan_pause_helpers import (
    resolve_actual_session,
    resolve_context_engine,
    session_id_from_session,
)

from jiuwenclaw.agentserver.deep_agent.rails.utils import is_ask_user_question_interrupt

logger = logging.getLogger(__name__)

_WINDOW_QAS_KEY = "_window_qas"
_LAYER_KEY = "_qa_block_layer"
_PRELOADED_QA_IDS_KEY = "_preloaded_qa_ids"
_PENDING_ORPHAN_SALVAGE_KEY = "_qa_block_pending_orphan_salvage"
_ASSEMBLY_COMMITTED_QA_ID_KEY = "_qa_block_assembly_committed_qa_id"


def _is_resume_invoke(ctx: AgentCallbackContext) -> bool:
    inputs = ctx.inputs
    if isinstance(inputs, InvokeInputs):
        return isinstance(inputs.query, InteractiveInput)
    return False


def _is_popup_confirmation_resume(ctx: AgentCallbackContext) -> bool:
    """检测弹窗确认恢复场景(如工具权限"本次允许")。
    仅当 resume 的 result 标记为 interrupt 且包含 interrupt_ids 时，
    才认为是弹窗恢复，跳过QA组装。普通 InteractiveInput resume（如 stale pointer 空上下文场景）仍需要走正常组装流程。
    """
    return is_ask_user_question_interrupt(ctx)


def _is_frozen_entry(entry: QABlockEntry | None) -> bool:
    return bool(
        entry is not None
        and entry.is_history
        and entry.freeze_committed_at
    )


def _get_assembly_committed_qa_id(session: Any) -> str | None:
    getter = getattr(session, "get_state", None)
    if not callable(getter):
        return None
    qa_id = getter(_ASSEMBLY_COMMITTED_QA_ID_KEY)
    return str(qa_id) if qa_id else None


def _set_assembly_committed_qa_id(session: Any, qa_id: str) -> None:
    updater = getattr(session, "update_state", None)
    if callable(updater):
        updater({_ASSEMBLY_COMMITTED_QA_ID_KEY: qa_id})


def clear_assembly_committed_qa_id(session: Any) -> None:
    """Clear per-user-turn assembly marker (also called from freeze rail)."""
    updater = getattr(session, "update_state", None)
    if callable(updater):
        updater({_ASSEMBLY_COMMITTED_QA_ID_KEY: None})


def _context_has_active_qa_work(ctx: AgentCallbackContext, qa_id: str) -> bool:
    """True when context carries native messages for the active QA turn.

    Aligns with ``_group_messages_by_qa`` / ``is_other_qa_message``: ReAct
    messages without explicit ``metadata.qa_id`` belong to ``current_qa_id``.
    """
    context = ctx.context
    if context is None:
        return False
    getter = getattr(context, "get_messages", None)
    if not callable(getter):
        return False
    for message in getter() or ():
        if not is_other_qa_message(message, qa_id):
            return True
    return False


def _should_skip_reassembly(
    ctx: AgentCallbackContext,
    session: Any,
    active_qa_id: str,
    entry: QABlockEntry | None,
) -> bool:
    if _is_frozen_entry(entry):
        return False
    if (
        entry is None
        and _is_resume_invoke(ctx)
        and not (ctx.context.get_messages() if ctx.context is not None else None)
    ):
        return False
    if _get_assembly_committed_qa_id(session) == active_qa_id:
        return True
    if _context_has_active_qa_work(ctx, active_qa_id):
        return True
    return False


def _clear_current_qa_pointer(session: Any, registry: QABlockRegistry, qa_id: str) -> None:
    if registry.current_qa_id != qa_id:
        return
    registry.current_qa_id = None
    save_registry(session, registry)
    if _get_assembly_committed_qa_id(session) == qa_id:
        clear_assembly_committed_qa_id(session)


def _set_pending_orphan_salvage(session: Any, qa_id: str) -> None:
    updater = getattr(session, "update_state", None)
    if callable(updater):
        updater({_PENDING_ORPHAN_SALVAGE_KEY: qa_id})


def _pop_pending_orphan_salvage(session: Any) -> str | None:
    getter = getattr(session, "get_state", None)
    if not callable(getter):
        return None
    qa_id = getter(_PENDING_ORPHAN_SALVAGE_KEY)
    if not qa_id:
        return None
    updater = getattr(session, "update_state", None)
    if callable(updater):
        updater({_PENDING_ORPHAN_SALVAGE_KEY: None})
    return str(qa_id)


class JiuClawQABlockAssemblyRail(DeepAgentRail):
    priority = 82

    def __init__(self, config: QABlockConfig | None = None):
        super().__init__()
        self._config = config or QABlockConfig()
        self._freeze_rail: Any | None = None

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    def attach_freeze_rail(self, freeze_rail: Any | None) -> None:
        """Wire freeze rail for orphan QA salvage on new user invoke."""
        self._freeze_rail = freeze_rail

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        """Detect orphan QA left from a prior user invoke.

        Registered as a DeepAgent outer-only hook (not bridged to inner
        ReAct invoke). Task-loop iterations only fire before_model_call;
        they do not re-enter this hook, so in-progress QA within the same
        outer invoke is not subject to orphan deferral here.
        """
        if not self._config.enabled:
            return
        if _is_resume_invoke(ctx):
            logger.debug("[QABlockAssemblyRail] before_invoke skipped: resume invoke")
            return

        session = resolve_actual_session(ctx.session)
        if session is None:
            logger.debug("[QABlockAssemblyRail] before_invoke skipped: no session")
            return
        clear_assembly_committed_qa_id(session)

        session_id = session_id_from_session(session)
        registry = load_registry(session)
        qa_id = registry.current_qa_id
        if not qa_id:
            logger.debug(
                "[QABlockAssemblyRail] before_invoke skipped: no current_qa_id session_id=%s",
                session_id,
            )
            return

        entry = registry.blocks.get(qa_id)
        if _is_frozen_entry(entry):
            _clear_current_qa_pointer(session, registry, qa_id)
            logger.info(
                "[QABlockAssemblyRail] repaired stale current_qa_id session_id=%s qa_id=%s",
                session_id,
                qa_id,
            )
            return

        if entry is None or not entry.freeze_committed_at:
            _set_pending_orphan_salvage(session, qa_id)
            logger.info(
                "[QABlockAssemblyRail] deferred orphan salvage session_id=%s qa_id=%s",
                session_id,
                qa_id,
            )

    async def _salvage_orphan_qa(
        self,
        ctx: AgentCallbackContext,
        session: Any,
        session_id: str,
        qa_id: str,
    ) -> bool:
        freeze_rail = self._freeze_rail
        if freeze_rail is None:
            return False
        try:
            await freeze_rail.freeze_current_qa_sync(
                session_id,
                agent=ctx.agent,
                session=session,
                status="interrupted",
            )
        except Exception as exc:
            logger.warning(
                "[QABlockAssemblyRail] orphan salvage freeze failed session_id=%s qa_id=%s: %s",
                session_id,
                qa_id,
                exc,
                exc_info=True,
            )
            return False

        registry = load_registry(session, force_reload=True)
        entry = registry.blocks.get(qa_id)
        if _is_frozen_entry(entry):
            if registry.current_qa_id == qa_id:
                _clear_current_qa_pointer(session, registry, qa_id)
            logger.info(
                "[QABlockAssemblyRail] orphan QA salvaged session_id=%s qa_id=%s",
                session_id,
                qa_id,
            )
            return True

        logger.warning(
            "[QABlockAssemblyRail] freeze completed but entry not fully frozen "
            "session_id=%s qa_id=%s current_qa_id=%s",
            session_id,
            qa_id,
            registry.current_qa_id,
        )
        return registry.current_qa_id != qa_id

    async def _run_deferred_orphan_salvage(
        self,
        ctx: AgentCallbackContext,
        session: Any,
        session_id: str,
    ) -> None:
        pending = _pop_pending_orphan_salvage(session)
        if not pending:
            return

        registry = load_registry(session)
        if registry.current_qa_id != pending:
            return

        salvaged = await self._salvage_orphan_qa(ctx, session, session_id, pending)
        if not salvaged:
            if self._freeze_rail is not None:
                registry = load_registry(session, force_reload=True)
            _clear_current_qa_pointer(session, registry, pending)
            logger.warning(
                "[QABlockAssemblyRail] deferred salvage failed, cleared orphan "
                "session_id=%s qa_id=%s",
                session_id,
                pending,
            )

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        if not self._config.enabled:
            return
        if ctx.context is None:
            logger.info("[QABlockAssemblyRail] skipped: context not ready")
            return

        session = resolve_actual_session(ctx.session)
        agent = ctx.agent
        if session is None or agent is None:
            return

        context_engine = resolve_context_engine(agent)
        if context_engine is None:
            return

        assembly_start = time.perf_counter()
        session_id = session_id_from_session(session)

        # 弹窗确认恢复场景(如工具权限"本次允许")，跳过QA组装，沿用当前上下文
        if _is_popup_confirmation_resume(ctx):
            logger.info(
                "[QABlockAssemblyRail] skip assembly for popup confirmation resume "
                "session_id=%s", session_id,
            )
            return

        await self._run_deferred_orphan_salvage(ctx, session, session_id)

        registry = load_registry(session)
        if registry.current_qa_id:
            active_qa_id = registry.current_qa_id
            entry = registry.blocks.get(active_qa_id)
            if _is_frozen_entry(entry):
                _clear_current_qa_pointer(session, registry, active_qa_id)
            elif (
                entry is None
                and _is_resume_invoke(ctx)
                and not (ctx.context.get_messages() if ctx.context is not None else None)
            ):
                logger.warning(
                    "[QABlockAssemblyRail] resume with empty context, clearing stale "
                    "current_qa_id session_id=%s qa_id=%s",
                    session_id,
                    active_qa_id,
                )
                _clear_current_qa_pointer(session, registry, active_qa_id)
            elif _should_skip_reassembly(ctx, session, active_qa_id, entry):
                logger.info(
                    "[QABlockAssemblyRail] skip re-assembly session_id=%s active_qa_id=%s "
                    "committed=%s has_native_work=%s",
                    session_id,
                    active_qa_id,
                    _get_assembly_committed_qa_id(session) == active_qa_id,
                    _context_has_active_qa_work(ctx, active_qa_id),
                )
                return
            else:
                logger.warning(
                    "[QABlockAssemblyRail] stale current_qa_id without assembly commit or "
                    "native work session_id=%s qa_id=%s",
                    session_id,
                    active_qa_id,
                )
                _clear_current_qa_pointer(session, registry, active_qa_id)

        context = ctx.context
        clear_assembly_qa_artifact_state(context)
        registry = maybe_compact_catalog_l1(registry, self._config)

        workspace_root = ""
        if self.workspace is not None:
            workspace_root = getattr(self.workspace, "root_path", "") or ""
        store = QABlockStore(workspace_root, session_id, self.sys_operation)
        registry, recovered = await reconcile_orphan_l0_blocks(
            registry,
            store,
            config=self._config,
        )
        if recovered:
            save_registry(session, registry)
            logger.info(
                "[QABlockAssemblyRail] reconciled orphan L0 blocks session_id=%s qa_ids=%s",
                session_id,
                recovered,
            )

        catalog_text = build_catalog_text(registry)
        prompt_builder = getattr(agent, "system_prompt_builder", None)
        if prompt_builder is not None:
            lang = getattr(prompt_builder, "language", None) or "cn"
            prompt_builder.add_section(
                build_catalog_section(registry, lang=lang, catalog_text=catalog_text)
            )
        else:
            logger.warning("[QABlockAssemblyRail] no system_prompt_builder session_id=%s", session_id)

        history = context_engine.get_history_qa_buffer(
            session_id,
            context.context_id(),
            max_blocks=self._config.history_qa_buffer_size,
        )

        token_counter = None
        counter_getter = getattr(context, "token_counter", None)
        if callable(counter_getter):
            token_counter = counter_getter()

        artifact_store = QAArtifactStore(session, workspace_root, self.sys_operation)

        layer = QABlockLayer(
            registry,
            history,
            store,
            token_counter=token_counter,
            config=self._config,
            artifact_store=artifact_store,
        )

        selected_qa_ids: list[str] | None = None
        if self._config.selector_enabled:
            next_query = extract_next_user_query(context.get_messages())
            model = resolve_selector_model(agent)
            selector = QABlockSelector(self._config)
            try:
                selected_qa_ids = await selector.select(
                    next_query,
                    registry,
                    history,
                    model=model,
                    catalog_text=catalog_text,
                )
            except Exception as exc:
                logger.warning(
                    "[QABlockAssemblyRail] selector failed, rule+last_n fallback "
                    "session_id=%s: %s",
                    session_id,
                    exc,
                    exc_info=True,
                )
                selected_qa_ids = fallback_rule_last_n(
                    next_query,
                    registry,
                    config=self._config,
                )
            ctx.extra[_PRELOADED_QA_IDS_KEY] = list(selected_qa_ids or [])
            await layer.hydrate_history_into_window(context, selected_qa_ids=selected_qa_ids)
        else:
            ctx.extra[_PRELOADED_QA_IDS_KEY] = []
            await layer.hydrate_history_into_window(context)

        qa_id, _ = allocate_qa_id(registry)
        registry.current_qa_id = qa_id
        save_registry(session, registry)
        _set_assembly_committed_qa_id(session, qa_id)

        window_qas = layer.build_window_qas(context)

        qa_mgr = context.get_qa_artifact_manager()
        if qa_mgr is not None and window_qas:
            proc_ctx = make_processor_ctx(context, sys_operation=self.sys_operation)
            store = qa_mgr.build_store(proc_ctx, proc_ctx.workspace)
            if qa_mgr.needs_history_artifact_work(context, store, window_qas):
                await qa_mgr.apply_artifact_to_context(
                    proc_ctx,
                    workspace=proc_ctx.workspace,
                    window_qas=window_qas,
                    context=context,
                )

        ctx.extra[_WINDOW_QAS_KEY] = window_qas
        ctx.extra[_LAYER_KEY] = layer

        preloaded = ctx.extra.get(_PRELOADED_QA_IDS_KEY, [])
        elapsed_ms = (time.perf_counter() - assembly_start) * 1000
        logger.info(
            "[QABlockAssemblyRail] assembled session_id=%s qa_id=%s elapsed_ms=%.1f "
            "selector_enabled=%s preloaded=%s window_qas=%s",
            session_id,
            qa_id,
            elapsed_ms,
            self._config.selector_enabled,
            preloaded,
            len(window_qas),
        )
