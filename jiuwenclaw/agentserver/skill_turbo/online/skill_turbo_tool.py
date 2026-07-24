# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""skill_turbo_tool —— 在线执行工具（替代 skill_acceleration_exec）。

主 Agent LLM 在 PlanTask 之间参与推理，逐个 PlanTask 推理 → 调本工具跑单节点。
分两阶段（设计 §5.2）：
- **activate**（``plan_name`` 省略，首调）：初始化 ContextStore + 加载 schema +
  返回 next_candidates，不执行任何 PlanNode
- **execute**（``plan_name`` 非空，后续）：候选集校验 → 参数组装 → 参数校验 →
  调 SkillCodeExecutor.run_single_node → 更新 ContextStore → 返回摘要 + next_candidates

省 token 关键：大字段（doc_content/outline 正文）留在 ContextStore.accumulator，
不进对话历史；products 只含标量 + 文件路径。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from openjiuwen.core.foundation.tool import tool

from jiuwenclaw.agentserver.skill_turbo.online import (
    context_store,
    executor_single,
    fallback_policy,
    flow_scheduler,
    param_validator,
    schema_loader,
    task_progress,
)

if TYPE_CHECKING:
    from openjiuwen.core.session.agent import Session

logger = logging.getLogger(__name__)

# ── 停止提示：任务完成时追加，引导 LLM 总结并结束 ──
_SKILL_TURBO_ONLINE_STOP_HINT = (
    "\n\n[SYSTEM] The skill_turbo_tool task is complete and the artifact has already "
    "been generated. The file(s) have ALREADY been sent to the user by the internal "
    "delivery pipeline — do NOT call send_file_to_user again. You should now "
    "summarize this result to the user and finish your turn. Do NOT call "
    "skill_turbo_tool, skill_tool, or send_file_to_user again for this task."
)

# 精确/后缀黑名单：大正文键（优化修复 F7，避免子串误伤 outline_path）
_LARGE_FIELD_EXACT = frozenset({
    "doc_content", "outline_text", "page_html", "narrative_context",
    "doc_raw", "html", "content", "research",
})
_LARGE_FIELD_SUFFIXES = ("_html", "_raw", "_text", "_content")
_META_PRODUCT_KEYS = frozenset({
    "status", "message", "fallback", "node", "plan_name",
})
_PATH_KEY_SUFFIXES = ("_path", "_dir", "_file", "_filename")


def _is_large_field(key: str) -> bool:
    """判断键是否为大字段（不应进 products 摘要）。

    白名单优先：路径类后缀 / 元字段不算大字段。
    黑名单：精确匹配或正文类后缀。
    """
    key_lower = key.lower()
    if key_lower in _META_PRODUCT_KEYS:
        return False
    if any(key_lower.endswith(sfx) for sfx in _PATH_KEY_SUFFIXES):
        return False
    if key_lower in _LARGE_FIELD_EXACT:
        return True
    if any(key_lower.endswith(sfx) for sfx in _LARGE_FIELD_SUFFIXES):
        return True
    return False


def _build_product_summary(node_outputs: dict[str, Any]) -> dict[str, Any]:
    """从 node_outputs 构造产物摘要（仅含标量 + 文件路径，不含大字段）。

    设计 §5.2 + 优化修复 F7：保留 outline_path 等路径键，过滤超长正文。
    """
    summary: dict[str, Any] = {}
    for key, value in node_outputs.items():
        if not isinstance(key, str):
            continue
        if _is_large_field(key):
            # 路径键已在 _is_large_field 放行；正文键若意外很短仍可保留短串
            if isinstance(value, str) and len(value) <= 80 and (
                "/" in value or "\\" in value
            ):
                summary[key] = value
            continue
        if isinstance(value, (bool, int, float)):
            summary[key] = value
            continue
        if isinstance(value, str):
            if ("/" in value or "\\" in value) and len(value) < 500:
                summary[key] = value
            elif len(value) <= 200:
                summary[key] = value
            continue
        if isinstance(value, (list, dict)):
            try:
                serialized = json.dumps(value, default=str)
                if len(serialized) <= 300:
                    summary[key] = value
            except Exception:
                logger.debug(
                    "product summary skip non-serializable key=%s type=%s",
                    key, type(value).__name__,
                    exc_info=True,
                )
    return summary


def _build_node_summary(plan_name: str, node_outputs: dict[str, Any]) -> str:
    """构造节点执行摘要文本。"""
    status = node_outputs.get("status", "ok")
    message = node_outputs.get("message", "")
    parts = [f"节点 {plan_name} 执行完成（status={status}）"]
    if message:
        parts.append(str(message)[:200])
    return "，".join(parts)


def release_turbo_active_body(parent_session: Any, skill_name: str) -> None:
    """释放层2 turbo 正文 pin（任务完成/回退时调用）。

    设计 §5.1 / §6.6：任务完成或回退 skill_tool 时 unregister_active_skill_body，
    移除 context engine 的 window-pin，释放 [ACTIVE SKILL_TURBO BODY] 块。
    复用 skill_prompt_rail._unregister_turbo_active_body（其内部调
    openjiuwen active_skill_bodies.unregister_active_skill_body）。
    """
    if parent_session is None or not skill_name:
        return
    turbo_name = f"{skill_name}_turbo"
    try:
        from jiuwenclaw.agentserver.deep_agent.rails.skill_prompt_rail import (
            _unregister_turbo_active_body,
        )
        _unregister_turbo_active_body(parent_session, turbo_name)
        logger.debug(
            "[SkillTurboOnlineTool] released turbo active body skill=%s", skill_name,
        )
    except Exception as exc:
        logger.debug(
            "[SkillTurboOnlineTool] release turbo active body failed: %s", exc,
        )


# 兼容旧私有名
_release_turbo_active_body = release_turbo_active_body


def _resolve_skill_root_for_turbo(skill_name: str, env: Any) -> str:
    """解析指定 skill 的根目录（含 turbo/ 子目录）。

    优先级：
    1. env.skill_root / skill_name（环境配置的 skills 父目录 + skill 名）
    2. get_agent_registered_skill_dirs() 各目录 / skill_name
    3. get_agent_registered_skill_dirs() 各目录（可能本身就是 skill 根）

    skill_name 经白名单校验；拼接后 resolve 必须仍位于 base 之下。
    """
    from jiuwenclaw.agentserver.skill_turbo.online.skill_name_guard import (
        InvalidSkillNameError,
        safe_join_skill_dir,
        validate_skill_name,
    )

    try:
        skill_name = validate_skill_name(skill_name)
    except InvalidSkillNameError as exc:
        logger.warning("[SkillTurboOnlineTool] %s", exc)
        return ""

    # 1. env.skill_root / skill_name
    env_skill_root = getattr(env, "skill_root", "") or ""
    if env_skill_root:
        joined = safe_join_skill_dir(env_skill_root, skill_name)
        if joined is not None:
            return str(joined)
        # 也许 env.skill_root 本身就是 skill 根
        env_path = Path(env_skill_root)
        if env_path.name == skill_name and env_path.is_dir():
            return str(env_path.resolve())

    # 2. get_agent_registered_skill_dirs
    try:
        from jiuwenclaw.utils import get_agent_registered_skill_dirs
        skill_dirs = get_agent_registered_skill_dirs()
        for d in skill_dirs:
            joined = safe_join_skill_dir(d, skill_name)
            if joined is not None:
                return str(joined)
            if d.name == skill_name and d.is_dir():
                return str(d.resolve())
    except Exception as exc:
        logger.debug("[SkillTurboOnlineTool] get_agent_registered_skill_dirs failed: %s", exc)

    return ""


def _read_last_user_message(session: Any) -> str:
    """读取 session 最后一条用户消息（activate 时 query 缺省用）。"""
    if session is None:
        return ""
    try:
        # 尝试从 session 获取消息历史
        messages = getattr(session, "messages", None)
        if messages is None:
            getter = getattr(session, "get_messages", None)
            if callable(getter):
                messages = getter()
        if not messages:
            return ""
        # 从后往前找最后一条用户消息
        for msg in reversed(messages):
            role = getattr(msg, "role", "") or getattr(msg, "type", "")
            if role in ("user", "human", "UserMessage"):
                content = getattr(msg, "content", "")
                if isinstance(content, str) and content.strip():
                    return content.strip()
                if isinstance(content, list):
                    # 多模态消息
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            text = part.get("text", "")
                            if text.strip():
                                return text.strip()
    except Exception as exc:
        logger.debug("[SkillTurboOnlineTool] read last user message failed: %s", exc)
    return ""


def _build_base_accumulator(
    query: str,
    skill_name: str,
    skill_root: str,
    env: Any,
    parent_session: Any,
    request_metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    """构造 activate 时的初始 accumulator（query + 锁定 skill + env 补缺）。

    优化修复 F2：强制写入工具入参 skill_name 与 resolve 后的 skill_root，
    不以 env.skill_root（可能是 skills 父目录）覆盖。
    """
    accumulator: dict[str, Any] = {
        "query": query,
        "skill_name": skill_name,
        "skill_root": skill_root,
    }
    # env 仅补缺（不覆盖已锁定的 skill_name/skill_root）
    skill_checksum = getattr(env, "skill_checksum", "")
    if skill_checksum and "skill_checksum" not in accumulator:
        accumulator["skill_checksum"] = skill_checksum
    accumulator["skill_checksum_ok"] = getattr(env, "skill_checksum_ok", False)
    # session 上下文
    if parent_session is not None:
        getter = getattr(parent_session, "get_session_id", None)
        sid = getter() if callable(getter) else ""
        if sid:
            accumulator["conversation_id"] = str(sid)
    # request metadata
    if isinstance(request_metadata, dict):
        output_dir = request_metadata.get("output_dir")
        if output_dir and isinstance(output_dir, str) and output_dir.strip():
            accumulator["workspace_base"] = output_dir.strip()
        accumulator["metadata"] = request_metadata
    return accumulator


# ── 工具定义 ──

def normalize_plan_name(plan_name: Any) -> str | None:
    """归一化 plan_name；首期仅返回 str|None（兼容历史 dict/list 畸形）。"""
    if plan_name is None:
        return None
    if isinstance(plan_name, str):
        return plan_name.strip() or None
    if isinstance(plan_name, list):
        items = [str(x).strip() for x in plan_name if str(x).strip()]
        if not items:
            return None
        if len(items) > 1:
            logger.warning(
                "[SkillTurboOnlineTool] plan_name list not supported in v1, using first: %s",
                items[0],
            )
        return items[0]
    if isinstance(plan_name, dict):
        if "node" in plan_name and isinstance(plan_name["node"], str):
            logger.warning(
                "[SkillTurboOnlineTool] deprecated plan_name dict with 'node' key",
            )
            return str(plan_name["node"]).strip() or None
        keys = [str(k) for k, v in plan_name.items() if v]
        if not keys:
            return None
        if len(keys) > 1:
            logger.warning(
                "[SkillTurboOnlineTool] deprecated plan_name dict multi-key, using first: %s",
                keys[0],
            )
        else:
            logger.warning(
                "[SkillTurboOnlineTool] deprecated plan_name dict-as-flag: %s",
                keys[0],
            )
        return keys[0]
    raise TypeError(f"unsupported plan_name type: {type(plan_name)}")


@tool(
    name="skill_turbo_tool",
    description=(
        "Skill 加速器在线执行工具。当用户意图匹配某 skill 加速面时优先调用。"
        "分两阶段：①activate（首调，plan_name 省略）：初始化任务上下文 + 加载执行流程，"
        "返回首个候选节点；②execute（后续，plan_name 非空）：执行单个 PlanTask，"
        "返回产物摘要 + 下一候选集。主 Agent 据返回的 next_candidates 逐节点驱动。"
        "【重要】每次调用仅处理一个独立任务。若用户要求多个产物，必须串行调用。"
        "【临时排除】当用户消息中包含自定义模板路径信息（如出现 模板目录路径、"
        "模板主文件路径 等模板路径字段，或含 pack_dir）时，不要调用此工具——"
        "当前 skill 加速器暂未适配模板包功能，此类请求应改用 skill_tool 走标准流程。"
    ),
)
async def skill_turbo_tool(
    skill_name: str,
    scenario: str,
    plan_name: str | None = None,
    query: str | None = None,
    increment: dict | None = None,
) -> dict[str, Any]:
    """Skill 加速器在线执行（activate / execute 两阶段）。

    Args:
        skill_name: turbo 源 skill 名，如 "pptx-craft"。首调必填，锁定本次 turbo 任务。
        scenario: 任务切面，如 "create_ppt"。首调锁定，后续必须一致。
        plan_name: None=activate（首调：只初始化+加载层2，不执行节点）；
            非空字符串=execute（执行单个 PlanTask，如 "p0_pipeline_init"）。
        query: 用户任务原文。activate 时传入或缺省（缺省则工具读 session 最后一条用户消息）。
        increment: 可选增量覆盖（execute 时；仅覆盖该节点 inputs 声明的键）。
    """
    # ── [TEMP-TEMPLATE-BYPASS] 模板请求拦截（临时措施，待 skill 加速器适配模板功能后删除整块）──
    _template_path_markers = ("模板目录路径", "模板主文件路径")
    _check_text = query or ""
    if any(marker in _check_text for marker in _template_path_markers):
        logger.info(
            "[SkillTurboOnlineTool] 检测到自定义模板路径，跳过 skill 加速器，建议改用 skill_tool"
        )
        return {
            "success": False,
            "error": (
                "检测到用户提供了自定义模板路径，当前 skill 加速器暂未适配模板包功能。"
                "请改用 skill_tool 走 pptx-craft 标准流程处理此请求"
                "（直接执行，无需再调用 skill_turbo_tool）。"
            ),
        }
    # ── [/TEMP-TEMPLATE-BYPASS] ──

    # 运行时归一化（兼容历史 dict 畸形；公开 schema 已是 str|None）
    try:
        plan_name = normalize_plan_name(plan_name)
    except TypeError as exc:
        return {"success": False, "error": str(exc)}

    from jiuwenclaw.agentserver.skill_turbo.skill_turbo_tools import (
        get_current_skill_turbo_adapter,
        get_current_request_metadata,
        set_skill_turbo_hitl_tic,
    )
    from jiuwenclaw.agentserver.tools.subagent_executor import get_subagent_parent_session

    adapter = get_current_skill_turbo_adapter()
    if adapter is None:
        return {"success": False, "error": "SkillTurboOnlineTool 未初始化（adapter 未注入）"}

    parent_session: Session | None = get_subagent_parent_session()
    request_metadata = get_current_request_metadata()
    if parent_session is None:
        logger.warning(
            "[SkillTurboOnlineTool] parent_session missing for task progress "
            "mode=%s",
            "activate" if plan_name is None else "execute",
        )

    # 构建 SkillTurbo 实例（提供 executor + environment）
    from jiuwenclaw.agentserver.skill_turbo.agent import SkillTurbo
    config = adapter.build_skill_turbo_config()
    skill_turbo_inst = SkillTurbo(config)
    env = skill_turbo_inst.env
    executor = skill_turbo_inst.executor

    # 解析 skill root（含 turbo/ 子目录）
    from jiuwenclaw.agentserver.skill_turbo.online.skill_name_guard import (
        InvalidSkillNameError,
        validate_skill_name,
    )
    try:
        skill_name = validate_skill_name(skill_name)
    except InvalidSkillNameError as exc:
        return {"success": False, "error": str(exc)}

    skill_root = _resolve_skill_root_for_turbo(skill_name, env)
    if not skill_root:
        return {
            "success": False,
            "error": f"未找到 skill {skill_name!r} 的根目录",
        }

    # 探测 turbo 产物
    turbo_face = schema_loader.discover_turbo_face(skill_root)
    if turbo_face is None:
        return {
            "success": False,
            "error": f"skill {skill_name!r} 无 turbo 加速面产物",
        }
    if scenario not in turbo_face.scenarios:
        return {
            "success": False,
            "error": f"切面 {scenario!r} 不在可用切面列表 {list(turbo_face.scenarios)}",
        }

    # ── activate（plan_name 省略）──
    if plan_name is None:
        with task_progress.progress_emit_scope(mode="activate") as tracker:
            result = await _activate(
                skill_name, scenario, query, skill_root, turbo_face, env, executor,
                parent_session, request_metadata,
            )
            return _attach_progress_diag(result, tracker)

    # ── execute（plan_name 非空）──
    with task_progress.progress_emit_scope(mode="execute") as tracker:
        result = await _execute(
            skill_name, scenario, plan_name, increment, turbo_face, env, executor,
            parent_session, request_metadata,
        )
        return _attach_progress_diag(result, tracker)


def _attach_progress_diag(
    result: dict[str, Any],
    tracker: task_progress.ProgressEmitTracker,
) -> dict[str, Any]:
    """进度是增强项：缺 session / 写失败时附诊断，不硬失败。"""
    if not isinstance(result, dict):
        return result
    summary = tracker.warning_summary
    if summary:
        result = dict(result)
        result["progress_emit_warning"] = summary
    return result


async def _activate(
    skill_name: str,
    scenario: str,
    query: str | None,
    skill_root: str,
    turbo_face: schema_loader.TurboFace,
    env: Any,
    executor: Any,
    parent_session: Any,
    request_metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    """activate 模式：初始化 ContextStore + 加载 schema + 返回 next_candidates。"""
    # query：传入则用，缺省读 session 最后一条用户消息
    if not query:
        query = _read_last_user_message(parent_session)
    if not query:
        return {"success": False, "error": "activate 缺少 query，且 session 无最后一条用户消息"}

    # 创建 ContextStore
    session_id = ""
    if parent_session is not None:
        getter = getattr(parent_session, "get_session_id", None)
        if callable(getter):
            session_id = str(getter() or "")
    task_id = context_store.make_task_id(session_id)
    # 优先用 turbo_face.skill_root（与探测一致），缺省用 resolve 结果
    resolved_root = turbo_face.skill_root or skill_root
    accumulator = _build_base_accumulator(
        query, skill_name, resolved_root, env, parent_session, request_metadata,
    )
    ctx = context_store.TurboContext(
        task_id=task_id,
        skill_name=skill_name,
        scenario=scenario,
        turbo_dir=turbo_face.turbo_dir,
        accumulator=accumulator,
        completed=set(),
        retry_count={},
        fallback_count=0,
        fallback_nodes=[],
        status="running",
    )

    # 加载 schema
    try:
        schema = schema_loader.load_schema(turbo_face.turbo_dir, scenario)
    except Exception as exc:
        return {"success": False, "error": f"加载 schema 失败: {exc}"}

    # 计算首个候选集（可能 when-skip）
    candidates = flow_scheduler.advance_and_candidates(schema, ctx)

    # 任务进度：首帧 pending 列表 + when-skip 同步
    req_id = task_progress.extract_request_id(request_metadata)
    await task_progress.init_progress(
        ctx, schema, parent_session, request_id=req_id,
    )
    await task_progress.sync_from_completed(
        ctx, schema, parent_session, request_id=req_id,
    )

    # 持久化 ctx（含 task_progress）
    await context_store.save_online_context(parent_session, ctx)

    logger.info(
        "[SkillTurboOnlineTool] activate skill=%s scenario=%s task=%s candidates=%s",
        skill_name, scenario, task_id, candidates,
    )

    return {
        "success": True,
        "mode": "activate",
        "skill_name": skill_name,
        "scenario": scenario,
        "execution_flow": flow_scheduler.format_execution_flow_overview(schema),
        "next_candidates": candidates,
        "task_complete": False,
    }


async def _execute(
    skill_name: str,
    scenario: str,
    plan_name: str,
    increment: dict | None,
    turbo_face: schema_loader.TurboFace,
    env: Any,
    executor: Any,
    parent_session: Any,
    request_metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    """execute 模式：候选集校验 → 参数组装 → 参数校验 → 执行单节点 → 更新 ContextStore。"""
    # 取 ContextStore
    ctx = await context_store.load_online_context(parent_session)
    if ctx is None:
        return {
            "success": False,
            "error": "无活跃的在线任务上下文，请先 activate（plan_name 省略）",
        }

    # 校验 scenario/skill_name 一致
    if ctx.scenario != scenario or ctx.skill_name != skill_name:
        return {
            "success": False,
            "error": (
                f"scenario/skill_name 不一致：ctx=({ctx.skill_name},{ctx.scenario}), "
                f"调用=({skill_name},{scenario})"
            ),
        }

    # 加载 schema
    try:
        schema = schema_loader.load_schema(ctx.turbo_dir, scenario)
    except Exception as exc:
        return {"success": False, "error": f"加载 schema 失败: {exc}"}

    req_id = task_progress.extract_request_id(request_metadata)

    # resume 校准：空进度则补 init；有进度则回退陈旧 in_progress
    if not (getattr(ctx, "task_progress", None) or {}):
        await task_progress.init_progress(
            ctx, schema, parent_session, request_id=req_id,
        )
    else:
        task_progress.prepare_resume_progress(ctx)

    # 候选集校验（advance_and_candidates 可能 when-skip 并写 skip_defaults；早退前须落盘 — F11）
    candidates = flow_scheduler.advance_and_candidates(schema, ctx)
    await task_progress.sync_from_completed(
        ctx, schema, parent_session, request_id=req_id,
    )

    plan_names = [plan_name]
    invalid = [p for p in plan_names if p not in candidates]
    if invalid:
        await context_store.save_online_context(parent_session, ctx)
        await task_progress.compensate_task_update_if_needed(
            ctx, parent_session, had_state_change=True, request_id=req_id,
        )
        return fallback_policy.build_candidate_violation_output(
            invalid[0] if len(invalid) == 1 else str(invalid), candidates,
        )

    # 逐个执行（当前 plan_names 为单元素；并行批预留直接扩展此循环）
    req_meta = request_metadata or {}
    single = executor_single.SkillCodeExecutor(
        env,
        request_id=task_progress.extract_request_id(req_meta),
        channel_id=task_progress.extract_channel_id(req_meta),
    )

    had_progress_change = False
    for p in plan_names:
        if await task_progress.mark_started(
            ctx, p, parent_session, request_id=req_id,
        ):
            had_progress_change = True
        await context_store.save_online_context(parent_session, ctx)

    results: list[Any] = []
    for p in plan_names:
        try:
            results.append(
                await _execute_single(
                    p, schema, ctx, increment, single, executor, parent_session,
                    request_metadata=request_metadata,
                )
            )
        except BaseException as exc:
            results.append(exc)

    # 处理结果
    for i, result in enumerate(results):
        p = plan_names[i]
        if isinstance(result, dict) and result.get("success") is False:
            # 参数重试提示（无 fallback 标记）→ 返回给 Agent 重试
            if not result.get("fallback"):
                await context_store.save_online_context(parent_session, ctx)
                await task_progress.compensate_task_update_if_needed(
                    ctx, parent_session,
                    had_state_change=had_progress_change,
                    request_id=req_id,
                )
                return result
            # 参数耗尽后 fallback 仍失败 → 计数；超阈值整任务回退，否则返回错误
            ctx.record_fallback(p)
            if await task_progress.mark_completed(
                ctx, p, parent_session, failed=True,
                error=str(result.get("error") or "fallback failed"),
                request_id=req_id,
            ):
                had_progress_change = True
            if fallback_policy.should_task_fallback(
                ctx.fallback_count, fallback_nodes=ctx.fallback_nodes,
            ):
                await task_progress.finalize_progress(
                    ctx, parent_session, request_id=req_id,
                )
                ctx.status = "fallback_to_skill_tool"
                await context_store.save_online_context(parent_session, ctx)
                context_store.mark_pending_clear_online_context(parent_session)
                release_turbo_active_body(parent_session, skill_name)
                return fallback_policy.build_task_fallback_output(
                    skill_name, ctx.fallback_count, ctx.fallback_nodes, stage=3,
                )
            await context_store.save_online_context(parent_session, ctx)
            await task_progress.compensate_task_update_if_needed(
                ctx, parent_session,
                had_state_change=had_progress_change,
                request_id=req_id,
            )
            return result
        if isinstance(result, BaseException):
            # AbortError 或其他异常
            from jiuwenclaw.agentserver.skill_turbo.plan_node import AbortError
            if isinstance(result, AbortError):
                # HITL：保持 in_progress，不标 completed
                await context_store.save_online_context(parent_session, ctx)
                await task_progress.compensate_task_update_if_needed(
                    ctx, parent_session,
                    had_state_change=had_progress_change,
                    request_id=req_id,
                )
                return await _handle_abort(result, p, ctx, parent_session)
            logger.warning(
                "[SkillTurboOnlineTool] node execution failed plan=%s error=%s",
                p, result, exc_info=result,
            )
            ctx.record_fallback(p)
            if await task_progress.mark_completed(
                ctx, p, parent_session, failed=True,
                error=str(result), request_id=req_id,
            ):
                had_progress_change = True
            if fallback_policy.should_task_fallback(
                ctx.fallback_count, fallback_nodes=ctx.fallback_nodes,
            ):
                await task_progress.finalize_progress(
                    ctx, parent_session, request_id=req_id,
                )
                ctx.status = "fallback_to_skill_tool"
                await context_store.save_online_context(parent_session, ctx)
                context_store.mark_pending_clear_online_context(parent_session)
                release_turbo_active_body(parent_session, skill_name)
                return fallback_policy.build_task_fallback_output(
                    skill_name, ctx.fallback_count, ctx.fallback_nodes, stage=3,
                )
            # 未达整任务回退阈值：必须返回失败，禁止 success:True 误导 LLM
            await context_store.save_online_context(parent_session, ctx)
            await task_progress.compensate_task_update_if_needed(
                ctx, parent_session,
                had_state_change=had_progress_change,
                request_id=req_id,
            )
            return {
                "success": False,
                "error": str(result),
                "plan_name": p,
                "fallback": True,
            }
        # 正常完成 / 节点 fallback 成功：result 是 node_outputs dict
        if isinstance(result, dict):
            if result.get("fallback"):
                ctx.record_fallback(p)
            ctx.update(result, p)
            if await task_progress.mark_completed(
                ctx, p, parent_session, failed=False, request_id=req_id,
            ):
                had_progress_change = True

    # 检查累计 fallback → 整任务回退
    if fallback_policy.should_task_fallback(
        ctx.fallback_count, fallback_nodes=ctx.fallback_nodes,
    ):
        await task_progress.finalize_progress(
            ctx, parent_session, request_id=req_id,
        )
        ctx.status = "fallback_to_skill_tool"
        await context_store.save_online_context(parent_session, ctx)
        context_store.mark_pending_clear_online_context(parent_session)
        # 释放层2 turbo 正文 pin（设计 §5.7 / §6.4）
        release_turbo_active_body(parent_session, skill_name)
        # M6（设计 §8.4 阶段3）：回退直跳 skill_tool（stage=3）
        return fallback_policy.build_task_fallback_output(
            skill_name, ctx.fallback_count, ctx.fallback_nodes, stage=3,
        )

    # 持久化 ctx
    await context_store.save_online_context(parent_session, ctx)

    # 构造摘要 + next_candidates
    candidates = flow_scheduler.advance_and_candidates(schema, ctx)
    if await task_progress.sync_from_completed(
        ctx, schema, parent_session, request_id=req_id,
    ):
        had_progress_change = True
    await context_store.save_online_context(parent_session, ctx)
    complete = flow_scheduler.is_task_complete(schema, ctx)

    # 产物摘要（最后一个节点的 outputs，仅含标量 + 路径）
    last_result = results[-1] if results else {}
    products = _build_product_summary(last_result) if isinstance(last_result, dict) else {}
    summary_text = _build_node_summary(
        plan_names[-1] if len(plan_names) == 1 else str(plan_names),
        last_result if isinstance(last_result, dict) else {},
    )

    output: dict[str, Any] = {
        "success": True,
        "mode": "execute",
        "plan_name": plan_name,
        "summary": summary_text,
        "products": products,
        "next_candidates": candidates,
        "task_complete": complete,
    }
    if complete:
        output["result"] = "任务已完成"
        output["result"] += _SKILL_TURBO_ONLINE_STOP_HINT
        await task_progress.finalize_progress(
            ctx, parent_session, request_id=req_id,
        )
        # 先落盘最终进度供 after_tool_call flush；清理由 rail 在 flush 后执行（F1/F2）
        ctx.status = "completed"
        await context_store.save_online_context(parent_session, ctx)
        context_store.mark_pending_clear_online_context(parent_session)
        release_turbo_active_body(parent_session, skill_name)
    else:
        await task_progress.compensate_task_update_if_needed(
            ctx, parent_session,
            had_state_change=had_progress_change,
            request_id=req_id,
        )
    return output


async def _execute_single(
    plan_name: str,
    schema: dict,
    ctx: context_store.TurboContext,
    increment: dict | None,
    single: executor_single.SkillCodeExecutor,
    executor: Any,
    parent_session: Any,
    *,
    request_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """执行单个节点：参数组装 → 参数校验 → run_single_node / 参数耗尽 fallback。"""
    # 组装 node_inputs（全量 accumulator 副本，见 param_validator F1）
    node_inputs, missing = param_validator.assemble_node_inputs(
        plan_name, schema, ctx.accumulator, increment,
    )

    # 参数校验
    if missing:
        retry = ctx.record_retry(plan_name)
        if fallback_policy.should_retry(retry):
            return fallback_policy.build_param_retry_output(
                plan_name, missing, retry,
            )
        # 重试耗尽 → 单节点 fallback（优化修复 F4；不裸跑节点）
        logger.warning(
            "[SkillTurboOnlineTool] param retry exhausted plan=%s retry=%s → node fallback",
            plan_name, retry,
        )
        return await _param_validation_fallback(
            plan_name, schema, ctx, node_inputs, missing, single, executor,
            parent_session, request_metadata=request_metadata,
        )

    # O1：execute 期绑定父会话到 _session_var，供 call_llm 写 llm_reasoning
    from jiuwenclaw.agentserver.skill_turbo.executor import (
        bind_online_parent_session,
        reset_online_parent_session,
    )

    req_id = task_progress.extract_request_id(request_metadata)
    channel_id = task_progress.extract_channel_id(request_metadata)
    session_tokens = bind_online_parent_session(
        parent_session, request_id=req_id, channel_id=channel_id,
    )
    session_id = ""
    if parent_session is not None:
        getter = getattr(parent_session, "get_session_id", None)
        if callable(getter):
            try:
                session_id = str(getter() or "")
            except Exception:
                session_id = ""
    logger.info(
        "[SkillTurboOnlineTool] bind_session session_id=%s for plan=%s",
        session_id or "-",
        plan_name,
    )
    try:
        node_outputs = await single.run_single_node(
            turbo_dir=ctx.turbo_dir,
            scenario=ctx.scenario,
            plan_name=plan_name,
            schema=schema,
            node_inputs=node_inputs,
            parent_session=parent_session,
            executor=executor,
        )
        return node_outputs
    finally:
        reset_online_parent_session(session_tokens)


async def _param_validation_fallback(
    plan_name: str,
    schema: dict,
    ctx: context_store.TurboContext,
    node_inputs: dict[str, Any],
    missing: list[str],
    single: executor_single.SkillCodeExecutor,
    executor: Any,
    parent_session: Any,
    *,
    request_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """参数校验重试耗尽后走 executor.fallback → fallback_handler（F4）。"""
    error = ValueError(f"参数校验失败，缺失 inputs: {missing}")
    if executor is None:
        return {
            "success": False,
            "error": str(error),
            "missing_keys": list(missing),
            "fallback": True,
            "plan_name": plan_name,
        }

    from jiuwenclaw.agentserver.skill_turbo.executor import (
        bind_online_parent_session,
        reset_online_parent_session,
    )

    req_id = task_progress.extract_request_id(request_metadata)
    channel_id = task_progress.extract_channel_id(request_metadata)
    session_tokens = bind_online_parent_session(
        parent_session, request_id=req_id, channel_id=channel_id,
    )
    try:
        node = single.load_node(
            ctx.turbo_dir, ctx.scenario, plan_name, schema,
        )
        bind = getattr(executor, "bind_node_callbacks", None) or getattr(
            executor, "_bind_node_callbacks", None,
        )
        if callable(bind):
            bind(node)
        # 原 set_parent_session 死代码已替换为 bind_online_parent_session（O1/R1b）
        result = await executor.fallback(node, node_inputs, error)
        if isinstance(result, dict):
            out = dict(result)
            out.setdefault("fallback", True)
            return out
        return {"result": result, "fallback": True, "plan_name": plan_name}
    except Exception as exc:
        logger.warning(
            "[SkillTurboOnlineTool] param fallback failed plan=%s error=%s",
            plan_name, exc, exc_info=True,
        )
        return {
            "success": False,
            "error": f"参数校验 fallback 失败: {exc}",
            "missing_keys": list(missing),
            "fallback": True,
            "plan_name": plan_name,
        }
    finally:
        reset_online_parent_session(session_tokens)


async def _handle_abort(
    exc: BaseException,
    plan_name: str,
    ctx: context_store.TurboContext,
    parent_session: Any,
) -> dict[str, Any]:
    """处理 HITL 中断（AbortError）：存 tic + 持久化中断现场。

    设计 §6.5：在线模式无 root，中断点在某个 group/叶节点内部。
    - set_skill_turbo_hitl_tic：存 ToolInterruptException 到 ContextVar，
      stream_event_rail.after_tool_call 据此改写 tool_result 为 TIE →
      harness 原生 HITL 机制暂停 + 前端展示审批三件套
    - save_online_context：持久化 ContextStore 快照（accumulator/completed/...）
    - save_online_interrupt_state：持久化 interrupted_plan_name + pending_tool_call_id，
      恢复时只重放该节点（非 root 重放）
    """
    from jiuwenclaw.agentserver.skill_turbo.skill_turbo_tools import set_skill_turbo_hitl_tic
    from jiuwenclaw.agentserver.skill_turbo.permission_bridge import extract_tool_interrupt

    tic = extract_tool_interrupt(exc)
    if tic is not None:
        tcid = tic.tool_call.id if tic.tool_call else ""
        logger.info(
            "[SkillTurboOnlineTool] HITL interrupt plan=%s tcid=%s",
            plan_name, tcid or "?",
        )
        set_skill_turbo_hitl_tic(tic)
        # 持久化中断现场：ContextStore 快照 + 中断节点信息
        # 两者共用一次 pre_run/post_run 落盘（skip_post_run 合并）
        await context_store.save_online_context(parent_session, ctx, skip_post_run=True)
        await context_store.save_online_interrupt_state(
            parent_session,
            interrupted_plan_name=plan_name,
            pending_tool_call_id=tcid,
        )
        return {"success": False, "error": "任务已暂停等待审批", "plan_name": plan_name}

    logger.warning("[SkillTurboOnlineTool] AbortError without ToolInterruptException cause")
    return {"success": False, "error": f"任务中断: {exc}", "plan_name": plan_name}


def get_skill_turbo_online_tools() -> list:
    """返回在线执行工具列表，供 interface_deep.py 注册。"""
    return [skill_turbo_tool]


__all__ = [
    "skill_turbo_tool",
    "get_skill_turbo_online_tools",
    "normalize_plan_name",
]
