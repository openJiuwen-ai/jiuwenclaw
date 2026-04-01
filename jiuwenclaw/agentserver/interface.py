# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""JiuWenClaw Facade - 统一入口与 SDK 适配层.

此模块提供：
- 统一的 JiuWenClaw 公开 API
- SDK 工厂路由（通过环境变量选择）
- 公共编排逻辑（session 队列、Skills 路由、heartbeat、流式包装）
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, AsyncIterator, Tuple

from dotenv import load_dotenv

from jiuwenclaw.agentserver.agent_adapters import (
    AgentAdapter,
    create_adapter,
    resolve_sdk_choice,
)
from jiuwenclaw.agentserver.prompt_builder import build_user_prompt
from jiuwenclaw.agentserver.session_manager import SessionManager
from jiuwenclaw.agentserver.skill_manager import SkillManager
from jiuwenclaw.config import get_config
from jiuwenclaw.schema.agent import AgentRequest, AgentResponse, AgentResponseChunk
from jiuwenclaw.agentserver.session_history import append_history_record
from jiuwenclaw.schema.message import ReqMethod
from jiuwenclaw.utils import get_agent_home_dir, get_agent_workspace_dir, get_env_file
from jiuwenclaw.agentserver.memory.config import get_memory_mode
from jiuwenclaw.schema.hook_event import AgentServerHookEvents
from jiuwenclaw.extensions.registry import ExtensionRegistry
from jiuwenclaw.schema.hooks_context import MemoryHookContext

load_dotenv(dotenv_path=get_env_file())

logger = logging.getLogger(__name__)

_SKILL_ROUTES: dict[ReqMethod, str] = {
    ReqMethod.SKILLS_LIST: "handle_skills_list",
    ReqMethod.SKILLS_INSTALLED: "handle_skills_installed",
    ReqMethod.SKILLS_GET: "handle_skills_get",
    ReqMethod.SKILLS_MARKETPLACE_LIST: "handle_skills_marketplace_list",
    ReqMethod.SKILLS_INSTALL: "handle_skills_install",
    ReqMethod.SKILLS_UNINSTALL: "handle_skills_uninstall",
    ReqMethod.SKILLS_IMPORT_LOCAL: "handle_skills_import_local",
    ReqMethod.SKILLS_MARKETPLACE_ADD: "handle_skills_marketplace_add",
    ReqMethod.SKILLS_MARKETPLACE_REMOVE: "handle_skills_marketplace_remove",
    ReqMethod.SKILLS_MARKETPLACE_TOGGLE: "handle_skills_marketplace_toggle",
    ReqMethod.SKILLS_SKILLNET_SEARCH: "handle_skills_skillnet_search",
    ReqMethod.SKILLS_SKILLNET_INSTALL: "handle_skills_skillnet_install",
    ReqMethod.SKILLS_SKILLNET_INSTALL_STATUS: "handle_skills_skillnet_install_status",
    ReqMethod.SKILLS_SKILLNET_EVALUATE: "handle_skills_skillnet_evaluate",
    ReqMethod.SKILLS_CLAWHUB_GET_TOKEN: "handle_skills_clawhub_get_token",
    ReqMethod.SKILLS_CLAWHUB_SET_TOKEN: "handle_skills_clawhub_set_token",
    ReqMethod.SKILLS_CLAWHUB_SEARCH: "handle_skills_clawhub_search",
    ReqMethod.SKILLS_CLAWHUB_DOWNLOAD: "handle_skills_clawhub_download",
    ReqMethod.SKILLS_EVOLUTION_STATUS: "handle_skills_evolution_status",
    ReqMethod.SKILLS_EVOLUTION_GET: "handle_skills_evolution_get",
    ReqMethod.SKILLS_EVOLUTION_SAVE: "handle_skills_evolution_save",
}


class JiuWenClaw:
    """JiuWenClaw 统一门面.

    提供：
    - SDK 工厂路由
    - 统一对外 API（create_instance, reload_agent_config, process_message, process_message_stream）
    - 公共编排（session 队列、Skills 路由、heartbeat、流式包装）
    """

    def __init__(self) -> None:
        self._adapter: AgentAdapter | None = None
        self._sdk_name: str | None = None
        self._skill_manager = SkillManager(workspace_dir=str(get_agent_workspace_dir()))
        self._session_manager = SessionManager()

    def _ensure_adapter(self) -> AgentAdapter:
        """确保 adapter 已初始化，如果未初始化则根据环境变量创建."""
        if self._adapter is None:
            self._sdk_name = resolve_sdk_choice()
            self._adapter = create_adapter(self._sdk_name)
            self._skill_manager.set_skillnet_install_complete_hook(
                self.create_instance
            )
            logger.info("[JiuWenClaw] Initialized adapter: sdk=%s", self._sdk_name)
        return self._adapter

    async def create_instance(self, config: dict[str, Any] | None = None) -> None:
        """初始化 Agent 实例.

        Args:
            config: 可选配置，透传给底层 adapter.
        """
        adapter = self._ensure_adapter()
        await adapter.create_instance(config)
        logger.info("[JiuWenClaw] Agent instance created: sdk=%s", self._sdk_name)

    async def reload_agent_config(
        self,
        config_base: dict[str, Any] | None = None,
        env_overrides: dict[str, Any] | None = None,
    ) -> None:
        """从配置重新加载.

        Args:
            config_base: 可选的完整配置快照；传入时优先使用它而不是读取本地 config.yaml。
            env_overrides: 可选的环境变量增量；仅覆盖请求中出现的 key。
        """
        adapter = self._ensure_adapter()
        await adapter.reload_agent_config(config_base, env_overrides)
        logger.info("[JiuWenClaw] Agent config reloaded: sdk=%s", self._sdk_name)

    def _build_inputs(self, request: AgentRequest) -> Tuple[dict[str, Any], str]:
        """构建 adapter 所需的 inputs 字典."""
        from openjiuwen.core.session.interaction.interactive_input import InteractiveInput

        config_base = get_config()
        memory_mode = get_memory_mode(config_base)
        query = request.params.get("query", "")
        channel = request.session_id.split('_')[0] if request.session_id else "web"
        language = config_base.get("preferred_language", "zh")

        if isinstance(query, InteractiveInput):
            final_query = query
        else:
            answers = request.params.get("answers", [])
            if answers:
                request_id = request.params.get("request_id", "")
                interactive_input = self._build_interactive_input_from_answers(request_id, answers)
                final_query = interactive_input if interactive_input is not None else build_user_prompt(
                    query,
                    files=request.params.get("files", {}),
                    channel=channel,
                    language=language
                )
            else:
                final_query = build_user_prompt(
                    query,
                    files=request.params.get("files", {}),
                    channel=channel,
                    language=language
                )

        inputs: dict[str, Any] = {
            "conversation_id": request.session_id,
            "query": final_query,
            "channel": channel,
            "language": language,
        }

        run = request.params.get("run")
        if run:
            inputs["run"] = run

        return inputs, memory_mode

    def _build_interactive_input_from_answers(
        self, request_id: str, answers: list[dict]
    ) -> Any:
        """从用户答案构建 InteractiveInput.

        Args:
            request_id: 工具调用 ID
            answers: 用户答案列表，每个答案对应一个问题

        Returns:
            InteractiveInput 实例
        """
        from openjiuwen.core.session.interaction.interactive_input import InteractiveInput

        interactive_input = InteractiveInput()

        answer = answers[0] if answers else {}
        selected_options = answer.get("selected_options", []) if isinstance(answer, dict) else []
        custom_input = answer.get("custom_input", "") if isinstance(answer, dict) else ""

        if "本次允许" in selected_options:
            confirm_payload = {"approved": True, "auto_confirm": False, "feedback": ""}
        elif "总是允许" in selected_options:
            confirm_payload = {"approved": True, "auto_confirm": True, "feedback": ""}
        elif "拒绝" in selected_options:
            confirm_payload = {"approved": False, "auto_confirm": False, "feedback": custom_input or "用户拒绝"}
        else:
            confirm_payload = {"approved": False, "auto_confirm": False, "feedback": "未知选项"}

        interactive_input.update(request_id, confirm_payload)
        logger.info(
            "[JiuWenClaw] InteractiveInput.update: request_id=%s payload=%s",
            request_id, confirm_payload
        )

        return interactive_input

    async def _handle_skills_request(self, request: AgentRequest) -> AgentResponse | None:
        """处理 Skills 相关请求，返回 None 表示不是 Skills 请求."""
        if request.req_method not in _SKILL_ROUTES:
            return None

        handler_name = _SKILL_ROUTES[request.req_method]
        handler = getattr(self._skill_manager, handler_name)
        try:
            payload = await handler(request.params)
            _reload_after_skills = handler_name in [
                "handle_skills_install",
                "handle_skills_uninstall",
                "handle_skills_import_local",
                "handle_skills_skillnet_install",
            ]
            if handler_name == "handle_skills_skillnet_install" and payload.get("pending"):
                _reload_after_skills = False
            if _reload_after_skills:
                await self.create_instance()
        except Exception as exc:
            logger.error("[JiuWenClaw] skills 请求处理失败: %s", exc)
            return AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(exc)},
                metadata=request.metadata,
            )
        return AgentResponse(
            request_id=request.request_id,
            channel_id=request.channel_id,
            ok=True,
            payload=payload,
            metadata=request.metadata,
        )

    async def _process_interrupt(self, request: AgentRequest) -> AgentResponse:
        """处理 interrupt 请求.

        根据 intent 分流：
        - pause: 暂停 ReAct 循环（不取消任务）
        - resume: 恢复已暂停的 ReAct 循环
        - cancel: 取消所有运行中的任务
        - supplement: 取消当前任务但保留 todo

        Args:
            request: AgentRequest，params 中可包含：
                - intent: 中断意图 ('pause' | 'cancel' | 'resume' | 'supplement')
                - new_input: 新的用户输入（用于切换任务）

        Returns:
            AgentResponse 包含 interrupt_result 事件数据
        """
        adapter = self._ensure_adapter()
        # 调用 adapter 的 process_interrupt 处理 SDK 特定逻辑（如 pause/resume、todo 标记等）
        response = await adapter.process_interrupt(request)
        intent = request.params.get("intent", "cancel")

        if intent == "pause":
            # 暂停：不取消任务，只暂停 ReAct 循环
            return response

        if intent == "resume":
            # 恢复：恢复 ReAct 循环
            return response

        if intent == "supplement":
            # 取消当前 session 的任务
            session_id = self._session_manager.get_session_id(request.session_id)
            await self._session_manager.cancel_session_task(session_id, "interrupt(supplement): ")
            return response

        # cancel: 取消所有 session 的任务
        await self._session_manager.cancel_all_session_tasks(f"interrupt(intent={intent}): ")
        return response

    async def process_message(self, request: AgentRequest) -> AgentResponse:
        """处理非流式请求.

        支持多 session 并发执行，同 session 内任务按先进后出顺序执行.
        """
        adapter = self._ensure_adapter()

        if request.req_method == ReqMethod.CHAT_CANCEL:
            return await self._process_interrupt(request)

        if request.req_method == ReqMethod.CHAT_ANSWER:
            return await adapter.handle_user_answer(request)

        heartbeat_response = await adapter.handle_heartbeat(request)
        if heartbeat_response is not None:
            return heartbeat_response

        skills_response = await self._handle_skills_request(request)
        if skills_response is not None:
            return skills_response

        session_id = self._session_manager.get_session_id(request.session_id)
        query = request.params.get("query", "")
        append_history_record(
            session_id=session_id,
            request_id=request.request_id,
            channel_id=request.channel_id,
            role="user",
            content=query,
            timestamp=time.time(),
        )

        logger.info(
            "[JiuWenClaw] 处理请求: request_id=%s channel_id=%s session_id=%s sdk=%s",
            request.request_id, request.channel_id, session_id, self._sdk_name,
        )

        inputs, memory_mode = self._build_inputs(request)

        # cloud memory: before chat hook
        if memory_mode == "cloud":
            mem_ctx = MemoryHookContext(
                session_id=request.session_id or "default",
                request_id=request.request_id or "",
                channel_id=request.channel_id,
                agent_name="main_agent",
                workspace_dir=str(get_agent_home_dir()),
                extra=request.params,
            )
            await ExtensionRegistry.get_instance().trigger(AgentServerHookEvents.MEMORY_BEFORE_CHAT, mem_ctx)
            memory_block = "\n\n".join(b for b in mem_ctx.memory_blocks if b)
            inputs["memory_block"] = memory_block

        async def run_agent_task():
            return await adapter.process_message_impl(request, inputs)

        result = await self._session_manager.submit_and_wait(session_id, run_agent_task)

        if result.ok and result.payload.get("content"):
            content = result.payload["content"]
            content_str = content if isinstance(content, str) else str(content)
            append_history_record(
                session_id=session_id,
                request_id=request.request_id,
                channel_id=request.channel_id,
                role="assistant",
                event_type="chat.final",
                content=content_str,
                timestamp=time.time(),
            )

            # cloud memory: after chat hook
            if memory_mode == "cloud":
                after_ctx = MemoryHookContext(
                    session_id=request.session_id or "default",
                    request_id=request.request_id or "",
                    channel_id=request.channel_id,
                    agent_name="main_agent",
                    workspace_dir=str(get_agent_home_dir()),
                    assistant_message=content_str,
                    extra=request.params,
                )
                await ExtensionRegistry.get_instance().trigger(AgentServerHookEvents.MEMORY_AFTER_CHAT, after_ctx)

        return result

    async def process_message_stream(
            self, request: AgentRequest
    ) -> AsyncIterator[AgentResponseChunk]:
        """处理流式请求.

        支持多 session 并发执行，同 session 内任务按先进后出顺序执行.
        """
        adapter = self._ensure_adapter()

        session_id = self._session_manager.get_session_id(request.session_id)
        query = request.params.get("query", "")
        append_history_record(
            session_id=session_id,
            request_id=request.request_id,
            channel_id=request.channel_id,
            role="user",
            content=query,
            timestamp=time.time(),
        )

        logger.info(
            "[JiuWenClaw] 处理流式请求: request_id=%s channel_id=%s session_id=%s sdk=%s",
            request.request_id, request.channel_id, session_id, self._sdk_name,
        )

        inputs, memory_mode = self._build_inputs(request)
        rid = request.request_id
        cid = request.channel_id

        # cloud memory: before chat hook
        if memory_mode == "cloud":
            mem_ctx = MemoryHookContext(
                session_id=request.session_id or "default",
                request_id=request.request_id or "",
                channel_id=request.channel_id,
                agent_name="main_agent",
                workspace_dir=str(get_agent_home_dir()),
                extra=request.params,
            )
            await ExtensionRegistry.get_instance().trigger(AgentServerHookEvents.MEMORY_BEFORE_CHAT, mem_ctx)
            memory_block = "\n\n".join(b for b in mem_ctx.memory_blocks if b)
            inputs["memory_block"] = memory_block

        stream_queue = asyncio.Queue()
        stream_done = asyncio.Event()
        final_answer_content = ""
        final_answer_chunks: list[str] = []

        async def run_stream_task():
            try:
                async for chunk in adapter.process_message_stream_impl(request, inputs):
                    await stream_queue.put(("chunk", chunk))
            except asyncio.CancelledError:
                logger.info("[JiuWenClaw] 流式任务被取消: request_id=%s session_id=%s", rid, session_id)
                await stream_queue.put(("error", asyncio.CancelledError()))
            except Exception as exc:
                logger.exception("[JiuWenClaw] 流式任务异常: %s", exc)
                await stream_queue.put(("error", exc))
            finally:
                stream_done.set()

        await self._session_manager.submit_task(session_id, run_stream_task)

        try:
            while not stream_done.is_set() or not stream_queue.empty():
                try:
                    item = await asyncio.wait_for(stream_queue.get(), timeout=0.1)
                except asyncio.TimeoutError:
                    continue

                event_type, data = item

                if event_type == "error":
                    if isinstance(data, asyncio.CancelledError):
                        logger.info("[JiuWenClaw] 流式处理被中断: request_id=%s", rid)
                        raise data
                    append_history_record(
                        session_id=session_id,
                        request_id=rid,
                        channel_id=cid,
                        role="assistant",
                        event_type="chat.error",
                        content=str(data),
                        timestamp=time.time(),
                    )
                    yield AgentResponseChunk(
                        request_id=rid,
                        channel_id=cid,
                        payload={"event_type": "chat.error", "error": str(data)},
                        is_complete=False,
                    )
                else:
                    if isinstance(data, AgentResponseChunk):
                        if isinstance(data.payload, dict) and isinstance(data.payload.get("event_type"), str):
                            et = str(data.payload.get("event_type"))
                            append_history_record(
                                session_id=session_id,
                                request_id=rid,
                                channel_id=cid,
                                role="assistant",
                                event_type=et,
                                content=data.payload.get("content") or data.payload.get("error") or "",
                                timestamp=time.time(),
                                extra={"event_payload": dict(data.payload)},
                            )
                            if et == "chat.final":
                                final_answer_content = str(data.payload.get("content", ""))
                            elif et == "chat.delta":
                                final_answer_chunks.append(str(data.payload.get("content", "")))
                        yield data
                    elif isinstance(data, dict) and isinstance(data.get("event_type"), str):
                        et = str(data.get("event_type"))
                        append_history_record(
                            session_id=session_id,
                            request_id=rid,
                            channel_id=cid,
                            role="assistant",
                            event_type=et,
                            content=data.get("content") or data.get("error") or "",
                            timestamp=time.time(),
                            extra={"event_payload": dict(data)},
                        )
                        if et == "chat.final":
                            final_answer_content = str(data.get("content", ""))
                        elif et == "chat.delta":
                            final_answer_chunks.append(str(data.get("content", "")))
                        yield AgentResponseChunk(
                            request_id=rid,
                            channel_id=cid,
                            payload=data,
                            is_complete=False,
                        )
        except asyncio.CancelledError:
            logger.info("[JiuWenClaw] 流式处理被中断: request_id=%s", rid)
            raise

        # cloud memory: after chat hook
        if memory_mode == "cloud":
            assistant_message = final_answer_content or "".join(final_answer_chunks)
            after_ctx = MemoryHookContext(
                session_id=request.session_id or "default",
                request_id=request.request_id or "",
                channel_id=request.channel_id,
                agent_name="main_agent",
                workspace_dir=str(get_agent_home_dir()),
                assistant_message=assistant_message,
                extra=request.params,
            )
            await ExtensionRegistry.get_instance().trigger(AgentServerHookEvents.MEMORY_AFTER_CHAT, after_ctx)

        yield AgentResponseChunk(
            request_id=rid,
            channel_id=cid,
            payload={"is_complete": True},
            is_complete=True,
        )