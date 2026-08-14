"""DoloresAgent AgentLoop — pi 风格极简 agent 内核（fork 内自写，不继承 BaseAgent）。

设计见 openspec/changes/dolores-agent-loop/。
数据流：loop → session.write_stream(OutputSchema) → forwarder → OutputLeaseManager → adapter 消费。
followupQueue 替代 EventManager + LoopCoordinator。
"""
from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import suppress
from typing import Any, Optional

from openjiuwen.core.common.logging import agent_logger as logger


from openjiuwen.core.single_agent.rail.base import AgentRail, AgentCallbackContext, AgentCallbackEvent, InvokeInputs
from openjiuwen.core.single_agent.agent_callback_manager import AgentCallbackManager
from openjiuwen.core.single_agent.ability_manager import AbilityManager
from openjiuwen.harness.schema.interaction import (
    InteractionOutputStream,
    OutputLeaseManager,
    SendInputRequest,
    ActiveInteractionRound,
    RoundWorkItem,
    RoundOutcome,
)
from openjiuwen.harness.schema.config import DeepAgentConfig
from openjiuwen.harness.schema.interaction import InteractionPhase
from openjiuwen.core.session.stream.base import OutputSchema

# 可选 import（延迟使用）
try:
    from openjiuwen.harness.schema.interaction import InputDispatchMode
except ImportError:
    InputDispatchMode = None

# InteractiveInput 实际路径是 core.session.interaction.interactive_input，
# 不是 harness.schema.interaction（后者只有 SendInputRequest/InteractionPhase 等）。
# 必须与 facade（interface.py._build_interactive_input_from_answers）同路径，
# 否则 send_input 收到 InteractiveInput 时 isinstance 判不出来 → 误报 query 非空错误。
try:
    from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
except ImportError:
    InteractiveInput = None


class _SimplePromptBuilder:
    """Minimal system prompt builder for rail compatibility (SkillUseRail calls add_section/remove_section)."""
    def __init__(self, system_prompt: str = "", language: str = "zh"):
        self._prompt = system_prompt or "You are a helpful assistant."
        self.language = language
        self._sections = []
    def build(self):
        parts = [self._prompt] + [s.get("content", "") for s in self._sections]
        return "\n\n".join(p for p in parts if p)
    def set_language(self, lang):
        self.language = lang
    def add_section(self, section):
        # PromptSection has .content as Dict[str, str] (multilingual) — pick current language
        if isinstance(section, dict):
            self._sections.append(section)
        elif hasattr(section, "content"):
            content = section.content
            if isinstance(content, dict):
                text = content.get(self.language) or content.get("zh") or content.get("en") or ""
            else:
                text = str(content)
            self._sections.append({"content": text, "name": getattr(section, "name", "")})
        else:
            self._sections.append({"content": str(section)})
    def remove_section(self, name):
        if hasattr(name, "value"):
            name = name.value
        self._sections = [s for s in self._sections if s.get("name", "") != str(name)]


class AgentLoop:
    """pi 风格极简 agent 内核。不继承 BaseAgent，组合 agent-core 原语。"""

    def __init__(
        self,
        *,
        card: Any,
        model: Any = None,
        deep_config: Optional[DeepAgentConfig] = None,
        system_prompt_builder: Any = None,
        system_prompt: str = None,
        ability_manager: Optional[AbilityManager] = None,
        context_engine: Any = None,
        sys_operation: Any = None,
        model_request_config: Any = None,
        model_client_config: Any = None,
    ) -> None:
        self._card = card
        self._model = model
        self._deep_config = deep_config
        self._system_prompt = system_prompt
        # 用 stock 的 SystemPromptBuilder（而非手写 _SimplePromptBuilder）：
        # - 按 priority 排序、多语言 render，与 DeepAgent 输出一致
        # - 各 rail 的 before_model_call 调 add_section 注入 RUNTIME/RESPONSE/SAFETY/SKILLS 等
        #   段，模型才能像 stock 一样"叙述动作"再调工具（否则 prompt 太薄，只吐"!"）
        self._system_prompt_builder = system_prompt_builder or self._make_system_prompt_builder(system_prompt)

        # rail 机制
        self._agent_callback_manager = AgentCallbackManager(agent_id=card.id)
        # 能力/工具
        self._ability_manager = ability_manager or AbilityManager(owner_id=card.id)
        # 上下文引擎：路径 2 —— AgentLoop 自建 vendored context_engine + 直接 set preset processor 链，
        # 不走 ContextProcessorRail（rail.init 要 agent.react_agent._config，AgentLoop 没有）。
        # 2.2 只建 engine + processor 清单存起来；2.4 才在 loop 里 route add_messages/get_context_window。
        self._model_request_config = model_request_config
        self._model_client_config = model_client_config
        self._sys_operation = sys_operation
        self._context_engine = context_engine or self._build_context_engine()
        # per-session context（路径 2：lazy create 于 _run_round fresh 分支）
        self._context: Any = None
        # kv_cache hook（路径 3）：照 react_agent.py:814-857 接 4 步。glm-5.1（OpenAI-compatible）
        # 无 release/evict_kvc → runtime.supports_*=False → 全 no-op；切 Ascend/vLLM-affinity 自动生效。
        try:
            from openjiuwen.core.single_agent.kv_cache.kv_cache_hooks import KVCacheModelCallHook
            self._kv_cache_hook: Any = KVCacheModelCallHook()
        except Exception:
            self._kv_cache_hook = None
        # 输出流租约
        self._interaction_output = OutputLeaseManager()

        # followupQueue（替代 EventManager）
        self._followup_queue: list[RoundWorkItem] = []
        self._interaction_wakeup = asyncio.Event()

        # ask_user 中断续跑状态：中断时存 messages/ctx/pending_tc，回复时取出续跑
        self._suspended: Optional[dict] = None

        # 会话/状态
        self._interaction_session = None
        self._interaction_started = False
        self._interaction_phase = InteractionPhase.IDLE
        self._active_round: Optional[ActiveInteractionRound] = None
        self._bound_session_id: Optional[str] = None

        # asyncio tasks
        self._forwarder_task: Optional[asyncio.Task] = None
        self._round_worker_task: Optional[asyncio.Task] = None
        self._round_task: Optional[asyncio.Task] = None

        # locks
        self._interaction_start_lock = asyncio.Lock()
        self._interaction_send_lock = asyncio.Lock()
        self._interaction_control_lock = asyncio.Lock()

    # —— 属性 ——

    @property
    def card(self) -> Any:
        return self._card

    @property
    def deep_config(self) -> Optional[DeepAgentConfig]:
        return self._deep_config

    @deep_config.setter
    def deep_config(self, value: Optional[DeepAgentConfig]) -> None:
        self._deep_config = value

    @property
    def _deep_config_attr(self) -> Optional[DeepAgentConfig]:
        return self._deep_config

    @property
    def system_prompt_builder(self) -> Any:
        return self._system_prompt_builder

    @property
    def ability_manager(self) -> AbilityManager:
        return self._ability_manager

    @ability_manager.setter
    def ability_manager(self, value: AbilityManager) -> None:
        self._ability_manager = value

    @property
    def agent_callback_manager(self) -> AgentCallbackManager:
        return self._agent_callback_manager

    @property
    def context_engine(self) -> Any:
        return self._context_engine

    @context_engine.setter
    def context_engine(self, value: Any) -> None:
        self._context_engine = value

    @property
    def interaction_started(self) -> bool:
        return self._interaction_started

    @property
    def active_round(self) -> Optional[ActiveInteractionRound]:
        return self._active_round

    @property
    def loop_controller(self):
        """AgentLoop 无协调器（followupQueue 替代）。返回 None。"""
        return None

    @property
    def goal_manager(self):
        """AgentLoop 无 goal_manager。"""
        return None

    def has_output_stream(self) -> bool:
        return self._interaction_output.has_consumer()

    @property
    def sys_operation(self) -> Any:
        return self._sys_operation

    # —— rail 注册 ——

    def _make_system_prompt_builder(self, system_prompt: str = None) -> Any:
        """建 stock 版 SystemPromptBuilder + IDENTITY 段。失败回落 _SimplePromptBuilder。"""
        try:
            from openjiuwen.harness.prompts.builder import SystemPromptBuilder
            from openjiuwen.harness.prompts.sections import SectionName
            from openjiuwen.core.single_agent.prompts.builder import PromptSection
        except Exception:
            return _SimplePromptBuilder(system_prompt or "")
        language = "cn"
        if self._deep_config is not None:
            language = getattr(self._deep_config, "language", None) or "cn"
        builder = SystemPromptBuilder(language=language)
        identity = system_prompt or "You are a helpful assistant."
        builder.add_section(PromptSection(
            name=SectionName.IDENTITY,
            content={"cn": identity, "en": identity},
            priority=10,
        ))
        return builder

    def _build_context_engine(self) -> Any:
        """路径 2：建 vendored ContextEngine + preset processor 清单（glm-5.1 调阈值）。

        processor 清单存 self._context_processors，create_context 时传进去（2.3/2.4）。
        阈值调到 128k 模型的 ~50-60% 触发，避免像基线那样涨到 128k 溢出。
        压缩器需 model/model_client 做摘要 LLM 调用，从 env-gate 传入的 model_request_config/
        model_client_config 取（与 stock ContextProcessorRail 从 react_agent._config 取等价）。
        """
        try:
            from jiuwenswarm.extensions.dolores.server.runtime.context_engine import (
                ContextEngine, ContextEngineConfig,
                MessageSummaryOffloaderConfig, DialogueCompressorConfig,
                CurrentRoundCompressorConfig, ReasoningToolLoopCompactProcessorConfig,
            )
            from jiuwenswarm.extensions.dolores.server.runtime.context_engine.processor.compressor.round_level_compressor import (
                RoundLevelCompressorConfig,
            )
        except Exception:
            # vendored context_engine 不可用时退化为无压缩（基线行为，不崩）
            self._context_processors = []
            return None

        model_name = getattr(self._model_request_config, "model_name", None) or "glm-5.1"
        # glm-5.1 上下文 128k；context_window_tokens 留余量设 120k，processor 阈值按比例调低
        ce_config = ContextEngineConfig(
            model_name=model_name,
            context_window_tokens=120000,
            enable_tiktoken_counter=False,  # usage_metadata 优先（priority 1 免费），不重 tokenize
        )
        workspace = getattr(self._deep_config, "workspace", None) if self._deep_config else None
        engine = ContextEngine(
            config=ce_config, workspace=workspace, sys_operation=self._sys_operation,
        )

        m_cfg = self._model_request_config
        m_cc = self._model_client_config
        # preset 链（抄 context_processor_rail._build_preset_processors，阈值 glm 化）
        # 调参（2.4 烟测后）：首轮 target=1800/mkeep=10/0.6 太激→模型重做（68 calls vs 基线 29）→
        # 提阈值到 ~90-100k 触发（留 28k headroom 防 128k 溢出）+ 加大 target/keep 留更多细节，
        # 少压缩 = 少重做。
        self._context_processors = [
            ("MessageSummaryOffloader", MessageSummaryOffloaderConfig(
                large_message_threshold=15000,
                offload_message_type=["tool"],
                protected_tool_names=["read_file"],
                model=m_cfg, model_client=m_cc,
            )),
            ("ReasoningToolLoopCompactProcessor", ReasoningToolLoopCompactProcessorConfig()),
            ("DialogueCompressor", DialogueCompressorConfig(
                tokens_threshold=100000,           # 60k→100k（glm 128k 留 headroom）
                messages_to_keep=16,               # 10→16（留更多近期原始消息）
                keep_last_round=False,
                compression_target_tokens=6000,    # 1800→6000（摘要不那么丢细节）
                model=m_cfg, model_client=m_cc,
            )),
            ("CurrentRoundCompressor", CurrentRoundCompressorConfig(
                tokens_threshold=100000,
                messages_to_keep=6,                # 3→6
                model=m_cfg, model_client=m_cc,
            )),
            ("RoundLevelCompressor", RoundLevelCompressorConfig(
                trigger_context_ratio=0.75,         # 0.6→0.75（≈90k 触发，留 headroom）
                target_total_tokens=110000,         # 80k→110k
                keep_recent_messages=12,            # 6→12
                model=m_cfg, model_client=m_cc,
            )),
        ]
        return engine

    async def _dump_context_probe(self) -> None:
        """一次性 dump context 消息分布（env DOLORES_DUMP_CONTEXT 门控）。
        context 涨到 ~50k token 时触发，看膨胀大头是谁（write_file args / read_file result / search / narration）。
        按类别（asst+tool_call[write_file/...] / tool_result / asst_text / user）汇总字符数+条数。
        """
        if getattr(self, "_context_dumped", False) or self._context is None:
            return
        try:
            msgs = self._context.get_messages()
        except Exception:
            return
        total_chars = 0
        rows = []
        tally = {}
        for i, m in enumerate(msgs):
            content = getattr(m, "content", "") or ""
            if not isinstance(content, str):
                content = str(content)
            content_len = len(content)
            args_len = 0
            tc_names = []
            for tc in (getattr(m, "tool_calls", None) or []):
                args = getattr(tc, "arguments", "") or ""
                if not isinstance(args, str):
                    args = str(args)
                args_len += len(args)
                tc_names.append(getattr(tc, "name", "?"))
            row_chars = content_len + args_len
            total_chars += row_chars
            mtype = type(m).__name__
            if mtype == "AssistantMessage" and tc_names:
                cat = f"asst+tc[{','.join(tc_names)}]"
            elif mtype == "ToolMessage":
                cat = "tool_result"
            elif mtype == "AssistantMessage":
                cat = "asst_text"
            elif mtype == "UserMessage":
                cat = "user"
            else:
                cat = mtype
            tally.setdefault(cat, [0, 0])
            tally[cat][0] += 1
            tally[cat][1] += row_chars
            rows.append((i, cat, content_len, args_len, content[:80]))
        est_tokens = total_chars // 3  # 中文为主，粗估（char≈2-3 token）
        # 触发：攒够 10 条消息 OR 字符数过 15k（取大），一次性 dump
        if len(msgs) < 10 and total_chars < 15000:
            return
        self._context_dumped = True
        try:
            with open(r"D:\jiuwenAgent\dolores\jiuwenswarm\_context_dump.txt", "w", encoding="utf-8") as f:
                f.write(f"=== context dump | msgs={len(msgs)} chars={total_chars} est_tokens≈{est_tokens} ===\n")
                f.write("=== 按类别汇总（类别 / 条数 / 字符数 / 估算token）===\n")
                for cat, (n, ch) in sorted(tally.items(), key=lambda x: -x[1][1]):
                    f.write(f"  {cat:45s} {n:4d} 条  {ch:8d} 字符  ≈{ch//3:6d} tok\n")
                f.write("\n=== 每条（idx / 类别 / content字符 / args字符 / 前80字符）===\n")
                for i, cat, cl, al, preview in rows:
                    f.write(f"[{i:3d}] {cat:40s} c={cl:6d} a={al:6d} | {preview!r}\n")
        except Exception:
            pass

    async def _dump_exit_probe(self, messages: list, ai_message: Any, tools: Any,
                               iteration: int) -> None:
        """一次性 dump 早退点（no-tool-call final）模型看到的输入+产出（env DOLORES_DUMP_EXIT 门控）。

        抓的是 model.stream 那一刻 messages 的状态：messages[0]=system（含 todo/阶段锚段？），
        messages[1:-1]=压缩后 context_messages（pptx-craft 多阶段进度压没？），messages[-1]=ai_message
        （模型给的无 tool_call final，803 字符级，是否误判完成）。只在 `not tool_calls` 早退分支调。
        """
        if not os.environ.get("DOLORES_DUMP_EXIT", "").strip().lower() in ("1", "true", "yes", "on"):
            return
        if getattr(self, "_exit_dumped", False):
            return
        self._exit_dumped = True
        try:
            lines = []
            n_tools = len(tools) if tools is not None else 0
            sys_content = ""
            if messages:
                sc = getattr(messages[0], "content", "") or ""
                sys_content = sc if isinstance(sc, str) else str(sc)
            lines.append(f"=== EXIT DUMP | iter={iteration} msgs={len(messages)} tools={n_tools} "
                         f"system_chars={len(sys_content)} ===")
            lines.append("\n--- [0] SYSTEM PROMPT (full) ---")
            lines.append(sys_content)
            lines.append("\n--- [1..-1] CONTEXT MESSAGES (compressed, what model saw as history) ---")
            total_hist = 0
            for i in range(1, max(1, len(messages) - 0)):
                if i >= len(messages):
                    break
                # 最后一条是刚生成的 ai_message，跳过（单独 dump）
                if i == len(messages) - 1:
                    continue
                m = messages[i]
                mtype = type(m).__name__
                content = getattr(m, "content", "") or ""
                if not isinstance(content, str):
                    content = str(content)
                tcs = getattr(m, "tool_calls", None) or []
                tc_names = [getattr(tc, "name", "?") for tc in tcs]
                args_total = 0
                for tc in tcs:
                    a = getattr(tc, "arguments", "") or ""
                    args_total += len(a) if isinstance(a, str) else len(str(a))
                total_hist += len(content) + args_total
                tag = f"{mtype}" + (f"+tc[{','.join(tc_names)}]" if tc_names else "")
                preview = content[:300].replace("\n", " ")
                lines.append(f"[{i:3d}] {tag:40s} c={len(content):6d} a={args_total:6d} | {preview!r}")
            lines.append(f"\n--- history total chars (excl system+final) = {total_hist} ≈{total_hist//3} tok ---")
            lines.append("\n--- [-1] MODEL FINAL (the no-tool-call output that ended the loop) ---")
            final_content = getattr(ai_message, "content", "") or ""
            if not isinstance(final_content, str):
                final_content = str(final_content)
            lines.append(f"(len={len(final_content)})")
            lines.append(final_content)
            with open(r"D:\jiuwenAgent\dolores\jiuwenswarm\_exit_dump.txt", "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
        except Exception:
            pass

    async def _kv_cache_pre_invoke(self, context_window: Any, session: Any, model_name: Any) -> dict:
        """路径 3 (task 3.x)：kv_cache hook 4 步（照 react_agent.py:814-857）。
        resolve_runtime → resolve_lineage → handle_context_window_change → build_invoke_kwargs。
        glm-5.1 无 release/evict_kvc → supports_*=False → 全 no-op，返回 {}。
        全 try/except 包死，hook 任何失败不影响 loop（返回 {} 即 stream 调用不变）。
        """
        if self._kv_cache_hook is None:
            return {}
        try:
            from openjiuwen.core.foundation.kv_cache.kv_cache_config import KVCacheAffinityConfig
            kv_config = getattr(self._deep_config, "kv_cache_affinity_config", None) or KVCacheAffinityConfig()
            runtime = self._kv_cache_hook.resolve_runtime(self._model, kv_config)
            fallback_sid = session.get_session_id() if session is not None else "default"
            session_id, parent_session_id = self._kv_cache_hook.resolve_lineage(runtime, session, fallback_sid)
            await self._kv_cache_hook.handle_context_window_change(
                runtime=runtime, llm=self._model, context=self._context,
                context_window=context_window, session_id=session_id,
                parent_session_id=parent_session_id, model_name=model_name or "",
            )
            return self._kv_cache_hook.build_invoke_kwargs(
                runtime=runtime, llm=self._model, session=session,
                session_id=session_id, parent_session_id=parent_session_id,
            )
        except Exception:
            return {}

    async def register_rail(self, rail: AgentRail) -> "AgentLoop":
        await self._agent_callback_manager.register_rail(rail, self)
        # 像 DeepAgent 一样：rail 有 set_sys_operation 就调
        if hasattr(rail, "set_sys_operation") and self._sys_operation is not None:
            rail.set_sys_operation(self._sys_operation)
        # 与 DeepAgent._sync_prompt_builder_references 一致：把 builder 引用塞给 rail，
        # rail 的 before_model_call 才能用 self.system_prompt_builder.add_section 注入段。
        if self._system_prompt_builder is not None:
            if hasattr(rail, "system_prompt_builder") or "system_prompt_builder" in getattr(rail, "__dict__", {}):
                rail.system_prompt_builder = self._system_prompt_builder
            if hasattr(rail, "_system_prompt_builder") or "_system_prompt_builder" in getattr(rail, "__dict__", {}):
                setattr(rail, "_system_prompt_builder", self._system_prompt_builder)
        if hasattr(rail, "init"):
            rail.init(self)
        return self

    async def unregister_rail(self, rail: AgentRail) -> None:
        await self._agent_callback_manager.unregister_rail(rail, self)
        if hasattr(rail, "uninit"):
            rail.uninit(self)

    # —— 驱动契约方法（照抄 DeepAgent，EventManager → followupQueue）——

    async def ensure_initialized(self) -> None:
        """异步初始化。"""
        pass

    async def start(self, *, session: Any = None) -> None:
        """绑 session + 启动 forwarder + round worker。"""
        async with self._interaction_start_lock:
            if session is None:
                from openjiuwen.core.session.agent import create_agent_session
                session = create_agent_session(session_id="default", card=self._card)
                await session.pre_run(inputs={})

            sid = session.get_session_id()
            if self._interaction_started:
                if self._bound_session_id == sid:
                    return
                raise RuntimeError(f"Already bound to {self._bound_session_id}")

            self._interaction_session = session
            self._bound_session_id = sid
            self._interaction_phase = InteractionPhase.IDLE
            self._interaction_started = True

            # forwarder: session stream → OutputLeaseManager
            self._forwarder_task = asyncio.create_task(
                self._forward_session_stream(), name=f"agentloop_forwarder[{sid}]"
            )
            # round worker: followupQueue → _run_round
            self._round_worker_task = asyncio.create_task(
                self._round_worker(), name=f"agentloop_worker[{sid}]"
            )

    async def stop(self) -> None:
        """终止。"""
        if not self._interaction_started:
            return
        self._interaction_phase = InteractionPhase.TERMINATED
        await self._interaction_output.shutdown()
        self._followup_queue.clear()
        self._interaction_wakeup.set()

        for task in (self._round_worker_task, self._forwarder_task):
            if task is not None and not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await task
        self._round_worker_task = None
        self._forwarder_task = None
        self._interaction_started = False

    async def abort(self, session: Any = None) -> None:
        """全局中止。"""
        await self._cancel_active_round(reason="abort")

    async def cancel_round(self, *, reason: Any = None) -> bool:
        """取消当前轮。"""
        return await self._cancel_active_round(reason=str(reason or "cancelled"))

    async def _cancel_active_round(self, *, reason: str) -> bool:
        """取消当前 round task。"""
        if self._round_task is not None and not self._round_task.done():
            self._round_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._round_task
            self._round_task = None
            return True
        return False

    async def attach_output(self) -> Optional[InteractionOutputStream]:
        """挂输出流。"""
        if not self._interaction_started or self._interaction_phase is InteractionPhase.TERMINATED:
            raise RuntimeError("interaction_terminated")
        async with self._interaction_send_lock:
            async with self._interaction_control_lock:
                lease = await self._interaction_output.attach()
                return InteractionOutputStream(self, lease) if lease is not None else None

    async def send_input(self, request: SendInputRequest) -> None:
        """投递输入 → followupQueue + notify。"""
        if os.environ.get("DOLORES_RESUME_DIAG", "").strip().lower() in ("1", "true", "yes", "on"):
            _q = request.inputs.get("query") if isinstance(request.inputs, dict) else None
        if not self._interaction_started or self._interaction_phase is InteractionPhase.TERMINATED:
            raise RuntimeError("interaction_terminated")
        async with self._interaction_send_lock:
            inputs = request.inputs
            if not isinstance(inputs, dict):
                raise ValueError("send_input requires inputs['query']")
            query = inputs.get("query")
            _is_ii = InteractiveInput is not None and isinstance(query, InteractiveInput)
            if _is_ii:
                work = RoundWorkItem.user(
                    request_id=request.request_id, inputs=inputs, reset_loop=False
                )
            elif isinstance(query, str) and query.strip():
                work = RoundWorkItem.user(request_id=request.request_id, inputs=inputs)
            else:
                raise ValueError("send_input requires inputs['query'] to be non-empty string")
            self._followup_queue.append(work)
            self._notify_work()

    async def next_output(self, lease: Any) -> Optional[Any]:
        """取下一个输出。"""
        return await self._interaction_output.next_item(lease)

    async def detach_output(self, token: str, *, abort_active_round: bool) -> None:
        """摘除输出流。"""
        async with self._interaction_control_lock:
            detached = await self._interaction_output.detach(token)
            if not detached:
                return
            self._followup_queue.clear()
            if abort_active_round:
                await self._cancel_active_round(reason="output_detached")

    # —— 内部：forwarder + round worker（照抄 DeepAgent 模式，EventManager → followupQueue）——

    async def _safe_fire(self, ctx, event, timeout=None):
        """fire rail hook. No timeout — SkillUseRail needs time to scan SKILL.md files.
        路径 5 (task 5.1)：rail 异常不再静默吞（原 `except: pass`），log 出来便于调试；
        仍不 break loop（pi 风格归一，rail 失败不毁轮次）。"""
        try:
            await ctx.fire(event)
        except Exception as e:
            logger.warning("[AgentLoop] rail %s failed (suppressed, loop continues): %s", getattr(event, "value", event), e)

    def _notify_work(self) -> None:
        self._interaction_wakeup.set()

    async def _forward_session_stream(self) -> None:
        """session stream → OutputLeaseManager.emit（照抄 DeepAgent）。"""
        session = self._interaction_session
        if session is None:
            return
        try:
            async for chunk in session.stream_iterator():
                await self._interaction_output.emit(chunk)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass  # TODO: log

    async def _round_worker(self) -> None:
        """从 followupQueue 取 work → 跑 _run_round（替代 DeepAgent _supervisor_loop）。"""
        try:
            while self._interaction_started and self._interaction_phase is not InteractionPhase.TERMINATED:
                if not self._interaction_output.has_consumer():
                    self._interaction_phase = InteractionPhase.IDLE
                    self._interaction_wakeup.clear()
                    await self._interaction_wakeup.wait()
                    continue

                # 从 followupQueue 取 work
                work = self._followup_queue.pop(0) if self._followup_queue else None
                if work is None:
                    # 无 work → 等待（不立即关输出流——race fix）
                    self._interaction_phase = InteractionPhase.IDLE
                    self._interaction_wakeup.clear()
                    if self._followup_queue:  # 二次检查（send_input 可能在 pop 后 push）
                        continue
                    await self._interaction_wakeup.wait()
                    continue

                # 跑一回合（直接 await，不用 create_task 避免 task 调度问题）
                self._interaction_phase = InteractionPhase.RUNNING
                # 路径 5 (task 5.5)：completion_timeout 墙钟（照 deep_agent.py:2106，默认 600s/round）。
                # 超时 → asyncio.TimeoutError → 标 error，防单轮卡死（stock DeepAgent controller.wait_round_completion 等价）。
                _ct = float(getattr(self._deep_config, "completion_timeout", 600.0) or 600.0) if self._deep_config else 600.0
                try:
                    await asyncio.wait_for(self._run_round(work), timeout=_ct)
                except asyncio.TimeoutError:
                    logger.warning("[AgentLoop] round timed out after %ss (completion_timeout)", _ct)
                    session = self._interaction_session
                    if session is not None:
                        try:
                            await session.write_stream(OutputSchema(
                                type="error", index=0,
                                payload={"error": f"round timed out after {_ct}s (completion_timeout)", "result_type": "error"},
                            ))
                        except Exception:
                            pass
                self._round_task = None
                # round 完成后，队列空则关闭输出流（让 adapter 的 async for 结束）
                if not self._followup_queue:
                    await self._interaction_output.finish_current()
                self._round_task = None
        except asyncio.CancelledError:
            pass
        except Exception:
            self._interaction_phase = InteractionPhase.IDLE

    def _should_keep_interaction_open(self) -> bool:
        """是否保持输出流开着。"""
        return bool(self._followup_queue)

    # —— loop 本体（task 2.x 填充 pi 双 while + rail 钩子；当前空壳产一个 answer chunk）——

    async def _run_round(self, work: RoundWorkItem) -> RoundOutcome:
        """跑一回合。pi 风格 + rail 钩子（ctx.fire 两层）。

        ask_user 中断：不结束任务，把 messages/ctx/pending_tc 存 self._suspended，
        结束本轮（让输出流关掉、问题回前端）。用户回复经 send_input 传来
        InteractiveInput → 新 RoundWorkItem(reset_loop=False) → 本方法走 resume 分支：
        重新执行 pending_tc（arguments=InteractiveInput，与 stock react_agent 一致）
        得 ToolMessage，续跑 for 循环。
        """
        session = self._interaction_session
        if session is None:
            return RoundOutcome()

        from openjiuwen.core.foundation.llm.schema.message import (
            SystemMessage, UserMessage, AssistantMessage, ToolMessage,
        )
        from openjiuwen.core.single_agent.rail.base import ModelCallInputs

        inputs = work.inputs if hasattr(work, "inputs") else {}
        query = inputs.get("query", "") if isinstance(inputs, dict) else str(inputs)


        # —— resume 分支：ask_user 回复 ——
        is_resume = (
            InteractiveInput is not None
            and isinstance(query, InteractiveInput)
            and self._suspended is not None
        )
        suspended_this_round = False

        if is_resume:
            suspended = self._suspended
            self._suspended = None
            messages = suspended["messages"]
            ctx = suspended["ctx"]
            pending_tc = suspended["pending_tc"]
            model_name = suspended["model_name"]
            tools = suspended["tools"]
            max_iterations = suspended["max_iterations"]
            # 重新执行 ask_user（arguments=InteractiveInput）→ 拿 ToolMessage。
            # ToolCall.arguments 是 str 字段，构造器会校验拒收 InteractiveInput；
            # 与 stock react_agent._resume_react 一致：deepcopy 后直接 setattr（pydantic v2
            # 默认 validate_assignment=False，不走校验），把 InteractiveInput 原样塞进去，
            # 由 ask_user 工具自行解析。
            import copy as _copy
            try:
                resume_tc = _copy.deepcopy(pending_tc)
                resume_tc.arguments = query
            except Exception as e:
                raise
            # BaseInterruptRail.before_tool_call 从 ctx.extra[RESUME_USER_INPUT_KEY] 取续跑输入
            # （不是从 tool_call.arguments 取），且按 tool_call_id 匹配 InteractiveInput.user_inputs。
            # facade 已把答案 update(call_id, payload)，所以这里把 InteractiveInput 塞进 ctx.extra，
            # rail 就能匹配到、走 reject 分支返回 ToolMessage（否则 re-interrupt）。
            try:
                from openjiuwen.core.single_agent.interrupt.state import RESUME_USER_INPUT_KEY
                ctx.extra[RESUME_USER_INPUT_KEY] = query
            except Exception as e:
                pass
            try:
                results = await self._ability_manager.execute(
                    ctx=ctx, tool_call=[resume_tc], session=session,
                )
            except Exception as exc:
                results = [(exc, ToolMessage(tool_call_id=pending_tc.id, content=f"Tool execution failed: {exc}"))]
            for _r, tool_msg in results:
                if tool_msg is not None:
                    messages.append(tool_msg)
                    # 路径 2 (task 2.5)：ask_user 答案的 ToolMessage 入 context，
                    # 否则后续轮 re-sync 从 window 拿不到答案 → 模型看不到回复（与 DeepAgent
                    # 复用 live context 一致）。对齐 DeepAgent _handle_resume：续跑结果落 context。
                    if self._context is not None:
                        try:
                            await self._context.add_messages(tool_msg)
                        except Exception:
                            pass
                tcid = getattr(tool_msg, "tool_call_id", None) if tool_msg is not None else pending_tc.id
                content = tool_msg.content if tool_msg is not None else ""
                await session.write_stream(OutputSchema(
                    type="tool_result", index=0,
                    payload={"tool_result": {"tool_name": pending_tc.name,
                            "tool_call_id": tcid, "result": content, "is_error": False}},
                ))
            ctx.inputs = ModelCallInputs(messages=messages, tools=tools)
        else:
            if not query:
                return RoundOutcome()
            # —— fresh round ——
            sys_prompt = self._system_prompt or "You are a helpful assistant."
            if self._system_prompt_builder is not None:
                try:
                    sys_prompt = self._system_prompt_builder.build()
                except Exception:
                    pass
            messages = [SystemMessage(content=sys_prompt), UserMessage(content=query)]

            # 路径 2 (task 2.3)：建/取 per-session context，把 user 消息塞进 context_engine
            # （持久化 + 触发 ADD 路径压缩）。loop 的 messages 仍由每轮 get_context_window 重同步。
            if self._context_engine is not None and self._context is None:
                try:
                    self._context = await self._context_engine.create_context(
                        "default", session, processors=self._context_processors,
                    )
                except Exception:
                    self._context = None
            if self._context is not None:
                try:
                    await self._context.add_messages(UserMessage(content=query))
                except Exception:
                    pass

            model_name = None
            if self._deep_config is not None:
                model_name = getattr(self._deep_config, "model_name", None)
            if model_name is None and self._model is not None:
                model_name = getattr(self._model, "model_name", None)
            tools = None
            try:
                tool_infos = await self._ability_manager.list_tool_info()
                if tool_infos:
                    tools = tool_infos
            except Exception:
                pass

            # —— rail ctx ——
            ii = InvokeInputs(query=query)
            ctx = AgentCallbackContext(agent=self, inputs=ii, session=session)
            ctx.extra["_streaming"] = True

            await self._safe_fire(ctx, AgentCallbackEvent.BEFORE_INVOKE)
            await self._safe_fire(ctx, AgentCallbackEvent.BEFORE_TASK_ITERATION)
            query = ctx.inputs.query or query  # re-read（rail 可改）

            max_iterations = 15
            if self._deep_config is not None:
                max_iterations = getattr(self._deep_config, "max_iterations", 15) or 15

        round_error = None
        try:
            for _iteration in range(max_iterations):
                # 路径 2 (task 2.4)：每轮从压缩后的 context window 重同步 messages——
                # history 走 window.context_messages（compaction 在此生效），不再裸 list 只增不缩。
                # system 占位 messages[0] 保留，下面 BEFORE_MODEL_CALL 后再 rebuild。
                # system_messages 传 messages[0]（上一轮 build 出的 system，含全部 sections）
                # → get_context_window 触发器把 system + tools + context_messages 三块一起数全，
                #   对齐 DeepAgent react_agent._railed_model_call 的计数口径，避免触发器漏数
                #   system prompt（~8k）而把真实总量低估、迟迟不触发压缩。
                _kv_kwargs: dict = {}
                if self._context is not None and messages:
                    try:
                        _win = await self._context.get_context_window(
                            system_messages=[messages[0]] if messages else [], tools=tools,
                        )
                        _sys_msg = messages[0]
                        messages = [_sys_msg] + list(_win.context_messages)
                        # 路径 3 (task 3.x)：kv_cache hook pre-invoke（glm no-op，返回 {}）
                        _kv_kwargs = await self._kv_cache_pre_invoke(_win, session, model_name)
                    except Exception:
                        pass
                # 探针：context 涨到 ~50k token 时一次性 dump 每条消息分布（env 门控）
                if os.environ.get("DOLORES_DUMP_CONTEXT", "").strip().lower() in ("1", "true", "yes", "on"):
                    await self._dump_context_probe()
                if ctx.consume_force_finish():
                    break
                for s in ctx.drain_steering():
                    messages.append(UserMessage(content=f"[STEERING] {s}"))

                ctx.inputs = ModelCallInputs(messages=messages, tools=tools)
                # BEFORE_MODEL_CALL 前，记下刚塞进 ctx.inputs.tools 的引用。
                # v3 rail（JiuWenProgressiveToolRail.before_model_call）会把 ctx.inputs.tools
                # 整体替换为按需检索的少量工具（基础工具 + search_tools）。用 id() 比对 fire 前后
                # 引用是否被替换，来判定 rail 是否接管了工具过滤——而不靠真假性兜底（空列表会
                # 被误判成"没接管"而回退全量，重蹈"像全量注入"覆辙）。
                _railed_tools_id = id(ctx.inputs.tools)
                await self._safe_fire(ctx, AgentCallbackEvent.BEFORE_MODEL_CALL)
                if self._system_prompt_builder is not None:
                    try:
                        sys_prompt = self._system_prompt_builder.build()
                        messages[0] = SystemMessage(content=sys_prompt)
                        ctx.inputs.messages = messages
                    except Exception:
                        pass
                messages = ctx.inputs.messages or messages
                if id(ctx.inputs.tools) != _railed_tools_id:
                    # 开关开启：progressive rail 已注册并改写了 ctx.inputs.tools
                    # （即使过滤后为空也用过滤结果——rail 主动清空代表本轮无可见工具，
                    # 仍应让 model 看到 nav 摘要去 search，而非塞回全量）。
                    tools = ctx.inputs.tools
                else:
                    # 开关关闭（progressive_tool_enabled=false，rail 未注册）或 rail 未改写：
                    # 回退原逻辑——重新拉一次 ability_manager 最新工具列表刷新进 tools，
                    # 再同步给 ctx.inputs.tools，保持 model 与运行期动态增删的工具一致。
                    try:
                        new_tools = await self._ability_manager.list_tool_info()
                        if new_tools:
                            tools = new_tools
                            ctx.inputs.tools = tools
                    except Exception:
                        pass

                accumulated = None
                chunk_index = 0
                _stream_final_exc: Optional[Exception] = None
                # 路径 5 (task 5.2/5.3)：pi 风格流错误归一+分类+瞬时重试 3×（exp backoff 2·2^attempt）。
                # quota/billing → fast-fail；context overflow → 不重试（走 context_engine 压缩）；
                # 429/5xx/overloaded/timeout/network/stream-ended → 重试。只在未吐 chunk 时重试（避免重复输出）。
                for _attempt in range(3):
                    accumulated = None
                    chunk_index = 0
                    # 路径 5.4-lite：流级 stall 看门狗。model.stream 若 N 秒无 chunk（glm 偶发静默挂死）
                    # → TimeoutError → 5.3 retry 分类为 timeout（retryable）重试。每块重置计时，正常流不误伤。
                    # 默认 120s（env DOLORES_STALL_TIMEOUT 覆盖）；completion_timeout 仍是兜底墙钟。
                    try:
                        _stall_to = float(os.environ.get("DOLORES_STALL_TIMEOUT", "120") or 120)
                    except Exception:
                        _stall_to = 120.0
                    try:
                        _stream_iter = self._model.stream(
                            messages=messages, model=model_name, tools=tools, **_kv_kwargs,
                        ).__aiter__()
                        while True:
                            try:
                                chunk = await asyncio.wait_for(_stream_iter.__anext__(), timeout=_stall_to)
                            except StopAsyncIteration:
                                break
                            except asyncio.TimeoutError as _stall:
                                raise TimeoutError(
                                    f"model.stream stall: no chunk in {_stall_to:g}s"
                                ) from _stall
                            # stock LLMRetryRail 等 stream chunk 检查器（suffix 死循环检测）。
                            # before_model_call 已把 inspect_stream_chunk 装进 ctx.extra。
                            # 命中重复 suffix → raise build_error("LLM repeated stream output…")
                            # → 传播到下面 except → ON_MODEL_EXCEPTION → request_retry → 重试。
                            for _insp in (ctx.extra.get("_STREAM_CHUNK_INSPECTORS_KEY") or []):
                                await _insp(ctx, chunk)
                            accumulated = chunk if accumulated is None else accumulated + chunk
                            if getattr(chunk, "reasoning_content", None):
                                await session.write_stream(OutputSchema(
                                    type="llm_reasoning", index=chunk_index,
                                    payload={"content": chunk.reasoning_content, "result_type": "answer"},
                                ))
                                chunk_index += 1
                            if getattr(chunk, "content", None):
                                await session.write_stream(OutputSchema(
                                    type="llm_output", index=chunk_index,
                                    payload={"content": chunk.content, "result_type": "answer"},
                                ))
                                chunk_index += 1
                        _stream_final_exc = None
                        break  # 成功
                    except Exception as exc:
                        _stream_final_exc = exc
                        # 让 LLMRetryRail.on_model_exception 分类 + 可能 ctx.request_retry()。
                        # 不用 _safe_fire（rail 设 _retry_request 是副作用，不抛；rail 自身抛会被外层兜）。
                        try:
                            await ctx.fire(AgentCallbackEvent.ON_MODEL_EXCEPTION)
                        except Exception:
                            pass
                        _retry_req = ctx.consume_retry_request()
                        _msg = str(exc)
                        _is_repeat = "LLM repeated stream output" in _msg
                        _is_stall = ("model.stream stall" in _msg) or ("stream frame timeout" in _msg)
                        # rail 请求重试（suffix 死循环 / stream 超时）：已吐部分是重复垃圾，丢弃重试。
                        # 覆盖下面 5.x 的 chunk_index>0 不重试规则（仅对 repeat/stall marker 覆盖）。
                        _rail_retry = _retry_req is not None and (_is_repeat or _is_stall)

                        _msg_low = _msg.lower()
                        _non_retryable = any(k in _msg_low for k in (
                            "insufficient_quota", "quota exceeded", "billing", "goausagelimit",
                        ))
                        _overflow = ("context length" in _msg_low) or ("maximum context" in _msg_low) or ("too long" in _msg_low)

                        if _rail_retry and _attempt < 2 and not _non_retryable and not _overflow:
                            _delay = (_retry_req.delay_seconds or 0) or (2 ** _attempt)
                            await session.write_stream(OutputSchema(
                                type="llm_output", index=chunk_index,
                                payload={"content": f"\n[auto_retry {_attempt + 1}/3 in {_delay}s: {_msg[:120]}]\n", "result_type": "answer"},
                            ))
                            try:
                                await asyncio.sleep(_delay)
                            except asyncio.CancelledError:
                                _stream_final_exc = exc
                                break
                            continue
                        # 5.x 原有：fast-fail / overflow / 最终失败 / 已吐部分（非 repeat/stall，重试会重复）→ 不重试
                        if _non_retryable or _overflow or _attempt >= 2 or chunk_index > 0:
                            break
                        _delay = 2 ** _attempt
                        await session.write_stream(OutputSchema(
                            type="llm_output", index=chunk_index,
                            payload={"content": f"\n[auto_retry {_attempt + 1}/3 in {_delay}s: {str(exc)[:120]}]\n", "result_type": "answer"},
                        ))
                        try:
                            await asyncio.sleep(_delay)
                        except asyncio.CancelledError:
                            _stream_final_exc = exc
                            break
                        continue
                if _stream_final_exc is not None:
                    # ON_MODEL_EXCEPTION 已在异常处理每 attempt 内 fire 过；这里只兜底写 error。
                    await session.write_stream(OutputSchema(
                        type="error", index=0, payload={"error": str(_stream_final_exc), "result_type": "error"},
                    ))
                    round_error = _stream_final_exc
                    break

                if accumulated is None:
                    ai_message = AssistantMessage(content="", tool_calls=[])
                else:
                    ai_message = AssistantMessage(
                        content=accumulated.content or "",
                        tool_calls=getattr(accumulated, "tool_calls", None) or [],
                        usage_metadata=getattr(accumulated, "usage_metadata", None),
                    )
                messages.append(ai_message)
                # 路径 2 (task 2.4)：assistant 入 context（带 usage_metadata，压缩跳过重 tokenize）
                if self._context is not None:
                    try:
                        await self._context.add_messages(ai_message)
                    except Exception:
                        pass
                ctx.inputs.messages = messages

                await self._safe_fire(ctx, AgentCallbackEvent.AFTER_MODEL_CALL)

                if ai_message.usage_metadata:
                    await session.write_stream(OutputSchema(
                        type="llm_usage", index=0,
                        payload={"usage_metadata": ai_message.usage_metadata, "result_type": "answer"},
                    ))

                tool_calls = ai_message.tool_calls or []
                if not tool_calls:
                    await session.write_stream(OutputSchema(
                        type="answer", index=0,
                        payload={"output": ai_message.content, "result_type": "answer"},
                    ))
                    # 探针：早退点（无 tool_call final）dump 模型看到的压缩后上下文+产出
                    await self._dump_exit_probe(messages, ai_message, tools, _iteration)
                    await self._safe_fire(ctx, AgentCallbackEvent.AFTER_REACT_ITERATION)
                    break

                for tc in tool_calls:
                    await session.write_stream(OutputSchema(
                        type="tool_call", index=0,
                        payload={"tool_call": {"id": tc.id, "name": tc.name, "arguments": tc.arguments}},
                    ))

                tc_name_by_id = {tc.id: tc.name for tc in tool_calls}
                try:
                    results = await self._ability_manager.execute(
                        ctx=ctx, tool_call=tool_calls, session=session,
                    )
                except Exception as exc:
                    for tc in tool_calls:
                        tm = ToolMessage(
                            tool_call_id=tc.id,
                            content=f"Tool execution failed: {exc}",
                        )
                        messages.append(tm)
                        if self._context is not None:
                            try:
                                await self._context.add_messages(tm)
                            except Exception:
                                pass
                        await session.write_stream(OutputSchema(
                            type="tool_result", index=0,
                            payload={"tool_result": {"tool_name": tc.name, "tool_call_id": tc.id,
                                    "result": f"Error: {exc}", "is_error": True}},
                        ))
                    await self._safe_fire(ctx, AgentCallbackEvent.AFTER_REACT_ITERATION)
                    continue

                # ToolMessage schema 只有 tool_call_id + content；ask_user 等中断型工具
                # 返回 (result, None)，需容忍 tool_msg=None 并记下中断的 tool_call。
                interrupt_tc = None
                for idx, (result, tool_msg) in enumerate(results):
                    if tool_msg is not None:
                        messages.append(tool_msg)
                        # 路径 2 (task 2.4)：tool result 入 context（ADD 路径触发压缩）
                        if self._context is not None:
                            try:
                                await self._context.add_messages(tool_msg)
                            except Exception:
                                pass
                        tcid = getattr(tool_msg, "tool_call_id", None)
                        content = tool_msg.content
                    else:
                        interrupt_tc = tool_calls[idx] if idx < len(tool_calls) else tool_calls[0]
                        tcid = getattr(interrupt_tc, "id", None)
                        content = str(result) if result is not None else ""
                    await session.write_stream(OutputSchema(
                        type="tool_result", index=0,
                        payload={"tool_result": {"tool_name": tc_name_by_id.get(tcid),
                                "tool_call_id": tcid,
                                "result": content,
                                "is_error": False}},
                    ))
                ctx.inputs.messages = messages

                await self._safe_fire(ctx, AgentCallbackEvent.AFTER_REACT_ITERATION)
                if interrupt_tc is not None:
                    # ask_user 中断：存续跑状态，结束本轮（输出流关闭、问题回前端）
                    self._suspended = {
                        "messages": messages,
                        "ctx": ctx,
                        "pending_tc": interrupt_tc,
                        "model_name": model_name,
                        "tools": tools,
                        "max_iterations": max_iterations,
                    }
                    suspended_this_round = True
                    break

        except Exception as exc:
            round_error = exc
        finally:
            # 任务迭代后 / 调用后：仅在轮次真正完成（非中断挂起）时对称触发。
            # 中断挂起的轮次由 resume 续跑完成后统一触发，避免重复。
            if not suspended_this_round:
                try:
                    await self._safe_fire(ctx, AgentCallbackEvent.AFTER_TASK_ITERATION)
                except Exception:
                    pass
                try:
                    await self._safe_fire(ctx, AgentCallbackEvent.AFTER_INVOKE)
                except Exception:
                    pass

        return RoundOutcome()

    # —— 子 agent 物化（task 4.x）——

    async def create_subagent(self, subagent_type: Any, session_id: Any = None) -> "AgentLoop":
        """从 SubAgentConfig 建 AgentLoop 子 agent（极简，不走 create_deep_agent）。"""
        spec = subagent_type
        if hasattr(spec, "agent_card"):
            sub = AgentLoop(
                card=spec.agent_card,
                model=getattr(spec, "model", None) or self._model,
                system_prompt=getattr(spec, "system_prompt", None),
                deep_config=self._deep_config,
            )
            for tool in getattr(spec, "tools", None) or []:
                try:
                    sub._ability_manager.add(tool)
                except Exception:
                    pass
            for rail in getattr(spec, "rails", None) or []:
                try:
                    await sub.register_rail(rail)
                except Exception:
                    pass
            return sub
        return AgentLoop(
            card=self._card,
            model=self._model,
            deep_config=self._deep_config,
            system_prompt=f"You are a {subagent_type} sub-agent.",
        )

    # —— 热重载 ——

    async def load_harness_config(self, config_path: Any) -> Any:
        return None

    async def unload_harness_config(self, config_path: Any) -> Any:
        return None

    def configure(self, config: Any) -> "AgentLoop":
        if isinstance(config, DeepAgentConfig):
            self._deep_config = config
        return self

    def get_context_usage(self, session_id: Any = None) -> Any:
        if self._context_engine is not None and hasattr(self._context_engine, "get_usage"):
            return self._context_engine.get_usage(session_id=session_id)
        return None

    def load_state(self, *args, **kwargs):
        """Stub for rail compatibility (TaskPlanningRail calls agent.load_state)."""
        pass

    def save_state(self, *args, **kwargs):
        """Stub for rail compatibility."""
        pass
