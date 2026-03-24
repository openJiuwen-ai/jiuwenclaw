# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import asyncio
import datetime
from datetime import timezone
from typing import List

from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.security.json_utils import JsonUtils
from openjiuwen.core.foundation.llm import AssistantMessage, UserMessage
from openjiuwen.core.memory.config.config import AgentMemoryConfig, MemoryScopeConfig
from openjiuwen.core.memory.long_term_memory import LongTermMemory
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, AgentRail


def _log_memory_task_exception(task: asyncio.Task) -> None:
    task_name = task.get_name()
    try:
        task.result()
        logger.info("memory rail task [%s] completed", task_name)
    except asyncio.CancelledError:
        logger.warning("memory rail task [%s] cancelled", task_name)
    except Exception as e:
        logger.exception("memory rail task [%s] failed: %s", task_name, e)


class MemoryRail(AgentRail):
    """AgentRail that integrates long-term memory into ReActAgent lifecycle.

    Hooks:
      before_invoke      - load memory variables from long-term memory into ctx.extra
      after_invoke       - async write conversation to long-term memory (answer only)
    """

    def __init__(
        self,
        mem_scope_id: str,
        agent_memory_config: AgentMemoryConfig,
    ) -> None:
        super().__init__()
        self._mem_scope_id = mem_scope_id
        self._agent_memory_config = agent_memory_config
        self._memory = LongTermMemory()

        # Derive feature flags at construction time
        self._enable_long_term_mem = agent_memory_config.enable_long_term_mem
        self._enable_fragment_memory = (agent_memory_config.enable_user_profile or
                                        agent_memory_config.enable_semantic_memory or
                                        agent_memory_config.enable_episodic_memory)
        self._enable_summary_memory = agent_memory_config.enable_summary_memory
        self._enable_mem_variables = len(agent_memory_config.mem_variables) > 0
        self._mem_variables_config = agent_memory_config.mem_variables

    # ------------------------------------------------------------------
    # BEFORE_INVOKE: load memory variables
    # ------------------------------------------------------------------

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        # Skip on resume path — memory already loaded in the first invoke
        if ctx.extra.get("is_resume"):
            return

        user_id = ctx.extra.get("user_id", "")
        if not user_id:
            return

        query = ctx.inputs.query if hasattr(ctx.inputs, "query") else ""
        result = {}

        if self._enable_mem_variables:
            try:
                variables = await self._memory.get_variables(user_id, self._mem_scope_id)
                if variables:
                    allowed = {v.name for v in self._mem_variables_config}
                    filtered = {k: v for k, v in variables.items() if k in allowed}
                    result["sys_memory_variables"] = JsonUtils.safe_json_dumps(filtered, ensure_ascii=False)
                logger.info("memory_variables: %s", variables)
            except Exception as e:
                logger.error("MemoryRail: get_variables failed: %s", e)

        if self._enable_long_term_mem:
            memory_contents: List[str] = []
            try:
                if self._enable_fragment_memory:
                    mems = await self._memory.search_user_mem(
                        user_id=user_id,
                        scope_id=self._mem_scope_id,
                        query=query,
                        num=10,
                    )
                    if mems:
                        memory_contents.append("用户画像记忆：")
                        memory_contents.extend(m.mem_info.content for m in mems)
                    logger.info("long_term_memory: %s", mems)
                if self._enable_summary_memory:
                    mems = await self._memory.search_user_history_summary(
                        user_id=user_id,
                        scope_id=self._mem_scope_id,
                        query=query,
                        num=5,
                    )
                    if mems:
                        memory_contents.append("摘要记忆：")
                        memory_contents.extend(m.mem_info.content for m in mems)
                    logger.info("user_summary_memory: %s", mems)
                if memory_contents:
                    result["sys_long_term_memory"] = JsonUtils.safe_json_dumps(memory_contents, ensure_ascii=False)
                else:
                    result["sys_long_term_memory"] = "[]"
            except Exception as e:
                logger.error("MemoryRail: search memory failed: %s", e)
                result["sys_long_term_memory"] = "[]"

        ctx.extra["memory_variables"] = result
        ctx.extra["_original_query"] = query

    # ------------------------------------------------------------------
    # AFTER_INVOKE: write conversation to long-term memory
    # ------------------------------------------------------------------

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        user_id = ctx.extra.get("user_id", "")
        if not user_id:
            return

        result = getattr(ctx.inputs, "result", None)
        if not isinstance(result, dict) or result.get("result_type") != "answer":
            return

        query = ctx.extra.get("_original_query", "")
        output = result.get("output", "")

        message_list = []
        if query:
            message_list.append(UserMessage(content=query))
        if output:
            message_list.append(AssistantMessage(content=output))

        if not message_list:
            return

        conversation_id = getattr(ctx.inputs, "conversation_id", None) or "default_session"

        task = asyncio.create_task(
            self._memory.add_messages(
                user_id=user_id,
                scope_id=self._mem_scope_id,
                session_id=conversation_id,
                messages=message_list,
                timestamp=datetime.datetime.now(tz=timezone.utc).astimezone(),
                agent_config=self._agent_memory_config,
            )
        )
        task.set_name("memory_rail_add_messages")
        task.add_done_callback(_log_memory_task_exception)
