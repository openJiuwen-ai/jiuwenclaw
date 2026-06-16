# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""QA artifact rail: async compact triggers + load_qa_index tool (plan mode)."""

from __future__ import annotations

import logging
from typing import Any

from openjiuwen.core.context_engine.context.session_memory_manager import SessionMemoryConfig
from openjiuwen.core.context_engine.qa_artifact import (
    LOAD_QA_INDEX_TOOL_NAME,
    LoadQaIndexTool,
    QAArtifactConfig,
    build_qa_artifact_manager,
)
from openjiuwen.core.context_engine.qa_artifact.window import build_window_qas_from_context
from openjiuwen.core.context_engine.qa_block.layer import QABlockLayer
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs
from openjiuwen.harness.rails.base import DeepAgentRail

from jiuwenclaw.agentserver.deep_agent.plan_pause_helpers import (
    resolve_actual_session,
    session_id_from_session,
)
from jiuwenclaw.agentserver.deep_agent.rails.qa_block_assembly_rail import (
    _LAYER_KEY,
    _WINDOW_QAS_KEY,
)
from jiuwenclaw.agentserver.deep_agent.session_active_ctx_registry import (
    SessionActiveCtxRegistry,
)

logger = logging.getLogger(__name__)


class JiuClawQAArtifactRail(DeepAgentRail):
    priority = 84

    def __init__(
        self,
        qa_config: QAArtifactConfig | None = None,
        session_memory_config: SessionMemoryConfig | None = None,
    ):
        super().__init__()
        self._qa_config = qa_config or QAArtifactConfig()
        self._session_memory_config = session_memory_config or SessionMemoryConfig()
        self._mgr = build_qa_artifact_manager(self._qa_config, self._session_memory_config)
        self._load_qa_tool: LoadQaIndexTool | None = None
        self._active_ctx_registry = SessionActiveCtxRegistry()

    @property
    def enabled(self) -> bool:
        return self._qa_config.enabled

    @property
    def qa_artifact_manager(self) -> Any:
        return self._mgr

    def release_active_ctx_for_session(self, session_id: str) -> None:
        """Explicit cleanup when a session is destroyed outside normal after_invoke."""
        self._active_ctx_registry.release(session_id)

    def bind_artifact_model_defaults(
        self,
        model_config: Any,
        model_client_config: Any,
    ) -> None:
        self._mgr.bind_model_defaults(model_config, model_client_config)

    def init(self, agent) -> None:
        super().init(agent)
        config = getattr(getattr(agent, "react_agent", None), "_config", None)
        if config is not None:
            model_config = getattr(config, "model_config_obj", None)
            model_client_config = getattr(config, "model_client_config", None)
            if self._session_memory_config.model is None:
                self._session_memory_config.model = model_config
            if self._session_memory_config.model_client is None:
                self._session_memory_config.model_client = model_client_config
            self.bind_artifact_model_defaults(model_config, model_client_config)

        agent_id = getattr(getattr(agent, "card", None), "id", None)
        self._load_qa_tool = LoadQaIndexTool(self._render_qa_index_for_tool, agent_id=agent_id)
        ability_manager = getattr(agent, "ability_manager", None)
        if ability_manager is not None:
            for existing in list(ability_manager.list() or []):
                if existing.name == LOAD_QA_INDEX_TOOL_NAME:
                    ability_manager.remove(existing.name)
            ability_manager.add(self._load_qa_tool.card)
            logger.info("[QAArtifactRail] load_qa_index registered agent_id=%s", agent_id)

    def uninit(self, agent) -> None:
        ability_manager = getattr(agent, "ability_manager", None)
        if ability_manager is not None and self._load_qa_tool is not None:
            ability_manager.remove(self._load_qa_tool.card.name)
        self._active_ctx_registry.clear()

    async def _render_qa_index_for_tool(self, qa_id: str, **kwargs: Any) -> str:
        session = kwargs.get("session")
        ctx = self._active_ctx_registry.resolve(session=session)
        if ctx is None or self.workspace is None:
            session_id = session_id_from_session(resolve_actual_session(session))
            logger.warning(
                "[QAArtifactRail] load_qa_index missing active ctx session_id=%s qa_id=%s "
                "pinned_sessions=%s",
                session_id,
                qa_id,
                len(self._active_ctx_registry),
            )
            return f"QA {qa_id} unavailable: no active session context"
        layer = ctx.extra.get(_LAYER_KEY)
        if not isinstance(layer, QABlockLayer):
            return f"QA {qa_id} unavailable: qa layer missing"
        qa_ref = await layer.qa_ref_for_index(qa_id, ctx.context)
        return await self._mgr.render_index(ctx, workspace=self.workspace, qa=qa_ref)

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        if not self._qa_config.enabled:
            return
        session_id = self._active_ctx_registry.pin(ctx)
        if session_id:
            logger.debug("[QAArtifactRail] pinned active ctx session_id=%s", session_id)

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Kick async history compaction after assembly hydrate, before context window trim."""
        if not self._qa_config.enabled:
            return
        self._active_ctx_registry.pin(ctx)
        if ctx.context is None or self.workspace is None:
            return

        layer = ctx.extra.get(_LAYER_KEY)
        if isinstance(layer, QABlockLayer):
            window_qas = layer.build_window_qas(ctx.context)
        else:
            window_qas = ctx.extra.get(_WINDOW_QAS_KEY)
            if not window_qas:
                sys_operation = getattr(ctx, "sys_operation", None)
                window_qas = build_window_qas_from_context(ctx.context, sys_operation=sys_operation)
        if not window_qas:
            return

        window_tokens = sum(getattr(ref, "tokens", 0) for ref in window_qas)
        started = await self._mgr.maybe_compact_history_block(
            ctx,
            workspace=self.workspace,
            window_qas=window_qas,
            window_tokens=window_tokens,
        )
        if started:
            logger.info(
                "[QAArtifactRail] before_model_call trigger1 scheduled window_qas=%s",
                len(window_qas),
            )

    async def after_model_call(self, ctx: AgentCallbackContext) -> None:
        if not self._qa_config.enabled:
            return
        self._active_ctx_registry.pin(ctx)
        if ctx.context is None or self.workspace is None:
            return
        sys_operation = getattr(ctx, "sys_operation", None)
        window_qas = build_window_qas_from_context(ctx.context, sys_operation=sys_operation)
        if not window_qas:
            return

        window_tokens = sum(getattr(ref, "tokens", 0) for ref in window_qas)
        sm_trigger = self._session_memory_config.trigger_tokens

        started = await self._mgr.maybe_compact_history_block(
            ctx,
            workspace=self.workspace,
            window_qas=window_qas,
            window_tokens=window_tokens,
        )
        if not started and window_tokens >= sm_trigger:
            await self._mgr.on_session_memory_trigger(
                ctx,
                workspace=self.workspace,
                window_qas=window_qas,
            )

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        self._active_ctx_registry.pop(ctx)

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        if not self._qa_config.enabled:
            return
        self._active_ctx_registry.pin(ctx)
        inputs = ctx.inputs
        if not isinstance(inputs, ToolCallInputs):
            return
        if inputs.tool_name != LOAD_QA_INDEX_TOOL_NAME:
            return
        qa_id = None
        if isinstance(inputs.tool_args, dict):
            qa_id = inputs.tool_args.get("qa_id")
        tool_msg = inputs.tool_msg
        if qa_id and tool_msg is not None:
            QABlockLayer.annotate_tool_result_source_qa(tool_msg, str(qa_id))
            logger.info("[QAArtifactRail] annotated source_qa_id=%s", qa_id)
