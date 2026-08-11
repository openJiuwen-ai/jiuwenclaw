# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""SkillTurbo 主入口 —— 在线执行内核容器。

保留 __init__ 构造 executor，供在线工具 online/skill_turbo_tool.py 取 _executor 使用。
批量执行方法（run/run_stream/resume_stream）已废弃。
"""

from __future__ import annotations

from typing import Any

from jiuwenswarm.agents.skill_turbo.environment import SkillTurboEnvironment
from jiuwenswarm.agents.skill_turbo.executor import SkillTurboExecutor


class SkillTurbo:
    """SkillTurbo 主入口（在线执行内核容器）。"""

    def __init__(self, config: dict[str, Any]):
        self._env = SkillTurboEnvironment(config)
        self._executor = SkillTurboExecutor(self._env)

    @property
    def artifact_holder(self) -> dict[str, dict[str, Any]]:
        """返回 executor 的节点产物 holder，供外部构建产物摘要。"""
        return self._executor.node_artifacts

