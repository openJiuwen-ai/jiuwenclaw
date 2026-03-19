# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Built-in agent factory builder.

Constructs a default agent factory callable from RLConfig.runtime
parameters and registered tools, so users don't need to write one
for standard use cases.
"""

from typing import Any, List

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.dev_tools.agentrl.config.schemas import AgentRuntimeConfig
from openjiuwen.dev_tools.agentrl.coordinator.schemas import RLTask


class AgentFactory:
    """Callable factory that creates ReActAgent instances for each RL task.

    ``proxy_url`` must be set (by MainTrainer) before the first call.
    """

    def __init__(
        self,
        system_prompt: str,
        tools: List[Any],
        tool_names: List[str],
        temperature: float,
        max_new_tokens: int,
        top_p: float,
        presence_penalty: float,
        frequency_penalty: float,
    ) -> None:
        self._system_prompt = system_prompt
        self._tools = tools
        self._tool_names = tool_names
        self._temperature = temperature
        self._max_new_tokens = max_new_tokens
        self._top_p = top_p
        self._presence_penalty = presence_penalty
        self._frequency_penalty = frequency_penalty
        self.proxy_url: str | None = None

    def __call__(self, rl_task: RLTask):
        """Create and configure a ReActAgent instance for the given RL task."""
        from openjiuwen.core.single_agent import ReActAgent, ReActAgentConfig
        from openjiuwen.core.single_agent.schema.agent_card import AgentCard

        if not self.proxy_url:
            raise build_error(
                StatusCode.AGENT_RL_PROXY_NOT_INITIALIZED,
                error_msg="proxy_url has not been set on AgentFactory, "
                          "BackendProxy must be started before creating agents",
            )

        # Use task_id as part of the agent card ID so that each task gets its
        # own callback namespace in Runner.callback_framework.  A shared id
        # ("rl_agent") causes all parallel workers to share the same event
        # namespace, making every registered TrajectoryCollectionRail receive
        # LLM-call callbacks from *all* concurrent tasks — producing rollouts
        # that contain turns from multiple different questions.
        agent_card = AgentCard(
            id=f"rl_agent_{rl_task.task_id}",
            name="RLTrainingAgent",
            description="RL training agent based on ReActAgent",
        )

        config = ReActAgentConfig()
        config.configure_model_client(
            provider="OpenAI",
            api_key="EMPTY",
            api_base=f"{self.proxy_url}/v1",
            model_name="agentrl",
        )
        # configure_model_client does not accept timeout; override the field directly
        # to set the 300 s timeout for vLLM inference.
        if config.model_client_config is not None:
            config.model_client_config.timeout = 300
        # Apply inference hyper-parameters to the model request config so that
        # max_tokens, temperature, top_p, etc. are actually sent to vLLM.
        if config.model_config_obj is not None:
            config.model_config_obj.max_tokens = self._max_new_tokens
            config.model_config_obj.temperature = self._temperature
            config.model_config_obj.top_p = self._top_p
            if self._presence_penalty != 0.0:
                config.model_config_obj.presence_penalty = self._presence_penalty
            if self._frequency_penalty != 0.0:
                config.model_config_obj.frequency_penalty = self._frequency_penalty
        config.configure_prompt_template([
            {"role": "system", "content": self._system_prompt}
        ])
        config.configure_max_iterations(10)

        agent = ReActAgent(card=agent_card)
        agent.configure(config)

        if self._tools:
            self._register_tools(agent)

        return agent

    def _register_tools(self, agent) -> None:
        """Register tools on the agent's ability_manager and Runner.resource_mgr.

        ``ability_manager.add()`` only accepts ToolCard objects, but ``@tool``
        decorated functions are ``LocalFunction`` (a Tool subclass) instances
        that carry a ``.card`` attribute.  We must therefore:
        1. Add the ToolCard to ability_manager (so the LLM schema is available).
        2. Register the Tool instance with Runner.resource_mgr (so execution can
           retrieve it by id when the model calls the tool).

        NOTE: We always reconstruct a fresh LocalFunction instance in this process
        rather than using the cloudpickled one.  When AgentFactory is serialized
        by Ray (cloudpickle), LocalFunction.invoke is a closure whose globals
        include _TRANSFORM_NOOP (a plain object()).  cloudpickle bakes in the
        *value* of that object, creating a new object() on deserialization.
        The worker's callback framework also returns its own new object() from
        trigger_transform, making the `result is _TRANSFORM_NOOP` identity check
        always fail — so invoke() returns the sentinel instead of the real tool
        result.  Re-constructing LocalFunction here lets _ToolMeta.__call__ run
        fresh in this worker, capturing the worker's own _TRANSFORM_NOOP.
        """
        from openjiuwen.core.foundation.tool.base import Tool as FoundationTool
        from openjiuwen.core.foundation.tool import ToolCard
        from openjiuwen.core.foundation.tool.function.function import LocalFunction
        from openjiuwen.core.runner import Runner

        for t in self._tools:
            if isinstance(t, FoundationTool) and hasattr(t, 'card') and t.card:
                agent.ability_manager.add(t.card)
                if not Runner.resource_mgr.get_tool(tool_id=t.card.id):
                    # Reconstruct LocalFunction fresh so _ToolMeta.__call__ runs
                    # in this process, capturing the local _TRANSFORM_NOOP.
                    func = getattr(t, '_func', None)
                    if func is not None:
                        t = LocalFunction(card=t.card, func=func)
                    Runner.resource_mgr.add_tool(t)
            elif isinstance(t, ToolCard):
                agent.ability_manager.add(t)
            else:
                from openjiuwen.core.common.logging import logger
                logger.warning(
                    "AgentFactory: unrecognized tool type %s, skipping.", type(t)
                )


def build_agent_factory(
    runtime_cfg: AgentRuntimeConfig,
    tools: List[Any],
    tool_names: List[str],
) -> AgentFactory:
    """Build a default AgentFactory from runtime config + tools."""
    from openjiuwen.core.foundation.prompt import PromptTemplate

    system_prompt = runtime_cfg.system_prompt
    if isinstance(system_prompt, PromptTemplate):
        system_prompt = system_prompt.content

    return AgentFactory(
        system_prompt=system_prompt,
        tools=list(tools),
        tool_names=list(tool_names),
        temperature=runtime_cfg.temperature,
        max_new_tokens=runtime_cfg.max_new_tokens,
        top_p=runtime_cfg.top_p,
        presence_penalty=runtime_cfg.presence_penalty,
        frequency_penalty=runtime_cfg.frequency_penalty,
    )
