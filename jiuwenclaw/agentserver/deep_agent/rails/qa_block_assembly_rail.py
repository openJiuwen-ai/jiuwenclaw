# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Assemble QA catalog + selector preload + hydrate before the first model call (plan mode)."""

from __future__ import annotations

import logging
import time
from typing import Any

from openjiuwen.core.context_engine.qa_artifact.store import QAArtifactStore
from openjiuwen.core.context_engine.qa_artifact.assembly_state import clear_assembly_qa_artifact_state
from openjiuwen.core.context_engine.qa_artifact.window import make_processor_ctx
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
from openjiuwen.core.context_engine.qa_block.store import QABlockStore
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
from openjiuwen.core.single_agent.interrupt.state import RESUME_USER_INPUT_KEY

from jiuwenclaw.agentserver.deep_agent.plan_pause_helpers import (
    resolve_actual_session,
    resolve_context_engine,
)

logger = logging.getLogger(__name__)

_ASSEMBLED_KEY = "_qa_block_assembled"
_WINDOW_QAS_KEY = "_window_qas"
_LAYER_KEY = "_qa_block_layer"
_CURRENT_QA_KEY = "_current_qa_id"
_PRELOADED_QA_IDS_KEY = "_preloaded_qa_ids"


def _is_task_continuation(ctx: AgentCallbackContext, next_query: str) -> bool:
    resume_input = ctx.extra.get(RESUME_USER_INPUT_KEY)
    if isinstance(resume_input, InteractiveInput):
        return True
    if resume_input is not None and not next_query.strip():
        return True
    return False


def _last_n_history_qa_ids(registry: Any, n: int = 1) -> list[str]:
    if n <= 0:
        return []
    entries = sorted(
        (entry for entry in registry.blocks.values() if entry.is_history),
        key=lambda entry: entry.qa_index,
    )
    if not entries:
        return []
    return [entry.qa_id for entry in entries[-n:]]


class JiuClawQABlockAssemblyRail(DeepAgentRail):
    priority = 82

    def __init__(self, config: QABlockConfig | None = None):
        super().__init__()
        self._config = config or QABlockConfig()

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        if not self._config.enabled:
            return
        if ctx.extra.get(_ASSEMBLED_KEY):
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
        session_id = session.get_session_id() if hasattr(session, "get_session_id") else ""
        context = ctx.context
        clear_assembly_qa_artifact_state(context)
        registry = load_registry(session)
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
            is_continuation = _is_task_continuation(ctx, next_query)
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
            if not selected_qa_ids and is_continuation:
                selected_qa_ids = _last_n_history_qa_ids(
                    registry, n=self._config.max_preload_blocks
                )
                logger.info(
                    "[QABlockAssemblyRail] task continuation fallback "
                    "session_id=%s preloaded=%s reason=no_query_history_required",
                    session_id,
                    selected_qa_ids,
                )
            ctx.extra[_PRELOADED_QA_IDS_KEY] = list(selected_qa_ids or [])
            await layer.hydrate_history_into_window(context, selected_qa_ids=selected_qa_ids)
        else:
            ctx.extra[_PRELOADED_QA_IDS_KEY] = []
            await layer.hydrate_history_into_window(context)

        qa_id, _ = allocate_qa_id(registry)
        registry.current_qa_id = qa_id
        save_registry(session, registry)

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

        ctx.extra[_ASSEMBLED_KEY] = True
        ctx.extra[_WINDOW_QAS_KEY] = window_qas
        ctx.extra[_LAYER_KEY] = layer
        ctx.extra[_CURRENT_QA_KEY] = qa_id

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
