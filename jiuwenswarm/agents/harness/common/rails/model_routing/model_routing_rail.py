"""model_routing.rail — ModelRoutingRail."""
from __future__ import annotations
from dataclasses import asdict
from datetime import datetime
import re
import time
from typing import Any, Callable, Optional
from openjiuwen.core.context_engine import TiktokenCounter
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.base import DeepAgentRail
from jiuwenswarm.common.utils import logger
from .capability import ModelCapability, build_capability_table_from_config, _capability_rank
from .classifier import LLMClassifier, _task_score
from .stats import _ModelUsageStats, get_stats_store, reset_stats_store_for_test
from .privacy import _check_privacy
from .routing import _decide_and_select
from .types import PriorModelCall, TaskAnalysis, RoutingDecision, _extract_prompt_text, _message_text, _agent_model_name, _extract_agent_info, _get_session_id, _new_trace_id, _new_span_id

class ModelRoutingRail(DeepAgentRail):
    """模型路由 Rail（穿刺版）—— 产出推荐模型 + 任务分析 + token 统计；apply_routing 控制是否真切换。"""

    priority: int = 95  # 早于 TaskPlanningRail(90)，确保路由先生效

    def __init__(
        self,
        capability_table: Optional[list[ModelCapability]] = None,
        *,
        classifier: Optional[Classifier] = None,
        stats: Optional[_ModelUsageStats] = None,
        stats_path: Optional[str] = None,
        apply_routing: bool = False,
        privacy_check: bool = False,
    ) -> None:
        super().__init__()
        self._capability_table: list[ModelCapability] = capability_table or []
        self._classifier: Classifier = classifier or LLMClassifier()
        self._call_history: list[PriorModelCall] = []
        self._token_counter = TiktokenCounter()
        self._stats: _ModelUsageStats = stats or get_stats_store(stats_path)
        self._apply_routing: bool = apply_routing
        self._trace_id: str = _new_trace_id()
        self._call_start: str = ""
        self._privacy_check: bool = privacy_check
        self._load_persisted_table(persist=False)

    # ---- 生命周期钩子 ---- #
    def _load_persisted_table(self, *, persist: bool = True) -> None:
        """加载持久化模型表（含 token_used）合并进能力表；persist=True 时回写整表。

        启动时调 persist=False（只读合并，不写文件——避免 env 未解析的默认模型覆盖真实统计）；
        reload 时调 persist=True（合并 + 回写整表，sync config 模型 + 保留 token_used）。
        """
        try:
            models = self._stats.snapshot().get("models", {})
        except Exception as exc:
            logger.debug("[ModelRouting] stats snapshot failed: %s", exc)
            return
        for cap in self._capability_table:
            key = cap.model_id or cap.model_name
            entry = models.get(key) or models.get(cap.model_name)
            if not isinstance(entry, dict):
                continue
            tu = entry.get("token_used") if isinstance(entry.get("token_used"), dict) else entry
            cap.token_used = {
                "input_tokens": int(tu.get("input_tokens", 0) or 0),
                "output_tokens": int(tu.get("output_tokens", 0) or 0),
                "call_count": int(tu.get("call_count", 0) or 0),
                "last_used": tu.get("last_used"),
            }
        # 回写整表（仅在 reload 时；启动 persist=False 不写，避免默认值覆盖 + 保留文件里已有但不在当前 caps 的模型统计）
        if not persist:
            return
        try:
            self._stats.persist_table(self._capability_table)
        except Exception as exc:
            logger.debug("[ModelRouting] persist_table failed: %s", exc)

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        """invoke 开始时铸造新 trace_id，本次 invoke 内所有 prior-call span 共享。"""
        self._trace_id = _new_trace_id()
        self._call_history = []  # 新 invoke 重置前置调用链

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        self._call_start = datetime.now().isoformat()
        try:
            messages = getattr(getattr(ctx, "inputs", None), "messages", None) or []
            prompt_text = _extract_prompt_text(messages)
            input_tokens = self._count_tokens(messages)
            agent_info = _extract_agent_info(ctx)
            session_id = _get_session_id(ctx)

            # --- 安全检查 1：单模型跳过 ---
            #    单模型时无法路由（没得选），直接用唯一模型。
            #    隐私检查不执行——单模型场景下 rail 不负责隐私过滤，用户自行承担。
            if len(self._capability_table) <= 1:
                single = self._capability_table[0] if self._capability_table else None
                privacy_note = " (privacy check NOT performed, rail not responsible)" if self._privacy_check else ""
                self._emit_decision(
                    ctx,
                    recommended_cap=single,
                    category="skipped",
                    difficulty="skipped",
                    input_tokens=input_tokens,
                    agent_info=agent_info,
                    reason="single model available, routing skipped" + privacy_note,
                    privacy_hit=False,
                )
                logger.info(
                    "[ModelRouting] skipped (single model)%s; in_tok=%d",
                    privacy_note, input_tokens,
                )
                return

            # --- 安全检查 2：隐私命中优先可信(is_trusted)模型 ---
            #    privacy_check=false（默认）时跳过隐私检查，直接进正常路由。
            if not self._privacy_check:
                pass  # 跳过隐私检查，进正常路由
            elif _check_privacy(prompt_text):
                logger.info(
                    "[ModelRouting] PRIVACY-HIT: session=%s caps=%d trusted=%d prompt_preview=%r",
                    session_id, len(self._capability_table),
                    sum(1 for c in self._capability_table if c.is_trusted),
                    (prompt_text or "")[:40],
                )
                # 按能力排序取最强 trusted
                trusted_candidates = [c for c in self._capability_table if c.is_trusted]
                trusted = max(trusted_candidates, key=_capability_rank) if trusted_candidates else None
                if trusted is not None:
                    self._emit_decision(
                        ctx,
                        recommended_cap=trusted,
                        category="privacy",
                        difficulty="unknown",
                        input_tokens=input_tokens,
                        agent_info=agent_info,
                        reasoning="privacy hit -> trusted model (is_trusted)",
                        privacy_hit=True,
                    )
                    logger.info(
                        "[ModelRouting] privacy hit -> trusted model %s",
                        trusted.model_name,
                    )
                    return
                # 无可信模型：直接取消本次请求，给前端留消息（不做 2 分支确认）
                logger.warning(
                    "[ModelRouting] PRIVACY-CANCEL (no trusted): session=%s -> cancel + leave message",
                    session_id,
                )
                ctx.request_force_finish({"output": "⚠️ 检测到隐私/敏感信息，且无可信(is_trusted)模型，已取消本次请求。请配置 is_trusted 模型后再试。", "result_type": "answer"})
                return

            # --- 正常路由（含图请求会在 _decide_and_select 里限到 model_type=="vision" 候选）---
            category, difficulty, cls_reasoning = await self._classifier(prompt_text, ctx)
            target = _task_score(category, difficulty)
            recommended_cap, reason = _decide_and_select(
                category, difficulty, self._capability_table, ctx
            )
            self._emit_decision(
                ctx,
                recommended_cap=recommended_cap,
                category=category,
                difficulty=difficulty,
                input_tokens=input_tokens,
                agent_info=agent_info,
                reasoning=f"{cls_reasoning}; {reason}",
                privacy_hit=False,
            )
            rec_id = (
                recommended_cap.model_id or recommended_cap.model_name
                if recommended_cap
                else None
            )
            logger.info(
                "[ModelRouting] classifier: [%s,%s] score=%d in_tok=%d privacy=%s -> recommend=%s",
                category,
                difficulty,
                target,
                input_tokens,
                privacy_hit,
                rec_id,
            )
        except Exception as exc:
            logger.warning("[ModelRouting] before_model_call failed: %s", exc, exc_info=True)

    async def after_model_call(self, ctx: AgentCallbackContext) -> None:
        try:
            response = getattr(getattr(ctx, "inputs", None), "response", None)
            usage = getattr(response, "usage_metadata", None)
            input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
            output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
            model_name = _agent_model_name(ctx) or "unknown"
            end_time = datetime.now().isoformat()
            # 1) 累积到本次 invoke 的前置调用链（完整 OTel span）
            self._call_history.append(
                PriorModelCall(
                    model=model_name,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    iteration=len(self._call_history),
                    trace_id=self._trace_id,
                    start_time=self._call_start or end_time,
                    end_time=end_time,
                )
            )
            # 2) 持久化 per-model token 用量（按 client_id；优先用实际切到的 cap，回退 model_name 查）
            used_cap = ctx.extra.get("_model_routing_used_cap") if isinstance(ctx.extra, dict) else None
            cap = used_cap or next((c for c in self._capability_table if c.model_name == model_name), None)
            mid = (cap.model_id if cap and cap.model_id else model_name) or model_name
            self._stats.record(
                mid,
                model_name,
                input_tokens,
                output_tokens,
                model_provider=cap.model_provider if cap else "unknown",
                model_group=cap.model_group if cap else "unknown",
                is_trusted=cap.is_trusted if cap else False,
            )
        except Exception as exc:
            logger.debug("[ModelRouting] after_model_call failed: %s", exc)


    # ---- 内部 ---- #
    def _emit_decision(
        self,
        ctx: AgentCallbackContext,
        *,
        recommended_cap: Optional[ModelCapability],
        category: str,
        difficulty: str,
        input_tokens: int,
        agent_info: dict[str, Any],
        reasoning: str,
        privacy_hit: bool,
    ) -> None:
        recommended_model_id = (
            recommended_cap.model_id or recommended_cap.model_name
            if recommended_cap
            else None
        )
        # 真切换：apply_routing 打开且能力表带 Model 对象时，切换 agent 当前模型
        # （仿 openjiuwen TaskPlanningRail：set_llm + 同步 config.model_name）
        if (
            self._apply_routing
            and recommended_cap is not None
            and recommended_cap.model is not None
        ):
            try:
                ctx.agent.set_llm(recommended_cap.model)
                # 记下实际切到的 cap，供 after_model_call 按 client_id 记 token（同模型多 API 场景）
                if isinstance(ctx.extra, dict):
                    ctx.extra["_model_routing_used_cap"] = recommended_cap
                mname = (
                    getattr(getattr(recommended_cap.model, "model_config", None), "model_name", None)
                    or recommended_cap.model_name
                )
                if mname:
                    cfg = getattr(ctx.agent, "_config", None) or getattr(ctx.agent, "config", None)
                    if cfg is not None:
                        try:
                            setattr(cfg, "model_name", mname)
                            # 同步 api_base/api_key/client_provider + model_config，
                            # 否则 railed model call 用路由后的 model_name 但 model_client_config
                            # 还是前端的 → 打到前端端点 → 返回前端模型名（set_llm 只设 _llm 不够）
                            setattr(cfg, "model_client_config", recommended_cap.model.model_client_config)
                            setattr(cfg, "model_config_obj", recommended_cap.model.model_config)
                        except Exception:
                            pass
                    # 补 set_llm 不更新 agent.model_name 的缺口：set_llm 只装 _llm，
                    # 不碰 model_name；这里同步成路由后的名字，让 RuntimePromptRail 的
                    # "当前模型"段读到实时模型（而非 runtime_state 的旧值）
                    try:
                        if hasattr(ctx.agent, "model_name"):
                            setattr(ctx.agent, "model_name", mname)
                    except Exception:
                        pass
                logger.info("[ModelRouting] applied set_llm -> %s", mname or "unknown")
            except Exception as exc:
                logger.warning("[ModelRouting] set_llm failed: %s", exc)
        decision = RoutingDecision(
            recommended_model_id=recommended_model_id,
            analysis=TaskAnalysis(
                category=category,
                difficulty=difficulty,
                predicted_input_tokens=input_tokens,
                agent_info=agent_info,
            ),
            reasoning=reasoning,
            prior_calls_otel=[c.to_otel_span() for c in self._call_history],
            model_usage_stats=self._stats.snapshot(),
            privacy_hit=privacy_hit,
        )
        ctx.extra["model_routing_decision"] = asdict(decision)

    def _count_tokens(self, messages: list[Any]) -> int:
        """独立计算 prompt token（tiktoken），不依赖模型上报。"""
        try:
            return int(self._token_counter.count_messages(messages))
        except Exception:
            text = "\n".join(_message_text(m) for m in messages)
            try:
                return int(self._token_counter.count(text))
            except Exception:
                return max(0, len(text) // 4)

    def reload_capability_table(
        self,
        config: dict[str, Any] | None,
        *,
        model_builder: Optional[Callable[[dict, dict], Any]] = None,
    ) -> None:
        """从 config 重新加载能力表（配置/env 更新时调用，无需重建整个 rail）。

        启动时由 ``_build_model_routing_rail`` 构建；热重载若走整 rail 重建则自动刷新，
        否则可显式调本方法。model_builder 传 ``JiuWenSwarmDeepAdapter._build_model_from_entry``。
        """
        self._capability_table = build_capability_table_from_config(
            config, model_builder=model_builder
        )
        logger.info(
            "[ModelRouting] capability table reloaded: %d models",
            len(self._capability_table),
        )
        self._load_persisted_table()


