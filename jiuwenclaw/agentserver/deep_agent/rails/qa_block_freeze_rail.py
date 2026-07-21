# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Freeze completed QA blocks after each DeepAgent invoke (plan mode)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

from openjiuwen.core.context_engine.qa_artifact.window import make_processor_ctx
from openjiuwen.core.context_engine.qa_block.config import QABlockConfig
from openjiuwen.core.context_engine.qa_block.freezer import FreezeCommitResult, QABlockFreezer
from openjiuwen.core.context_engine.qa_block.messages import extract_qa_native_messages
from openjiuwen.core.context_engine.qa_block.registry import load_registry, save_registry
from openjiuwen.core.context_engine.qa_block.selector import resolve_summarizer_model
from openjiuwen.core.context_engine.qa_block.store import QABlockStore
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
from openjiuwen.core.single_agent.interrupt.state import INTERRUPTION_KEY
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, InvokeInputs
from openjiuwen.harness.rails.base import DeepAgentRail

from jiuwenclaw.agentserver.deep_agent.plan_pause_helpers import (
    post_agent_execute_for_session,
    resolve_actual_session,
    resolve_context_engine,
)
from jiuwenclaw.agentserver.deep_agent.rails.qa_block_assembly_rail import (
    clear_assembly_committed_qa_id,
)

from jiuwenclaw.agentserver.deep_agent.rails.utils import is_ask_user_question_interrupt

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


def _is_ask_user_question_interrupt(ctx: AgentCallbackContext) -> bool:
    """检测中断是否为工具权限确认弹窗(ask_user_question/popup)。

    与用户主动取消(CancelledError)不同：
    - 工具权限弹窗：result_type="interrupt" + 包含 interrupt_ids
    - 用户主动取消：不设置 result，直接抛 CancelledError

    这类中断应跳过QA卸载，沿用当前上下文。
    """
    return is_ask_user_question_interrupt(ctx)


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

    async def _clear_empty_current_qa_after_failed_freeze(
        self,
        session: Any,
        context_engine: Any,
        *,
        session_id: str,
        context: Any | None = None,
        persist_context: bool = True,
    ) -> None:
        """Drop stale in-progress pointer when freeze had nothing to commit."""
        registry = load_registry(session)
        qa_id = registry.current_qa_id
        if not qa_id:
            return

        if context is not None:
            getter = getattr(context, "get_messages", None)
            if callable(getter):
                messages = getter() or []
                native = extract_qa_native_messages(messages, registry)
                if native:
                    roles = {getattr(message, "role", None) for message in native}
                    if "user" in roles or "tool" in roles:
                        logger.info(
                            "[QABlockFreezeRail] skip clear after failed freeze: "
                            "native user/tool still present session_id=%s qa_id=%s",
                            session_id,
                            qa_id,
                        )
                        return

        registry.current_qa_id = None
        save_registry(session, registry)
        clear_assembly_committed_qa_id(session)
        if persist_context:
            await context_engine.save_contexts(session)
            await self._persist_freeze_checkpoint(session, session_id=session_id)
        else:
            # Registry pointer cleared; caller (orphan salvage) restores messages first,
            # then persists context to avoid checkpointing a stripped-without-user buffer.
            await self._persist_freeze_checkpoint(session, session_id=session_id)
        logger.info(
            "[QABlockFreezeRail] cleared empty current_qa_id after failed freeze "
            "session_id=%s qa_id=%s persist_context=%s",
            session_id,
            qa_id,
            persist_context,
        )

    async def _persist_freeze_checkpoint(self, session: Any, *, session_id: str) -> None:
        """Flush registry after freeze; inner ReAct post_run may have checkpointed early."""
        try:
            await post_agent_execute_for_session(session)
        except Exception as exc:
            logger.warning(
                "[QABlockFreezeRail] freeze checkpoint flush failed session_id=%s: %s",
                session_id,
                exc,
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
        persist_context: bool = True,
    ) -> None:
        """Emergency freeze before plan cancel checkpoint.

        ``persist_context=False`` skips ``save_contexts`` so callers (orphan salvage)
        can restore temporarily stripped current-round user messages before persisting.
        Registry/checkpoint flush still runs so ``current_qa_id`` updates are durable.
        """
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
            clear_assembly_committed_qa_id(actual_session)
            if persist_context:
                await context_engine.save_contexts(actual_session)
                await self._persist_freeze_checkpoint(actual_session, session_id=session_id)
            else:
                await self._persist_freeze_checkpoint(actual_session, session_id=session_id)
            logger.info(
                "[QABlockFreezeRail] cancel sync freeze done session_id=%s qa_id=%s status=%s "
                "persist_context=%s",
                session_id,
                entry.qa_id,
                status,
                persist_context,
            )
        else:
            await self._clear_empty_current_qa_after_failed_freeze(
                actual_session,
                context_engine,
                session_id=session_id,
                context=context,
                persist_context=persist_context,
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

        # 弹窗交互(ask_user_question)中断时不卸载QA，沿用当前上下文
        if _is_ask_user_question_interrupt(ctx):
            logger.info("[QABlockFreezeRail] skip freeze for ask_user_question interrupt session_id=%s", session_id)
            return

        # 弹窗确认恢复：仅当仍处于中断时跳过 freeze。
        # stream 外层 result 常为 None，不能再据此 skip，否则成功收尾会留下孤儿 QA。
        # invoke 看 result_type=interrupt；stream 以 session INTERRUPTION_KEY 为准。
        if isinstance(ctx.inputs, InvokeInputs) and isinstance(ctx.inputs.query, InteractiveInput):
            result = getattr(ctx.inputs, "result", None)
            still_interrupted = (
                (isinstance(result, dict) and result.get("result_type") == "interrupt")
                or bool(session.get_state(INTERRUPTION_KEY))
            )
            if still_interrupted:
                logger.info(
                    "[QABlockFreezeRail] skip freeze for popup confirmation resume session_id=%s",
                    session_id,
                )
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
            await self._clear_empty_current_qa_after_failed_freeze(
                session,
                context_engine,
                session_id=session_id,
                context=context,
            )
            return

        clear_assembly_committed_qa_id(session)
        ctx.extra[_FREEZE_DONE_KEY] = entry.qa_id
        await context_engine.save_contexts(session)
        await self._persist_freeze_checkpoint(session, session_id=session_id)
        logger.info(
            "[QABlockFreezeRail] freeze committed session_id=%s qa_id=%s status=%s persist=%s",
            session_id,
            entry.qa_id,
            freeze_status,
            persist_mode,
        )
