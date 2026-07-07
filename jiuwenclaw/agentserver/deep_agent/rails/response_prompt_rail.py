# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Inject the response/message-format section before each model call."""
from __future__ import annotations

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.base import DeepAgentRail

from jiuwenclaw.agentserver.deep_agent.prompt_builder import _response_prompt


class ResponsePromptRail(DeepAgentRail):
    """Inject the response section as an independent prompt section."""

    priority = 5

    def __init__(self) -> None:
        super().__init__()
        self.system_prompt_builder = None

    def init(self, agent) -> None:
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)

    def uninit(self, agent) -> None:
        # 热重载后 agent.system_prompt_builder 可能已是新引用，退休清理前先同步缓存，
        # 确保 remove_section 落到当前生效的 builder 上。
        _builder = getattr(agent, "system_prompt_builder", None)
        if _builder is not None:
            self.system_prompt_builder = _builder
        if self.system_prompt_builder is not None:
            self.system_prompt_builder.remove_section("response")
        self.system_prompt_builder = None

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        # 热重载（DeepAgent._hot_reload_system_prompt）会新建 SystemPromptBuilder 并替换
        # agent.system_prompt_builder，但保留型 rail 不会重新 init()，缓存的
        # self.system_prompt_builder 可能指向旧 builder。这里每次从 ctx.agent 现取最新
        # builder 并刷新缓存，使后续 add_section 都落到当前生效的 builder 上。
        _builder = getattr(getattr(ctx, "agent", None), "system_prompt_builder", None)
        if _builder is not None:
            self.system_prompt_builder = _builder

        if self.system_prompt_builder is None:
            return

        section = _response_prompt(self.system_prompt_builder.language or "cn")
        self.system_prompt_builder.add_section(section)
