# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""JiuWenClaw - 基于 openjiuwen ReActAgent 的 IAgentServer 实现."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
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

from jiuwenclaw.agentserver.tools.multi_session_toolkits import MultiSessionToolkit
from jiuwenclaw.agentserver.tools.send_file_to_user import SendFileToolkit
from jiuwenclaw.agentserver.prompt_builder import build_system_prompt, build_user_prompt
from jiuwenclaw.agentserver.gateway_push import GatewayPushTransport
from jiuwenclaw.agentserver.tools.cron_tools import CronToolRoute, CronTools

from jiuwenclaw.utils import (
    get_agent_root_dir,
    get_agent_home_dir,
    get_checkpoint_dir,
    get_env_file,
    get_workspace_dir,
)
from jiuwenclaw.config import get_config, resolve_env_vars
from jiuwenclaw.agentserver.react_agent import JiuClawReActAgent
from jiuwenclaw.agentserver.permissions.checker import TOOL_PERMISSION_CHANNEL_ID
from jiuwenclaw.schema.hook_event import AgentServerHookEvents
from jiuwenclaw.schema.hooks_context import MemoryHookContext
from jiuwenclaw.agentserver.tools.browser_tools import register_browser_runtime_mcp_server
from jiuwenclaw.agentserver.tools.audio_tools import (
    audio_question_answering,
    audio_metadata,
)
from jiuwenclaw.agentserver.tools.image_tools import visual_question_answering
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
from jiuwenclaw.agentserver.tools.task_tools import (
    get_task_tools,
    _is_task_memory_enabled,
)
from jiuwenclaw.agentserver.tools.video_tools import video_understanding
from jiuwenclaw.agentserver.tools.deepresearch_tools import get_deepresearch_tools
from jiuwenclaw.agentserver.tools.deepresearch_task_manager import DeepResearchTaskManager
from jiuwenclaw.agentserver.tools.xiaoyi_phone_tools import (
    get_user_location,
    create_note, search_notes, modify_note,
    create_calendar_event, search_calendar_event,
    search_contact,
    search_photo_gallery, upload_photo,
    search_file, upload_file,
    call_phone,
    send_message, search_message,
    create_alarm, search_alarms, modify_alarm, delete_alarm,
    xiaoyi_collection,
    image_reading,
    xiaoyi_gui_agent,
)
from jiuwenclaw.agentserver.tools.multimodal_config import (
    apply_audio_model_config_from_yaml,
    apply_vision_model_config_from_yaml,
    apply_video_model_config_from_yaml,
)
from jiuwenclaw.agentserver.memory.compaction import ContextCompactionManager
from jiuwenclaw.agentserver.memory.config import clear_config_cache, get_memory_mode
from jiuwenclaw.agentserver.memory import clear_memory_manager_cache
from jiuwenclaw.agentserver.permissions import (
    init_permission_engine,
    get_permission_engine,
    PermissionLevel,
)
from jiuwenclaw.agentserver.skill_manager import SkillManager, _get_skills_dir
from jiuwenclaw.agentserver.cli_file_service import CLIFileService
from jiuwenclaw.evolution.service import EvolutionService
from jiuwenclaw.schema.agent import AgentRequest, AgentResponse, AgentResponseChunk
from jiuwenclaw.agentserver.memory import get_memory_manager
from jiuwenclaw.agentserver.session_history import append_history_record
from jiuwenclaw.schema.message import ReqMethod
from jiuwenclaw.local_env_config import set_local_config

load_dotenv(dotenv_path=get_env_file())

logger = logging.getLogger(__name__)


@dataclass
class RuntimeToolContext:
    """运行时工具注册上下文，封装_register_runtime_tools的相关参数."""
    session_id: str | None
    channel_id: str | None
    request_id: str | None
    mode: str = "plan"
    request_params: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


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

_CLI_FILE_ROUTES: dict[ReqMethod, str] = {
    ReqMethod.CLI_FILE_VIEW: "handle_cli_file_view",
    ReqMethod.CLI_FILE_LIST: "handle_cli_file_list",
}


class JiuWenClaw:
    """基于 openJiuwen ReActAgent 的 AgentServer 实现."""

    def __init__(self, gateway_push: GatewayPushTransport | None = None) -> None:
        self._instance: JiuClawReActAgent | None = None
        self._skill_manager = SkillManager()
        self._skill_manager.set_skillnet_install_complete_hook(self.create_instance)
        self._session_tasks: dict[str, asyncio.Task] = {}  # session_id -> running_task
        self._session_priorities: dict[str, int] = {}  # session_id -> 优先级计数器（用于先进后出）
        self._session_queues: dict[str, asyncio.PriorityQueue] = {}  # session_id -> 优先队列
        self._session_processors: dict[str, asyncio.Task] = {}  # session_id -> processor_task
        # Memory system expects workspace_dir/layout:
        # - workspace_dir/memory/MEMORY.md + USER.md
        # - workspace_dir/memory/memory.db (SQLite vector index)
        # Therefore we set workspace_dir to agent root, not agent/workspace.
        self._workspace_dir: str = str(get_agent_root_dir())
        self._agent_name: str = "main_agent"
        self._compaction_manager: ContextCompactionManager | None = None
        self._browser_mcp_registered: bool = False
        self._vision_mcp_registered: bool = False
        self._audio_mcp_registered: bool = False
        self._memory_tools_registered: bool = False
        self._task_memory_tools_registered: bool = False
        self._mcp_tools_registered: bool = False
        self._video_tool_registered: bool = False
        self._xiaoyi_phone_tools_registered: bool = False
        self._deepresearch_tools_registered: bool = False
        self._deepresearch_task_manager: DeepResearchTaskManager | None = None
        self._todo_tool_sessions_registered: set[str] = set()
        self._sysop_card_id: str | None = None

        self._session_tool = None
        self._cron_tools = CronTools(gateway_push=gateway_push)
        self._cli_file_service = CLIFileService()

    @staticmethod
    async def set_checkpoint():
        try:
            PersistenceCheckpointerProvider()
            checkpoint_path = get_checkpoint_dir()
            checkpointer = await CheckpointerFactory.create(
                CheckpointerConfig(
                    type="persistence",
                    conf={"db_type": "sqlite", "db_path": str(checkpoint_path / "checkpoint")},
                )
            )
            CheckpointerFactory.set_default_checkpointer(checkpointer)
        except Exception as e:
            logger.error(("[JiuWenClaw] fail to setup checkpoint due to: %s", e))

    async def _load_react_config(self, config):
        # 提取 agent_name，如果不存在则使用默认值
        react_config = config.get("react", {}).copy()
        agent_name = react_config.pop("agent_name", "main_agent")
        self._agent_name = agent_name

        # 处理 model_client_config：确保包含必需字段
        model_configs = config.get("models", {})
        if not isinstance(model_configs, dict):
            model_configs = {}
        else:
            model_configs = model_configs.copy()
        memory_mode = get_memory_mode(config)
        react_config = {**react_config, **model_configs.get("default", {}).copy(), "prompt_template": [
            {"role": "system", "content": await build_system_prompt(
                mode="plan",
                language=config.get("preferred_language", "en"),
                channel="web",
                memory_mode=memory_mode,
            )}
        ]}

        # 创建 ReActAgentConfig
        agent_config = ReActAgentConfig(**react_config)

        context_engine_config = react_config.get('context_engine_config', {}).copy()

        if context_engine_config.get("enabled", False):
            message_offloader_config = context_engine_config.get("message_offloader_config", {}).copy()
            dialogue_compressor_config = context_engine_config.get("dialogue_compressor_config", {}).copy()
            # 上下文压缩卸载
            model_name = (model_configs
                          .get("default", {})
                          .get("model_client_config", {})
                          .get("model_name", "default"))
            processors = [
                (
                    "MessageOffloader",
                    MessageOffloaderConfig(
                        tokens_threshold=message_offloader_config.get("tokens_threshold", 5000),
                        large_message_threshold=message_offloader_config.get("large_message_threshold", 20000),
                        trim_size=message_offloader_config.get("trim_size", 5000),
                        offload_message_type=["tool"],
                        keep_last_round=message_offloader_config.get("keep_last_round", False),
                    )
                ),
                (
                    "DialogueCompressor",
                    DialogueCompressorConfig(
                        tokens_threshold=dialogue_compressor_config.get("tokens_threshold", 100000),
                        model=ModelRequestConfig(
                            model=model_name
                        ),
                        model_client=model_configs.get("default", {}).get("model_client_config", {}),
                        keep_last_round=dialogue_compressor_config.get("keep_last_round", False),
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
                - workspace_dir: 工作区目录，默认 "agent"（memory 落在 agent/memory 下）。
                - 其余字段透传给 ReActAgentConfig。
        """
        await self.set_checkpoint()

        config_base = get_config()
        apply_video_model_config_from_yaml(config_base)
        apply_audio_model_config_from_yaml(config_base)
        apply_vision_model_config_from_yaml(config_base)
        agent_config = await self._load_react_config(config_base)

        sysop_card_id: str | None = None
        try:
            sysop_card = SysOperationCard(
                mode=OperationMode.LOCAL,
                # Scope sys_operation to agent root so skills/memory/home/workspace are all accessible.
                work_config=LocalWorkConfig(work_dir=str(get_agent_root_dir())),
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
                await self._instance.register_skill(str(_get_skills_dir()))
            except Exception as exc:
                logger.warning("[JiuWenClaw] register_skill failed, continue without skills: %s", exc)

            # Register EvolutionService (enable evolution feature)
            evolution_cfg: dict = config_base.get("react", {}).pop("evolution", {})
            evolution_enabled: bool = evolution_cfg.get("enabled", False)

            # 检查是否有有效的模型配置（api_key 或 client_provider）
            has_valid_model_config = False
            models_config = config_base.get("models")
            if not isinstance(models_config, dict):
                models_config = {}
            default_models_config = models_config.get("default")
            if not isinstance(default_models_config, dict):
                default_models_config = {}
            if isinstance(default_models_config.get("model_client_config"), dict):
                mcc = default_models_config.get("model_client_config")
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
                evo_service = EvolutionService(
                    llm=self._instance._get_llm(),
                    model=agent_config.model_name,
                    skills_base_dir=str(_get_skills_dir()),
                    auto_scan=evolution_auto_scan,
                )
                self._instance.set_evolution_service(evo_service)
                logger.info("[JiuWenClaw] Evolution has been enabled: auto_scan=%s", evolution_auto_scan)
            elif evolution_enabled and not has_valid_model_config:
                logger.warning("[JiuWenClaw] Evolution is enabled but skipped: no valid model API key configured")
        else:
            logger.warning("[JiuWenClaw] ReActAgent has no _skill_util; skip skill registration.")

        memory_mode = get_memory_mode(config_base)
        if memory_mode == "local":
            await init_memory_manager_async(
                workspace_dir=self._workspace_dir,
                agent_id=self._agent_name,
            )
            for tool in [memory_search, memory_get, write_memory, edit_memory, read_memory]:
                Runner.resource_mgr.add_tool(tool)
                self._instance.ability_manager.add(tool.card)
            self._memory_tools_registered = True
        else:
            self._memory_tools_registered = False

        # add task memory tools (TaskMemoryService skill)
        if _is_task_memory_enabled():
            try:
                for tool in get_task_tools():
                    Runner.resource_mgr.add_tool(tool)
                    self._instance.ability_manager.add(tool.card)
                self._task_memory_tools_registered = True
                logger.info("[JiuWenClaw] task memory tools registered")
            except Exception as exc:
                logger.warning("[JiuWenClaw] task memory tools registration failed: %s", exc)

        # add video_understanding tool
        has_video_key = any([
            os.environ.get("VIDEO_API_KEY"),
            os.environ.get("ZHIPU_API_KEY"),
        ])
        if has_video_key:
            try:
                if not Runner.resource_mgr.get_tool(video_understanding.card.id):
                    Runner.resource_mgr.add_tool(video_understanding)
                self._instance.ability_manager.add(video_understanding.card)
                self._video_tool_registered = True
            except Exception as exc:
                self._video_tool_registered = False
                logger.warning("[JiuWenClaw] video_understanding tool registration failed: %s", exc)

        for mcp_tool in get_mcp_tools():
            Runner.resource_mgr.add_tool(mcp_tool)
            self._instance.ability_manager.add(mcp_tool.card)
        self._mcp_tools_registered = True

        if memory_mode == "local" and self._compaction_manager is None:
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
        elif memory_mode != "local":
            self._compaction_manager = None

        try:
            self._browser_mcp_registered = await register_browser_runtime_mcp_server(
                self._instance,
                tag=f"agent.{self._agent_name}",
            )
        except Exception as exc:
            logger.warning("[JiuWenClaw] browser MCP registration skipped: %s", exc)

        # add vision tools (直接注册方式)
        has_vision_key = any([
            os.environ.get("VISION_API_KEY"),
            os.environ.get("API_KEY"),
            os.environ.get("GEMINI_API_KEY"),
        ])
        if has_vision_key:
            try:
                for tool in [visual_question_answering]:
                    Runner.resource_mgr.add_tool(tool)
                    self._instance.ability_manager.add(tool.card)
                self._vision_mcp_registered = True
                logger.info("[JiuWenClaw] vision tools registered successfully")
            except Exception as exc:
                logger.warning("[JiuWenClaw] vision tools registration skipped: %s", exc)

        # add audio tools (直接注册方式)
        has_audio_key = any([
            os.environ.get("AUDIO_API_KEY"),
            os.environ.get("API_KEY"),
        ])
        has_acr_key = all([
            os.environ.get("ACR_ACCESS_KEY"),
            os.environ.get("ACR_ACCESS_SECRET"),
        ])
        audio_tools_to_register = []
        if has_audio_key:
            audio_tools_to_register.append(audio_question_answering)
        if has_acr_key:
            audio_tools_to_register.append(audio_metadata)
        
        if audio_tools_to_register:
            try:
                for tool in audio_tools_to_register:
                    Runner.resource_mgr.add_tool(tool)
                    self._instance.ability_manager.add_tool(tool.card)
                self._audio_mcp_registered = True
                logger.info("[JiuWenClaw] audio tools registered successfully")
            except Exception as exc:
                logger.warning("[JiuWenClaw] audio tools registration skipped: %s", exc)

        # add device-side plugins (xiaoyi phone tools)
        config_base = get_config()
        channels_cfg = config_base.get("channels", {})
        xiaoyi_cfg = channels_cfg.get("xiaoyi", {})
        xiaoyi_phone_tools_enabled = xiaoyi_cfg.get("phone_tools_enabled", False)

        if xiaoyi_phone_tools_enabled:
            try:
                # 批量注册所有设备侧工具
                phone_tools = [
                    get_user_location,
                    create_note, search_notes, modify_note,
                    create_calendar_event, search_calendar_event,
                    search_contact,
                    search_photo_gallery, upload_photo,
                    search_file, upload_file,
                    call_phone,
                    send_message, search_message,
                    create_alarm, search_alarms, modify_alarm, delete_alarm,
                    xiaoyi_collection,
                    image_reading,
                    xiaoyi_gui_agent,
                ]

                for tool in phone_tools:
                    try:
                        Runner.resource_mgr.add_tool(tool)
                        self._instance.ability_manager.add(tool.card)
                    except Exception as tool_exc:
                        logger.warning(f"[JiuWenClaw] Failed to register tool {tool.card.name}: {tool_exc}")

                self._xiaoyi_phone_tools_registered = True
                logger.info(f"[JiuWenClaw] {len(phone_tools)} xiaoyi phone tools registered successfully")
            except Exception as exc:
                logger.warning("[JiuWenClaw] xiaoyi phone tools registration skipped: %s", exc)
        else:
            logger.info("[JiuWenClaw] xiaoyi channel not enabled, skipping phone tools")

        # add deepresearch tools
        try:
            for tool in get_deepresearch_tools():
                Runner.resource_mgr.add_tool(tool)
                self._instance.ability_manager.add(tool.card)
            self._deepresearch_tools_registered = True
            logger.info("[JiuWenClaw] deepresearch tools registered successfully")
        except Exception as exc:
            self._deepresearch_tools_registered = False
            logger.warning("[JiuWenClaw] deepresearch tools registration failed: %s", exc)

        # add cron tools（路由由 push_cron_route 在每轮 run_* 任务内设置，见 ContextVar）
        try:
            for cron_tool in self._cron_tools.get_tools():
                Runner.resource_mgr.add_tool(cron_tool)
                self._instance.ability_manager.add(cron_tool.card)
        except Exception as exc:
            logger.error("[JiuWenClaw] 定时工具加载失败， reason=%s", exc)

        self._register_extension_tools()

        # ---- 权限引擎初始化 ----
        permissions_cfg = config_base.get("permissions", {})
        init_permission_engine(permissions_cfg)
        logger.info(
            "[JiuWenClaw] Permission engine initialized: enabled=%s",
            permissions_cfg.get("enabled", True),
        )
        logger.info("[JiuWenClaw] 初始化完成: agent_name=%s", self._agent_name)

    async def reload_agent_config(
        self,
        config_base: dict[str, Any] | None = None,
        env_overrides: dict[str, Any] | None = None,
    ) -> None:
        """热更新当前 Agent 配置。

        Args:
            config_base: 可选的完整配置快照；传入时优先使用它而不是读取本地 config.yaml。
            env_overrides: 可选的环境变量增量；仅覆盖请求中出现的 key。
        """
        if self._instance is None:
            raise RuntimeError("JiuWenClaw 未初始化，请先调用 create_instance()")

        clear_config_cache()
        clear_memory_manager_cache()

        if env_overrides is not None:
            if not isinstance(env_overrides, dict):
                raise TypeError("env_overrides must be a dict when provided")
            for env_key, env_value in env_overrides.items():
                set_local_config(env_key, env_value)

        if config_base is None:
            config_base = get_config()
        elif not isinstance(config_base, dict):
            raise TypeError("config_base must be a dict when provided")
        else:
            config_base = resolve_env_vars(config_base)

        # 同步扩展配置到 ExtensionRegistry
        # Gateway 已解密 extension_security_configs，AgentServer 直接使用明文
        try:
            from jiuwenclaw.extensions.registry import ExtensionRegistry
            registry = ExtensionRegistry.get_instance()
            registry.update_config(config_base)
            logger.info("[JiuWenClaw] Extension config synced to Registry")
        except Exception as exc:
            logger.warning("[JiuWenClaw] ExtensionRegistry update failed: %s", exc)

        apply_video_model_config_from_yaml(config_base)
        apply_audio_model_config_from_yaml(config_base)
        apply_vision_model_config_from_yaml(config_base)
        agent_config = await self._load_react_config(config_base)

        if self._sysop_card_id:
            agent_config.sys_operation_id = self._sysop_card_id

        if hasattr(self._instance, "_llm"):
            self._instance._llm = None
        self._instance.configure(agent_config)
        # Hot-update evolution service
        evo_svc = getattr(self._instance, "_evolution_service", None)
        if evo_svc is not None:
            new_llm = self._instance._get_llm()
            new_model = agent_config.model_name
            evo_svc.update_llm(new_llm, new_model)
            _env_auto_scan = os.getenv("EVOLUTION_AUTO_SCAN")
            if _env_auto_scan is not None:
                evo_svc.auto_scan = _env_auto_scan.lower() in ("true", "1", "yes")
        # 权限配置热更新
        permissions_cfg = config_base.get("permissions", {})
        try:
            engine = get_permission_engine()
            engine.update_config(permissions_cfg)
            logger.info("[JiuWenClaw] Permission config reloaded: enabled=%s", permissions_cfg.get("enabled", True))
        except Exception as exc:
            logger.warning("[JiuWenClaw] Permission config reload failed: %s", exc)
        logger.info("[JiuWenClaw] 配置已热更新，未重启进程")

    @staticmethod
    def _cron_tool_route_for_ctx(ctx: RuntimeToolContext) -> CronToolRoute:
        """与 _register_runtime_tools 中 channel / cron_context_channel 规则一致，供 push_cron_route 使用。"""
        channel = (ctx.channel_id or "").strip() or (
            (ctx.session_id or "").split("_")[0] if ctx.session_id else ""
        )
        try:
            from jiuwenclaw.gateway.cron.models import CronTargetChannel

            normalized_channel = channel.strip().lower()
            if normalized_channel.startswith("feishu_enterprise:"):
                cron_context_channel = normalized_channel
            else:
                allowed = {e.value for e in CronTargetChannel}
                if normalized_channel in allowed:
                    cron_context_channel = normalized_channel
                else:
                    cron_context_channel = CronTargetChannel.WEB.value
        except Exception:
            from jiuwenclaw.gateway.cron.models import CronTargetChannel

            cron_context_channel = CronTargetChannel.WEB.value

        if channel in ("heartbeat", "cron"):
            return CronToolRoute()
        return CronToolRoute(
            request_id=ctx.request_id or "",
            channel_id=cron_context_channel,
            session_id=ctx.session_id,
        )

    def _register_extension_tools(self) -> None:
        """将 ExtensionRegistry 登记的扩展本地工具挂到 Runner 与 ability_manager。"""
        if self._instance is None:
            return
        try:
            from jiuwenclaw.extensions.registry import ExtensionRegistry
            from jiuwenclaw.extensions.sdk.local_tool_builder import make_extension_tools

            ext_reg = ExtensionRegistry.get_instance()
            existing_ext_tool_names: set[str] = {
                ab.name
                for ab in self._instance.ability_manager.list()
                if isinstance(ab, ToolCard)
            }
            for ext_tool in make_extension_tools(ext_reg.extension_local_tool_entries):
                tname = ext_tool.card.name
                tid = ext_tool.card.id
                if tname in existing_ext_tool_names:
                    logger.warning(
                        "[JiuWenClaw] extension tool name conflicts with existing tool, skip: %s",
                        tname,
                    )
                    continue
                try:
                    if Runner.resource_mgr.get_tool(tid):
                        logger.warning(
                            "[JiuWenClaw] extension tool id already in resource_mgr, skip: %s",
                            tid,
                        )
                        continue
                except Exception as exc:
                    logger.debug(
                        "[JiuWenClaw] extension tool get_tool probe failed for %s, will try add: %s",
                        tid,
                        exc,
                    )
                try:
                    Runner.resource_mgr.add_tool(ext_tool)
                    self._instance.ability_manager.add(ext_tool.card)
                    existing_ext_tool_names.add(tname)
                    logger.info(
                        "[JiuWenClaw] extension tool add success, name : %s", tname,
                    )
                except Exception as exc:
                    logger.warning(
                        "[JiuWenClaw] extension tool register failed (%s): %s",
                        tname,
                        exc,
                    )
        except RuntimeError:
            logger.warning("[JiuWenClaw] ExtensionRegistry unavailable, skip extension tools")

    async def _register_runtime_tools(self, ctx: RuntimeToolContext) -> None:
        """Register per-request tools for current agent execution.
        
        Args:
            ctx: 运行时工具注册上下文，包含session_id、channel_id、request_id等参数
        """
        if self._instance is None:
            raise RuntimeError("JiuWenClaw 未初始化，请先调用 create_instance()")

        self._session_tool = None

        tool_list = self._instance.ability_manager.list()
        for tool in tool_list:
            if isinstance(tool, ToolCard):
                if tool.name.startswith("todo_"):
                    self._instance.ability_manager.remove(tool.name)
                elif tool.name.startswith("cron_"):
                    # 不在此移除：cron 工具固定注册在 ResourceMgr，路由由 ContextVar（push_cron_route）按 Task 隔离
                    continue
                elif tool.name.startswith("session_"):
                    self._instance.ability_manager.remove(tool.name)
                elif tool.name.startswith("send_file_to_user"):
                    self._instance.ability_manager.remove(tool.name)

        # channel：与 _cron_tool_route_for_ctx 一致（用于 prompt / send_file 等）；cron 路由本身已用 ContextVar 绑定
        channel = (ctx.channel_id or "").strip() or (
            (ctx.session_id or "").split("_")[0] if ctx.session_id else ""
        )

        logger.info(f"[JiuwenClaw] update tool and prompt for channel {channel}")

        config_base = get_config()
        send_file_tool_enabled = config_base.get("channels", {}).get(channel, {}).get("send_file_allowed", False)
        if send_file_tool_enabled:
            # Register send file toolkit
            send_file_toolkit = SendFileToolkit(
                request_id=ctx.request_id,
                session_id=ctx.session_id,
                channel_id=ctx.channel_id,
                metadata=ctx.metadata,
            )
            for tool in send_file_toolkit.get_tools():
                Runner.resource_mgr.add_tool(tool)
                self._instance.ability_manager.add(tool.card)

        # 小艺手机端插件(xiaoyi phone tools)未生效时重新加载
        channels_cfg = config_base.get("channels", {})
        xiaoyi_cfg = channels_cfg.get("xiaoyi", {})
        xiaoyi_phone_tools_enabled = xiaoyi_cfg.get("phone_tools_enabled", False)

        if xiaoyi_phone_tools_enabled and not self._xiaoyi_phone_tools_registered:
            try:
                phone_tools = [
                    get_user_location,
                    create_note, search_notes, modify_note,
                    create_calendar_event, search_calendar_event,
                    search_contact,
                    search_photo_gallery, upload_photo,
                    search_file, upload_file,
                    call_phone,
                    send_message, search_message,
                    create_alarm, search_alarms, modify_alarm, delete_alarm,
                    xiaoyi_collection,
                    image_reading,
                    xiaoyi_gui_agent,
                ]

                for tool in phone_tools:
                    try:
                        if not Runner.resource_mgr.get_tool(tool.card.id):
                            Runner.resource_mgr.add_tool(tool)
                            self._instance.ability_manager.add(tool.card)
                    except Exception as tool_exc:
                        logger.error(f"[JiuWenClaw] Tool {tool.card.name} may already exist: {tool_exc}")

            except Exception as exc:
                logger.warning(f"[JiuWenClaw] xiaoyi phone tools runtime registration skipped: {exc}")

        effective_session_id = ctx.session_id or "default"
        if ctx.mode == "plan":
            todo_toolkit = TodoToolkit(session_id=effective_session_id)
            for tool in todo_toolkit.get_tools():
                Runner.resource_mgr.add_tool(tool)
                self._instance.ability_manager.add(tool.card)
            self._todo_tool_sessions_registered.add(effective_session_id)
        else:
            config_base = get_config()
            session_toolkits = MultiSessionToolkit(
                session_id=effective_session_id,
                channel_id=ctx.channel_id,
                request_id=ctx.request_id,
                sub_agent_config=await self._load_react_config(config_base)
            )
            self._session_tool = session_toolkits
            for tool in session_toolkits.get_tools():
                Runner.resource_mgr.add_tool(tool)
                self._instance.ability_manager.add(tool.card)

        memory_mode = get_memory_mode(config_base)
        memory_block = ""
        if memory_mode == "local":
            if not self._memory_tools_registered:
                await init_memory_manager_async(
                    workspace_dir=self._workspace_dir,
                    agent_id=self._agent_name,
                )
                for tool in [memory_search, memory_get, write_memory, edit_memory, read_memory]:
                    Runner.resource_mgr.add_tool(tool)
                    self._instance.ability_manager.add(tool.card)
                self._memory_tools_registered = True
        else:
            self._memory_tools_registered = False
            mem_ctx = MemoryHookContext(
                session_id=ctx.session_id or "default",
                request_id=ctx.request_id or "",
                channel_id=ctx.channel_id,
                agent_name=self._agent_name,
                workspace_dir=self._workspace_dir,
                extra=ctx.request_params if ctx.request_params is not None else {},
            )
            # 插件需要延迟引用
            from jiuwenclaw.extensions.registry import ExtensionRegistry
            await ExtensionRegistry.get_instance().trigger(AgentServerHookEvents.MEMORY_BEFORE_CHAT, mem_ctx)
            memory_block = "\n\n".join(b for b in mem_ctx.memory_blocks if b)

        if not self._task_memory_tools_registered and _is_task_memory_enabled():
            try:
                for tool in get_task_tools():
                    if not Runner.resource_mgr.get_tool(tool.card.id):
                        Runner.resource_mgr.add_tool(tool)
                    self._instance.ability_manager.add(tool.card)
                self._task_memory_tools_registered = True
            except Exception as exc:
                logger.warning("[JiuWenClaw] ensure task memory tools failed: %s", exc)

        logger.info("[JiuWenClaw] Checking multimodal API keys...")
        
        video_api_key = os.environ.get("VIDEO_API_KEY")
        zhipu_api_key = os.environ.get("ZHIPU_API_KEY")
        has_video_key = any([
            video_api_key,
            zhipu_api_key,
        ])
        logger.info(
            "[JiuWenClaw] Video API key check: VIDEO_API_KEY=%s (valid=%s), ZHIPU_API_KEY=%s (valid=%s), "
            "has_video_key=%s",
            video_api_key[:20] + "..." if video_api_key else None,
            bool(video_api_key),
            zhipu_api_key[:20] + "..." if zhipu_api_key else None,
            bool(zhipu_api_key),
            has_video_key,
        )
        
        if has_video_key:
            try:
                if not Runner.resource_mgr.get_tool(video_understanding.card.id):
                    Runner.resource_mgr.add_tool(video_understanding)
                self._instance.ability_manager.add(video_understanding.card)
                logger.info("[JiuWenClaw] Registered video_understanding tool")
            except Exception as exc:
                logger.warning("[JiuWenClaw] ensure video_understanding tool failed: %s", exc)
        else:
            try:
                self._instance.ability_manager.remove(video_understanding.card.name)
                logger.info("[JiuWenClaw] Unregistered video_understanding tool")
            except Exception as exc:
                logger.debug("[JiuWenClaw] unregister video_understanding tool failed (tool may not exist): %s", exc)

        vision_api_key = os.environ.get("VISION_API_KEY")
        api_key = os.environ.get("API_KEY")
        gemini_api_key = os.environ.get("GEMINI_API_KEY")
        has_vision_key = any([
            vision_api_key,
            api_key,
            gemini_api_key,
        ])
        logger.info(
            "[JiuWenClaw] Vision API key check: VISION_API_KEY=%s (valid=%s), API_KEY=%s (valid=%s), "
            "GEMINI_API_KEY=%s (valid=%s), has_vision_key=%s",
            vision_api_key[:20] + "..." if vision_api_key else None,
            bool(vision_api_key),
            api_key[:20] + "..." if api_key else None,
            bool(api_key),
            gemini_api_key[:20] + "..." if gemini_api_key else None,
            bool(gemini_api_key),
            has_vision_key,
        )
        
        if has_vision_key:
            try:
                for tool in [visual_question_answering]:
                    if not Runner.resource_mgr.get_tool(tool.card.id):
                        Runner.resource_mgr.add_tool(tool)
                    self._instance.ability_manager.add(tool.card)
                logger.info("[JiuWenClaw] Registered vision tools")
            except Exception as exc:
                logger.warning("[JiuWenClaw] ensure vision tools failed: %s", exc)
        else:
            try:
                for tool in [visual_question_answering]:
                    self._instance.ability_manager.remove(tool.card.name)
                logger.info("[JiuWenClaw] Unregistered vision tools")
            except Exception as exc:
                logger.debug("[JiuWenClaw] unregister vision tools failed (tools may not exist): %s", exc)

        audio_api_key = os.environ.get("AUDIO_API_KEY")
        api_key_for_audio = os.environ.get("API_KEY")
        acr_access_key = os.environ.get("ACR_ACCESS_KEY")
        acr_access_secret = os.environ.get("ACR_ACCESS_SECRET")
        has_audio_key = any([
            audio_api_key,
            api_key_for_audio,
        ])
        has_acr_key = all([
            acr_access_key,
            acr_access_secret,
        ])
        logger.info(
            "[JiuWenClaw] Audio API key check: AUDIO_API_KEY=%s (valid=%s), API_KEY=%s (valid=%s), "
            "ACR_ACCESS_KEY=%s (valid=%s), ACR_ACCESS_SECRET=%s (valid=%s), has_audio_key=%s, has_acr_key=%s",
            audio_api_key[:20] + "..." if audio_api_key else None,
            bool(audio_api_key),
            api_key_for_audio[:20] + "..." if api_key_for_audio else None,
            bool(api_key_for_audio),
            acr_access_key[:20] + "..." if acr_access_key else None,
            bool(acr_access_key),
            acr_access_secret[:20] + "..." if acr_access_secret else None,
            bool(acr_access_secret),
            has_audio_key,
            has_acr_key,
        )
        audio_tools_to_register = []
        if has_audio_key:
            audio_tools_to_register.append(audio_question_answering)
        if has_acr_key:
            audio_tools_to_register.append(audio_metadata)
        
        if audio_tools_to_register:
            try:
                for tool in audio_tools_to_register:
                    if not Runner.resource_mgr.get_tool(tool.card.id):
                        Runner.resource_mgr.add_tool(tool)
                    self._instance.ability_manager.add(tool.card)
            except Exception as exc:
                logger.warning("[JiuWenClaw] ensure audio tools failed: %s", exc)
        else:
            try:
                for tool in [audio_question_answering, audio_metadata]:
                    self._instance.ability_manager.remove(tool.card.name)
            except Exception as exc:
                logger.debug("[JiuWenClaw] unregister audio tools failed (tools may not exist): %s", exc)

        # register deepresearch tools
        if not self._deepresearch_tools_registered:
            try:
                # 初始化 DeepResearchTaskManager（全局单例）
                if self._deepresearch_task_manager is None:
                    self._deepresearch_task_manager = await DeepResearchTaskManager.get_instance()
                    logger.info("[JiuWenClaw] Initialized DeepResearchTaskManager")

                for tool in get_deepresearch_tools():
                    if not Runner.resource_mgr.get_tool(tool.card.id):
                        Runner.resource_mgr.add_tool(tool)
                    self._instance.ability_manager.add(tool.card)
                self._deepresearch_tools_registered = True
                logger.info("[JiuWenClaw] Registered deepresearch tools")
            except Exception as exc:
                logger.warning("[JiuWenClaw] ensure deepresearch tools failed: %s", exc)

        current_mcp_tools = get_mcp_tools()
        current_mcp_tool_names = {tool.card.name for tool in current_mcp_tools}
        
        for mcp_tool in current_mcp_tools:
            try:
                if not Runner.resource_mgr.get_tool(mcp_tool.card.id):
                    Runner.resource_mgr.add_tool(mcp_tool)
                self._instance.ability_manager.add(mcp_tool.card)
            except Exception as exc:
                logger.warning("[JiuWenClaw] register MCP tool failed: %s", exc)
        
        all_mcp_tool_names = {"mcp_free_search", "mcp_paid_search", "mcp_fetch_webpage", "mcp_exec_command"}
        tools_to_remove = all_mcp_tool_names - current_mcp_tool_names
        for tool_name in tools_to_remove:
            try:
                self._instance.ability_manager.remove(tool_name)
            except Exception as exc:
                logger.debug("[JiuWenClaw] unregister MCP tool %s failed (tool may not exist): %s", tool_name, exc)

        system_prompt = await build_system_prompt(
            mode=ctx.mode,
            language=config_base.get("preferred_language", "zh"),
            channel=channel,
            memory_block=memory_block,
            memory_mode=memory_mode,
        )
        logger.debug(
            "[JiuWenClaw] system prompt built: memory_mode=%s channel=%s\n%s",
            memory_mode,
            channel,
            system_prompt,
        )
        self._instance._config.prompt_template = [{
            "role": "system",
            "content": system_prompt,
        }]

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

    async def _handle_cli_file_command(self, request: AgentRequest) -> AgentResponse:
        """处理 CLI 文件命令 (/view, /ls)"""
        self._cli_file_service.session_id = request.session_id
        
        params = request.params or {}
        path = params.get("path", "")
        cmd_params = params.get("params", {})
        
        if request.req_method == ReqMethod.CLI_FILE_VIEW:
            result = self._cli_file_service.handle_view_command(path, cmd_params)
        elif request.req_method == ReqMethod.CLI_FILE_LIST:
            result = self._cli_file_service.handle_ls_command(path)
        else:
            return AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": f"未知的 CLI 文件命令: {request.req_method}"},
                metadata=request.metadata,
            )
        
        return AgentResponse(
            request_id=request.request_id,
            channel_id=request.channel_id,
            ok=result.success,
            payload={
                "content": result.content,
                "error": result.error,
                "metadata": result.metadata,
            },
            metadata=request.metadata,
        )

    async def process_message(self, request: AgentRequest) -> AgentResponse:
        """调用 Runner.run_agent 处理请求，返回完整响应.

        支持多 session 并发执行，同 session 内任务按先进先出顺序执行.
        """
        # Interrupt 请求路由
        if request.req_method == ReqMethod.CHAT_CANCEL:
            return await self.process_interrupt(request)

        # User answer routing (evolution approval & permission approval)
        if request.req_method == ReqMethod.CHAT_ANSWER:
            return await self._handle_user_answer(request)

        # Heartbeat 处理
        if "heartbeat" in request.params:
            # todo 修复目录
            heartbeat_md = get_agent_home_dir() / "HEARTBEAT.md"
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
                _reload_after_skills = handler_name in [
                    "handle_skills_install",
                    "handle_skills_uninstall",
                    "handle_skills_import_local",
                    "handle_skills_skillnet_install",
                ]
                if (
                    handler_name == "handle_skills_skillnet_install"
                    and payload.get("pending")
                ):
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

        # CLI 文件命令处理
        if request.req_method in _CLI_FILE_ROUTES:
            return await self._handle_cli_file_command(request)

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
        query = request.params.get("query", "")
        append_history_record(
            session_id=session_id,
            request_id=request.request_id,
            channel_id=request.channel_id,
            role="user",
            content=query,
            timestamp=time.time(),
        )

        # 确保 session 的任务处理器在运行
        await self._ensure_session_processor(session_id)

        logger.info(
            "[JiuWenClaw] 处理请求: request_id=%s channel_id=%s session_id=%s",
            request.request_id, request.channel_id, session_id,
        )
        config_base = get_config()
        memory_mode = get_memory_mode(config_base)
        inputs = {
            "conversation_id": request.session_id,
            "query": build_user_prompt(
                request.params.get("query", ""),
                files=request.params.get("files", {}),
                channel=str(request.channel_id or ""),
                language=config_base.get("preferred_language", "zh")
            ),
        }

        if memory_mode == "local" and self._compaction_manager:
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
            token_cid = TOOL_PERMISSION_CHANNEL_ID.set((request.channel_id or "").strip())
            ctx = RuntimeToolContext(
                session_id=request.session_id,
                channel_id=request.channel_id,
                request_id=request.request_id,
                mode=request.params.get("mode", "plan"),
                request_params=request.params,
                metadata=request.metadata,
            )
            cron_route_tok = self._cron_tools.push_cron_route(self._cron_tool_route_for_ctx(ctx))
            try:
                await self._register_runtime_tools(ctx)
                return await Runner.run_agent(agent=self._instance, inputs=inputs)
            except asyncio.CancelledError:
                logger.info("[JiuWenClaw] Agent 任务被取消: request_id=%s session_id=%s", request.request_id, session_id)
                raise
            except Exception as e:
                logger.error("[JiuWenClaw] Agent 任务执行异常: %s", e)
                raise
            finally:
                self._cron_tools.reset_cron_route(cron_route_tok)
                TOOL_PERMISSION_CHANNEL_ID.reset(token_cid)

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

        if memory_mode == "local" and self._compaction_manager and content:
            if isinstance(content, dict):
                content_str = content.get("output", str(content))
            else:
                content_str = str(content)
            self._compaction_manager.add_message("assistant", content_str)

        assistant_content = content if isinstance(content, str) else str(content)
        append_history_record(
            session_id=session_id,
            request_id=request.request_id,
            channel_id=request.channel_id,
            role="assistant",
            event_type="chat.final",
            content=assistant_content,
            timestamp=time.time(),
        )
        if memory_mode == "cloud":
            if isinstance(content, dict):
                content_str = content.get("output", str(content))
            else:
                content_str = str(content)
            after_ctx = MemoryHookContext(
                session_id=request.session_id or "default",
                request_id=request.request_id or "",
                channel_id=request.channel_id,
                agent_name=self._agent_name,
                workspace_dir=self._workspace_dir,
                assistant_message=content_str,
                extra=request.params,
            )

            await ExtensionRegistry.get_instance().trigger(AgentServerHookEvents.MEMORY_AFTER_CHAT, after_ctx)

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
                payload={"event_type": "chat.error", "error": "模型未正确配置，请先配置模型信息", "is_complete": True},
                is_complete=True,
            )
            return

        session_id = self._get_session_id(request)
        query = request.params.get("query", "")
        append_history_record(
            session_id=session_id,
            request_id=request.request_id,
            channel_id=request.channel_id,
            role="user",
            content=query,
            timestamp=time.time(),
        )
        await self._ensure_session_processor(session_id)

        logger.info(
            "[JiuWenClaw] 处理流式请求: request_id=%s channel_id=%s session_id=%s",
            request.request_id, request.channel_id, session_id,
        )
        config_base = get_config()
        memory_mode = get_memory_mode(config_base)
        inputs = {
            "conversation_id": request.session_id,
            "query": build_user_prompt(
                request.params.get("query", ""),
                files=request.params.get("files", {}),
                channel=str(request.channel_id or ""),
                language=config_base.get("preferred_language", "zh")
            ),
        }

        # supplement 任务：读取现有 todo 待办，拼入 query 让 agent 知道有未完成的任务
        if memory_mode == "local" and self._compaction_manager:
            self._compaction_manager.add_message("user", query)
            memory_mgr = await get_memory_manager(
                agent_id=self._agent_name,
                workspace_dir=self._workspace_dir
            )
            if memory_mgr:
                await self._compaction_manager.check_and_compact(memory_mgr)

        rid = request.request_id
        cid = request.channel_id
        final_answer_content = ""
        final_answer_chunks: list[str] = []

        # 创建流式输出队列
        stream_queue = asyncio.Queue()
        stream_done = asyncio.Event()

        # 创建流式任务函数
        async def run_stream_task():
            """执行流式任务，将产生的 chunk 放入队列."""
            token_cid = TOOL_PERMISSION_CHANNEL_ID.set((request.channel_id or "").strip())
            ctx = RuntimeToolContext(
                session_id=request.session_id,
                channel_id=request.channel_id,
                request_id=request.request_id,
                mode=request.params.get("mode", "plan"),
                request_params=request.params,
                metadata=request.metadata,
            )
            cron_route_tok = self._cron_tools.push_cron_route(self._cron_tool_route_for_ctx(ctx))
            try:
                await self._register_runtime_tools(ctx)
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
                self._cron_tools.reset_cron_route(cron_route_tok)
                TOOL_PERMISSION_CHANNEL_ID.reset(token_cid)
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
                    if isinstance(data, dict):
                        et = data.get("event_type")
                        if isinstance(et, str):
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

        if memory_mode == "cloud":
            assistant_message = final_answer_content or "".join(final_answer_chunks)
            after_ctx = MemoryHookContext(
                session_id=request.session_id or "default",
                request_id=request.request_id or "",
                channel_id=request.channel_id,
                agent_name=self._agent_name,
                workspace_dir=self._workspace_dir,
                assistant_message=assistant_message,
                extra=request.params,
            )

            await ExtensionRegistry.get_instance().trigger(AgentServerHookEvents.MEMORY_AFTER_CHAT, after_ctx)

        if request.params.get("mode", "plan") == "plan":
            # 终止 chunk
            yield AgentResponseChunk(
                request_id=rid,
                channel_id=cid,
                payload={"is_complete": True},
                is_complete=True,
            )
        else:
            yield AgentResponseChunk(
                request_id=rid,
                channel_id=cid,
                payload={"is_complete": True},
                is_complete=True and self._session_tool.all_tasks_done(),
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
                    return {
                        "event_type": "chat.delta",
                        "content": content,
                        "source_chunk_type": chunk_type,
                    }

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
                        # Check if this is a chunked/partial answer (streaming)
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
                        return {
                            "event_type": "chat.delta",
                            "content": content,
                            "source_chunk_type": chunk_type,
                        }
                    return {
                        "event_type": "chat.final",
                        "content": content,
                        "source_chunk_type": chunk_type,
                    }

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

                if chunk_type == "processing_complete":
                    return {
                        "event_type": "chat.processing_status",
                        "is_processing": False,
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
                return {
                    "event_type": "chat.delta",
                    "content": content,
                    "source_chunk_type": chunk_type,
                }

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
                    return {
                        "event_type": "chat.delta",
                        "content": str(output),
                        "source_chunk_type": "dict_output",
                    }
                return None

        except Exception:
            logger.debug("[_parse_stream_chunk] 解析异常", exc_info=True)

        return None

    def _prepare_instance_by_session(self):
        pass

    def is_working(self) -> dict:
        """返回 Agent 是否正在工作的状态.

        用于沙箱保活校验，检查当前服务是否有正在处理或待处理的消息任务.

        判断维度：
        1. 非流式任务：_session_tasks 中正在执行的 Task
        2. 待处理消息：_session_queues 中队列的消息数
        3. 流式任务：_instance._stream_tasks 中正在执行的流式 Task

        Returns:
            dict 包含以下字段:
                - working: bool, 是否正在工作（有活跃任务或待处理消息）
                - initialized: bool, Agent 实例是否已初始化
                - model_configured: bool, 模型是否已配置
                - active_tasks: int, 当前正在执行的非流式任务数
                - stream_tasks: int, 当前正在执行的流式任务数
                - pending_messages: int, 队列中待处理的消息数
                - active_sessions: list[str], 有活跃任务或待处理消息的 session 列表
        """
        initialized = self._instance is not None
        model_configured = self._has_valid_model_config() if initialized else False

        active_sessions = set()
        active_tasks = 0
        stream_tasks = 0
        pending_messages = 0

        # 检查正在执行的非流式任务
        for session_id, task in self._session_tasks.items():
            if task is not None and not task.done():
                active_sessions.add(session_id)
                active_tasks += 1

        # 检查队列中待处理的消息
        for session_id, queue in self._session_queues.items():
            queue_size = queue.qsize()
            if queue_size > 0:
                active_sessions.add(session_id)
                pending_messages += queue_size

        # 检查流式任务
        if initialized:
            stream_task_set = getattr(self._instance, '_stream_tasks', set())
            for task in stream_task_set:
                if not task.done():
                    stream_tasks += 1

        working = initialized and (active_tasks > 0 or pending_messages > 0 or stream_tasks > 0)

        return {
            "working": working,
            "initialized": initialized,
            "model_configured": model_configured,
            "active_tasks": active_tasks,
            "stream_tasks": stream_tasks,
            "pending_messages": pending_messages,
            "active_sessions": list(active_sessions),
        }
