# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""JiuWenClaw ReAct Adapter - 基于 openjiuwen ReActAgent 的适配器实现.

此模块实现 AgentAdapter 协议，封装 ReAct SDK 的所有专属逻辑。
公共编排逻辑（session 队列、Skills 路由、heartbeat 等）由 Facade 层处理。
"""

from __future__ import annotations

import asyncio
import logging
import os
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

from jiuwenclaw.agentserver.prompt_builder import build_system_prompt
from jiuwenclaw.agentserver.tools.multi_session_toolkits import MultiSessionToolkit
from jiuwenclaw.agentserver.tools import SendFileToolkit
from jiuwenclaw.gateway.cron import CronController, CronTargetChannel

from jiuwenclaw.utils import (
    get_agent_root_dir,
    get_checkpoint_dir,
    get_env_file, get_deepagent_heartbeat_path,
)
from jiuwenclaw.config import get_config
from jiuwenclaw.agentserver.react_agent import JiuClawReActAgent
from jiuwenclaw.agentserver.permissions.checker import TOOL_PERMISSION_CHANNEL_ID
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
)
from jiuwenclaw.agentserver.skill_manager import _get_skills_dir
from jiuwenclaw.evolution.service import EvolutionService
from jiuwenclaw.schema.agent import AgentRequest, AgentResponse, AgentResponseChunk
from jiuwenclaw.agentserver.memory import get_memory_manager
from jiuwenclaw.schema.hook_event import AgentServerHookEvents
from jiuwenclaw.extensions.registry import ExtensionRegistry
from jiuwenclaw.schema.hooks_context import MemoryHookContext

load_dotenv(dotenv_path=get_env_file())

logger = logging.getLogger(__name__)

# 探活请求发送的 content，AgentServer 可识别为心跳
HEARTBEAT_PROMPT = "如果你的workspace目录存在HEARTBEAT.md文件, 读取文件内容并且根据文件内容执行任务. 如果没有HEARTBEAT.md文件, 仅回复HEARTBEAT_OK"


@dataclass
class RuntimeToolContext:
    """运行时工具注册上下文，封装_register_runtime_tools的相关参数."""
    session_id: str | None
    channel_id: str | None
    request_id: str | None
    mode: str = "plan"
    request_params: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


class JiuWenClawReactAdapter:
    """ReAct SDK 适配器，实现 AgentAdapter 协议.

    封装所有 ReAct 专属逻辑：
    - ReActAgent 实例生命周期管理
    - ReAct runtime tools 注册
    - ReAct stream event 解析
    - ReAct evolution 绑定
    - ReAct interrupt / user_answer 处理
    """

    def __init__(self) -> None:
        self._instance: JiuClawReActAgent | None = None
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
        self._todo_tool_sessions_registered: set[str] = set()
        self._sysop_card_id: str | None = None
        self._session_tool = None

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
            logger.error("[JiuWenClawReactAdapter] fail to setup checkpoint due to: %s", e)

    def _load_react_config(self, config):
        react_config = config.get("react", {}).copy()
        agent_name = react_config.pop("agent_name", "main_agent")
        self._agent_name = agent_name

        model_configs = config.get("models", {})
        if not isinstance(model_configs, dict):
            model_configs = {}
        else:
            model_configs = model_configs.copy()
        memory_mode = get_memory_mode(config)
        react_config = {**react_config, **model_configs.get("default", {}).copy(), "prompt_template": [
            {"role": "system", "content": build_system_prompt(
                mode="plan",
                language=config.get("preferred_language", "en"),
                channel="web",
                memory_mode=memory_mode,
            )}
        ]}

        agent_config = ReActAgentConfig(**react_config)

        context_engine_config = react_config.get('context_engine_config', {}).copy()

        if context_engine_config.get("enabled", False):
            message_offloader_config = context_engine_config.get("message_offloader_config", {}).copy()
            dialogue_compressor_config = context_engine_config.get("dialogue_compressor_config", {}).copy()
            model_name = (model_configs
                          .get("default", {})
                          .get("model_client_config", {})
                          .get("model_name", "default"))
            processors = [
                (
                    "MessageOffloader",
                    MessageOffloaderConfig(
                        messages_threshold=message_offloader_config.get("messages_threshold", 40),
                        tokens_threshold=message_offloader_config.get("tokens_threshold", 20000),
                        large_message_threshold=message_offloader_config.get("large_message_threshold", 1000),
                        trim_size=message_offloader_config.get("trim_size", 500),
                        offload_message_type=["tool"],
                        keep_last_round=message_offloader_config.get("keep_last_round", False),
                    )
                ),
                (
                    "DialogueCompressor",
                    DialogueCompressorConfig(
                        messages_threshold=dialogue_compressor_config.get("messages_threshold", 40),
                        tokens_threshold=dialogue_compressor_config.get("tokens_threshold", 50000),
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
        agent_config = self._load_react_config(config_base)

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
            logger.warning("[JiuWenClawReactAdapter] add sys_operation failed, fallback without it: %s", exc)
        self._sysop_card_id = sysop_card_id

        agent_card = AgentCard(name=self._agent_name, id='jiuwenclaw')
        self._instance = JiuClawReActAgent(card=agent_card)

        if sysop_card_id and hasattr(self._instance, "_skill_util"):
            agent_config.sys_operation_id = sysop_card_id
        elif sysop_card_id:
            logger.warning("[JiuWenClawReactAdapter] ReActAgent has no _skill_util; skip sys_operation_id binding.")

        self._instance.configure(agent_config)

        # register installed skills (compatible with openjiuwen variants).
        if hasattr(self._instance, "_skill_util"):
            try:
                await self._instance.register_skill(str(_get_skills_dir()))
            except Exception as exc:
                logger.warning("[JiuWenClawReactAdapter] register_skill failed, continue without skills: %s", exc)

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
                logger.info("[JiuWenClawReactAdapter] Evolution has been enabled: auto_scan=%s", evolution_auto_scan)
            elif evolution_enabled and not has_valid_model_config:
                logger.warning(
                    "[JiuWenClawReactAdapter] Evolution is enabled but skipped: no valid model API key configured")
        else:
            logger.warning("[JiuWenClawReactAdapter] ReActAgent has no _skill_util; skip skill registration.")

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
                logger.info("[JiuWenClawReactAdapter] task memory tools registered")
            except Exception as exc:
                logger.warning("[JiuWenClawReactAdapter] task memory tools registration failed: %s", exc)

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
                logger.warning("[JiuWenClawReactAdapter] video_understanding tool registration failed: %s", exc)

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
            logger.warning("[JiuWenClawReactAdapter] browser MCP registration skipped: %s", exc)

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
                logger.info("[JiuWenClawReactAdapter] vision tools registered successfully")
            except Exception as exc:
                logger.warning("[JiuWenClawReactAdapter] vision tools registration skipped: %s", exc)

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
                    self._instance.ability_manager.add(tool.card)
                self._audio_mcp_registered = True
                logger.info("[JiuWenClawReactAdapter] audio tools registered successfully")
            except Exception as exc:
                logger.warning("[JiuWenClawReactAdapter] audio tools registration skipped: %s", exc)

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
                        logger.warning(f"[JiuWenClawReactAdapter] Failed to register tool {tool.card.name}: {tool_exc}")

                self._xiaoyi_phone_tools_registered = True
                logger.info(f"[JiuWenClawReactAdapter] {len(phone_tools)} xiaoyi phone tools registered successfully")
            except Exception as exc:
                logger.warning("[JiuWenClawReactAdapter] xiaoyi phone tools registration skipped: %s", exc)
        else:
            logger.info("[JiuWenClawReactAdapter] xiaoyi channel not enabled, skipping phone tools")

        # add cron tools
        try:
            cron_controller = CronController.get_instance()
            for cron_tool in cron_controller.get_tools():
                Runner.resource_mgr.add_tool(cron_tool)
                self._instance.ability_manager.add(cron_tool.card)
        except Exception as exc:
            logger.error("[JiuWenClawReactAdapter] 定时工具加载失败， reason=%s", exc)
        # ---- 权限引擎初始化 ----
        permissions_cfg = config_base.get("permissions", {})
        init_permission_engine(permissions_cfg)
        logger.info(
            "[JiuWenClawReactAdapter] Permission engine initialized: enabled=%s",
            permissions_cfg.get("enabled", True),
        )
        logger.info("[JiuWenClawReactAdapter] 初始化完成: agent_name=%s", self._agent_name)

    async def reload_agent_config(
            self,
            config_base: dict[str, Any] | None = None,
            env_overrides: dict[str, Any] | None = None,
    ) -> None:
        """从 config.yaml 重新加载配置并 reconfigure 当前实例，使模型/API 等配置生效且不重启进程。

        Args:
            config_base: 可选的完整配置快照；传入时优先使用它而不是读取本地 config.yaml。
            env_overrides: 可选的环境变量增量；仅覆盖请求中出现的 key。
        """
        if self._instance is None:
            raise RuntimeError("JiuWenClawReactAdapter 未初始化，请先调用 create_instance()")
        clear_config_cache()
        clear_memory_manager_cache()

        if env_overrides is not None:
            if not isinstance(env_overrides, dict):
                raise TypeError("env_overrides must be a dict when provided")
            for env_key, env_value in env_overrides.items():
                if env_value is None:
                    os.environ.pop(str(env_key), None)
                else:
                    os.environ[str(env_key)] = str(env_value)

        if config_base is None:
            config_base = get_config()
        elif not isinstance(config_base, dict):
            raise TypeError("config_base must be a dict when provided")
        else:
            from jiuwenclaw.config import resolve_env_vars
            config_base = resolve_env_vars(config_base)

        apply_video_model_config_from_yaml(config_base)
        apply_audio_model_config_from_yaml(config_base)
        apply_vision_model_config_from_yaml(config_base)
        agent_config = self._load_react_config(config_base)

        if self._sysop_card_id:
            agent_config.sys_operation_id = self._sysop_card_id

        if hasattr(self._instance, "_llm"):
            self._instance._llm = None
        self._instance.configure(agent_config)

        evo_svc = getattr(self._instance, "_evolution_service", None)
        if evo_svc is not None:
            new_llm = self._instance._get_llm()
            new_model = agent_config.model_name
            evo_svc.update_llm(new_llm, new_model)
            _env_auto_scan = os.getenv("EVOLUTION_AUTO_SCAN")
            if _env_auto_scan is not None:
                evo_svc.auto_scan = _env_auto_scan.lower() in ("true", "1", "yes")

        permissions_cfg = config_base.get("permissions", {})
        try:
            engine = get_permission_engine()
            engine.update_config(permissions_cfg)
            logger.info("[JiuWenClawReactAdapter] Permission config reloaded: enabled=%s",
                        permissions_cfg.get("enabled", True))
        except Exception as exc:
            logger.warning("[JiuWenClawReactAdapter] Permission config reload failed: %s", exc)
        logger.info("[JiuWenClawReactAdapter] 配置已热更新，未重启进程")

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
                    self._instance.ability_manager.remove(tool.name)
                elif tool.name.startswith("session_"):
                    self._instance.ability_manager.remove(tool.name)
                elif tool.name.startswith("send_file_to_user"):
                    self._instance.ability_manager.remove(tool.name)

        # 定时工具：按 channel 注册；优先用 channel_id，否则从 session_id 前缀推断
        channel = (ctx.channel_id or "").strip() or (
            (ctx.session_id or "").split("_")[0] if ctx.session_id else ""
        )
        logger.info(f"[JiuwenClaw] update tool and prompt for channel {channel}")
        if channel not in ["heartbeat", "cron"]:
            cron_controller = CronController.get_instance()
            if channel == "feishu":
                cron_controller.set_target_channel(CronTargetChannel.FEISHU)
            elif channel == "wecom":
                cron_controller.set_target_channel(CronTargetChannel.WECOM)
            elif channel == "xiaoyi":
                cron_controller.set_target_channel(CronTargetChannel.XIAOYI)
            elif channel in ("web", "sess"):
                cron_controller.set_target_channel(CronTargetChannel.WEB)

            for cron_tool in cron_controller.get_tools():
                if not Runner.resource_mgr.get_tool(cron_tool.card.id):
                    Runner.resource_mgr.add_tool(cron_tool)
                self._instance.ability_manager.add(cron_tool.card)

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
                        logger.error(f"[JiuWenClawReactAdapter] Tool {tool.card.name} may already exist: {tool_exc}")

            except Exception as exc:
                logger.warning(f"[JiuWenClawReactAdapter] xiaoyi phone tools runtime registration skipped: {exc}")

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
                sub_agent_config=self._load_react_config(config_base)
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
                logger.warning("[JiuWenClawReactAdapter] ensure task memory tools failed: %s", exc)

        logger.info("[JiuWenClawReactAdapter] Checking multimodal API keys...")

        video_api_key = os.environ.get("VIDEO_API_KEY")
        zhipu_api_key = os.environ.get("ZHIPU_API_KEY")
        has_video_key = any([
            video_api_key,
            zhipu_api_key,
        ])

        if has_video_key:
            try:
                if not Runner.resource_mgr.get_tool(video_understanding.card.id):
                    Runner.resource_mgr.add_tool(video_understanding)
                self._instance.ability_manager.add(video_understanding.card)
                logger.info("[JiuWenClawReactAdapter] Registered video_understanding tool")
            except Exception as exc:
                logger.warning("[JiuWenClawReactAdapter] ensure video_understanding tool failed: %s", exc)
        else:
            try:
                self._instance.ability_manager.remove(video_understanding.card.name)
                logger.info("[JiuWenClawReactAdapter] Unregistered video_understanding tool")
            except Exception as exc:
                logger.debug(
                    "[JiuWenClawReactAdapter] unregister video_understanding tool failed (tool may not exist): %s", exc)

        vision_api_key = os.environ.get("VISION_API_KEY")
        api_key = os.environ.get("API_KEY")
        gemini_api_key = os.environ.get("GEMINI_API_KEY")
        has_vision_key = any([
            vision_api_key,
            api_key,
            gemini_api_key,
        ])

        if has_vision_key:
            try:
                for tool in [visual_question_answering]:
                    if not Runner.resource_mgr.get_tool(tool.card.id):
                        Runner.resource_mgr.add_tool(tool)
                    self._instance.ability_manager.add(tool.card)
                logger.info("[JiuWenClawReactAdapter] Registered vision tools")
            except Exception as exc:
                logger.warning("[JiuWenClawReactAdapter] ensure vision tools failed: %s", exc)
        else:
            try:
                for tool in [visual_question_answering]:
                    self._instance.ability_manager.remove(tool.card.name)
                logger.info("[JiuWenClawReactAdapter] Unregistered vision tools")
            except Exception as exc:
                logger.debug("[JiuWenClawReactAdapter] unregister vision tools failed (tools may not exist): %s", exc)

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
                logger.warning("[JiuWenClawReactAdapter] ensure audio tools failed: %s", exc)
        else:
            try:
                for tool in [audio_question_answering, audio_metadata]:
                    self._instance.ability_manager.remove(tool.card.name)
            except Exception as exc:
                logger.debug("[JiuWenClawReactAdapter] unregister audio tools failed (tools may not exist): %s", exc)

        current_mcp_tools = get_mcp_tools()
        current_mcp_tool_names = {tool.card.name for tool in current_mcp_tools}

        for mcp_tool in current_mcp_tools:
            try:
                if not Runner.resource_mgr.get_tool(mcp_tool.card.id):
                    Runner.resource_mgr.add_tool(mcp_tool)
                self._instance.ability_manager.add(mcp_tool.card)
            except Exception as exc:
                logger.warning("[JiuWenClawReactAdapter] register MCP tool failed: %s", exc)

        all_mcp_tool_names = {"mcp_free_search", "mcp_paid_search", "mcp_fetch_webpage", "mcp_exec_command"}
        tools_to_remove = all_mcp_tool_names - current_mcp_tool_names
        for tool_name in tools_to_remove:
            try:
                self._instance.ability_manager.remove(tool_name)
            except Exception as exc:
                logger.debug("[JiuWenClawReactAdapter] unregister MCP tool %s failed (tool may not exist): %s",
                             tool_name, exc)

        system_prompt = build_system_prompt(
            mode=ctx.mode,
            language=config_base.get("preferred_language", "zh"),
            channel=channel,
            memory_block=memory_block,
            memory_mode=memory_mode,
        )
        logger.debug(
            "[JiuWenClawReactAdapter] system prompt built: memory_mode=%s channel=%s\n%s",
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
                    "[JiuWenClawReactAdapter] interrupt: 已暂停 ReAct 循环 request_id=%s",
                    request.request_id,
                )
            message = "任务已暂停"

        elif intent == "resume":
            # 恢复：恢复 ReAct 循环
            if self._instance is not None and hasattr(self._instance, 'resume'):
                self._instance.resume()
                logger.info(
                    "[JiuWenClawReactAdapter] interrupt: 已恢复 ReAct 循环 request_id=%s",
                    request.request_id,
                )
            message = "任务已恢复"

        elif intent == "supplement":
            # supplement: 取消当前任务，但保留 todo（新任务会根据 todo 待办继续执行）
            # 先解除暂停，防止 task 阻塞在 pause_event.wait 上
            if self._instance is not None and hasattr(self._instance, 'resume'):
                self._instance.resume()

            # 取消流式任务
            if self._instance is not None:
                stream_tasks = getattr(self._instance, '_stream_tasks', set())
                active = [t for t in stream_tasks if not t.done()]
                if active:
                    logger.info(
                        "[JiuWenClawReactAdapter] interrupt(supplement): 取消 %d 个流式任务 request_id=%s",
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

            # 取消流式任务
            if self._instance is not None:
                stream_tasks = getattr(self._instance, '_stream_tasks', set())
                active = [t for t in stream_tasks if not t.done()]
                if active:
                    logger.info(
                        "[JiuWenClawReactAdapter] interrupt: 取消 %d 个流式任务 request_id=%s",
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
                            "[JiuWenClawReactAdapter] interrupt: 已将 %d 个未完成 todo 项标记为 cancelled session_id=%s",
                            cancel_count, request.session_id,
                        )
                except Exception as exc:
                    logger.warning("[JiuWenClawReactAdapter] 标记 todo cancelled 失败: %s", exc)

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
        if os.getenv("API_KEY"):
            return True

        if self._instance is not None and hasattr(self._instance, "_config"):
            config = self._instance._config
            if hasattr(config, "model_client_config") and isinstance(config.model_client_config, dict):
                mcc = config.model_client_config
                api_key = mcc.get("api_key", "")
                if api_key:
                    return True

        return False

    async def handle_user_answer(self, request: AgentRequest) -> AgentResponse:
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

    async def handle_heartbeat(self, request: AgentRequest) -> AgentResponse | None:
        """处理 heartbeat 请求，返回 None 表示继续正常流程."""
        if "heartbeat" not in request.params:
            return None

        heartbeat_md = get_deepagent_heartbeat_path()
        if not os.path.isfile(heartbeat_md):
            logger.debug("[JiuWenClawReactAdapter] heartbeat OK (no HEARTBEAT.md): request_id=%s", request.request_id)
            return AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={"heartbeat": "HEARTBEAT_OK"},
                metadata=request.metadata,
            )

        task_list = []
        try:
            with open(heartbeat_md, "r", encoding="utf-8") as f:
                lines = f.readlines()
                for line in lines:
                    line = line.strip()
                    if line != "" and not line.startswith("<!--"):
                        task_list.append(line)
        except Exception as exc:
            logger.warning("[JiuWenClawReactAdapter] 读取 HEARTBEAT.md 失败: %s", exc)
            return AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={"heartbeat": "HEARTBEAT_OK"},
                metadata=request.metadata,
            )

        if not task_list:
            logger.debug("[JiuWenClawReactAdapter] HEARTBEAT.md 为空，短路返回")
            return AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={"heartbeat": "HEARTBEAT_OK"},
                metadata=request.metadata,
            )

        task_list_str = "\n".join(task_list)
        query = f"请检查下面用户遗留给你的任务项，并按照顺序完成所有待办事项，并将结果以markdown文件保存在你的工作目录下：\n{task_list_str}"
        request.params["query"] = query
        logger.info(
            "[JiuWenClawReactAdapter] heartbeat 触发 HEARTBEAT.md 任务: request_id=%s session_id=%s",
            request.request_id, request.session_id,
        )
        return None

    async def process_message_impl(
            self, request: AgentRequest, inputs: dict[str, Any]
    ) -> AgentResponse:
        """Execute a single non-streaming request and return the response.

        Args:
            request: AgentRequest 对象
            inputs: 已构建好的输入字典，包含 conversation_id 和 query

        Returns:
            AgentResponse 包含执行结果
        """
        if self._instance is None:
            raise RuntimeError("JiuWenClawReactAdapter 未初始化，请先调用 create_instance()")

        if not self._has_valid_model_config():
            return AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": "模型未正确配置，请先配置模型信息"},
                metadata=request.metadata,
            )

        session_id = request.session_id or "default"

        config_base = get_config()
        memory_mode = get_memory_mode(config_base)
        if memory_mode == "local" and self._compaction_manager:
            query = request.params.get("query", "")
            self._compaction_manager.add_message("user", query)
            memory_mgr = await get_memory_manager(
                agent_id=self._agent_name,
                workspace_dir=self._workspace_dir
            )
            if memory_mgr:
                await self._compaction_manager.check_and_compact(memory_mgr)

        token_cid = TOOL_PERMISSION_CHANNEL_ID.set((request.channel_id or "").strip())
        try:
            ctx = RuntimeToolContext(
                session_id=request.session_id,
                channel_id=request.channel_id,
                request_id=request.request_id,
                mode=request.params.get("mode", "plan"),
                request_params=request.params,
                metadata=request.metadata,
            )
            await self._register_runtime_tools(ctx)
            result = await Runner.run_agent(agent=self._instance, inputs=inputs)
        except asyncio.CancelledError:
            logger.info("[JiuWenClawReactAdapter] Agent 任务被取消: request_id=%s session_id=%s", request.request_id,
                        session_id)
            raise
        except Exception as e:
            logger.error("[JiuWenClawReactAdapter] Agent 任务执行异常: %s", e)
            raise
        finally:
            TOOL_PERMISSION_CHANNEL_ID.reset(token_cid)

        content = result if isinstance(result, (str, dict)) else str(result)

        if memory_mode == "local" and self._compaction_manager and content:
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

    async def process_message_stream_impl(
            self, request: AgentRequest, inputs: dict[str, Any]
    ) -> AsyncIterator[AgentResponseChunk]:
        """Execute a streaming request; yield response chunks.

        Args:
            request: AgentRequest 对象
            inputs: 已构建好的输入字典，包含 conversation_id 和 query

        Yields:
            AgentResponseChunk 流式响应块
        """
        if self._instance is None:
            raise RuntimeError("JiuWenClawReactAdapter 未初始化，请先调用 create_instance()")

        if not self._has_valid_model_config():
            yield AgentResponseChunk(
                request_id=request.request_id,
                channel_id=request.channel_id,
                payload={"event_type": "chat.error", "error": "模型未正确配置，请先配置模型信息", "is_complete": True},
                is_complete=True,
            )
            return

        session_id = request.session_id or "default"
        rid = request.request_id
        cid = request.channel_id

        config_base = get_config()
        memory_mode = get_memory_mode(config_base)
        if memory_mode == "local" and self._compaction_manager:
            query = request.params.get("query", "")
            self._compaction_manager.add_message("user", query)
            memory_mgr = await get_memory_manager(
                agent_id=self._agent_name,
                workspace_dir=self._workspace_dir
            )
            if memory_mgr:
                await self._compaction_manager.check_and_compact(memory_mgr)

        token_cid = TOOL_PERMISSION_CHANNEL_ID.set((request.channel_id or "").strip())
        try:
            ctx = RuntimeToolContext(
                session_id=request.session_id,
                channel_id=request.channel_id,
                request_id=request.request_id,
                mode=request.params.get("mode", "plan"),
                request_params=request.params,
                metadata=request.metadata,
            )
            await self._register_runtime_tools(ctx)
            async for chunk in Runner.run_agent_streaming(self._instance, inputs):
                parsed = self._parse_stream_chunk(chunk)
                if parsed is None:
                    continue
                yield AgentResponseChunk(
                    request_id=rid,
                    channel_id=cid,
                    payload=parsed,
                    is_complete=False,
                )
        except asyncio.CancelledError:
            logger.info("[JiuWenClawReactAdapter] 流式任务被取消: request_id=%s session_id=%s", rid, session_id)
            raise
        except Exception as exc:
            logger.exception("[JiuWenClawReactAdapter] 流式任务异常: %s", exc)
            yield AgentResponseChunk(
                request_id=rid,
                channel_id=cid,
                payload={"event_type": "chat.error", "error": str(exc)},
                is_complete=False,
            )
        finally:
            TOOL_PERMISSION_CHANNEL_ID.reset(token_cid)

        if request.params.get("mode", "plan") == "plan":
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
                is_complete=True and self._session_tool.all_tasks_done() if self._session_tool else True,
            )

    @staticmethod
    def _parse_stream_chunk(chunk) -> dict | None:
        """将 SDK OutputSchema 转为前端可消费的 payload dict.

        参考 openjiuwen_agent._parse_stream_chunk 的处理逻辑，
        过滤掉 traceId / invokeId 等调试帧，按 type 分类提取数据。

        Returns:
            dict  – 含 event_type 的 payload，或 None（需跳过的帧）。
        """
        try:
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
