# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""skill_turbo_tool — 薄、无状态的在线执行工具.

activate(plan_name=None): 返回 schema 概览供 Agent 规划 todo
execute(plan_name 非空): 隔离加载 + 跑单 PlanNode + 返回产物摘要

设计要点（§6）：
- 工具无状态：不读不写 ContextStore，inputs 由 Agent 从历史工具结果组装传入
- 大字段不进对话历史：node py 落盘，工具只返回路径+标量
- AbortError 透传：node.run 不吞 AbortError，工具透传给 harness 暂停
- HITL resume：harness 重执 tool call，工具检测 resume_user_input 注入
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from jiuwenclaw.agentserver.skill_turbo.online.executor_single import SkillCodeExecutor
from jiuwenclaw.agentserver.skill_turbo.online.param_validator import (
    get_plan_task,
    validate_node_inputs,
    validate_plan_name,
    validate_scenario,
    validate_skill_name,
)
from jiuwenclaw.agentserver.skill_turbo.online.schema_loader import (
    TurboFace,
    discover_turbo_face,
    load_schema,
)
from jiuwenclaw.agentserver.skill_turbo.plan_node import AbortError, PlanNode
from jiuwenclaw.utils import logger

__all__ = ["skill_turbo_tool", "resolve_turbo_face_for_skill"]


# ─────────────────────────────────────────────────────────────────────────────
# 辅助函数
# ─────────────────────────────────────────────────────────────────────────────


def _iter_skill_roots() -> list[Path]:
    """获取候选 skill 根目录列表."""
    try:
        from jiuwenclaw.utils import get_agent_registered_skill_dirs

        return list(get_agent_registered_skill_dirs())
    except Exception:
        return []


def resolve_turbo_face_for_skill(skill_name: str) -> TurboFace:
    """发现指定 skill 的 turbo 面.

    Args:
        skill_name: 源 skill 名，如 "pptx-craft"

    Returns:
        TurboFace

    Raises:
        ValueError: 未找到 turbo 面
    """
    for root in _iter_skill_roots():
        face = discover_turbo_face(str(root))
        if face is not None and face.source_skill == skill_name:
            return face
    raise ValueError(f"未找到 skill {skill_name!r} 的 turbo 加速面")


def _build_env_base(skill_name: str, turbo_face: TurboFace) -> dict[str, Any]:
    """构建 env 基础键（与批量 _merge_env_config_to_inputs 相同方案，D3）.

    Agent 不应感知的运行时基础键由工具填充：
    - skill_root: skill 根目录
    - skill_name: 源 skill 名
    """
    # turbo_dir = .../skills/<skill_name>/turbo
    # skill_root = .../skills/<skill_name>
    skill_root = str(Path(turbo_face.turbo_dir).parent)
    return {
        "skill_root": skill_root,
        "skill_name": skill_name,
    }


def _inject_runtime_context(inputs: dict[str, Any]) -> None:
    """注入运行时上下文字段

    仅注入 inputs 中尚不存在的键（Agent 显式传入的优先）。
    """
    # effective_project_dir：adapter 在 _update_runtime_config 中写入 ContextVar
    try:
        from jiuwenclaw.agentserver.tools.subagent_executor.context_vars import (
            get_effective_request_workspace_dir,
        )

        effective_project_dir = get_effective_request_workspace_dir()
        if effective_project_dir and "effective_project_dir" not in inputs:
            inputs["effective_project_dir"] = effective_project_dir
    except ImportError:
        pass

    # workspace_base：从 request_metadata.output_dir 提取（与批量 skill_turbo_tools L303-308 一致）
    try:
        from jiuwenclaw.agentserver.skill_turbo.skill_turbo_tools import (
            get_current_request_metadata,
        )

        request_metadata = get_current_request_metadata()
        if isinstance(request_metadata, dict):
            output_dir = request_metadata.get("output_dir")
            if (
                output_dir
                and isinstance(output_dir, str)
                and output_dir.strip()
                and "workspace_base" not in inputs
            ):
                inputs["workspace_base"] = output_dir.strip()
    except ImportError:
        pass

    # conversation_id：从父会话获取
    try:
        from jiuwenclaw.agentserver.tools.subagent_executor import (
            get_subagent_parent_session,
        )

        parent_session = get_subagent_parent_session()
        if parent_session is not None and "conversation_id" not in inputs:
            inputs["conversation_id"] = parent_session.get_session_id()
    except ImportError:
        pass


# 大字段过滤：这些键的值通常是文件内容正文，不应进对话历史
_LARGE_FIELD_HINTS = {
    "doc_content", "outline", "outline_text", "doc_raw",
    "search_pool", "research_context", "template_narrative_context",
    "charlie_tasks", "slide_tasks",
}


def _build_product_summary(
    node_outputs: Any,
    schema_outputs: list[str] | None = None,
) -> dict[str, Any]:
    """过滤大字段，只保留 schema 声明的 outputs（路径+标量）.

    Args:
        node_outputs: 节点执行返回的产物字典
        schema_outputs: schema 中该节点声明的 outputs 键名列表。
            若提供，则只返回这些键（消除 env_base + 输入透传键冗余）。
            若为 None（兼容旧调用），返回所有非过滤键。
    """
    if not isinstance(node_outputs, dict):
        return {}

    summary: dict[str, Any] = {}
    for key, value in node_outputs.items():
        if key.startswith("_") or key in {"node", "status", "message", "steps", "result"}:
            continue
        if schema_outputs is not None and key not in schema_outputs:
            continue
        # 大字段过滤：已知大字段键跳过
        if key in _LARGE_FIELD_HINTS:
            continue
        # 大字符串过滤（>2000 字符的正文）
        if isinstance(value, str) and len(value) > 2000:
            continue
        # 保留标量 + 路径字符串 + 小 dict/list
        summary[key] = value
    return summary


def _build_node_summary(plan_name: str, node_outputs: Any) -> str:
    """构建节点执行摘要."""
    if isinstance(node_outputs, dict):
        status = node_outputs.get("status", "ok")
        message = node_outputs.get("message", "")
        if message:
            return f"节点 {plan_name} 执行完成（status={status}）: {message}"
        return f"节点 {plan_name} 执行完成（status={status}）"
    return f"节点 {plan_name} 执行完成"


def _build_activate_response(
    skill_name: str,
    scenario: str,
    schema: dict[str, Any],
) -> dict[str, Any]:
    """构建 activate 响应（schema 节点契约概览供 Agent 调用各节点）.

    注：执行流程（执行顺序/条件/续跑）由 SKILL_TURBO.md 正文承载（SkillTurboRail 层2 钉入），
    Agent 据此推理规划 todo；schema 只提供各节点参数契约（plan_tasks）。
    """
    plan_tasks_overview = []
    for task in schema.get("plan_tasks", []):
        if not isinstance(task, dict):
            continue
        # 只返回独立入口节点（independent_entry != false）
        if task.get("independent_entry", True) is False:
            continue
        # 去掉 title 中的 "Stage N: " 序号前缀，Agent 自行递增编号
        raw_title = task.get("title", task.get("plan_name", ""))
        clean_title = re.sub(r"^Stage\s+\d+:\s*", "", raw_title)
        plan_tasks_overview.append({
            "plan_name": task.get("plan_name", ""),
            "title": clean_title,
            "inputs": task.get("inputs", []),
            "optional_inputs": task.get("optional_inputs", []),
            "outputs": task.get("outputs", []),
            "when": task.get("when"),
            # 补条件分类元数据，Agent 据此区分 plan_time/runtime/default_on
            "when_category": task.get("when_category"),
            "when_known_after": task.get("when_known_after"),
            "when_self_noop": task.get("when_self_noop"),
        })

    return {
        "success": True,
        "mode": "activate",
        "skill_name": skill_name,
        "scenario": scenario,
        "plan_tasks": plan_tasks_overview,
        "task_complete": False,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 流式执行辅助：run_stream + 进度转发
# ─────────────────────────────────────────────────────────────────────────────

# _execute_stream 在 result dict 之上追加的元键，提取 result 时剔除
_CHUNK_META_KEYS: frozenset[str] = frozenset({
    "node", "status", "message", "content", "plan_name", "task_id",
})


async def _run_node_stream_with_progress(
    node: PlanNode,
    inputs: dict[str, Any],
    plan_name: str,
) -> dict[str, Any]:
    """通过 run_stream() 执行节点，转发进度消息到父会话 stream。

    复用与批量模式 _execute_node_stream 相同的 run_stream() 代码路径，
    使节点进度消息（如"正在检测环境依赖..."）在在线模式下也到达前端，
    消除在线 vs 批量的 chat.delta 事件差异。

    结果提取：
    - 正常执行：最后一个非 progress chunk 包含 **result，剔除元键后得到 result
    - fallback 兜底：fallback_handler 将 result 写入 inputs dict，chunks 为
      fallback.started/finished 事件，无 result chunk
    - 默认 _execute_stream：yield 原始 result dict（无元键），直接使用

    Args:
        node: 已加载并绑定 callbacks 的 PlanNode
        inputs: 节点输入（fallback 时会被 mutate 写入 result）
        plan_name: 节点 plan_name（用于日志）

    Returns:
        节点结果 dict
    """
    parent_session = None
    try:
        from jiuwenclaw.agentserver.tools.subagent_executor import (
            get_subagent_parent_session,
        )
        from openjiuwen.core.session.stream import OutputSchema

        parent_session = get_subagent_parent_session()
    except ImportError:
        pass

    node_outputs: dict[str, Any] | None = None
    had_fallback = False

    async for chunk in node.run_stream(inputs):
        if not isinstance(chunk, dict):
            continue

        # fallback 事件 chunk（fallback.started / fallback.finished）
        if "event_type" in chunk:
            had_fallback = True
            continue  # 批量模式也不转发 fallback 事件给前端

        # 正常节点 chunk — 转发进度消息到父会话 stream
        message = chunk.get("message", "") or chunk.get("content", "")
        if message and parent_session is not None:
            try:
                await parent_session.write_stream(
                    OutputSchema(
                        type="llm_output",
                        index=0,
                        payload={"content": str(message), "result_type": "answer"},
                    )
                )
            except Exception:
                logger.debug(
                    "[skill_turbo_tool] progress write_stream failed plan_name=%s",
                    plan_name,
                    exc_info=True,
                )

        # 从非 progress chunk 提取 result（最后一个生效）
        if chunk.get("status") != "progress":
            node_outputs = {
                k: v for k, v in chunk.items() if k not in _CHUNK_META_KEYS
            }

    # fallback 情况：result 由 fallback_handler 写入 inputs dict
    if had_fallback and node_outputs is None:
        node_outputs = inputs

    # 安全兜底
    if node_outputs is None:
        logger.warning(
            "[skill_turbo_tool] run_stream yielded no result chunk plan_name=%s",
            plan_name,
        )
        node_outputs = {}

    return node_outputs


# ─────────────────────────────────────────────────────────────────────────────
# 核心：skill_turbo_tool
# ─────────────────────────────────────────────────────────────────────────────


async def skill_turbo_tool(
    skill_name: str,
    scenario: str,
    plan_name: str | None = None,
    inputs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """在线执行 skill turbo 的单个 PlanNode.

    Args:
        skill_name: 源 skill 名，如 "pptx-craft"
        scenario: 任务切面，如 "create_ppt"
        plan_name: None=activate（返回 schema 概览）；非空=execute（跑单节点）
        inputs: execute 时该节点所需输入（Agent 从历史工具结果组装）

    Returns:
        activate: {success, mode:"activate", plan_tasks}
        execute 成功: {success, mode:"execute", plan_name, summary, products}
        execute 必填缺失: {success:false, error, missing_keys, plan_name}
        execute 节点失败: {success:false, error, plan_name}
    """
    # ── 公共校验 ──
    skill_name = validate_skill_name(skill_name)
    scenario = validate_scenario(scenario)

    mode = "activate" if plan_name is None else "execute"
    logger.info(
        "[skill_turbo_tool] call: skill_name=%s scenario=%s plan_name=%s mode=%s",
        skill_name, scenario, plan_name, mode,
    )

    # 发现 turbo 面 + 加载 schema（无状态，每次现算）
    turbo_face = resolve_turbo_face_for_skill(skill_name)
    schema = load_schema(turbo_face.turbo_dir, scenario)
    logger.info(
        "[skill_turbo_tool] turbo face resolved: turbo_dir=%s schema_keys=%d",
        turbo_face.turbo_dir, len(schema),
    )

    # ── activate：返回 schema 概览 ──
    if plan_name is None:
        response = _build_activate_response(skill_name, scenario, schema)
        logger.info(
            "[skill_turbo_tool] activate response: plan_tasks=%d scenario=%s",
            len(response.get("plan_tasks", [])), scenario,
        )
        return response

    # ── execute：跑单节点 ──
    plan_name = validate_plan_name(plan_name, schema)
    agent_inputs = dict(inputs) if inputs else {}

    # env 基础键注入（与批量完全相同方案，D3）
    env_base = _build_env_base(skill_name, turbo_face)
    node_inputs = {**env_base, **agent_inputs}

    # 运行时上下文注入（与批量 skill_turbo_tools 一致，避免产物落入 ./workspace fallback）
    _inject_runtime_context(node_inputs)

    # 必填校验（轻量，无重试计数）
    missing = validate_node_inputs(plan_name, node_inputs, schema)
    if missing:
        return {
            "success": False,
            "error": f"缺失 inputs: {missing}",
            "missing_keys": missing,
            "plan_name": plan_name,
        }

    # 隔离加载 + 执行
    executor = SkillCodeExecutor()
    node = executor.load_node(turbo_face.turbo_dir, scenario, plan_name, schema)
    logger.info(
        "[skill_turbo_tool] node loaded: plan_name=%s node_type=%s",
        plan_name, type(node).__name__,
    )

    # 绑定 runtime callbacks（复用批量 executor 的 callback 注入）
    _bind_callbacks_from_context(node, executor)

    # HITL resume 检测（harness 重执时设）
    resume_user_input = _get_resume_user_input()
    if resume_user_input is not None:
        executor.set_pending_resume(node, resume_user_input)

    # 跑节点（自带单节点 fallback 兜底；AbortError 透传）
    try:
        logger.info("[skill_turbo_tool] execute start: plan_name=%s", plan_name)
        node_outputs = await _run_node_stream_with_progress(node, node_inputs, plan_name)
    except AbortError:
        # HITL 中断：透传给 harness 暂停（不在工具内吞）
        logger.info("[skill_turbo_tool] execute aborted (HITL): plan_name=%s", plan_name)
        raise
    except Exception as exc:
        # 节点失败（fallback 兜底也失败）：返回结构化错误，Agent 自决
        logger.warning("[skill_turbo_tool] execute failed: plan_name=%s error=%s", plan_name, exc)
        return {
            "success": False,
            "error": f"节点 {plan_name} 执行失败: {exc}",
            "plan_name": plan_name,
        }

    # 返回摘要（大字段已落盘，过滤）
    # 消除 env_base + 输入透传键冗余
    plan_task_def = get_plan_task(plan_name, schema)
    schema_outputs = plan_task_def.get("outputs", []) if plan_task_def else None
    products = _build_product_summary(node_outputs, schema_outputs)

    logger.info(
        "[skill_turbo_tool] execute done: plan_name=%s products_keys=%s",
        plan_name, list(products.keys()) if isinstance(products, dict) else "N/A",
    )
    return {
        "success": True,
        "mode": "execute",
        "plan_name": plan_name,
        "summary": _build_node_summary(plan_name, node_outputs),
        "products": products,
    }


# ─────────────────────────────────────────────────────────────────────────────
# callback 绑定 + resume 检测（从当前上下文获取）
# ─────────────────────────────────────────────────────────────────────────────


def _bind_callbacks_from_context(node: PlanNode, executor: SkillCodeExecutor) -> None:
    """从当前上下文获取 parent executor 并绑定 callbacks.

    复用批量 SkillTurboExecutor 的 use_tool/call_llm/stream_llm/fallback。
    通过 adapter 构建 SkillTurbo 实例获取 executor（与批量 skill_turbo 工具同模式）。
    """
    try:
        from jiuwenclaw.agentserver.skill_turbo.skill_turbo_tools import (
            get_current_skill_turbo_adapter,
        )

        adapter = get_current_skill_turbo_adapter()
        if adapter is not None:
            # 通过 adapter 构建 SkillTurbo 获取 executor（与批量 skill_turbo 工具同模式）
            config = adapter.build_skill_turbo_config()
            from jiuwenclaw.agentserver.skill_turbo.agent import SkillTurbo

            skill_turbo_inst = SkillTurbo(config)
            parent_executor = skill_turbo_inst._executor  # SkillTurboExecutor 实例
            executor.bind_node_callbacks(node, parent_executor)

            # 绑定父会话到 executor 的 _session_var（节点内 stream 直写父会话）
            _bind_online_parent_session(parent_executor)
            return
    except Exception as exc:
        logger.warning("[skill_turbo_tool] bind callbacks via adapter failed: %s", exc)

    # 兜底：无 adapter 时节点能力受限（仅能跑无 LLM/tool 依赖的节点）
    logger.warning("[skill_turbo_tool] no adapter, node %s will have limited capability", node.plan_name)


def _bind_online_parent_session(parent_executor: Any) -> None:
    """绑定父会话到 executor 的 _session_var（节点内 stream 直写父会话）.

    复用批量 executor 的 ContextVar 机制：设置 _session_var 让节点内
    call_llm/stream_llm/call_tool 的事件直写父会话 stream。
    """
    try:
        from jiuwenclaw.agentserver.skill_turbo.executor import _session_var
        from jiuwenclaw.agentserver.tools.subagent_executor import (
            get_subagent_parent_session,
        )

        parent_session = get_subagent_parent_session()
        if parent_session is not None:
            _session_var.set(parent_session)
    except Exception as exc:
        logger.debug("[skill_turbo_tool] bind parent session failed: %s", exc)


def _get_resume_user_input() -> Any:
    """从 ContextVar 检测 HITL resume user input（harness 重执时设）."""
    try:
        from openjiuwen.core.runner.callback import RESUME_USER_INPUT_KEY

        from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
    except ImportError:
        return None

    # resume_user_input 由 harness 在重执 tool call 时设置在 ctx.extra 中
    # 工具内通过 ContextVar 读取（与批量 permission_bridge 机制一致）
    try:
        from jiuwenclaw.agentserver.skill_turbo.permission_bridge import (
            SKILL_TURBO_RESUME_CTX_KEY,
        )
        # 实际 resume 信号通过 harness 的 ctx.extra[RESUME_USER_INPUT_KEY] 传递
        # 工具层通过 ContextVar 获取（由 stream_event_rail 或 harness 设置）
    except ImportError:
        pass
    return None
