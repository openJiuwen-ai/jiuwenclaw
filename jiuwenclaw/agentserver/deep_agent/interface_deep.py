# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""JiuWenClaw Deep Adapter - 基于 openjiuwen DeepAgent 的适配器实现.

此模块实现 AgentAdapter 协议，封装 Deep SDK 的所有专属逻辑。
公共编排逻辑（session 队列、Skills 路由、heartbeat 等）由 Facade 层处理。
"""

from __future__ import annotations

import asyncio
from contextvars import ContextVar, Token
from dataclasses import dataclass
import logging
import os
from typing import Any, AsyncIterator

from dotenv import load_dotenv
from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig, Model
from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.context_engine import MessageOffloaderConfig, DialogueCompressorConfig
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent import AgentCard, ReActAgentConfig
from openjiuwen.core.sys_operation import SysOperation, SysOperationCard, OperationMode, LocalWorkConfig
from openjiuwen.core.session.checkpointer import CheckpointerFactory
from openjiuwen.core.session.checkpointer.checkpointer import CheckpointerConfig
from openjiuwen.core.session.checkpointer.persistence import PersistenceCheckpointerProvider
from openjiuwen.deepagents import DeepAgent, DeepAgentConfig
from openjiuwen.deepagents.factory import create_deep_agent
from openjiuwen.deepagents.prompts import resolve_language
from openjiuwen.deepagents.workspace.workspace import Workspace
from openjiuwen.deepagents.rails import SkillUseRail, TaskPlanningRail, ToolPromptRail
from openjiuwen.deepagents.rails.filesystem_rail import FileSystemRail

from jiuwenclaw.agentserver.deep_agent.cron_runtime import CronRuntimeBridge
from jiuwenclaw.agentserver.deep_agent.rails import (
    JiuClawStreamEventRail
)
from jiuwenclaw.gateway.cron import CronTargetChannel
from jiuwenclaw.utils import logger, USER_WORKSPACE_DIR, get_env_file, get_agent_root_dir
from jiuwenclaw.config import get_config
from jiuwenclaw.agentserver.tools.browser_tools import register_browser_runtime_mcp_server
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
from jiuwenclaw.agentserver.deep_agent.prompt_builder import build_identity_prompt
from jiuwenclaw.agentserver.skill_manager import _SKILLS_DIR
from jiuwenclaw.evolution.service import EvolutionService
from jiuwenclaw.schema.agent import AgentRequest, AgentResponse, AgentResponseChunk
from jiuwenclaw.agentserver.memory import get_memory_manager
from openjiuwen.deepagents.tools import WebFreeSearchTool, WebPaidSearchTool, WebFetchWebpageTool
from openjiuwen.deepagents.tools.todo import create_todos_tool, TodoStatus, TodoModifyTool

load_dotenv(dotenv_path=get_env_file())

_react_config = get_config().get("react", {})
_STREAM_CHAR_THRESHOLD = _react_config.get("stream_character_threshold", 2000)

_CRON_TOOL_CHANNEL_ID: ContextVar[str] = ContextVar(
    "cron_tool_channel_id",
    default=CronTargetChannel.WEB.value,
)
_CRON_TOOL_SESSION_ID: ContextVar[str | None] = ContextVar(
    "cron_tool_session_id",
    default=None,
)
_CRON_TOOL_METADATA: ContextVar[dict[str, Any] | None] = ContextVar(
    "cron_tool_metadata",
    default=None,
)
_CRON_TOOL_MODE: ContextVar[str | None] = ContextVar(
    "cron_tool_mode",
    default=None,
)

logger = logging.getLogger(__name__)


class _RuntimeCronToolContext:
    """Stable cron tool context proxy backed by per-task contextvars."""

    def __init__(self, tool_scope: str) -> None:
        self._tool_scope = tool_scope

    @property
    def channel_id(self) -> str:
        return _CRON_TOOL_CHANNEL_ID.get()

    @property
    def session_id(self) -> str | None:
        return _CRON_TOOL_SESSION_ID.get()

    @property
    def metadata(self) -> dict[str, Any] | None:
        return _CRON_TOOL_METADATA.get()

    @property
    def mode(self) -> str | None:
        return _CRON_TOOL_MODE.get()

    @property
    def tool_scope(self) -> str:
        return self._tool_scope


class JiuWenClawDeepAdapter:
    """Deep SDK 适配器，实现 AgentAdapter 协议.

    封装所有 Deep SDK 专属逻辑：
    - DeepAgent 实例生命周期管理
    - Deep runtime tools 注册
    - Deep stream event 解析
    - Deep evolution 绑定
    - Deep interrupt / user_answer 处理
    """

    def __init__(self) -> None:
        self._instance: DeepAgent | None = None
        self._workspace_dir: str = str(get_agent_root_dir())
        self._agent_name: str = "main_agent"
        self._compaction_manager: ContextCompactionManager | None = None
        self._browser_mcp_registered: bool = False
        self._memory_tools_registered: bool = False
        self._web_tools_registered: bool = False
        self._todo_tool_sessions_registered: set[str] = set()
        self._model: Model | None = None
        self._config_cache: dict[str, Any] = {}
        self._filesystem_rail: FileSystemRail | None = None
        self._skill_rail: SkillUseRail | None = None
        self._stream_event_rail: JiuClawStreamEventRail | None = None
        self._task_planning_rail: TaskPlanningRail | None = None
        self._tool_prompt_rail: ToolPromptRail | None = None
        self._evolution_service: EvolutionService | None = None
        self._tool_cards = None
        self._sys_operation = None
        self._cron_runtime = CronRuntimeBridge()
        self._runtime_cron_tool_context = _RuntimeCronToolContext(
            tool_scope=f"runtime_{id(self):x}",
        )

    @staticmethod
    def _resolve_prompt_channel(session_id: str | None = None) -> str:
        """Resolve prompt channel from session id."""
        if not session_id:
            return "web"

        channel = session_id.split("_", 1)[0]
        if channel == "sess":
            return "web"
        if channel in {"cron", "heartbeat", "feishu", "web"}:
            return channel
        return "web"

    @staticmethod
    def _resolve_prompt_language() -> str:
        """Resolve configured prompt language for builder input."""
        config_base = get_config()
        return str(config_base.get("preferred_language", "zh")).strip().lower()

    def _resolve_runtime_language(self) -> str:
        """Resolve normalized runtime language shared by rails and tools."""
        return resolve_language(self._resolve_prompt_language())

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
            logger.error("[JiuWenClawDeepAdapter] fail to setup checkpoint due to: %s", e)

    def _create_model(self, config: dict) -> Model:
        model_configs = config.get("models", {}).copy()
        default_model_config = model_configs.get("default", {}).copy()
        react_config = config.get("react", {}).copy()

        model_client_config = default_model_config.get("model_client_config") or {}
        if not model_client_config:
            react_model_client_config = react_config.get("model_client_config") or {}
            model_client_config = react_model_client_config

        model_name = (
                model_client_config.get("model_name")
                or react_config.get("model_name")
                or "gpt-4"
        )
        model_config_obj = default_model_config.get("model_config_obj") or {}
        if not model_config_obj:
            react_model_config_obj = react_config.get("model_config_obj") or {}
            model_config_obj = react_model_config_obj

        model_config = ModelRequestConfig(
            model=model_name,
            temperature=model_config_obj.get("temperature", 0.95)
        )
        client_config = ModelClientConfig(**model_client_config)
        self._model = Model(
            model_client_config=client_config,
            model_config=model_config,
        )
        return self._model

    def _get_evolution_target(self) -> Any | None:
        """Return the runtime object that actually owns evolution hooks."""
        if self._instance is None:
            return None

        react_agent = getattr(self._instance, "react_agent", None)
        if react_agent is not None:
            return react_agent

        return self._instance

    def _bind_evolution_service(self, evo_service: EvolutionService) -> bool:
        """Attach EvolutionService to the active runtime agent when supported."""
        target = self._get_evolution_target()
        if target is None or not hasattr(target, "set_evolution_service"):
            return False

        target.set_evolution_service(evo_service)
        self._evolution_service = evo_service
        return True

    @staticmethod
    def _resolve_skill_mode(config: dict[str, Any]) -> str:
        """Validate configured skill mode and fallback safely on invalid values."""
        raw_skill_mode = config.get("skill_mode", SkillUseRail.SKILL_MODE_AUTO_LIST)
        valid_modes = {
            SkillUseRail.SKILL_MODE_AUTO_LIST,
            SkillUseRail.SKILL_MODE_ALL,
        }
        if isinstance(raw_skill_mode, str) and raw_skill_mode in valid_modes:
            return raw_skill_mode

        logger.warning(
            "[JiuWenClawDeepAdapter] invalid skill_mode=%r, fallback to %s",
            raw_skill_mode,
            SkillUseRail.SKILL_MODE_AUTO_LIST,
        )
        return SkillUseRail.SKILL_MODE_AUTO_LIST

    @staticmethod
    def _create_sys_operation() -> SysOperation | None:
        """Create a sys operation."""
        try:
            sysop_card = SysOperationCard(
                mode=OperationMode.LOCAL,
                work_config=LocalWorkConfig(),
            )
            result = Runner.resource_mgr.add_sys_operation(sysop_card)
            if result.is_err():
                logger.warning("[JiuWenClawDeepAdapter] add sys_operation failed: %s", result.msg())
                return None
            return Runner.resource_mgr.get_sys_operation(sysop_card.id)
        except Exception as exc:
            logger.warning("[JiuWenClawDeepAdapter] add sys_operation failed: %s", exc)
            return None

    def _build_filesystem_rail(self) -> FileSystemRail | None:
        """Build FileSystemRail."""
        try:
            fs_rail = FileSystemRail()
            logger.info("[JiuWenClawDeepAdapter] FileSystemRail create success")
        except Exception as exc:
            logger.warning("[JiuWenClawDeepAdapter] FileSystemRail create failed: %s", exc)
            fs_rail = None
        return fs_rail

    def _build_skill_rail(self, config: dict[str, Any], include_tools: bool = False) -> SkillUseRail | None:
        """Build SkillUseRail."""
        try:
            skill_mode = self._resolve_skill_mode(config)
            logger.info("[JiuWenClawDeepAdapter] current skill_mode: %s", skill_mode)
            skill_rail = SkillUseRail(
                skills_dir=str(_SKILLS_DIR),
                skill_mode=skill_mode,
                include_tools=include_tools,
                language=self._resolve_runtime_language(),
            )
            logger.info("[JiuWenClawDeepAdapter] SkillUseRail create success")
        except Exception as exc:
            logger.warning("[JiuWenClawDeepAdapter] SkillUseRail create failed: %s", exc)
            skill_rail = None
        return skill_rail

    def _build_stream_event_rail(self) -> JiuClawStreamEventRail | None:
        """Build JiuClawStreamEventRail."""
        try:
            stream_event_rail = JiuClawStreamEventRail()
            logger.info("[JiuWenClawDeepAdapter] JiuClawStreamEventRail create success")
        except Exception as exc:
            logger.warning("[JiuWenClawDeepAdapter] JiuClawStreamEventRail create failed: %s", exc)
            stream_event_rail = None
        return stream_event_rail

    def _build_task_planning_rail(self) -> TaskPlanningRail | None:
        """Build TaskPlanningRail."""
        try:
            task_planning_rail = TaskPlanningRail(
                language=self._resolve_runtime_language(),
            )
            logger.info("[JiuWenClawDeepAdapter] TaskPlanningRail create success")
        except Exception as exc:
            logger.warning("[JiuWenClawDeepAdapter] TaskPlanningRail create failed: %s", exc)
            task_planning_rail = None
        return task_planning_rail

    def _build_tool_prompt_rail(self) -> ToolPromptRail | None:
        """Build ToolPromptRail."""
        try:
            tool_prompt_rail = ToolPromptRail(
                language=self._resolve_runtime_language(),
            )
            logger.info("[JiuWenClawDeepAdapter] ToolPromptRail create success")
        except Exception as exc:
            logger.warning("[JiuWenClawDeepAdapter] ToolPromptRail create failed: %s", exc)
            tool_prompt_rail = None
        return tool_prompt_rail

    def _build_agent_rails(self, config: dict[str, Any]) -> list[Any]:
        """Build DeepAgent rails consistently for cold start and hot reload."""

        @dataclass
        class _RailBuildInfo:
            attr_name: str
            build_func: callable
            params: dict = None

            def __post_init__(self):
                self.params = self.params or {}

        rail_infos = [
            _RailBuildInfo("_filesystem_rail", self._build_filesystem_rail),
            _RailBuildInfo("_skill_rail", self._build_skill_rail,
                           {"config": config, "include_tools": self._filesystem_rail is None}),
            _RailBuildInfo("_stream_event_rail", self._build_stream_event_rail),
            _RailBuildInfo("_tool_prompt_rail", self._build_tool_prompt_rail),
        ]

        rails_list = []
        for info in rail_infos:
            rail_instance = info.build_func(**info.params)
            if rail_instance is not None:
                setattr(self, info.attr_name, rail_instance)
                rails_list.append(rail_instance)
        return rails_list

    def _rails_snapshot_for_unregister(self) -> list[Any]:
        """与 _build_agent_rails 顺序一致，用于热更新前 unregister."""
        rails = []
        for attr in ("_filesystem_rail", "_skill_rail", "_stream_event_rail", "_tool_prompt_rail"):
            r = getattr(self, attr, None)
            if r is not None:
                rails.append(r)
        return rails

    def _make_deep_agent_config(
            self,
            *,
            model: Model,
            config: dict[str, Any],
            agent_card: AgentCard,
            tool_cards: list[Any],
    ) -> DeepAgentConfig:
        """与 create_deep_agent() 中 DeepAgentConfig 构造保持一致."""
        workspace_obj = Workspace(root_path="./")
        resolved_language = self._resolve_runtime_language()
        normalized_tool_cards = [
            tool.card if hasattr(tool, "card") else tool
            for tool in (tool_cards or [])
        ]
        return DeepAgentConfig(
            model=model,
            card=agent_card,
            system_prompt=build_identity_prompt(
                mode="agent",
                language=self._resolve_prompt_language(),
                channel=self._resolve_prompt_channel(),
            ),
            stop_condition=None,
            enable_task_loop=config.get("enable_task_loop", True),
            max_iterations=config.get("max_iterations", 15),
            subagents=None,
            tools=normalized_tool_cards,
            workspace=workspace_obj,
            skills=None,
            backend=None,
            sys_operation=self._sys_operation,
            language=resolved_language,
            prompt_mode=None,
            vision_model_config=None,
        )

    def _get_current_agent_rails(self, config: dict[str, Any]) -> list[Any]:
        """Return the currently managed rail instances in stable order."""
        rails_list: list[Any] = []
        if self._filesystem_rail is not None:
            rails_list.append(self._filesystem_rail)

        self._skill_rail = self._build_skill_rail(config, include_tools=self._filesystem_rail is None)
        if self._skill_rail is not None:
            rails_list.append(self._skill_rail)

        if self._stream_event_rail is not None:
            rails_list.append(self._stream_event_rail)

        self._tool_prompt_rail = self._build_tool_prompt_rail()
        if self._tool_prompt_rail is not None:
            rails_list.append(self._tool_prompt_rail)

        return rails_list

    def _proc_memory_compression_config(self, agent_config: ReActAgentConfig):
        """Process memory compress config."""
        config_base = get_config()
        memory_compression_config = config_base.get('memory_compression', {}).copy()
        model_name = self._model.model_config.model_name
        model_client_config = self._model.model_client_config
        if memory_compression_config.get("enabled", False):
            message_offloader_config = memory_compression_config.get("message_offloader_config", {}).copy()
            dialogue_compressor_config = memory_compression_config.get("dialogue_compressor_config", {}).copy()
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
                        model_client_config=model_client_config,
                        keep_last_round=dialogue_compressor_config.get("keep_last_round", False),
                    )
                )
            ]
            agent_config.configure_context_processors(processors)

    def _create_evolution_service(self, config: dict[str, Any]):
        """Create evolution service"""
        evolution_cfg: dict = config.get("evolution", {})
        evolution_enabled: bool = evolution_cfg.get("enabled", False)

        has_valid_model_config = False
        if isinstance(config.get("model_client_config"), dict):
            mcc = config["model_client_config"]
            api_key = mcc.get("api_key", "")
            if api_key or os.getenv("API_KEY"):
                has_valid_model_config = True
        if not has_valid_model_config:
            if os.getenv("API_KEY"):
                has_valid_model_config = True

        if evolution_enabled and has_valid_model_config:
            _env_auto_scan = os.getenv("EVOLUTION_AUTO_SCAN")
            if _env_auto_scan is not None:
                evolution_auto_scan: bool = _env_auto_scan.lower() in ("true", "1", "yes")
            else:
                evolution_auto_scan = evolution_cfg.get("auto_scan", False)
            evo_service = EvolutionService(
                llm=self._model,
                model=config.get("model_name", "gpt-4"),
                skills_base_dir=str(_SKILLS_DIR),
                auto_scan=evolution_auto_scan,
            )
            if self._bind_evolution_service(evo_service):
                logger.info("[JiuWenClawDeepAdapter] Evolution has been enabled: auto_scan=%s", evolution_auto_scan)
            else:
                logger.warning(
                    "[JiuWenClawDeepAdapter] Evolution service created but no compatible agent hook was found")
        elif evolution_enabled and not has_valid_model_config:
            logger.warning(
                "[JiuWenClawDeepAdapter] Evolution is enabled but skipped: no valid model API key configured")

    async def _proc_context_compaction(self):
        """Process context compaction config."""
        agent_config = self._instance.react_agent.config
        self._proc_memory_compression_config(agent_config)
        self._instance.react_agent.configure(agent_config)

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

    async def _get_tool_cards(self):
        """Get tool cards."""
        tool_cards = []
        await init_memory_manager_async(
            workspace_dir=self._workspace_dir,
            agent_id=self._agent_name,
        )
        for tool in [memory_search, memory_get, write_memory, edit_memory, read_memory]:
            Runner.resource_mgr.add_tool(tool)
            tool_cards.append(tool.card)
        self._memory_tools_registered = True

        for tool_cls in [WebFreeSearchTool, WebPaidSearchTool, WebFetchWebpageTool]:
            tool_instance = tool_cls()
            Runner.resource_mgr.add_tool(tool_instance)
            tool_cards.append(tool_instance.card)
        self._web_tools_registered = True

        try:
            for cron_tool in self._build_cron_tools():
                tool_cards.append(cron_tool)
        except Exception as exc:
            logger.error("[JiuWenClawDeepAdapter] 定时工具初始化失败， reason=%s", exc)
        return tool_cards

    def _build_cron_tools(self) -> list[Any]:
        """Build cron tools from the shared runtime bridge."""
        return self._cron_runtime.build_tools(context=self._runtime_cron_tool_context)

    async def _register_mcp_server(self):
        """Register MCP server."""
        if self._instance is None:
            logger.warning("[JiuWenClawDeepAdapter] browser MCP registration skipped: instance is None")
            return

        if self._browser_mcp_registered:
            return

        try:
            self._browser_mcp_registered = await register_browser_runtime_mcp_server(
                self._instance,
                tag=f"agent.{self._agent_name}",
            )
        except Exception as exc:
            logger.warning("[JiuWenClawDeepAdapter] browser MCP registration skipped: %s", exc)

    async def create_instance(self, config: dict[str, Any] | None = None) -> None:
        """初始化 DeepAgent 实例.

        Args:
            config: 可选配置，支持以下字段：
                - agent_name: Agent 名称，默认 "main_agent"。
                - workspace_dir: 工作区目录，默认 "workspace/agent"。
                - 其余字段透传给 DeepAgentConfig。
        """
        await self.set_checkpoint()

        config_base = get_config()
        config = config_base.get('react', {}).copy()
        self._config_cache = config.copy()
        self._agent_name = config.get("agent_name", "main_agent")

        model = self._create_model(config_base)
        agent_card = AgentCard(name=self._agent_name, id='jiuwenclaw')
        tool_cards = await self._get_tool_cards()
        self._tool_cards = tool_cards
        rails_list = self._build_agent_rails(config)

        sys_operation = self._create_sys_operation()
        if sys_operation is None:
            raise RuntimeError("sys_operation is not available, maybe task is not running")

        self._sys_operation = sys_operation
        self._instance = create_deep_agent(
            model=model,
            card=agent_card,
            system_prompt=build_identity_prompt(
                mode="agent",
                language=self._resolve_prompt_language(),
                channel=self._resolve_prompt_channel(),
            ),
            tools=tool_cards if tool_cards else [],
            rails=rails_list if rails_list else [],
            enable_task_loop=config.get("enable_task_loop", True),
            max_iterations=config.get("max_iterations", 15),
            sys_operation=sys_operation
        )
        await self._proc_context_compaction()
        self._create_evolution_service(config)
        await self._register_mcp_server()
        logger.info("[JiuWenClawDeepAdapter] 初始化完成: agent_name=%s", self._agent_name)

    async def reload_agent_config(self) -> None:
        """从 config.yaml 重新加载配置，通过 DeepAgent.configure() 热更新当前实例（不新建 DeepAgent）。"""
        if self._instance is None:
            raise RuntimeError("JiuWenClawDeepAdapter 未初始化，请先调用 create_instance()")
        clear_config_cache()
        clear_memory_manager_cache()

        config_base = get_config()
        config = config_base.get('react', {}).copy()
        self._config_cache = config.copy()

        model = self._create_model(config_base)
        self._agent_name = config.get("agent_name", "main_agent")
        agent_card = AgentCard(name=self._agent_name, id='jiuwenclaw')

        old_rails = self._rails_snapshot_for_unregister()
        for rail in old_rails:
            await self._instance.unregister_rail(rail)

        rails_list = self._get_current_agent_rails(config)

        deep_cfg = self._make_deep_agent_config(
            model=model,
            config=config,
            agent_card=agent_card,
            tool_cards=self._tool_cards if self._tool_cards else [],
        )
        self._instance.configure(deep_cfg)
        self._instance.card = agent_card

        for rail in rails_list:
            self._instance.add_rail(rail)

        evo_svc = self._evolution_service
        if evo_svc is not None:
            self._bind_evolution_service(evo_svc)
        if evo_svc is not None and self._model is not None:
            new_model = config.get("model_name", "gpt-4")
            evo_svc.update_llm(self._model, new_model)
            _env_auto_scan = os.getenv("EVOLUTION_AUTO_SCAN")
            if _env_auto_scan is not None:
                evo_svc.auto_scan = _env_auto_scan.lower() in ("true", "1", "yes")
        logger.info("[JiuWenClawDeepAdapter] 配置已热更新（configure），未重启进程")

    def _bind_runtime_cron_context(
            self,
            *,
            channel_id: str | None,
            session_id: str | None,
            metadata: dict[str, Any] | None,
            mode: str | None,
    ) -> tuple[Token[str], Token[str | None], Token[dict[str, Any] | None], Token[str | None]]:
        normalized_channel = str(channel_id or "").strip() or CronTargetChannel.WEB.value
        normalized_mode = str(mode).strip() if isinstance(mode, str) and mode.strip() else None
        normalized_metadata = dict(metadata) if isinstance(metadata, dict) else None
        return (
            _CRON_TOOL_CHANNEL_ID.set(normalized_channel),
            _CRON_TOOL_SESSION_ID.set(session_id),
            _CRON_TOOL_METADATA.set(normalized_metadata),
            _CRON_TOOL_MODE.set(normalized_mode),
        )

    @staticmethod
    def _reset_runtime_cron_context(
            tokens: tuple[Token[str], Token[str | None], Token[dict[str, Any] | None], Token[str | None]],
    ) -> None:
        channel_token, session_token, metadata_token, mode_token = tokens
        _CRON_TOOL_MODE.reset(mode_token)
        _CRON_TOOL_METADATA.reset(metadata_token)
        _CRON_TOOL_SESSION_ID.reset(session_token)
        _CRON_TOOL_CHANNEL_ID.reset(channel_token)

    async def _register_runtime_tools(
            self,
            session_id: str | None,
            mode="plan",
    ) -> None:
        """Register per-request tools for current agent execution."""
        if self._instance is None:
            raise RuntimeError("JiuWenClawDeepAdapter 未初始化，请先调用 create_instance()")

        tool_list = self._instance.ability_manager.list()
        for tool in tool_list:
            if isinstance(tool, ToolCard):
                if tool.name.startswith("todo_"):
                    self._instance.ability_manager.remove(tool.name)

        effective_session_id = session_id or "default"

        if mode == "plan":
            self._instance.react_agent.config.prompt_template = \
                [{"role": "system", "content": build_identity_prompt(
                    mode="plan",
                    language=self._resolve_prompt_language(),
                    channel=self._resolve_prompt_channel(session_id),
                )}]
            todo_tools = create_todos_tool(
                operation=self._instance._deep_config.sys_operation,
                workspace=str(getattr(self._instance._deep_config.workspace, 'workspace_root', '')),
                language=self._resolve_runtime_language()
            )
            for tool in todo_tools:
                Runner.resource_mgr.add_tool(tool)
                self._instance.ability_manager.add(tool.card)
            self._todo_tool_sessions_registered.add(effective_session_id)

            if self._task_planning_rail is None:
                self._task_planning_rail = self._build_task_planning_rail()
                if self._task_planning_rail is not None:
                    await self._instance.register_rail(self._task_planning_rail)
                    logger.info("[JiuWenClawDeepAdapter] TaskPlanningRail registered for plan mode")
        else:
            self._instance.react_agent.config.prompt_template = \
                [{"role": "system", "content": build_identity_prompt(
                    mode="agent",
                    language=self._resolve_prompt_language(),
                    channel=self._resolve_prompt_channel(session_id),
                )}]
            tool_list = self._instance.ability_manager.list()
            for tool in tool_list:
                if isinstance(tool, ToolCard):
                    if tool.name.startswith("todo_"):
                        self._instance.ability_manager.remove(tool.name)

            if self._task_planning_rail is not None:
                await self._instance.unregister_rail(self._task_planning_rail)
                self._task_planning_rail = None
                logger.info("[JiuWenClawDeepAdapter] TaskPlanningRail unregistered for agent mode")

        if not self._memory_tools_registered:
            await init_memory_manager_async(
                workspace_dir=self._workspace_dir,
                agent_id=self._agent_name,
            )
            for tool in [memory_search, memory_get, write_memory, edit_memory, read_memory]:
                Runner.resource_mgr.add_tool(tool)
                self._instance.ability_manager.add(tool.card)
            self._memory_tools_registered = True

        if not self._web_tools_registered:
            for tool_cls in [WebFreeSearchTool, WebPaidSearchTool, WebFetchWebpageTool]:
                tool_instance = tool_cls()
                Runner.resource_mgr.add_tool(tool_instance)
                self._instance.ability_manager.add(tool_instance.card)
            self._web_tools_registered = True

    async def process_interrupt(self, request: AgentRequest) -> AgentResponse:
        """处理 interrupt 请求.

        根据 intent 分流：
        - pause: 暂停循环（不取消任务）
        - resume: 恢复已暂停的循环
        - cancel: 取消所有运行中的任务
        - supplement: 取消当前任务但保留 todo

        Args:
            request: AgentRequest，params 中可包含：
                - intent: 中断意图 ('pause' | 'cancel' | 'resume' | 'supplement')
                - new_input: 新的用户输入（用于切换任务）

        Returns:
            AgentResponse 包含 interrupt_result 事件数据
        """
        intent = request.params.get("intent", "cancel")
        new_input = request.params.get("new_input")

        success = True

        if intent == "pause":
            if self._instance is not None and hasattr(self._instance, 'pause'):
                self._instance.pause()
                logger.info(
                    "[JiuWenClawDeepAdapter] interrupt: 已暂停 ReAct 循环 request_id=%s",
                    request.request_id,
                )
            message = "任务已暂停"

        elif intent == "resume":
            if self._instance is not None and hasattr(self._instance, 'resume'):
                self._instance.resume()
                logger.info(
                    "[JiuWenClawDeepAdapter] interrupt: 已恢复 ReAct 循环 request_id=%s",
                    request.request_id,
                )
            message = "任务已恢复"

        elif intent == "supplement":
            if self._instance is not None and hasattr(self._instance, 'resume'):
                self._instance.resume()
            message = "任务已切换"

        else:
            if self._instance is not None and hasattr(self._instance, 'resume'):
                self._instance.resume()

            if request.session_id:
                try:
                    await self._cancel_pending_todos(request.session_id)
                except Exception as exc:
                    logger.warning("[JiuWenClawDeepAdapter] 标记 todo cancelled 失败: %s", exc)

            if new_input:
                message = "已切换到新任务"
            else:
                message = "任务已取消"

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

        if self._model is not None:
            return True

        return False

    async def handle_user_answer(self, request: AgentRequest) -> AgentResponse:
        """Handle chat.user_answer request, route user answer to evolution approval Future."""
        request_id = request.params.get("request_id", "") if isinstance(request.params, dict) else ""
        answers = request.params.get("answers", []) if isinstance(request.params, dict) else []
        resolved = False
        target = self._get_evolution_target()
        if target is not None:
            resolve_fn = getattr(target, 'resolve_evolution_approval', None)
            if resolve_fn is not None:
                resolved = resolve_fn(request_id, answers)
        return AgentResponse(
            request_id=request.request_id,
            channel_id=request.channel_id,
            ok=True,
            payload={"accepted": True, "resolved": resolved},
            metadata=request.metadata,
        )

    async def _handle_slash_command(self, query: str) -> dict[str, Any] | None:
        """Intercept /evolve and /solidify before agent invocation.

        Returns result dict if handled, None to proceed normally.
        """
        stripped = query.strip()

        if stripped.startswith("/solidify"):
            if self._evolution_service is None:
                return {"output": "演进功能未启用。", "result_type": "error"}
            return self._evolution_service.handle_solidify_command(stripped)

        if stripped.startswith("/evolve"):
            if self._evolution_service is None:
                return {"output": "演进功能未启用。", "result_type": "error"}
            return await self._evolution_service.handle_evolve_command(
                stripped, None, []
            )

        return None

    async def _cancel_pending_todos(self, session_id: str) -> None:
        """将未完成的 todo 项标记为 cancelled."""
        if self._instance is None:
            return

        deep_config = self._instance._deep_config
        modify_tool = TodoModifyTool(
            operation=deep_config.sys_operation,
            workspace=str(getattr(deep_config.workspace, "workspace_root", "")),
            language=resolve_language(),
        )
        modify_tool.set_file(session_id)

        try:
            todos = await modify_tool.load_todos()
            if not todos:
                return

            _DONE_STATUSES = {
                TodoStatus.COMPLETED.value,
                TodoStatus.CANCELLED.value,
            }

            ids_to_cancel = []
            for todo in todos:
                if todo.status.value not in _DONE_STATUSES:
                    ids_to_cancel.append(todo.id)

            if ids_to_cancel:
                await modify_tool._cancel_todos(ids_to_cancel, todos)
                logger.info(
                    "[JiuWenClawDeepAdapter] 已将 session %s 的未完成任务标记为 cancelled",
                    session_id,
                )
        except Exception as exc:
            logger.warning("[JiuWenClawDeepAdapter] 标记 todo cancelled 失败: %s", exc)

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
            raise RuntimeError("JiuWenClawDeepAdapter 未初始化，请先调用 create_instance()")

        if not self._has_valid_model_config():
            return AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": "模型未正确配置，请先配置模型信息"},
                metadata=request.metadata,
            )

        session_id = request.session_id or "default"
        query = request.params.get("query", "")

        slash_result = await self._handle_slash_command(query)
        if slash_result is not None:
            content = slash_result.get("output", str(slash_result))
            return AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=slash_result.get("result_type") != "error",
                payload={"content": content},
                metadata=request.metadata,
            )

        if self._compaction_manager:
            self._compaction_manager.add_message("user", query)
            memory_mgr = await get_memory_manager(
                agent_id=self._agent_name,
                workspace_dir=self._workspace_dir
            )
            if memory_mgr:
                await self._compaction_manager.check_and_compact(memory_mgr)

        cron_context_tokens = self._bind_runtime_cron_context(
            channel_id=request.channel_id,
            session_id=request.session_id,
            metadata=request.metadata,
            mode=request.params.get("mode", "plan"),
        )
        try:
            await self._register_runtime_tools(request.session_id, request.params.get("mode", "plan"))
            result = await Runner.run_agent(agent=self._instance, inputs=inputs)
        except asyncio.CancelledError:
            logger.info("[JiuWenClawDeepAdapter] Agent 任务被取消: request_id=%s session_id=%s", request.request_id,
                        session_id)
            raise
        except Exception as e:
            logger.error("[JiuWenClawDeepAdapter] Agent 任务执行异常: %s", e)
            raise
        finally:
            self._reset_runtime_cron_context(cron_context_tokens)

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
            raise RuntimeError("JiuWenClawDeepAdapter 未初始化，请先调用 create_instance()")

        if not self._has_valid_model_config():
            yield AgentResponseChunk(
                request_id=request.request_id,
                channel_id=request.channel_id,
                payload={"event_type": "chat.error", "error": "模型未正确配置，请先配置模型信息"},
                is_complete=True,
            )
            return

        session_id = request.session_id or "default"
        rid = request.request_id
        cid = request.channel_id
        query = request.params.get("query", "")

        slash_result = await self._handle_slash_command(query)
        if slash_result is not None:
            content = slash_result.get("output", str(slash_result))
            yield AgentResponseChunk(
                request_id=rid,
                channel_id=cid,
                payload={"event_type": "chat.final", "content": content},
                is_complete=True,
            )
            return

        if self._compaction_manager:
            self._compaction_manager.add_message("user", query)
            memory_mgr = await get_memory_manager(
                agent_id=self._agent_name,
                workspace_dir=self._workspace_dir
            )
            if memory_mgr:
                await self._compaction_manager.check_and_compact(memory_mgr)

        has_streamed_content = False
        accumulated_text = ""
        accumulated_reasoning = ""

        async def _flush_text():
            nonlocal accumulated_text
            if not accumulated_text:
                return
            yield AgentResponseChunk(
                request_id=rid,
                channel_id=cid,
                payload={"event_type": "chat.final", "content": accumulated_text},
                is_complete=False,
            )
            accumulated_text = ""

        async def _flush_reasoning():
            nonlocal accumulated_reasoning
            if not accumulated_reasoning:
                return
            yield AgentResponseChunk(
                request_id=rid,
                channel_id=cid,
                payload={"event_type": "chat.reasoning", "content": accumulated_reasoning},
                is_complete=False,
            )
            accumulated_reasoning = ""

        cron_context_tokens = self._bind_runtime_cron_context(
            channel_id=request.channel_id,
            session_id=request.session_id,
            metadata=request.metadata,
            mode=request.params.get("mode", "plan"),
        )
        try:
            await self._register_runtime_tools(request.session_id, request.params.get("mode", "plan"))
            if self._stream_event_rail is not None:
                self._stream_event_rail.reset_abort()
            async for chunk in Runner.run_agent_streaming(self._instance, inputs):
                if not (hasattr(chunk, "type") and hasattr(chunk, "payload")):
                    parsed = self._parse_stream_chunk(chunk)
                    if parsed is not None:
                        if accumulated_text:
                            yield AgentResponseChunk(
                                request_id=rid,
                                channel_id=cid,
                                payload={"event_type": "chat.final", "content": accumulated_text},
                                is_complete=False,
                            )
                            accumulated_text = ""
                        if accumulated_reasoning:
                            yield AgentResponseChunk(
                                request_id=rid,
                                channel_id=cid,
                                payload={"event_type": "chat.reasoning", "content": accumulated_reasoning},
                                is_complete=False,
                            )
                            accumulated_reasoning = ""
                        yield AgentResponseChunk(
                            request_id=rid,
                            channel_id=cid,
                            payload=parsed,
                            is_complete=False,
                        )
                    continue

                chunk_type = chunk.type

                if chunk_type == "llm_reasoning":
                    if accumulated_text:
                        yield AgentResponseChunk(
                            request_id=rid,
                            channel_id=cid,
                            payload={"event_type": "chat.final", "content": accumulated_text},
                            is_complete=False,
                        )
                        accumulated_text = ""
                    content = (
                        (chunk.payload.get("content", "") or chunk.payload.get("output", ""))
                        if isinstance(chunk.payload, dict)
                        else str(chunk.payload)
                    )
                    if content:
                        accumulated_reasoning += content
                        if len(accumulated_reasoning) >= _STREAM_CHAR_THRESHOLD:
                            yield AgentResponseChunk(
                                request_id=rid,
                                channel_id=cid,
                                payload={"event_type": "chat.reasoning", "content": accumulated_reasoning},
                                is_complete=False,
                            )
                            accumulated_reasoning = ""
                    continue

                if chunk_type == "llm_output":
                    has_streamed_content = True
                    if accumulated_reasoning:
                        yield AgentResponseChunk(
                            request_id=rid,
                            channel_id=cid,
                            payload={"event_type": "chat.reasoning", "content": accumulated_reasoning},
                            is_complete=False,
                        )
                        accumulated_reasoning = ""
                    content = (
                        chunk.payload.get("content", "")
                        if isinstance(chunk.payload, dict)
                        else str(chunk.payload)
                    )
                    if content:
                        accumulated_text += content
                        if len(accumulated_text) >= _STREAM_CHAR_THRESHOLD:
                            yield AgentResponseChunk(
                                request_id=rid,
                                channel_id=cid,
                                payload={"event_type": "chat.final", "content": accumulated_text},
                                is_complete=False,
                            )
                            accumulated_text = ""
                    continue

                if chunk_type == "answer":
                    if accumulated_text:
                        yield AgentResponseChunk(
                            request_id=rid,
                            channel_id=cid,
                            payload={"event_type": "chat.final", "content": accumulated_text},
                            is_complete=False,
                        )
                        accumulated_text = ""
                    if accumulated_reasoning:
                        yield AgentResponseChunk(
                            request_id=rid,
                            channel_id=cid,
                            payload={"event_type": "chat.reasoning", "content": accumulated_reasoning},
                            is_complete=False,
                        )
                        accumulated_reasoning = ""
                    if has_streamed_content:
                        continue
                    parsed = self._parse_stream_chunk(chunk)
                    if parsed is not None:
                        yield AgentResponseChunk(
                            request_id=rid,
                            channel_id=cid,
                            payload=parsed,
                            is_complete=False,
                        )
                    continue

                if accumulated_text:
                    yield AgentResponseChunk(
                        request_id=rid,
                        channel_id=cid,
                        payload={"event_type": "chat.final", "content": accumulated_text},
                        is_complete=False,
                    )
                    accumulated_text = ""
                if accumulated_reasoning:
                    yield AgentResponseChunk(
                        request_id=rid,
                        channel_id=cid,
                        payload={"event_type": "chat.reasoning", "content": accumulated_reasoning},
                        is_complete=False,
                    )
                    accumulated_reasoning = ""
                parsed = self._parse_stream_chunk(chunk)
                if parsed is not None:
                    yield AgentResponseChunk(
                        request_id=rid,
                        channel_id=cid,
                        payload=parsed,
                        is_complete=False,
                    )

            if accumulated_text:
                yield AgentResponseChunk(
                    request_id=rid,
                    channel_id=cid,
                    payload={"event_type": "chat.final", "content": accumulated_text},
                    is_complete=False,
                )
            if accumulated_reasoning:
                yield AgentResponseChunk(
                    request_id=rid,
                    channel_id=cid,
                    payload={"event_type": "chat.reasoning", "content": accumulated_reasoning},
                    is_complete=False,
                )
        except asyncio.CancelledError:
            logger.info("[JiuWenClawDeepAdapter] 流式任务被取消: request_id=%s session_id=%s", rid, session_id)
            raise
        except Exception as exc:
            logger.exception("[JiuWenClawDeepAdapter] 流式任务异常: %s", exc)
            yield AgentResponseChunk(
                request_id=rid,
                channel_id=cid,
                payload={"event_type": "chat.error", "error": str(exc)},
                is_complete=False,
            )
        finally:
            self._reset_runtime_cron_context(cron_context_tokens)

        yield AgentResponseChunk(
            request_id=rid,
            channel_id=cid,
            payload=None,
            is_complete=True,
        )

    @staticmethod
    def _parse_stream_chunk(chunk, *, _has_streamed_content: bool = False) -> dict | None:
        """将 SDK OutputSchema 转为前端可消费的 payload dict.

        Args:
            chunk: OutputSchema 或 dict
            _has_streamed_content: 是否已通过 llm_output 流式发送过内容

        Returns:
            dict  – 含 event_type 的 payload，或 None（需跳过的帧）。
        """
        try:
            if hasattr(chunk, "type") and hasattr(chunk, "payload"):
                chunk_type = chunk.type
                payload = chunk.payload

                if chunk_type == "controller_output" and payload is not None:
                    inner_t = getattr(payload, "type", None)
                    inner_val = (
                        getattr(inner_t, "value", inner_t) if inner_t is not None else None
                    )
                    if inner_val == "task_completion":
                        return None

                if chunk_type == "llm_output":
                    content = (
                        payload.get("content", "")
                        if isinstance(payload, dict)
                        else str(payload)
                    )
                    if not content:
                        return None
                    return {"event_type": "chat.delta", "content": content}

                if chunk_type == "llm_reasoning":
                    content = (
                        (payload.get("content", "") or payload.get("output", ""))
                        if isinstance(payload, dict)
                        else str(payload)
                    )
                    if not content:
                        return None
                    return {"event_type": "chat.reasoning", "content": content}

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
                        is_chunked = (
                            output.get("chunked", False)
                            if isinstance(output, dict)
                            else False
                        )
                    else:
                        content = str(payload)
                        is_chunked = False

                    if _has_streamed_content and not is_chunked:
                        return {"event_type": "chat.final", "content": ""}

                    if not content:
                        return None
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

                if isinstance(payload, dict):
                    if "traceId" in payload or "invokeId" in payload:
                        return None
                    content = payload.get("content") or payload.get("output")
                    if not content:
                        return None
                else:
                    content = str(payload)
                return {"event_type": "chat.delta", "content": content}

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