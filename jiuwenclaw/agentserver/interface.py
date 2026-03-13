# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""JiuWenClaw - 基于 openjiuwen ReActAgent 的 IAgentServer 实现."""

from __future__ import annotations

import asyncio
import os
from typing import Any, AsyncIterator

from dotenv import load_dotenv
from openjiuwen.core.context_engine import MessageOffloaderConfig, DialogueCompressorConfig
from openjiuwen.core.foundation.llm import ModelRequestConfig
from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent import AgentCard, ReActAgentConfig
from openjiuwen.core.sys_operation import SysOperationCard, OperationMode, LocalWorkConfig
from openjiuwen.core.session.checkpointer import CheckpointerFactory
from openjiuwen.core.session.checkpointer.checkpointer import CheckpointerConfig
from openjiuwen.core.session.checkpointer.persistence import PersistenceCheckpointerProvider

from jiuwenclaw.gateway.cron import CronController, CronTargetChannel
from jiuwenclaw.utils import get_root_dir, logger, USER_WORKSPACE_DIR
from jiuwenclaw.config import get_config
from jiuwenclaw.agentserver.react_agent import JiuClawReActAgent
from jiuwenclaw.agentserver.tools.browser_tools import register_browser_runtime_mcp_server
from jiuwenclaw.agentserver.tools.mcp_toolkits import get_mcp_tools
from jiuwenclaw.agentserver.tools.todo_toolkits import TaskStatus, TodoToolkit
from jiuwenclaw.agentserver.tools.memory_tools import (
    init_memory_manager_async,
    memory_search,
    memory_get,
    write_memory,
    edit_memory,
    read_memory,
)
from jiuwenclaw.agentserver.memory.compaction import ContextCompactionManager
from jiuwenclaw.agentserver.memory.config import clear_config_cache
from jiuwenclaw.agentserver.memory import clear_memory_manager_cache
from jiuwenclaw.agentserver.skill_manager import SkillManager, _SKILLS_DIR
from jiuwenclaw.agentserver.prompt_builder import DEFAULT_WORKSPACE_DIR
from jiuwenclaw.evolution.skill_optimizer import SkillOptimizer
from jiuwenclaw.schema.agent import AgentRequest, AgentResponse, AgentResponseChunk
from jiuwenclaw.agentserver.memory import get_memory_manager
from jiuwenclaw.schema.message import ReqMethod

load_dotenv(dotenv_path=get_root_dir() / ".env")


SYSTEM_PROMPT = """# 角色
你是一个能够帮助用户执行任务的小助手。

在完成任务的同时，你应该充分利用记忆系统，记录用户背景、任务上下文、项目信息、偏好、路径或环境信息等长期有价值的内容，以保持与用户的长期上下文连续性。

你的上下文在过长时会被自动压缩，当你看到已卸载内容标记并认为获取该内容有助于回答问题时，可随时调用reload_original_context_messages函数：

调用reload_original_context_messages(offload_handle="<id>", offload_type="<type>")，并使用标记中的确切值

请勿猜测或编造缺失的内容

存储类型："in_memory"（会话缓存）
"""

TODO_PROMPT = """
# 任务执行规则
1. 所有任务必须通过 todo 工具进行记录和追踪。
2. 首先，你应该尝试使用 todo_create 创建新任务。
3. 但如果遇到"错误：待办列表已存在"的提示，则必须使用 todo_insert 函数添加任务。
4. 如果用户有新的需求，请分析当前已有任务，并结合当前执行情况，对当前的 todo 任务实现最小改动，以满足用户的需求。
5. **完成任务强制规则**：
   - 任务的每个子项执行完毕后，**必须调用 todo_complete 工具**将其标记为已完成
   - todo_complete 工具需要传入对应的任务ID（从当前待办列表中获取）
   - 只有成功调用 todo_complete 工具后，才能向用户报告任务已完成
6. 严禁仅用语言表示任务完成，必须实际调用工具。

处理用户请求时，请检查你的技能是否适用，阅读对应的技能描述，使用合理的技能。
"""

# Skills 请求路由表
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
}


class JiuWenClaw:
    """基于 openJiuwen ReActAgent 的 AgentServer 实现."""

    def __init__(self) -> None:
        self._instance: JiuClawReActAgent | None = None
        self._skill_manager = SkillManager()
        self._session_tasks: dict[str, asyncio.Task] = {}  # session_id -> running_task
        self._session_priorities: dict[str, int] = {}  # session_id -> 优先级计数器（用于先进后出）
        self._session_queues: dict[str, asyncio.PriorityQueue] = {}  # session_id -> 优先队列
        self._session_processors: dict[str, asyncio.Task] = {}  # session_id -> processor_task
        self._workspace_dir: str = DEFAULT_WORKSPACE_DIR
        self._agent_name: str = "main_agent"
        self._compaction_manager: ContextCompactionManager | None = None
        self._browser_mcp_registered: bool = False
        self._memory_tools_registered: bool = False
        self._mcp_tools_registered: bool = False
        self._todo_tool_sessions_registered: set[str] = set()
        self._sysop_card_id: str | None = None

    @staticmethod
    async def set_checkpoint():
        try:
            PersistenceCheckpointerProvider()
            checkpoint_path = USER_WORKSPACE_DIR / "checkpoint"
            checkpointer = await CheckpointerFactory.create(
                CheckpointerConfig(
                    type="persistence",
                    conf={"db_type": "sqlite", "db_path": f"{checkpoint_path}/checkpoint"},
                )
            )
            CheckpointerFactory.set_default_checkpointer(checkpointer)
        except Exception as e:
            logger.error(("[JiuWenClaw] fail to setup checkpoint due to: %s", e))

    def _load_react_config(self, config):
        # 提取 agent_name，如果不存在则使用默认值
        agent_name = config.pop("agent_name", "main_agent")
        self._agent_name = agent_name

        if "workspace_dir" in config:
            self._workspace_dir = config.pop("workspace_dir")

        # 处理 model_client_config：确保包含必需字段
        if "model_client_config" in config:
            model_client_config = config["model_client_config"]
            # 如果 model_client_config 缺少必需字段，尝试从顶层配置补充
            if not isinstance(model_client_config, dict):
                model_client_config = {}
            # 确保必需字段存在（即使为空，也会在运行时通过环境变量填充）
            if "client_provider" not in model_client_config:
                model_client_config["client_provider"] = config.pop("model_provider", "OpenAI")
            # 库要求首字母大写的 OpenAI / SiliconFlow，小写 openai 会报 Unsupported client_type
            p = model_client_config.get("client_provider", "")
            if isinstance(p, str) and p.strip().lower() == "openai":
                model_client_config["client_provider"] = "OpenAI"
            if "api_base" not in model_client_config:
                model_client_config["api_base"] = config.pop("api_base", "")
            if "api_key" not in model_client_config:
                model_client_config["api_key"] = config.pop("api_key", "")
            model_client_config["timeout"] = config.pop("timeout", 1800)
            config["model_client_config"] = model_client_config

        config["model_config_obj"] = {
            "temperature": 0.95
        }

        config["prompt_template"] = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]

        # 创建 ReActAgentConfig
        agent_config = ReActAgentConfig(**config)

        # 上下文压缩卸载
        processors = [
            (
                "MessageOffloader",
                MessageOffloaderConfig(
                    messages_threshold=40,
                    tokens_threshold=20000,
                    large_message_threshold=1000,
                    trim_size=500,
                    offload_message_type=["tool"],
                    keep_last_round=False,
                )
            ),
            (
                "DialogueCompressor",
                DialogueCompressorConfig(
                    messages_threshold=40,
                    tokens_threshold=50000,
                    model=ModelRequestConfig(
                        model=config["model_name"]
                    ),
                    model_client=config["model_client_config"],
                    keep_last_round=False,
                )
            )
        ]
        agent_config.configure_context_processors(processors)
        return agent_config

    async def create_instance(self, config: dict[str, Any] | None = None) -> None:
        """初始化 ReActAgent 实例.

        Args:
            config: 可选配置，支持以下字段：
                - agent_name: Agent 名称，默认 "main_agent"。
                - workspace_dir: 工作区目录，默认 "workspace/agent"。
                - 其余字段透传给 ReActAgentConfig。
        """
        await self.set_checkpoint()

        config_base = get_config()
        config = config_base.get('react', {}).copy()
        agent_config = self._load_react_config(config)

        sysop_card_id: str | None = None
        try:
            sysop_card = SysOperationCard(
                mode=OperationMode.LOCAL,
                work_config=LocalWorkConfig(work_dir=str(USER_WORKSPACE_DIR / "workspace")),
            )
            Runner.resource_mgr.add_sys_operation(sysop_card)
            sysop_card_id = sysop_card.id
        except Exception as exc:
            logger.warning("[JiuWenClaw] add sys_operation failed, fallback without it: %s", exc)
        self._sysop_card_id = sysop_card_id

        agent_card = AgentCard(name=self._agent_name, id='jiuwenclaw')
        self._instance = JiuClawReActAgent(card=agent_card)

        if sysop_card_id and hasattr(self._instance, "_skill_util"):
            agent_config.sys_operation_id = sysop_card_id
        elif sysop_card_id:
            logger.warning("[JiuWenClaw] ReActAgent has no _skill_util; skip sys_operation_id binding.")

        self._instance.configure(agent_config)

        # register installed skills (compatible with openjiuwen variants).
        if hasattr(self._instance, "_skill_util"):
            try:
                await self._instance.register_skill(str(_SKILLS_DIR))
            except Exception as exc:
                logger.warning("[JiuWenClaw] register_skill failed, continue without skills: %s", exc)

            # Register SkillOptimizer (enable evolution feature)
            evolution_cfg: dict = config.pop("evolution", {})
            evolution_enabled: bool = evolution_cfg.get("enabled", False)

            # 检查是否有有效的模型配置（api_key 或 client_provider）
            has_valid_model_config = False
            if isinstance(config.get("model_client_config"), dict):
                mcc = config["model_client_config"]
                # 检查是否有 api_key（非空）或通过环境变量配置
                api_key = mcc.get("api_key", "")
                if api_key or os.getenv("API_KEY"):
                    has_valid_model_config = True
            # 如果没有 api_key，检查是否通过其他方式配置（如从环境变量获取）
            if not has_valid_model_config:
                if os.getenv("API_KEY"):
                    has_valid_model_config = True

            if evolution_enabled and has_valid_model_config:
                # 优先从环境变量读取（前端配置）回退到 config.yaml
                _env_auto_scan = os.getenv("EVOLUTION_AUTO_SCAN")
                if _env_auto_scan is not None:
                    evolution_auto_scan: bool = _env_auto_scan.lower() in ("true", "1", "yes")
                else:
                    evolution_auto_scan = evolution_cfg.get("auto_scan", False)
                optimizer = SkillOptimizer(
                    llm=self._instance._get_llm(),
                    model=agent_config.model_name,
                    skills_base_dir=str(_SKILLS_DIR),
                    auto_scan=evolution_auto_scan,
                )
                self._instance.register_online_optimizer(optimizer)
                logger.info("[JiuWenClaw] Evolution has been enabled: auto_scan=%s", evolution_auto_scan)
            elif evolution_enabled and not has_valid_model_config:
                logger.warning("[JiuWenClaw] Evolution is enabled but skipped: no valid model API key configured")
        else:
            logger.warning("[JiuWenClaw] ReActAgent has no _skill_util; skip skill registration.")

        # add memory tools
        await init_memory_manager_async(
            workspace_dir=self._workspace_dir,
            agent_id=self._agent_name,
        )
        for tool in [memory_search, memory_get, write_memory, edit_memory, read_memory]:
            Runner.resource_mgr.add_tool(tool)
            self._instance.ability_manager.add(tool.card)
        self._memory_tools_registered = True

        for mcp_tool in get_mcp_tools():
            Runner.resource_mgr.add_tool(mcp_tool)
            self._instance.ability_manager.add(mcp_tool.card)
        self._mcp_tools_registered = True

        if self._compaction_manager is None:
            memory_mgr = await get_memory_manager(
                agent_id=self._agent_name,
                workspace_dir=self._workspace_dir
            )
            if memory_mgr:
                self._compaction_manager = ContextCompactionManager(
                    workspace_dir=self._workspace_dir,
                    threshold=8000,
                    keep_recent=10
                )

        try:
            self._browser_mcp_registered = await register_browser_runtime_mcp_server(
                self._instance,
                tag=f"agent.{self._agent_name}",
            )
        except Exception as exc:
            logger.warning("[JiuWenClaw] browser MCP registration skipped: %s", exc)

        # add cron tools
        try:
            cron_controller = CronController.get_instance()
            for cron_tool in cron_controller.get_tools():
                Runner.resource_mgr.add_tool(cron_tool)
                self._instance.ability_manager.add(cron_tool.card)
        except Exception as exc:
            logger.error("[JiuWenClaw] 定时工具加载失败， reason=%s", exc)
        logger.info("[JiuWenClaw] 初始化完成: agent_name=%s", self._agent_name)

    def reload_agent_config(self) -> None:
        """从 config.yaml 重新加载配置并 reconfigure 当前实例，使模型/API 等配置生效且不重启进程。"""
        if self._instance is None:
            raise RuntimeError("JiuWenClaw 未初始化，请先调用 create_instance()")
        clear_config_cache()
        clear_memory_manager_cache()

        config_base = get_config()
        config = config_base.get('react', {}).copy()
        agent_config = self._load_react_config(config)

        if self._sysop_card_id:
            agent_config.sys_operation_id = self._sysop_card_id

        if hasattr(self._instance, "_llm"):
            self._instance._llm = None
        self._instance.configure(agent_config)
        # 使 evolution 热更新生效：刷新 SkillOptimizer 的 LLM / model / auto_scan
        _env_auto_scan = os.getenv("EVOLUTION_AUTO_SCAN")
        if _env_auto_scan is not None:
            auto_scan_val = _env_auto_scan.lower() in ("true", "1", "yes")
        else:
            auto_scan_val = None
        new_llm = self._instance._get_llm()
        new_model = agent_config.model_name
        for opt in getattr(self._instance, "_online_optimizers", []):
            if auto_scan_val is not None and hasattr(opt, "auto_scan"):
                opt.auto_scan = auto_scan_val
            if isinstance(opt, SkillOptimizer):
                opt._llm = new_llm
                opt._model = new_model
                if hasattr(opt, "_manager"):
                    opt._manager._llm = new_llm
                    opt._manager._model = new_model
        # 使 evolution 热更新生效：刷新 SkillOptimizer 的 LLM / model / auto_scan
        # 检查是否有有效的模型配置
        has_valid_model_config = False
        if isinstance(config.get("model_client_config"), dict):
            mcc = config["model_client_config"]
            api_key = mcc.get("api_key", "")
            if api_key or os.getenv("API_KEY"):
                has_valid_model_config = True
        if not has_valid_model_config:
            if os.getenv("API_KEY"):
                has_valid_model_config = True

        if has_valid_model_config:
            _env_auto_scan = os.getenv("EVOLUTION_AUTO_SCAN")
            if _env_auto_scan is not None:
                auto_scan_val = _env_auto_scan.lower() in ("true", "1", "yes")
            else:
                auto_scan_val = None
            new_llm = self._instance._get_llm()
            new_model = agent_config.model_name
            for opt in getattr(self._instance, "_online_optimizers", []):
                if auto_scan_val is not None and hasattr(opt, "auto_scan"):
                    opt.auto_scan = auto_scan_val
                if isinstance(opt, SkillOptimizer):
                    opt._llm = new_llm
                    opt._model = new_model
                    if hasattr(opt, "_manager"):
                        opt._manager._llm = new_llm
                        opt._manager._model = new_model
        logger.info("[JiuWenClaw] 配置已热更新，未重启进程")

    async def _register_runtime_tools(self, session_id: str | None, mode="plan") -> None:
        """Register per-request tools for current agent execution."""
        if self._instance is None:
            raise RuntimeError("JiuWenClaw 未初始化，请先调用 create_instance()")

        tool_list = self._instance.ability_manager.list()
        for tool in tool_list:
            if isinstance(tool, ToolCard):
                if tool.name.startswith("todo_"):
                    self._instance.ability_manager.remove(tool.name)
                elif tool.name.startswith("cron_"):
                    self._instance.ability_manager.remove(tool.name)

        # 定时工具
        channel = session_id.split('_')[0]
        if channel not in ["heartbeat", "cron"]:
            cron_controller = CronController.get_instance()

            if channel == "feishu":
                cron_controller.set_target_channel(CronTargetChannel.FEISHU)
            if channel == "sess":
                cron_controller.set_target_channel(CronTargetChannel.WEB)

            for cron_tool in cron_controller.get_tools():
                Runner.resource_mgr.add_tool(cron_tool)
                self._instance.ability_manager.add(cron_tool.card)

        effective_session_id = session_id or "default"
        if mode == "plan":
            self._instance._config.prompt_template = [{
                "role": "system",
                "content": SYSTEM_PROMPT + TODO_PROMPT,
            }]
            # if effective_session_id not in self._todo_tool_sessions_registered:
            todo_toolkit = TodoToolkit(session_id=effective_session_id)
            for tool in todo_toolkit.get_tools():
                Runner.resource_mgr.add_tool(tool)
                self._instance.ability_manager.add(tool.card)
            self._todo_tool_sessions_registered.add(effective_session_id)
        else:
            self._instance._config.prompt_template = [{
                "role": "system",
                "content": SYSTEM_PROMPT,
            }]
            tool_list = self._instance.ability_manager.list()
            for tool in tool_list:
                if isinstance(tool, ToolCard):
                    if tool.name.startswith("todo_"):
                        self._instance.ability_manager.remove(tool.name)

        if not self._memory_tools_registered:
            await init_memory_manager_async(
                workspace_dir=self._workspace_dir,
                agent_id=self._agent_name,
            )
            for tool in [memory_search, memory_get, write_memory, edit_memory, read_memory]:
                Runner.resource_mgr.add_tool(tool)
                self._instance.ability_manager.add(tool.card)
            self._memory_tools_registered = True

        if not self._mcp_tools_registered:
            for mcp_tool in get_mcp_tools():
                Runner.resource_mgr.add_tool(mcp_tool)
                self._instance.ability_manager.add(mcp_tool.card)
            self._mcp_tools_registered = True

    async def process_interrupt(self, request: AgentRequest) -> AgentResponse:
        """处理 interrupt 请求.

        根据 intent 分流：
        - pause: 暂停 ReAct 循环（不取消任务）
        - resume: 恢复已暂停的 ReAct 循环
        - cancel: 取消所有运行中的任务

        Args:
            request: AgentRequest，params 中可包含：
                - intent: 中断意图 ('pause' | 'cancel' | 'resume')
                - new_input: 新的用户输入（用于切换任务）

        Returns:
            AgentResponse 包含 interrupt_result 事件数据
        """
        intent = request.params.get("intent", "cancel")
        new_input = request.params.get("new_input")

        success = True

        if intent == "pause":
            # 暂停：不取消任务，只暂停 ReAct 循环
            if self._instance is not None and hasattr(self._instance, 'pause'):
                self._instance.pause()
                logger.info(
                    "[JiuWenClaw] interrupt: 已暂停 ReAct 循环 request_id=%s",
                    request.request_id,
                )
            message = "任务已暂停"

        elif intent == "resume":
            # 恢复：恢复 ReAct 循环
            if self._instance is not None and hasattr(self._instance, 'resume'):
                self._instance.resume()
                logger.info(
                    "[JiuWenClaw] interrupt: 已恢复 ReAct 循环 request_id=%s",
                    request.request_id,
                )
            message = "任务已恢复"

        elif intent == "supplement":
            # supplement: 取消当前任务，但保留 todo（新任务会根据 todo 待办继续执行）
            # 先解除暂停，防止 task 阻塞在 pause_event.wait 上
            if self._instance is not None and hasattr(self._instance, 'resume'):
                self._instance.resume()

            # 取消当前 session 的非流式任务
            session_id = self._get_session_id(request)
            await self._cancel_session_task(session_id, "interrupt(supplement): ")

            # 取消流式任务
            if self._instance is not None:
                stream_tasks = getattr(self._instance, '_stream_tasks', set())
                active = [t for t in stream_tasks if not t.done()]
                if active:
                    logger.info(
                        "[JiuWenClaw] interrupt(supplement): 取消 %d 个流式任务 request_id=%s",
                        len(active), request.request_id,
                    )
                    for t in active:
                        t.cancel()

            # 不清理 todo！保留所有待办项，新任务会根据 todo 中的待办继续执行
            message = "任务已切换"

        else:
            # cancel / 其他：取消所有运行中的任务
            # 先恢复暂停（防止 cancel 时 task 阻塞在 pause_event.wait 上）
            if self._instance is not None and hasattr(self._instance, 'resume'):
                self._instance.resume()

            # 取消所有 session 的非流式任务
            await self._cancel_all_session_tasks(f"interrupt(intent={intent}): ")

            # 取消流式任务
            if self._instance is not None:
                stream_tasks = getattr(self._instance, '_stream_tasks', set())
                active = [t for t in stream_tasks if not t.done()]
                if active:
                    logger.info(
                        "[JiuWenClaw] interrupt: 取消 %d 个流式任务 request_id=%s",
                        len(active), request.request_id,
                    )
                    for t in active:
                        t.cancel()

            # 将未完成的 todo 项标记为 cancelled（保留在列表中，agent 不会执行）
            if request.session_id:
                try:
                    todo_toolkit = TodoToolkit(session_id=request.session_id)
                    tasks = todo_toolkit._load_tasks()
                    cancel_count = 0
                    for t in tasks:
                        if t.status.value in ("waiting", "running"):
                            t.status = TaskStatus.CANCELLED
                            cancel_count += 1
                    if cancel_count:
                        todo_toolkit._save_tasks(tasks)
                        logger.info(
                            "[JiuWenClaw] interrupt: 已将 %d 个未完成 todo 项标记为 cancelled session_id=%s",
                            cancel_count, request.session_id,
                        )
                except Exception as exc:
                    logger.warning("[JiuWenClaw] 标记 todo cancelled 失败: %s", exc)

            if new_input:
                message = "已切换到新任务"
            else:
                message = "任务已取消"

        # 返回 interrupt_result 事件
        payload = {
            "event_type": "chat.interrupt_result",
            "intent": intent,
            "success": success,
            "message": message,
        }

        if new_input:
            payload["new_input"] = new_input

        return AgentResponse(
            request_id=request.request_id,
            channel_id=request.channel_id,
            ok=True,
            payload=payload,
            metadata=request.metadata,
        )

    def _has_valid_model_config(self) -> bool:
        """检查是否有有效的模型配置."""
        # 检查环境变量中是否有 API_KEY
        if os.getenv("API_KEY"):
            return True

        # 检查实例的配置
        if self._instance is not None and hasattr(self._instance, "_config"):
            config = self._instance._config
            if hasattr(config, "model_client_config") and isinstance(config.model_client_config, dict):
                mcc = config.model_client_config
                api_key = mcc.get("api_key", "")
                if api_key:
                    return True

        return False

    async def _handle_user_answer(self, request: AgentRequest) -> AgentResponse:
        """Handle chat.user_answer request, route user answer to evolution approval Future."""
        request_id = request.params.get("request_id", "") if isinstance(request.params, dict) else ""
        answers = request.params.get("answers", []) if isinstance(request.params, dict) else []
        resolved = False
        if self._instance is not None:
            resolved = self._instance.resolve_evolution_approval(request_id, answers)
        return AgentResponse(
            request_id=request.request_id,
            channel_id=request.channel_id,
            ok=True,
            payload={"accepted": True, "resolved": resolved},
            metadata=request.metadata,
        )

    def _get_session_id(self, request: AgentRequest) -> str:
        """获取 session_id，默认为 'default'."""
        return request.session_id or "default"

    async def _cancel_session_task(self, session_id: str, log_msg_prefix: str = "") -> None:
        """取消指定 session 的非流式任务."""
        task = self._session_tasks.get(session_id)
        if task is not None and not task.done():
            logger.info(
                "[JiuWenClaw] %s取消 session 非流式任务: session_id=%s",
                log_msg_prefix, session_id,
            )
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
            self._session_tasks[session_id] = None

    async def _cancel_all_session_tasks(self, log_msg_prefix: str = "") -> None:
        """取消所有 session 的非流式任务."""
        for session_id in list(self._session_tasks.keys()):
            await self._cancel_session_task(session_id, log_msg_prefix)

    async def _ensure_session_processor(self, session_id: str) -> None:
        """确保 session 的任务处理器在运行."""
        if session_id not in self._session_processors or self._session_processors[session_id].done():
            # 创建新的优先级队列和计数器
            self._session_queues[session_id] = asyncio.PriorityQueue()
            self._session_priorities[session_id] = 0

            # 创建任务处理器
            async def process_session_queue():
                """处理 session 任务队列（先进后出执行，新任务优先）."""
                queue = self._session_queues[session_id]
                while True:
                    try:
                        # 从队列获取任务（优先级高的先执行）
                        priority, task_func = await queue.get()
                        if task_func is None:  # 信号：关闭队列
                            break

                        # 执行任务
                        self._session_tasks[session_id] = asyncio.create_task(task_func())
                        try:
                            await self._session_tasks[session_id]
                        finally:
                            self._session_tasks[session_id] = None
                            queue.task_done()

                    except asyncio.CancelledError:
                        logger.info("[JiuWenClaw] Session 任务处理器被取消: session_id=%s", session_id)
                        break
                    except Exception as e:
                        logger.error("[JiuWenClaw] Session 任务处理器异常: %s", e)

                # 清理
                self._session_queues.pop(session_id, None)
                self._session_priorities.pop(session_id, None)
                self._session_tasks.pop(session_id, None)
                self._session_processors.pop(session_id, None)
                logger.info("[JiuWenClaw] Session 任务处理器已关闭: session_id=%s", session_id)

            self._session_processors[session_id] = asyncio.create_task(process_session_queue())

    async def process_message(self, request: AgentRequest) -> AgentResponse:
        """调用 Runner.run_agent 处理请求，返回完整响应.

        支持多 session 并发执行，同 session 内任务按先进先出顺序执行.
        """
        # Interrupt 请求路由
        if request.req_method == ReqMethod.CHAT_CANCEL:
            return await self.process_interrupt(request)

        # User answer routing (evolution approval keep/undo)
        if request.req_method == ReqMethod.CHAT_ANSWER:
            return await self._handle_user_answer(request)

        # Heartbeat 处理
        if "heartbeat" in request.params:
            # todo 修复目录
            heartbeat_md = USER_WORKSPACE_DIR / "workspace" / "HEARTBEAT.md"
            if not os.path.isfile(heartbeat_md):
                # 无自定义任务，短路返回
                logger.debug("[JiuWenClaw] heartbeat OK (no HEARTBEAT.md): request_id=%s", request.request_id)
                return AgentResponse(
                    request_id=request.request_id,
                    channel_id=request.channel_id,
                    ok=True,
                    payload={"heartbeat": "HEARTBEAT_OK"},
                    metadata=request.metadata,
                )
            # 读取 HEARTBEAT.md，拼接为任务提示词，走正常 chat 流程
            task_list = []
            try:
                with open(heartbeat_md, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                    for line in lines:
                        line = line.strip()
                        if line != "":
                            if not line.startswith("<!--"):
                                task_list.append(line)

            except Exception as exc:
                logger.warning("[JiuWenClaw] 读取 HEARTBEAT.md 失败: %s", exc)
                return AgentResponse(
                    request_id=request.request_id,
                    channel_id=request.channel_id,
                    ok=True,
                    payload={"heartbeat": "HEARTBEAT_OK"},
                    metadata=request.metadata,
                )
            if not task_list:
                logger.debug("[JiuWenClaw] HEARTBEAT.md 为空，短路返回")
                return AgentResponse(
                    request_id=request.request_id,
                    channel_id=request.channel_id,
                    ok=True,
                    payload={"heartbeat": "HEARTBEAT_OK"},
                    metadata=request.metadata,
                )
            task_list = "\n".join(task_list)
            query = f"请检查下面用户遗留给你的任务项，并按照顺序完成所有待办事项，并将结果以markdown文件保存在你的工作目录下：\n{task_list}"
            request.params["query"] = query
            logger.info(
                "[JiuWenClaw] heartbeat 触发 HEARTBEAT.md 任务: request_id=%s session_id=%s",
                request.request_id, request.session_id,
            )

        # Skills 请求委托给 SkillManager
        if request.req_method in _SKILL_ROUTES:
            handler_name = _SKILL_ROUTES[request.req_method]
            handler = getattr(self._skill_manager, handler_name)
            try:
                payload = await handler(request.params)
                if handler_name in ["handle_skills_install", "handle_skills_uninstall", "handle_skills_import_local"]:
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

        # 原有 chat 逻辑
        if self._instance is None:
            raise RuntimeError("JiuWenClaw 未初始化，请先调用 create_instance()")

        # 检查模型配置
        if not self._has_valid_model_config():
            return AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": "模型未正确配置，请先配置模型信息"},
                metadata=request.metadata,
            )

        session_id = self._get_session_id(request)

        # 确保 session 的任务处理器在运行
        await self._ensure_session_processor(session_id)

        logger.info(
            "[JiuWenClaw] 处理请求: request_id=%s channel_id=%s session_id=%s",
            request.request_id, request.channel_id, session_id,
        )
        inputs = {
            "conversation_id": request.session_id,
            "query": request.params.get("query", ""),
        }

        query = request.params.get("query", "")
        if self._compaction_manager:
            self._compaction_manager.add_message("user", query)

            memory_mgr = await get_memory_manager(
                agent_id=self._agent_name,
                workspace_dir=self._workspace_dir
            )
            if memory_mgr:
                await self._compaction_manager.check_and_compact(memory_mgr)

        # 创建任务函数并放入队列（先进后出：新任务优先）
        # 使用 Future 来获取结果
        result_future = asyncio.get_event_loop().create_future()

        async def run_agent_task():
            try:
                await self._register_runtime_tools(request.session_id, request.params.get("mode", "plan"))
                return await Runner.run_agent(agent=self._instance, inputs=inputs)
            except asyncio.CancelledError:
                logger.info("[JiuWenClaw] Agent 任务被取消: request_id=%s session_id=%s", request.request_id, session_id)
                raise
            except Exception as e:
                logger.error("[JiuWenClaw] Agent 任务执行异常: %s", e)
                raise

        # 包装任务，完成后将结果放入 future
        async def task_wrapper():
            try:
                result = await run_agent_task()
                result_future.set_result(result)
            except Exception as e:
                result_future.set_exception(e)

        # 使用负数优先级实现先进后出（新请求优先级更高）
        # 每次递减，新请求的优先级更高
        self._session_priorities[session_id] -= 1
        priority = self._session_priorities[session_id]
        await self._session_queues[session_id].put((priority, task_wrapper))

        # 等待任务完成
        try:
            result = await result_future
        except asyncio.CancelledError:
            # 当前请求被取消，但队列中的任务会继续执行
            raise
        except Exception as e:
            logger.error("[JiuWenClaw] 任务执行失败: %s", e)
            raise

        content = result if isinstance(result, (str, dict)) else str(result)

        if self._compaction_manager and content:
            if isinstance(content, dict):
                content_str = content.get("output", str(content))
            else:
                content_str = str(content)
            self._compaction_manager.add_message("assistant", content_str)

        return AgentResponse(
            request_id=request.request_id,
            channel_id=request.channel_id,
            ok=True,
            payload={"content": content},
            metadata=request.metadata,
        )

    async def process_message_stream(
            self, request: AgentRequest
    ) -> AsyncIterator[AgentResponseChunk]:
        """流式处理：通过 JiuClawReActAgent.stream() 逐条返回 chunk.

        支持多 session 并发执行，同 session 内任务按先进后出顺序执行.

        OutputSchema 事件类型映射:
            content_chunk → chat.delta   (逐字流式文本)
            answer        → chat.final   (最终完整回答)
            tool_call     → chat.tool_call
            tool_result   → chat.tool_result
            error         → chat.error
            thinking      → chat.processing_status
            todo.updated  → todo.updated  (todo 列表变更通知)
        """
        if self._instance is None:
            raise RuntimeError("JiuWenClaw 未初始化，请先调用 create_instance()")

        # 检查模型配置
        if not self._has_valid_model_config():
            yield AgentResponseChunk(
                request_id=request.request_id,
                channel_id=request.channel_id,
                payload={"event_type": "chat.error", "error": "模型未正确配置，请先配置模型信息"},
                is_complete=True,
            )
            return

        session_id = self._get_session_id(request)
        await self._ensure_session_processor(session_id)

        logger.info(
            "[JiuWenClaw] 处理流式请求: request_id=%s channel_id=%s session_id=%s",
            request.request_id, request.channel_id, session_id,
        )

        inputs = {
            "conversation_id": request.session_id,
            "query": request.params.get("query", ""),
        }

        # supplement 任务：读取现有 todo 待办，拼入 query 让 agent 知道有未完成的任务
        if request.params.get("is_supplement"):
            try:
                todo_toolkit = TodoToolkit(session_id=request.session_id)
                tasks = todo_toolkit._load_tasks()
                pending = [t for t in tasks if t.status.value != "completed"]
                if pending:
                    todo_summary = "\n".join(
                        f"  - [{t.idx}] {t.tasks}" for t in pending
                    )
                    original_query = inputs["query"]
                    inputs["query"] = (
                        f"当前待办列表中有以下未完成的任务：\n{todo_summary}\n\n"
                        f"用户追加了新需求：{original_query}\n\n"
                        f"请先使用 todo_insert 将新需求添加到待办列表，"
                        f"然后按顺序执行所有未完成的待办任务。"
                    )
                    logger.info(
                        "[JiuWenClaw] supplement: 已将 %d 个待办项拼入 query, session_id=%s",
                        len(pending), request.session_id,
                    )
            except Exception as exc:
                logger.warning("[JiuWenClaw] supplement: 读取 todo 列表失败: %s", exc)

        query = request.params.get("query", "")
        if self._compaction_manager:
            self._compaction_manager.add_message("user", query)
            memory_mgr = await get_memory_manager(
                agent_id=self._agent_name,
                workspace_dir=self._workspace_dir
            )
            if memory_mgr:
                await self._compaction_manager.check_and_compact(memory_mgr)

        rid = request.request_id
        cid = request.channel_id

        # 创建流式输出队列
        stream_queue = asyncio.Queue()
        stream_done = asyncio.Event()

        # 创建流式任务函数
        async def run_stream_task():
            """执行流式任务，将产生的 chunk 放入队列."""
            try:
                await self._register_runtime_tools(request.session_id, request.params.get("mode", "plan"))
                async for chunk in Runner.run_agent_streaming(self._instance, inputs):
                    parsed = self._parse_stream_chunk(chunk)
                    if parsed is None:
                        continue
                    await stream_queue.put(("chunk", parsed))
            except asyncio.CancelledError:
                logger.info("[JiuWenClaw] 流式任务被取消: request_id=%s session_id=%s", rid, session_id)
                await stream_queue.put(("error", asyncio.CancelledError()))
            except Exception as exc:
                logger.exception("[JiuWenClaw] 流式任务异常: %s", exc)
                await stream_queue.put(("error", exc))
            finally:
                stream_done.set()

        # 包装任务
        async def task_wrapper():
            await run_stream_task()

        # 使用负数优先级实现先进后出（新请求优先级更高）
        self._session_priorities[session_id] -= 1
        priority = self._session_priorities[session_id]
        await self._session_queues[session_id].put((priority, task_wrapper))

        # 从流式队列中读取并 yield 结果
        try:
            while not stream_done.is_set() or not stream_queue.empty():
                try:
                    # 使用 timeout 避免永久阻塞
                    item = await asyncio.wait_for(stream_queue.get(), timeout=0.1)
                except asyncio.TimeoutError:
                    continue

                event_type, data = item

                if event_type == "error":
                    if isinstance(data, asyncio.CancelledError):
                        logger.info("[JiuWenClaw] 流式处理被中断: request_id=%s", rid)
                        raise data
                    yield AgentResponseChunk(
                        request_id=rid,
                        channel_id=cid,
                        payload={"event_type": "chat.error", "error": str(data)},
                        is_complete=False,
                    )
                else:
                    yield AgentResponseChunk(
                        request_id=rid,
                        channel_id=cid,
                        payload=data,
                        is_complete=False,
                    )
        except asyncio.CancelledError:
            logger.info("[JiuWenClaw] 流式处理被中断: request_id=%s", rid)
            raise

        # 终止 chunk
        yield AgentResponseChunk(
            request_id=rid,
            channel_id=cid,
            payload=None,
            is_complete=True,
        )

    # ------------------------------------------------------------------
    # OutputSchema 解析
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_stream_chunk(chunk) -> dict | None:
        """将 SDK OutputSchema 转为前端可消费的 payload dict.

        参考 openjiuwen_agent._parse_stream_chunk 的处理逻辑，
        过滤掉 traceId / invokeId 等调试帧，按 type 分类提取数据。

        Returns:
            dict  – 含 event_type 的 payload，或 None（需跳过的帧）。
        """
        try:
            # OutputSchema 对象：有 type + payload
            if hasattr(chunk, "type") and hasattr(chunk, "payload"):
                chunk_type = chunk.type
                payload = chunk.payload

                if chunk_type == "content_chunk":
                    content = (
                        payload.get("content", "")
                        if isinstance(payload, dict)
                        else str(payload)
                    )
                    if not content:
                        return None
                    return {"event_type": "chat.delta", "content": content}

                if chunk_type == "answer":
                    if isinstance(payload, dict):
                        if payload.get("result_type") == "error":
                            return {
                                "event_type": "chat.error",
                                "error": payload.get("output", "未知错误"),
                            }
                        output = payload.get("output", {})
                        content = (
                            output.get("output", "")
                            if isinstance(output, dict)
                            else str(output)
                        )
                        # Check if this is a chunked answer (first chunk)
                        is_chunked = (
                            output.get("chunked", False)
                            if isinstance(output, dict)
                            else False
                        )
                    else:
                        content = str(payload)
                        is_chunked = False
                    if not content:
                        return None
                    # For chunked answers, return as delta (will be accumulated)
                    # For non-chunked, return as final
                    if is_chunked:
                        return {"event_type": "chat.delta", "content": content}
                    return {"event_type": "chat.final", "content": content}

                if chunk_type == "tool_call":
                    tool_info = (
                        payload.get("tool_call", payload)
                        if isinstance(payload, dict)
                        else payload
                    )
                    return {"event_type": "chat.tool_call", "tool_call": tool_info}

                if chunk_type == "tool_result":
                    if isinstance(payload, dict):
                        result_info = payload.get("tool_result", payload)
                        result_payload = {
                            "result": result_info.get("result", str(result_info))
                            if isinstance(result_info, dict)
                            else str(result_info),
                        }
                        if isinstance(result_info, dict):
                            result_payload["tool_name"] = (
                                    result_info.get("tool_name")
                                    or result_info.get("name")
                            )
                            result_payload["tool_call_id"] = (
                                    result_info.get("tool_call_id")
                                    or result_info.get("toolCallId")
                            )
                    else:
                        result_payload = {"result": str(payload)}
                    return {
                        "event_type": "chat.tool_result",
                        **result_payload,
                    }

                if chunk_type == "error":
                    error_msg = (
                        payload.get("error", str(payload))
                        if isinstance(payload, dict)
                        else str(payload)
                    )
                    return {"event_type": "chat.error", "error": error_msg}

                if chunk_type == "thinking":
                    return {
                        "event_type": "chat.processing_status",
                        "is_processing": True,
                        "current_task": "thinking",
                    }

                if chunk_type == "todo.updated":
                    todos = (
                        payload.get("todos", [])
                        if isinstance(payload, dict)
                        else []
                    )
                    return {"event_type": "todo.updated", "todos": todos}

                if chunk_type == "context.compressed":
                    if isinstance(payload, dict):
                        return {
                            "event_type": "context.compressed",
                            "rate": payload.get("rate", 0),
                            "before_compressed": payload.get("before_compressed"),
                            "after_compressed": payload.get("after_compressed"),
                        }
                    return {"event_type": "context.compressed", "rate": 0}

                if chunk_type == "chat.ask_user_question":
                    return {
                        "event_type": "chat.ask_user_question",
                        **(payload if isinstance(payload, dict) else {}),
                    }

                # 未知 type：过滤调试帧，保留有内容的
                if isinstance(payload, dict):
                    if "traceId" in payload or "invokeId" in payload:
                        return None
                    content = payload.get("content") or payload.get("output")
                    if not content:
                        return None
                else:
                    content = str(payload)
                return {"event_type": "chat.delta", "content": content}

            # 普通 dict
            if isinstance(chunk, dict):
                if "traceId" in chunk or "invokeId" in chunk:
                    return None
                if chunk.get("result_type") == "error":
                    return {
                        "event_type": "chat.error",
                        "error": chunk.get("output", "未知错误"),
                    }
                output = chunk.get("output", "")
                if output:
                    return {"event_type": "chat.delta", "content": str(output)}
                return None

        except Exception:
            logger.debug("[_parse_stream_chunk] 解析异常", exc_info=True)

        return None

    def _prepare_instance_by_session(self):
        pass