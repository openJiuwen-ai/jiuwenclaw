# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""JiuWenClaw Deep Adapter - 基于 openjiuwen DeepAgent 的适配器实现.

此模块实现 AgentAdapter 协议，封装 Deep SDK 的所有专属逻辑。
公共编排逻辑（session 队列、Skills 路由、heartbeat 等）由 Facade 层处理。
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, List, Tuple

from dotenv import load_dotenv
from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig, Model
from openjiuwen.core.foundation.store.base_embedding import EmbeddingConfig
from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.runner import Runner
from openjiuwen.core.session.checkpointer import CheckpointerFactory
from openjiuwen.core.session.checkpointer.checkpointer import CheckpointerConfig
from openjiuwen.core.session.checkpointer.persistence import PersistenceCheckpointerProvider
from openjiuwen.core.single_agent import AgentCard, ReActAgentConfig
from openjiuwen.core.sys_operation import SysOperation, SysOperationCard, OperationMode, LocalWorkConfig
from openjiuwen.harness import (
    AudioModelConfig,
    DeepAgent,
    DeepAgentConfig,
    VisionModelConfig,
)
from openjiuwen.harness.factory import create_deep_agent
from openjiuwen.harness.prompts import resolve_language
from openjiuwen.harness.rails import SkillUseRail, TaskPlanningRail, SecurityRail, SkillEvolutionRail
from openjiuwen.harness.rails.context_engineering_rail import ContextEngineeringRail
from openjiuwen.harness.rails.filesystem_rail import FileSystemRail
from openjiuwen.harness.rails.heartbeat_rail import HeartbeatRail
from openjiuwen.agent_evolving.online.schema import (
    EvolutionContext,
    EvolutionRecord,
    EvolutionTarget,
)
from openjiuwen.agent_evolving.online.signal_detector import SignalDetector
from openjiuwen.harness.rails.memory_rail import MemoryRail
from openjiuwen.harness.subagents.browser_agent import build_browser_agent_config
from openjiuwen.harness.tools import (
    WebFetchWebpageTool,
    WebFreeSearchTool,
    WebPaidSearchTool,
    create_audio_tools,
    create_vision_tools,
)
from openjiuwen.harness.tools.todo import TodoStatus, TodoModifyTool
from openjiuwen.harness.workspace.workspace import Workspace, WorkspaceNode

from jiuwenclaw.agentserver.deep_agent.cron_runtime import CronRuntimeBridge
from jiuwenclaw.agentserver.deep_agent.interrupt.interrupt_helpers import (
    build_permission_rail,
    convert_interactions_to_ask_user_question,
)
from jiuwenclaw.agentserver.deep_agent.prompt_builder import build_identity_prompt
from jiuwenclaw.agentserver.deep_agent.rails import (
    JiuClawStreamEventRail
)
from jiuwenclaw.agentserver.memory import clear_memory_manager_cache
from jiuwenclaw.agentserver.memory.config import clear_config_cache, get_memory_mode
from jiuwenclaw.agentserver.deep_agent.permissions.checker import TOOL_PERMISSION_CHANNEL_ID
from jiuwenclaw.agentserver.tools.multimodal_config import (
    apply_audio_model_config_from_yaml,
    apply_video_model_config_from_yaml,
    apply_vision_model_config_from_yaml,
)
from jiuwenclaw.agentserver.tools.video_tools import video_understanding

from jiuwenclaw.agentserver.tools import SendFileToolkit
from jiuwenclaw.agentserver.tools.multi_session_toolkits import MultiSessionToolkit
from jiuwenclaw.agentserver.tools.xiaoyi_phone_tools import (
    get_user_location,
    create_note,
    search_notes,
    modify_note,
    create_calendar_event,
    search_calendar_event,
    search_contact,
    search_photo_gallery,
    upload_photo,
    search_file,
    upload_file,
    call_phone,
    send_message,
    search_message,
    create_alarm,
    search_alarms,
    modify_alarm,
    delete_alarm,
    xiaoyi_collection,
    xiaoyi_gui_agent,
    image_reading,
)
from jiuwenclaw.config import get_config, resolve_env_vars
from jiuwenclaw.gateway.cron import CronController, CronTargetChannel
from jiuwenclaw.schema.agent import AgentRequest, AgentResponse, AgentResponseChunk
from jiuwenclaw.utils import get_env_file, get_agent_root_dir, get_checkpoint_dir, get_agent_skills_dir

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


def _parse_int(value: Any, default: int) -> int:
    """Parse integer-like values safely."""
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


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
        self._vision_tools_registered: bool = False
        self._audio_tools_registered: bool = False
        self._video_tool_registered: bool = False
        self._model: Model | None = None
        self._model_client_config: ModelClientConfig | None = None
        self._model_request_config: ModelRequestConfig | None = None
        self._config_cache: dict[str, Any] = {}
        self._filesystem_rail: FileSystemRail | None = None
        self._skill_rail: SkillUseRail | None = None
        self._stream_event_rail: JiuClawStreamEventRail | None = None
        self._task_planning_rail: TaskPlanningRail | None = None
        self._context_engineering_rail: ContextEngineeringRail | None = None
        self._security_rail: SecurityRail | None = None
        self._memory_rail: MemoryRail | None = None
        self._heartbeat_rail: HeartbeatRail | None = None
        self._skill_evolution_rail: SkillEvolutionRail | None = None
        self._pending_evolution_data: dict[str, dict] = {}
        self._permission_rail: Any = None
        self._tool_cards = None
        self._sys_operation = None
        self._vision_model_config: VisionModelConfig | None = None
        self._audio_model_config: AudioModelConfig | None = None
        self._video_model_config: bool = False
        self._vision_tools: list[Any] = []
        self._audio_tools: list[Any] = []
        self._xiaoyi_phone_tools_registered: bool = False
        self._paid_search_registered: bool = False
        self._paid_search_tool: WebPaidSearchTool | None = None
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
    def _browser_runtime_enabled() -> bool:
        """Whether browser runtime support is enabled for DeepAgent subagent wiring."""
        value = str(
            os.getenv("PLAYWRIGHT_RUNTIME_MCP_ENABLED")
            or os.getenv("BROWSER_RUNTIME_MCP_ENABLED")
            or ""
        ).strip().lower()
        return value in {"1", "true", "yes", "on"}

    def _build_browser_subagents(
            self,
            model: Model,
            config: dict[str, Any],
    ) -> list[Any] | None:
        """Build browser subagent config when browser runtime is enabled."""
        if not self._browser_runtime_enabled():
            return None

        return [
            build_browser_agent_config(
                model,
                workspace=self._workspace_dir or "./",
                language=self._resolve_runtime_language(),
                max_iterations=_parse_int(
                    os.getenv("BROWSER_AGENT_MAX_ITERATIONS"),
                    config.get("max_iterations", 15),
                ),
            )
        ]

    def _build_vision_model_config(
            self,
            config_base: dict[str, Any],
    ) -> VisionModelConfig | None:
        """Build DeepAgent vision config from service config/env mapping."""
        apply_vision_model_config_from_yaml(config_base)
        api_key = str(os.getenv("VISION_API_KEY", "")).strip()
        base_url = str(
            os.getenv("VISION_BASE_URL")
            or os.getenv("VISION_API_BASE")
            or ""
        ).strip()
        model_name = str(
            os.getenv("VISION_MODEL")
            or os.getenv("VISION_MODEL_NAME")
            or ""
        ).strip()
        if not api_key or not base_url or not model_name:
            logger.info(
                "[JiuWenClawDeepAdapter] vision tools skipped: incomplete config"
            )
            return None
        return VisionModelConfig(
            api_key=api_key,
            base_url=base_url,
            model=model_name,
            max_retries=_parse_int(os.getenv("VISION_MAX_RETRIES"), 3),
        )

    def _build_audio_model_config(
            self,
            config_base: dict[str, Any],
    ) -> AudioModelConfig | None:
        """Build DeepAgent audio config from service config/env mapping."""
        apply_audio_model_config_from_yaml(config_base)
        api_key = str(os.getenv("AUDIO_API_KEY", "")).strip()
        base_url = str(
            os.getenv("AUDIO_BASE_URL")
            or os.getenv("AUDIO_API_BASE")
            or ""
        ).strip()
        if not api_key or not base_url:
            logger.info(
                "[JiuWenClawDeepAdapter] audio tools skipped: incomplete config"
            )
            return None
        transcription_model = str(
            os.getenv("AUDIO_TRANSCRIPTION_MODEL")
            or os.getenv("AUDIO_MODEL_NAME")
            or ""
        ).strip()
        question_answering_model = str(
            os.getenv("AUDIO_QUESTION_ANSWERING_MODEL")
            or os.getenv("AUDIO_MODEL_NAME")
            or ""
        ).strip()
        config_kwargs: dict[str, Any] = {
            "api_key": api_key,
            "base_url": base_url,
            "max_retries": _parse_int(os.getenv("AUDIO_MAX_RETRIES"), 3),
            "http_timeout": _parse_int(os.getenv("AUDIO_HTTP_TIMEOUT"), 20),
            "max_audio_bytes": _parse_int(
                os.getenv("AUDIO_MAX_AUDIO_BYTES"),
                25 * 1024 * 1024,
            ),
        }
        acr_access_key = str(os.getenv("ACR_ACCESS_KEY", "")).strip()
        acr_access_secret = str(os.getenv("ACR_ACCESS_SECRET", "")).strip()
        acr_base_url = str(os.getenv("ACR_BASE_URL", "")).strip()
        if acr_access_key:
            config_kwargs["acr_access_key"] = acr_access_key
        if acr_access_secret:
            config_kwargs["acr_access_secret"] = acr_access_secret
        if acr_base_url:
            config_kwargs["acr_base_url"] = acr_base_url
        if transcription_model:
            config_kwargs["transcription_model"] = transcription_model
        if question_answering_model:
            config_kwargs[
                "question_answering_model"
            ] = question_answering_model
        return AudioModelConfig(**config_kwargs)

    def _build_video_model_config(
            self,
            config_base: dict[str, Any],
    ) -> bool:
        """Build DeepAgent video config from service config/env mapping."""
        apply_video_model_config_from_yaml(config_base)
        if not os.getenv("VIDEO_API_KEY"):
            logger.info(
                "[JiuWenClawDeepAdapter] video tools skipped: incomplete config"
            )
            return False
        return True

    def _refresh_multimodal_configs(
            self,
            config_base: dict[str, Any],
    ) -> None:
        """Refresh cached multimodal configs and live tool instances."""
        self._vision_model_config = self._build_vision_model_config(config_base)
        self._audio_model_config = self._build_audio_model_config(config_base)
        self._video_model_config = self._build_video_model_config(config_base)

        for tool in self._vision_tools:
            tool.vision_model_config = self._vision_model_config
        for tool in self._audio_tools:
            tool.audio_model_config = self._audio_model_config

    def _sync_tool_group(
            self,
            *,
            current_tools: list[Any],
            registered: bool,
            enabled: bool,
            create_fn: Callable[[], list[Any]],
            warn_label: str,
    ) -> tuple[list[Any], bool]:
        """统一处理一组工具的热更新：启用时注册，禁用时移除。

        Returns:
            (updated_tools, updated_registered)
        """
        if not enabled:
            if registered:
                self._remove_registered_tools(current_tools)
                self._prune_tool_cards({t.card.name for t in current_tools})
            return [], False
        if not registered:
            try:
                new_tools = create_fn()
                for tool in new_tools:
                    Runner.resource_mgr.add_tool(tool)
                    self._append_tool_card(tool.card)
                    if self._instance is not None and hasattr(self._instance, "ability_manager"):
                        self._instance.ability_manager.add(tool.card)
                return new_tools, bool(new_tools)
            except Exception as exc:
                logger.warning(
                    "[JiuWenClawDeepAdapter] %s reload failed: %s", warn_label, exc
                )
                return [], False
        return current_tools, registered

    def _remove_registered_tools(self, tools: list[Any]) -> None:
        """Remove tool instances from ability manager and resource manager."""
        if not tools:
            return
        for tool in tools:
            try:
                Runner.resource_mgr.remove_tool(tool.card.id)
            except Exception as exc:
                logger.warning(
                    "[JiuWenClawDeepAdapter] remove tool failed: %s",
                    exc,
                )
            if self._instance is not None and hasattr(
                    self._instance,
                    "ability_manager",
            ):
                try:
                    self._instance.ability_manager.remove(tool.card.name)
                except Exception:
                    logger.debug(
                        "[JiuWenClawDeepAdapter] ability remove skipped for %s",
                        tool.card.name,
                        exc_info=True,
                    )

    def _append_tool_card(self, card: ToolCard) -> None:
        """Append tool card if it is not already tracked."""
        if self._tool_cards is None:
            self._tool_cards = []
        existing_names = {
            item.card.name if hasattr(item, "card") else item.name
            for item in self._tool_cards
        }
        if card.name not in existing_names:
            self._tool_cards.append(card)

    def _prune_tool_cards(self, tool_names: set[str]) -> None:
        """Remove tracked tool cards by tool name."""
        if not self._tool_cards:
            return
        self._tool_cards = [
            item
            for item in self._tool_cards
            if (
                   item.card.name if hasattr(item, "card") else item.name
               ) not in tool_names
        ]

    def _sync_multimodal_tools_for_runtime(self) -> None:
        """Sync multimodal tool registration after config reload."""
        self._vision_tools, self._vision_tools_registered = self._sync_tool_group(
            current_tools=self._vision_tools,
            registered=self._vision_tools_registered,
            enabled=self._vision_model_config is not None,
            create_fn=lambda: create_vision_tools(
                language=self._resolve_runtime_language(),
                vision_model_config=self._vision_model_config,
            ),
            warn_label="vision tools",
        )

        self._audio_tools, self._audio_tools_registered = self._sync_tool_group(
            current_tools=self._audio_tools,
            registered=self._audio_tools_registered,
            enabled=self._audio_model_config is not None,
            create_fn=lambda: create_audio_tools(
                language=self._resolve_runtime_language(),
                audio_model_config=self._audio_model_config,
            ),
            warn_label="audio tools",
        )

        _, self._video_tool_registered = self._sync_tool_group(
            current_tools=[video_understanding],
            registered=self._video_tool_registered,
            enabled=bool(self._video_model_config),
            create_fn=lambda: [video_understanding],
            warn_label="video tool",
        )

    def _sync_paid_search_tool_for_runtime(self) -> None:
        """Sync paid-search tool registration after config reload."""
        tools, self._paid_search_registered = self._sync_tool_group(
            current_tools=[self._paid_search_tool] if self._paid_search_tool else [],
            registered=self._paid_search_registered,
            enabled=any(
                os.environ.get(key)
                for key in ("PERPLEXITY_API_KEY", "SERPER_API_KEY", "JINA_API_KEY")
            ),
            create_fn=lambda: [WebPaidSearchTool(language=self._resolve_runtime_language())],
            warn_label="paid search tool",
        )
        self._paid_search_tool = tools[0] if tools else None

    @staticmethod
    async def set_checkpoint():
        try:
            PersistenceCheckpointerProvider()
            checkpoint_path = get_checkpoint_dir()
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
        self._model_client_config = client_config
        self._model_request_config = model_config
        self._model = Model(
            model_client_config=client_config,
            model_config=model_config,
        )
        return self._model

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
                work_config=LocalWorkConfig(shell_allowlist=None),
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
                skills_dir=str(get_agent_skills_dir()),
                skill_mode=skill_mode,
                include_tools=include_tools,
            )
            logger.info("[JiuWenClawDeepAdapter] SkillUseRail create success")
        except Exception as exc:
            logger.warning("[JiuWenClawDeepAdapter] SkillUseRail create failed: %s", exc)
            skill_rail = None
        return skill_rail

    def _build_context_engineering_rail(self, config: dict[str, Any]) -> ContextEngineeringRail | None:
        """Build ContextEngineeringRail with user config merged into presets.

        用户提供的 processor 配置（dict 格式）会与预置配置做字段级别合并，
        只覆盖用户指定的字段，其他使用预置默认值。
        """
        try:
            user_processors: List[Tuple[str, dict]] = []
            context_engine_cfg = config.get("context_engine_config", {})

            offloader_cfg = context_engine_cfg.get("message_offloader_config", {})
            if isinstance(offloader_cfg, dict) and offloader_cfg:
                user_processors.append(("MessageOffloader", offloader_cfg))

            compressor_cfg = context_engine_cfg.get("dialogue_compressor_config", {})
            if isinstance(compressor_cfg, dict) and compressor_cfg:
                user_processors.append(("DialogueCompressor", compressor_cfg))

            # 构建 ContextEngineeringRail
            context_rail = ContextEngineeringRail(
                processors=user_processors if user_processors else None,
                preset=True,
            )
            logger.info(
                "[JiuWenClawDeepAdapter] ContextEngineeringRail create success, "
                "user_processors=%s",
                [p[0] for p in user_processors] if user_processors else "none"
            )
            return context_rail
        except Exception as exc:
            logger.warning("[JiuWenClawDeepAdapter] ContextEngineeringRail create failed: %s", exc)
            return None

    def _build_skill_evolution_rail(self, config: dict[str, Any]) -> SkillEvolutionRail | None:
        """Build SkillEvolutionRail."""
        try:
            _env_auto_scan = os.getenv("EVOLUTION_AUTO_SCAN")
            if _env_auto_scan is not None:
                evolution_auto_scan: bool = _env_auto_scan.lower() in ("true", "1", "yes")
            else:
                evolution_auto_scan = config.get("evolution", {}).get("auto_scan", False)
            skill_evolution_rail = SkillEvolutionRail(
                skills_dir=str(get_agent_skills_dir()),
                llm=self._model,
                model=config.get("model_name", "gpt-4"),
                auto_scan=evolution_auto_scan,
                auto_save=False
            )
            self._skill_evolution_rail = skill_evolution_rail
            logger.info("[JiuWenClaw] SkillEvolutionRail create success")
        except Exception as exc:
            logger.warning("[JiuWenClaw] SkillEvolutionRail create failed: %s", exc)
            skill_evolution_rail = None
        return skill_evolution_rail

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
            task_planning_rail = TaskPlanningRail()
            logger.info("[JiuWenClawDeepAdapter] TaskPlanningRail create success")
        except Exception as exc:
            logger.warning("[JiuWenClawDeepAdapter] TaskPlanningRail create failed: %s", exc)
            task_planning_rail = None
        return task_planning_rail

    def _build_security_rail(self) -> SecurityRail | None:
        """Build SecurityPromptRail."""
        try:
            security_prompt_rail = SecurityRail()
            logger.info("[JiuWenClawDeepAdapter] SecurityPromptRail create success")
        except Exception as exc:
            logger.warning("[JiuWenClawDeepAdapter] SecurityPromptRail create failed: %s", exc)
            security_prompt_rail = None
        return security_prompt_rail

    def _build_memory_rail(self) -> MemoryRail | None:
        try:
            config = get_config()
            embed_config = config.get("embed") if isinstance(config, dict) else None
            if (not isinstance(embed_config, dict) or not embed_config.get("embed_api_key")
                    or not embed_config.get("embed_base_url") or not embed_config.get("embed_model")):
                logger.warning("[JiuWenClawDeepAdapter] MemoryRail create failed: No available embedding config")
            memory_rail = MemoryRail(
                embedding_config=EmbeddingConfig(
                    model_name=embed_config.get("embed_model"),
                    base_url=embed_config.get("embed_base_url"),
                    api_key=embed_config.get("embed_api_key")
                ),
            )
            logger.info("[JiuWenClawDeepAdapter] MemoryRail create success")
        except Exception as exc:
            logger.warning("[JiuWenClawDeepAdapter] MemoryRail create failed: %s", exc)
            memory_rail = None
        return memory_rail


    def _build_heartbeat_rail(self) -> HeartbeatRail | None:
        """Build HeartbeatRail."""
        try:
            heartbeat_rail = HeartbeatRail()
            logger.info("[JiuWenClawDeepAdapter] HeartbeatRail create success")
        except Exception as exc:
            logger.warning("[JiuWenClawDeepAdapter] HeartbeatRail create failed: %s", exc)
            heartbeat_rail = None
        return heartbeat_rail

    def _build_agent_rails(self, config: dict[str, Any], config_base: dict[str, Any]) -> list[Any]:
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
            _RailBuildInfo("_task_planning_rail", self._build_task_planning_rail),
            _RailBuildInfo("_security_rail", self._build_security_rail),
            _RailBuildInfo("_heartbeat_rail", self._build_heartbeat_rail),
            _RailBuildInfo("_permission_rail", build_permission_rail, {"config": config_base, "llm": self._model,
                                                                       "model_name": config_base.get("models", {}).get(
                                                                           "default", {}).get("model_client_config",
                                                                                              {}).get("model_name",
                                                                                                      "gpt-4")}),
        ]
        if config.get("context_engine_config", {}).get("enabled", False):
            rail_infos.append(
                _RailBuildInfo(
                    "_context_engineering_rail",
                    self._build_context_engineering_rail,
                    {"config": config},
                )
            )
        if config.get("evolution", {}).get("enabled", False):
            rail_infos.append(
                _RailBuildInfo(
                    "_skill_evolution_rail",
                    self._build_skill_evolution_rail,
                    {"config": config},
                )
            )

        if get_memory_mode(config_base) == "local":
            rail_infos.append(_RailBuildInfo("_memory_rail", self._build_memory_rail))

        rails_list = []
        for info in rail_infos:
            logger.info("[JiuWenClawDeepAdapter] Building rail: %s with params: %s", info.attr_name, info.params)
            rail_instance = info.build_func(**info.params)
            if rail_instance is not None:
                setattr(self, info.attr_name, rail_instance)
                rails_list.append(rail_instance)
                logger.info("[JiuWenClawDeepAdapter] Rail %s built successfully and added to rails_list",
                            info.attr_name)
            else:
                logger.warning("[JiuWenClawDeepAdapter] Rail %s build returned None", info.attr_name)
        logger.info("[JiuWenClawDeepAdapter] Total rails built: %d, rail names: %s", len(rails_list),
                    [type(r).__name__ for r in rails_list])
        return rails_list

    def _make_deep_agent_config(
            self,
            *,
            model: Model,
            config: dict[str, Any],
            agent_card: AgentCard,
            tool_cards: list[Any],
            rails: list[Any] | None = None,
    ) -> DeepAgentConfig:
        """与 create_deep_agent() 中 DeepAgentConfig 构造保持一致."""
        resolved_language = self._resolve_runtime_language()
        workspace_obj = Workspace(
            root_path=self._workspace_dir or "./",
            language=resolved_language
        )
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
            enable_task_loop=config.get("enable_task_loop", True),
            max_iterations=config.get("max_iterations", 15),
            subagents=self._build_browser_subagents(model, config),
            tools=normalized_tool_cards,
            workspace=workspace_obj,
            skills=None,
            backend=None,
            sys_operation=self._sys_operation,
            language=resolved_language,
            prompt_mode=None,
            rails=rails,
            vision_model_config=self._vision_model_config,
            audio_model_config=self._audio_model_config,
        )

    def _get_current_agent_rails(self, config: dict[str, Any], config_base: dict[str, Any] | None = None) -> list[Any]:
        """Return rail instances that need to be re-initialized on hot reload.

        SkillUseRail, ContextEngineeringRail, and MemoryRail are rebuilt on config reload.
        All other rails read language dynamically from system_prompt_builder.language
        and are updated in-place where needed — they are NOT passed to configure()
        so their existing registered state is preserved without an uninit/init cycle.
        """
        # Apply in-place updates to skill_evolution_rail (no re-init needed).
        if self._skill_evolution_rail is not None:
            self._skill_evolution_rail.update_llm(self._model, config.get("model_name", "gpt-4"))
            _env_auto_scan = os.getenv("EVOLUTION_AUTO_SCAN")
            if _env_auto_scan is not None:
                self._skill_evolution_rail.auto_scan = _env_auto_scan.lower() in ("true", "1", "yes")

        self._skill_rail = self._build_skill_rail(config, include_tools=self._filesystem_rail is None)

        if config.get("context_engine_config", {}).get("enabled", False):
            self._context_engineering_rail = self._build_context_engineering_rail(config)
        else:
            self._context_engineering_rail = None

        # MemoryRail 持有 EmbeddingConfig，embed 配置变更时需要整体重建。
        if config_base is not None and get_memory_mode(config_base) == "local":
            self._memory_rail = self._build_memory_rail()
        else:
            self._memory_rail = None

        # PermissionRail: in-place 更新已有 rail，或首次启用时新建。
        permission_config = config_base.get("permissions", {}) if config_base else {}
        if self._permission_rail is not None:
            self._permission_rail.update_config(permission_config)
            logger.info("[JiuWenClawDeepAdapter] _permission_rail config hot-updated")
        elif permission_config.get("enabled", False):
            self._permission_rail = build_permission_rail(
                config=config_base, llm=self._model,
                model_name=config_base.get("models", {}).get(
                    "default", {}).get("model_client_config", {}).get("model_name", "gpt-4"),
            )
            if self._permission_rail is not None:
                logger.info("[JiuWenClawDeepAdapter] _permission_rail newly created on hot-reload")

        rails_list = []
        if self._skill_rail is not None:
            rails_list.append(self._skill_rail)
        if self._context_engineering_rail is not None:
            rails_list.append(self._context_engineering_rail)
        if self._memory_rail is not None:
            rails_list.append(self._memory_rail)
        if self._permission_rail is not None:
            rails_list.append(self._permission_rail)
        return rails_list

    async def _get_tool_cards(self):
        """Get tool cards."""
        tool_cards = []

        for tool_cls in [WebFreeSearchTool, WebFetchWebpageTool]:
            tool_instance = tool_cls()
            Runner.resource_mgr.add_tool(tool_instance)
            tool_cards.append(tool_instance.card)

        # 付费搜索工具：有任意一个付费 key 就注册
        if any(
            os.environ.get(key)
            for key in ("PERPLEXITY_API_KEY", "SERPER_API_KEY", "JINA_API_KEY")
        ):
            self._paid_search_tool = WebPaidSearchTool(language=self._resolve_runtime_language())
            Runner.resource_mgr.add_tool(self._paid_search_tool)
            tool_cards.append(self._paid_search_tool.card)
            self._paid_search_registered = True

        self._vision_tools = []
        self._vision_tools_registered = False
        if self._vision_model_config is not None:
            try:
                for tool in create_vision_tools(
                        language=self._resolve_runtime_language(),
                        vision_model_config=self._vision_model_config,
                ):
                    Runner.resource_mgr.add_tool(tool)
                    tool_cards.append(tool.card)
                    self._vision_tools.append(tool)
                self._vision_tools_registered = bool(self._vision_tools)
            except Exception as exc:
                self._vision_tools = []
                logger.warning(
                    "[JiuWenClawDeepAdapter] vision tools registration failed: %s",
                    exc,
                )

        self._audio_tools = []
        self._audio_tools_registered = False
        if self._audio_model_config is not None:
            try:
                for tool in create_audio_tools(
                        language=self._resolve_runtime_language(),
                        audio_model_config=self._audio_model_config,
                ):
                    Runner.resource_mgr.add_tool(tool)
                    tool_cards.append(tool.card)
                    self._audio_tools.append(tool)
                self._audio_tools_registered = bool(self._audio_tools)
            except Exception as exc:
                self._audio_tools = []
                logger.warning(
                    "[JiuWenClawDeepAdapter] audio tools registration failed: %s",
                    exc,
                )

        self._video_tool_registered = False
        if self._video_model_config:
            try:
                Runner.resource_mgr.add_tool(video_understanding)
                tool_cards.append(video_understanding.card)
                self._video_tool_registered = True
            except Exception as exc:
                logger.warning(
                    "[JiuWenClawDeepAdapter] video tool registration failed: %s",
                    exc,
                )

        # 小艺手机端工具：由 channels.xiaoyi.phone_tools_enabled 控制
        config_base = get_config()
        xiaoyi_phone_tools_enabled = (
            config_base.get("channels", {}).get("xiaoyi", {}).get("phone_tools_enabled", False)
        )
        if xiaoyi_phone_tools_enabled and not self._xiaoyi_phone_tools_registered:
            _xiaoyi_tools = [
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
            try:
                for xt in _xiaoyi_tools:
                    Runner.resource_mgr.add_tool(xt)
                    tool_cards.append(xt.card)
                self._xiaoyi_phone_tools_registered = True
                logger.info(
                    "[JiuWenClawDeepAdapter] %d xiaoyi phone tools registered", len(_xiaoyi_tools)
                )
            except Exception as exc:
                logger.warning(
                    "[JiuWenClawDeepAdapter] xiaoyi phone tools registration failed: %s", exc
                )

        return tool_cards

    def _build_cron_tools(self) -> list[Any]:
        """Build cron tools from the shared runtime bridge."""
        return self._cron_runtime.build_tools(context=self._runtime_cron_tool_context)

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
        self._refresh_multimodal_configs(config_base)
        config = config_base.get('react', {}).copy()
        self._config_cache = config.copy()
        self._agent_name = config.get("agent_name", "main_agent")

        model = self._create_model(config_base)
        agent_card = AgentCard(name=self._agent_name, id='jiuwenclaw')

        tool_cards = await self._get_tool_cards()
        self._tool_cards = tool_cards
        rails_list = self._build_agent_rails(config, config_base)

        sys_operation = self._create_sys_operation()
        if sys_operation is None:
            raise RuntimeError("sys_operation is not available, maybe task is not running")

        self._sys_operation = sys_operation
        browser_subagents = self._build_browser_subagents(model, config)
        self._instance = create_deep_agent(
            model=model,
            card=agent_card,
            system_prompt=build_identity_prompt(
                mode="agent",
                language=self._resolve_prompt_language(),
                channel=self._resolve_prompt_channel(),
            ),
            tools=tool_cards if tool_cards else [],
            subagents=browser_subagents,
            rails=rails_list if rails_list else [],
            enable_task_loop=config.get("enable_task_loop", True),
            max_iterations=config.get("max_iterations", 15),
            workspace=Workspace(
                root_path=self._workspace_dir or "./",
                language=self._resolve_runtime_language(),
            ),
            sys_operation=sys_operation,
            vision_model_config=self._vision_model_config,
            audio_model_config=self._audio_model_config,
        )
        logger.info("[JiuWenClawDeepAdapter] 初始化完成: agent_name=%s", self._agent_name)

    async def reload_agent_config(
            self,
            config_base: dict[str, Any] | None = None,
            env_overrides: dict[str, Any] | None = None,
    ) -> None:
        """从 config.yaml 重新加载配置，通过 DeepAgent.configure() 热更新当前实例（不新建 DeepAgent）。

        DeepAgent.configure() 现在自动处理 rail 生命周期：保留旧已注册 rails 的注销上下文，
        并在下次 _ensure_initialized() 时先卸载旧回调，再注册新的 rails。

        Args:
            config_base: 可选的完整配置快照；传入时优先使用它而不是读取本地 config.yaml。
            env_overrides: 可选的环境变量增量；仅覆盖请求中出现的 key。
        """
        if self._instance is None:
            raise RuntimeError("JiuWenClawDeepAdapter 未初始化，请先调用 create_instance()")
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
            config_base = resolve_env_vars(config_base)

        self._refresh_multimodal_configs(config_base)
        config = config_base.get('react', {}).copy()
        self._config_cache = config.copy()

        model = self._create_model(config_base)
        self._agent_name = config.get("agent_name", "main_agent")
        agent_card = AgentCard(name=self._agent_name, id='jiuwenclaw')
        self._sync_multimodal_tools_for_runtime()
        self._sync_paid_search_tool_for_runtime()

        rails_list = self._get_current_agent_rails(config, config_base)

        deep_cfg = self._make_deep_agent_config(
            model=model,
            config=config,
            agent_card=agent_card,
            tool_cards=self._tool_cards if self._tool_cards else [],
            rails=rails_list,
        )
        self._instance.configure(deep_cfg)

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
            mode: str = "plan",
            request_id: str | None = None,
    ) -> None:
        """Register per-request tools for current agent execution."""
        if self._instance is None:
            raise RuntimeError("JiuWenClawDeepAdapter 未初始化，请先调用 create_instance()")

        resolved_language = self._resolve_runtime_language()
        if mode == "plan":
            if self._task_planning_rail is None:
                self._task_planning_rail = self._build_task_planning_rail()
                if self._task_planning_rail is not None:
                    await self._instance.register_rail(self._task_planning_rail)
                    logger.info("[JiuWenClawDeepAdapter] TaskPlanningRail registered for plan mode")
            # plan 模式：卸载 multi-session 工具
            for existing in list(self._instance.ability_manager.list() or []):
                if getattr(existing, "name", "").startswith(("session_new", "session_cancel", "session_list")):
                    self._instance.ability_manager.remove(existing.name)
        else:
            if self._task_planning_rail is not None:
                await self._instance.unregister_rail(self._task_planning_rail)
                self._task_planning_rail = None
                logger.info("[JiuWenClawDeepAdapter] TaskPlanningRail unregistered for agent mode")
            # agent 模式：注册 multi-session 工具（每次请求用新的 request_id 重建）
            if request_id and session_id and self._model_client_config is not None:
                try:
                    for existing in list(self._instance.ability_manager.list() or []):
                        if getattr(existing, "name", "").startswith(("session_new", "session_cancel", "session_list")):
                            self._instance.ability_manager.remove(existing.name)
                    sub_agent_config = ReActAgentConfig(
                        model_client_config=self._model_client_config,
                        model_config_obj=self._model_request_config,
                    )
                    multi_session_toolkit = MultiSessionToolkit(
                        session_id=session_id,
                        channel_id=_CRON_TOOL_CHANNEL_ID.get(),
                        request_id=request_id,
                        sub_agent_config=sub_agent_config,
                    )
                    for ms_tool in multi_session_toolkit.get_tools():
                        Runner.resource_mgr.add_tool(ms_tool)
                        self._instance.ability_manager.add(ms_tool.card)
                    logger.info("[JiuWenClawDeepAdapter] MultiSessionToolkit registered for agent mode")
                except Exception as exc:
                    logger.error("[JiuWenClawDeepAdapter] MultiSessionToolkit 注册失败: %s", exc)

        # 定时工具：按当前 session 的 channel 注册（contextvar 已由 _bind_runtime_cron_context 设置）
        if session_id not in ("heartbeat", "cron"):
            try:
                channel = self._resolve_prompt_channel(session_id)
                _CHANNEL_TO_CRON_TARGET = {
                    "feishu": CronTargetChannel.FEISHU,
                    "wecom": CronTargetChannel.WECOM,
                    "xiaoyi": CronTargetChannel.XIAOYI,
                    "web": CronTargetChannel.WEB,
                    "sess": CronTargetChannel.WEB,
                }
                cron_target = _CHANNEL_TO_CRON_TARGET.get(channel, CronTargetChannel.WEB)
                CronController.get_instance().set_target_channel(cron_target)
                for cron_tool in self._build_cron_tools():
                    if not Runner.resource_mgr.get_tool(cron_tool.card.id):
                        Runner.resource_mgr.add_tool(cron_tool)
                    self._instance.ability_manager.add(cron_tool.card)
            except Exception as exc:
                logger.error("[JiuWenClawDeepAdapter] 定时工具注册失败: %s", exc)

        # send_file 工具：由 channels.<channel>.send_file_allowed 控制，每次请求重新注册
        # channel_id/metadata 由调用前的 _bind_runtime_cron_context 已写入 contextvar
        config_base = get_config()
        channel = self._resolve_prompt_channel(session_id)
        send_file_enabled = config_base.get("channels", {}).get(channel, {}).get("send_file_allowed", False)
        if send_file_enabled and request_id and session_id:
            # 先卸载上一次请求遗留的 send_file 工具
            for existing in list(self._instance.ability_manager.list() or []):
                if getattr(existing, "name", "").startswith("send_file_to_user"):
                    self._instance.ability_manager.remove(existing.name)
            send_file_toolkit = SendFileToolkit(
                request_id=request_id,
                session_id=session_id,
                channel_id=_CRON_TOOL_CHANNEL_ID.get(),
                metadata=_CRON_TOOL_METADATA.get(),
            )
            for sf_tool in send_file_toolkit.get_tools():
                Runner.resource_mgr.add_tool(sf_tool)
                self._instance.ability_manager.add(sf_tool.card)

        # Sync language onto shared builder and deep config so rails
        # (SecurityRail, MemoryRail, etc.) see the updated language in before_model_call.
        if self._instance.system_prompt_builder is not None:
            self._instance.system_prompt_builder.language = resolved_language
        if self._instance._deep_config is not None:
            self._instance._deep_config.language = resolved_language

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
        updated_todos = None

        if intent == "pause":
            # 暂停：通过 StreamEventRail 在下一个 model_call/tool_call checkpoint 阻塞
            if self._stream_event_rail is not None:
                self._stream_event_rail.pause()
                logger.info(
                    "[JiuWenClawDeepAdapter] interrupt: 已暂停执行 request_id=%s",
                    request.request_id,
                )
            message = "任务已暂停"

        elif intent == "resume":
            # 恢复：解除 StreamEventRail 的 pause 阻塞 + 清除 abort 标志
            if self._stream_event_rail is not None:
                self._stream_event_rail.resume()
                logger.info(
                    "[JiuWenClawDeepAdapter] interrupt: 已恢复执行 request_id=%s",
                    request.request_id,
                )
            message = "任务已恢复"

        elif intent == "supplement":
            # supplement: 停止当前执行，但保留 todo（新任务会根据 todo 待办继续执行）
            # 1. 通过 rail abort 在 checkpoint 抛 CancelledError，打断当前内层执行
            if self._stream_event_rail is not None:
                self._stream_event_rail.abort()
            # 2. 终止 DeepAgent 外层 task loop
            if self._instance is not None:
                await self._instance.abort()
            # 3. 不清理 todo — 保留给新任务继续
            logger.info(
                "[JiuWenClawDeepAdapter] interrupt(supplement): 已停止执行 request_id=%s",
                request.request_id,
            )
            message = "任务已切换"

        else:
            # cancel（默认）：停止所有执行 + 清理 todo
            # 1. 通过 rail abort 在 checkpoint 抛 CancelledError，打断当前内层执行
            if self._stream_event_rail is not None:
                self._stream_event_rail.abort()
            # 2. 终止 DeepAgent 外层 task loop
            if self._instance is not None:
                await self._instance.abort()
            # 3. 将未完成的 todo 项标记为 cancelled，并获取更新后的 todo 列表
            updated_todos = None
            if request.session_id:
                try:
                    updated_todos = await self._cancel_pending_todos(request.session_id)
                except Exception as exc:
                    logger.warning("[JiuWenClawDeepAdapter] 标记 todo cancelled 失败: %s", exc)

            logger.info(
                "[JiuWenClawDeepAdapter] interrupt(cancel): 已停止执行 request_id=%s",
                request.request_id,
            )
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

        # cancel 后附带更新的 todo 列表，通知前端刷新
        if intent not in ("pause", "resume", "supplement") and updated_todos is not None:
            payload["todos"] = updated_todos

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
        if self._instance is not None and hasattr(self._instance, "_react_agent"):
            react_agent = self._instance.react_agent
            if react_agent is not None and hasattr(react_agent, "_config"):
                config = react_agent._config
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
        if request_id.startswith("skill_evolve_approve_"):
            resolved = await self._handle_evolution_approval(request_id, answers)

        return AgentResponse(
            request_id=request.request_id,
            channel_id=request.channel_id,
            ok=True,
            payload={"accepted": True, "resolved": resolved},
            metadata=request.metadata,
        )

    async def handle_heartbeat(self, request: AgentRequest) -> AgentResponse | None:
        """处理 heartbeat 请求，返回 None 表示继续正常流程."""
        return None

    async def _handle_evolution_approval(self, request_id: str, answers: list) -> bool:
        """Persist approved evolution records from the cached _evolution_data."""
        evo_data = self._pending_evolution_data.pop(request_id, None)
        if evo_data is None or self._skill_evolution_rail is None:
            return False

        skill_name = evo_data.get("skill_name", "")
        raw_records = evo_data.get("records", [])
        kept = 0
        for i, raw in enumerate(raw_records):
            accept = (
                    i < len(answers)
                    and isinstance(answers[i], dict)
                    and "接收" in answers[i].get("selected_options", [])
            )
            if accept:
                record = EvolutionRecord.from_dict(raw)
                await self._skill_evolution_rail.store.append_record(skill_name, record)
                kept += 1

        logger.info(
            "[JiuWenClaw] evolution approval resolved: request_id=%s kept=%d/%d skill=%s",
            request_id, kept, len(raw_records), skill_name,
        )
        return True

    # ------------------------------------------------------------------
    # /evolve & /solidify command handlers (new online module)
    # ------------------------------------------------------------------

    async def _handle_evolve_command(self, query: str, session_id: str) -> dict[str, Any]:
        """/evolve [list | <skill_name>] handler using the new online evolution module.

        Returns a result dict.  When evolution records are generated the dict
        includes an ``approval_chunks`` list so the caller can forward the
        approval event to the frontend.
        """
        rail = self._skill_evolution_rail
        assert rail is not None
        store = rail.store

        skill_names = store.list_skill_names()

        parts = query.split(maxsplit=1)
        skill_arg = parts[1].strip() if len(parts) > 1 else ""

        # --- /evolve list (or bare /evolve) ---
        if not skill_arg or skill_arg == "list":
            if not skill_names:
                return {
                    "output": "当前 skills_base_dir 下未找到任何 Skill 目录。",
                    "result_type": "answer",
                }
            summary = await store.list_pending_summary(skill_names)
            return {
                "output": f"**Skills 演进记录：**\n\n{summary}",
                "result_type": "answer",
            }

        # --- /evolve <skill_name> ---
        skill_name = skill_arg
        if skill_name not in skill_names:
            available = "、".join(skill_names) or "（无可用 Skill）"
            return {
                "output": (
                    f"在 skills_base_dir 下未找到 Skill '{skill_name}'。\n"
                    f"当前可用 Skill：{available}\n"
                    f"可使用 /evolve list 查看所有记录。"
                ),
                "result_type": "error",
            }

        # 1) Collect conversation messages from the context engine cache
        parsed_messages = self._collect_messages_for_evolve(session_id)
        if not parsed_messages:
            return {
                "output": "当前对话无可用消息，无法检测演进信号。请先与 Agent 进行对话后再执行 /evolve。",
                "result_type": "answer",
            }

        # 2) Detect signals (reuse rail's dedup set)
        existing_skills = {n for n in skill_names if store.skill_exists(n)}
        detector = SignalDetector(existing_skills=existing_skills)
        detected = detector.detect(parsed_messages)

        new_signals = [
            sig for sig in detected
            if (sig.signal_type, sig.excerpt[:100]) not in rail.processed_signal_keys
        ]
        for sig in new_signals:
            rail.processed_signal_keys.add((sig.signal_type, sig.excerpt[:100]))

        attributed = [s for s in new_signals if s.skill_name == skill_name]
        if not attributed:
            return {
                "output": "当前对话未发现明确的演进信号（无工具执行失败、无用户纠正）。\n",
                "result_type": "answer",
            }

        # 3) Generate experience records
        context = EvolutionContext(
            skill_name=skill_name,
            signals=attributed,
            skill_content=await store.read_skill_content(skill_name),
            messages=parsed_messages,
            existing_desc_records=await store.get_pending_records(skill_name, EvolutionTarget.DESCRIPTION),
            existing_body_records=await store.get_pending_records(skill_name, EvolutionTarget.BODY),
        )
        try:
            records = await rail.evolver.generate_skill_experience(context)
        except Exception as exc:
            logger.warning("[JiuWenClaw] evolve generate failed (skill=%s): %s", skill_name, exc)
            return {
                "output": f"演进经验生成失败：{exc}",
                "result_type": "error",
            }

        if not records:
            return {
                "output": "当前对话未发现明确的演进信号（无工具执行失败、无用户纠正）。\n",
                "result_type": "answer",
            }

        # 4) Build approval event (do NOT persist yet)
        request_id = f"skill_evolve_approve_{uuid.uuid4().hex[:8]}"
        questions = []
        for record in records:
            content_preview = record.change.content[:1000]
            section = record.change.section
            target_tag = record.change.target.value
            questions.append({
                "question": (
                    f"**Skill '{skill_name}' 演进生成了新经验：**\n\n"
                    f"- **目标**: {target_tag}\n"
                    f"- **章节**: {section}\n\n"
                    f"{content_preview}"
                ),
                "header": "技能演进审批",
                "options": [
                    {"label": "接收", "description": "保留此演进经验"},
                    {"label": "拒绝", "description": "丢弃此演进经验"},
                ],
                "multi_select": False,
            })

        self._pending_evolution_data[request_id] = {
            "skill_name": skill_name,
            "records": [r.to_dict() for r in records],
        }

        summaries = "\n".join(
            f"  {i + 1}. **[{r.change.section}]** {r.change.content[:200]}"
            for i, r in enumerate(records)
        )
        return {
            "output": (
                f"已为 Skill '{skill_name}' 生成 {len(records)} 条演进经验，请审批：\n"
                f"{summaries}"
            ),
            "result_type": "answer",
            "approval_chunks": [
                {
                    "event_type": "chat.ask_user_question",
                    "request_id": request_id,
                    "questions": questions,
                }
            ],
        }

    def _collect_messages_for_evolve(self, session_id: str) -> list[dict]:
        """Retrieve and normalize cached conversation messages for /evolve."""
        if self._instance is None or self._instance.react_agent is None:
            return []

        context_engine = self._instance.react_agent.context_engine
        context = context_engine.get_context(session_id=session_id)
        if context is None:
            return []

        try:
            raw_messages = list(context.get_messages())
        except Exception as exc:
            logger.debug("[JiuWenClaw] _collect_messages_for_evolve failed: %s", exc)
            return []

        return SkillEvolutionRail._parse_messages(raw_messages)

    async def _handle_solidify_command(self, query: str) -> dict[str, Any]:
        """/solidify <skill_name> handler using the new online EvolutionStore."""
        rail = self._skill_evolution_rail
        assert rail is not None
        store = rail.store

        parts = query.split(maxsplit=1)
        skill_name = parts[1].strip() if len(parts) > 1 else ""
        if not skill_name:
            return {
                "output": "请指定 Skill 名称：`/solidify <skill_name>`",
                "result_type": "error",
            }

        count = await store.solidify(skill_name)
        if count == 0:
            msg = f"Skill '{skill_name}' 没有待固化的演进经验。"
        else:
            msg = f"已将 {count} 条演进经验固化到 Skill '{skill_name}' 的 SKILL.md。"
        return {"output": msg, "result_type": "answer"}

    async def _handle_slash_command(
            self, query: str, session_id: str = "default",
    ) -> dict[str, Any] | None:
        """Intercept /evolve and /solidify before agent invocation.

        Returns result dict if handled, None to proceed normally.
        The dict may contain an ``approval_chunks`` list that the caller
        should forward to the frontend as separate stream events.
        """
        stripped = query.strip()

        if stripped.startswith("/solidify"):
            if self._skill_evolution_rail is None:
                return {"output": "演进功能未启用。", "result_type": "error"}
            return await self._handle_solidify_command(stripped)

        if stripped.startswith("/evolve"):
            if self._skill_evolution_rail is None:
                return {"output": "演进功能未启用。", "result_type": "error"}
            return await self._handle_evolve_command(stripped, session_id)

        return None

    async def _cancel_pending_todos(self, session_id: str) -> list[dict] | None:
        """将未完成的 todo 项标记为 cancelled.

        Returns:
            更新后的 todo 列表（前端格式），用于附加到 interrupt_result 事件通知前端刷新。
            如果没有 todo 或操作失败，返回 None。
        """
        if self._instance is None:
            return None

        modify_tool = None
        try:
            tool_card = self._instance.ability_manager.get("todo_modify")
            registered_tool = Runner.resource_mgr.get_tool(tool_card.id)
            if registered_tool is not None:
                modify_tool = registered_tool
        except Exception:
            pass

        if modify_tool is None:
            deep_config = self._instance._deep_config
            modify_tool = TodoModifyTool(
                operation=deep_config.sys_operation,
                workspace=str(deep_config.workspace.get_node_path(WorkspaceNode.TODO)),
                language=self._resolve_runtime_language(),
            )

        modify_tool.set_file(session_id)

        try:
            todos = await modify_tool.load_todos()
            if not todos:
                return None

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

            # 重新加载并返回前端格式的 todo 列表
            updated_todos = await modify_tool.load_todos()
            if updated_todos and self._stream_event_rail is not None:
                return self._stream_event_rail._format_todos_for_frontend(updated_todos)
            return None
        except Exception as exc:
            logger.warning("[JiuWenClawDeepAdapter] 标记 todo cancelled 失败: %s", exc)
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

        slash_result = await self._handle_slash_command(query, session_id)
        if slash_result is not None:
            approval_chunks = slash_result.get("approval_chunks")
            if approval_chunks:
                payload: dict[str, Any] = {"approval_chunks": approval_chunks}
            else:
                content = slash_result.get("output", str(slash_result))
                payload = {"content": content}
            return AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=slash_result.get("result_type") != "error",
                payload=payload,
                metadata=request.metadata,
            )

        cron_context_tokens = self._bind_runtime_cron_context(
            channel_id=request.channel_id,
            session_id=request.session_id,
            metadata=request.metadata,
            mode=request.params.get("mode", "plan"),
        )
        token_cid = TOOL_PERMISSION_CHANNEL_ID.set((request.channel_id or "").strip())
        try:
            await self._register_runtime_tools(request.session_id, request.params.get("mode", "plan"), request_id=request.request_id)
            result = await Runner.run_agent(agent=self._instance, inputs=inputs)
        except asyncio.CancelledError:
            logger.info("[JiuWenClawDeepAdapter] Agent 任务被取消: request_id=%s session_id=%s", request.request_id,
                        session_id)
            raise
        except Exception as e:
            logger.error("[JiuWenClawDeepAdapter] Agent 任务执行异常: %s", e)
            raise
        finally:
            TOOL_PERMISSION_CHANNEL_ID.reset(token_cid)
            self._reset_runtime_cron_context(cron_context_tokens)

        content = result if isinstance(result, (str, dict)) else str(result)

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

        # 拦截斜杠命令
        slash_result = await self._handle_slash_command(query, session_id)
        if slash_result is not None:
            approval_chunks = slash_result.get("approval_chunks", [])
            if approval_chunks:
                for chunk in approval_chunks:
                    yield AgentResponseChunk(
                        request_id=request.request_id,
                        channel_id=request.channel_id,
                        payload=chunk,
                        is_complete=False,
                    )
                yield AgentResponseChunk(
                    request_id=request.request_id,
                    channel_id=request.channel_id,
                    payload={"event_type": "chat.done"},
                    is_complete=True,
                )
            else:
                content = slash_result.get("output", str(slash_result))
                yield AgentResponseChunk(
                    request_id=request.request_id,
                    channel_id=request.channel_id,
                    payload={"event_type": "chat.final", "content": content},
                    is_complete=True,
                )
            return

        has_streamed_content = False
        accumulated_text = ""
        accumulated_reasoning = ""
        evolution_status_started = False
        evolution_status_ended = False

        cron_context_tokens = self._bind_runtime_cron_context(
            channel_id=request.channel_id,
            session_id=request.session_id,
            metadata=request.metadata,
            mode=request.params.get("mode", "plan"),
        )
        token_cid = TOOL_PERMISSION_CHANNEL_ID.set((request.channel_id or "").strip())
        try:
            await self._register_runtime_tools(request.session_id, request.params.get("mode", "plan"), request_id=request.request_id)
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
                    if (
                            not evolution_status_started
                            and self._skill_evolution_rail is not None
                            and request.params.get("mode", "plan") == "plan"
                    ):
                        # Mark evolution phase start before after_invoke auto-evolution runs.
                        yield AgentResponseChunk(
                            request_id=rid,
                            channel_id=cid,
                            payload={"event_type": "chat.evolution_status", "status": "start"},
                            is_complete=False,
                        )
                        evolution_status_started = True
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
                    if (
                            parsed.get("event_type") == "chat.ask_user_question"
                            and isinstance(parsed.get("_evolution_data"), dict)
                    ):
                        evo_req_id = parsed.get("request_id", "")
                        if evo_req_id.startswith("skill_evolve_approve_"):
                            self._pending_evolution_data[evo_req_id] = parsed.pop("_evolution_data")
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

            # after_invoke 在流关闭后触发，其中缓存的审批事件无法通过
            # session.write_stream 传递，需手动注入到 stream 输出
            if self._skill_evolution_rail is not None:
                for evt in self._skill_evolution_rail.drain_pending_approval_events():
                    parsed = self._parse_stream_chunk(evt)
                    if parsed is not None:
                        if (
                                parsed.get("event_type") == "chat.ask_user_question"
                                and isinstance(parsed.get("_evolution_data"), dict)
                        ):
                            evo_req_id = parsed.get("request_id", "")
                            if evo_req_id.startswith("skill_evolve_approve_"):
                                self._pending_evolution_data[evo_req_id] = parsed.pop("_evolution_data")
                        yield AgentResponseChunk(
                            request_id=rid,
                            channel_id=cid,
                            payload=parsed,
                            is_complete=False,
                        )

            if evolution_status_started and not evolution_status_ended:
                yield AgentResponseChunk(
                    request_id=rid,
                    channel_id=cid,
                    payload={"event_type": "chat.evolution_status", "status": "end"},
                    is_complete=False,
                )
                evolution_status_ended = True
        except asyncio.CancelledError:
            logger.info("[JiuWenClawDeepAdapter] 流式任务被取消: request_id=%s session_id=%s", rid, session_id)
            raise
        except Exception as exc:
            logger.exception("[JiuWenClawDeepAdapter] 流式任务异常: %s", exc)
            if evolution_status_started and not evolution_status_ended:
                yield AgentResponseChunk(
                    request_id=rid,
                    channel_id=cid,
                    payload={"event_type": "chat.evolution_status", "status": "end"},
                    is_complete=False,
                )
                evolution_status_ended = True
            yield AgentResponseChunk(
                request_id=rid,
                channel_id=cid,
                payload={"event_type": "chat.error", "error": str(exc)},
                is_complete=False,
            )
        finally:
            TOOL_PERMISSION_CHANNEL_ID.reset(token_cid)
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
                    if inner_val == "task_failed":
                        error = next((item.text for item in payload.data if hasattr(item, "text")), "任务执行失败")
                        return {"event_type": "chat.error", "error": error}

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

                if chunk_type == "__interaction__":
                    return convert_interactions_to_ask_user_question([payload])

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
