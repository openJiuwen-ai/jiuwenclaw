# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Freeze completed QA blocks after each DeepAgent invoke (plan mode)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

from openjiuwen.core.context_engine.qa_artifact.window import make_processor_ctx
from openjiuwen.core.context_engine.qa_block.config import QABlockConfig
from openjiuwen.core.context_engine.qa_block.freezer import FreezeCommitResult, QABlockFreezer
from openjiuwen.core.context_engine.qa_block.registry import load_registry
from openjiuwen.core.context_engine.qa_block.selector import resolve_summarizer_model
from openjiuwen.core.context_engine.qa_block.store import QABlockStore
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, InvokeInputs
from openjiuwen.harness.rails.base import DeepAgentRail

from jiuwenclaw.agentserver.deep_agent.plan_pause_helpers import (
    resolve_actual_session,
    resolve_context_engine,
)

logger = logging.getLogger(__name__)

_PRELOADED_QA_IDS_KEY = "_preloaded_qa_ids"
_FREEZE_DONE_KEY = "_qa_block_freeze_done"


def infer_qa_status(ctx: AgentCallbackContext) -> Literal["completed", "interrupted"]:
    inputs = ctx.inputs
    result = getattr(inputs, "result", None) if isinstance(inputs, InvokeInputs) else None
    if isinstance(result, dict):
        result_type = str(result.get("result_type") or "")
        if result_type == "interrupt":
            return "interrupted"
    if ctx.extra.get("_qa_block_freeze_interrupted"):
        return "interrupted"
    return "completed"


class JiuClawQABlockFreezeRail(DeepAgentRail):
    priority = 75

    def __init__(self, config: QABlockConfig | None = None):
        super().__init__()
        self._config = config or QABlockConfig()
        self._freezer = QABlockFreezer(self._config)
        self._qa_artifact_mgr: Any | None = None

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    def attach_qa_artifact(self, mgr: Any | None) -> None:
        """Wire QAArtifactManager for bounded overview await before freeze."""
        self._qa_artifact_mgr = mgr

    async def _maybe_await_overview_before_freeze(self, session: Any) -> None:
        mgr = self._qa_artifact_mgr
        if mgr is None or session is None:
            return
        registry = load_registry(session)
        qa_id = registry.current_qa_id
        if not qa_id:
            return
        finished = await mgr.await_pending_overview(
            qa_id,
            session_id=registry.session_id,
            timeout_s=self._config.freeze_overview_await_s,
        )
        if not finished:
            logger.info(
                "[QABlockFreezeRail] overview await timeout before freeze qa_id=%s",
                qa_id,
            )

    def init(self, agent) -> None:
        super().init(agent)
        config = getattr(getattr(agent, "react_agent", None), "_config", None)
        if config is None:
            config = getattr(getattr(agent, "_react_agent", None), "_config", None)
        if config is not None:
            model_config = getattr(config, "model_config_obj", None)
            model_client_config = getattr(config, "model_client_config", None)
            self.bind_summarizer_model_defaults(model_config, model_client_config)

    def bind_summarizer_model_defaults(
        self,
        model_config: Any,
        model_client_config: Any,
    ) -> None:
        self._freezer.bind_summarizer_model_defaults(model_config, model_client_config)

    async def _schedule_freeze_artifact_produce_async(
        self,
        *,
        _session: Any,
        context: Any,
        qa_id: str,
        native_messages: list,
    ) -> None:
        mgr = self._qa_artifact_mgr
        if mgr is None or self.workspace is None:
            return
        artifact_ctx = make_processor_ctx(context, sys_operation=self.sys_operation)
        mgr.schedule_freeze_artifact_produce(
            artifact_ctx,
            workspace=self.workspace,
            qa_id=qa_id,
            native_messages=native_messages,
        )

    def _on_freeze_commit(self, session: Any, context: Any, commit: FreezeCommitResult) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning(
                "[QABlockFreezeRail] no event loop for freeze produce schedule qa_id=%s",
                commit.entry.qa_id,
            )
            return
        loop.create_task(
            self._schedule_freeze_artifact_produce_async(
                _session=session,
                context=context,
                qa_id=commit.entry.qa_id,
                native_messages=commit.native_messages,
            )
        )

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        if not self._config.enabled:
            return
        await self._freeze_session(ctx, persist_mode="async")

    async def freeze_current_qa_sync(
        self,
        session_id: str,
        *,
        agent: Any,
        session: Any = None,
        status: Literal["completed", "interrupted"] = "interrupted",
    ) -> None:
        """Emergency freeze before plan cancel checkpoint."""
        if not self._config.enabled:
            return
        context_engine = resolve_context_engine(agent)
        if context_engine is None:
            logger.info("[QABlockFreezeRail] cancel freeze skipped: no context_engine")
            return

        if session is None:
            logger.info("[QABlockFreezeRail] cancel freeze skipped: no session session_id=%s", session_id)
            return

        actual_session = resolve_actual_session(session)
        context = context_engine.get_context(session_id=session_id)
        if context is None:
            logger.info("[QABlockFreezeRail] cancel freeze skipped: no context session_id=%s", session_id)
            return

        workspace_root = ""
        if self.workspace is not None:
            workspace_root = getattr(self.workspace, "root_path", "") or ""
        store = QABlockStore(workspace_root, session_id, self.sys_operation)
        history = context_engine.get_history_qa_buffer(
            session_id,
            context.context_id(),
            max_blocks=self._config.history_qa_buffer_size,
        )

        await self._maybe_await_overview_before_freeze(actual_session)
        summarizer_model = resolve_summarizer_model(agent)
        entry = await self._freezer.freeze(
            actual_session,
            context,
            history,
            store,
            status=status,
            persist_mode="sync",
            summarizer_model=summarizer_model,
            post_commit=lambda commit, s=actual_session, c=context: self._on_freeze_commit(s, c, commit),
        )
        if entry is not None:
            await context_engine.save_contexts(actual_session)
            logger.info(
                "[QABlockFreezeRail] cancel sync freeze done session_id=%s qa_id=%s status=%s",
                session_id,
                entry.qa_id,
                status,
            )

    async def _freeze_session(
        self,
        ctx: AgentCallbackContext,
        *,
        persist_mode: Literal["async", "sync"],
        status: Literal["completed", "interrupted"] | None = None,
    ) -> None:
        if ctx.extra.get(_FREEZE_DONE_KEY):
            logger.info("[QABlockFreezeRail] freeze skipped: already done this invoke")
            return

        session = resolve_actual_session(ctx.session)
        agent = ctx.agent
        if session is None or agent is None:
            return

        context_engine = resolve_context_engine(agent)
        if context_engine is None:
            return

        session_id = session.get_session_id() if hasattr(session, "get_session_id") else ""
        context = context_engine.get_context(session_id=session_id)
        if context is None:
            logger.info("[QABlockFreezeRail] freeze skipped: context not in pool session_id=%s", session_id)
            return

        workspace_root = ""
        if self.workspace is not None:
            workspace_root = getattr(self.workspace, "root_path", "") or ""
        store = QABlockStore(workspace_root, session_id, self.sys_operation)
        history = context_engine.get_history_qa_buffer(
            session_id,
            context.context_id(),
            max_blocks=self._config.history_qa_buffer_size,
        )
        freeze_status = status or infer_qa_status(ctx)
        preloaded = ctx.extra.get(_PRELOADED_QA_IDS_KEY)
        await self._maybe_await_overview_before_freeze(session)
        summarizer_model = resolve_summarizer_model(agent)
        entry = await self._freezer.freeze(
            session,
            context,
            history,
            store,
            status=freeze_status,
            persist_mode=persist_mode,
            preloaded_qa_ids=preloaded if isinstance(preloaded, list) else None,
            summarizer_model=summarizer_model,
            post_commit=lambda commit, s=session, c=context: self._on_freeze_commit(s, c, commit),
        )
        if entry is None:
            return

        ctx.extra[_FREEZE_DONE_KEY] = entry.qa_id
        await context_engine.save_contexts(session)
        logger.info(
            "[QABlockFreezeRail] freeze committed session_id=%s qa_id=%s status=%s persist=%s",
            session_id,
            entry.qa_id,
            freeze_status,
            persist_mode,
        )
