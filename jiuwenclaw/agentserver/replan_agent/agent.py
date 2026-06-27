# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""RePlanAgent 主入口 —— 串联规划、执行、降级。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, AsyncIterator

from jiuwenclaw.agentserver.replan_agent.environment import RePlanEnvironment
from jiuwenclaw.agentserver.replan_agent.executor import PlanCodeLoadError, RePlanExecutor
from jiuwenclaw.agentserver.replan_agent.plan_node import AbortError
from jiuwenclaw.agentserver.replan_agent.validator import PlanCodeValidationError
from jiuwenclaw.agentserver.replan_agent.planner import RePlanPlanner

if TYPE_CHECKING:
    from jiuwenclaw.schema.agent import AgentResponseChunk


class RePlanNotHandled(Exception):
    """RePlanAgent 无法处理该任务，需要降级到 DeepAgent。"""


class RePlanAgent:
    """RePlanAgent 主入口。"""

    def __init__(self, config: dict[str, Any]):
        self._env = RePlanEnvironment(config)
        self._planner = RePlanPlanner(self._env)
        self._executor = RePlanExecutor(self._env)

    async def run(self, task: str, inputs: dict[str, Any]) -> Any:
        """主入口：规划 → 执行，失败降级到 DeepAgent（非流式）。"""
        plan_code = await self._planner.plan(task, context=inputs)

        if plan_code is None:
            # 无匹配skill，抛出异常让interface.py处理降级
            raise RePlanNotHandled("无匹配skill")

        try:
            return await self._executor.execute_plan(plan_code, inputs)
        except AbortError:
            # HITL 中断：不能降级，必须向上抛给 adapter 转 HITL 三件套
            raise
        except (PlanCodeValidationError, PlanCodeLoadError) as e:
            # 规划代码校验或加载失败，抛出异常让interface.py处理降级
            raise RePlanNotHandled(f"规划代码校验或加载失败: {e}") from e
        except Exception as e:
            # 其他异常，抛出异常让interface.py处理降级
            raise RePlanNotHandled(f"规划执行失败: {e}") from e

    async def run_stream(
        self,
        task: str,
        inputs: dict[str, Any],
        request_id: str,
        channel_id: str,
    ) -> AsyncIterator[AgentResponseChunk]:
        """
        主入口：规划 → 执行，失败降级到 DeepAgent（流式）。

        Args:
            task: 任务描述
            inputs: 输入参数字典
            request_id: 请求ID（用于AgentResponseChunk）
            channel_id: 渠道ID（用于AgentResponseChunk）

        Yields:
            AgentResponseChunk: 流式响应片段（与 DeepAgent 兼容）
        """
        plan_code = await self._planner.plan(task, context=inputs)

        if plan_code is None:
            # 无匹配skill，抛出异常让interface.py处理降级
            raise RePlanNotHandled("无匹配skill")

        try:
            # 执行规划代码（流式）
            async for chunk in self._executor.execute_plan_stream(
                plan_code, inputs, request_id, channel_id
            ):
                yield chunk
        except AbortError:
            # HITL 中断不降级，透传给 adapter 发 HITL 三件套
            raise
        except (PlanCodeValidationError, PlanCodeLoadError) as e:
            # 规划代码校验或加载失败，抛出异常让interface.py处理降级
            raise RePlanNotHandled(f"规划代码校验或加载失败: {e}") from e
        except Exception as e:
            # 其他异常，抛出异常让interface.py处理降级
            raise RePlanNotHandled(f"规划执行失败: {e}") from e

    # ──────────────────────── Resume 入口 ────────────────────────

    async def resume_stream(
        self,
        *,
        plan_code: str,
        inputs: dict[str, Any],
        request_id: str,
        channel_id: str,
        pending_tool_call_id: str,
        user_input: Any,
    ) -> AsyncIterator[AgentResponseChunk]:
        """从 HITL 中断点恢复执行：跳过 planner，直接重放 plan_code。

        adapter 在收到带 answers 的请求时调用本方法，提供：
        - 中断时保存的 ``plan_code`` / ``inputs``
        - 中断时记录的 ``pending_tool_call_id``
        - 用户审批回复（``ConfirmPayload`` / dict / etc.）

        二次执行时 ``RePlanExecutor`` 重放到同一 tool_call_id，将 ``user_input``
        注入 ctx.extra[RESUME_USER_INPUT_KEY]，``PermissionInterruptRail`` 据此
        给出 approve / reject。
        """
        self._executor.set_pending_resume(
            expected_tool_call_id=pending_tool_call_id,
            user_input=user_input,
        )
        try:
            async for chunk in self._executor.execute_plan_stream(
                plan_code, inputs, request_id, channel_id
            ):
                yield chunk
        except AbortError:
            raise
        except (PlanCodeValidationError, PlanCodeLoadError) as e:
            raise RePlanNotHandled(f"规划代码校验或加载失败: {e}") from e
        except Exception as e:
            raise RePlanNotHandled(f"规划执行失败: {e}") from e


