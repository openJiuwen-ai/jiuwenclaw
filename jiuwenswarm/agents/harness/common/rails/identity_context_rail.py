# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""IdentityContextRail — inject SOUL.md / IDENTITY.md / USER.md into system prompt.

Mirrors WorkBuddy's <identity_context> injection: bundles the three persona
files into one PromptSection and injects it per model call. Files are read
fresh each call (hot-reload friendly). Missing files are skipped silently.

The section sits at priority 8, before the shared scaffold INTRO (priority 10),
so the persona context appears at the very top of the system prompt.
"""

from __future__ import annotations

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts import PromptSection
from openjiuwen.harness.rails.base import DeepAgentRail

from jiuwenswarm.common.utils import (
    get_deepagent_identity_md_path,
    get_deepagent_soul_md_path,
    get_deepagent_user_md_path,
    logger,
)

# Section priority: before shared scaffold INTRO (10) so persona context
# appears at the very top of the system prompt.
_IDENTITY_CONTEXT_PRIORITY = 8


class IdentityContextRail(DeepAgentRail):
    """注入 SOUL.md / IDENTITY.md / USER.md 人设文件到系统提示词。

    每个 model call 前重新读取三个文件，确保热更新即时生效。文件缺失
    或为空时静默跳过——不影响其余 prompt。

    职责:
    1. before_model_call: 读取 SOUL/IDENTITY/USER.md，合并为一个
       ``identity_context`` PromptSection 注入到 system_prompt_builder。
    2. 每次调用前先 remove 上一轮注入的同名 section，避免重复。
    """

    priority: int = 7  # 在 RuntimePromptRail(5) 之后、AvatarPromptRail(85) 之前

    def __init__(self, language: str = "cn") -> None:
        super().__init__()
        self._agent = None
        self._language = language

    def init(self, agent) -> None:  # type: ignore[override]
        """从 agent 获取引用。"""
        self._agent = agent

    def uninit(self, agent) -> None:  # type: ignore[override]
        """清理注入的 section 并释放引用。"""
        builder = getattr(self._agent or agent, "system_prompt_builder", None)
        if builder is not None:
            builder.remove_section("identity_context")
        self._agent = None

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """读取 persona 文件并注入 identity_context section。"""
        builder = getattr(
            getattr(self, "_deep_agent", None) or ctx.agent,
            "system_prompt_builder",
            None,
        )
        if builder is None:
            return

        # 清理上一轮可能残留的同名 section
        builder.remove_section("identity_context")

        parts: list[str] = []
        for label, path in (
            ("SOUL.md", get_deepagent_soul_md_path()),
            ("IDENTITY.md", get_deepagent_identity_md_path()),
            ("USER.md", get_deepagent_user_md_path()),
        ):
            try:
                content = path.read_text(encoding="utf-8").strip()
            except FileNotFoundError:
                continue
            except Exception as exc:
                logger.debug("IdentityContextRail skip %s: %s", label, exc)
                continue
            if not content:
                continue
            parts.append(f"# {label}\n\n{content}")

        if not parts:
            return

        # 优先用 builder.language（可能被 RuntimePromptRail 按请求更新），
        # 否则回退到构造时传入的语言。
        language = getattr(builder, "language", self._language) or self._language
        if language == "cn":
            wrapper = (
                "# 人设上下文\n\n"
                "以下文件定义了你的身份、性格与用户信息。遵循其中的设定与人设。\n\n"
            )
        else:
            wrapper = (
                "# Identity Context\n\n"
                "The following files define your identity, personality, and user info. "
                "Follow the settings and persona within.\n\n"
            )
        content = wrapper + "\n\n---\n\n".join(parts)

        builder.add_section(
            PromptSection(
                name="identity_context",
                content={language: content},
                priority=_IDENTITY_CONTEXT_PRIORITY,
            )
        )


__all__ = ["IdentityContextRail"]
