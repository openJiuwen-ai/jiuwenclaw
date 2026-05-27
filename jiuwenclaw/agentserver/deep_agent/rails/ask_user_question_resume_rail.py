# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Restore user reply query after ask_user_question text_only pause."""

from __future__ import annotations

import logging

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, TaskIterationInputs
from openjiuwen.harness.rails.base import DeepAgentRail

from jiuwenclaw.agentserver.tools.ask_user_question_session_state import (
    pop_text_only_resume_user_query,
)

logger = logging.getLogger(__name__)


class AskUserQuestionResumeRail(DeepAgentRail):
    """Ensure the user's plain-text reply is not replaced by TaskPlan stage text."""

    async def before_task_iteration(self, ctx: AgentCallbackContext) -> None:
        if ctx.session is None or not isinstance(ctx.inputs, TaskIterationInputs):
            return

        resume_query = pop_text_only_resume_user_query(ctx.session)
        if not resume_query:
            return

        ctx.inputs.query = resume_query
        ctx.inputs.is_follow_up = True
        logger.info(
            "[AskUserQuestionResumeRail] restored text_only resume query for task iteration"
        )
