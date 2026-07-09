# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""SkillTurbo 工具化 —— 将 SkillTurbo 封装为 DeepAgent 的 @tool。

参照 subagent（fork_agent / spawn_subagent）模式：
- LLM 在工具选择阶段顺带完成意图判定，消除独立 match_skill LLM 调用
- tool.invoke() 内部启动 SkillTurbo.run_stream，chunks 转发到父会话 stream
- AbortError 透传（HITL 中断/恢复机制不变）
- 返回值追加停止提示，软引导 LLM 结束
"""

from __future__ import annotations

import logging
from contextvars import ContextVar, Token
from typing import TYPE_CHECKING, Any

from openjiuwen.core.foundation.tool import tool

if TYPE_CHECKING:
    from openjiuwen.core.session.agent import Session

logger = logging.getLogger(__name__)

# ── 停止提示：追加到工具返回值，引导 LLM 总结并结束 ──
_SKILL_TURBO_STOP_HINT = (
    "\n\n[SYSTEM] The skill_acceleration_exec task is complete and the artifact has already been "
    "generated. The file(s) have ALREADY been sent to the user by the internal "
    "delivery pipeline — do NOT call send_file_to_user again. You should now "
    "summarize this result to the user and finish your turn. Do NOT call "
    "skill_acceleration_exec, skill_tool, or send_file_to_user again for this task — the "
    "work is already done; calling any of them again would duplicate the work."
)

# ── SkillTurbo event_type → DeepAgent OutputSchema.type 反向映射 ──
# SkillTurbo executor 产出的 payload.event_type 带 "chat." 前缀，而 DeepAgent 主循环 /
# _parse_stream_chunk 期望原始 OutputSchema.type（不带前缀）。直接用 event_type 作 type
# 会导致 tool_call / tool_result / tool_update 等事件被静默丢弃。
_SKILL_TURBO_EVENT_TYPE_TO_OUTPUT_TYPE: dict[str, str] = {
    "chat.delta": "llm_output",
    "chat.reasoning": "llm_reasoning",
    "chat.llm_usage": "llm_usage",
    "chat.usage_metadata": "llm_usage",
    "chat.tool_call": "tool_call",
    "chat.tool_result": "tool_result",
    "chat.tool_update": "tool_update",
    "chat.tool_calls.delta": "tool_calls.delta",
    "chat.error": "error",
}

# ── 不转发给父会话的事件类型 ──
# plan/node 生命周期事件：前端无对应 handler，DeepAgent 也无显式处理。
# plan.started / node.started 的 content 字段会被 _parse_stream_chunk fallback
# 误改写为 chat.delta 泄露给用户；plan.finished / node.finished 则被静默丢弃。
# 统一在此过滤，避免噪音。
_SKILL_TURBO_SKIP_EVENT_TYPES: frozenset[str] = frozenset({
    "plan.started",
    "plan.finished",
    "node.started",
    "node.finished",
})

# ── ContextVar：在 before_tool_call 中注入，供工具函数读取 ──
_current_skill_turbo_adapter: ContextVar[Any] = ContextVar(
    "current_skill_turbo_adapter", default=None
)

# ── ContextVar：SkillTurbo HITL 中断信号 ──
# skill_turbo_tools catch AbortError 后提取 ToolInterruptException 存入此 ContextVar，
# StreamEventRail.after_tool_call 读取后改写 ctx.inputs.tool_result 为 TIE，
# 使 harness 原生 HITL 机制（build_interrupt_state）检测并触发暂停。
_skill_turbo_hitl_tic: ContextVar[Any] = ContextVar("skill_turbo_hitl_tic", default=None)


def set_skill_turbo_hitl_tic(tic: Any) -> Token:
    return _skill_turbo_hitl_tic.set(tic)


def get_skill_turbo_hitl_tic() -> Any:
    return _skill_turbo_hitl_tic.get()


def set_current_skill_turbo_adapter(adapter: Any) -> Token:
    """绑定当前 async 上下文的 DeepAdapter 实例，返回 Token 用于 reset。"""
    return _current_skill_turbo_adapter.set(adapter)


def get_current_skill_turbo_adapter() -> Any:
    """获取当前上下文的 DeepAdapter 实例。"""
    return _current_skill_turbo_adapter.get()


def reset_current_skill_turbo_adapter(token: Token) -> None:
    """恢复之前的 adapter 绑定。"""
    _current_skill_turbo_adapter.reset(token)


# ── ContextVar：当前请求的 metadata ──
# 在 _update_runtime_config 中设置（md 是局部变量，无竞态），
# skill_turbo 通过 get_current_request_metadata() 读取，
# 替代 adapter._current_request_metadata 实例属性（并发覆盖风险）。
_current_request_metadata: ContextVar[Any] = ContextVar(
    "current_request_metadata", default=None
)


def set_current_request_metadata(metadata: Any) -> Token:
    """绑定当前 async 上下文的请求 metadata，返回 Token 用于 reset。"""
    return _current_request_metadata.set(metadata)


def get_current_request_metadata() -> Any:
    """获取当前上下文的请求 metadata。"""
    return _current_request_metadata.get()


def reset_current_request_metadata(token: Token) -> None:
    """恢复之前的 metadata 绑定。"""
    _current_request_metadata.reset(token)


def _build_artifact_summary(holder: dict[str, Any]) -> str:
    """从 executor 的 _node_artifacts_holder 构建产物摘要文本。

    格式: ``- {plan_name}: {info 摘要} | 文件: {路径列表}``
    """
    if not holder:
        return ""
    lines = ["[SkillAccelerationExec 产物摘要]"]
    for plan_name, node_info in holder.items():
        if not isinstance(node_info, dict):
            continue
        parts: list[str] = []
        info = node_info.get("info")
        if isinstance(info, dict) and info:
            parts.append(", ".join(
                f"{k}={v}" for k, v in info.items() if v is not None
            ))
        files = node_info.get("files")
        if isinstance(files, list) and files:
            parts.append("文件: " + ", ".join(
                f.get("path", "") for f in files
                if isinstance(f, dict) and f.get("path")
            ))
        if parts:
            lines.append(f"- {plan_name}: {' | '.join(parts)}")
    return "\n".join(lines) if len(lines) > 1 else ""


def _wrap_skill_turbo_result(
    result_dict: dict[str, Any],
    artifact_holder: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """在结果末尾追加产物摘要；成功时追加停止提示引导 LLM 结束当前轮次。

    失败时不追加停止提示——系统提示要求失败时回退到 skill_tool 走标准流程，
    若此处追加 "finish your turn" 会与之矛盾。
    """
    artifact_text = _build_artifact_summary(artifact_holder or {})
    if result_dict.get("success"):
        parts = [result_dict.get("result") or ""]
        if artifact_text:
            parts.append(artifact_text)
        parts.append(_SKILL_TURBO_STOP_HINT)
        result_dict["result"] = "\n\n".join(p for p in parts if p)
    else:
        parts = [result_dict.get("error") or ""]
        if artifact_text:
            parts.append(artifact_text)
        result_dict["error"] = "\n\n".join(p for p in parts if p)
    return result_dict


@tool(
    name="skill_acceleration_exec",
    description=(
        "技能加速模块。当用户意图涉及技能类任务（如生成 PPT、文档转换等结构化产出）时，"
        "可优先尝试调用此工具以获得更快的生成流程。工具内部会二次判断是否真正匹配已支持的技能，"
        "不匹配时自动降级为普通对话。当前内部支持 ppt-craft 技能（PPT 演示文稿制作）。"
        "【重要】每次调用仅处理一个独立任务。若用户要求生成多个同类产物（如多份不同主题的 PPT），"
        "必须为每个产物分别发起独立调用，且严格串行：等待前一次调用完全结束并收到返回结果后，"
        "才能发起下一次调用。严禁在同一轮对话中并行发起多次调用。"
    ),
)
async def skill_turbo(query: str) -> dict[str, Any]:
    """执行 SkillAccelerationExec 任务。

    Args:
        query: 对单个任务的忠实总结，须严格基于用户原话与历史上下文中已有的信息，
            不得自行扩写、脑补或补充用户未提及的内容细节（如擅自罗列章节大纲、
            技术要点、子主题等）。仅在用户表达零散时做必要的凝练与指代消解，
            确保任务目标、产物与约束完整可执行，但不新增任何信息。
            每次调用只处理一个任务；若用户要求多个任务，必须串行调用：
            等待前一次调用完成并收到返回结果后，再发起下一次调用。
    """
    from jiuwenclaw.agentserver.skill_turbo.agent import SkillTurbo, SkillTurboNotHandled
    from jiuwenclaw.agentserver.tools.subagent_executor import get_subagent_parent_session
    from jiuwenclaw.agentserver.tools.subagent_executor.context_vars import (
        get_effective_request_workspace_dir,
        get_effective_request_output_dir,
    )
    from openjiuwen.core.session.stream.base import OutputSchema
    from openjiuwen.core.runner.callback import AbortError

    adapter = get_current_skill_turbo_adapter()
    if adapter is None:
        return _wrap_skill_turbo_result(
            {"success": False, "error": "SkillAccelerationExec 未初始化"}
        )

    parent_session: Session | None = get_subagent_parent_session()

    # 构造 config 和 SkillTurbo 实例
    config = adapter.build_skill_turbo_config()
    skill_turbo_inst = SkillTurbo(config)

    # metadata：通过 ContextVar 读取（_update_runtime_config 中设置，无并发覆盖风险）
    request_metadata = get_current_request_metadata()
    request_id = request_metadata.get("request_id", "") if isinstance(request_metadata, dict) else ""
    channel_id = request_metadata.get("channel_id", "") if isinstance(request_metadata, dict) else ""

    # 构建 inputs：从 ContextVar 和 adapter 补全 executor 所需的上下文字段
    inputs: dict[str, Any] = {"query": query}
    if parent_session is not None:
        inputs["conversation_id"] = parent_session.get_session_id()
    if request_id:
        inputs["request_id"] = request_id
    if channel_id:
        inputs["channel_id"] = channel_id

    # effective_project_dir：adapter 在 _update_runtime_config 中已写入 ContextVar
    effective_project_dir = get_effective_request_workspace_dir()
    if effective_project_dir:
        inputs["effective_project_dir"] = effective_project_dir

    # 不再直接传入 output_dir，让 pipeline 自行调用 generate-timestamp-dir
    # 为每次任务创建独立的时间戳子目录，避免同一 session 连续任务共用 output 目录导致产物串扰
    # 但需要确保 pipeline 能正确获取 output 的父目录（用于生成时间戳子目录）
    # 方案：从 metadata 的 output_dir 提取父目录，作为 workspace_base 传入
    if isinstance(request_metadata, dict):
        output_dir = request_metadata.get("output_dir")
        if output_dir and isinstance(output_dir, str) and output_dir.strip():
            # output_dir 格式：.../files/user_id/chat_id/output
            # 需要将其作为 workspace_base，让 pipeline 在此目录下生成时间戳子目录
            inputs["workspace_base"] = output_dir.strip()
            logger.debug(
                "[SkillTurboTool] workspace_base 设置为 output_dir: %s",
                output_dir.strip()
            )

    if isinstance(request_metadata, dict):
        inputs["metadata"] = request_metadata

    try:
        async for chunk in skill_turbo_inst.run_stream(
            query, inputs, request_id, channel_id
        ):
            if not chunk.payload:
                continue

            event_type = chunk.payload.get("event_type", "unknown")

            # plan/node 生命周期事件：前端无 handler，跳过转发；
            # 其 content 若进入 _parse_stream_chunk 会被误改写为 chat.delta 泄露给用户
            if event_type in _SKILL_TURBO_SKIP_EVENT_TYPES:
                continue

            # 转发 chunk 到父会话 stream（前端实时可见）
            if parent_session is not None:
                # SkillTurbo executor 产出的 event_type 带 "chat." 前缀（如 "chat.tool_call"），
                # 但 DeepAgent 的 _parse_stream_chunk 期望原始 OutputSchema.type（如 "tool_call"）。
                # 若直接用 event_type 作 type，会因类型不匹配被静默丢弃，需反向映射回原始 type。
                output_type = _SKILL_TURBO_EVENT_TYPE_TO_OUTPUT_TYPE.get(event_type, event_type)
                output = OutputSchema(
                    type=output_type,
                    index=0,
                    payload=chunk.payload,
                )
                try:
                    await parent_session.write_stream(output)
                except Exception:
                    logger.debug("[SkillTurboTool] write_stream failed, skipping", exc_info=True)

        # 过程输出已通过 write_stream 实时推给前端，tool result 仅返回精简完成信号 + 产物摘要
        return _wrap_skill_turbo_result(
            {"success": True, "result": "任务已完成"},
            artifact_holder=skill_turbo_inst.artifact_holder,
        )

    except AbortError as e:
        # HITL 中断：提取 ToolInterruptException 存入 ContextVar，
        # after_tool_call 会改写 ctx.inputs.tool_result 为 TIE 触发 harness 原生 HITL。
        # 不能直接 raise TIE（被 _execute_single_tool_call 包装为 AbilityExecutionError）。
        # resume_ctx 已由 executor.save_resume_ctx 保存，恢复时走 _try_skill_turbo_resume。
        from jiuwenclaw.agentserver.skill_turbo.permission_bridge import (
            extract_tool_interrupt,
        )
        tic = extract_tool_interrupt(e)
        if tic is not None:
            logger.info(
                "[SkillTurboTool] HITL interrupt, storing TIC. tcid=%s",
                tic.tool_call.id if tic.tool_call else "?",
            )
            set_skill_turbo_hitl_tic(tic)
            return _wrap_skill_turbo_result(
                {"success": False, "error": "任务已暂停等待审批"},
                artifact_holder=skill_turbo_inst.artifact_holder,
            )
        # Fallback: AbortError 无 ToolInterruptException cause，返回错误
        logger.warning("[SkillTurboTool] AbortError without ToolInterruptException cause")
        return _wrap_skill_turbo_result(
            {"success": False, "error": f"任务中断: {e}"},
            artifact_holder=skill_turbo_inst.artifact_holder,
        )
    except SkillTurboNotHandled as exc:
        logger.info("[SkillTurboTool] SkillTurbo 未处理: %s", exc)
        return _wrap_skill_turbo_result(
            {"success": False, "error": f"SkillAccelerationExec 未处理: {exc}"},
            artifact_holder=skill_turbo_inst.artifact_holder,
        )
    except Exception as exc:
        logger.warning("[SkillTurboTool] 执行失败: %s", exc, exc_info=True)
        return _wrap_skill_turbo_result(
            {"success": False, "error": f"执行失败: {exc}"},
            artifact_holder=skill_turbo_inst.artifact_holder,
        )


def get_skill_turbo_tools() -> list:
    """返回 SkillTurbo 工具列表，供 interface_deep.py 注册。"""
    return [skill_turbo]
