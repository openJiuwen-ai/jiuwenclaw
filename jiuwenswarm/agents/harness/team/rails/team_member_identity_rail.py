# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team member identity rail: inject the member's expert-group identity section.

团包模板的 persona 被拍平进 Team 层 prompt(冷恢复真相源,语义不动),最终落在
TeamPolicyRail 的 team_extra(P:17)杂项槽位——无结构、位置靠后。本 rail 把
「团名 + 主理人/成员名 + 角色定位」作为独立 PromptSection(P:10,紧随通用
identity)注入系统提示词,与单专家的 identity section 锚定同构。

身份文本由装配期(_apply_agent_group)渲染、经 RailSpec.params 传入(随
TeamAgentSpec 序列化,冷恢复/分布式重建不丢);非专家团(空身份文本)由
provider 返回 None,本 rail 不挂载,行为零变化。

不采用每轮 prompt attachment 重申:TeamPolicyRail 的设计注释明确常量内容走
attachment 每轮全价重编码、永不命中前缀缓存;主理人身份在团生命周期内不变,
静态 section 足够。换团时 spec 重建、params 更新,身份块自然切换;退团时
rail 随 member 实例销毁,section 经 uninit 摘除,无残留。
"""

from __future__ import annotations

import logging

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts import PromptSection
from openjiuwen.harness.rails.base import DeepAgentRail

logger = logging.getLogger(__name__)

# section 名按角色区分,便于日志/调试定位;同一 member 只有一个实例。
_SECTION_NAME = "team_member_identity"

# 紧随通用 identity(P:10, sections/identity.py),先于 team_role(P:11)。
_SECTION_PRIORITY = 10


class TeamMemberIdentityRail(DeepAgentRail):
    """把 member 的专家团身份块作为独立 PromptSection 注入系统提示词。

    内容静态(构造期定型),before_model_call 每轮幂等 upsert(与
    TeamPolicyRail 静态 section 同款:builder 前缀保持字节稳定、可共享缓存)。
    """

    priority = 12  # 与 TeamPolicyRail 同档;section 位次由 PromptSection.priority 决定

    def __init__(
        self,
        *,
        role: str = "",
        display_name: str = "",
        group_display: str = "",
        identity_text: dict[str, str] | None = None,
    ) -> None:
        super().__init__()
        self.system_prompt_builder = None
        self._role = role
        self._display_name = display_name
        self._group_display = group_display
        self._identity_text = dict(identity_text or {})
        self._section = PromptSection(
            name=_SECTION_NAME,
            content=self._identity_text,
            priority=_SECTION_PRIORITY,
        )

    def init(self, agent) -> None:
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)

    def uninit(self, agent) -> None:
        _ = agent
        if self.system_prompt_builder is not None:
            self.system_prompt_builder.remove_section(_SECTION_NAME)
        self.system_prompt_builder = None

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        _ = ctx
        if self.system_prompt_builder is None or not self._identity_text:
            return
        # builder.add_section 同名覆盖,幂等;prefix 内容静态,不破坏缓存共享。
        self.system_prompt_builder.add_section(self._section)
