# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved
import copy
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Tuple, Union

from openjiuwen.core.application.llm_agent.rails.invoke_result_adapter_rail import InvokeResultAdapterRail
from openjiuwen.core.application.llm_agent.rails.memory_rail import MemoryRail
from openjiuwen.core.single_agent.rail.base import AgentRail
from openjiuwen.core.common.logging import logger
from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig
from openjiuwen.core.foundation.llm import ModelConfig
from openjiuwen.core.foundation.tool import Tool
from openjiuwen.core.session import Config
from openjiuwen.core.session.agent import Session
from openjiuwen.core.single_agent.agents.react_agent import ReActAgentConfig as _NewReActAgentConfig, ReActAgent
from openjiuwen.core.single_agent.legacy import (
    LegacyReActAgentConfig as ReActAgentConfig,
    WorkflowSchema,
    PluginSchema,
)
from openjiuwen.core.single_agent.legacy.agent import WorkflowFactory
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.workflow import Workflow, WorkflowCard, generate_workflow_key


# ---------------------------------------------------------------------------
# Config conversion
# ---------------------------------------------------------------------------

def _convert_legacy_config(legacy: ReActAgentConfig) -> _NewReActAgentConfig:
    """Convert LegacyReActAgentConfig to ReActAgentConfig."""
    config = _NewReActAgentConfig()

    config.mem_scope_id = legacy.memory_scope_id or ""
    config.prompt_template = list(legacy.prompt_template)
    config.max_iterations = legacy.constrain.max_iteration
    config.context_engine_config = ContextEngineConfig(
        max_context_message_num=200,
        default_window_round_num=legacy.constrain.reserved_max_chat_rounds,
    )

    if legacy.model is not None:
        model_info = legacy.model.model_info
        provider = legacy.model.model_provider or ""
        model_name = ""
        api_key = ""
        api_base = ""
        verify_ssl = False

        if model_info is not None:
            model_name = getattr(model_info, "model", "") or getattr(model_info, "model_name", "") or ""
            api_key = getattr(model_info, "api_key", "") or ""
            api_base = getattr(model_info, "api_base", "") or ""
            verify_ssl = getattr(model_info, "verify_ssl", False) or False

        if provider and model_name:
            config.configure_model_client(
                provider=provider,
                api_key=api_key,
                api_base=api_base,
                model_name=model_name,
                verify_ssl=verify_ssl,
            )
        else:
            config.model_provider = provider
            config.model_name = model_name
            config.api_key = api_key
            config.api_base = api_base

    return config


# ---------------------------------------------------------------------------
# LLMAgentRefactor
# ---------------------------------------------------------------------------

class LLMAgentRefactor:
    """LLMAgent-compatible adapter backed by ReActAgentForStudio.

    Aligns with the full public interface of legacy.BaseAgent (and LLMAgent)
    without inheriting from it.
    """

    def __init__(self, agent_config: ReActAgentConfig) -> None:
        # Replicate legacy.BaseAgent.__init__ state
        self._config_wrapper = Config()
        self._config_wrapper.set_agent_config(agent_config)
        self.agent_config = agent_config
        self._config = self._config_wrapper

        self._context_engine = self._create_context_engine()
        self._tools: List[Tool] = []
        self._workflows: List[Workflow] = []

        # Build inner agent
        react_config = _convert_legacy_config(agent_config)
        card = AgentCard(
            id=agent_config.id,
            name=agent_config.id,
            description=agent_config.description,
        )
        self._inner = ReActAgent(card)
        self._inner.configure(react_config)

        # Pending rails: registered lazily on first invoke (register_rail is async)
        self._pending_rails: List[AgentRail] = []

        # Result adapter rail: converts raw ReActAgent result to legacy schema
        self._pending_rails.append(InvokeResultAdapterRail())

        # MemoryRail: long-term memory integration
        _enable_memory = bool(
            agent_config.memory_scope_id and (
                agent_config.agent_memory_config.enable_long_term_mem
                or len(agent_config.agent_memory_config.mem_variables)
            )
        )
        if _enable_memory:
            self._pending_rails.append(MemoryRail(
                mem_scope_id=agent_config.memory_scope_id,
                agent_memory_config=agent_config.agent_memory_config,
            ))

    # ------------------------------------------------------------------
    # legacy.BaseAgent interface: config / properties
    # ------------------------------------------------------------------

    def config(self) -> Config:
        return self._config_wrapper

    @property
    def tools(self) -> List[Tool]:
        return self._tools

    @property
    def workflows(self) -> List[Workflow]:
        return self._workflows

    @property
    def context_engine(self) -> ContextEngine:
        return self._context_engine

    async def _ensure_rails_registered(self) -> None:
        if self._pending_rails:
            for rail in self._pending_rails:
                await self._inner.register_rail(rail)
            self._pending_rails.clear()

    def _create_context_engine(self) -> ContextEngine:
        if (hasattr(self.agent_config, 'constrain') and
                hasattr(self.agent_config.constrain, 'reserved_max_chat_rounds')):
            max_rounds = self.agent_config.constrain.reserved_max_chat_rounds
        else:
            max_rounds = 10
        context_config = ContextEngineConfig(max_context_message_num=max_rounds * 2)
        return ContextEngine(config=context_config)

    # ------------------------------------------------------------------
    # legacy.BaseAgent interface: dynamic configuration
    # ------------------------------------------------------------------

    def add_prompt(self, prompt_template: List[Dict]) -> None:
        if hasattr(self.agent_config, 'prompt_template'):
            current_prompt_template = copy.deepcopy(self.agent_config.prompt_template)
            current_prompt_template.extend(copy.deepcopy(prompt_template))
            self.set_prompt_template(current_prompt_template)
        else:
            logger.warning(
                "%s has no prompt_template field, add_prompt operation ignored",
                self.agent_config.__class__.__name__,
            )

    def set_prompt_template(self, prompt_template: List[Dict]) -> None:
        self.agent_config.prompt_template = copy.deepcopy(prompt_template)
        self._inner.config.prompt_template = self.agent_config.prompt_template

    def add_tools(self, tools: List[Tool]) -> None:
        from openjiuwen.core.runner import Runner
        for tool in tools:
            if tool.card.name not in self.agent_config.tools:
                self.agent_config.tools.append(tool.card.name)
            if hasattr(self.agent_config, 'plugins'):
                existing_plugin_names = {p.name for p in self.agent_config.plugins}
                if tool.card.name not in existing_plugin_names:
                    self.agent_config.plugins.append(self._tool_to_plugin_schema(tool))
            existing_tool_names = {t.card.name for t in self._tools}
            if tool.card.name not in existing_tool_names:
                self._tools.append(tool)
            Runner.resource_mgr.add_tool(tool=[tool], tag=self.agent_config.id)
            self._inner.ability_manager.add(tool.card)

    def add_workflows(
            self,
            workflows: List[Union[Workflow, Callable[[], Workflow]]]
    ) -> None:
        logger.info("LLMAgentRefactor.add_workflows called with %d workflows", len(workflows))

        def make_workflow_provider(workflow):
            def provider():
                return workflow
            return provider

        for item in workflows:
            workflow_card = None
            provider = None
            is_provider = True
            if isinstance(item, WorkflowFactory):
                provider = item
                workflow_card = provider.card()
            elif callable(item) and hasattr(item, 'id') and hasattr(item, 'version'):
                provider = item
                workflow_card = WorkflowCard(
                    id=getattr(item, 'id'),
                    name=getattr(item, 'name', None),
                    description=getattr(item, 'description', None),
                    version=getattr(item, 'version'),
                    input_params=getattr(item, "input_params", None) or getattr(item, "inputs", None),
                )
            elif callable(item):
                raise ValueError(
                    "Callable workflow provider must have 'id' and 'version' attributes. "
                    "Use @workflow_provider decorator or WorkflowFactory class."
                )
            else:
                provider = make_workflow_provider(item)
                workflow_card = item.card
                is_provider = False

            workflow_key = generate_workflow_key(workflow_card.id, workflow_card.version)
            existing_keys = {
                generate_workflow_key(w.id, w.version)
                for w in self.agent_config.workflows
            }

            if workflow_key not in existing_keys:
                self.agent_config.workflows.append(WorkflowSchema(
                    id=workflow_card.id,
                    name=workflow_card.name,
                    version=workflow_card.version,
                    description=workflow_card.description or "",
                    input_params=workflow_card.input_params,
                ))

            # Sync card into inner agent's ability_manager
            workflow_card_copy = copy.deepcopy(workflow_card)
            workflow_card_copy.id = workflow_key
            self._inner.ability_manager.add(workflow_card_copy)

            try:
                from openjiuwen.core.runner import Runner
                Runner.resource_mgr.add_workflow(
                    card=workflow_card_copy, workflow=provider, tag=self.agent_config.id
                )
            except Exception as e:
                logger.error("Failed to add workflow to global resource_mgr: %s", e)

    def remove_workflows(self, workflows: List[Tuple[str, str]]) -> None:
        logger.info("LLMAgentRefactor.remove_workflows called with %d workflows", len(workflows))
        from openjiuwen.core.runner import Runner
        for workflow_id, workflow_version in workflows:
            workflow_key = generate_workflow_key(workflow_id, workflow_version)
            remaining, workflow_name = [], None
            for w in self.agent_config.workflows:
                if w.id == workflow_id and w.version == workflow_version:
                    workflow_name = w.name
                else:
                    remaining.append(w)
            self.agent_config.workflows = remaining
            try:
                Runner.resource_mgr.remove_workflow(workflow_key)
            except Exception as e:
                logger.error("Failed to remove workflow from global resource_mgr: %s", e)
            if workflow_name is not None:
                self._inner.ability_manager.remove(workflow_name)

    def bind_workflows(self, workflows: List[Workflow]) -> None:
        self.add_workflows(workflows)

    def add_plugins(self, plugins: List) -> None:
        if hasattr(self.agent_config, 'plugins'):
            existing_names = {p.name for p in self.agent_config.plugins}
            for plugin in plugins:
                if plugin.name not in existing_names:
                    self.agent_config.plugins.append(plugin)
                    existing_names.add(plugin.name)
        else:
            logger.warning(
                "%s has no plugins field, add_plugins operation ignored",
                self.agent_config.__class__.__name__,
            )

    @staticmethod
    def _tool_to_plugin_schema(tool: Tool) -> PluginSchema:
        inputs = {"type": "object", "properties": {}, "required": []}
        if hasattr(tool, 'params') and tool.params:
            for param in tool.params:
                inputs["properties"][param.name] = {"type": param.type, "description": param.description}
                if param.required:
                    inputs["required"].append(param.name)
        return PluginSchema(
            id=tool.card.id,
            name=tool.card.name,
            description=getattr(tool, 'description', ""),
            inputs=inputs,
        )

    # ------------------------------------------------------------------
    # Invocation — pure delegation, no output modification
    # ------------------------------------------------------------------

    async def invoke(self, inputs: Dict, session: Session = None) -> Dict:
        await self._ensure_rails_registered()
        return await self._inner.invoke(inputs, session)

    async def stream(self, inputs: Dict, session: Session = None) -> AsyncIterator[Any]:
        await self._ensure_rails_registered()
        async for chunk in self._inner.stream(inputs, session):
            yield chunk

    async def clear_session(self, session_id: str = "default_session"):
        from openjiuwen.core.runner import Runner
        await Runner.release(session_id=session_id)
        await self.context_engine.clear_context(session_id=session_id)


# ---------------------------------------------------------------------------
# Factory functions (same signatures as llm_agent.py)
# ---------------------------------------------------------------------------

def create_llm_agent_config(
    agent_id: str,
    agent_version: str,
    description: str,
    workflows: List[WorkflowSchema],
    plugins: List[PluginSchema],
    model: ModelConfig,
    prompt_template: List[Dict],
    tools: Optional[List[str]] = None):
    """Create LLM Agent configuration - same signature as llm_agent.create_llm_agent_config."""
    if tools is None:
        tools = []
    return ReActAgentConfig(
        id=agent_id,
        version=agent_version,
        description=description,
        workflows=workflows,
        plugins=plugins,
        model=model,
        prompt_template=prompt_template,
        tools=tools,
    )


def create_llm_agent(
    agent_config: ReActAgentConfig,
    workflows: List[Workflow] = None,
    tools: List[Tool] = None):
    """Create LLM Agent - same signature as llm_agent.create_llm_agent."""
    agent = LLMAgentRefactor(agent_config)
    if workflows:
        agent.add_workflows(workflows)
    agent.add_tools(tools or [])
    return agent


__all__ = [
    "LLMAgentRefactor",
    "create_llm_agent_config",
    "create_llm_agent",
]
