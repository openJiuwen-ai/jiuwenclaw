# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""RePlanFallbackHandler —— 节点级 fallback 的委托接口与 DeepAgent 实现。

设计原则：
- RePlanExecutor 不再自建 ReActAgent，而是通过 handler 委托 fallback 执行。
- DeepAdapter 侧提供实现，复用 fork/spawn subagent 的正式执行路径。
- Executor 只关心 handler 的输入/输出契约，不关心内部 agent 如何构建。
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

from openjiuwen.core.session.agent import Session

logger = logging.getLogger(__name__)


class FallbackContractError(Exception):
    """fallback subagent 未达成节点契约，需降级到 DeepAgent。"""

    def __init__(
        self,
        *,
        node_name: str,
        reason: str,
        original_error: Exception | None = None,
    ) -> None:
        self.node_name = node_name
        self.reason = reason
        self.original_error = original_error
        super().__init__(
            f"fallback 未达成节点 {node_name} 契约: {reason}"
            + (f" (原错误: {original_error})" if original_error else "")
        )


class RePlanFallbackHandler(ABC):
    """节点级 fallback 委托接口。

    RePlanExecutor 在节点执行失败时调用此 handler，
    由外部（通常是 DeepAdapter）提供具体实现。
    """

    @abstractmethod
    async def fallback(
        self,
        node_name: str,
        instruction: str,
        inputs: dict[str, Any],
        error: Exception,
        parent_session: Session | None = None,
    ) -> dict[str, Any]:
        """非流式 fallback：使用外部 agent 兜底失败节点。"""

    @abstractmethod
    def fallback_stream(
        self,
        node_name: str,
        instruction: str,
        inputs: dict[str, Any],
        error: Exception,
        parent_session: Session | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """流式 fallback：使用外部 agent 兜底失败节点。"""


class DeepAgentFallbackHandler(RePlanFallbackHandler):
    """基于 DeepAgent spawn subagent 的 fallback 实现。

    这里不再自建 ReActAgent。fallback 作为一种 isolated spawn subagent，
    直接复用 DeepAgent fork/spawn 子代理体系：
    - create_deep_agent
    - SubagentContextRail
    - SubagentSkillUseRail
    - ProgressiveToolRail
    - ContextEngineeringRail(minimal)
    - 工具继承与工具事件转发
    """

    def __init__(
        self,
        adapter: Any,
        *,
        request_id: str = "",
        channel_id: str = "",
        session_id: str = "",
    ):
        self._adapter = adapter
        self._request_id = request_id
        self._channel_id = channel_id
        self._session_id = session_id

    def _get_subagent_executor(self) -> Any:
        executor = None
        get_executor = getattr(self._adapter, "_get_fork_agent_executor", None)
        if callable(get_executor):
            executor = get_executor()
        if executor is None:
            raise RuntimeError("DeepAgent subagent executor not initialized")
        return executor

    @staticmethod
    def _build_fallback_query(
        node_name: str,
        instruction: str,
        inputs: dict[str, Any],
        error: Exception,
    ) -> str:
        """构造 fallback subagent 的任务 prompt。

        要求 subagent 完成节点原任务后，以严格 JSON 收尾，声明是否真正达成节点契约。
        success=false 会触发整个 RePlan 降级到 DeepAgent，因此 subagent 必须如实判定。
        """
        import json

        return (
            f"你正在替代一个失败的 RePlan 规划节点完成任务。\n"
            f"节点名称: {node_name}\n"
            f"任务说明: {instruction}\n"
            f"原失败原因: {type(error).__name__}: {error}\n"
            f"输入参数: {json.dumps(inputs, ensure_ascii=False, default=str)}\n\n"
            f"## 强制输出格式\n"
            f"先输出给用户看的正文内容，然后在末尾另起一行输出契约声明（单行内联代码）：\n"
            f"---\n"
            f'`{{"success": true/false, "result": {{...}}}}`\n'
            f"- success: 是否真正达成了节点任务说明中的全部目标（文件已生成/校验已通过/字段已写入）。\n"
            f"  - 只有在产出可被下游节点直接消费时才填 true。\n"
            f"  - 若未能完成、部分完成、或无法确认，必须填 false，并在 result 中说明原因。\n"
            f"- result: 节点产出。成功时填需要回写到 inputs 的字段（如 outline_path、p4_validate_status 等）；"
            f'失败时填 {{"reason": "..."}}。\n'
            f"JSON 必须压成单行，切勿多行展开。切勿伪造 success=true，否则会导致下游节点崩溃。"
        )

    @staticmethod
    def _parse_fallback_output(fallback_output: Any) -> tuple[bool, dict[str, Any]]:
        """解析 subagent 末尾的 JSON 契约声明。

        Returns:
            (success, result): success 表示是否达成节点契约；result 为回写字段。
            无法解析时视为失败。
        """
        import json
        import re

        text = str(fallback_output or "")
        # 优先匹配单行内联代码: `{"success": ...}`
        inline_blocks = re.findall(r"`(\{.*?\})`", text)
        # 兼容多行代码块: ```json ... ```
        code_blocks = re.findall(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
        blocks = inline_blocks or code_blocks
        if not blocks:
            logger.warning(
                "[DeepAgentFallbackHandler] fallback output has no JSON contract block, "
                "treat as failed"
            )
            return False, {"reason": "fallback 输出未包含 JSON 契约声明"}

        try:
            payload = json.loads(blocks[-1])
        except json.JSONDecodeError as e:
            logger.warning(
                "[DeepAgentFallbackHandler] fallback JSON parse failed: %s, treat as failed",
                e,
            )
            return False, {"reason": f"fallback JSON 解析失败: {e}"}

        success = bool(payload.get("success", False))
        result = payload.get("result") or {}
        if not isinstance(result, dict):
            result = {"reason": str(result)}
        return success, result

    @staticmethod
    def _build_success_result(
        node_name: str,
        inputs: dict[str, Any],
        contract_result: dict[str, Any],
        error: Exception,
    ) -> dict[str, Any]:
        """构建 fallback 成功后的结果 dict。

        将 subagent 返回的 contract_result 合并回 inputs，
        保留 fallback 标记供下游感知，但状态为 completed 而非 degraded。
        """
        context_result = dict(inputs)
        # 合并 subagent 声明的产出字段（如 outline_path、p4_validate_status 等）
        context_result.update({
            key: value
            for key, value in contract_result.items()
            if key not in {"reason"}
        })
        context_result.update({
            "node": node_name,
            "status": "completed",
            "fallback": True,
            "fallback_reason": f"{type(error).__name__}: {error}",
        })
        return context_result

    @staticmethod
    def _log_degraded_result(
        node_name: str,
        fallback_output: Any,
        error: Exception,
    ) -> None:
        output_text = str(fallback_output or "")
        logger.info(
            "[DeepAgentFallbackHandler] degraded result built node=%s output_len=%d output_preview=%s reason=%s",
            node_name,
            len(output_text),
            output_text[:300],
            f"{type(error).__name__}: {error}",
        )

    async def _execute_spawn_fallback(
        self,
        node_name: str,
        instruction: str,
        inputs: dict[str, Any],
        error: Exception,
        parent_session: Session | None,
    ) -> Any:
        """通过现有 spawn subagent 执行 RePlan fallback。"""
        from jiuwenclaw.agentserver.tools.subagent_models import SubagentTaskSpec

        executor = self._get_subagent_executor()
        query = self._build_fallback_query(node_name, instruction, inputs, error)
        task = SubagentTaskSpec(
            role_id="RePlanFallback",
            objective=f"替代失败的 RePlan 规划节点 {node_name} 完成任务",
            prompt=query,
        )
        return await executor.execute_spawn(task, parent_session=parent_session)

    async def fallback(
        self,
        node_name: str,
        instruction: str,
        inputs: dict[str, Any],
        error: Exception,
        parent_session: Session | None = None,
    ) -> dict[str, Any]:
        """非流式 fallback 实现。"""
        result = await self._execute_spawn_fallback(
            node_name,
            instruction,
            inputs,
            error,
            parent_session,
        )
        success = bool(getattr(result, "success", False))
        if not success:
            raise RuntimeError(getattr(result, "error", "fallback subagent failed") or "fallback subagent failed")

        fallback_output = getattr(result, "result", None) or ""
        logger.warning(
            "[DeepAgentFallbackHandler] node fallback spawn finished node=%s task_id=%s error=%s",
            node_name,
            getattr(result, "task_id", ""),
            error,
        )
        self._log_degraded_result(node_name, fallback_output, error)

        # 契约校验：subagent 必须自证达成节点目标，否则视为 fallback 失败，
        # 抛出异常让 RePlanAgent 降级到 DeepAgent。
        success, contract_result = self._parse_fallback_output(fallback_output)
        if not success:
            reason = contract_result.get("reason", "fallback subagent 未达成节点契约")
            logger.error(
                "[DeepAgentFallbackHandler] node fallback contract failed node=%s reason=%s, "
                "degrading to DeepAgent",
                node_name,
                reason,
            )
            raise FallbackContractError(
                node_name=node_name,
                reason=reason,
                original_error=error,
            )

        logger.info(
            "[DeepAgentFallbackHandler] node fallback contract passed node=%s",
            node_name,
        )
        return self._build_success_result(node_name, inputs, contract_result, error)

    def fallback_stream(
        self,
        node_name: str,
        instruction: str,
        inputs: dict[str, Any],
        error: Exception,
        parent_session: Session | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """流式 fallback 实现。"""
        return self._fallback_stream_impl(node_name, instruction, inputs, error, parent_session)

    async def _fallback_stream_impl(
        self,
        node_name: str,
        instruction: str,
        inputs: dict[str, Any],
        error: Exception,
        parent_session: Session | None,
    ) -> AsyncIterator[dict[str, Any]]:
        """流式 fallback 的实际实现。

        execute_spawn 会通过 SubagentSessionProxy 将模型流和工具事件写入 parent_session；
        RePlanExecutor._execute_node_stream 已经并发 drain 该 session，因此这里无需手工
        parse subagent stream，只负责 fallback 生命周期事件和最终结构化结果。
        """
        logger.warning(
            "[DeepAgentFallbackHandler] node fallback_stream via spawn node=%s error=%s",
            node_name,
            error,
        )

        yield {
            "event_type": "fallback.started",
            "node_name": node_name,
            "error": f"{type(error).__name__}: {error}",
            "timestamp": int(asyncio.get_event_loop().time() * 1000),
        }

        result = None
        fallback_error: Exception | None = None
        try:
            result = await self._execute_spawn_fallback(
                node_name,
                instruction,
                inputs,
                error,
                parent_session,
            )
        except Exception as e:
            fallback_error = e
            logger.error(
                "[DeepAgentFallbackHandler] fallback_stream error node=%s error=%s",
                node_name,
                e,
            )
            yield {
                "event_type": "chat.error",
                "error": f"Fallback 执行失败: {e}",
                "is_fallback": True,
                "node_name": node_name,
            }
        finally:
            yield {
                "event_type": "fallback.finished",
                "node_name": node_name,
                "timestamp": int(asyncio.get_event_loop().time() * 1000),
            }

        if fallback_error is not None:
            return

        success = bool(getattr(result, "success", False))
        if not success:
            error_text = getattr(result, "error", "fallback subagent failed") or "fallback subagent failed"
            yield {
                "event_type": "chat.error",
                "error": f"Fallback 执行失败: {error_text}",
                "is_fallback": True,
                "node_name": node_name,
            }
            return

        fallback_output = getattr(result, "result", None) or ""
        self._log_degraded_result(node_name, fallback_output, error)

        # 契约校验：subagent 必须自证达成节点目标，否则视为 fallback 失败，
        # 抛出异常让 RePlanAgent 降级到 DeepAgent。
        contract_success, contract_result = self._parse_fallback_output(fallback_output)
        if not contract_success:
            reason = contract_result.get("reason", "fallback subagent 未达成节点契约")
            logger.error(
                "[DeepAgentFallbackHandler] node fallback_stream contract failed node=%s reason=%s, "
                "degrading to DeepAgent",
                node_name,
                reason,
            )
            yield {
                "event_type": "chat.error",
                "error": f"Fallback 未达成节点契约，降级到 DeepAgent: {reason}",
                "is_fallback": True,
                "node_name": node_name,
            }
            raise FallbackContractError(
                node_name=node_name,
                reason=reason,
                original_error=error,
            )

        success_result = self._build_success_result(node_name, inputs, contract_result, error)
        inputs.update({
            key: value
            for key, value in success_result.items()
            if key not in {"node", "status"}
        })
        logger.info(
            "[DeepAgentFallbackHandler] node fallback_stream contract passed node=%s",
            node_name,
        )
