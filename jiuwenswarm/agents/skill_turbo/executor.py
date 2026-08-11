# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""SkillTurboExecutor —— 规划代码校验、加载与异步执行。"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import importlib
import json
import logging
import secrets
import sys
import time
import uuid
from contextlib import AbstractAsyncContextManager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator

from openjiuwen.core.session.agent import Session
from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    ModelCallInputs,
    ToolCallInputs,
)

from jiuwenswarm.agents.harness.common.rails.stream_event_rail import JiuSwarmStreamEventRail
from jiuwenswarm.server.runtime.agent_adapter.llm_io_trace import (
    begin_tool_trace_event,
    end_tool_trace_event,
    log_tool_call_input,
    log_tool_call_output,
)
from jiuwenswarm.agents.skill_turbo.permission_bridge import (
    build_tool_ctx,
)
from jiuwenswarm.agents.skill_turbo.json_utils import extract_llm_json

# stream_source_id 字段名（目标 stream_utils 未导出常量，本地定义）。
STREAM_SOURCE_ID_FIELD = "stream_source_id"
from jiuwenswarm.agents.skill_turbo.plan_node import AbortError, PlanNode
from jiuwenswarm.agents.skill_turbo.validator import (
    PlanCodeValidator,
)

if TYPE_CHECKING:
    from jiuwenswarm.agents.skill_turbo.environment import SkillTurboEnvironment
    from jiuwenswarm.agents.skill_turbo.evolver import SkillTurboEvolver

logger = logging.getLogger(__name__)

# ──────────────────────── 全局上下文变量 ────────────────────────
# Session管理（用于发送事件）
_session_var: ContextVar[Session | None] = ContextVar("skill_turbo_session", default=None)

# 流式输出缓冲层：对 chat.delta / chat.reasoning 累加后再 flush，
# 减少高频小 chunk 对前端的消息压力。与 subagent_executor 的
# _SUBAGENT_STREAM_FLUSH_INTERVAL_SECONDS 语义一致。
_SKILL_TURBO_STREAM_FLUSH_INTERVAL_SECONDS: float = 3.0

# 需要缓冲的事件类型
_BUFFERABLE_EVENT_TYPES: frozenset[str] = frozenset({"chat.delta", "chat.reasoning"})

# Request上下文
_request_id_var: ContextVar[str] = ContextVar("skill_turbo_request_id", default="")
_channel_id_var: ContextVar[str] = ContextVar("skill_turbo_channel_id", default="")


# ──────────────────────── 简单的ToolCall类 ────────────────────────
@dataclass
class ToolCall:
    """简单的ToolCall对象，用于Rail回调。"""
    name: str
    arguments: dict[str, Any]
    id: str


# ──────────────────────── 异常类型 ────────────────────────


class FallbackLimitExceededError(Exception):
    """Fallback 次数超过限制。"""


# ──────────────────────── 执行配置 ────────────────────────
@dataclass
class ExecutorConfig:
    """Executor 配置项。"""

    max_fallback_count: int = 3  # 最大 fallback 次数
    enable_fallback: bool = True  # 是否启用 fallback 机制


# LLM 并发槽位排队等待超过该阈值（毫秒）时，升级日志级别到 INFO，
# 便于在生产环境观测排队拥堵；低于阈值的快速排队仅打 DEBUG。
_LLM_QUEUE_WAIT_LOG_THRESHOLD_MS: float = 1000.0


class SkillTurboExecutor:
    """规划代码运行时引擎。"""

    def __init__(
        self,
        environment: SkillTurboEnvironment,
        trace_collector: SkillTurboEvolver | None = None,
        config: ExecutorConfig | None = None,
    ):
        self._env = environment
        self._validator = PlanCodeValidator(
            allowed_import_prefixes=environment.skill_code_import_prefixes
        )
        self._trace_collector = trace_collector
        self._config = config or ExecutorConfig()

        # ──────────────────────── Rail机制初始化 ────────────────────────
        # 引入核心Rail：StreamEventRail（用于发送事件）
        self._stream_event_rail = JiuSwarmStreamEventRail()

        # 复用 DeepAgent 已有的 PermissionInterruptRail（基于权限引擎做 ALLOW/DENY/ASK）。
        # ASK 决策会抛出 AbortError(cause=ToolInterruptException(request, tool_call))，
        # 由 _run_rail_hook → use_tool 透传出去，最终被 adapter 转成 HITL 三件套 chunk。
        permission_rail = self._build_permission_rail()
        # 保存引用：replay-skip 路径需跳过权限 rail，但保留事件发射类 rail
        self._permission_rail = permission_rail

        # 节点内 ask_user：工具由 tools_loader 注册；rail 负责 before_tool_call 打断。
        # 不调用 rail.init（那会往 DeepAgent.ability_manager 再挂一遍工具）。
        ask_user_rail = self._build_ask_user_rail()
        self._ask_user_rail = ask_user_rail

        # Rail列表（按优先级排序）
        # SkillTurboArtifactRail 已从链上移除：后续若需产物上报再接 TaskExecutionRail hook。
        self._rails = [self._stream_event_rail]
        if permission_rail is not None:
            self._rails.append(permission_rail)
        if ask_user_rail is not None:
            self._rails.append(ask_user_rail)

        # 按优先级排序（priority越小越先执行）
        self._rails.sort(key=lambda r: getattr(r, 'priority', 0))

        logger.debug(
            "[SkillTurboExecutor] Rails initialized: %s",
            [type(r).__name__ for r in self._rails]
        )

        # 执行状态追踪（每个请求独立）
        # 并发安全说明：SkillTurbo 每次请求创建新实例，SkillTurboExecutor 作为其实例属性也是请求隔离的，
        # 因此实例属性不会跨请求共享，不存在并发问题。
        self._fallback_count = 0
        self._execution_inputs: dict[str, Any] = {}
        # use_tool 计数器：(tool_name, canonical_args) → call_index，用于生成
        # 重放确定性 tool_call_id（Step 8 中真正落实算法，这里先准备容器）。
        self._tool_call_counter: dict[str, int] = {}
        # resume 模式下，adapter 在 run_stream 入口注入；executor 在 use_tool
        # 命中目标 tool_call_id 时一次性消费。
        self._pending_resume: dict[str, Any] | None = None
        
        # 当前任务状态（用于跨协程共享当前 task_id，解决 asyncio.create_task 复制 ContextVar 的问题）
        self._current_task_id_holder: dict[str, str | None] = {"task_id": None}
        self._task_states_holder: dict[str, dict[str, Any]] = {}

        # 节点产物记录 holder：plan_name → 产物 dict。
        # 供 node_artifacts 属性对外暴露；在线执行路径下不再由 task 事件机器填充，
        # 保留空 dict 以兼容外部读取（agent.artifact_holder）。
        self._node_artifacts_holder: dict[str, dict[str, Any]] = {}

        # ──────────────────────── LLM 并发限制 ────────────────────────
        # 实例级 Semaphore，限制本 Executor 内 call_llm / stream_llm 的并发数。
        # 配置来源：environment.config["llm_concurrency_limit"]，<=0 表示不限制。
        # 注意：必须延迟到首次使用时再创建 Semaphore，否则会绑定到 __init__ 所在
        # 的事件循环（Executor 通常在 agent 初始化阶段构造，可能与 run 时事件循环不同）。
        self._llm_concurrency_limit: int = self._read_llm_concurrency_limit()
        self._llm_semaphore: asyncio.Semaphore | None = None

        # 节点显示名映射：由 skill 的 root 节点通过 display_names 类属性提供。
        # 缺省为空 dict，_display_name 将原样返回 plan_name。
        self._display_names: dict[str, str] = {}

    def _display_name(self, plan_name: str) -> str:
        """将内部 plan_name 转为界面上展示的名称，未映射时原样返回。"""
        return self._display_names.get(plan_name, plan_name)

    def validate(self, plan_code: str) -> list[str]:
        """校验规划代码。"""
        return self._validator.validate(plan_code)

    @property
    def node_artifacts(self) -> dict[str, dict[str, Any]]:
        """返回节点产物 holder，供外部构建产物摘要。"""
        return self._node_artifacts_holder

    def _merge_env_config_to_inputs(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """将环境配置合并到 inputs，供 skill_code 使用。

        skill_code（如 pipeline_init.py）需要通过 inputs 获取外部资源路径，
        例如 skill_root 用于定位 pptx-craft 等技能目录。
        这些路径由 Environment 在初始化时解析，需要注入到执行上下文中。
        """
        merged = dict(inputs)
        # 注入 skill_root（技能根目录）
        skill_root = self._env.skill_root
        if skill_root and "skill_root" not in merged:
            merged["skill_root"] = skill_root
            logger.debug(
                "[SkillTurboExecutor] merged skill_root from env: %s", skill_root
            )
        # [TEMP-EXTERNAL-SKILL] 注入 skill_name（外部 skill 目录名）
        skill_name = self._env.skill_name
        if skill_name and "skill_name" not in merged:
            merged["skill_name"] = skill_name
        # [TEMP-EXTERNAL-SKILL] 注入 skill_checksum（SHA256 校验值）
        skill_checksum = self._env.skill_checksum
        if skill_checksum and "skill_checksum" not in merged:
            merged["skill_checksum"] = skill_checksum
        # [TEMP-EXTERNAL-SKILL] 注入 skill_checksum_ok（框架层预计算的校验结果）
        if "skill_checksum_ok" not in merged:
            merged["skill_checksum_ok"] = self._env.skill_checksum_ok
        return merged

    def _build_tool_loader_context(
        self,
        inputs: dict[str, Any],
        *,
        request_id: str = "",
        channel_id: str = "",
    ):
        metadata = inputs.get("metadata")
        return self._env.build_tool_loader_context(
            request_id=request_id or str(inputs.get("request_id") or ""),
            session_id=str(inputs.get("conversation_id") or ""),
            channel_id=channel_id or str(inputs.get("channel_id") or ""),
            request_metadata=metadata if isinstance(metadata, dict) else None,
        )


    def _build_permission_rail(self) -> Any | None:
        """构建 PermissionInterruptRail；权限被禁用或构建失败时返回 None。

        复用 DeepAgent 的 ``build_permission_rail`` 工厂，配置取自
        ``environment.config['permissions']``，model 句柄取 ``model_client``。
        """
        try:
            from jiuwenswarm.agents.harness.common.rails.interrupt.interrupt_helpers import (
                build_permission_rail,
            )
        except Exception as exc:
            logger.warning(
                "[SkillTurboExecutor] build_permission_rail import failed: %s", exc
            )
            return None

        try:
            cfg = self._env.config if isinstance(self._env.config, dict) else {}
        except Exception:
            cfg = {}

        model = self._env.model_client
        model_name = None
        try:
            mc = getattr(model, "model_config", None)
            model_name = getattr(mc, "model_name", None) if mc is not None else None
        except Exception:
            model_name = None

        try:
            return build_permission_rail(cfg, llm=model, model_name=model_name)
        except Exception as exc:
            logger.warning(
                "[SkillTurboExecutor] build_permission_rail failed: %s", exc
            )
            return None

    def _build_ask_user_rail(self) -> Any | None:
        """挂载 StructuredAskUserRail，使节点内 ``ask_user`` 能走 HITL 打断。

        工具实例由 ``tools_loader`` 注册；此处只挂 rail 的 before_tool_call 逻辑，
        不调用 ``init()``（避免写入 DeepAgent.ability_manager）。
        """
        try:
            from jiuwenswarm.agents.harness.common.rails.ask_user_rail import (
                StructuredAskUserRail,
            )
        except Exception as exc:
            logger.warning(
                "[SkillTurboExecutor] StructuredAskUserRail import failed: %s", exc
            )
            return None

        language = "cn"
        try:
            cfg = self._env.config if isinstance(self._env.config, dict) else {}
            raw = str(cfg.get("language") or "cn").strip().lower()
            language = "cn" if raw in ("cn", "zh", "zh-cn", "zh_cn") else "en"
        except Exception:
            language = "cn"

        try:
            return StructuredAskUserRail(language=language)
        except Exception as exc:
            logger.warning(
                "[SkillTurboExecutor] StructuredAskUserRail create failed: %s", exc
            )
            return None

    async def _run_rail_hook(
        self,
        hook_name: str,
        ctx: AgentCallbackContext,
        *,
        skip_rails: set[Any] | None = None,
    ) -> None:
        """按 Rail 优先级执行 hook。

        关键：``AbortError``（PermissionInterruptRail HITL 中断）必须向上抛出，
        否则护栏会被悄悄吞掉，前端永远收不到审批请求。
        其它普通 ``Exception`` 仍按"单 Rail 失败不影响主链"打 warning 后继续。

        ``skip_rails``：需跳过的 rail 实例集合。replay-skip 路径用此参数
        跳过 ``PermissionInterruptRail``，但仍执行事件发射类 rail 的 ``before_tool_call``，
        以补发 ``chat.tool_call`` / ``chat.tool_update`` 事件。
        """
        for rail in self._rails:
            if skip_rails and rail in skip_rails:
                continue
            hook = getattr(rail, hook_name, None)
            if hook is None:
                continue
            try:
                logger.debug(
                    "[SkillTurboExecutor] Running Rail %s hook %s",
                    type(rail).__name__,
                    hook_name,
                )
                await hook(ctx)
            except AbortError:
                # HITL 中断：让上层 use_tool / PlanNode / Agent / Adapter 看到
                raise
            except Exception as e:
                logger.warning(
                    "[SkillTurboExecutor] Rail %s failed: %s - %s",
                    hook_name,
                    type(rail).__name__,
                    e,
                )

    @staticmethod
    def _build_tool_call_context(
        tool_name: str,
        kwargs: dict[str, Any],
    ) -> AgentCallbackContext:
        tool_call = ToolCall(
            name=tool_name,
            arguments=kwargs,
            id=f"tool_{uuid.uuid4().hex[:8]}",
        )
        return AgentCallbackContext(
            agent=None,
            session=_session_var.get(),
            inputs=ToolCallInputs(
                tool_name=tool_name,
                tool_call=tool_call,
                tool_args=kwargs,
            ),
            context=None,
            extra={},
        )

    @staticmethod
    def _build_model_call_context() -> AgentCallbackContext:
        return AgentCallbackContext(
            agent=None,
            session=_session_var.get(),
            inputs=ModelCallInputs(
                messages=[],
                tools=None,
                model_context=None,
            ),
            context=None,
            extra={},
        )

    @staticmethod
    def _serialize_usage_metadata(usage_metadata: Any) -> dict[str, Any]:
        if isinstance(usage_metadata, dict):
            return usage_metadata
        if hasattr(usage_metadata, "model_dump"):
            return usage_metadata.model_dump()
        if hasattr(usage_metadata, "dict"):
            return usage_metadata.dict()
        result: dict[str, Any] = {}
        for key in (
            "model_name",
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "input_cost",
            "output_cost",
            "total_cost",
        ):
            value = getattr(usage_metadata, key, None)
            if value is not None:
                result[key] = value
        return result

    async def _emit_llm_usage(
        self,
        session: Session | None,
        usage_metadata: Any,
        *,
        node_name: str | None = None,
        stream_source_id: str | None = None,
    ) -> None:
        if not usage_metadata or session is None:
            return
        usage_payload = self._serialize_usage_metadata(usage_metadata)
        if not usage_payload:
            return
        payload: dict[str, Any] = {"usage_metadata": usage_payload}
        if node_name is not None:
            payload["plan_name"] = node_name
        if stream_source_id is not None:
            payload[STREAM_SOURCE_ID_FIELD] = stream_source_id
        try:
            await session.write_stream(
                OutputSchema(
                    type="llm_usage",
                    index=0,
                    payload=payload,
                )
            )
        except Exception as e:
            logger.warning(
                "[SkillTurboExecutor] Failed to send llm_usage event: %s",
                e,
            )

    def _interface_log_session_id(self) -> str:
        conversation_id = self._execution_inputs.get("conversation_id", "")
        if conversation_id:
            return str(conversation_id)
        return self._session_id(_session_var.get())

    @staticmethod
    def _session_id(session: Session | None) -> str:
        if session is not None and callable(getattr(session, "get_session_id", None)):
            sid = session.get_session_id()
            return str(sid) if sid else ""
        return ""

    def has_tool(self, tool_name: str) -> bool:
        """
        检查工具是否存在。

        Args:
            tool_name: 工具名称

        Returns:
            bool: 工具是否存在
        """
        return self._env.has_tool(tool_name)

    def current_task_id(self) -> str | None:
        return self._current_task_id()

    def get_workspace_base_path(self) -> Path | None:
        value = self._execution_inputs.get("effective_project_dir")
        if value:
            try:
                return Path(str(value)).expanduser().resolve()
            except Exception:
                logger.debug("[SkillTurboExecutor] invalid effective_project_dir for artifact detection: %s", value)
        try:
            from jiuwenswarm.common.utils import get_agent_workspace_dir
            return get_agent_workspace_dir()
        except TypeError:
            return None
        except Exception as e:
            logger.warning("[SkillTurboExecutor] 获取agent工作空间目录失败，将返回None: %s", e)
            return None

    # ──────────────────────── Resume 支持 ────────────────────────

    def set_pending_resume(
        self,
        *,
        expected_tool_call_id: str,
        user_input: Any,
    ) -> None:
        """adapter 在 resume 路径开始执行前调用，注入用户审批回复。

        ``use_tool`` 重放到与 ``expected_tool_call_id`` 相同的 tool_call_id 时，
        将一次性把 ``user_input`` 注入 ctx.extra[RESUME_USER_INPUT_KEY]，
        让 ``PermissionInterruptRail`` 完成 approve / reject 决策。
        其它 tool_call 不带 resume 输入（首次行为）。
        """
        if not expected_tool_call_id:
            return
        self._pending_resume = {
            "expected_tool_call_id": expected_tool_call_id,
            "user_input": user_input,
        }

    def _consume_pending_resume_input(self, current_tool_call_id: str) -> Any | None:
        pending = self._pending_resume
        if not pending:
            return None
        if pending.get("expected_tool_call_id") != current_tool_call_id:
            return None
        self._pending_resume = None
        return pending.get("user_input")

    def _next_tool_call_id(self, tool_name: str, kwargs: dict[str, Any]) -> str:
        """生成确定性 tool_call_id：基于 (tool_name, canonical_args, call_index) 哈希。

        - canonical_args：``json.dumps(sort_keys, default=str)`` 后取 sha1[:8]
        - call_index：本次执行内同 (name, args) 的第几次调用（从 0 起算）

        重放时只要 plan_code+inputs 一致，相同顺序的同名同参调用必然得到同样的 id；
        与 ``PermissionInterruptRail`` 的 ``user_inputs[tool_call_id]`` 对应即可命中。
        """
        try:
            args_canonical = json.dumps(kwargs, sort_keys=True, default=str)
        except Exception:
            args_canonical = repr(sorted(kwargs.items()))
        args_hash = hashlib.sha1(args_canonical.encode("utf-8")).hexdigest()[:8]
        key = f"{tool_name}|{args_hash}"
        idx = self._tool_call_counter.get(key, 0)
        self._tool_call_counter[key] = idx + 1
        return f"skill_turbo-tc-{tool_name}-{args_hash}-{idx}"

    async def use_tool(self, tool_name: str, **kwargs: Any) -> Any:
        """
        调用工具（带 PermissionInterruptRail 护栏）。

        流程：
        1. 生成确定性 tool_call_id（重放时与中断时一致）。
        2. 若处于 resume 模式且 id 命中，注入用户审批载荷到 ctx.extra。
        3. ``before_tool_call`` 跑 rail 链；rail 抛出 ``AbortError`` 表示需要 HITL。
           捕获后向上抛出，由 harness 转 HITL chunks；resume 时经 stream_event_rail
           桥接 ``RESUME_USER_INPUT_KEY`` → ``set_pending_resume``。
        4. rail 通过 ``ctx.extra['_skip_tool']`` 标记 reject 时，直接返回 rail 注入的
           tool_result，不真正执行工具。
        5. 否则执行工具并跑 ``after_tool_call``。
        """
        session = _session_var.get()
        tool_call_id = self._next_tool_call_id(tool_name, kwargs)
        resume_input = self._consume_pending_resume_input(tool_call_id)

        # ─── 工具调用 trace 上下文 ───
        # begin_tool_trace_event 在本次 use_tool 调用范围内分配独立 event_id，
        # tool_call_request 与 tool_call_output 共享同一 event_id，便于在
        # full.log 通过 event_id grep 还原一次工具调用的完整入参与出参。
        trace_token = begin_tool_trace_event()
        trace_session_id = self._tool_trace_session_id()
        trace_request_id = _request_id_var.get()
        log_tool_call_input(
            session_id=trace_session_id,
            request_id=trace_request_id,
            iteration=None,
            agent="skill_turbo",
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            args=kwargs,
        )
        trace_start = time.monotonic()
        trace_status: str = "ok"
        trace_result: Any = None
        trace_error: str | None = None

        def _emit_trace_output() -> None:
            duration_ms = (time.monotonic() - trace_start) * 1000.0
            try:
                log_tool_call_output(
                    session_id=trace_session_id,
                    request_id=trace_request_id,
                    iteration=None,
                    agent="skill_turbo",
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                    status=trace_status,
                    duration_ms=duration_ms,
                    result=trace_result,
                    error=trace_error,
                )
            except Exception:
                logger.debug(
                    "[SkillTurboExecutor] log_tool_call_output failed name=%s tcid=%s",
                    tool_name,
                    tool_call_id,
                    exc_info=True,
                )

        try:
            ctx = build_tool_ctx(
                session=session,
                tool_name=tool_name,
                tool_args=kwargs,
                tool_call_id=tool_call_id,
                resume_user_input=resume_input,
            )

            # skill_turbo 外层统一审批：审批已在 deepagent 层对 skill_turbo 工具
            # 整体完成（config: skill_turbo: ask），内部工具调用不再逐个审批，
            # 始终跳过 PermissionInterruptRail，直接放行。
            # 仍执行事件发射类 rail（stream_event_rail 等）的 before_tool_call，
            # 以补发 chat.tool_call / chat.tool_update 事件，避免前端工具结果
            # 凭空出现、缺调用上下文。
            await self._run_rail_hook(
                "before_tool_call",
                ctx,
                skip_rails=(
                    {self._permission_rail}
                    if self._permission_rail is not None
                    else None
                ),
            )

            # rail 通过 _skip_tool 标记 reject，已经在 ctx.inputs.tool_result 写入结果
            if ctx.extra.get("_skip_tool"):
                logger.debug(
                    "[SkillTurboExecutor] use_tool skipped by rail name=%s tcid=%s",
                    tool_name,
                    tool_call_id,
                )
                trace_status = "skipped"
                trace_result = ctx.inputs.tool_result
                return ctx.inputs.tool_result

            # 获取工具函数
            tool_fn = self._env.get_tool_function(tool_name)
            if tool_fn is None:
                trace_status = "error"
                trace_error = f"未知工具: {tool_name}"
                raise ValueError(f"未知工具: {tool_name}")

            logger.debug(
                "[SkillTurboExecutor] use_tool name=%s tcid=%s kwargs_keys=%s",
                tool_name,
                tool_call_id,
                list(kwargs.keys()),
            )


            try:
                result = await tool_fn(**kwargs)
                logger.debug(
                    "[SkillTurboExecutor] use_tool done name=%s result_type=%s",
                    tool_name,
                    type(result).__name__,
                )

                ctx.inputs.tool_result = result
                await self._run_rail_hook("after_tool_call", ctx)
                trace_status = "ok"
                trace_result = result
                return result
            except Exception as e:
                if isinstance(e, AbortError):
                    # HITL 中断：透传，不作为普通工具错误处理
                    trace_status = "interrupted"
                    trace_error = repr(e)
                    raise
                logger.exception(
                    "[SkillTurboExecutor] use_tool failed name=%s err=%r", tool_name, e
                )
                ctx.inputs.tool_result = f"Error: {e}"
                await self._run_rail_hook("after_tool_call", ctx)
                trace_status = "error"
                trace_error = repr(e)
                raise
        finally:
            _emit_trace_output()
            end_tool_trace_event(trace_token)

    def _tool_trace_session_id(self) -> str:
        """工具 trace 用的 session_id，优先复用 LLM trace 的 ContextVar，保持口径一致。"""
        try:
            from jiuwenswarm.server.runtime.agent_adapter.interface_deep import _LLM_TRACE_SESSION_ID

            sid = _LLM_TRACE_SESSION_ID.get()
            if sid:
                return str(sid)
        except Exception:
            # interface_deep 不可用时回退到本模块自有 session_id 解析。
            pass
        return self._interface_log_session_id()

    def _read_llm_concurrency_limit(self) -> int:
        """从 environment.config 读取 LLM 并发上限。

        约定 key 为 ``llm_concurrency_limit``，<=0 或缺省/非法值表示不限制。
        """
        try:
            cfg = self._env.config if isinstance(self._env.config, dict) else {}
        except Exception:
            cfg = {}
        raw = cfg.get("llm_concurrency_limit", 0)
        try:
            limit = int(raw)
        except (TypeError, ValueError):
            logger.warning(
                "[SkillTurboExecutor] invalid llm_concurrency_limit=%r, fallback to 0 (unlimited)",
                raw,
            )
            return 0
        return limit if limit > 0 else 0

    def _llm_concurrency_guard(self) -> AbstractAsyncContextManager[None]:
        """返回 LLM 并发限制的异步上下文管理器。

        - 当未配置（limit<=0）时，返回 no-op 上下文，零开销。
        - 首次进入时按当前事件循环懒初始化 ``asyncio.Semaphore``，避免与
          Executor 构造时所在事件循环不一致导致的 ``RuntimeError``。
        - 当槽位已满需要排队时，会打 INFO 日志输出当前队列长度和等待耗时，
          便于在生产环境观测排队拥堵情况。
        """
        if self._llm_concurrency_limit <= 0:
            return contextlib.nullcontext()
        if self._llm_semaphore is None:
            self._llm_semaphore = asyncio.Semaphore(self._llm_concurrency_limit)
            logger.debug(
                "[SkillTurboExecutor] llm semaphore initialized limit=%d",
                self._llm_concurrency_limit,
            )
        return self._acquire_llm_slot(self._llm_semaphore)

    @contextlib.asynccontextmanager
    async def _acquire_llm_slot(
        self, sem: asyncio.Semaphore
    ) -> AsyncIterator[None]:
        """带排队拥堵日志的 Semaphore 包装。

        - 抢占前若 Semaphore 已满（``locked()`` 为 True），打 INFO 日志记录入队，
          其中 ``queued`` 反映当前已挂起的等待者数量（含本次）。
        - 真正拿到槽位后，如果等过超过 ``_LLM_QUEUE_WAIT_LOG_THRESHOLD_MS`` 毫秒，
          额外打一条 INFO 报告排队耗时；否则仅 DEBUG。
        - 离开 with 块时释放槽位，调用方与原先 ``async with sem`` 行为一致。
        """
        queued = sem.locked()
        if queued:
            # asyncio.Semaphore 内部使用 _waiters 双端队列存放挂起者；
            # 这里只读不改，作为可观测指标使用，缺失时回退到 -1。
            waiters = getattr(sem, "_waiters", None)
            waiting_count = (len(waiters) + 1) if waiters is not None else -1
            logger.info(
                "[SkillTurboExecutor] llm slot queued limit=%d waiting=%d",
                self._llm_concurrency_limit,
                waiting_count,
            )
        wait_start = time.monotonic() if queued else 0.0
        await sem.acquire()
        try:
            if queued:
                waited_ms = (time.monotonic() - wait_start) * 1000.0
                if waited_ms >= _LLM_QUEUE_WAIT_LOG_THRESHOLD_MS:
                    logger.info(
                        "[SkillTurboExecutor] llm slot acquired after wait=%.1fms",
                        waited_ms,
                    )
                else:
                    logger.debug(
                        "[SkillTurboExecutor] llm slot acquired after wait=%.1fms",
                        waited_ms,
                    )
            yield
        finally:
            sem.release()

    @staticmethod
    def _gen_stream_source_id(node_name: str) -> str:
        """生成并发场景下的 stream_source_id。

        前缀 ``skill_turbo:`` 用于与 subagent (``sess_xxx_subagent_…``) 区分；
        ``node_name`` 提升可读性；4 字节 hex 保证同一 node_name 多次并发不碰撞。
        """
        return f"skill_turbo:{node_name}:{secrets.token_hex(4)}"

    async def call_llm(
        self,
        prompt: str,
        system_prompt: str = "",
        *,
        node_name: str = "unknown",
        concurrent: bool = False,
    ) -> str:
        """
        调用 LLM（使用Rail机制）。

        Args:
            prompt: 用户提示
            system_prompt: 系统提示
            node_name: 节点名称（用于日志与 source id 可读性）
            concurrent: 是否处于并发上下文中。True 时 Executor 自动生成
                stream_source_id，并注入到本次产生的 llm_reasoning / llm_usage
                事件，方便前端按调用分桶。

        Returns:
            LLM 响应文本

        Raises:
            RuntimeError: 模型客户端未配置
        """
        ctx = self._build_model_call_context()
        session = ctx.session
        # 获取模型客户端
        client = self._env.model_client
        if client is None:
            raise RuntimeError("model_client 未配置")

        # 仅并发时生成 id；串行场景保持 None，行为不变
        source_id = self._gen_stream_source_id(node_name) if concurrent else None

        logger.debug(
            "[SkillTurboExecutor] call_llm prompt_len=%s system_len=%s node=%s source_id=%s",
            len(prompt),
            len(system_prompt),
            node_name,
            source_id,
        )

        # 记录节点执行

        # 构建消息列表
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        try:
            await self._run_rail_hook('before_model_call', ctx)

            # 使用 Model.invoke() 调用 LLM（受实例级 Semaphore 限流保护）
            async with self._llm_concurrency_guard():
                response = await client.invoke(messages)

            # llm_usage 事件注入 source_id
            await self._emit_llm_usage(
                session,
                getattr(response, "usage_metadata", None),
                node_name=node_name,
                stream_source_id=source_id,
            )

            # AssistantMessage 有两个属性：
            # - content: 普通文本内容
            # - reasoning_content: 推理过程内容（与 DeepAgent 保持一致）

            # 处理 reasoning_content（推理过程）并注入 source_id
            reasoning_content = getattr(response, "reasoning_content", None)
            if reasoning_content and session:
                payload: dict[str, Any] = {
                    "content": str(reasoning_content),
                    "plan_name": self._display_name(node_name),
                }
                if source_id is not None:
                    payload[STREAM_SOURCE_ID_FIELD] = source_id
                try:
                    await session.write_stream(
                        OutputSchema(type="llm_reasoning", index=0, payload=payload)
                    )
                except Exception as e:
                    logger.warning(
                        "[SkillTurboExecutor] Failed to send llm_reasoning event: %s",
                        e,
                    )

            # AssistantMessage.content 是响应文本
            result = response.content

            # 处理正文内容并注入 source_id（与 reasoning 保持一致）
            if result and session:
                output_payload: dict[str, Any] = {
                    "content": str(result),
                    "plan_name": self._display_name(node_name),
                }
                if source_id is not None:
                    output_payload[STREAM_SOURCE_ID_FIELD] = source_id
                try:
                    await session.write_stream(
                        OutputSchema(type="llm_output", index=0, payload=output_payload)
                    )
                except Exception as e:
                    logger.warning(
                        "[SkillTurboExecutor] Failed to send llm_output event: %s",
                        e,
                    )

            # 设置response（供after回调使用）
            ctx.inputs.response = result

            return result
        except Exception as e:
            ctx.exception = e
            await self._run_rail_hook('on_model_exception', ctx)
            raise
        finally:
            await self._run_rail_hook('after_model_call', ctx)

    async def stream_llm(
        self,
        prompt: str,
        system_prompt: str = "",
        node_name: str = "unknown",
        concurrent: bool = False,
    ) -> AsyncIterator[str]:
        """
        流式调用 LLM（使用Rail机制）。
        
        注意：reasoning_content 会自动处理并发送事件，业务代码无需关心。

        Args:
            prompt: 用户提示
            system_prompt: 系统提示
            node_name: 节点名称（用于标识来源）
            concurrent: 是否处于并发上下文中。True 时 Executor 自动生成
                stream_source_id，并注入到本次产生的 llm_reasoning / llm_usage
                事件，方便前端按调用分桶。

        Yields:
            str: 流式文本片段（普通文本内容）
            
        内部机制：
            - reasoning 事件会通过 session stream 实时发送
            - 业务代码只看到普通文本，无需关心 reasoning
        """
        ctx = self._build_model_call_context()
        session = ctx.session
        # 获取模型客户端
        client = self._env.model_client
        if client is None:
            raise RuntimeError("model_client 未配置")

        # 仅并发时生成 id；串行场景保持 None，行为不变
        source_id = self._gen_stream_source_id(node_name) if concurrent else None

        logger.debug(
            "[SkillTurboExecutor] stream_llm prompt_len=%s system_len=%s node=%s source_id=%s",
            len(prompt),
            len(system_prompt),
            node_name,
            source_id,
        )

        # 记录节点执行

        # 构建消息列表
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        accumulated_message = ""
        try:
            await self._run_rail_hook('before_model_call', ctx)
            
            # 使用 Model.stream() 流式调用 LLM
            # 流式调用整个生命周期都占用一个 LLM "槽位"，因此用 Semaphore 包裹整个流。
            async with self._llm_concurrency_guard():
                async for chunk in client.stream(messages):
                    # usage_metadata 通常只在最后一个 chunk 有值，避免对每个 chunk 都调用
                    chunk_usage = getattr(chunk, "usage_metadata", None)
                    if chunk_usage:
                        await self._emit_llm_usage(
                            session,
                            chunk_usage,
                            node_name=node_name,
                            stream_source_id=source_id,
                        )
                    # AssistantMessageChunk 有两个属性：
                    # - content: 普通文本内容
                    # - reasoning_content: 推理过程内容
                    
                    # ──────────────────────── 自动处理 reasoning_content ────────────────────────
                    # 框架层面自动发送 reasoning 事件，业务代码无需关心
                    reasoning_content = getattr(chunk, "reasoning_content", None)
                    if reasoning_content:
                        if session:
                            reasoning_payload: dict[str, Any] = {
                                "content": str(reasoning_content),
                                "plan_name": self._display_name(node_name),
                            }
                            if source_id is not None:
                                reasoning_payload[STREAM_SOURCE_ID_FIELD] = source_id
                            try:
                                await session.write_stream(
                                    OutputSchema(
                                        type="llm_reasoning",
                                        index=0,
                                        payload=reasoning_payload,
                                    )
                                )
                            except Exception as e:
                                logger.warning(
                                    "[SkillTurboExecutor] Failed to send stream llm_reasoning event: %s",
                                    e,
                                )
                    
                    # ──────────────────────── 处理普通文本内容 ────────────────────────
                    text_chunk = chunk.content
                    if text_chunk:
                        # 累积消息（供after回调使用）
                        accumulated_message += text_chunk

                        # 通过 session stream 发送正文 delta（与 reasoning 保持一致）
                        if session:
                            output_payload: dict[str, Any] = {
                                "content": str(text_chunk),
                                "plan_name": self._display_name(node_name),
                            }
                            if source_id is not None:
                                output_payload[STREAM_SOURCE_ID_FIELD] = source_id
                            try:
                                await session.write_stream(
                                    OutputSchema(
                                        type="llm_output",
                                        index=0,
                                        payload=output_payload,
                                    )
                                )
                            except Exception as e:
                                logger.warning(
                                    "[SkillTurboExecutor] Failed to send stream llm_output event: %s",
                                    e,
                                )

                        # 返回普通文本（业务代码只看到普通文本）
                        yield text_chunk
            
            # 设置response（供after回调使用）
            ctx.inputs.response = accumulated_message
            
        except Exception as e:
            ctx.exception = e
            await self._run_rail_hook('on_model_exception', ctx)
            raise
        finally:
            await self._run_rail_hook('after_model_call', ctx)

    async def fallback(
        self,
        node: PlanNode,
        inputs: dict[str, Any],
        error: Exception,
    ) -> Any:
        """
        节点执行失败时委托 fallback_handler 兜底。

        Args:
            node: 失败的节点
            inputs: 输入参数
            error: 异常对象

        Returns:
            Fallback 执行结果（status="degraded"）

        Raises:
            FallbackLimitExceededError: Fallback 次数超过限制
            RuntimeError: Fallback handler 未初始化
        """
        self._check_fallback_limit(error)
        self._record_fallback_call(node, "fallback")

        handler = self._env.fallback_handler
        if handler is None:
            logger.error(
                "[SkillTurboExecutor] fallback_handler missing, re-raising error plan_name=%s error=%s",
                node.plan_name,
                error,
            )
            raise error

        logger.warning(
            "[SkillTurboExecutor] node fallback via handler plan_name=%s error=%s fallback_count=%d",
            node.plan_name,
            error,
            self._fallback_count,
        )
        return await handler.fallback(
            node_name=node.plan_name,
            instruction=node.instruction or "",
            inputs=inputs,
            error=error,
            parent_session=_session_var.get(),
        )

    async def fallback_stream(
        self,
        node: PlanNode,
        inputs: dict[str, Any],
        error: Exception,
    ) -> AsyncIterator[dict[str, Any]]:
        """
        节点执行失败时委托 fallback_handler 兜底（流式版本）。

        Args:
            node: 失败的节点
            inputs: 输入参数
            error: 异常对象

        Yields:
            dict[str, Any]: Fallback Agent 的流式输出chunk，包含fallback标识事件和内容chunk

        Raises:
            FallbackLimitExceededError: Fallback 次数超过限制
            RuntimeError: Fallback handler 未初始化
        """
        self._check_fallback_limit(error)
        self._record_fallback_call(node, "fallback_stream")

        handler = self._env.fallback_handler
        if handler is None:
            logger.error(
                "[SkillTurboExecutor] fallback_handler missing, re-raising error plan_name=%s error=%s",
                node.plan_name,
                error,
            )
            raise error

        logger.warning(
            "[SkillTurboExecutor] node fallback_stream via handler plan_name=%s error=%s fallback_count=%d",
            node.plan_name,
            error,
            self._fallback_count,
        )
        async for chunk in handler.fallback_stream(
            node_name=node.plan_name,
            instruction=node.instruction or "",
            inputs=inputs,
            error=error,
            parent_session=_session_var.get(),
        ):
            yield chunk




    def _check_fallback_limit(self, error: Exception) -> None:
        """检查 fallback 前置条件：是否启用、是否超限。不修改计数。"""
        if not self._config.enable_fallback:
            logger.error(
                "[SkillTurboExecutor] fallback disabled, re-raising error: %s",
                error,
            )
            raise error

        if self._fallback_count >= self._config.max_fallback_count:
            logger.error(
                "[SkillTurboExecutor] fallback limit exceeded: %d/%d",
                self._fallback_count,
                self._config.max_fallback_count,
            )
            raise FallbackLimitExceededError(
                f"Fallback 次数超过限制: {self._fallback_count}/{self._config.max_fallback_count}。最后错误: {error}"
            ) from error

    def _record_fallback_call(self, node: PlanNode, trace_prefix: str) -> None:
        self._fallback_count += 1

    def _current_task_id(self) -> str | None:
        """返回当前正在执行的二层节点 task_id（供外部消费）。

        在线执行路径下 task 事件机器已废弃，本方法恒返回 None；
        保留实例属性 fallback 以兼容未来可能的轻量 task 追踪需求。
        """
        holder_task_id = self._current_task_id_holder.get("task_id")
        if holder_task_id:
            return holder_task_id
        for task_id, state in self._task_states_holder.items():
            if state.get("status") == "in_progress":
                return task_id
        return None


    def _bind_node_callbacks(self, root: PlanNode) -> None:
        root.set_runtime_callbacks(
            has_tool=self.has_tool,
            use_tool=self.use_tool,
            call_llm=self.call_llm,
            stream_llm=self.stream_llm,
            fallback=self.fallback,
            fallback_stream=self.fallback_stream,
            extract_json=extract_llm_json,
            log=self._log_from_node,
        )

    @staticmethod
    def _log_from_node(
        node: PlanNode,
        level: str,
        message: str,
        args: tuple[Any, ...],
    ) -> None:
        """输出节点受控日志。"""
        log_level = level.lower()
        if log_level not in {"debug", "info", "warning", "error"}:
            log_level = "info"
        log_fn = getattr(logger, log_level)
        try:
            message_text = message % args if args else message
        except Exception:
            message_text = f"{message} args={args!r}"
        session = _session_var.get()
        session_id = getattr(session, "id", "") if session is not None else ""
        log_fn(
            "[PlanNodeLog] request_id=%s session_id=%s node=%s message=%s",
            _request_id_var.get(),
            session_id,
            node.plan_name,
            message_text,
        )

