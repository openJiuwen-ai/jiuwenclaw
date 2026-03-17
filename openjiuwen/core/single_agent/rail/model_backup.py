# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from typing import List

from openjiuwen.core.foundation.llm import Model
from openjiuwen.core.single_agent.rail.base import AgentRail, AgentCallbackContext


class ModelBackupRail(AgentRail):
    def __init__(self, backup_models: List[Model]):
        super().__init__()
        self.backup_models = backup_models
        self.index = 0

    async def on_model_exception(self, ctx: AgentCallbackContext) -> None:
        if hasattr(ctx.agent, "set_llm") and self.index < len(self.backup_models):
            ctx.agent.set_llm(self.backup_models[self.index])
            self.index += 1
            ctx.request_retry()