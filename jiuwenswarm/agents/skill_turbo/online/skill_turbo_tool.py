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
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

from jiuwenswarm.agents.skill_turbo.online.executor_single import SkillCodeExecutor
from jiuwenswarm.agents.skill_turbo.online.param_validator import (
    get_plan_task,
    validate_node_inputs,
    validate_plan_name,
    validate_scenario,
    validate_skill_name,
)
from jiuwenswarm.agents.skill_turbo.online.schema_loader import (
    TurboFace,
    discover_turbo_face,
    load_schema,
)
from jiuwenswarm.agents.skill_turbo.plan_node import AbortError, PlanNode
from jiuwenswarm.common.utils import logger

__all__ = ["skill_turbo_tool", "resolve_turbo_face_for_skill", "clear_cached_parent_executor"]


# ─────────────────────────────────────────────────────────────────────────────
# 辅助函数
# ─────────────────────────────────────────────────────────────────────────────


def resolve_turbo_face_for_skill(skill_name: str) -> TurboFace:
    """发现指定 skill 的 turbo 面（复用 discover_all_turbo_faces 缓存）.

    Args:
        skill_name: 源 skill 名，如 "pptx-craft"

    Returns:
        TurboFace

    Raises:
        ValueError: 未找到 turbo 面
    """
    # 通过 discover_all_turbo_faces（已缓存）获取所有 faces，
    # 然后匹配 source_skill，避免每次 execute 都重复扫描。
    from jiuwenswarm.agents.skill_turbo.online.schema_loader import (
        discover_all_turbo_faces,
    )
    
    all_faces = discover_all_turbo_faces()  # 进程级缓存，TTL 60s + mtime 失效
    for face in all_faces:
        if face.source_skill == skill_name:
            return face
    
    raise ValueError(f"未找到 skill {skill_name!r} 的 turbo 加速面")


_NULL_LIKE = frozenset(("null", "none", ""))


def _normalize_nullable_param(value: Any) -> str | None:
    """Normalize nullable string params from LLM tool calls.

    LLMs (notably glm-5.2) sometimes serialize JSON ``null`` as the string
    ``"null"`` / ``"None"`` instead of omitting the parameter or sending actual
    ``null``.  Without normalization the tool treats ``scenario="null"`` as a
    real scenario name, enters execute mode, and fails with
    ``schema_null.json not found`` — making the entire turbo channel
    unavailable (case_21 regression).

    Treat ``None``, ``"null"``, ``"None"``, ``"none"`` (case-insensitive) and
    blank strings as Python ``None``; preserve all other strings (stripped).
    """
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.lower() in _NULL_LIKE:
            return None
        return stripped
    return value


def _build_env_base(skill_name: str, turbo_face: TurboFace) -> dict[str, Any]:
    """构建 env 基础键（与批量 _merge_env_config_to_inputs 相同方案，D3）.

    Agent 不应感知的运行时基础键由工具填充：
    - skill_root: skill 根目录
    - skill_name: 源 skill 名

    假定 turbo_dir.parent == skill_root（产物目录结构契约）。
    产物结构：.../skills/<skill_name>/turbo/，turbo_dir.parent 即 skill_root。
    """
    # turbo_dir = .../skills/<skill_name>/turbo
    # skill_root = .../skills/<skill_name>
    skill_root = str(Path(turbo_face.turbo_dir).parent)
    return {
        "skill_root": skill_root,
        "skill_name": skill_name,
    }


def _is_valid_output_dir(value: Any) -> bool:
    """Check if value is a non-empty string suitable for output_dir."""
    return isinstance(value, str) and bool(value.strip())


def _inject_runtime_context(inputs: dict[str, Any]) -> None:
    """注入运行时上下文字段

    仅注入 inputs 中尚不存在的键（Agent 显式传入的优先）。
    """
    # effective_project_dir：adapter 在 _update_runtime_config 中写入 ContextVar
    try:
        from jiuwenswarm.agents.skill_turbo.online.context_vars import (
            get_effective_request_workspace_dir,
        )

        effective_project_dir = get_effective_request_workspace_dir()
        if effective_project_dir and "effective_project_dir" not in inputs:
            inputs["effective_project_dir"] = effective_project_dir
    except ImportError:
        pass

    # workspace_base：从 request_metadata.output_dir 提取（与批量 skill_turbo_tools L303-308 一致）
    try:
        from jiuwenswarm.agents.skill_turbo.skill_turbo_tools import (
            get_current_request_metadata,
        )

        request_metadata = get_current_request_metadata()
        if isinstance(request_metadata, dict):
            output_dir = request_metadata.get("output_dir")
            if _is_valid_output_dir(output_dir) and "workspace_base" not in inputs:
                inputs["workspace_base"] = output_dir.strip()
    except ImportError:
        pass

    # conversation_id：从父会话获取
    try:
        from jiuwenswarm.agents.skill_turbo.online.context_vars import (
            get_subagent_parent_session,
        )

        parent_session = get_subagent_parent_session()
        if parent_session is not None and "conversation_id" not in inputs:
            inputs["conversation_id"] = parent_session.get_session_id()
    except ImportError:
        pass


def _build_product_summary(
    node_outputs: Any,
    schema_outputs: list[str] | None = None,
) -> dict[str, Any]:
    """过滤大字段，只保留 schema 声明的 outputs（路径+标量）.

    大字段过滤契约：
    - **白名单**：schema 的 ``plan_tasks[].outputs`` 是单一真值源。
      skill 若不想让某字段进 Agent 摘要，**不要**在 ``outputs`` 中声明它
      未在 ``outputs`` 声明的键一律不进 summary，即使节点 py 把它放进了 node_outputs。
    - **通用长度兜底**：任何 >2000 字符的字符串值都过滤，作为节点 py 不守约时的安全网，
      防止大正文意外进对话历史。

    Args:
        node_outputs: 节点执行返回的产物字典
        schema_outputs: schema 中该节点声明的 outputs 键名列表。
            若提供，则只返回这些键（消除 env_base + 输入透传键冗余）。
            若为 None，返回所有非过滤键。
    """
    if not isinstance(node_outputs, dict):
        return {}

    summary: dict[str, Any] = {}
    for key, value in node_outputs.items():
        if key.startswith("_") or key in {"node", "status", "message", "steps", "result"}:
            continue
        if schema_outputs is not None and key not in schema_outputs:
            continue
        # 通用大字符串兜底（>2000 字符）：防止节点 py 不守约时大正文进对话历史
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
        from jiuwenswarm.agents.skill_turbo.online.context_vars import (
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
                        payload={
                            "content": str(message),
                            "result_type": "answer",
                            "stream_source_id": f"skill_turbo:{plan_name}",
                        },
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
    scenario: str | None = None,
    plan_name: str | None = None,
    inputs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """在线执行 skill turbo 的单个 PlanNode.

    Args:
        skill_name: 源 skill 名，如 "pptx-craft"
        scenario: 任务切面，如 "create_ppt"；None=discover（返回场景清单）
        plan_name: None=activate/discover（返回概览）；非空=execute（跑单节点）
        inputs: execute 时该节点所需输入（Agent 从历史工具结果组装）

    Returns:
        discover: {success, mode:"discover", scenarios, selection_rules}
        activate: {success, mode:"activate", plan_tasks}
        execute 成功: {success, mode:"execute", plan_name, summary, products}
        execute 必填缺失: {success:false, error, missing_keys, plan_name}
        execute 节点失败: {success:false, error, plan_name}
    """
    # ── 公共校验 ──
    skill_name = validate_skill_name(skill_name)

    # LLM 可能将 JSON null 序列化为字符串
    scenario = _normalize_nullable_param(scenario)
    plan_name = _normalize_nullable_param(plan_name)

    turbo_face = resolve_turbo_face_for_skill(skill_name)

    # ── discover：返回场景清单 + 触发条件 + 选择规则 ──
    if scenario is None and plan_name is None:
        from jiuwenswarm.agents.skill_turbo.online.schema_loader import (
            extract_scenario_summaries,
            extract_scenario_selection_rules,
        )
        summaries = extract_scenario_summaries(turbo_face.turbo_dir)
        selection_rules = extract_scenario_selection_rules(turbo_face.turbo_dir)
        logger.info(
            "[skill_turbo_tool] discover: skill_name=%s scenarios=%d",
            skill_name, len(summaries),
        )
        return {
            "success": True,
            "mode": "discover",
            "skill_name": skill_name,
            "turbo_name": turbo_face.turbo_name,
            "scenarios": summaries,
            "selection_rules": selection_rules,
        }

    # scenario 必填（activate / execute）
    if scenario is None:
        return {
            "success": False,
            "error": "scenario 不能为空（discover 须同时省略 plan_name）",
        }
    scenario = validate_scenario(scenario)

    mode = "activate" if plan_name is None else "execute"
    logger.info(
        "[skill_turbo_tool] call: skill_name=%s scenario=%s plan_name=%s mode=%s",
        skill_name, scenario, plan_name, mode,
    )

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
    # session_var_token 供 finally 清理 _session_var，防 ContextVar 污染
    parent_executor, session, _session_var_token = _bind_callbacks_from_context(
        node, executor, skill_name, scenario
    )

    # 整段 execute 用 try/finally 包裹，finally 清理 _session_var，
    # 防 ContextVar 污染（同一协程/线程后续复用时误写已完成节点的父会话代理）。
    try:
        # HITL resume 检测（adapter 在线 resume 时通过 ContextVar 设置）
        resume_user_input = _get_resume_user_input()
        if resume_user_input is not None and parent_executor is not None:
            # set_pending_resume 需要写 parent_executor（而非 node）
            # pending_tool_call_id 由 stream_event_rail 从 RESUME_USER_INPUT_KEY 桥接
            # 包装为 {"pending_tool_call_id": ..., "user_input": ...} 后注入 ContextVar。
            logger.info(
                "[skill_turbo_tool] resume detected: plan_name=%s",
                plan_name,
            )
            pending_tool_call_id = ""
            actual_user_input = resume_user_input

            # 从 resume_user_input 中提取 pending_tool_call_id（adapter 传入的包装）
            if isinstance(resume_user_input, dict):
                pending_tool_call_id = resume_user_input.get("pending_tool_call_id", "")
                actual_user_input = resume_user_input.get("user_input", resume_user_input)

            # 注入 pending_resume 到 parent_executor
            if pending_tool_call_id:
                parent_executor.set_pending_resume(
                    expected_tool_call_id=pending_tool_call_id,
                    user_input=actual_user_input,
                )
                logger.info(
                    "[skill_turbo_tool] set_pending_resume: tcid=%s", pending_tool_call_id
                )
            else:
                logger.warning(
                    "[skill_turbo_tool] resume: no pending_tool_call_id, skip set_pending_resume"
                )

        # 跑节点（自带单节点 fallback 兜底；AbortError 透传）
        try:
            logger.info("[skill_turbo_tool] execute start: plan_name=%s", plan_name)
            node_outputs = await _run_node_stream_with_progress(node, node_inputs, plan_name)
        except AbortError as abort_exc:
            # HITL 中断时保存断点（供 adapter 在线 resume 注入 pending_tool_call_id）
            logger.info("[skill_turbo_tool] execute aborted (HITL): plan_name=%s", plan_name)
            if session is not None:
                try:
                    from jiuwenswarm.agents.skill_turbo.permission_bridge import (
                        extract_tool_interrupt,
                        save_resume_ctx,
                    )

                    tic = extract_tool_interrupt(abort_exc)
                    pending_tool_call_id = (
                        tic.tool_call.id if tic and tic.tool_call else ""
                    )
                    if pending_tool_call_id:
                        await save_resume_ctx(
                            session,
                            skill_name=skill_name,
                            scenario=scenario,
                            plan_name=plan_name,
                            inputs=agent_inputs,
                            pending_tool_call_id=pending_tool_call_id,
                        )
                        logger.info(
                            "[skill_turbo_tool] saved resume ctx: plan_name=%s tcid=%s",
                            plan_name,
                            pending_tool_call_id,
                        )
                    else:
                        logger.warning(
                            "[skill_turbo_tool] HITL interrupt but no tool_call_id, skip save"
                        )
                except Exception as save_exc:
                    logger.error(
                        "[skill_turbo_tool] save resume ctx FAILED: plan_name=%s error=%s; "
                        "online resume will replay from skill start (AbortError still propagates, "
                        "harness native resume unaffected)",
                        plan_name,
                        save_exc,
                    )
            # 透传 AbortError 给上层（harness/adapter 处理 HITL）
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

        # 提取节点声明的约定键（_ 前缀，会被 _build_product_summary 过滤故需先提取）：
        # _stop_hint: 节点自决的停止提示文本
        # _task_complete: 节点自决的任务完成标志
        # 通用机制：执行器零硬编码，仅提取透传；skill-specific 文本/判断由 turbo_codes 节点负责。
        stop_hint = node_outputs.pop("_stop_hint", None)
        task_complete = bool(node_outputs.pop("_task_complete", False))

        products = _build_product_summary(node_outputs, schema_outputs)
        summary = _build_node_summary(plan_name, node_outputs)
        if stop_hint:
            summary = f"{summary}\n\n{stop_hint}"

        logger.info(
            "[skill_turbo_tool] execute done: plan_name=%s products_keys=%s task_complete=%s",
            plan_name, list(products.keys()) if isinstance(products, dict) else "N/A",
            task_complete,
        )
        return {
            "success": True,
            "mode": "execute",
            "plan_name": plan_name,
            "summary": summary,
            "products": products,
            "task_complete": task_complete,
        }
    finally:
        # 清理 _session_var，恢复到绑定前状态（防 ContextVar 泄漏/污染）
        if _session_var_token is not None:
            try:
                from jiuwenswarm.agents.skill_turbo.executor import _session_var
                _session_var.reset(_session_var_token)
            except Exception as reset_exc:
                logger.debug(
                    "[skill_turbo_tool] reset _session_var failed: %s", reset_exc
                )


# ─────────────────────────────────────────────────────────────────────────────
# callback 绑定 + resume 检测（从当前上下文获取）
# ─────────────────────────────────────────────────────────────────────────────
# Executor 缓存（按任务复用，避免每节点重建）
# ─────────────────────────────────────────────────────────────────────────────

# 模块级缓存（进程内单例）：session_id → (cache_key, executor)
# 注意：session state 不适合（deepcopy 会丢失 executor 运行时状态）
# 用 OrderedDict + 容量上限做 LRU 兜底：正常经 skill_complete 清理；
# 异常路径（Agent 崩溃/session 异常终止）未清理的僵尸条目由容量上限回收。
# threading.Lock 保护 LRU move_to_end / popitem 组合（同步操作，不跨 await，不阻塞事件循环）。
_PARENT_EXECUTOR_CACHE: "OrderedDict[str, tuple[str, Any]]" = OrderedDict()
_PARENT_EXECUTOR_CACHE_MAX = 64
_PARENT_EXECUTOR_CACHE_LOCK = threading.Lock()


def _get_or_create_parent_executor(skill_name: str, scenario: str):
    """获取或创建 parent executor（按 session + 任务缓存，整个任务复用）.
    
    避免每次 execute 都构造完整 SkillTurbo agent 实例。
    executor 按 session_id + 任务（skill_name + scenario）缓存在模块级字典，
    同一 session 的同一任务所有 execute 共享同一 executor 实例。
    
    HITL resume 需要同一 executor（持有 _pending_resume + 确定性 _tool_call_counter）。
    
    注意：不能用 session state（会 deepcopy 导致 executor 运行时状态丢失），
    必须用模块级字典缓存活对象引用。
    
    Returns:
        (parent_executor, session) or (None, None) if unavailable
    """
    # import + 上下文获取：依赖缺失或 ContextVar 未设时降级返回 (None, None)
    try:
        from jiuwenswarm.agents.skill_turbo.skill_turbo_tools import (
            get_current_skill_turbo_adapter,
        )
        from jiuwenswarm.agents.skill_turbo.online.context_vars import (
            get_subagent_parent_session,
        )

        adapter = get_current_skill_turbo_adapter()
        session = get_subagent_parent_session()
    except (ImportError, LookupError) as exc:
        logger.debug("[skill_turbo_tool] get_or_create_parent_executor unavailable: %s", exc)
        return None, None

    if adapter is None or session is None:
        return None, None

    session_id = session.get_session_id()
    if not session_id:
        logger.warning("[skill_turbo_tool] no session_id, cannot cache executor")
        return None, None

    cache_key = f"{skill_name}:{scenario}"

    # 从模块级缓存取（加锁保护 LRU 组合操作）
    with _PARENT_EXECUTOR_CACHE_LOCK:
        cached = _PARENT_EXECUTOR_CACHE.get(session_id)
        if cached and cached[0] == cache_key:
            _PARENT_EXECUTOR_CACHE.move_to_end(session_id)  # LRU: 标记最近使用
            logger.debug(
                "[skill_turbo_tool] reuse cached executor: session=%s key=%s",
                session_id, cache_key,
            )
            return cached[1], session

    # 缓存未命中或 skill/scenario 不匹配，构造新 executor
    # 构造/缓存异常不静默吞：让调用方感知真实 bug（harness 转 tool error 返回 Agent）
    config = adapter.build_skill_turbo_config()
    from jiuwenswarm.agents.skill_turbo.agent import SkillTurbo

    skill_turbo_inst = SkillTurbo(config)
    parent_executor = getattr(skill_turbo_inst, "_executor")

    # 存入模块级缓存（LRU 兜底：超容量淘汰最久未用条目），加锁保护
    with _PARENT_EXECUTOR_CACHE_LOCK:
        _PARENT_EXECUTOR_CACHE[session_id] = (cache_key, parent_executor)
        _PARENT_EXECUTOR_CACHE.move_to_end(session_id)
        while len(_PARENT_EXECUTOR_CACHE) > _PARENT_EXECUTOR_CACHE_MAX:
            evicted_id, evicted = _PARENT_EXECUTOR_CACHE.popitem(last=False)
            logger.warning(
                "[skill_turbo_tool] evicted stale executor (cache full): session=%s key=%s",
                evicted_id, evicted[0],
            )
    logger.info(
        "[skill_turbo_tool] created and cached new executor: session=%s key=%s",
        session_id, cache_key,
    )
    return parent_executor, session


def clear_cached_parent_executor(session_id: str) -> None:
    """清理指定 session 的缓存 executor（skill_complete 时调用，防内存泄漏）."""
    if not session_id:
        return
    with _PARENT_EXECUTOR_CACHE_LOCK:
        removed = _PARENT_EXECUTOR_CACHE.pop(session_id, None)
    if removed is not None:
        logger.debug(
            "[skill_turbo_tool] cleared cached executor: session=%s key=%s",
            session_id, removed[0],
        )


def _bind_callbacks_from_context(
    node: PlanNode,
    executor: SkillCodeExecutor,
    skill_name: str,
    scenario: str,
) -> tuple[Any, Any, Any]:
    """从当前上下文获取 parent executor 并绑定 callbacks.

    复用批量 SkillTurboExecutor 的 use_tool/call_llm/stream_llm/fallback。
    parent_executor 按任务缓存，避免每节点重建。

    Returns:
        (parent_executor, session, session_var_token)，无 adapter 时返回 (None, None, None)。
        parent_executor 供 HITL resume 的 set_pending_resume 使用。
        session_var_token 供调用方 finally 清理 _session_var。
    """
    parent_executor, session = _get_or_create_parent_executor(skill_name, scenario)
    if parent_executor is None:
        logger.warning(
            "[skill_turbo_tool] no parent executor, node %s will have limited capability",
            node.plan_name,
        )
        return None, None, None

    session_var_token = None
    try:
        executor.bind_node_callbacks(node, parent_executor)
        # 绑定父会话到 executor 的 _session_var（节点内 stream 直写父会话）
        session_var_token = _bind_online_parent_session(parent_executor, node.plan_name)
    except Exception as exc:
        logger.warning("[skill_turbo_tool] bind callbacks failed: %s", exc)

    return parent_executor, session, session_var_token


def _bind_online_parent_session(parent_executor: Any, plan_name: str) -> Any:
    """绑定父会话到 executor 的 _session_var（节点内 stream 直写父会话）.

    复用批量 executor 的 ContextVar 机制：设置 _session_var 让节点内
    call_llm/stream_llm/call_tool 的事件直写父会话 stream。

    用 SubagentSessionProxy 包装父会话，自动注入 stream_source_id，
    使前端能将节点内 LLM 推理/输出事件路由到 stage 子气泡而非主气泡
    （case_22 回归：Stage 2+ 的 chat.reasoning/chat.delta 缺少
    stream_source_id，被前端归入主气泡）。
    
    返回 reset token 供调用方 finally 清理，防 ContextVar 污染。
    
    Returns:
        reset token（调用方需 finally _session_var.reset(token)），失败时返回 None。
    """
    try:
        from jiuwenswarm.agents.skill_turbo.executor import _session_var
        from jiuwenswarm.agents.skill_turbo.online.context_vars import (
            get_subagent_parent_session,
        )
        from jiuwenswarm.agents.skill_turbo.online.session_proxy import (
            SubagentSessionProxy,
        )

        parent_session = get_subagent_parent_session()
        if parent_session is not None:
            stream_source_id = f"skill_turbo:{plan_name}"
            proxy = SubagentSessionProxy(
                parent_session=parent_session,
                subagent_id=stream_source_id,
                role_id="skill_turbo",
            )
            token = _session_var.set(proxy)
            return token
        return None
    except Exception as exc:
        # 关键降级（session 绑定失败），记录 warning 便于定位
        logger.warning("[skill_turbo_tool] bind parent session failed: %s", exc)
        return None


def _get_resume_user_input() -> Any:
    """从 ContextVar 读取 HITL resume user input（stream_event_rail resume 桥接时设置）.

    Resume 路径：harness 原生重执 tool call 时在 ``ctx.extra[RESUME_USER_INPUT_KEY]``
    注入用户回复；stream_event_rail 桥接到 ContextVar 供本工具读取。
    """
    try:
        from jiuwenswarm.agents.skill_turbo.skill_turbo_tools import (
            get_skill_turbo_resume_input,
        )
        return get_skill_turbo_resume_input()
    except (ImportError, LookupError) as exc:
        # ImportError: 依赖缺失；LookupError: ContextVar 未设（防御性，当前有 default）
        # 两者均属"非 resume 路径"正常降级，返回 None。
        # 其他异常（ContextVar 被 corrupt、内部 bug）不静默吞，propagate 给 execute。
        logger.debug("[skill_turbo_tool] _get_resume_user_input unavailable: %s", exc)
        return None
