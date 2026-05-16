# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""JiuWenClaw Deep Adapter - 基于 openjiuwen DeepAgent 的适配器实现.

此模块实现 AgentAdapter 协议，封装 Deep SDK 的所有专属逻辑。
公共编排逻辑（session 队列、Skills 路由、heartbeat 等）由 Facade 层处理。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import subprocess
import time
from contextvars import ContextVar, Token
from dataclasses import dataclass
from shutil import which
from typing import Any, AsyncIterator, Callable, List, Optional, Tuple

import yaml
from dotenv import load_dotenv
from openjiuwen.core.context_engine.schema.config import ContextEngineConfig
from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig, Model
from openjiuwen.core.foundation.store.base_embedding import EmbeddingConfig
from openjiuwen.core.foundation.tool import ToolCard, McpServerConfig
from openjiuwen.core.runner import Runner
from openjiuwen.core.session.checkpointer import CheckpointerFactory
from openjiuwen.core.session.checkpointer.checkpointer import CheckpointerConfig
from openjiuwen.core.session.checkpointer.persistence import PersistenceCheckpointerProvider
from openjiuwen.core.single_agent import AgentCard, ReActAgentConfig
from openjiuwen.core.sys_operation import (
    SysOperation,
    SysOperationCard,
    OperationMode,
    LocalWorkConfig,
    SandboxGatewayConfig,
)
from openjiuwen.core.sys_operation.config import (
    SandboxIsolationConfig,
    PreDeployLauncherConfig,
    ContainerScope,
)
from openjiuwen.core.sys_operation.cwd import set_cwd
from openjiuwen.harness import (
    AudioModelConfig,
    DeepAgent,
    DeepAgentConfig,
    VisionModelConfig,
)
from openjiuwen.harness.factory import create_deep_agent
from openjiuwen.harness.prompts import resolve_language
from openjiuwen.harness.rails import (
    SkillUseRail,
    TaskPlanningRail,
    SecurityRail,
    SkillEvolutionRail,
    SkillCreateRail,
    SubagentRail,
    SysOperationRail,
    HeartbeatRail,
    MemoryRail
)
from openjiuwen.harness.rails.context_engineer.context_assemble_rail import ContextAssembleRail
from openjiuwen.harness.rails.context_engineer.context_processor_rail import ContextProcessorRail
from openjiuwen.agent_evolving.signal import SignalDetector
from openjiuwen.harness.subagents.browser_agent import build_browser_agent_config
from openjiuwen.harness.subagents.research_agent import build_research_agent_config
from openjiuwen.harness.tools import (
    WebFetchWebpageTool,
    WebFreeSearchTool,
    WebPaidSearchTool,
    create_audio_tools,
    create_vision_tools,
    TodoModifyTool,
)

try:
    from openjiuwen.harness.tools import is_paid_search_enabled
except ImportError:  # Compatibility with older agent-core versions.
    try:
        from openjiuwen.harness.tools.web_tools import is_paid_search_enabled
    except ImportError:

        def is_paid_search_enabled() -> bool:
            api_key_envs = (
                "BOCHA_API_KEY",
                "PERPLEXITY_API_KEY",
                "SERPER_API_KEY",
                "JINA_API_KEY",
            )
            for key in api_key_envs:
                if str(os.environ.get(key, "") or "").strip():
                    return True
            return False


from openjiuwen.harness.schema.task import TodoStatus
from openjiuwen.harness.workspace.workspace import Workspace, WorkspaceNode

from jiuwenclaw.agents.harness.team.a2x.a2x_registry_runtime import (
    init_a2x_client,
    register_blank_agent_if_teammate,
    resolve_a2x_config,
)
from jiuwenclaw.agents.harness.common.tools.cron.cron_runtime import CronRuntimeBridge
from jiuwenclaw.agents.harness.common.auto_harness import AutoHarnessService
from jiuwenclaw.agents.harness.common.rails.interrupt.interrupt_helpers import (
    build_permission_rail,
    convert_interactions_to_ask_user_question,
)
from jiuwenclaw.agents.harness.common.prompt.prompt_builder import build_identity_prompt
from jiuwenclaw.agents.harness.common.rails import (
    JiuClawStreamEventRail,
    ResponsePromptRail,
    RuntimePromptRail,
)
from jiuwenclaw.agents.harness.common.rails.permissions.owner_scopes import (
    TOOL_PERMISSION_CONTEXT,
    setup_permission_context,
    cleanup_permission_context,
)
from jiuwenclaw.agents.harness.common.memory import clear_memory_manager_cache
from jiuwenclaw.agents.harness.common.memory.config import (
    clear_config_cache,
    get_memory_mode,
    is_memory_enabled,
    is_proactive_memory,
)
from jiuwenclaw.agents.harness.common.memory.external_memory_config import is_builtin_memory_allowed
from jiuwenclaw.agents.harness.common.rails.permissions.tool_permission_context import TOOL_PERMISSION_CHANNEL_ID
from jiuwenclaw.server.runtime.session.session_metadata import build_server_push_message
from jiuwenclaw.server.runtime.session.session_history import append_history_record
from jiuwenclaw.server.runtime.skill.skill_manager import SkillManager
from jiuwenclaw.agents.harness.common.tools.multimodal_config import (
    apply_audio_model_config_from_yaml,
    apply_image_gen_model_config_from_yaml,
    apply_video_model_config_from_yaml,
    apply_vision_model_config_from_yaml,
    dedicated_multimodal_model_configured,
)
from jiuwenclaw.agents.harness.common.tools.video_tools import video_understanding
from jiuwenclaw.agents.harness.common.tools.image_tools import generate_image

from jiuwenclaw.agents.harness.common.tools import SendFileToolkit, SkillToolkit
from jiuwenclaw.agents.harness.common.tools.wiki_tools import wiki_ingest, wiki_query, wiki_lint
from jiuwenclaw.agents.harness.common.tools.acp_output_tools import get_tools as get_acp_output_tools
from jiuwenclaw.agents.harness.common.tools.multi_session_toolkits import MultiSessionToolkit
from jiuwenclaw.agents.harness.common.tools.xiaoyi_phone_tools import (
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
    query_collection,
    add_collection,
    delete_collection,
    save_media_to_gallery,
    save_file_to_file_manager,
    convert_timestamp_to_utc8_time,
    view_push_result,
    xiaoyi_gui_agent,
    image_reading,
)
from jiuwenclaw.common.config import get_config, get_default_models, resolve_env_vars
from jiuwenclaw.agents.harness.common.plugins.rail_manager import get_rail_manager
from jiuwenclaw.gateway.cron import CronTargetChannel
from jiuwenclaw.common.schema.agent import AgentRequest, AgentResponse, AgentResponseChunk
from jiuwenclaw.common.utils import (
    get_agent_memory_dir,
    get_agent_skills_dir,
    get_agent_workspace_dir,
    get_checkpoint_dir,
    get_config_dir,
    get_deepagent_agent_md_path,
    get_deepagent_heartbeat_path,
    get_deepagent_identity_md_path,
    get_deepagent_soul_md_path,
    get_deepagent_user_md_path,
    get_env_file,
    reset_free_search_runtime_flags,
)

load_dotenv(dotenv_path=get_env_file(), override=True)
reset_free_search_runtime_flags()

_react_config = get_config().get("react", {})
_sandbox_config = get_config().get("sandbox", {})

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

_ACP_BLOCKED_DEFAULT_TOOL_NAMES = frozenset(
    {
        "read_file",
        "write_file",
        "edit_file",
        "bash",
        "code",
    }
)
_PLACEHOLDER_API_BASES = frozenset({"https://example.com/compatible-mode/v1"})


def _get_skill_create_enabled(config: dict[str, Any] | None) -> bool:
    """读取 skill_create 配置，环境变量优先，不存在时从 config.yaml 读取.

    Args:
        config: 配置字典（包含 evolution.skill_create）

    Returns:
        True 表示启用 SkillCreateRail，False 表示不启用
    """
    env_skill_create = os.getenv("SKILL_CREATE")
    if env_skill_create is not None:
        return env_skill_create.lower() in ("true", "1", "yes")
    return (config or {}).get("evolution", {}).get("skill_create", False)


def init_permission_engine(*_args: Any, **_kwargs: Any) -> None:
    """Legacy shim for tests/older call sites.

    The project now relies on openjiuwen's PermissionInterruptRail and does not
    require a standalone permission engine initialization step.
    """
    return None


def _mcc_looks_usable(mcc: dict) -> bool:
    """检查 model_client_config 是否包含有效的 API 凭据。"""
    api_key = str(mcc.get("api_key", "") or "").strip()
    api_base = str(mcc.get("api_base", "") or "").strip()
    return bool(api_key) and bool(api_base) and api_base not in _PLACEHOLDER_API_BASES


def parse_int(value: Any, default: int) -> int:
    """Parse integer-like values safely."""
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _deep_agent_context_engine_config(react_cfg: dict[str, Any] | None) -> ContextEngineConfig:
    """供 ``create_deep_agent(..., context_engine_config=...)`` 使用（与 agent-core 集成测试方法二一致）。

    仅根据 ``react.context_engine_config.enable_kv_cache_release`` 切换亲和开关；
    其余字段与 ``ReActAgentConfig`` 默认 ``context_engine_config`` 一致。
    """
    react_cfg = react_cfg or {}
    cec = react_cfg.get("context_engine_config")
    enable_kv = bool(cec.get("enable_kv_cache_release", False)) if isinstance(cec, dict) else False
    return ReActAgentConfig().context_engine_config.model_copy(
        update={"enable_kv_cache_release": enable_kv}
    )


def _build_context_assemble_rail() -> ContextAssembleRail | None:
    """Build ContextAssembleRail."""
    try:
        context_assemble_rail = ContextAssembleRail()
        logger.info("[JiuWenClawDeepAdapter] ContextAssembleRail create success")
    except Exception as exc:
        logger.warning("[JiuWenClawDeepAdapter] ContextAssembleRail create failed: %s", exc)
        context_assemble_rail = None
    return context_assemble_rail


def _build_context_processor_rail(config: dict[str, Any]) -> ContextProcessorRail | None:
    """Build ContextProcessorRail with user config.

    从配置中读取 processor 配置，传递给 ContextProcessorRail。

    Args:
        config: 配置字典
    """
    try:
        user_processors: List[Tuple[str, dict]] = []
        context_engine_cfg = config.get("context_engine_config", {})

        offloader_cfg = context_engine_cfg.get("message_summary_offloader_config", {})
        if isinstance(offloader_cfg, dict) and offloader_cfg:
            user_processors.append(("MessageSummaryOffloader", offloader_cfg))

        compressor_cfg = context_engine_cfg.get("dialogue_compressor_config", {})
        if isinstance(compressor_cfg, dict) and compressor_cfg:
            user_processors.append(("DialogueCompressor", compressor_cfg))

        current_round_cfg = context_engine_cfg.get("current_round_compressor_config", {})
        if isinstance(current_round_cfg, dict) and current_round_cfg:
            user_processors.append(("CurrentRoundCompressor", current_round_cfg))

        round_level_cfg = context_engine_cfg.get("round_level_compressor_config", {})
        if isinstance(round_level_cfg, dict) and round_level_cfg:
            user_processors.append(("RoundLevelCompressor", round_level_cfg))

        context_rail = ContextProcessorRail(
            processors=user_processors if user_processors else None,
            preset=True,
        )
        logger.info(
            "[JiuWenClawDeepAdapter] ContextProcessorRail create success for agent.plan mode, "
            "user_processors=%s",
            [p[0] for p in user_processors] if user_processors else "none",
        )
        return context_rail
    except Exception as exc:
        logger.warning("[JiuWenClawDeepAdapter] ContextProcessorRail create failed: %s", exc)
        return None


_MODE_DISPLAY_MAP: dict[str, dict[str, str]] = {
    "agent.plan": {"cn": "规划模式", "en": "Planning Mode"},
    "agent.fast": {"cn": "性能模式", "en": "Performance Mode"},
    "team": {"cn": "集群模式", "en": "Cluster Mode"},
}


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
        self._workspace_dir: str = str(get_agent_workspace_dir())
        self._agent_name: str = "main_agent"
        self._vision_tools_registered: bool = False
        self._audio_tools_registered: bool = False
        self._video_tool_registered: bool = False
        self._image_gen_tool_registered: bool = False
        self._model: Model | None = None
        self._model_client_config: ModelClientConfig | None = None
        self._model_request_config: ModelRequestConfig | None = None
        self._config_cache: dict[str, Any] = {}
        self._filesystem_rail: SysOperationRail | None = None
        self._skill_rail: SkillUseRail | None = None
        self._stream_event_rail: JiuClawStreamEventRail | None = None
        self._task_planning_rail: TaskPlanningRail | None = None
        self._context_assemble_rail: ContextAssembleRail | None = None
        self._context_assemble_mode: str | None = None
        self._context_processor_rail: ContextProcessorRail | None = None
        self._runtime_prompt_rail: RuntimePromptRail | None = None
        self._response_prompt_rail: ResponsePromptRail | None = None
        self._security_rail: SecurityRail | None = None
        self._memory_rail: MemoryRail | None = None
        self._external_memory_rail: Any = None
        self._external_memory_rail_registered: bool = False
        self._heartbeat_rail: HeartbeatRail | None = None
        self._skill_evolution_rail: SkillEvolutionRail | None = None
        self._skill_create_rail: SkillCreateRail | None = None
        self._subagent_rail: SubagentRail | None = None
        self._permission_rail: Any = None
        self._avatar_rail: Any = None
        self._tool_cards = None
        self._evolution_watcher_tasks: set[asyncio.Task] = set()
        self._sys_operation = None
        self._vision_model_config: VisionModelConfig | None = None
        self._audio_model_config: AudioModelConfig | None = None
        self._video_model_config: bool = False
        self._image_gen_model_config: bool = False
        self._vision_tools: list[Any] = []
        self._audio_tools: list[Any] = []
        self._instance_overrides: dict[str, Any] = {}
        self._xiaoyi_phone_tools_registered: bool = False
        self._paid_search_registered: bool = False
        self._paid_search_tool: WebPaidSearchTool | None = None
        self._skill_manager: SkillManager | None = None
        self._a2x_client: Any | None = None
        self._a2x_config: dict[str, Any] = {}
        self._a2x_blank_service_id: str = ""
        self._a2x_blank_dataset: str = ""
        self._cron_runtime = CronRuntimeBridge()
        self._runtime_cron_tool_context = _RuntimeCronToolContext(
            tool_scope=f"runtime_{id(self):x}",
        )
        self._is_proactive_memory: bool | None = None
        self._model_cache: dict[str, Model] = {}
        self._model_name_to_keys: dict[str, list[str]] = {}
        self._default_model_name: str = ""
        self._registered_mcp_server_ids: set[str] = set()
        self._registered_mcp_servers: dict[str, McpServerConfig] = {}
        self._auto_harness_service: Optional[AutoHarnessService] = None

    def set_skill_manager(self, skill_manager: SkillManager) -> None:
        """Inject shared SkillManager from facade for tool reuse."""
        self._skill_manager = skill_manager

    @staticmethod
    def _get_a2x_config(config_base: dict[str, Any]) -> dict[str, Any]:
        """Resolve A2X config from ``react.a2x_registry`` with safe defaults."""
        return resolve_a2x_config(config_base)

    def _sync_a2x_runtime_state(self) -> None:
        """Expose A2X runtime state on the underlying DeepAgent instance."""
        if self._instance is None:
            return
        setattr(self._instance, "_jiuwen_a2x_client", self._a2x_client)
        setattr(self._instance, "_jiuwen_a2x_config", self._a2x_config)
        setattr(self._instance, "_jiuwen_a2x_blank_service_id", self._a2x_blank_service_id)
        setattr(self._instance, "_jiuwen_a2x_blank_dataset", self._a2x_blank_dataset)

    def _clear_a2x_runtime_state(self) -> None:
        """Remove exposed A2X runtime state from the underlying DeepAgent instance."""
        if self._instance is None:
            return
        for attr, value in (
            ("_jiuwen_a2x_client", None),
            ("_jiuwen_a2x_config", {}),
            ("_jiuwen_a2x_blank_service_id", ""),
            ("_jiuwen_a2x_blank_dataset", ""),
        ):
            if hasattr(self._instance, attr):
                try:
                    setattr(self._instance, attr, value)
                except Exception:
                    pass

    async def _close_a2x_client(self) -> None:
        """Close the mounted A2X client if initialized."""
        if self._a2x_client is None:
            self._a2x_config = {}
            self._a2x_blank_service_id = ""
            self._a2x_blank_dataset = ""
            self._clear_a2x_runtime_state()
            return
        client = self._a2x_client
        config = self._a2x_config
        self._a2x_client = None
        self._a2x_config = {}
        self._a2x_blank_service_id = ""
        self._a2x_blank_dataset = ""
        self._clear_a2x_runtime_state()
        close_timeout_raw = config.get("close_timeout", 5.0)
        try:
            close_timeout = max(float(close_timeout_raw), 0.1)
        except (TypeError, ValueError):
            close_timeout = 5.0
        try:
            await asyncio.wait_for(client.aclose(), timeout=close_timeout)
            logger.info("[JiuWenClawDeepAdapter] A2X Client closed")
        except TimeoutError:
            logger.warning(
                "[JiuWenClawDeepAdapter] A2X Client close timed out after %.1fs",
                close_timeout,
            )
        except Exception as exc:
            logger.warning(
                "[JiuWenClawDeepAdapter] A2X Client close failed: %s",
                exc,
                exc_info=True,
            )

    async def _init_a2x_client(self, config_base: dict[str, Any]) -> None:
        """Initialize and mount AsyncA2XRegistryClient on the adapter instance."""
        if self._a2x_client is not None:
            await self._close_a2x_client()

        client, a2x_config = await init_a2x_client(config_base)
        self._a2x_config = a2x_config
        self._a2x_client = client
        self._a2x_blank_service_id = ""
        self._a2x_blank_dataset = ""

    async def _try_init_a2x_client(self, config_base: dict[str, Any], *, reload: bool = False) -> None:
        """Best-effort A2X client init that never blocks agent startup."""
        try:
            await self._init_a2x_client(config_base)
            await register_blank_agent_if_teammate(
                self._a2x_client,
                self._a2x_config,
                source="deep-agent-reload" if reload else "deep-agent-init",
            )
            registration = getattr(self._a2x_client, "_jiuwen_blank_agent_registration", {})
            if isinstance(registration, dict):
                self._a2x_blank_service_id = str(registration.get("service_id") or "").strip()
                self._a2x_blank_dataset = str(registration.get("dataset") or "").strip()
            self._sync_a2x_runtime_state()
            logger.info(
                "[JiuWenClawDeepAdapter] A2X Client %s: role=%s base_url=%s",
                "reinitialized on reload" if reload else "initialized successfully",
                self._a2x_config.get("role", "teammate"),
                self._a2x_config.get("base_url", ""),
            )
        except Exception as exc:  # noqa: BLE001
            self._a2x_client = None
            self._a2x_config = {}
            self._a2x_blank_service_id = ""
            self._a2x_blank_dataset = ""
            self._clear_a2x_runtime_state()
            logger.warning(
                "[JiuWenClawDeepAdapter] A2X Client %s failed, agent will continue to %s: %s",
                "reload initialization" if reload else "initialize",
                "run" if reload else "start",
                exc,
                exc_info=True,
            )

    @staticmethod
    def _is_acp_tool_profile(config: dict[str, Any] | None = None) -> bool:
        if not isinstance(config, dict):
            return False
        tool_profile = str(config.get("tool_profile") or "").strip().lower()
        if tool_profile:
            return tool_profile == "acp"
        channel_id = str(config.get("channel_id") or "").strip().lower()
        return channel_id == "acp"

    def _filesystem_rail_enabled_for_profile(self) -> bool:
        raw = self._instance_overrides.get("enable_filesystem_rail", True)
        return bool(raw)

    def _skill_include_tools_for_profile(self) -> bool:
        if self._is_acp_tool_profile(self._instance_overrides):
            return False
        return self._filesystem_rail is None

    @staticmethod
    def _resolve_prompt_channel(session_id: str | None = None) -> str:
        """Resolve prompt channel from session id."""
        if not session_id:
            return "web"

        channel = session_id.split("_", 1)[0]
        if channel == "sess":
            return "web"
        if channel in {"acp", "cron", "heartbeat", "feishu", "web", "dingtalk", "wecom"}:
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

    def _resolve_model_name(self) -> str:
        """Resolve current model name from model request config."""
        if self._model_request_config and hasattr(self._model_request_config, "model_name"):
            return self._model_request_config.model_name or "unknown"
        return "unknown"

    def _write_runtime_state(self, mode: str, language: str, channel: str) -> None:
        """将当前运行时状态写入 config 目录下的 runtime_state.yaml。"""
        try:
            git_branch = "N/A"
            git_bin = which("git")
            if git_bin:
                try:
                    r = subprocess.run(
                        [git_bin, "rev-parse", "--abbrev-ref", "HEAD"],
                        capture_output=True,
                        text=True,
                        timeout=5,
                        cwd=os.path.dirname(__file__),
                    )
                    if r.returncode == 0 and r.stdout.strip():
                        git_branch = r.stdout.strip()
                except Exception:
                    pass

            mode_display = _MODE_DISPLAY_MAP.get(mode, {}).get(language, mode)

            state = {
                "model": self._resolve_model_name(),
                "mode": mode_display,
                "language": language,
                "channel": channel,
                "agent": self._agent_name,
                "platform": f"{platform.system()} {platform.machine()}",
                "python": platform.python_version(),
                "git_branch": git_branch,
            }
            path = get_config_dir() / "runtime_state.yaml"
            with open(path, "w", encoding="utf-8") as f:
                yaml.safe_dump(state, f, allow_unicode=True, sort_keys=False)
        except Exception as exc:
            logger.debug("[JiuWenClawDeepAdapter] write runtime_state failed: %s", exc)

    @staticmethod
    def _browser_runtime_enabled() -> bool:
        """Whether browser runtime support is enabled for DeepAgent subagent wiring."""
        value = (
            str(
                os.getenv("PLAYWRIGHT_RUNTIME_MCP_ENABLED")
                or os.getenv("BROWSER_RUNTIME_MCP_ENABLED")
                or ""
            )
            .strip()
            .lower()
        )
        return value in {"1", "true", "yes", "on"}

    @staticmethod
    def _resolve_managed_browser_binary_from_config() -> str:
        """Resolve managed-browser binary from saved browser config."""
        config_base = get_config()
        if not isinstance(config_base, dict):
            return ""
        config = resolve_env_vars(config_base)
        browser_cfg = config.get("browser", {}) if isinstance(config, dict) else {}
        if not isinstance(browser_cfg, dict):
            return ""
        chrome_path = browser_cfg.get("chrome_path", "")
        if isinstance(chrome_path, str):
            return chrome_path.strip()
        if not isinstance(chrome_path, dict):
            return ""
        platform_map = {
            "win32": "windows",
            "cygwin": "windows",
            "darwin": "macos",
            "linux": "linux",
            "linux2": "linux",
        }
        os_key = platform_map.get(os.sys.platform, "default")
        for key in (os_key, "default"):
            value = chrome_path.get(key, "")
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    @staticmethod
    def _is_subagent_enabled(subagent_cfg: Any) -> bool:
        """Treat only explicit `enabled: true` as enabled."""
        return isinstance(subagent_cfg, dict) and bool(subagent_cfg.get("enabled", False))

    @staticmethod
    def _is_subagent_default_enabled(subagent_cfg: Any) -> bool:
        """Default-enabled subagent: enabled unless explicitly set to false."""
        if not isinstance(subagent_cfg, dict):
            return True  # no config → default enabled
        return subagent_cfg.get("enabled", True) is not False

    def _build_configured_subagents(
        self,
        model: Model,
        config: dict[str, Any],
        config_base: dict[str, Any] | None = None,
    ) -> tuple[list[Any] | None, bool]:
        """Build configured research + browser subagents (agent 模式)."""
        react_cfg = config if isinstance(config, dict) else {}
        subagents_cfg = react_cfg.get("subagents")

        resolved_language = self._resolve_runtime_language()
        workspace = self._workspace_dir or "./"
        subagents: list[Any] = []
        should_add_general_purpose = False

        if isinstance(subagents_cfg, dict):
            general_agent_cfg = subagents_cfg.get("general_agent")
            if self._is_subagent_enabled(general_agent_cfg):
                should_add_general_purpose = True

            research_agent_cfg = subagents_cfg.get("research_agent")
            if self._is_subagent_enabled(research_agent_cfg):
                subagents.append(
                    build_research_agent_config(
                        model,
                        workspace=workspace,
                        language=resolved_language,
                        max_iterations=parse_int(
                            research_agent_cfg.get("max_iterations"),
                            react_cfg.get("max_iterations", 15),
                        ),
                    )
                )

        browser_agent_cfg = (
            subagents_cfg.get("browser_agent") if isinstance(subagents_cfg, dict) else {}
        )
        browser_enabled = self._browser_runtime_enabled()
        if browser_enabled:
            if not str(os.getenv("BROWSER_DRIVER") or "").strip():
                os.environ["BROWSER_DRIVER"] = "managed"
                logger.info(
                    "[JiuWenClawDeepAdapter] browser subagent enabled without BROWSER_DRIVER; "
                    "defaulting to managed mode"
                )
            if not str(os.getenv("BROWSER_MANAGED_BINARY") or "").strip():
                chrome_path = self._resolve_managed_browser_binary_from_config()
                if chrome_path:
                    os.environ["BROWSER_MANAGED_BINARY"] = chrome_path
                    logger.info(
                        "[JiuWenClawDeepAdapter] using browser.chrome_path for managed browser: %s",
                        chrome_path,
                    )
            subagents.append(
                build_browser_agent_config(
                    model,
                    workspace=workspace,
                    language=resolved_language,
                    max_iterations=parse_int(
                        (
                            browser_agent_cfg.get("max_iterations")
                            if isinstance(browser_agent_cfg, dict)
                            else None
                        ),
                        react_cfg.get("max_iterations", 15),
                    ),
                )
            )
        elif (
            isinstance(subagents_cfg, dict)
            and isinstance(browser_agent_cfg, dict)
            and browser_agent_cfg
        ):
            logger.info(
                "[JiuWenClawDeepAdapter] browser_agent config detected but browser runtime is not enabled; "
                "skipping browser subagent registration"
            )

        return subagents or None, should_add_general_purpose

    @staticmethod
    def _build_mcp_server_config(entry: dict[str, Any]) -> McpServerConfig | None:
        name = str(entry.get("name", "")).strip()
        if not name:
            return None
        transport = str(entry.get("transport", "")).strip().lower()
        if transport not in {"stdio", "sse"}:
            return None
        payload: dict[str, Any] = {
            "server_name": name,
            "client_type": transport,
        }
        if transport == "stdio":
            command = str(entry.get("command", "")).strip()
            if not command:
                return None
            params: dict[str, Any] = {"command": command}
            args = entry.get("args")
            if isinstance(args, list):
                params["args"] = [str(item) for item in args]
            cwd = entry.get("cwd")
            if isinstance(cwd, str) and cwd.strip():
                params["cwd"] = cwd.strip()
            env = entry.get("env")
            if isinstance(env, dict):
                params["env"] = {str(k): str(v) for k, v in env.items()}
            timeout_s = entry.get("timeout_s")
            if isinstance(timeout_s, (int, float)) and int(timeout_s) > 0:
                params["timeout_s"] = int(timeout_s)
            payload["server_path"] = f"stdio://{name}"
            payload["params"] = params
        else:
            url = str(entry.get("url", "")).strip()
            if not url:
                return None
            payload["server_path"] = url
            params: dict[str, Any] = {}
            headers = entry.get("headers")
            if isinstance(headers, dict):
                params["headers"] = {str(k): str(v) for k, v in headers.items()}
            timeout_s = entry.get("timeout_s")
            if isinstance(timeout_s, (int, float)) and int(timeout_s) > 0:
                params["timeout_s"] = int(timeout_s)
            if params:
                payload["params"] = params
        return McpServerConfig(**payload)

    @staticmethod
    def _extract_enabled_mcp_server_entries(config_base: dict[str, Any]) -> list[dict[str, Any]]:
        if not isinstance(config_base, dict):
            return []
        mcp_cfg = config_base.get("mcp", {})
        if not isinstance(mcp_cfg, dict):
            return []
        servers = mcp_cfg.get("servers", [])
        if not isinstance(servers, list):
            return []
        result: list[dict[str, Any]] = []
        for item in servers:
            if not isinstance(item, dict):
                continue
            if not bool(item.get("enabled", True)):
                continue
            result.append(item)
        return result

    async def _register_mcp_server(self, cfg: McpServerConfig, *, tag: str) -> bool:
        if self._instance is None:
            return False
        try:
            result = await Runner.resource_mgr.add_mcp_server(cfg, tag=tag)
            ok = True
            if result is not None:
                is_ok = getattr(result, "is_ok", None)
                if callable(is_ok):
                    ok = bool(is_ok())
                elif isinstance(result, bool):
                    ok = result
            if ok:
                server_id = str(getattr(cfg, "server_id", "") or "").strip()
                if not server_id:
                    logger.warning(
                        "[JiuWenClawDeepAdapter] MCP server_id missing after registration: %s", cfg
                    )
                    return False
                self._instance.ability_manager.add(cfg)
                self._registered_mcp_server_ids.add(server_id)
                self._registered_mcp_servers[server_id] = cfg
                return True
            logger.warning(
                "[JiuWenClawDeepAdapter] MCP server register failed: %s", cfg.server_name
            )
            return False
        except Exception as exc:
            logger.warning("[JiuWenClawDeepAdapter] MCP server register failed: %s", exc)
            return False

    async def _unregister_mcp_server(self, server_id: str) -> None:
        if self._instance is None:
            return
        cfg = self._registered_mcp_servers.get(server_id)
        server_name = getattr(cfg, "server_name", "") if cfg is not None else ""
        try:
            await Runner.resource_mgr.remove_mcp_server(server_id)
        except Exception as exc:
            logger.warning("[JiuWenClawDeepAdapter] MCP server remove failed: %s", exc)
        if server_name:
            try:
                self._instance.ability_manager.remove(server_name)
            except Exception as exc:
                logger.warning("[JiuWenClawDeepAdapter] MCP ability remove failed: %s", exc)
        self._registered_mcp_server_ids.discard(server_id)
        self._registered_mcp_servers.pop(server_id, None)

    async def _register_mcp_servers_from_config(
        self, config_base: dict[str, Any], *, tag: str = "agent.main"
    ) -> None:
        enabled_entries = self._extract_enabled_mcp_server_entries(config_base)
        for entry in enabled_entries:
            cfg = self._build_mcp_server_config(entry)
            if cfg is None:
                logger.warning(
                    "[JiuWenClawDeepAdapter] skip invalid mcp server entry: %s",
                    entry.get("name", "<unknown>"),
                )
                continue
            await self._register_mcp_server(cfg, tag=tag)

    async def _sync_mcp_servers_for_runtime(
        self, config_base: dict[str, Any], *, tag: str = "agent.reload"
    ) -> None:
        if self._instance is None:
            return
        enabled_entries = self._extract_enabled_mcp_server_entries(config_base)
        desired_by_name: dict[str, McpServerConfig] = {}
        for entry in enabled_entries:
            cfg = self._build_mcp_server_config(entry)
            if cfg is None:
                logger.warning(
                    "[JiuWenClawDeepAdapter] skip invalid mcp server entry: %s",
                    entry.get("name", "<unknown>"),
                )
                continue
            server_name = str(getattr(cfg, "server_name", "") or "").strip()
            if not server_name:
                logger.warning(
                    "[JiuWenClawDeepAdapter] skip mcp server without server_name: %s",
                    entry.get("name", "<unknown>"),
                )
                continue
            desired_by_name[server_name] = cfg

        current_by_name: dict[str, tuple[str, McpServerConfig]] = {}
        for server_id, cfg in self._registered_mcp_servers.items():
            server_name = str(getattr(cfg, "server_name", "") or "").strip()
            if not server_name or server_name in current_by_name:
                continue
            current_by_name[server_name] = (server_id, cfg)

        current_names = set(current_by_name.keys())
        desired_names = set(desired_by_name.keys())
        to_remove = current_names - desired_names
        to_add = desired_names - current_names
        to_check = current_names & desired_names

        for server_name in to_remove:
            server_id = current_by_name[server_name][0]
            await self._unregister_mcp_server(server_id)

        for server_name in to_add:
            await self._register_mcp_server(desired_by_name[server_name], tag=tag)

        for server_name in to_check:
            server_id, current_cfg = current_by_name[server_name]
            desired_cfg = desired_by_name[server_name]
            current_sig = {
                "server_name": getattr(current_cfg, "server_name", None),
                "client_type": getattr(current_cfg, "client_type", None),
                "server_path": getattr(current_cfg, "server_path", None),
                "params": getattr(current_cfg, "params", None),
                "auth_headers": getattr(current_cfg, "auth_headers", None),
                "auth_query_params": getattr(current_cfg, "auth_query_params", None),
            }
            desired_sig = {
                "server_name": getattr(desired_cfg, "server_name", None),
                "client_type": getattr(desired_cfg, "client_type", None),
                "server_path": getattr(desired_cfg, "server_path", None),
                "params": getattr(desired_cfg, "params", None),
                "auth_headers": getattr(desired_cfg, "auth_headers", None),
                "auth_query_params": getattr(desired_cfg, "auth_query_params", None),
            }
            if json.dumps(current_sig, sort_keys=True, default=str) == json.dumps(
                desired_sig, sort_keys=True, default=str
            ):
                continue
            await self._unregister_mcp_server(server_id)
            await self._register_mcp_server(desired_cfg, tag=tag)

    @staticmethod
    def _build_vision_model_config(
        config_base: dict[str, Any],
    ) -> VisionModelConfig | None:
        """Build DeepAgent vision config from service config/env mapping."""
        if not dedicated_multimodal_model_configured(config_base, "vision"):
            logger.info(
                "[JiuWenClawDeepAdapter] vision tools skipped: models.vision has no dedicated "
                "api_key in config.yaml"
            )
            return None
        apply_vision_model_config_from_yaml(config_base)
        api_key = str(os.getenv("VISION_API_KEY", "")).strip()
        base_url = str(os.getenv("VISION_BASE_URL") or os.getenv("VISION_API_BASE") or "").strip()
        model_name = str(os.getenv("VISION_MODEL") or os.getenv("VISION_MODEL_NAME") or "").strip()
        if not api_key or not base_url or not model_name:
            logger.info("[JiuWenClawDeepAdapter] vision tools skipped: incomplete config")
            return None
        return VisionModelConfig(
            api_key=api_key,
            base_url=base_url,
            model=model_name,
            max_retries=parse_int(os.getenv("VISION_MAX_RETRIES"), 3),
        )

    @staticmethod
    def _build_audio_model_config(
        config_base: dict[str, Any],
    ) -> AudioModelConfig | None:
        """Build DeepAgent audio config from service config/env mapping."""
        if not dedicated_multimodal_model_configured(config_base, "audio"):
            logger.info(
                "[JiuWenClawDeepAdapter] skip full audio LLM config: models.audio has no "
                "dedicated api_key in config.yaml"
            )
            return None
        apply_audio_model_config_from_yaml(config_base)
        api_key = str(os.getenv("AUDIO_API_KEY", "")).strip()
        base_url = str(os.getenv("AUDIO_BASE_URL") or os.getenv("AUDIO_API_BASE") or "").strip()
        if not api_key or not base_url:
            logger.info("[JiuWenClawDeepAdapter] audio tools skipped: incomplete config")
            return None
        transcription_model = str(
            os.getenv("AUDIO_TRANSCRIPTION_MODEL") or os.getenv("AUDIO_MODEL_NAME") or ""
        ).strip()
        question_answering_model = str(
            os.getenv("AUDIO_QUESTION_ANSWERING_MODEL") or os.getenv("AUDIO_MODEL_NAME") or ""
        ).strip()
        config_kwargs: dict[str, Any] = {
            "api_key": api_key,
            "base_url": base_url,
            "max_retries": parse_int(os.getenv("AUDIO_MAX_RETRIES"), 3),
            "http_timeout": parse_int(os.getenv("AUDIO_HTTP_TIMEOUT"), 20),
            "max_audio_bytes": parse_int(
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
            config_kwargs["question_answering_model"] = question_answering_model
        return AudioModelConfig(**config_kwargs)

    @staticmethod
    def _build_video_model_config(
        config_base: dict[str, Any],
    ) -> bool:
        """Build DeepAgent video config from service config/env mapping."""
        apply_video_model_config_from_yaml(config_base)
        if not dedicated_multimodal_model_configured(config_base, "video"):
            logger.info(
                "[JiuWenClawDeepAdapter] skip video_understanding: models.video has no "
                "dedicated api_key in config.yaml"
            )
            return False
        if not os.getenv("VIDEO_API_KEY"):
            logger.info("[JiuWenClawDeepAdapter] video tools skipped: incomplete config")
            return False
        return True

    @staticmethod
    def _build_image_gen_model_config(
        config_base: dict[str, Any],
    ) -> bool:
        """Build DeepAgent image generation config from service config/env mapping."""
        apply_image_gen_model_config_from_yaml(config_base)
        if not os.getenv("IMAGE_GEN_API_KEY"):
            logger.info("[JiuWenClawDeepAdapter] image_gen tool skipped: incomplete config")
            return False
        return True

    def _iter_runtime_audio_tools(self, agent_id: str | None) -> list[Any]:
        """可注册的音频工具：须先在 config 中为 ``models.audio`` 配置独立 ``api_key``。

        与 vision / video 一致，无该 key 时不挂载任何音频工具（含 ``audio_metadata``）。
        已配置 key 且 ``_audio_model_config`` 完整时注册全部 harness 音频工具；否则仅保留
        ``audio_metadata``（ACRCloud，仍依赖 ``ACR_*`` 环境变量在运行时识别曲库）。
        """
        config_base = get_config()
        if not dedicated_multimodal_model_configured(config_base, "audio"):
            logger.info(
                "[JiuWenClawDeepAdapter] skip all audio tools (incl. audio_metadata): "
                "models.audio 未配置独立 api_key"
            )
            return []
        lang = self._resolve_runtime_language()
        cfg = self._audio_model_config if self._audio_model_config else None
        tools = list(
            create_audio_tools(
                language=lang,
                audio_model_config=cfg,
                agent_id=agent_id,
            )
        )
        if self._audio_model_config:
            return tools
        filtered = [t for t in tools if t.card.name == "audio_metadata"]
        if len(tools) > len(filtered):
            logger.info(
                "[JiuWenClawDeepAdapter] skip audio_transcription & audio_question_answering: "
                "incomplete audio LLM config (metadata only)"
            )
        return filtered

    def _refresh_multimodal_configs(
        self,
        config_base: dict[str, Any],
    ) -> None:
        """Refresh cached multimodal configs and live tool instances."""
        self._vision_model_config = self._build_vision_model_config(config_base)
        self._audio_model_config = self._build_audio_model_config(config_base)
        self._video_model_config = self._build_video_model_config(config_base)
        self._image_gen_model_config = self._build_image_gen_model_config(config_base)

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
                logger.warning("[JiuWenClawDeepAdapter] %s reload failed: %s", warn_label, exc)
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
            item.card.name if hasattr(item, "card") else item.name for item in self._tool_cards
        }
        if card.name not in existing_names:
            self._tool_cards.append(card)

    def _prioritize_paid_search_tool_card(self) -> None:
        """Keep paid_search before free_search when both cards are present."""
        if not self._tool_cards:
            return
        paid_cards = [
            item
            for item in self._tool_cards
            if (item.card.name if hasattr(item, "card") else item.name) == "paid_search"
        ]
        if not paid_cards:
            return
        remaining_cards = [
            item
            for item in self._tool_cards
            if (item.card.name if hasattr(item, "card") else item.name) != "paid_search"
        ]
        free_index = next(
            (
                idx
                for idx, item in enumerate(remaining_cards)
                if (item.card.name if hasattr(item, "card") else item.name) == "free_search"
            ),
            0,
        )
        self._tool_cards = remaining_cards[:free_index] + paid_cards + remaining_cards[free_index:]

    def _prune_tool_cards(self, tool_names: set[str]) -> None:
        """Remove tracked tool cards by tool name."""
        if not self._tool_cards:
            return
        self._tool_cards = [
            item
            for item in self._tool_cards
            if (item.card.name if hasattr(item, "card") else item.name) not in tool_names
        ]

    def _sync_multimodal_tools_for_runtime(self) -> None:
        """Sync multimodal tool registration after config reload."""
        agent_id = self._instance.card.id if self._instance else None
        self._vision_tools, self._vision_tools_registered = self._sync_tool_group(
            current_tools=self._vision_tools,
            registered=self._vision_tools_registered,
            enabled=self._vision_model_config is not None,
            create_fn=lambda: create_vision_tools(
                language=self._resolve_runtime_language(),
                vision_model_config=self._vision_model_config,
                agent_id=agent_id,
            ),
            warn_label="vision tools",
        )

        self._audio_tools, self._audio_tools_registered = self._sync_tool_group(
            current_tools=self._audio_tools,
            registered=self._audio_tools_registered,
            enabled=True,
            create_fn=lambda: self._iter_runtime_audio_tools(agent_id),
            warn_label="audio tools",
        )

        _, self._video_tool_registered = self._sync_tool_group(
            current_tools=[video_understanding],
            registered=self._video_tool_registered,
            enabled=bool(self._video_model_config),
            create_fn=lambda: [video_understanding],
            warn_label="video tool",
        )

        _, self._image_gen_tool_registered = self._sync_tool_group(
            current_tools=[generate_image],
            registered=self._image_gen_tool_registered,
            enabled=bool(self._image_gen_model_config),
            create_fn=lambda: [generate_image],
            warn_label="generate_image tool",
        )

    def _sync_paid_search_tool_for_runtime(self) -> None:
        """Sync paid-search tool registration after config reload."""
        agent_id = self._instance.card.id if self._instance else None
        tools, self._paid_search_registered = self._sync_tool_group(
            current_tools=[self._paid_search_tool] if self._paid_search_tool else [],
            registered=self._paid_search_registered,
            enabled=is_paid_search_enabled(),
            create_fn=lambda: [
                WebPaidSearchTool(language=self._resolve_runtime_language(), agent_id=agent_id)
            ],
            warn_label="paid search tool",
        )
        self._paid_search_tool = tools[0] if tools else None
        if self._paid_search_tool is not None:
            self._prioritize_paid_search_tool_card()

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

    @staticmethod
    def _build_model_from_entry(mcc: dict, mco: dict) -> Model:
        """根据单个模型条目的 model_client_config / model_config_obj 构建 Model 实例。"""
        name = mcc.get("model_name", "")
        m_config = ModelRequestConfig(
            model=name,
            temperature=mco.get("temperature", 0.95),
        )
        mcc_fields = {k: v for k, v in mcc.items() if k != "model_name"}
        if not mcc_fields.get("client_provider"):
            mcc_fields["client_provider"] = "OpenAI"
        return Model(model_client_config=ModelClientConfig(**mcc_fields), model_config=m_config)

    def _build_model_cache_from_defaults(self, config: dict) -> None:
        """从 models.defaults 列表构建模型缓存。

        key 使用 {model_name}#{index} 格式以支持同名模型共存。
        同时记录 _model_name_to_keys 映射以便按 model_name 查找。
        """
        self._model_name_to_keys.clear()
        name_counter: dict[str, int] = {}

        for entry in get_default_models(config):
            mcc = entry.get("model_client_config") or {}
            if not mcc.get("model_name"):
                continue
            model_name = mcc["model_name"]
            idx = name_counter.get(model_name, 0)
            name_counter[model_name] = idx + 1
            cache_key = f"{model_name}#{idx}"
            try:
                self._model_cache[cache_key] = self._build_model_from_entry(
                    mcc,
                    entry.get("model_config_obj") or {},
                )
            except Exception as exc:
                logger.warning(
                    "[JiuWenClawDeepAdapter] 跳过无效模型条目 %s: %s",
                    model_name, exc,
                )
                continue
            if model_name not in self._model_name_to_keys:
                self._model_name_to_keys[model_name] = []
            self._model_name_to_keys[model_name].append(cache_key)

            # 同时用纯 model_name 作为 key 指向 is_default=true 的条目
            if entry.get("is_default") is True:
                self._model_cache[model_name] = self._model_cache[cache_key]

            alias = entry.get("alias", "")
            if alias and alias != model_name and alias not in self._model_cache:
                self._model_cache[alias] = self._model_cache[cache_key]

    def _build_model_cache_legacy(self, config: dict) -> None:
        """回退到旧格式（models.default / react 段）构建单条目缓存。"""
        default_model_config = config.get("models", {}).get("default", {})
        react_config = config.get("react", {})

        mcc = dict(
            default_model_config.get("model_client_config")
            or react_config.get("model_client_config")
            or {}
        )
        model_name = mcc.get("model_name") or react_config.get("model_name") or "gpt-4"
        if "model_name" not in mcc:
            mcc["model_name"] = model_name

        mco = (
            default_model_config.get("model_config_obj")
            or react_config.get("model_config_obj")
            or {}
        )
        try:
            self._model_cache[model_name] = self._build_model_from_entry(mcc, mco)
        except Exception as exc:
            logger.warning(
                "[JiuWenClawDeepAdapter] 跳过无效模型条目(legacy) %s: %s",
                model_name, exc,
            )

    def _create_model(self, config: dict) -> Model:
        self._model_cache.clear()
        self._build_model_cache_from_defaults(config)
        if not self._model_cache:
            self._build_model_cache_legacy(config)

        if not self._model_cache:
            raise ValueError(
                "No valid model entries found in config — all entries failed validation. "
                "Check that api_key and api_base are set for at least one model."
            )

        # 优先取 is_default=true 的条目（纯 model_name key），否则取第一个
        default_name = None
        for name, keys in self._model_name_to_keys.items():
            if name in self._model_cache:
                default_name = name
                break
        if default_name is None:
            # 回退：取第一个 #index key
            for key in self._model_cache:
                if "#" in key:
                    default_name = key
                    break
        if default_name is None:
            default_name = next(iter(self._model_cache))

        self._default_model_name = default_name
        self._model = self._model_cache[default_name]
        self._model_client_config = self._model.model_client_config
        self._model_request_config = self._model.model_config
        return self._model

    def _resolve_model_for_request(self, request: AgentRequest) -> Model:
        """根据请求中的 model_name 参数查找对应模型（支持别名），未匹配则回退默认模型。

        支持两种格式：
        - 纯 model_name：查找 is_default=true 的条目
        - {model_name}#{index}：查找指定索引的条目
        """
        requested = (request.params.get("model_name") or "").strip()
        if not requested:
            return self._model
        # 精确匹配（#index 格式或纯 model_name key）
        if requested in self._model_cache:
            return self._model_cache[requested]
        # 回退：按纯 model_name 查找 is_default=true 的条目
        name_to_keys = self._model_name_to_keys
        if requested in name_to_keys and requested in self._model_cache:
            return self._model_cache[requested]
        return self._model

    def _apply_model_to_react_agent(self, model: Model) -> None:
        """将指定模型应用到 react_agent 实例（替换 _llm 和 _config 字段）。

        react_agent._railed_model_call 使用 self._config.model_name 作为 model= 参数，
        因此需要同时替换 _llm 和 _config 中的模型相关字段。
        """
        react_agent = getattr(self._instance, "_react_agent", None)
        if react_agent is None:
            return
        if callable(getattr(react_agent, "set_llm", None)):
            react_agent.set_llm(model)
        config = getattr(react_agent, "_config", None)
        if config is not None:
            config.model_name = model.model_config.model_name
            config.model_client_config = model.model_client_config
            config.model_config_obj = model.model_config
        self._model_request_config = model.model_config

    @staticmethod
    def _resolve_skill_mode(config: dict[str, Any]) -> str:
        """Validate configured skill mode and fallback safely on invalid values."""
        raw_skill_mode = config.get("skill_mode", SkillUseRail.SKILL_MODE_ALL)
        valid_modes = {
            SkillUseRail.SKILL_MODE_AUTO_LIST,
            SkillUseRail.SKILL_MODE_ALL,
        }
        if isinstance(raw_skill_mode, str) and raw_skill_mode in valid_modes:
            return raw_skill_mode

        logger.warning(
            "[JiuWenClawDeepAdapter] invalid skill_mode=%r, fallback to %s",
            raw_skill_mode,
            SkillUseRail.SKILL_MODE_ALL,
        )
        return SkillUseRail.SKILL_MODE_ALL

    @staticmethod
    def _build_response_prompt_rail() -> ResponsePromptRail | None:
        """Build ResponsePromptRail so message rules keep priority ordering."""
        try:
            rail = ResponsePromptRail()
            logger.info("[JiuWenClawDeepAdapter] ResponsePromptRail create success")
        except Exception as exc:
            logger.warning("[JiuWenClawDeepAdapter] ResponsePromptRail create failed: %s", exc)
            rail = None
        return rail

    @staticmethod
    def _create_sandbox_sys_operation(sandbox_url, sandbox_type) -> SysOperationCard | None:
        """Create a sandbox sys operation."""
        import openjiuwen.extensions.sys_operation.sandbox.providers
        try:
            file_funcs = [
                get_deepagent_agent_md_path,
                get_deepagent_heartbeat_path,
                get_deepagent_identity_md_path,
                get_deepagent_soul_md_path,
                get_deepagent_user_md_path
            ]
            sandbox_policy = {
                "policy": {
                    'filesystem_policy': {
                        'files': [],
                        'directories': []
                    }
                },
                "policy_mode": "append",
            }
            for func in file_funcs:
                path = func()
                if path is not None:
                    sandbox_policy["policy"]["filesystem_policy"]["files"].append(
                        {"path": str(path), "permissions": "0666"}
                    )
            sandbox_policy["policy"]["filesystem_policy"]["directories"].append(
                {"path": str(get_agent_memory_dir() / "daily_memory"), "permissions": "0777"}
            )
            gateway_config = SandboxGatewayConfig(
                isolation=SandboxIsolationConfig(container_scope=ContainerScope.SYSTEM),
                launcher_config=PreDeployLauncherConfig(
                    base_url=sandbox_url,
                    sandbox_type=sandbox_type,
                    idle_ttl_seconds=600,
                    extra_params=sandbox_policy,
                ),
            )
            sysop_card = SysOperationCard(
                mode=OperationMode.SANDBOX,
                work_config=LocalWorkConfig(shell_allowlist=None),
                gateway_config=gateway_config,
            )
            return sysop_card
        except Exception as exc:
            logger.warning("[JiuWenClawDeepAdapter] create sandbox sys operation failed: %s", exc)
            return None

    @staticmethod
    def _sys_operation_isolation_key(sysop_card: SysOperationCard) -> str | None:
        try:
            sys_operation = SysOperation(sysop_card)
            return sys_operation.isolation_key_template
        except Exception as exc:
            logger.debug(
                "[JiuWenClawDeepAdapter] failed to resolve sys_operation isolation key: %s",
                exc,
            )
            return None

    @staticmethod
    def _get_registered_sys_operation_by_isolation_key(
        isolation_key_template: str | None,
    ) -> SysOperation | None:
        if not isolation_key_template:
            return None

        try:
            resource_registry = getattr(Runner.resource_mgr, "_resource_registry", None)
            if resource_registry is None:
                return None
            sys_operation_mgr = resource_registry.sys_operation()
            owner_map = getattr(sys_operation_mgr, "_sandbox_key_owner_map", {})
            existing_op_id = owner_map.get(isolation_key_template)
            if not existing_op_id:
                return None
            return Runner.resource_mgr.get_sys_operation(existing_op_id)
        except Exception as exc:
            logger.debug(
                "[JiuWenClawDeepAdapter] failed to get registered sys_operation: %s",
                exc,
            )
            return None

    @staticmethod
    def _create_sys_operation() -> SysOperation | None:
        """Create a sys operation."""
        try:
            sandbox_url = _sandbox_config.get("url", None)
            sandbox_type = _sandbox_config.get("type", None)
            if sandbox_url and sandbox_type:
                sysop_card = JiuWenClawDeepAdapter._create_sandbox_sys_operation(sandbox_url, sandbox_type)
            else:
                sysop_card = SysOperationCard(
                    mode=OperationMode.LOCAL,
                    work_config=LocalWorkConfig(shell_allowlist=None),
                )
            if sysop_card is None:
                logger.warning("[JiuWenClawDeepAdapter] add sys_operation failed: sysop_card is None")
                return None
            isolation_key_template = JiuWenClawDeepAdapter._sys_operation_isolation_key(sysop_card)
            registered_sys_operation = (
                JiuWenClawDeepAdapter._get_registered_sys_operation_by_isolation_key(
                    isolation_key_template
                )
            )
            if registered_sys_operation is not None:
                logger.info(
                    "[JiuWenClawDeepAdapter] reuse registered sys_operation: %s",
                    registered_sys_operation.id,
                )
                return registered_sys_operation

            result = Runner.resource_mgr.add_sys_operation(sysop_card)
            if result.is_err():
                registered_sys_operation = (
                    JiuWenClawDeepAdapter._get_registered_sys_operation_by_isolation_key(
                        isolation_key_template
                    )
                )
                if registered_sys_operation is not None:
                    logger.info(
                        "[JiuWenClawDeepAdapter] reuse registered sys_operation after add failure: %s",
                        registered_sys_operation.id,
                    )
                    return registered_sys_operation
                logger.warning("[JiuWenClawDeepAdapter] add sys_operation failed: %s", result.msg())
                return None
            return Runner.resource_mgr.get_sys_operation(sysop_card.id)
        except Exception as exc:
            logger.warning("[JiuWenClawDeepAdapter] add sys_operation failed: %s", exc)
            return None

    @staticmethod
    def _build_filesystem_rail() -> SysOperationRail | None:
        """Build SysOperationRail."""
        try:
            fs_rail = SysOperationRail()
            logger.info("[JiuWenClawDeepAdapter] SysOperationRail create success")
        except Exception as exc:
            logger.warning("[JiuWenClawDeepAdapter] SysOperationRail create failed: %s", exc)
            fs_rail = None
        return fs_rail

    def _build_skill_rail(
        self, config: dict[str, Any], include_tools: bool = False
    ) -> SkillUseRail | None:
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

    def _build_skill_evolution_rail(self, config: dict[str, Any]) -> SkillEvolutionRail | None:
        """Build SkillEvolutionRail."""
        try:
            _env_auto_scan = os.getenv("EVOLUTION_AUTO_SCAN")
            if _env_auto_scan is not None:
                evolution_auto_scan: bool = _env_auto_scan.lower() in ("true", "1", "yes")
            else:
                evolution_auto_scan = config.get("evolution", {}).get("auto_scan", False)
            model_name = self._default_model_name or config.get("model_name", "gpt-4")
            skill_evolution_rail = SkillEvolutionRail(
                skills_dir=str(get_agent_skills_dir()),
                llm=self._model,
                model=model_name,
                auto_scan=evolution_auto_scan,
                auto_save=False,
            )
            self._skill_evolution_rail = skill_evolution_rail
            logger.info("[JiuWenClaw] SkillEvolutionRail create success")
        except Exception as exc:
            logger.warning("[JiuWenClaw] SkillEvolutionRail create failed: %s", exc)
            skill_evolution_rail = None
        return skill_evolution_rail

    def _build_skill_create_rail(self, config: dict[str, Any]) -> SkillCreateRail | None:
        """Build SkillCreateRail for new skill creation proposals.

        SkillCreateRail requires task-loop mode (enable_task_loop=True) to function
        because it uses AFTER_TASK_ITERATION event and enqueue_follow_up().
        Config: evolution.skill_create (bool) - true to register rail with auto_trigger=true.
        Env: SKILL_CREATE - takes precedence over config.yaml.
        """
        try:
            skill_create_enabled = _get_skill_create_enabled(config)
            # Check if skill_create is explicitly enabled
            if not skill_create_enabled:
                logger.debug("[JiuWenClaw] SkillCreateRail disabled by config")
                return None

            language = config.get("language", "cn")
            rail = SkillCreateRail(
                skills_dir=str(get_agent_skills_dir()),
                auto_trigger=True,  # When skill_create=true, auto_trigger is always true
                language=language,
            )
            self._skill_create_rail = rail
            logger.info("[JiuWenClaw] SkillCreateRail created with auto_trigger=True")
        except Exception as exc:
            logger.warning("[JiuWenClaw] SkillCreateRail create failed: %s", exc)
            rail = None
        return rail

    @staticmethod
    def _build_stream_event_rail() -> JiuClawStreamEventRail | None:
        """Build JiuClawStreamEventRail."""
        try:
            stream_event_rail = JiuClawStreamEventRail()
            logger.info("[JiuWenClawDeepAdapter] JiuClawStreamEventRail create success")
        except Exception as exc:
            logger.warning("[JiuWenClawDeepAdapter] JiuClawStreamEventRail create failed: %s", exc)
            stream_event_rail = None
        return stream_event_rail

    @staticmethod
    def _build_task_planning_rail() -> TaskPlanningRail | None:
        """Build TaskPlanningRail."""
        try:
            task_planning_rail = TaskPlanningRail()
            logger.info("[JiuWenClawDeepAdapter] TaskPlanningRail create success")
        except Exception as exc:
            logger.warning("[JiuWenClawDeepAdapter] TaskPlanningRail create failed: %s", exc)
            task_planning_rail = None
        return task_planning_rail

    @staticmethod
    def _build_subagent_rail() -> SubagentRail | None:
        """Build SubagentRail for subagent delegation."""
        try:
            subagent_rail = SubagentRail()
            logger.info("[JiuWenClawDeepAdapter] SubagentRail create success")
        except Exception as exc:
            logger.warning("[JiuWenClawDeepAdapter] SubagentRail create failed: %s", exc)
            subagent_rail = None
        return subagent_rail

    @staticmethod
    def _build_security_rail() -> SecurityRail | None:
        """Build SecurityPromptRail."""
        try:
            security_prompt_rail = SecurityRail()
            logger.info("[JiuWenClawDeepAdapter] SecurityPromptRail create success")
        except Exception as exc:
            logger.warning("[JiuWenClawDeepAdapter] SecurityPromptRail create failed: %s", exc)
            security_prompt_rail = None
        return security_prompt_rail

    def _build_memory_rail(self, mode: str) -> MemoryRail | None:
        try:
            config = get_config()
            embed_config = config.get("embed") if isinstance(config, dict) else None
            has_api_key = (
                embed_config.get("embed_api_key") if isinstance(embed_config, dict) else None
            )
            has_base_url = (
                embed_config.get("embed_base_url") if isinstance(embed_config, dict) else None
            )
            has_model = embed_config.get("embed_model") if isinstance(embed_config, dict) else None
            if not all([has_api_key, has_base_url, has_model]):
                logger.warning(
                    "[JiuWenClawDeepAdapter] MemoryRail create failed: No available embedding config"
                )
            self._is_proactive_memory = is_proactive_memory(mode, config)
            memory_rail = MemoryRail(
                embedding_config=EmbeddingConfig(
                    model_name=embed_config.get("embed_model"),
                    base_url=embed_config.get("embed_base_url"),
                    api_key=embed_config.get("embed_api_key"),
                ),
                is_proactive=self._is_proactive_memory,
            )
            logger.info("[JiuWenClawDeepAdapter] MemoryRail create success")
        except Exception as exc:
            logger.warning("[JiuWenClawDeepAdapter] MemoryRail create failed: %s", exc)
            memory_rail = None
        return memory_rail

    @staticmethod
    def _build_heartbeat_rail() -> HeartbeatRail | None:
        """Build HeartbeatRail."""
        try:
            heartbeat_rail = HeartbeatRail()
            logger.info("[JiuWenClawDeepAdapter] HeartbeatRail create success")
        except Exception as exc:
            logger.warning("[JiuWenClawDeepAdapter] HeartbeatRail create failed: %s", exc)
            heartbeat_rail = None
        return heartbeat_rail

    @staticmethod
    def _build_avatar_rail() -> Any | None:
        """Build AvatarPromptRail for digital avatar mode."""
        try:
            from jiuwenclaw.agents.harness.common.rails.avatar_rail import AvatarPromptRail

            rail = AvatarPromptRail()
            logger.info("[JiuWenClawDeepAdapter] AvatarPromptRail create success")
            return rail
        except Exception as exc:
            logger.warning("[JiuWenClawDeepAdapter] AvatarPromptRail create failed: %s", exc)
            return None

    def _build_runtime_prompt_rail(self) -> RuntimePromptRail | None:
        """Build RuntimePromptRail for per-model-call time/channel/runtime injection."""
        try:
            default_channel = (
                "acp"
                if self._is_acp_tool_profile(self._instance_overrides)
                else self._resolve_prompt_channel()
            )
            rail = RuntimePromptRail(
                language=self._resolve_runtime_language(),
                channel=default_channel,
            )
            logger.info("[JiuWenClawDeepAdapter] RuntimePromptRail create success")
        except Exception as exc:
            logger.warning("[JiuWenClawDeepAdapter] RuntimePromptRail create failed: %s", exc)
            rail = None
        return rail

    def _build_agent_rails(
        self, config: dict[str, Any], config_base: dict[str, Any], *, mode: str = "agent.plan"
    ) -> list[Any]:
        """Build DeepAgent rails consistently for cold start and hot reload."""

        @dataclass
        class _RailBuildInfo:
            attr_name: str
            build_func: callable
            params: dict = None

            def __post_init__(self):
                self.params = self.params or {}

        rail_infos = [
            _RailBuildInfo("_runtime_prompt_rail", self._build_runtime_prompt_rail),
            _RailBuildInfo("_response_prompt_rail", self._build_response_prompt_rail),
            _RailBuildInfo("_stream_event_rail", self._build_stream_event_rail),
            _RailBuildInfo("_task_planning_rail", self._build_task_planning_rail),
            _RailBuildInfo("_security_rail", self._build_security_rail),
            _RailBuildInfo("_heartbeat_rail", self._build_heartbeat_rail),
            _RailBuildInfo("_avatar_rail", self._build_avatar_rail),
            _RailBuildInfo("_subagent_rail", self._build_subagent_rail),
            _RailBuildInfo(
                "_permission_rail",
                build_permission_rail,
                {
                    "config": config_base,
                    "llm": self._model,
                    "model_name": config_base.get("models", {})
                    .get("default", {})
                    .get("model_client_config", {})
                    .get("model_name", "gpt-4"),
                },
            ),
            _RailBuildInfo(
                "_context_processor_rail",
                _build_context_processor_rail,
                {"config": self._config_cache},
            ),
        ]

        # SkillEvolutionRail 不在冷启动时挂载，由 _update_rails_for_mode 按 mode 按需注册/注销
        # 智能模式下关闭自演进，plan 模式下按配置启用

        # MemoryRail 不在冷启动时挂载，由 _update_rails_for_mode 按 mode 按需注册/注销

        if self._filesystem_rail_enabled_for_profile():
            rail_infos.insert(1, _RailBuildInfo("_filesystem_rail", self._build_filesystem_rail))
        else:
            self._filesystem_rail = None
        rail_infos.insert(
            2 if self._filesystem_rail_enabled_for_profile() else 1,
            _RailBuildInfo(
                "_skill_rail",
                self._build_skill_rail,
                {"config": config, "include_tools": self._skill_include_tools_for_profile()},
            ),
        )

        rails_list = []
        for info in rail_infos:
            logger.info(
                "[JiuWenClawDeepAdapter] Building rail: %s with params: %s",
                info.attr_name,
                info.params,
            )
            rail_instance = info.build_func(**info.params)
            if rail_instance is not None:
                setattr(self, info.attr_name, rail_instance)
                rails_list.append(rail_instance)
                logger.info(
                    "[JiuWenClawDeepAdapter] Rail %s built successfully and added to rails_list",
                    info.attr_name,
                )
            else:
                logger.warning(
                    "[JiuWenClawDeepAdapter] Rail %s build returned None", info.attr_name
                )
        logger.info(
            "[JiuWenClawDeepAdapter] Total rails built: %d, rail names: %s",
            len(rails_list),
            [type(r).__name__ for r in rails_list],
        )
        return rails_list

    @staticmethod
    def _resolve_enable_task_loop(
        config: dict[str, Any], config_base: dict[str, Any] | None
    ) -> bool:
        """Resolve enable_task_loop considering skill_create requirement.

        SkillCreateRail requires task-loop mode (enable_task_loop=True) to function
        because it uses AFTER_TASK_ITERATION event and enqueue_follow_up().
        When skill_create=True, we force enable_task_loop=True regardless of user config.

        Args:
            config: The react config section.
            config_base: The full config base (contains evolution.skill_create).

        Returns:
            True if task-loop should be enabled, False otherwise.
        """
        config_base = config_base or get_config()
        skill_create_enabled = _get_skill_create_enabled(config_base)
        configured_value = config.get("enable_task_loop", True)

        if skill_create_enabled:
            if not configured_value:
                logger.warning(
                    "[JiuWenClawDeepAdapter] skill_create=True requires enable_task_loop=True; "
                    "overriding user config (enable_task_loop=%s -> True)",
                    configured_value,
                )
            return True
        return configured_value

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
        config_base = get_config()
        workspace_obj = Workspace(root_path=self._workspace_dir or "./", language=resolved_language)
        normalized_tool_cards = [
            tool.card if hasattr(tool, "card") else tool for tool in (tool_cards or [])
        ]
        configured_subagents, should_add_general_agent = self._build_configured_subagents(model, config, config_base)
        return DeepAgentConfig(
            model=model,
            card=agent_card,
            system_prompt=build_identity_prompt(
                mode="agent.fast",
                language=self._resolve_prompt_language(),
                channel=(
                    "acp"
                    if self._is_acp_tool_profile(self._instance_overrides)
                    else self._resolve_prompt_channel()
                ),
            ),
            context_engine_config=_deep_agent_context_engine_config(config),
            enable_task_loop=self._resolve_enable_task_loop(config, get_config()),
            max_iterations=config.get("max_iterations", 15),
            subagents=configured_subagents,
            add_general_purpose_agent=should_add_general_agent,
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
            completion_timeout=config.get("completion_timeout", 3600.0),
        )

    def _update_permission_rail(self, config_base: dict[str, Any] | None) -> None:
        """原地更新已有 PermissionRail 配置，或在首次启用时新建。"""
        permission_config = config_base.get("permissions", {}) if config_base else {}
        if self._permission_rail is not None:
            self._permission_rail.update_config(permission_config)
            logger.info("[JiuWenClawDeepAdapter] _permission_rail config hot-updated")
        elif permission_config.get("enabled", False):
            self._permission_rail = build_permission_rail(
                config=config_base,
                llm=self._model,
                model_name=config_base.get("models", {})
                .get("default", {})
                .get("model_client_config", {})
                .get("model_name", "gpt-4"),
            )
            if self._permission_rail is not None:
                logger.info("[JiuWenClawDeepAdapter] _permission_rail newly created on hot-reload")

    def _get_current_agent_rails(
        self, config: dict[str, Any], config_base: dict[str, Any] | None = None
    ) -> list[Any]:
        """Return rail instances that need to be re-initialized on hot reload.

        SkillUseRail, ContextEngineeringRail, and MemoryRail are rebuilt on config reload.
        All other rails read language dynamically from system_prompt_builder.language
        and are updated in-place where needed — they are NOT passed to configure()
        so their existing registered state is preserved without an uninit/init cycle.
        """
        # Apply in-place updates to skill_evolution_rail (no re-init needed).
        if self._skill_evolution_rail is not None:
            self._skill_evolution_rail.update_llm(self._model, self._default_model_name)
            _env_auto_scan = os.getenv("EVOLUTION_AUTO_SCAN")
            if _env_auto_scan is not None:
                self._skill_evolution_rail.auto_scan = _env_auto_scan.lower() in (
                    "true",
                    "1",
                    "yes",
                )

        self._skill_rail = self._build_skill_rail(
            config,
            include_tools=self._skill_include_tools_for_profile(),
        )

        if not self._filesystem_rail_enabled_for_profile():
            self._filesystem_rail = None

        self._update_permission_rail(config_base)

        rails_list = []
        if self._skill_rail is not None:
            rails_list.append(self._skill_rail)
        if self._context_assemble_rail is not None:
            rails_list.append(self._context_assemble_rail)
        if self._context_processor_rail is not None:
            rails_list.append(self._context_processor_rail)
        if self._memory_rail is not None:
            rails_list.append(self._memory_rail)
        if self._avatar_rail is not None:
            rails_list.append(self._avatar_rail)
        if self._permission_rail is not None:
            rails_list.append(self._permission_rail)
        return rails_list

    async def _get_tool_cards(self, agent_id: str):
        """Get tool cards."""
        tool_cards = []

        for wtool in [wiki_ingest, wiki_query, wiki_lint]:
            if not Runner.resource_mgr.get_tool(wtool.card.id):
                Runner.resource_mgr.add_tool(wtool)
            tool_cards.append(wtool.card)

        # 付费搜索工具：有任意一个付费 key 就注册
        if is_paid_search_enabled():
            self._paid_search_tool = WebPaidSearchTool(
                language=self._resolve_runtime_language(), agent_id=agent_id
            )
            Runner.resource_mgr.add_tool(self._paid_search_tool)
            tool_cards.append(self._paid_search_tool.card)
            self._paid_search_registered = True

        for tool_cls in [WebFreeSearchTool, WebFetchWebpageTool]:
            tool_instance = tool_cls(agent_id=agent_id)
            Runner.resource_mgr.add_tool(tool_instance)
            tool_cards.append(tool_instance.card)

        self._vision_tools = []
        self._vision_tools_registered = False
        if self._vision_model_config is not None:
            try:
                for tool in create_vision_tools(
                    language=self._resolve_runtime_language(),
                    vision_model_config=self._vision_model_config,
                    agent_id=agent_id,
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
        try:
            self._audio_tools = self._iter_runtime_audio_tools(agent_id)
            for tool in self._audio_tools:
                Runner.resource_mgr.add_tool(tool)
                tool_cards.append(tool.card)
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

        # generate_image tool: use dedicated image_gen model config
        self._image_gen_tool_registered = False
        if self._image_gen_model_config:
            try:
                Runner.resource_mgr.add_tool(generate_image)
                tool_cards.append(generate_image.card)
                self._image_gen_tool_registered = True
            except Exception as exc:
                logger.warning(
                    "[JiuWenClawDeepAdapter] generate_image tool registration failed: %s",
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
                query_collection,
                add_collection,
                delete_collection,
                save_media_to_gallery,
                save_file_to_file_manager,
                convert_timestamp_to_utc8_time,
                view_push_result,
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

        try:
            skill_toolkit = SkillToolkit(manager=self._skill_manager)
            skill_tool_names: list[str] = []
            for tool in skill_toolkit.get_tools():
                if not Runner.resource_mgr.get_tool(tool.card.id):
                    Runner.resource_mgr.add_tool(tool)
                tool_cards.append(tool.card)
                skill_tool_names.append(tool.card.name)
            logger.info(
                "[JiuWenClawDeepAdapter] SkillToolkit registered: tools=%s",
                skill_tool_names,
            )
        except Exception as exc:
            logger.warning("[JiuWenClawDeepAdapter] skill tools registration failed: %s", exc)

        return tool_cards

    def _build_cron_tools(self) -> list[Any]:
        """Build cron tools from the shared runtime bridge."""
        agent_id = self._instance.card.id if self._instance else None
        return self._cron_runtime.build_tools(
            context=self._runtime_cron_tool_context,
            agent_id=agent_id,
            language=self._resolve_runtime_language(),
        )

    async def _proc_context_compaction(self) -> None:
        """Backward-compatible no-op hook for tests and legacy call sites."""
        return None

    async def create_instance(
        self, config: dict[str, Any] | None = None, *, mode: str = "agent.plan", sub_mode: str = None
    ) -> None:
        """初始化 DeepAgent 实例.

        Args:
            config: 可选配置，支持以下字段：
                - agent_name: Agent 名称，默认 "main_agent"。
                - workspace_dir: 工作区目录，默认 "workspace/agent"。
                - 其余字段透传给 DeepAgentConfig。
            mode: 实例化模式，默认 "agent.plan"，使用 create_deep_agent。
            sub_mode: 子模式
        """
        await self.set_checkpoint()

        self._instance_overrides = dict(config or {}) if isinstance(config, dict) else {}
        load_dotenv(dotenv_path=get_env_file(), override=True)
        config_base = get_config()
        self._refresh_multimodal_configs(config_base)
        config = config_base.get("react", {}).copy()
        self._config_cache = config.copy()
        self._agent_name = self._instance_overrides.get(
            "agent_name", config.get("agent_name", "main_agent")
        )
        self._workspace_dir = config.get("workspace_dir", str(get_agent_workspace_dir()))

        model = self._create_model(config_base)
        await self._try_init_a2x_client(config_base)
        agent_card = AgentCard(name=self._agent_name, id='jiuwenclaw')

        tool_cards = await self._get_tool_cards(agent_card.id)
        self._tool_cards = tool_cards

        # 权限护栏由 openjiuwen PermissionInterruptRail + ToolPermissionHost 接管；
        # 无需初始化 jiuwenclaw 内置 PermissionEngine（已弃用）。

        rails_list = self._build_agent_rails(config, config_base, mode=mode)

        sys_operation = self._create_sys_operation()
        if sys_operation is None:
            raise RuntimeError("sys_operation is not available, maybe task is not running")

        self._sys_operation = sys_operation
        configured_subagents, should_add_general_agent = self._build_configured_subagents(model, config, config_base)
        should_enable_general_agent = should_add_general_agent and (
            sub_mode == "plan" or (isinstance(mode, str) and mode.startswith("agent"))
        )
        common_kwargs = dict(
            model=model,
            card=agent_card,
            system_prompt=build_identity_prompt(
                mode="agent.fast",
                language=self._resolve_prompt_language(),
                channel=(
                    "acp"
                    if self._is_acp_tool_profile(self._instance_overrides)
                    else self._resolve_prompt_channel()
                ),
            ),
            tools=tool_cards if tool_cards else [],
            subagents=configured_subagents,
            rails=rails_list if rails_list else [],
            enable_task_loop=self._resolve_enable_task_loop(config, config_base),
            add_general_purpose_agent=should_enable_general_agent,
            max_iterations=config.get("max_iterations", 15),
            workspace=Workspace(
                root_path=self._workspace_dir or "./",
                language=self._resolve_runtime_language(),
            ),
            sys_operation=sys_operation,
            language=self._resolve_runtime_language(),
            auto_create_workspace=False
        )

        self._instance = create_deep_agent(
            **common_kwargs,
            context_engine_config=_deep_agent_context_engine_config(config),
            vision_model_config=self._vision_model_config,
            audio_model_config=self._audio_model_config,
            completion_timeout=config.get("completion_timeout", 3600.0),
        )

        await self._instance.ensure_initialized()
        project_dir = self._instance_overrides.get("project_dir")
        if project_dir:
            set_cwd(project_dir)

        self._sync_a2x_runtime_state()
        self._registered_mcp_server_ids.clear()
        self._registered_mcp_servers.clear()
        await self._register_mcp_servers_from_config(config_base, tag=f"agent.{mode}")
        logger.info(
            "[JiuWenClawDeepAdapter] 初始化完成: agent_name=%s, mode=%s, sub_mode=%s", self._agent_name, mode, sub_mode
        )

        # 动态加载用户自定义的 Rail 扩展
        await self.load_user_rails()

    async def load_user_rails(self) -> None:
        """动态加载用户自定义的 Rail 扩展."""
        try:
            manager = get_rail_manager()

            # 设置 agent 实例到 rail_manager，用于热更新
            manager.set_agent_instance(self._instance)

            extensions = manager.get_extensions()

            # 只加载配置中启用的 rail 扩展
            for ext in extensions:
                if ext["enabled"]:
                    try:
                        await manager.hot_reload_rail(ext["name"], True)
                    except Exception as e:
                        logger.error(
                            "[JiuWenClawDeepAdapter] 用户 Rail 扩展加载失败: %s, 错误: %s",
                            ext["name"],
                            e,
                        )
        except Exception as e:
            logger.error("[JiuWenClawDeepAdapter] 加载用户 Rail 扩展时发生错误: %s", e)

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
        config = config_base.get("react", {}).copy()
        self._config_cache = config.copy()

        model = self._create_model(config_base)
        await self._try_init_a2x_client(config_base, reload=True)
        self._sync_a2x_runtime_state()
        self._agent_name = self._instance_overrides.get("agent_name", config.get("agent_name", "main_agent"))
        agent_card = AgentCard(name=self._agent_name, id='jiuwenclaw')
        self._sync_multimodal_tools_for_runtime()
        self._sync_paid_search_tool_for_runtime()

        if not self._filesystem_rail_enabled_for_profile() and self._filesystem_rail is not None:
            try:
                await self._instance.unregister_rail(self._filesystem_rail)
            except Exception as exc:
                logger.warning(
                    "[JiuWenClawDeepAdapter] ACP filesystem rail unregister failed: %s", exc
                )
            self._filesystem_rail = None

        rails_list = self._get_current_agent_rails(config, config_base)

        # 加载用户自定义的 Rail 扩展
        await self.load_user_rails()

        deep_cfg = self._make_deep_agent_config(
            model=model,
            config=config,
            agent_card=agent_card,
            tool_cards=self._tool_cards if self._tool_cards else [],
            rails=rails_list,
        )
        self._instance.configure(deep_cfg)
        await self._sync_mcp_servers_for_runtime(config_base, tag="agent.reload")

        logger.info("[JiuWenClawDeepAdapter] 配置已热更新（configure），未重启进程")

    @staticmethod
    def _bind_runtime_cron_context(
        *,
        channel_id: str | None,
        session_id: str | None,
        metadata: dict[str, Any] | None,
        request_id: str | None,
        mode: str | None,
    ) -> tuple[Token[str], Token[str | None], Token[dict[str, Any] | None], Token[str | None]]:
        normalized_channel = str(channel_id or "").strip() or CronTargetChannel.WEB.value
        normalized_mode = str(mode).strip() if isinstance(mode, str) and mode.strip() else None
        normalized_metadata = dict(metadata) if isinstance(metadata, dict) else None
        if normalized_metadata is None:
            normalized_metadata = {}
        if isinstance(request_id, str) and request_id.strip():
            normalized_metadata["request_id"] = request_id.strip()
        return (
            _CRON_TOOL_CHANNEL_ID.set(normalized_channel),
            _CRON_TOOL_SESSION_ID.set(session_id),
            _CRON_TOOL_METADATA.set(normalized_metadata),
            _CRON_TOOL_MODE.set(normalized_mode),
        )

    @staticmethod
    def _reset_runtime_cron_context(
        tokens: tuple[
            Token[str], Token[str | None], Token[dict[str, Any] | None], Token[str | None]
        ],
    ) -> None:
        channel_token, session_token, metadata_token, mode_token = tokens
        _CRON_TOOL_MODE.reset(mode_token)
        _CRON_TOOL_METADATA.reset(metadata_token)
        _CRON_TOOL_SESSION_ID.reset(session_token)
        _CRON_TOOL_CHANNEL_ID.reset(channel_token)

    async def _update_rails_for_mode(self, mode: str) -> None:
        """按 mode 注册或卸载 rails。"""
        if mode == "agent.plan":
            await self._update_plan_mode_rails()
        else:
            await self._update_agent_mode_rails(mode)  # 透传 mode

    async def _update_plan_mode_rails(self) -> None:
        """plan 模式：注册 plan 专属 rails，卸载 agent 专属资源。"""
        if self._task_planning_rail is None:
            self._task_planning_rail = self._build_task_planning_rail()
            if self._task_planning_rail is not None:
                await self._instance.register_rail(self._task_planning_rail)
                logger.info("[JiuWenClawDeepAdapter] TaskPlanningRail registered for plan mode")
        # 卸载 multi-session 工具
        for existing in list(self._instance.ability_manager.list() or []):
            if getattr(existing, "name", "").startswith(
                ("session_new", "session_cancel", "session_list")
            ):
                self._instance.ability_manager.remove(existing.name)
        # plan 模式，根据config选择是否注册或者卸载memory rail
        await self._handle_memory_rail_by_config("plan")
        # 外接记忆 rail（mode-independent，注册一次，跨 reload 持久）
        await self._handle_external_memory_rail_by_config()
        # 上下文 rail（仅 plan 模式）
        context_enabled = self._config_cache.get("context_engine_config", {}).get("enabled", False)

        if self._context_assemble_rail is None or self._context_assemble_mode != "agent.plan":
            if self._context_assemble_rail is not None:
                await self._instance.unregister_rail(self._context_assemble_rail)
                self._context_assemble_rail = None
            self._context_assemble_rail = _build_context_assemble_rail()
            self._context_assemble_mode = "agent.plan"
            await self._instance.register_rail(self._context_assemble_rail)
            logger.info(
                "[JiuWenClawDeepAdapter] %s registered for plan mode", "ContextAssembleRail"
            )

        # ContextProcessorRail
        if context_enabled:
            if self._context_processor_rail is None:
                self._context_processor_rail = _build_context_processor_rail(self._config_cache)
                if self._context_processor_rail is not None:
                    await self._instance.register_rail(self._context_processor_rail)
                    logger.info(
                        "[JiuWenClawDeepAdapter] ContextProcessorRail registered for plan mode"
                    )
        else:
            if self._context_processor_rail is not None:
                await self._instance.unregister_rail(self._context_processor_rail)
                self._context_processor_rail = None
                logger.info(
                    "[JiuWenClawDeepAdapter] ContextProcessorRail unregistered for plan mode (disabled)"
                )

        # SkillEvolutionRail
        evolution_enabled = self._config_cache.get("evolution", {}).get("enabled", False)
        if evolution_enabled:
            if self._skill_evolution_rail is None:
                self._skill_evolution_rail = self._build_skill_evolution_rail(self._config_cache)
            if self._skill_evolution_rail is not None:
                await self._instance.register_rail(self._skill_evolution_rail)
                logger.info("[JiuWenClawDeepAdapter] SkillEvolutionRail registered for plan mode")
        else:
            # evolution disabled: unregister if exists
            if self._skill_evolution_rail is not None:
                await self._instance.unregister_rail(self._skill_evolution_rail)
                self._skill_evolution_rail = None
                logger.info("[JiuWenClawDeepAdapter] SkillEvolutionRail unregistered (evolution.enabled=false)")

        # SkillCreateRail
        skill_create_enabled = _get_skill_create_enabled(self._config_cache)
        if skill_create_enabled:
            # Warn if task_loop is disabled
            deep_config = getattr(self._instance, "deep_config", None) if self._instance else None
            if deep_config is not None:
                if not deep_config.enable_task_loop:
                    logger.warning(
                        "[JiuWenClawDeepAdapter] skill_create=true requires task_loop mode, "
                        "but enable_task_loop=False. SkillCreateRail may not function properly."
                    )
            if self._skill_create_rail is None:
                self._skill_create_rail = self._build_skill_create_rail(self._config_cache)
            if self._skill_create_rail is not None:
                await self._instance.register_rail(self._skill_create_rail)
                logger.info("[JiuWenClawDeepAdapter] SkillCreateRail registered for plan mode")
        else:
            # skill_create disabled: unregister if exists
            if self._skill_create_rail is not None:
                await self._instance.unregister_rail(self._skill_create_rail)
                self._skill_create_rail = None
                logger.info("[JiuWenClawDeepAdapter] SkillCreateRail unregistered (skill_create=false)")

        # 注册 subagent rail（plan 模式下启用）
        if self._subagent_rail is None:
            self._subagent_rail = self._build_subagent_rail()
            if self._subagent_rail is not None:
                await self._instance.register_rail(self._subagent_rail)
                logger.info("[JiuWenClawDeepAdapter] SubagentRail registered for plan mode")

    async def _update_agent_mode_rails(self, mode: str | None = None) -> None:
        """agent 模式：卸载 plan 专属 rails，按需注册 agent 专属 rails。"""
        # 卸载 plan 专属 rails
        rail_specs = (
            ("_task_planning_rail", "TaskPlanningRail"),
            ("_skill_evolution_rail", "SkillEvolutionRail"),
            ("_skill_create_rail", "SkillCreateRail"),
            ("_subagent_rail", "SubagentRail"),
        )

        for attr, label in rail_specs:
            rail = getattr(self, attr)
            if rail is not None:
                await self._instance.unregister_rail(rail)
                setattr(self, attr, None)
                logger.info(
                    "[JiuWenClawDeepAdapter] %s unregistered for %s mode",
                    label,
                    mode or "agent",
                )

        # agent 模式，根据 config 选择是否注册或者卸载 memory rail
        await self._handle_memory_rail_by_config("fast")
        # 外接记忆 rail（mode-independent，注册一次，跨 reload 持久）
        await self._handle_external_memory_rail_by_config()
        # agent/智能模式：恢复上下文 rail（仅配置启用时）
        if self._context_assemble_rail is None or self._context_assemble_mode == "agent.plan":
            if self._context_assemble_rail is not None:
                await self._instance.unregister_rail(self._context_assemble_rail)
                self._context_assemble_rail = None
            self._context_assemble_rail = _build_context_assemble_rail()
            self._context_assemble_mode = "agent.fast"
            await self._instance.register_rail(self._context_assemble_rail)

        if self._context_processor_rail is None:
            self._context_processor_rail = _build_context_processor_rail(self._config_cache)
            if self._context_processor_rail is not None:
                await self._instance.register_rail(self._context_processor_rail)
                logger.info(
                    "[JiuWenClawDeepAdapter] ContextProcessorRail registered for %s mode",
                    mode or "agent.fast",
                )

    @staticmethod
    def _acp_runtime_tools_enabled(
        request_metadata: dict[str, Any] | None,
    ) -> tuple[bool, bool]:
        caps = (
            dict(request_metadata.get("acp_client_capabilities") or {})
            if isinstance(request_metadata, dict)
            else {}
        )
        logger.info(
            "[ACP] _acp_runtime_tools_enabled: metadata_keys=%s caps=%s",
            list((request_metadata or {}).keys()),
            caps,
        )

        fs_raw = caps.get("fs")
        if fs_raw is True:
            fs_enabled = True
        elif isinstance(fs_raw, dict):
            fs_enabled = bool(fs_raw.get("readTextFile") or fs_raw.get("writeTextFile"))
        else:
            fs_enabled = False

        terminal_raw = caps.get("terminal")
        if terminal_raw is True:
            terminal_enabled = True
        elif isinstance(terminal_raw, dict):
            terminal_enabled = bool(
                terminal_raw.get("create")
                or terminal_raw.get("output")
                or terminal_raw.get("waitForExit")
                or terminal_raw.get("release")
            )
        else:
            terminal_enabled = False

        return fs_enabled, terminal_enabled

    async def _update_tools_for_mode(
        self, mode: str, session_id: str | None, request_id: str | None
    ) -> None:
        """按 mode 注册或卸载 multi-session 工具。"""
        if mode != "agent.fast":
            return
        if not (request_id and session_id and self._model_client_config is not None):
            return
        try:
            for existing in list(self._instance.ability_manager.list() or []):
                if getattr(existing, "name", "").startswith(
                    ("session_new", "session_cancel", "session_list")
                ):
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

    async def _update_session_tools(
        self,
        session_id: str | None,
        request_id: str | None,
        channel_id: str | None = None,
    ) -> None:
        """注册 cron 和 send_file 工具（与 mode 无关，每次请求刷新）。"""
        # 定时工具：按当前 session 的 channel 注册（contextvar 已由 _bind_runtime_cron_context 设置）
        if not (session_id.startswith("heartbeat") or session_id.startswith("cron")):
            try:
                cron_tools = self._build_cron_tools()
                if cron_tools:
                    logger.info(
                        "[JiuWenClawDeepAdapter] Registering %d cron tools", len(cron_tools)
                    )
                    for cron_tool in cron_tools:
                        if not Runner.resource_mgr.get_tool(cron_tool.card.id):
                            Runner.resource_mgr.add_tool(cron_tool)
                        self._instance.ability_manager.add(cron_tool.card)
                    logger.info("[JiuWenClawDeepAdapter] Cron tools registered successfully")
            except Exception as exc:
                logger.error("[JiuWenClawDeepAdapter] 定时工具注册失败: %s", exc)

        # send_file 工具：由 channels.<channel>.send_file_allowed 控制，每次请求重新注册
        # channel_id/metadata 由调用前的 _bind_runtime_cron_context 已写入 contextvar
        config_base = get_config()
        channel = (
            str(channel_id or self._resolve_prompt_channel(session_id) or "web").strip() or "web"
        )
        send_file_enabled = (
            config_base.get("channels", {}).get(channel, {}).get("send_file_allowed", False)
        )
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

    def _refresh_acp_runtime_tools(
        self,
        session_id: str | None,
        request_id: str | None,
        channel_id: str | None,
        request_metadata: dict[str, Any] | None,
    ) -> None:
        """Refresh ACP tools for the current request based on client capabilities."""
        acp_tool_names = (
            "read_text_file",
            "write_text_file",
            "create_terminal",
            "read_terminal_output",
            "wait_for_terminal_exit",
            "release_terminal",
        )
        if channel_id == "acp":
            for existing in list(self._instance.ability_manager.list() or []):
                if getattr(existing, "name", "") in _ACP_BLOCKED_DEFAULT_TOOL_NAMES:
                    self._instance.ability_manager.remove(existing.name)
        for existing in list(self._instance.ability_manager.list() or []):
            if getattr(existing, "name", "") in acp_tool_names:
                self._instance.ability_manager.remove(existing.name)

        fs_enabled, terminal_enabled = self._acp_runtime_tools_enabled(request_metadata)
        has_runtime_capability = fs_enabled or terminal_enabled
        can_register_acp_runtime_tools = self._should_register_acp_runtime_tools(
            channel_id=channel_id,
            request_id=request_id,
            session_id=session_id,
            has_runtime_capability=has_runtime_capability,
        )
        if can_register_acp_runtime_tools:
            for tool in get_acp_output_tools(session_id=session_id, request_id=request_id):
                if tool.card.name in {"read_text_file", "write_text_file"}:
                    if not fs_enabled:
                        continue
                elif not terminal_enabled:
                    continue
                Runner.resource_mgr.add_tool(tool)
                self._instance.ability_manager.add(tool.card)

        if channel_id == "acp":
            ability_names = sorted(self._collect_registered_ability_names())
            runtime_tool_candidates = (
                "read_text_file",
                "write_text_file",
                "create_terminal",
                "read_terminal_output",
                "wait_for_terminal_exit",
                "release_terminal",
            )
            acp_runtime_names = self._select_registered_runtime_tool_names(
                runtime_tool_candidates,
                ability_names,
            )
            logger.info(
                "[ACP] runtime tool snapshot: session_id=%s request_id=%s fs_enabled=%s terminal_enabled=%s "
                "acp_runtime_tools=%s ability_count=%d abilities=%s",
                session_id,
                request_id,
                fs_enabled,
                terminal_enabled,
                acp_runtime_names,
                len(ability_names),
                ability_names,
            )

    def _update_prompt_for_mode(self, mode: str, resolved_language: str) -> None:
        """同步 system_prompt_builder 的语言。"""
        if self._instance.system_prompt_builder is not None:
            self._instance.system_prompt_builder.language = resolved_language
        if self._instance.deep_config is not None:
            self._instance.deep_config.language = resolved_language

    @dataclass
    class _RuntimeConfig:
        """Per-request runtime config bundle for _update_runtime_config."""

        session_id: str | None = None
        mode: str = "agent.plan"
        request_id: str | None = None
        channel_id: str | None = None
        request_metadata: dict[str, Any] | None = None
        trusted_dirs: list[str] | None = None

    async def _update_runtime_config(self, runtime_config: "_RuntimeConfig") -> None:
        """Register per-request tools for current agent execution."""
        if self._instance is None:
            raise RuntimeError("JiuWenClawDeepAdapter 未初始化，请先调用 create_instance()")

        resolved_language = self._resolve_runtime_language()
        resolved_channel = (
            str(
                runtime_config.channel_id
                or self._resolve_prompt_channel(runtime_config.session_id)
                or "web"
            ).strip()
            or "web"
        )
        if self._runtime_prompt_rail:
            self._runtime_prompt_rail.set_language(resolved_language)
            self._runtime_prompt_rail.set_channel(resolved_channel)
            self._runtime_prompt_rail.set_trusted_dirs(runtime_config.trusted_dirs)
            self._runtime_prompt_rail.set_model_name(self._resolve_model_name())
            self._runtime_prompt_rail.set_mode(runtime_config.mode)
        self._write_runtime_state(
            mode=runtime_config.mode, language=resolved_language, channel=resolved_channel
        )

        await self._update_rails_for_mode(runtime_config.mode)
        await self._update_tools_for_mode(
            runtime_config.mode, runtime_config.session_id, runtime_config.request_id
        )
        await self._update_session_tools(
            runtime_config.session_id,
            runtime_config.request_id,
            channel_id=runtime_config.channel_id,
        )
        self._refresh_acp_runtime_tools(
            runtime_config.session_id,
            runtime_config.request_id,
            runtime_config.channel_id,
            runtime_config.request_metadata,
        )
        self._update_prompt_for_mode(runtime_config.mode, resolved_language)

        # user_todos 工具注册（工具只注册一次，channel_id 每次请求由 ContextVar 更新）
        try:
            from jiuwenclaw.agents.harness.common.tools.user_todo_tool import (
                get_decorated_tools as _get_user_todo_tools,
                set_global_workspace_dir as _set_user_todo_workspace,
                set_global_channel_id as _set_user_todo_channel_id,
            )

            _set_user_todo_workspace(self._workspace_dir)
            _set_user_todo_channel_id(_CRON_TOOL_CHANNEL_ID.get())
            for tool in _get_user_todo_tools():
                if not Runner.resource_mgr.get_tool(tool.card.id):
                    Runner.resource_mgr.add_tool(tool)
                self._instance.ability_manager.add(tool.card)
        except ImportError:
            pass

        # 处理两种场景的记忆工具移除：
        # 1. 群聊数字分身模式（group_digital_avatar=True + avatar_mode=True）：移除写入工具，但保留读取工具
        # 2. 记忆完全禁用（enable_memory=False + group_digital_avatar=True + avatar_mode=True）：移除所有记忆工具（读取和写入）
        perm_ctx = TOOL_PERMISSION_CONTEXT.get()
        if perm_ctx is not None:
            # 判断是否为群聊数字分身模式
            is_group_digital_avatar = perm_ctx.group_digital_avatar and perm_ctx.avatar_mode

            # 判断是否为记忆完全禁用（三个条件同时满足）
            should_disable_memory = (
                not perm_ctx.enable_memory
                and perm_ctx.group_digital_avatar
                and perm_ctx.avatar_mode
            )

            # 场景2：记忆完全禁用 - 移除所有记忆工具
            if should_disable_memory:
                _all_memory_tools = (
                    "write_memory",
                    "edit_memory",
                    "read_memory",
                    "memory_search",
                    "memory_get",
                )
                for tool_name in _all_memory_tools:
                    try:
                        self._instance.ability_manager.remove(tool_name)
                        logger.info("[JiuWenClawDeepAdapter] 记忆系统已禁用，移除 %s", tool_name)
                    except Exception:
                        pass
            # 场景1：群聊数字分身模式 - 只移除写入工具
            elif is_group_digital_avatar:
                for tool_name in ("write_memory", "edit_memory"):
                    try:
                        self._instance.ability_manager.remove(tool_name)
                        logger.info(
                            "[JiuWenClawDeepAdapter] 群聊模式下禁止写入记忆，移除 %s", tool_name
                        )
                    except Exception:
                        pass
            # 非群聊数字分身且记忆启用时，恢复写入工具
            else:
                try:
                    from openjiuwen.core.memory.lite.memory_tools import (
                        get_decorated_tools as _get_sdk_memory_tools,
                    )

                    for tool in _get_sdk_memory_tools():
                        name = getattr(getattr(tool, "card", None), "name", "")
                        if name in ("write_memory", "edit_memory"):
                            self._instance.ability_manager.add(tool.card)
                except ImportError:
                    pass

    @staticmethod
    def _should_register_acp_runtime_tools(
        channel_id: str | None,
        request_id: str | None,
        session_id: str | None,
        has_runtime_capability: bool,
    ) -> bool:
        if channel_id != "acp":
            return False
        if not request_id or not session_id:
            return False
        return has_runtime_capability

    async def cleanup(self) -> None:
        """Release adapter-owned external runtime resources."""
        await self._close_a2x_client()

    def _collect_registered_ability_names(self) -> set[str]:
        ability_names: set[str] = set()
        for card in self._instance.ability_manager.list() or []:
            ability_name = str(getattr(card, "name", "") or "").strip()
            if ability_name:
                ability_names.add(ability_name)
        return ability_names

    @staticmethod
    def _select_registered_runtime_tool_names(
        runtime_tool_candidates: tuple[str, ...],
        ability_names: set[str],
    ) -> list[str]:
        selected_names: list[str] = []
        for name in runtime_tool_candidates:
            if name in ability_names:
                selected_names.append(name)
        return selected_names

    async def process_interrupt(self, request: AgentRequest) -> AgentResponse:
        """处理 interrupt 请求.

        根据 intent 分流：
        - pause: 暂停循环（不取消任务）
        - resume: 恢复已暂停的循环
        - cancel: 为当前 session 生成取消结果与清理信息；真正停任务由 SessionManager 完成
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
        cancelled_tool_results = []

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
                # 收集被中断的工具执行信息，通知前端更新状态
                self._stream_event_rail.collect_cancelled_tool_updates(request.session_id)
                cancelled_tool_results = self._stream_event_rail.get_cancelled_tool_results()
                self._stream_event_rail.clear_cancelled_tool_results()
            # 2. 终止 DeepAgent 外层 task loop
            if self._instance is not None:
                await self._instance.abort()
            # 3. 取消 TaskScheduler 中正在运行的 asyncio.Tasks
            #    （协作式 abort 只能在 checkpoint 之间生效，无法打断 in-flight HTTP 调用）
            self._cancel_scheduler_running_tasks()
            # 4. 不清理 todo — 保留给新任务继续
            logger.info(
                "[JiuWenClawDeepAdapter] interrupt(supplement): 已停止执行 request_id=%s",
                request.request_id,
            )
            message = "任务已切换"

        else:
            # cancel（默认）：终止当前正在运行的 agent 任务 + 清理 todos。
            # 必须同时调用 rail.abort() 和 instance.abort()，否则流式模式下
            # DeepAgent 的 _run_task_loop_stream 后台 Task 不会停止
            # （stream_task.cancel() 只取消了 chunk 转发 Task，不影响 _stream_process）。
            # SessionManager.cancel_session_task 仅管理非流式队列 Task，对流式后台 Task 无效。
            if self._stream_event_rail is not None:
                self._stream_event_rail.abort()
                self._stream_event_rail.collect_cancelled_tool_updates(request.session_id)
                cancelled_tool_results = self._stream_event_rail.get_cancelled_tool_results()
                self._stream_event_rail.clear_cancelled_tool_results()
                self._stream_event_rail.reset_for_new_task()
                logger.info(
                    "[JiuWenClawDeepAdapter] interrupt(cancel): 已设置 abort 并解除 pause 阻塞"
                )
            if self._instance is not None:
                try:
                    await self._instance.abort()
                    logger.info(
                        "[JiuWenClawDeepAdapter] interrupt(cancel): 已终止 DeepAgent 任务循环"
                    )
                except Exception as exc:
                    logger.warning(
                        "[JiuWenClawDeepAdapter] interrupt(cancel): instance.abort 失败: %s",
                        exc,
                    )
                # 协作式 abort 只能在 checkpoint 之间生效，无法打断 in-flight LLM HTTP 调用。
                # 直接取消 TaskScheduler 中运行中的 asyncio.Task，注入 CancelledError 到 HTTP await 点。
                self._cancel_scheduler_running_tasks()

            updated_todos = None
            if request.session_id:
                try:
                    updated_todos = await self._cancel_pending_todos(request.session_id)
                except Exception as exc:
                    logger.warning("[JiuWenClawDeepAdapter] 标记 todo cancelled 失败: %s", exc)

                # Cancel auto_harness active run if exists
                try:
                    if self._auto_harness_service is not None \
                        and self._auto_harness_service.has_active_run(request.session_id):
                        self._auto_harness_service.cancel_session_run(request.session_id)
                        logger.info(
                            "[JiuWenClawDeepAdapter] interrupt(cancel): cancelled auto_harness run for session=%s",
                            request.session_id,
                        )
                except Exception as exc:
                    logger.warning(
                        "[JiuWenClawDeepAdapter] Failed to cancel auto_harness run: %s",
                        exc,
                    )

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

        # cancel 后附带被中断的工具执行结果，通知前端更新状态
        if cancelled_tool_results:
            payload["cancelled_tools"] = cancelled_tool_results
            # 写入历史记录，确保刷新网页后工具状态正确显示
            for tool_info in cancelled_tool_results:
                append_history_record(
                    session_id=request.session_id,
                    request_id=request.request_id,
                    channel_id=request.channel_id,
                    role="assistant",
                    event_type="chat.tool_result",
                    content=tool_info.get("result", ""),
                    timestamp=time.time(),
                    extra={
                        "tool_result": {
                            "tool_name": tool_info.get("tool_name", ""),
                            "tool_call_id": tool_info.get("tool_call_id", ""),
                            "result": tool_info.get("result", ""),
                            "status": tool_info.get("status", "error"),
                        },
                    },
                    mode=request.params.get("mode", "unknown") if isinstance(request.params, dict) else "unknown",
                )

        return AgentResponse(
            request_id=request.request_id,
            channel_id=request.channel_id,
            ok=True,
            payload=payload,
            metadata=request.metadata,
        )

    def _cancel_scheduler_running_tasks(self) -> None:
        """Cancel in-flight asyncio.Tasks in the Controller's TaskScheduler.

        Cooperative abort (rail.abort + instance.abort) only stops at checkpoints,
        but in-flight LLM HTTP requests need CancelledError injected directly
        at the await point to abort immediately.
        """
        try:
            controller = getattr(self._instance, '_loop_controller', None)
            if controller is None:
                return
            scheduler = getattr(controller, '_task_scheduler', None)
            if scheduler is None:
                return
            running = getattr(scheduler, '_running_tasks', None)
            if not running:
                return
            cancelled_count = 0
            for _task_id, (_executor, exec_task) in list(running.items()):
                if exec_task is not None and not exec_task.done():
                    exec_task.cancel()
                    cancelled_count += 1
            if cancelled_count > 0:
                logger.info(
                    "[JiuWenClawDeepAdapter] interrupt: 已取消 %d 个 TaskScheduler 运行中的任务",
                    cancelled_count,
                )
        except Exception as exc:
            logger.warning(
                "[JiuWenClawDeepAdapter] _cancel_scheduler_running_tasks 失败: %s",
                exc,
            )

    async def abort_on_gateway_disconnect(self) -> None:
        """Gateway 与 AgentServer 的 WebSocket 断开时：与 interrupt(cancel) 同样中止 rail 与 DeepAgent 实例。"""
        if self._stream_event_rail is not None:
            self._stream_event_rail.abort()
        if self._instance is not None:
            try:
                await self._instance.abort()
            except Exception as exc:
                logger.warning(
                    "[JiuWenClawDeepAdapter] abort_on_gateway_disconnect instance.abort failed: %s",
                    exc,
                )

    def _has_valid_model_config(self, requested_model_name: str = "") -> bool:
        """检查是否有有效的模型配置。

        优先检查请求中实际要用的模型（requested_model_name），其次检查默认模型，
        最后从 config.yaml 重新解析。与 _create_model 同源，不独立读取环境变量。
        """
        def _mcc_obj_looks_usable(mcc_obj: Any) -> bool:
            if not isinstance(mcc_obj, ModelClientConfig):
                return False
            return _mcc_looks_usable({
                "api_key": mcc_obj.api_key,
                "api_base": getattr(mcc_obj, "api_base", None),
            })

        # 优先检查请求中指定的模型（如用户在 UI 切换了模型）
        if requested_model_name and requested_model_name in self._model_cache:
            m = self._model_cache[requested_model_name]
            if _mcc_obj_looks_usable(getattr(m, "model_client_config", None)):
                return True

        # 检查默认模型
        if self._model is not None:
            if _mcc_obj_looks_usable(getattr(self._model, "model_client_config", None)):
                return True

        # 回退：检查 cache 中是否有任意一个有效模型
        for m in self._model_cache.values():
            if _mcc_obj_looks_usable(getattr(m, "model_client_config", None)):
                return True

        try:
            mcc = get_config().get("models", {}).get("default", {}).get("model_client_config", {})
            if isinstance(mcc, dict) and _mcc_looks_usable(mcc):
                return True
        except Exception as e:
            logger.warning("[JiuWenClawDeepAdapter] _has_valid_model_config config read failed: %s", e)

        return False

    async def handle_user_answer(self, request: AgentRequest) -> AgentResponse:
        """Handle chat.user_answer request, route user answer to evolution approval Future."""
        request_id = (
            request.params.get("request_id", "") if isinstance(request.params, dict) else ""
        )
        answers = request.params.get("answers", []) if isinstance(request.params, dict) else []
        session_id = request.session_id
        resolved = False
        if request_id.startswith("team_skill_evolve_"):
            resolved = await self._handle_team_skill_evolve_approval(
                request_id,
                answers,
                session_id,
                request.channel_id,
            )
        elif request_id.startswith("evolve_simplify_"):
            resolved = await self._handle_governance_approval(request_id, answers, "simplify")
        elif request_id.startswith("skill_evolve_"):
            resolved = await self._handle_evolution_approval(request_id, answers)

        return AgentResponse(
            request_id=request.request_id,
            channel_id=request.channel_id,
            ok=True,
            payload={"accepted": True, "resolved": resolved},
            metadata=request.metadata,
        )

    async def handle_heartbeat(self, request: AgentRequest) -> AgentResponse | None:
        """Handle heartbeat request. Returns None to continue normal flow.

        Injects a heartbeat prompt into the query to ensure the LLM receives
        a non-empty user message. Reading HEARTBEAT.md and injecting its content
        into the system prompt is handled by HeartbeatRail in before_model_call.
        """
        sid = str(request.session_id or "")
        if not sid.startswith("heartbeat"):
            return None

        request.params["query"] = "这是一次心跳请求任务"
        logger.info(
            "[JiuWenClawDeepAdapter] heartbeat query injected:" " request_id=%s session_id=%s",
            request.request_id,
            request.session_id,
        )
        return None

    async def _handle_evolution_approval(self, request_id: str, answers: list) -> bool:
        """Handle evolution approval via SkillEvolutionRail.on_approve/on_reject.

        Uses the optimizer path: calls rail.on_approve() for accepted records
        which will flush to store and solidify, or rail.on_reject() to discard.
        """
        rail = self._skill_evolution_rail
        if rail is None:
            logger.warning("[JiuWenClaw] evolution approval failed: no SkillEvolutionRail")
            return False

        # Determine if user accepted (any answer contains "接收")
        accepted = any(
            isinstance(ans, dict) and "接收" in ans.get("selected_options", []) for ans in answers
        )

        if accepted:
            await rail.on_approve(request_id)
            logger.info("[JiuWenClaw] evolution approval accepted: request_id=%s", request_id)
        else:
            await rail.on_reject(request_id)
            logger.info("[JiuWenClaw] evolution approval rejected: request_id=%s", request_id)

        return True

    # ------------------------------------------------------------------
    # Team Skill approval handlers
    # ------------------------------------------------------------------

    @staticmethod
    def _find_team_skill_rail(request_id: str, channel_id: str | None = None):
        """Find TeamSkillRail that owns the given pending request_id."""
        try:
            from jiuwenclaw.agents.harness.team import (
                find_team_skill_rail_across_managers,
                get_team_manager,
            )
            rail = get_team_manager(channel_id).find_team_skill_rail_for_request(request_id)
            if rail is not None:
                return rail
            return find_team_skill_rail_across_managers(request_id)
        except Exception:
            return None

    @staticmethod
    def _option_matches(answers: list, accept_labels: tuple[str, ...]) -> bool:
        """Check whether any answer's selected_options matches accept labels.

        Tolerates both Chinese and English labels (case-insensitive) so the
        agent-core layer can keep neutral English labels while jiuwenclaw UI
        may localize them.
        """
        normalized_accept = {s.strip().lower() for s in accept_labels}
        for ans in answers:
            if not isinstance(ans, dict):
                continue
            for opt in ans.get("selected_options", []) or []:
                if isinstance(opt, str) and opt.strip().lower() in normalized_accept:
                    return True
        return False

    async def _handle_team_skill_evolve_approval(
        self,
        request_id: str,
        answers: list,
        session_id: str | None = None,
        channel_id: str | None = None,
    ) -> bool:
        rail = self._find_team_skill_rail(request_id, channel_id)
        if rail is None:
            logger.warning("[JiuWenClaw] team skill evolve approval failed: no TeamSkillRail")
            return False

        accepted = self._option_matches(answers, ("accept", "接收", "接受"))

        logger.info(
            "[JiuWenClaw] team skill evolve approval: request_id=%s, answers=%s, accepted=%s",
            request_id, answers, accepted,
        )

        if accepted:
            await rail.on_approve_patch(request_id)
            # Sync updated team skill from workspace to global team_skills dir.
            try:
                from jiuwenclaw.agents.harness.team import sync_team_skills_across_managers
                if session_id:
                    sync_team_skills_across_managers(session_id)
            except Exception as exc:
                logger.warning("[JiuWenClaw] team skill sync after patch failed: %s", exc)
            logger.info("[JiuWenClaw] team skill evolve accepted: request_id=%s", request_id)
        else:
            await rail.on_reject_patch(request_id)
            logger.info("[JiuWenClaw] team skill evolve rejected: request_id=%s", request_id)

        return True

    # ------------------------------------------------------------------
    # /evolve, /evolve_list, /evolve_simplify & /solidify command handlers
    # ------------------------------------------------------------------

    async def _handle_evolve_command(self, query: str, session_id: str) -> dict[str, Any]:
        """/evolve [list | <skill_name> [<user_query>...]] handler using the optimizer path.

        Uses SkillEvolutionRail.generate_and_emit_experience to stage records
        in memory and emit approval events.

        Args:
            query: Command query, format: /evolve <skill_name> [<user_query>...]
            session_id: Current session ID for message collection

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

        # --- /evolve <skill_name> [<user_query>...] ---
        # Parse skill_name and optional user_query
        skill_parts = skill_arg.split(maxsplit=1)
        skill_name = skill_parts[0].strip()
        user_query = skill_parts[1].strip() if len(skill_parts) > 1 else ""

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

        # 2) Detect signals (reuse rail's dedup set)
        existing_skills = {n for n in skill_names if store.skill_exists(n)}
        detector = SignalDetector(existing_skills=existing_skills)
        detected = detector.detect(parsed_messages) if parsed_messages else []

        new_signals = [
            sig
            for sig in detected
            if (sig.signal_type, sig.excerpt[:100]) not in rail.processed_signal_keys
        ]
        for sig in new_signals:
            rail.processed_signal_keys.add((sig.signal_type, sig.excerpt[:100]))

        attributed = [s for s in new_signals if s.skill_name == skill_name]

        # If no detected signals and no user_query, nothing to evolve
        if not attributed and not user_query:
            return {
                "output": "当前对话未发现明确的演进信号（无工具执行失败、无用户纠正）。\n",
                "result_type": "answer",
            }

        # 3) Generate experience records and emit approval event
        # user_query is passed directly to generate_and_emit_experience which handles
        # synthetic signal and message creation internally.
        try:
            has_records = await rail.generate_and_emit_experience(
                skill_name, attributed, parsed_messages, user_query=user_query
            )
        except Exception as exc:
            logger.warning("[JiuWenClaw] evolve generate failed (skill=%s): %s", skill_name, exc)
            return {
                "output": f"演进经验生成失败：{exc}",
                "result_type": "error",
            }

        if not has_records:
            return {
                "output": "当前对话未发现明确的演进信号（无工具执行失败、无用户纠正）。\n",
                "result_type": "answer",
            }

        # 5) Drain the buffered approval event
        events = await rail.drain_pending_approval_events()
        if not events:
            return {
                "output": "演进经验生成失败：无法创建审批事件。",
                "result_type": "error",
            }

        # 6) Build response with approval chunks
        event = events[0]
        payload = event.payload or {}
        request_id = payload.get("request_id", "")
        questions = payload.get("questions", [])

        # Build summary from questions
        summaries = "\n".join(
            f"  {i + 1}. {q.get('question', '')[:200]}" for i, q in enumerate(questions)
        )

        return {
            "output": (
                f"已为 Skill '{skill_name}' 生成 {len(questions)} 条演进经验，请审批：\n"
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

    async def _handle_evolve_list_command(self, query: str) -> dict[str, Any]:
        """/evolve_list <skill_name> [--sort score] — show experiences with scores."""
        rail = self._skill_evolution_rail
        assert rail is not None
        store = rail.store

        parts = query.split()
        skill_name = parts[1] if len(parts) > 1 else ""
        if not skill_name or skill_name.startswith("--"):
            return {
                "output": "请指定 Skill 名称：`/evolve_list <skill_name>`",
                "result_type": "error",
            }

        if not store.skill_exists(skill_name):
            available = "、".join(store.list_skill_names()) or "（无可用 Skill）"
            return {
                "output": f"未找到 Skill '{skill_name}'。当前可用：{available}",
                "result_type": "error",
            }

        records = await store.get_records_by_score(skill_name)
        if not records:
            return {
                "output": f"Skill '{skill_name}' 暂无演进经验。",
                "result_type": "answer",
            }

        avg_score = sum(r.score for r in records) / len(records)

        lines = [
            f'📊 Skill "{skill_name}" — 经验库摘要\n',
            f"共 {len(records)} 条经验 | 平均分：{avg_score:.2f}\n",
            "| # | Score | Used | Effect | Section | Content (preview) |",
            "|---|---:|---|---|---|---|",
        ]
        for i, r in enumerate(records, 1):
            stats = r.usage_stats
            if stats:
                used_str = (
                    f"{stats.times_used}/{stats.times_presented}"
                    if stats.times_presented
                    else "0/0"
                )
                effect_str = f"+{stats.times_positive}/-{stats.times_negative}"
            else:
                used_str = "0/0"
                effect_str = "+0/-0"
            section = str(r.change.section).replace("|", "\\|")
            preview = r.change.content.split("\n")[0][:40].replace("|", "\\|")
            lines.append(
                f"| {i} | {r.score:.2f} | {used_str} | {effect_str} | {section} | {preview} |"
            )

        lines.append(f"\n提示：使用 /evolve_simplify {skill_name} 执行智能整理")
        return {
            "output": "\n".join(lines),
            "result_type": "answer",
        }

    async def _handle_evolve_simplify_command(self, query: str) -> dict[str, Any]:
        """/evolve_simplify <skill_name> [user_intent] — LLM-based experience cleanup with approval."""
        rail = self._skill_evolution_rail
        assert rail is not None
        store = rail.store

        parts = query.split(maxsplit=2)
        skill_name = parts[1] if len(parts) > 1 else ""
        user_intent = parts[2] if len(parts) > 2 else None

        if not skill_name:
            return {
                "output": "请指定 Skill 名称：`/evolve_simplify <skill_name> [user_intent]`",
                "result_type": "error",
            }

        if not store.skill_exists(skill_name):
            available = "、".join(store.list_skill_names()) or "（无可用 Skill）"
            return {
                "output": f"未找到 Skill '{skill_name}'。当前可用：{available}",
                "result_type": "error",
            }

        try:
            request_id = await rail.request_simplify(skill_name, user_intent)
        except Exception as exc:
            logger.warning("[JiuWenClaw] evolve_simplify failed: %s", exc)
            return {"output": f"智能整理分析失败：{exc}", "result_type": "error"}

        if not request_id:
            return {
                "output": f"Skill '{skill_name}' 经验库状态良好，无需整理。",
                "result_type": "answer",
            }

        approval_chunks = await rail.drain_pending_approval_events()
        return {
            "output": f"Skill '{skill_name}' 精简方案已生成，请在审批弹框中确认。",
            "result_type": "answer",
            "approval_chunks": approval_chunks,
        }

    async def _handle_evolve_rebuild_command(self, query: str) -> dict[str, Any]:
        """/evolve_rebuild <skill_name> [user_intent] — Build followup prompt for rebuild."""
        rail = self._skill_evolution_rail
        assert rail is not None
        store = rail.store

        parts = query.split(maxsplit=2)
        skill_name = parts[1] if len(parts) > 1 else ""
        user_intent = parts[2] if len(parts) > 2 else None

        if not skill_name:
            return {
                "output": "请指定 Skill 名称：`/evolve_rebuild <skill_name> [user_intent]`",
                "result_type": "error",
            }

        if not store.skill_exists(skill_name):
            available = "、".join(store.list_skill_names()) or "（无可用 Skill）"
            return {
                "output": f"未找到 Skill '{skill_name}'。当前可用：{available}",
                "result_type": "error",
            }

        try:
            followup_prompt = await rail.request_rebuild(skill_name, user_intent)
        except Exception as exc:
            logger.warning("[JiuWenClaw] evolve_rebuild failed: %s", exc)
            return {"output": f"重建分析失败：{exc}", "result_type": "error"}

        if not followup_prompt:
            return {
                "output": f"Skill '{skill_name}' 未生成可执行的重建指令。",
                "result_type": "error",
            }

        return {
            "action": "run_rebuild_followup",
            "followup_prompt": followup_prompt,
            "skill_name": skill_name,
            "result_type": "followup",
        }

    async def _handle_evolve_rollback_command(self, query: str) -> dict[str, Any]:
        """/evolve_rollback <skill_name> [version] — Rollback skill to archived version."""
        rail = self._skill_evolution_rail
        assert rail is not None
        store = rail.store

        parts = query.split(maxsplit=2)
        skill_name = parts[1] if len(parts) > 1 else ""
        version = parts[2].strip() if len(parts) > 2 else None

        if not skill_name:
            archives_hint = ""
            for name in store.list_skill_names():
                archives = store.list_archives(name)
                if archives:
                    body_versions = [a for a in archives if a.startswith("SKILL.v")]
                    archives_hint += f"\n  - **{name}**: {len(body_versions)} 个版本"
            return {
                "output": (
                    "请指定 Skill 名称：`/evolve_rollback <skill_name> [version]`"
                    + (f"\n\n可回滚的 Skill：{archives_hint}" if archives_hint else "")
                ),
                "result_type": "error",
            }

        if not store.skill_exists(skill_name):
            available = "、".join(store.list_skill_names()) or "（无可用 Skill）"
            return {
                "output": f"未找到 Skill '{skill_name}'。当前可用：{available}",
                "result_type": "error",
            }

        archives = store.list_archives(skill_name)
        body_versions = [a for a in archives if a.startswith("SKILL.v")]
        if not body_versions:
            return {
                "output": f"Skill '{skill_name}' 没有归档版本可回滚。",
                "result_type": "error",
            }

        # No version specified → list available versions for user to pick
        if not version:
            lines = [f"**Skill '{skill_name}' 可用归档版本（最新在前）：**\n"]
            for i, v in enumerate(body_versions):
                ts = v.replace("SKILL.v", "").replace(".md", "")
                if len(ts) >= 15:
                    display_ts = f"{ts[:4]}-{ts[4:6]}-{ts[6:8]} {ts[9:11]}:{ts[11:13]}:{ts[13:15]} UTC"
                else:
                    display_ts = ts
                marker = " ← 最近" if i == 0 else ""
                lines.append(f"  {i+1}. `{v}` ({display_ts}){marker}")
            lines.append(f"\n用法：`/evolve_rollback {skill_name} SKILL.v<时间戳>.md`")
            lines.append(f"快捷回滚到最近版本：`/evolve_rollback {skill_name} latest`")
            return {"output": "\n".join(lines), "result_type": "answer"}

        # "latest" shorthand → pick newest
        if version == "latest":
            version = body_versions[0]

        if version not in body_versions:
            hint = "、".join(f"`{v}`" for v in body_versions[:5])
            return {
                "output": f"版本 `{version}` 不存在。可用版本：{hint}",
                "result_type": "error",
            }

        try:
            success = await rail.rollback_skill(skill_name, version)
        except Exception as exc:
            logger.warning("[JiuWenClaw] evolve_rollback failed: %s", exc)
            return {"output": f"回滚失败：{exc}", "result_type": "error"}

        if success:
            return {
                "output": (
                    f"Skill '{skill_name}' 已成功回滚到 `{version}`。\n\n"
                    f"（当前状态已自动归档，可再次回滚恢复。）"
                ),
                "result_type": "answer",
            }
        return {
            "output": f"Skill '{skill_name}' 回滚失败，请检查归档版本是否有效。",
            "result_type": "error",
        }

    async def _handle_governance_approval(
        self, request_id: str, answers: list, kind: str
    ) -> bool:
        """Unified handler for simplify governance approvals."""
        rail = self._skill_evolution_rail
        if rail is None:
            logger.warning("[JiuWenClaw] governance approval failed: no SkillEvolutionRail")
            return False

        accept_labels = {"执行"} if kind == "simplify" else set()
        accepted = any(
            isinstance(ans, dict)
            and bool(accept_labels & set(ans.get("selected_options", [])))
            for ans in answers
        )

        if kind == "simplify":
            if accepted:
                await rail.on_approve_simplify(request_id)
            else:
                await rail.on_reject_simplify(request_id)

        logger.info(
            "[JiuWenClaw] governance %s %s: request_id=%s",
            kind, "accepted" if accepted else "rejected", request_id,
        )
        return True

    @staticmethod
    def _extract_rebuild_followup_prompt(slash_result: dict[str, Any] | None) -> str | None:
        """Return followup prompt when slash_result requests rebuild continuation."""
        if not isinstance(slash_result, dict):
            return None
        if slash_result.get("action") != "run_rebuild_followup":
            return None
        prompt = slash_result.get("followup_prompt")
        if not isinstance(prompt, str):
            return None
        prompt = prompt.strip()
        return prompt or None

    def _ensure_evolution_rail_for_slash(self, mode: str) -> str | None:
        """Check evolution availability for slash commands; lazily init rail if needed.

        Returns None when the rail is (or becomes) available, or an error message string.
        """
        if mode != "agent.plan":
            return "agent 模式下演进功能不可用。"
        if not self._config_cache.get("evolution", {}).get("enabled", False):
            return "演进功能未启用。"
        if self._skill_evolution_rail is None:
            self._skill_evolution_rail = self._build_skill_evolution_rail(self._config_cache)
        if self._skill_evolution_rail is None:
            return "演进功能初始化失败。"

        # SkillCreateRail requires skill_create config
        if _get_skill_create_enabled(self._config_cache):
            if self._skill_create_rail is None:
                self._skill_create_rail = self._build_skill_create_rail(self._config_cache)
        return None

    async def _handle_slash_command(
        self,
        query: str,
        session_id: str = "default",
        mode: str = "agent.plan",
    ) -> dict[str, Any] | None:
        """Intercept slash commands before agent invocation.

        Returns result dict if handled, None to proceed normally.
        The dict may contain an ``approval_chunks`` list that the caller
        should forward to the frontend as separate stream events.
        """
        stripped = query.strip()

        if stripped.startswith("/evolve_rewrite"):
            return {
                "output": "`/evolve_rewrite` 已删除，请使用 `/evolve_rebuild <skill_name> [user_intent]`。",
                "result_type": "error",
            }

        if stripped.startswith("/evolve_simplify"):
            err = self._ensure_evolution_rail_for_slash(mode)
            if err:
                return {"output": err, "result_type": "error"}
            return await self._handle_evolve_simplify_command(stripped)

        if stripped.startswith("/evolve_rebuild"):
            err = self._ensure_evolution_rail_for_slash(mode)
            if err:
                return {"output": err, "result_type": "error"}
            return await self._handle_evolve_rebuild_command(stripped)

        if stripped.startswith("/evolve_rollback"):
            err = self._ensure_evolution_rail_for_slash(mode)
            if err:
                return {"output": err, "result_type": "error"}
            return await self._handle_evolve_rollback_command(stripped)

        if stripped.startswith("/evolve_list"):
            err = self._ensure_evolution_rail_for_slash(mode)
            if err:
                return {"output": err, "result_type": "error"}
            return await self._handle_evolve_list_command(stripped)

        if stripped == "/evolve" or stripped.startswith("/evolve "):
            err = self._ensure_evolution_rail_for_slash(mode)
            if err:
                return {"output": err, "result_type": "error"}
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
            deep_config = self._instance.deep_config
            modify_tool = TodoModifyTool(
                operation=deep_config.sys_operation,
                workspace=str(deep_config.workspace.get_node_path(WorkspaceNode.TODO)),
                language=self._resolve_runtime_language(),
            )

        try:
            todos = await modify_tool.load_todos(session_id)
            if not todos:
                return None

            _done_statuses = {
                TodoStatus.COMPLETED.value,
                TodoStatus.CANCELLED.value,
            }

            ids_to_cancel = []
            for todo in todos:
                if todo.status.value not in _done_statuses:
                    ids_to_cancel.append(todo.id)

            if ids_to_cancel:
                await modify_tool._cancel_todos(ids_to_cancel, todos)
                logger.info(
                    "[JiuWenClawDeepAdapter] 已将 session %s 的未完成任务标记为 cancelled",
                    session_id,
                )

            # 重新加载并返回前端格式的 todo 列表
            updated_todos = await modify_tool.load_todos(session_id)
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

        _req_model = (request.params.get("model_name") or "") if isinstance(request.params, dict) else ""
        if not self._has_valid_model_config(_req_model):
            return AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": "模型未正确配置，请先配置模型信息"},
                metadata=request.metadata,
            )

        session_id = request.session_id or "default"
        query = request.params.get("query", "")
        mode = request.params.get("mode", "agent.plan")

        slash_result = await self._handle_slash_command(query, session_id, mode)
        if slash_result is not None:
            followup_prompt = self._extract_rebuild_followup_prompt(slash_result)
            if followup_prompt is not None:
                inputs = dict(inputs)
                inputs["query"] = followup_prompt
            else:
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
            request_id=request.request_id,
            mode=mode,
        )
        token_cid = TOOL_PERMISSION_CHANNEL_ID.set((request.channel_id or "").strip())
        token_perm = setup_permission_context(request)
        # 按请求选择模型
        resolved_model = self._resolve_model_for_request(request)
        self._apply_model_to_react_agent(resolved_model)
        try:
            await self._update_runtime_config(
                self._RuntimeConfig(
                    session_id=request.session_id,
                    mode=mode,
                    request_id=request.request_id,
                    channel_id=request.channel_id,
                    request_metadata=request.metadata,
                    trusted_dirs=inputs.get("trusted_dirs"),
                )
            )
            result = await Runner.run_agent(agent=self._instance, inputs=inputs)
        except asyncio.CancelledError:
            logger.info(
                "[JiuWenClawDeepAdapter] Agent 任务被取消: request_id=%s session_id=%s",
                request.request_id,
                session_id,
            )
            raise
        except Exception as e:
            logger.error("[JiuWenClawDeepAdapter] Agent 任务执行异常: %s", e)
            raise
        finally:
            TOOL_PERMISSION_CHANNEL_ID.reset(token_cid)
            cleanup_permission_context(token_perm)
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

        _req_model = (request.params.get("model_name") or "") if isinstance(request.params, dict) else ""
        if not self._has_valid_model_config(_req_model):
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
        mode = request.params.get("mode", "agent.plan")

        # Team 模式处理
        if mode in ("team", "code.team"):
            from jiuwenclaw.server.runtime.agent_adapter.team_helpers import process_team_message_stream

            resolved_model = self._resolve_model_for_request(request)
            self._apply_model_to_react_agent(resolved_model)
            resolved_language = self._resolve_runtime_language()
            resolved_channel = str(cid or self._resolve_prompt_channel(session_id) or "web").strip() or "web"
            if self._runtime_prompt_rail:
                self._runtime_prompt_rail.set_model_name(self._resolve_model_name())
                self._runtime_prompt_rail.set_mode(mode)
            self._write_runtime_state(mode=mode, language=resolved_language, channel=resolved_channel)

            async for chunk in process_team_message_stream(request, inputs, self._instance):
                yield chunk
            return

        # Auto-Harness 模式处理
        if mode == "auto_harness":
            if self._auto_harness_service is None:
                self._auto_harness_service = AutoHarnessService(
                    self._stream_event_rail,
                    agent=self._instance,
                )

            activate_response = request.params.get("activate_response")
            if isinstance(activate_response, dict):
                async for chunk in self._auto_harness_service.resume_activate(
                    session_id, rid, cid, activate_response
                ):
                    yield chunk
                return

            resolved_model = self._resolve_model_for_request(request)
            if self._auto_harness_service.is_activate_only_request(request, query):
                async for chunk in self._auto_harness_service.run_activate_only(
                    request, session_id, rid, query, model=resolved_model
                ):
                    yield chunk
                return
            if self._auto_harness_service.is_implement_only_request(request, query):
                async for chunk in self._auto_harness_service.run_implement_only(
                    request, session_id, rid, query, model=resolved_model
                ):
                    yield chunk
                return

            async for chunk in self._auto_harness_service.run(
                request, session_id, rid, query, model=resolved_model
            ):
                yield chunk
            return

        # 拦截斜杠命令
        slash_result = await self._handle_slash_command(query, session_id, mode)
        if slash_result is not None:
            followup_prompt = self._extract_rebuild_followup_prompt(slash_result)
            if followup_prompt is not None:
                inputs = dict(inputs)
                inputs["query"] = followup_prompt
            else:
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
        usage_accumulator = {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "input_cost": 0.0,
            "output_cost": 0.0,
            "total_cost": 0.0,
        }

        cron_context_tokens = self._bind_runtime_cron_context(
            channel_id=request.channel_id,
            session_id=request.session_id,
            metadata=request.metadata,
            request_id=request.request_id,
            mode=mode,
        )
        token_cid = TOOL_PERMISSION_CHANNEL_ID.set((request.channel_id or "").strip())
        token_perm = setup_permission_context(request)
        # 按请求选择模型
        resolved_model = self._resolve_model_for_request(request)
        self._apply_model_to_react_agent(resolved_model)
        try:
            await self._update_runtime_config(
                self._RuntimeConfig(
                    session_id=request.session_id,
                    mode=mode,
                    request_id=request.request_id,
                    channel_id=request.channel_id,
                    request_metadata=request.metadata,
                    trusted_dirs=inputs.get("trusted_dirs"),
                )
            )
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
                                payload={"event_type": "chat.delta", "content": accumulated_text},
                                is_complete=False,
                            )
                            accumulated_text = ""
                        if accumulated_reasoning:
                            yield AgentResponseChunk(
                                request_id=rid,
                                channel_id=cid,
                                payload={
                                    "event_type": "chat.reasoning",
                                    "content": accumulated_reasoning,
                                },
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

                if chunk_type == "llm_usage":
                    logger.info(f"[JiuWenClawDeepAdapter] llm_usage chunk: {chunk}")
                    usage_meta = (
                        chunk.payload.get("usage_metadata", {})
                        if isinstance(chunk.payload, dict)
                        else {}
                    )
                    if isinstance(usage_meta, dict):
                        for token in ("input_tokens", "output_tokens", "total_tokens"):
                            usage_accumulator[token] += usage_meta.get(token, 0) or 0
                        for cost in ("input_cost", "output_cost", "total_cost"):
                            usage_accumulator[cost] += usage_meta.get(cost, 0.0) or 0.0
                    yield AgentResponseChunk(
                        request_id=rid,
                        channel_id=cid,
                        payload={
                            "event_type": "chat.usage_metadata",
                            "metadata": chunk.payload,
                            "session_id": session_id,
                        },
                        is_complete=False,
                    )
                    continue

                if chunk_type == "llm_reasoning":
                    content = (
                        (chunk.payload.get("content", "") or chunk.payload.get("output", ""))
                        if isinstance(chunk.payload, dict)
                        else str(chunk.payload)
                    )
                    yield AgentResponseChunk(
                        request_id=rid,
                        channel_id=cid,
                        payload={"event_type": "chat.reasoning", "content": content},
                        is_complete=False,
                    )
                    continue

                if chunk_type == "llm_output":
                    has_streamed_content = True
                    if accumulated_reasoning:
                        yield AgentResponseChunk(
                            request_id=rid,
                            channel_id=cid,
                            payload={
                                "event_type": "chat.reasoning",
                                "content": accumulated_reasoning,
                            },
                            is_complete=False,
                        )
                        accumulated_reasoning = ""
                    content = (
                        chunk.payload.get("content", "")
                        if isinstance(chunk.payload, dict)
                        else str(chunk.payload)
                    )
                    yield AgentResponseChunk(
                        request_id=rid,
                        channel_id=cid,
                        payload={"event_type": "chat.delta", "content": content},
                        is_complete=False,
                    )
                    continue

                if chunk_type == "answer":
                    if accumulated_text:
                        yield AgentResponseChunk(
                            request_id=rid,
                            channel_id=cid,
                            payload={"event_type": "chat.delta", "content": accumulated_text},
                            is_complete=False,
                        )
                        accumulated_text = ""
                    if accumulated_reasoning:
                        yield AgentResponseChunk(
                            request_id=rid,
                            channel_id=cid,
                            payload={
                                "event_type": "chat.reasoning",
                                "content": accumulated_reasoning,
                            },
                            is_complete=False,
                        )
                        accumulated_reasoning = ""
                    if has_streamed_content:
                        parsed = self._parse_stream_chunk(chunk, _has_streamed_content=True)
                        if parsed is not None:
                            yield AgentResponseChunk(
                                request_id=rid,
                                channel_id=cid,
                                payload=parsed,
                                is_complete=False,
                            )
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
                        payload={"event_type": "chat.delta", "content": accumulated_text},
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

            if self._skill_evolution_rail is not None:
                drained_events = await self._skill_evolution_rail.drain_pending_approval_events(wait=False)
                for evt in drained_events:
                    parsed = self._parse_stream_chunk(evt)
                    if parsed is not None:
                        yield AgentResponseChunk(
                            request_id=rid,
                            channel_id=cid,
                            payload=parsed,
                            is_complete=False,
                        )
                task = asyncio.create_task(
                    self._watch_evolution_and_push(rid, cid, session_id)
                )
                task.add_done_callback(self._on_evolution_watcher_done)
                self._evolution_watcher_tasks.add(task)
        except asyncio.CancelledError:
            logger.info(
                "[JiuWenClawDeepAdapter] 流式任务被取消: request_id=%s session_id=%s",
                rid,
                session_id,
            )
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
            TOOL_PERMISSION_CHANNEL_ID.reset(token_cid)
            cleanup_permission_context(token_perm)
            self._reset_runtime_cron_context(cron_context_tokens)

        summary = {
            "input_tokens": usage_accumulator["input_tokens"],
            "output_tokens": usage_accumulator["output_tokens"],
            "total_tokens": usage_accumulator["total_tokens"],
        }
        if usage_accumulator["input_cost"] > 0:
            summary["input_cost"] = round(usage_accumulator["input_cost"], 6)
        if usage_accumulator["output_cost"] > 0:
            summary["output_cost"] = round(usage_accumulator["output_cost"], 6)
        if usage_accumulator["total_cost"] > 0:
            summary["total_cost"] = round(usage_accumulator["total_cost"], 6)

        logger.info(
            "[JiuWenClawDeepAdapter] llm_usage summary: request_id=%s session_id=%s usage=%s",
            rid,
            session_id,
            summary,
        )

        if usage_accumulator["total_tokens"] > 0:
            yield AgentResponseChunk(
                request_id=rid,
                channel_id=cid,
                payload={
                    "event_type": "chat.usage_summary",
                    "session_id": session_id,
                    "usage": summary,
                    "model": self._resolve_model_name(),
                },
                is_complete=False,
            )

        yield AgentResponseChunk(
            request_id=rid,
            channel_id=cid,
            payload=None,
            is_complete=True,
        )

    @staticmethod
    def _parse_stream_chunk(
        chunk,
        *,
        _has_streamed_content: bool = False,
        _stage: str = "",
    ) -> dict | None:
        """将 SDK OutputSchema 转为前端可消费的 payload dict.

        Args:
            chunk: OutputSchema 或 dict
            _has_streamed_content: 是否已通过 llm_output 流式发送过内容
            _stage: 当前阶段名称，用于 auto_harness harness.message 事件

        Returns:
            dict  – 含 event_type 的 payload，或 None（需跳过的帧）。
        """
        try:
            if hasattr(chunk, "type") and hasattr(chunk, "payload"):
                chunk_type = chunk.type
                payload = chunk.payload

                if chunk_type == "controller_output" and payload is not None:
                    inner_t = getattr(payload, "type", None)
                    inner_val = getattr(inner_t, "value", inner_t) if inner_t is not None else None
                    if inner_val == "task_completion":
                        return None
                    if inner_val == "task_failed":
                        error = next(
                            (item.text for item in payload.data if hasattr(item, "text")),
                            "任务执行失败",
                        )
                        return {"event_type": "chat.error", "error": error}

                if chunk_type == "llm_output":
                    content = (
                        payload.get("content", "") if isinstance(payload, dict) else str(payload)
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
                        payload.get("content", "") if isinstance(payload, dict) else str(payload)
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
                            output.get("output", "") if isinstance(output, dict) else str(output)
                        )
                        is_chunked = (
                            output.get("chunked", False) if isinstance(output, dict) else False
                        )
                    else:
                        content = str(payload)
                        is_chunked = False

                    if _has_streamed_content and not is_chunked:
                        return {"event_type": "chat.final", "content": content}

                    if not content:
                        return None
                    if is_chunked:
                        return {"event_type": "chat.delta", "content": content}
                    return {"event_type": "chat.final", "content": content}

                if chunk_type == "tool_call":
                    tool_info = (
                        payload.get("tool_call", payload) if isinstance(payload, dict) else payload
                    )
                    return {"event_type": "chat.tool_call", "tool_call": tool_info}

                if chunk_type == "tool_update":
                    if isinstance(payload, dict):
                        update_info = payload.get("tool_update", payload)
                        update_payload = (
                            dict(update_info)
                            if isinstance(update_info, dict)
                            else {"content": str(update_info)}
                        )
                    else:
                        update_payload = {"content": str(payload)}
                    return {
                        "event_type": "chat.tool_update",
                        **update_payload,
                    }

                if chunk_type == "tool_result":
                    if isinstance(payload, dict):
                        result_info = payload.get("tool_result", payload)
                        result_payload = {
                            "result": (
                                result_info.get("result", str(result_info))
                                if isinstance(result_info, dict)
                                else str(result_info)
                            ),
                        }
                        if isinstance(result_info, dict):
                            result_payload["tool_name"] = result_info.get(
                                "tool_name"
                            ) or result_info.get("name")
                            result_payload["tool_call_id"] = result_info.get(
                                "tool_call_id"
                            ) or result_info.get("toolCallId")
                            raw_output = result_info.get("raw_output")
                            if raw_output is None:
                                raw_output = result_info.get("rawOutput")
                            if raw_output is not None:
                                result_payload["raw_output"] = raw_output
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
                    todos = payload.get("todos", []) if isinstance(payload, dict) else []
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

                if chunk_type == "context_compression_state":
                    if hasattr(payload, "model_dump"):
                        state_payload = payload.model_dump(mode="json")
                    elif isinstance(payload, dict):
                        state_payload = payload
                    else:
                        state_payload = {"summary": str(payload)}
                    return {
                        "event_type": "context_compression_state",
                        **state_payload,
                    }

                if chunk_type == "chat.ask_user_question":
                    return {
                        "event_type": "chat.ask_user_question",
                        **(payload if isinstance(payload, dict) else {}),
                    }

                if chunk_type == "__interaction__":
                    if isinstance(payload, dict) and payload.get("interaction_type") == "activate_confirm":
                        return {
                            "event_type": "harness.activate_interaction",
                            "interaction_type": "activate_confirm",
                            "interaction_id": payload.get("interaction_id", ""),
                            "extension_name": payload.get("extension_name", ""),
                            "runtime_path": payload.get("runtime_path", ""),
                            "session_runtime_path": payload.get("session_runtime_path", ""),
                            "extension_runtime_path": payload.get("extension_runtime_path", payload.get("runtime_path", "")),
                            "options": payload.get("options", ["accept", "reject"]),
                        }
                    return convert_interactions_to_ask_user_question([payload])

                # Auto-harness specific: harness.message event
                if chunk_type == "message":
                    content = (
                        payload.get("content", "")
                        if isinstance(payload, dict)
                        else str(payload)
                    )
                    # Extract stage from payload if available, fallback to _stage parameter
                    stage_from_payload = (
                        payload.get("stage", "")
                        if isinstance(payload, dict)
                        else ""
                    )
                    result: dict[str, Any] = {
                        "event_type": "harness.message",
                        "content": content,
                        "stage": stage_from_payload or _stage,
                    }
                    # Pass through stages array for dynamic stage definition
                    if isinstance(payload, dict):
                        if "stages" in payload:
                            result["stages"] = payload["stages"]
                        if "pipeline" in payload:
                            result["pipeline"] = payload["pipeline"]
                    return result

                # Auto-harness specific: harness.stage_result event
                if chunk_type == "stage_result":
                    if isinstance(payload, dict):
                        stage = payload.get("stage", _stage)
                        return {
                            "event_type": "harness.stage_result",
                            "stage": stage,
                            "status": payload.get("status", "success"),
                            "error": payload.get("error", ""),
                            "messages": payload.get("messages", []),
                            "metrics": payload.get("metrics", {}),
                            "scope": payload.get("scope", ""),
                            "parent_stage": payload.get("parent_stage", ""),
                            "extension_stage": payload.get("extension_stage", ""),
                            "extension_name": payload.get("extension_name", ""),
                            "task_id": payload.get("task_id", ""),
                        }
                    return None

                # Auto-harness specific: harness.extension_ready event
                if chunk_type == "extension_ready":
                    if isinstance(payload, dict):
                        return {
                            "event_type": "harness.extension_ready",
                            "extension_name": payload.get("extension_name", ""),
                            "runtime_path": payload.get("runtime_path", ""),
                            "session_runtime_path": payload.get("session_runtime_path", ""),
                            "extension_runtime_path": payload.get("extension_runtime_path", ""),
                            "config_path": payload.get("config_path", ""),
                            "runtime_extensions": payload.get("runtime_extensions", []),
                            "verify_report": payload.get("verify_report", {}),
                            "components_summary": payload.get("components_summary", {}),
                        }
                    return None

                if chunk_type == "harness_session_finished":
                    if isinstance(payload, dict):
                        return {
                            "event_type": "harness.session_finished",
                            "pipeline": payload.get("pipeline", ""),
                            "status": payload.get("status", "success"),
                            "results_count": payload.get("results_count", 0),
                            "is_terminal": bool(payload.get("is_terminal", True)),
                        }
                    return {
                        "event_type": "harness.session_finished",
                        "status": "success",
                        "is_terminal": True,
                    }

                # Auto-harness specific: activate_testing_guide summary
                if chunk_type == "activate_testing_guide":
                    if isinstance(payload, dict):
                        text = payload.get("text", "")
                        if text:
                            return {"event_type": "chat.delta", "content": text}
                    return None

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

    async def _handle_memory_rail_by_config(self, mode: str):
        config = get_config()
        if get_memory_mode(config) == "local":
            # 引擎门禁：memory.engine 未放行内置时，等同于禁用
            builtin_on = is_builtin_memory_allowed(config) and is_memory_enabled(mode, config)
            if builtin_on:
                # 开启记忆
                if self._memory_rail is not None:
                    cur_memory_type = is_proactive_memory(mode, config)
                    if self._is_proactive_memory != cur_memory_type:
                        # 当前记忆类型（主动/被动）和之前注册的不一致，重新注册
                        await self._instance.unregister_rail(self._memory_rail)
                        self._memory_rail = None
                    else:
                        # 已经注册，且记忆类型相同，无需其他操作
                        return
                if self._memory_rail is None:
                    self._memory_rail = self._build_memory_rail(mode)
                if self._memory_rail is not None:
                    await self._instance.register_rail(self._memory_rail)
                    logger.info(f"[JiuWenClawDeepAdapter] MemoryRail registered for {mode} mode")
            elif not builtin_on and self._memory_rail is not None:
                await self._instance.unregister_rail(self._memory_rail)
                self._memory_rail = None
                logger.info(f"[JiuWenClawDeepAdapter] MemoryRail unregistered for {mode} mode")

    def _build_external_memory_rail(self):
        from jiuwenclaw.agents.harness.common.memory.external_memory_builder import (
            build_external_memory_rail,
        )

        return build_external_memory_rail(
            config=get_config(),
            workspace_dir=self._workspace_dir,
        )

    async def _handle_external_memory_rail_by_config(self):
        """Register / unregister ExternalMemoryRail based on config.

        External memory is mode-independent — configured once and active for
        both plan and fast modes. `_external_memory_rail_registered` dedups
        calls from both _update_plan_mode_rails() and _update_agent_mode_rails().
        Not part of `_get_current_agent_rails()`, so it is not torn down on
        config hot-reload (preserves prefetch cache + circuit breaker state).
        """
        from jiuwenclaw.agents.harness.common.memory.external_memory_config import (
            is_external_memory_enabled,
        )

        config = get_config()
        if is_external_memory_enabled(config):
            if self._external_memory_rail_registered:
                return
            if self._external_memory_rail is None:
                self._external_memory_rail = self._build_external_memory_rail()
            if self._external_memory_rail is None:
                return
            try:
                await self._instance.register_rail(self._external_memory_rail)
                self._external_memory_rail_registered = True
                logger.info("[JiuWenClawDeepAdapter] ExternalMemoryRail registered")
            except Exception as exc:
                logger.error("[JiuWenClawDeepAdapter] ExternalMemoryRail register failed: %s", exc)
                self._external_memory_rail = None
        elif self._external_memory_rail is not None and self._external_memory_rail_registered:
            # Call on_session_end BEFORE unregister_rail: unregister -> uninit()
            # is sync, and run_coroutine_threadsafe from the same event loop
            # thread would deadlock.
            provider = getattr(self._external_memory_rail, "_provider", None)
            if provider is not None and hasattr(provider, "on_session_end"):
                try:
                    await provider.on_session_end()
                except Exception as exc:
                    logger.debug("[JiuWenClawDeepAdapter] on_session_end failed: %s", exc)
            try:
                await self._instance.unregister_rail(self._external_memory_rail)
                logger.info("[JiuWenClawDeepAdapter] ExternalMemoryRail unregistered")
            except Exception as exc:
                logger.warning(
                    "[JiuWenClawDeepAdapter] ExternalMemoryRail unregister failed: %s", exc
                )
            self._external_memory_rail = None
            self._external_memory_rail_registered = False

    async def compress_context(self, session_id: str, session: Any = None) -> dict[str, Any]:
        """主动触发上下文压缩。

        Args:
            session_id: 会话ID
            session: Session 对象（可选）

        Returns:
            包含压缩结果的字典:
            - result: "busy" | "compressed" | "noop"
            - stats: 压缩统计信息（仅当 result == "compressed" 时）
        """
        if self._instance is None or self._instance.react_agent is None:
            raise ValueError("Agent instance not available")

        context_engine = self._instance.react_agent.context_engine
        react_agent = self._instance.react_agent

        context = context_engine.get_context(session_id=session_id)
        if context is None:
            return {"result": "noop", "stats": None}

        raw_total_tokens = await self._count_full_context_tokens(
            context, react_agent, session_id
        )

        result = await context_engine.compress_context(
            session=session,
            session_id=session_id,
        )

        response: dict[str, Any] = {"result": result}

        if result == "compressed":
            context = context_engine.get_context(session_id=session_id)
            if context:
                total_tokens = await self._count_full_context_tokens(
                    context, react_agent, session_id
                )

                stats = context.statistic()
                response["stats"] = {
                    "total_messages": stats.total_messages,
                    "total_tokens": total_tokens,
                    "raw_total_tokens": raw_total_tokens,
                }

        return response

    async def get_context_usage(self, session_id: str) -> dict[str, Any]:
        """获取当前上下文窗口占用统计。

        Args:
            session_id: 会话ID

        Returns:
            包含上下文使用情况统计的字典:
            - context_window_limit: 模型上下文窗口总 token 数
            - total_tokens: 当前上下文已用 token 数
            - system_prompt_tokens: 系统提示词 token 数
            - messages_tokens: 对话消息 token 数
            - tools_tokens: 工具定义 token 数
            - occupancy_rate: 占用率 (0-100)
            - message_count: 对话消息数量
            - context_occupancy: 上下文占用详情（来自 deepagent）
        """
        if self._instance is None:
            raise ValueError("Agent instance not available")

        context_engine = self._instance.react_agent.context_engine
        react_agent = self._instance.react_agent
        context = context_engine.get_context(session_id=session_id)
        if context is None:
            return {
                "context_window_limit": 0, "total_tokens": 0,
                "system_prompt_tokens": 0, "messages_tokens": 0,
                "tools_tokens": 0, "occupancy_rate": 0,
                "message_count": 0, "context_occupancy": None,
            }

        # 分项估算：直接用 context engine 的 token counter
        token_counter = context.token_counter()
        from openjiuwen.core.foundation.tool import ToolInfo

        # 系统提示词
        system_prompt = ""
        if hasattr(react_agent, "prompt_builder") and react_agent.prompt_builder is not None:
            system_prompt = react_agent.prompt_builder.build()
        elif hasattr(react_agent, "system_prompt_builder") and react_agent.system_prompt_builder is not None:
            system_prompt = react_agent.system_prompt_builder.build()
        if system_prompt and token_counter:
            system_prompt_tokens = token_counter.count(system_prompt) or 0
        elif system_prompt:
            system_prompt_tokens = len(system_prompt) // 4
        else:
            system_prompt_tokens = 0

        # 对话消息
        context_messages = context.get_messages() or []
        if context_messages and token_counter:
            messages_tokens = token_counter.count_messages(context_messages) or 0
        elif context_messages:
            messages_tokens = sum(len(str(msg.content)) // 4 for msg in context_messages)
        else:
            messages_tokens = 0

        # 工具定义
        tools: list[ToolInfo] = []
        if hasattr(react_agent, "ability_manager") and react_agent.ability_manager is not None:
            for card in react_agent.ability_manager.list() or []:
                if hasattr(card, "to_tool_info"):
                    tools.append(card.to_tool_info())
                elif hasattr(card, "name") and hasattr(card, "description"):
                    tools.append(ToolInfo(
                        name=card.name,
                        description=card.description or "",
                        parameters=getattr(card, "input_params", {}),
                    ))
        if tools and token_counter:
            tools_tokens = token_counter.count_tools(tools) or 0
        else:
            tools_tokens = 0

        # 总量 & 窗口限制：优先用 DeepAgent 的准确值，回退到估算
        total_tokens = system_prompt_tokens + messages_tokens + tools_tokens
        context_window_limit = 0
        occupancy_rate = 0.0
        context_occupancy = None

        try:
            usage = self._instance.get_context_usage(session_id=session_id)
            context_occupancy = usage
            # DeepAgent 的 total_tokens 来自 usage_metadata，比估算更准确
            da_total = usage.get("total_tokens", 0)
            if da_total > 0:
                total_tokens = da_total
            context_window_limit = usage.get("context_window_tokens", 0)
            occupancy_rate = usage.get("usage_percent", 0)
        except Exception as exc:
            logger.debug("[JiuWenClaw] DeepAgent.get_context_usage failed: %s", exc)
            from openjiuwen.core.context_engine.context.context_utils import ContextUtils
            model_name = (
                getattr(self._model_request_config, "model_name", "") or ""
                if self._model_request_config else ""
            )
            context_window_limit = ContextUtils.resolve_context_max(model_name=model_name)
            if context_window_limit > 0:
                occupancy_rate = round(total_tokens / context_window_limit * 100, 1)

        message_count = len(context_messages)

        return {
            "context_window_limit": context_window_limit,
            "total_tokens": total_tokens,
            "system_prompt_tokens": system_prompt_tokens,
            "messages_tokens": messages_tokens,
            "tools_tokens": tools_tokens,
            "occupancy_rate": occupancy_rate,
            "message_count": message_count,
            "context_occupancy": context_occupancy,
        }

    async def generate_recap(self, session_id: str) -> dict[str, Any]:
        """生成会话快速回顾（read-only，不修改对话历史）。

        取最近30条消息 → fast model → 1-3句摘要。
        """
        from jiuwenclaw.server.runtime.agent_adapter.recap_prompts import (
            RECENT_MESSAGE_WINDOW,
            build_recap_prompt,
        )

        messages = self._get_recent_messages(session_id, window=RECENT_MESSAGE_WINDOW)
        if not messages:
            return {"status": "no_turn"}

        prompt = build_recap_prompt(memory=None)
        summary_text = await self._call_model_for_recap(messages, prompt)
        if not summary_text:
            return {"status": "failed", "error": "Model returned empty response"}

        return {"status": "ok", "summary": summary_text.strip()}

    def _get_recent_messages(self, session_id: str, window: int = 30) -> list[Any]:
        """从当前 agent 对话上下文中提取最近N条消息。"""
        if self._instance is None or self._instance.react_agent is None:
            return []

        context_engine = self._instance.react_agent.context_engine
        context = context_engine.get_context(session_id=session_id)
        if context is None:
            return []

        try:
            all_messages = list(context.get_messages() or [])
        except Exception as exc:
            logger.debug("[JiuWenClaw] _get_recent_messages failed: %s", exc)
            return []

        return all_messages[-window:]

    async def _call_model_for_recap(
        self,
        messages: list[Any],
        prompt: str,
    ) -> str | None:
        """调用 fast model 生成 recap 摘要。

        - 不传 system prompt（空）
        - prompt 作为最后一条 user message 追加到对话末尾
        - 不传 tools
        - 不启用 thinking
        """
        from openjiuwen.core.foundation.llm.schema.message import UserMessage, AssistantMessage

        if self._model is None:
            logger.error("[generate_recap] no model instance available")
            return None

        recap_messages: list[Any] = []

        for msg in messages:
            role = getattr(msg, "role", None) or ""
            content = getattr(msg, "content", None) or ""
            if isinstance(content, list):
                # Multimodal content — extract text parts only
                content = " ".join(
                    str(p) for p in content
                    if isinstance(p, str) or (isinstance(p, dict) and p.get("type") == "text")
                )
            content = str(content)
            if not content.strip():
                continue

            if role == "user":
                recap_messages.append(UserMessage(content=content))
            elif role == "assistant":
                recap_messages.append(AssistantMessage(content=content))
            else:
                recap_messages.append(UserMessage(content=content))

        # Prompt 作为最后一条 user message 追加
        recap_messages.append(UserMessage(content=prompt))

        try:
            result = await self._model.invoke(
                recap_messages,
                max_tokens=300,
                temperature=0,
            )
            return getattr(result, "content", None) or str(result)
        except Exception:
            logger.exception("[generate_recap] model call failed")
            return None

    async def _count_full_context_tokens(
        self,
        context: Any,
        react_agent: Any,
        session_id: str,
    ) -> int:
        """计算完整上下文的 token 数（包含 system messages + context messages + tools）。
        Args:
            context: ModelContext 对象
            react_agent: ReActAgent 对象
            session_id: 会话ID

        Returns:
            完整上下文的 token 总数
        """
        from openjiuwen.core.foundation.llm import SystemMessage
        from openjiuwen.core.foundation.tool import ToolInfo

        token_counter = context.token_counter()
        total_tokens = 0

        # 1. 计算系统消息的 tokens
        system_prompt = ""
        if hasattr(react_agent, "prompt_builder") and react_agent.prompt_builder is not None:
            system_prompt = react_agent.prompt_builder.build()
        elif hasattr(react_agent, "system_prompt_builder") and react_agent.system_prompt_builder is not None:
            system_prompt = react_agent.system_prompt_builder.build()

        if system_prompt:
            if token_counter is not None:
                total_tokens += token_counter.count(system_prompt)
            else:
                total_tokens += len(system_prompt) // 4

        # 2. 计算对话消息的 tokens
        context_messages = context.get_messages()
        if context_messages:
            if token_counter is not None:
                total_tokens += token_counter.count_messages(context_messages)
            else:
                total_tokens += sum(len(str(msg.content)) // 4 for msg in context_messages)

        # 3. 计算工具定义的 tokens
        tools: list[ToolInfo] = []
        if hasattr(react_agent, "ability_manager") and react_agent.ability_manager is not None:
            for card in react_agent.ability_manager.list() or []:
                if hasattr(card, "to_tool_info"):
                    tools.append(card.to_tool_info())
                elif hasattr(card, "name") and hasattr(card, "description"):
                    tools.append(ToolInfo(
                        name=card.name,
                        description=card.description or "",
                        parameters=getattr(card, "input_params", {}),
                    ))

        if tools and token_counter is not None:
            total_tokens += token_counter.count_tools(tools)

        return total_tokens

    async def _watch_evolution_and_push(self, rid: str, cid: str, session_id: str) -> None:
        """等待演进后台 task 完成，通过 send_push 推送审批事件。

        审批事件必须先于 evolution_status:end 推送，否则 Gateway 在清除
        evolution_in_progress 和标记 pending_approval 之间存在竞争窗口。
        """
        from jiuwenclaw.server.gateway_push import WebSocketGatewayPushTransport

        transport = WebSocketGatewayPushTransport()

        async def _push_status(status: str, stage: str, message: str = "") -> None:
            await transport.send_push(build_server_push_message(
                session_id=session_id,
                request_id=rid,
                fallback_channel_id=cid,
                payload={
                    "event_type": "chat.evolution_status",
                    "status": status,
                    "stage": stage,
                    "message": message,
                },
            ))

        async def _push_approval(evt) -> None:
            raw_payload = evt.payload if hasattr(evt, "payload") and isinstance(evt.payload, dict) else evt
            # Inject event_type from OutputSchema.type so Gateway routes it correctly.
            payload = dict(raw_payload)
            evt_type = getattr(evt, "type", None)
            if evt_type and "event_type" not in payload:
                payload["event_type"] = evt_type
            await transport.send_push(build_server_push_message(
                session_id=session_id,
                request_id=rid,
                fallback_channel_id=cid,
                payload=payload,
            ))

        try:
            if self._skill_evolution_rail is None:
                return

            events = await self._skill_evolution_rail.drain_pending_approval_events(wait=True)
            outcomes = self._skill_evolution_rail.drain_evolution_outcomes()
            await self._skill_evolution_rail.cleanup_background_tasks()

            approval_events = [evt for evt in events if self._is_approval_event(evt)]
            outcome = outcomes[-1] if outcomes else None
            if not approval_events and outcome is None:
                logger.info(
                    "[JiuWenClawDeepAdapter] evolution watcher finished without approval/outcome: "
                    "request_id=%s session_id=%s",
                    rid,
                    session_id,
                )
                return

            await _push_status("start", "collecting", "Running evolution analysis...")

            for evt in approval_events:
                await _push_approval(evt)

            if outcome is not None:
                stage = str(outcome.get("status") or "failed").strip().lower()
                message = str(outcome.get("message") or "Evolution analysis failed")
                if stage == "failed":
                    await _push_status("end", "hidden", "")
                    return
                await _push_status("end", stage, message)
                return

            await _push_status("end", "completed", "Evolution analysis completed")
        except Exception as exc:
            logger.warning("[JiuWenClawDeepAdapter] evolution watcher failed: %s", exc)
            try:
                await _push_status("end", "hidden", "")
            except Exception:
                pass

    def _on_evolution_watcher_done(self, task: asyncio.Task) -> None:
        """Callback when an evolution watcher task completes.

        Discards the task from the tracking set and logs any exception.
        """
        self._evolution_watcher_tasks.discard(task)
        try:
            task.result()
        except Exception as exc:
            logger.warning("[JiuWenClawDeepAdapter] evolution watcher task exception: %s", exc)

    @staticmethod
    def _is_approval_event(evt) -> bool:
        """Check whether an OutputSchema event is an approval request."""
        evt_type = getattr(evt, "type", "")
        if evt_type == "chat.ask_user_question":
            return True
        if hasattr(evt, "payload") and isinstance(evt.payload, dict):
            return evt.payload.get("event_type") == "chat.ask_user_question"
        return False
