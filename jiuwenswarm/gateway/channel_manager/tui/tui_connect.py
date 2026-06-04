# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

import inspect
import logging
import os
import shutil
import sys
import time
from dataclasses import dataclass
from typing import Any

import yaml

from openjiuwen.core.foundation.llm import Model, ProviderType
from openjiuwen.core.foundation.llm.schema.config import (
    ModelClientConfig,
    ModelRequestConfig,
)
from openjiuwen.auto_harness.schema import load_auto_harness_config

from jiuwenswarm.common.config import (
    get_config,
    get_config_raw,
    get_default_models,
    resolve_env_vars,
    update_context_engine_enabled_in_config,
    update_memory_forbidden_enabled_in_config,
    update_permissions_enabled_in_config,
    get_model_names,
    get_model_config,
    add_or_update_model_in_config,
    update_default_models_in_config,
    ensure_defaults_list_in_config,
    update_preferred_language_in_config,
)
from jiuwenswarm.gateway.routing.route_binding import GatewayRouteBinding
from jiuwenswarm.common.version import __version__
from jiuwenswarm.common.utils import get_user_workspace_dir

logger = logging.getLogger(__name__)

# Auto-Harness config file path
_DEFAULT_REPO_URL = "https://gitcode.com/openJiuwen/agent-core.git"
_AUTO_HARNESS_CONFIG_DIR = get_user_workspace_dir() / "auto-harness"
_AUTO_HARNESS_CONFIG_FILE = _AUTO_HARNESS_CONFIG_DIR / "config.yaml"
_AUTO_HARNESS_LOCAL_REPO = _AUTO_HARNESS_CONFIG_DIR / "repo" / "openJiuwen--agent-core"

# Default values for ci_gate config
_DEFAULT_CI_GATE_PYTHON_EXECUTABLE = sys.executable
_DEFAULT_CI_GATE_INSTALL_COMMAND = "uv sync --active --group dev --extra cli"


def _get_auto_harness_config() -> dict[str, Any]:
    """Load auto-harness config.yaml with auto-fill for ci_gate defaults."""
    config: dict[str, Any] = {}

    if not _AUTO_HARNESS_CONFIG_FILE.exists():
        load_auto_harness_config(str(_AUTO_HARNESS_CONFIG_FILE))

    try:
        config = yaml.safe_load(_AUTO_HARNESS_CONFIG_FILE.read_text(encoding="utf-8")) or {}
    except Exception as e:
        logger.warning("[auto-harness config] Failed to load: %s", e)
        config = {}

    # Auto-fill ci_gate defaults if missing
    ci_gate = config.get("ci_gate") or {}
    git_config = config.get("git") or {}
    needs_save = False

    git_remote = git_config.get("remote")
    if not git_remote:
        git_config["remote"] = "autoharness"

    # Ensure local_repo is a string (not Path object which causes YAML serialization issues)
    local_repo = config.get("local_repo")
    repo_url = config.get("repo_url")
    if not local_repo:
        config["local_repo"] = str(_AUTO_HARNESS_LOCAL_REPO)
        needs_save = True

    if not repo_url:
        config["repo_url"] = str(_DEFAULT_REPO_URL)
        needs_save = True

    elif hasattr(local_repo, "__fspath__"):  # Path-like object
        config["local_repo"] = str(local_repo)
        needs_save = True

    if not ci_gate.get("python_executable"):
        ci_gate["python_executable"] = str(_DEFAULT_CI_GATE_PYTHON_EXECUTABLE)
        needs_save = True

    if not ci_gate.get("install_command"):
        ci_gate["install_command"] = _DEFAULT_CI_GATE_INSTALL_COMMAND
        needs_save = True

    budget = config.get("budget", {})
    max_tasks_per_session = budget.get("max_tasks_per_session", 5)
    if max_tasks_per_session > 5:
        budget["max_tasks_per_session"] = 5
        needs_save = True

    if needs_save:
        config["ci_gate"] = ci_gate
        _save_auto_harness_config(config)
        logger.info("[auto-harness config] Auto-filled ci_gate defaults: python_executable=%s, install_command=%s",
                    ci_gate.get("python_executable"), ci_gate.get("install_command"))

    return config


def _save_auto_harness_config(config: dict[str, Any]) -> None:
    """Save auto-harness config.yaml."""
    _AUTO_HARNESS_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _AUTO_HARNESS_CONFIG_FILE.write_text(
        yaml.dump(config, allow_unicode=True, sort_keys=False, default_flow_style=False),
        encoding="utf-8"
    )


def _update_auto_harness_git_user_name(value: str) -> None:
    """Update git.user_name, fork_owner, and gitcode.username in auto-harness config.
    - git.user_name: 用于 git commit
    - git.fork_owner: 用于创建 PR
    - gitcode.username: GitCode 登录用户名
    """
    config = _get_auto_harness_config()
    if "git" not in config:
        config["git"] = {}
    config["git"]["user_name"] = value
    config["git"]["fork_owner"] = value  # 合并：用户名同时作为 fork_owner
    if "gitcode" not in config:
        config["gitcode"] = {}
    config["gitcode"]["username"] = value  # 合并：用户名同时作为 gitcode.username
    _save_auto_harness_config(config)


def _update_auto_harness_git_user_email(value: str) -> None:
    """Update git.user_email in auto-harness config."""
    config = _get_auto_harness_config()
    if "git" not in config:
        config["git"] = {}
    config["git"]["user_email"] = value
    _save_auto_harness_config(config)


def _update_auto_harness_gitcode_access_token(value: str) -> None:
    """Update gitcode.access_token in auto-harness config."""
    config = _get_auto_harness_config()
    if "gitcode" not in config:
        config["gitcode"] = {}
    config["gitcode"]["access_token"] = value
    _save_auto_harness_config(config)

# ── 需要转发到 Agent 的方法集合 ──────────────────────────────

CLI_FORWARD_REQ_METHODS = frozenset(
    {
        "command.add_dir",
        "command.chrome",
        "command.compact",
        "command.context",
        "command.recap",
        "command.diff",
        "command.mcp",
        "command.resume",
        "command.sandbox",
        "command.session",
        "command.status",
        "chat.send",
        "chat.interrupt",
        "chat.resume",
        "chat.user_answer",
        "history.get",
        "browser.start",
        "skills.marketplace.list",
        "skills.list",
        "skills.installed",
        "skills.get",
        "skills.toggle",
        "skills.install",
        "skills.import_local",
        "skills.marketplace.add",
        "skills.marketplace.remove",
        "skills.marketplace.toggle",
        "skills.uninstall",
        "skills.skillnet.search",
        "skills.skillnet.install",
        "skills.skillnet.install_status",
        "skills.skillnet.evaluate",
        "skills.clawhub.get_token",
        "skills.clawhub.set_token",
        "skills.clawhub.search",
        "skills.clawhub.download",
        "skills.teamskillshub.info",
        "skills.teamskillshub.init",
        "skills.teamskillshub.validate",
        "skills.teamskillshub.pack",
        "skills.teamskillshub.search",
        "skills.teamskillshub.install",
        "skills.teamskillshub.publish",
        "skills.teamskillshub.delete",
        "skills.evolution.status",
        "skills.evolution.get",
        "skills.evolution.save",
        "plugins.list",
        "plugins.install",
        "plugins.uninstall",
        "plugins.enable",
        "plugins.disable",
        "plugins.reload",
        "permissions.tools.get",
        "permissions.tools.update",
        "permissions.rules.get",
        "permissions.rules.create",
        "permissions.rules.update",
        "extensions.list",
        "extensions.import",
        "extensions.delete",
        "extensions.toggle",
        "session.fork",
        # Agent configuration
        "agents.list",
        "agents.get",
        "agents.create",
        "agents.update",
        "agents.delete",
        "agents.enable",
        "agents.disable",
        "agents.tools_list",
        # Schedule task management
        "schedule.check_config",
        "schedule.update_config",
        "schedule.create",
        "schedule.run",
        "schedule.list",
        "schedule.status",
        "schedule.logs",
        "schedule.cancel",
        "schedule.delete",
    }
)

CLI_FORWARD_NO_LOCAL_HANDLER_METHODS = frozenset(
    {
        "command.add_dir",
        "command.chrome",
        "command.compact",
        "command.context",
        "command.recap",
        "command.diff",
        "command.mcp",
        "command.resume",
        "command.sandbox",
        "command.session",
        "command.status",
        "browser.start",
        "skills.marketplace.list",
        "skills.list",
        "skills.installed",
        "skills.get",
        "skills.toggle",
        "skills.install",
        "skills.import_local",
        "skills.marketplace.add",
        "skills.marketplace.remove",
        "skills.marketplace.toggle",
        "skills.uninstall",
        "skills.skillnet.search",
        "skills.skillnet.install",
        "skills.skillnet.install_status",
        "skills.skillnet.evaluate",
        "skills.clawhub.get_token",
        "skills.clawhub.set_token",
        "skills.clawhub.search",
        "skills.clawhub.download",
        "skills.teamskillshub.info",
        "skills.teamskillshub.init",
        "skills.teamskillshub.validate",
        "skills.teamskillshub.pack",
        "skills.teamskillshub.search",
        "skills.teamskillshub.install",
        "skills.teamskillshub.publish",
        "skills.teamskillshub.delete",
        "skills.evolution.status",
        "skills.evolution.get",
        "skills.evolution.save",
        "plugins.list",
        "plugins.install",
        "plugins.uninstall",
        "plugins.enable",
        "plugins.disable",
        "plugins.reload",
        "permissions.tools.get",
        "permissions.tools.update",
        "permissions.rules.get",
        "permissions.rules.create",
        "permissions.rules.update",
        "extensions.list",
        "extensions.import",
        "extensions.delete",
        "extensions.toggle",
        "session.fork",
        # Agent configuration
        "agents.list",
        "agents.get",
        "agents.create",
        "agents.update",
        "agents.delete",
        "agents.enable",
        "agents.disable",
        "agents.tools_list",
        # Schedule task management
        "schedule.check_config",
        "schedule.update_config",
        "schedule.create",
        "schedule.run",
        "schedule.list",
        "schedule.status",
        "schedule.logs",
        "schedule.cancel",
        "schedule.delete",
    }
)


@dataclass
class CliHandlersBindParams:
    channel: Any  # GatewayServer instance
    agent_client: Any = None
    message_handler: Any = None
    on_config_saved: Any = None
    path: str = "/tui"
    cron_controller: Any = None


@dataclass
class CliRouteBindParams:
    agent_client: Any = None
    message_handler: Any = None
    on_config_saved: Any = None
    path: str = "/tui"
    channel_id: str = "tui"
    cron_controller: Any = None


@dataclass
class ForwardRewindE2AParams:
    """Parameters for forwarding rewind request to AgentServer via E2A."""

    ws: Any
    req_id: str
    target_sid: str
    turn_index: int
    req_method: Any
    error_label: str


_CLI_CONFIG_SET_ENV_MAP = {
    "model_provider": "MODEL_PROVIDER",
    "model": "MODEL_NAME",
    "api_base": "API_BASE",
    "api_key": "API_KEY",
    "video_api_base": "VIDEO_API_BASE",
    "video_api_key": "VIDEO_API_KEY",
    "video_model": "VIDEO_MODEL_NAME",
    "video_provider": "VIDEO_PROVIDER",
    "audio_api_base": "AUDIO_API_BASE",
    "audio_api_key": "AUDIO_API_KEY",
    "audio_model": "AUDIO_MODEL_NAME",
    "audio_provider": "AUDIO_PROVIDER",
    "vision_api_base": "VISION_API_BASE",
    "vision_api_key": "VISION_API_KEY",
    "vision_model": "VISION_MODEL_NAME",
    "vision_provider": "VISION_PROVIDER",
    "email_address": "EMAIL_ADDRESS",
    "email_token": "EMAIL_TOKEN",
    "embed_api_key": "EMBED_API_KEY",
    "embed_api_base": "EMBED_API_BASE",
    "embed_model": "EMBED_MODEL",
    "jina_api_key": "JINA_API_KEY",
    "serper_api_key": "SERPER_API_KEY",
    "perplexity_api_key": "PERPLEXITY_API_KEY",
    "github_token": "GITHUB_TOKEN",
    "evolution_auto_scan": "EVOLUTION_AUTO_SCAN",
    "teamskills_market_url": "TEAM_SKILLS_HUB_BASE_URL",
    "teamskills_user_token": "TEAM_SKILLS_HUB_USER_TOKEN",
    "teamskills_system_token": "TEAM_SKILLS_HUB_SYSTEM_TOKEN",
    "teamskills_allowed_download_hosts": "TEAM_SKILLS_HUB_ALLOWED_DOWNLOAD_HOSTS",
}

_CLI_CONFIG_YAML_SETTERS: dict[str, Any] = {
    "context_engine_enabled": update_context_engine_enabled_in_config,
    "permissions_enabled": update_permissions_enabled_in_config,
    "memory_forbidden_enabled": update_memory_forbidden_enabled_in_config,
    "preferred_language": update_preferred_language_in_config,
    # Auto-Harness config items (stored in ~/.jiuwenswarm/auto-harness/config.yaml)
    # 用户名同时设置 git.user_name, fork_owner, gitcode.username（三者合一）
    "auto_harness_git_user_name": _update_auto_harness_git_user_name,
    "auto_harness_git_user_email": _update_auto_harness_git_user_email,
    "auto_harness_gitcode_access_token": _update_auto_harness_gitcode_access_token,
}

_CLI_CONFIG_YAML_KEYS = frozenset(_CLI_CONFIG_YAML_SETTERS.keys())


_PREFERRED_LANGUAGE_OPTIONS = ("zh", "en")


def _build_config_schema() -> list[dict]:
    """构建配置项 Schema，供前端渲染交互界面。与 config.yaml 结构对齐。"""
    available_providers = [p.value for p in ProviderType]
    # 显式使用 ProviderType.OpenAI 作为默认供应商，避免依赖枚举声明顺序
    default_provider = (
        ProviderType.OpenAI.value
        if hasattr(ProviderType, "OpenAI")
        else (available_providers[0] if available_providers else "")
    )
    empty = ""
    return [
        # Model
        {"key": "model", "label": "默认模型", "group": "Model", "type": "string",
         "source": "env", "default": empty},
        {"key": "model_provider", "label": "模型供应商", "group": "Model", "type": "select",
         "options": available_providers, "source": "env", "default": default_provider},
        {"key": "api_base", "label": "API 地址", "group": "Model", "type": "string",
         "source": "env", "default": empty},
        {"key": "api_key", "label": "API Key", "group": "Model", "type": "password",
         "sensitive": True, "source": "env", "default": empty},
        # Vision
        {"key": "vision_model", "label": "视觉模型", "group": "Vision", "type": "string",
         "source": "env", "default": empty},
        {"key": "vision_provider", "label": "视觉供应商", "group": "Vision", "type": "select",
         "options": available_providers, "source": "env", "default": default_provider},
        {"key": "vision_api_base", "label": "视觉API地址", "group": "Vision", "type": "string",
         "source": "env", "default": empty},
        {"key": "vision_api_key", "label": "视觉API Key", "group": "Vision", "type": "password",
         "sensitive": True, "source": "env", "default": empty},
        # Video
        {"key": "video_model", "label": "视频模型", "group": "Video", "type": "string",
         "source": "env", "default": empty},
        {"key": "video_provider", "label": "视频供应商", "group": "Video", "type": "select",
         "options": available_providers, "source": "env", "default": default_provider},
        {"key": "video_api_base", "label": "视频API地址", "group": "Video", "type": "string",
         "source": "env", "default": empty},
        {"key": "video_api_key", "label": "视频API Key", "group": "Video", "type": "password",
         "sensitive": True, "source": "env", "default": empty},
        # Audio
        {"key": "audio_model", "label": "音频模型", "group": "Audio", "type": "string",
         "source": "env", "default": empty},
        {"key": "audio_provider", "label": "音频供应商", "group": "Audio", "type": "select",
         "options": available_providers, "source": "env", "default": default_provider},
        {"key": "audio_api_base", "label": "音频API地址", "group": "Audio", "type": "string",
         "source": "env", "default": empty},
        {"key": "audio_api_key", "label": "音频API Key", "group": "Audio", "type": "password",
         "sensitive": True, "source": "env", "default": empty},
        # Embedding
        {"key": "embed_api_key", "label": "嵌入API Key", "group": "Embedding", "type": "password",
         "sensitive": True, "source": "env", "default": empty},
        {"key": "embed_api_base", "label": "嵌入API地址", "group": "Embedding", "type": "string",
         "source": "env", "default": empty},
        {"key": "embed_model", "label": "嵌入模型", "group": "Embedding", "type": "string",
         "source": "env", "default": empty},
        # Search & External
        {"key": "jina_api_key", "label": "Jina API Key", "group": "Search & External", "type": "password",
         "sensitive": True, "source": "env", "default": empty},
        {"key": "serper_api_key", "label": "Serper API Key", "group": "Search & External", "type": "password",
         "sensitive": True, "source": "env", "default": empty},
        {"key": "perplexity_api_key", "label": "Perplexity API Key", "group": "Search & External", "type": "password",
         "sensitive": True, "source": "env", "default": empty},
        {"key": "github_token", "label": "GitHub Token", "group": "Search & External", "type": "password",
         "sensitive": True, "source": "env", "default": empty},
        # TeamSkills
        {"key": "teamskills_market_url", "label": "TeamSkills Hub 地址", "group": "TeamSkills", "type": "string",
         "source": "env", "default": empty},
        {"key": "teamskills_user_token", "label": "TeamSkills 用户Token", "group": "TeamSkills", "type": "password",
         "sensitive": True, "source": "env", "default": empty},
        {"key": "teamskills_system_token", "label": "TeamSkills 系统Token", "group": "TeamSkills", "type": "password",
         "sensitive": True, "source": "env", "default": empty},
        {
         "key": "teamskills_allowed_download_hosts",
         "label": "TeamSkills 下载白名单Hosts(逗号分隔)",
         "group": "TeamSkills",
         "type": "string",
         "source": "env", "default": empty},
        # Email
        {"key": "email_address", "label": "邮箱地址", "group": "Email", "type": "string",
         "source": "env", "default": empty},
        {"key": "email_token", "label": "邮箱Token", "group": "Email", "type": "password",
         "sensitive": True, "source": "env", "default": empty},
        # Features
        {"key": "context_engine_enabled", "label": "上下文压缩", "group": "Features",
         "type": "toggle", "source": "yaml", "default": "false"},
        {"key": "permissions_enabled", "label": "权限管控", "group": "Features",
         "type": "toggle", "source": "yaml", "default": "false"},
        {"key": "memory_forbidden_enabled", "label": "敏感信息过滤", "group": "Features",
         "type": "toggle", "source": "yaml", "default": "false"},
        {"key": "preferred_language", "label": "显示语言", "group": "Features", "type": "select",
         "options": ["zh", "en"], "source": "yaml", "default": "zh"},
        {"key": "evolution_auto_scan", "label": "自动扫描技能", "group": "Features",
         "type": "toggle", "source": "env", "default": "false"},
        # Auto-Harness (定时任务配置) - 合并为三项
        {"key": "auto_harness_git_user_name", "label": "用户名", "group": "Auto-Harness",
         "type": "string", "source": "yaml", "default": empty,
         "description": "GitCode用户名，用于 git commit、创建 PR"},
        {"key": "auto_harness_git_user_email", "label": "邮箱", "group": "Auto-Harness",
         "type": "string", "source": "yaml", "default": empty,
         "description": "GitCode用户邮箱，用于 git commit"},
        {"key": "auto_harness_gitcode_access_token", "label": "GitCode Access Token", "group": "Auto-Harness",
         "type": "password", "sensitive": True, "source": "yaml", "default": empty,
         "description": "GitCode Access token，也可通过环境变量 GITCODE_ACCESS_TOKEN 配置"},
    ]


def _normalize_provider_value(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        return normalized

    available_model_providers = [provider.value for provider in ProviderType]
    lookup = {provider.lower(): provider for provider in available_model_providers}
    return lookup.get(normalized.lower(), normalized)



async def _clear_agent_config_cache(agent_client=None) -> None:
    try:
        if agent_client is not None:
            from jiuwenswarm.common.e2a.gateway_normalize import e2a_from_agent_fields
            from jiuwenswarm.common.schema.message import ReqMethod
            import uuid

            env = e2a_from_agent_fields(
                request_id=f"cfg-reload-{uuid.uuid4().hex[:8]}",
                channel_id="",
                req_method=ReqMethod.AGENT_RELOAD_CONFIG,
            )
            await agent_client.send_request(env)
        else:
            get_config()
    except Exception as e:  # noqa: BLE001
        logger.debug("[cli config.set] clear agent config cache skipped: %s", e)


def _persist_env_updates(updates: dict[str, str]) -> None:
    from jiuwenswarm.common.utils import get_env_file

    env_path = get_env_file()
    if not updates:
        return
    try:
        lines: list[str] = []
        if env_path.is_file():
            with open(env_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        new_lines: list[str] = []
        for line in lines:
            stripped = line.strip()
            found = False
            for env_key, value in updates.items():
                if stripped.startswith(env_key + "="):
                    new_lines.append(
                        f'{env_key}="{value}"\n' if value else f"{env_key}=\n"
                    )
                    found = True
                    break
            if not found:
                new_lines.append(line)
        for env_key, value in updates.items():
            if not any(s.strip().startswith(env_key + "=") for s in new_lines):
                new_lines.append(f'{env_key}="{value}"\n' if value else f"{env_key}=\n")
        env_path.parent.mkdir(parents=True, exist_ok=True)
        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
    except OSError as e:
        logger.warning("[cli config.set] 写回 .env 失败: %s", e)


def _load_env_from_file() -> dict[str, str]:
    """从 .env 文件读取环境变量值（不从当前 os.environ 读取）。"""
    from jiuwenswarm.common.utils import get_env_file

    env_path = get_env_file()
    result = {}
    if not env_path.is_file():
        return result
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if "=" in stripped:
                    key, _, val = stripped.partition("=")
                    val = val.strip('"').strip("'")
                    result[key] = val
    except OSError:
        pass
    return result


def register_cli_handlers(bind: CliHandlersBindParams) -> None:
    channel = bind.channel
    agent_client = bind.agent_client
    on_config_saved = bind.on_config_saved
    path = bind.path
    cron_controller_ref = bind.cron_controller

    async def _config_get(ws, req_id, params, session_id):
        payload = {
            param_key: (os.getenv(env_key) or "")
            for param_key, env_key in _CLI_CONFIG_SET_ENV_MAP.items()
        }
        payload["app_version"] = __version__
        try:
            raw = get_config_raw()
            for key, val in payload.items():
                from jiuwenswarm.extensions import ExtensionRegistry

                crypto_provider = ExtensionRegistry.get_instance().get_crypto_provider()
                if (
                    "api_key" in key.lower() or "token" in key.lower()
                ) and crypto_provider:
                    payload[key] = crypto_provider.decrypt(val)
            ctx_cfg = (raw.get("react") or {}).get("context_engine_config") or {}
            payload["context_engine_enabled"] = (
                "true" if ctx_cfg.get("enabled", False) else "false"
            )
            perm_cfg = raw.get("permissions") or {}
            payload["permissions_enabled"] = (
                "true" if perm_cfg.get("enabled", False) else "false"
            )
            mem_cfg = (raw.get("memory") or {}).get("forbidden_memory_definition") or {}
            payload["memory_forbidden_enabled"] = (
                "true" if mem_cfg.get("enabled", False) else "false"
            )
            payload["preferred_language"] = raw.get("preferred_language") or "zh"

            # Resolve model-related fields from config.yaml.
            # When models.defaults list is in use, it is the canonical source
            # for the current model. Environment variables may be stale if the
            # model was switched via /model or Web UI without restarting gateway.
            try:
                _default_models = get_default_models()
                if _default_models:
                    _current = _default_models[0]
                    _mcc = _current.get("model_client_config") or {}
                    _model_overrides = {
                        "model": _mcc.get("model_name"),
                        "model_provider": _mcc.get("client_provider"),
                        "api_base": _mcc.get("api_base"),
                        "api_key": _mcc.get("api_key"),
                    }
                    for _k, _v in _model_overrides.items():
                        if _v:
                            payload[_k] = str(_v)
            except Exception as e:
                logger.warning("[config.get] Failed to resolve default model config: %s", e)

            # Resolve multimodal model configs (vision, video, audio)
            _multimodal_sections = {
                "vision": {
                    "vision_model": "model_name",
                    "vision_provider": "client_provider",
                    "vision_api_base": "api_base",
                    "vision_api_key": "api_key",
                },
                "video": {
                    "video_model": "model_name",
                    "video_provider": "client_provider",
                    "video_api_base": "api_base",
                    "video_api_key": "api_key",
                },
                "audio": {
                    "audio_model": "model_name",
                    "audio_provider": "client_provider",
                    "audio_api_base": "api_base",
                    "audio_api_key": "api_key",
                },
            }
            for _section_name, _key_map in _multimodal_sections.items():
                try:
                    _section = (raw.get("models") or {}).get(_section_name)
                    if isinstance(_section, dict):
                        _smcc = _section.get("model_client_config") or {}
                        for _pk, _yk in _key_map.items():
                            if not payload.get(_pk):
                                _resolved = resolve_env_vars(str(_smcc.get(_yk, ""))) if _smcc.get(_yk) else ""
                                if _resolved:
                                    payload[_pk] = _resolved
                except Exception as e:
                    logger.warning("[config.get] Failed to resolve %s model config: %s", _section_name, e)
        except Exception:
            payload.setdefault("context_engine_enabled", "false")
            payload.setdefault("permissions_enabled", "false")
            payload.setdefault("memory_forbidden_enabled", "false")
            payload.setdefault("preferred_language", "zh")
        
        # Auto-Harness config values (from ~/.jiuwenclaw/auto-harness/config.yaml)
        # 合并显示：用户名、邮箱、Access Token 三项
        try:
            ah_config = _get_auto_harness_config()
            git_cfg = ah_config.get("git") or {}
            gitcode_cfg = ah_config.get("gitcode") or {}
            payload["auto_harness_git_user_name"] = git_cfg.get("user_name") or ""
            payload["auto_harness_git_user_email"] = git_cfg.get("user_email") or ""
            # Check env var first for access_token
            ah_token = os.getenv("GITCODE_ACCESS_TOKEN") or gitcode_cfg.get("access_token") or ""
            payload["auto_harness_gitcode_access_token"] = ah_token
        except Exception:
            payload.setdefault("auto_harness_git_user_name", "")
            payload.setdefault("auto_harness_git_user_email", "")
            payload.setdefault("auto_harness_gitcode_access_token", "")

        payload["schema"] = _build_config_schema()
        await channel.send_response(ws, req_id, ok=True, payload=payload)

    async def _config_set(ws, req_id, params, session_id):
        if not isinstance(params, dict):
            await channel.send_response(
                ws, req_id, ok=False, error="params must be object", code="BAD_REQUEST"
            )
            return
        for key, val in params.items():
            from jiuwenswarm.extensions import ExtensionRegistry

            crypto_provider = ExtensionRegistry.get_instance().get_crypto_provider()
            if ("api_key" in key.lower() or "token" in key.lower()) and crypto_provider:
                params[key] = crypto_provider.encrypt(val)

        env_updates: dict[str, str] = {}
        yaml_updated: list[str] = []
        available_model_providers = [provider.value for provider in ProviderType]

        for param_key, env_key in _CLI_CONFIG_SET_ENV_MAP.items():
            if param_key not in params:
                continue
            val = params[param_key]
            if param_key.endswith("_provider") and val:
                val = _normalize_provider_value(str(val))
                params[param_key] = val
            if (
                param_key.endswith("_provider")
                and val
                and val not in available_model_providers
            ):
                await channel.send_response(
                    ws,
                    req_id,
                    ok=False,
                    error=f"Model provider must in: {available_model_providers} ",
                    code="BAD_REQUEST",
                )
                return
            env_updates[env_key] = "" if val is None else str(val).strip()

        for param_key, setter in _CLI_CONFIG_YAML_SETTERS.items():
            if param_key not in params:
                continue
            raw_value = str(params[param_key]).strip()
            if param_key == "preferred_language":
                normalized_lang = raw_value.lower()
                if normalized_lang not in _PREFERRED_LANGUAGE_OPTIONS:
                    await channel.send_response(
                        ws,
                        req_id,
                        ok=False,
                        error=(
                            f"preferred_language must be one of "
                            f"{list(_PREFERRED_LANGUAGE_OPTIONS)}"
                        ),
                        code="BAD_REQUEST",
                    )
                    return
            try:
                if param_key == "preferred_language":
                    setter(raw_value)
                elif param_key.startswith("auto_harness_"):
                    # Auto-harness config items are strings, not toggles
                    setter(raw_value)
                else:
                    parsed = raw_value.lower() in ("true", "1", "yes")
                    setter(parsed)
                yaml_updated.append(param_key)
            except Exception as e:
                logger.warning(
                    "[cli config.set] 写回 config.yaml 失败 %s: %s", param_key, e
                )

        for env_key, value in env_updates.items():
            os.environ[env_key] = value
        # env 变量直接写 os.environ 立即生效；YAML 改动需要 agent 重启/热重载才生效
        applied_without_restart = not yaml_updated

        if env_updates:
            _persist_env_updates(env_updates)
        if yaml_updated:
            real_client = (
                agent_client.get("value")
                if isinstance(agent_client, dict)
                else agent_client
            )
            await _clear_agent_config_cache(real_client)

        updated_param_keys = [
            k for k, e in _CLI_CONFIG_SET_ENV_MAP.items() if e in env_updates
        ] + yaml_updated

        # 先回包再执行 on_config_saved（含 Agent 热重载），
        # 避免 WebSocket 长时间无响应、CLI 误以为无反馈。
        await channel.send_response(
            ws,
            req_id,
            ok=True,
            payload={
                "updated": updated_param_keys,
                "applied_without_restart": applied_without_restart,
            },
        )

        if env_updates or yaml_updated:
            if on_config_saved:
                try:
                    config_payload = get_config()
                    callback_result = on_config_saved(
                        set(env_updates.keys()) | set(yaml_updated),
                        env_updates=dict(env_updates),
                        config_payload=config_payload,
                    )
                    if inspect.isawaitable(callback_result):
                        await callback_result
                except Exception as e:  # noqa: BLE001
                    logger.warning("[cli config.set] on_config_saved failed: %s", e)

    async def _config_validate_model(ws, req_id, params, session_id):
        if not isinstance(params, dict):
            await channel.send_response(
                ws, req_id, ok=False, error="params must be object", code="BAD_REQUEST"
            )
            return

        api_base = str(params.get("api_base") or "").strip()
        api_key = str(params.get("api_key") or "").strip()
        model = str(params.get("model") or "").strip()
        model_provider = _normalize_provider_value(str(params.get("model_provider") or ""))
        verify_ssl = bool(params.get("verify_ssl", False))

        if not all([api_base, api_key, model, model_provider]):
            await channel.send_response(
                ws,
                req_id,
                ok=False,
                error="api_base, api_key, model, and model_provider are required",
                code="BAD_REQUEST",
            )
            return

        available_model_providers = [provider.value for provider in ProviderType]
        if model_provider not in available_model_providers:
            await channel.send_response(
                ws,
                req_id,
                ok=False,
                error=f"Model provider must be one of: {available_model_providers}",
                code="BAD_REQUEST",
            )
            return

        if api_base.endswith("/chat/completions"):
            api_base = api_base.rsplit("/chat/completions", 1)[0]
        api_base = api_base.rstrip("/")

        model_request_config = ModelRequestConfig(model=model, temperature=0)
        model_client_config = ModelClientConfig(
            client_id="config-validate",
            client_provider=model_provider,
            api_key=api_key,
            api_base=api_base,
            timeout=25.0,
            max_retries=0,
            verify_ssl=verify_ssl,
        )
        llm = Model(
            model_config=model_request_config,
            model_client_config=model_client_config,
        )

        async def _probe(max_tokens: int):
            return await llm.invoke(
                [{"role": "user", "content": "Hi"}],
                max_tokens=max_tokens,
                temperature=0,
            )

        try:
            try:
                response = await _probe(1)
            except Exception as first_exc:  # noqa: BLE001
                logger.info(
                    "[cli config.validate_model] max_tokens=1 failed, retrying with 16: %s",
                    first_exc,
                )
                response = await _probe(16)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[cli config.validate_model] LLM probe failed: %s", exc)
            await channel.send_response(
                ws,
                req_id,
                ok=False,
                error=str(exc).strip() or "LLM request failed",
                code="LLM_ERROR",
            )
            return

        if hasattr(response, "content"):
            content = response.content
        elif isinstance(response, dict):
            content = response.get("content", "")
        else:
            content = str(response)

        if not (isinstance(content, str) and content.strip()):
            await channel.send_response(
                ws,
                req_id,
                ok=False,
                error="Empty response from model",
                code="LLM_ERROR",
            )
            return

        await channel.send_response(
            ws,
            req_id,
            ok=True,
            payload={
                "provider": model_provider,
                "model": model,
                "response": content.strip(),
            },
        )

    async def _session_list(ws, req_id, params, session_id):
        from jiuwenswarm.common.e2a.gateway_normalize import e2a_from_agent_fields
        from jiuwenswarm.common.schema.message import ReqMethod

        limit = 20
        if isinstance(params, dict):
            raw_limit = params.get("limit")
            if isinstance(raw_limit, int):
                limit = raw_limit
            elif isinstance(raw_limit, str) and raw_limit.strip().isdigit():
                limit = int(raw_limit.strip())
        limit = max(1, min(limit, 200))

        real_client = (
            agent_client.get("value")
            if isinstance(agent_client, dict)
            else agent_client
        )
        if real_client is None:
            await channel.send_response(
                ws, req_id, ok=True, payload={"sessions": []}
            )
            return
        env = e2a_from_agent_fields(
            request_id=req_id,
            channel_id="tui",
            session_id=session_id,
            req_method=ReqMethod.SESSION_LIST,
            params=params or {},
            is_stream=False,
            timestamp=time.time(),
        )
        resp = await real_client.send_request(env)
        if not resp.ok:
            await channel.send_response(ws, req_id, ok=False, error="session.list failed")
            return
        all_sessions = (
            resp.payload.get("sessions", [])
            if isinstance(resp.payload, dict)
            else []
        )
        cli_sessions = [
            s for s in all_sessions if s.get("channel_id", "") == "tui"
        ][:limit]
        await channel.send_response(ws, req_id, ok=True, payload={"sessions": cli_sessions})

    async def _session_create(ws, req_id, params, session_id):
        from jiuwenswarm.common.utils import get_agent_sessions_dir
        from jiuwenswarm.server.runtime.session.session_metadata import init_session_metadata

        if not isinstance(params, dict):
            await channel.send_response(
                ws, req_id, ok=False, error="params must be object", code="BAD_REQUEST"
            )
            return
        target = str(params.get("session_id") or "").strip()
        if not target:
            await channel.send_response(
                ws, req_id, ok=False, error="session_id is required", code="BAD_REQUEST"
            )
            return
        workspace_session_dir = get_agent_sessions_dir()
        workspace_session_dir.mkdir(parents=True, exist_ok=True)
        session_dir = workspace_session_dir / target
        if session_dir.exists():
            await channel.send_response(
                ws,
                req_id,
                ok=False,
                error="session already exists",
                code="ALREADY_EXISTS",
            )
            return
        session_dir.mkdir()
        # 初始化元数据（与 web channel 对齐）
        init_session_metadata(
            session_id=target,
            channel_id="tui",
            title=str(params.get("title") or "").strip(),
            mode=params.get("mode", "code.normal"),
        )
        # 触发 SessionStart hook
        mh = bind.message_handler
        if mh:
            mh.trigger_session_start_hook(target, source="tui")
        await channel.send_response(ws, req_id, ok=True, payload={"session_id": target})

    async def _session_delete(ws, req_id, params, session_id):
        from jiuwenswarm.common.utils import get_agent_sessions_dir
        from jiuwenswarm.server.runtime.session.session_metadata import get_session_metadata
        from jiuwenswarm.common.e2a.gateway_normalize import e2a_from_agent_fields
        from jiuwenswarm.common.schema.message import ReqMethod

        if not isinstance(params, dict):
            await channel.send_response(
                ws, req_id, ok=False, error="params must be object", code="BAD_REQUEST"
            )
            return
        target = str(params.get("session_id") or "").strip()
        if not target:
            await channel.send_response(
                ws, req_id, ok=False, error="session_id is required", code="BAD_REQUEST"
            )
            return
        real_client = (
            agent_client.get("value")
            if isinstance(agent_client, dict)
            else agent_client
        )
        if real_client is not None:
            try:
                env = e2a_from_agent_fields(
                    request_id=req_id,
                    channel_id="tui",
                    session_id=session_id,
                    req_method=ReqMethod.SESSION_DELETE,
                    params=params,
                    is_stream=False,
                    timestamp=time.time(),
                )
                resp = await real_client.send_request(env)
                if resp.ok:
                    pl = resp.payload if isinstance(resp.payload, dict) else {}
                    await channel.send_response(ws, req_id, ok=True, payload=pl)
                    return
                pl = resp.payload if isinstance(resp.payload, dict) else {}
                err = pl.get("error", "session.delete failed")
                code = pl.get("code") or None
                if isinstance(code, str) and not code.strip():
                    code = None
                await channel.send_response(
                    ws,
                    req_id,
                    ok=False,
                    error=str(err),
                    code=code,
                )
                return
            except Exception as e:
                logger.warning("[cli session.delete] forward to agent failed, fallback local: %s", e)

        metadata = get_session_metadata(target)
        if str(metadata.get("mode") or "").strip().lower() == "team":
            await channel.send_response(
                ws,
                req_id,
                ok=False,
                error="team session delete requires agent server",
                code="AGENT_UNAVAILABLE",
            )
            return
        session_dir = get_agent_sessions_dir() / target
        if not session_dir.exists():
            await channel.send_response(
                ws, req_id, ok=False, error="session not found", code="NOT_FOUND"
            )
            return
        if not session_dir.is_dir():
            await channel.send_response(
                ws,
                req_id,
                ok=False,
                error="session is not a directory",
                code="BAD_REQUEST",
            )
            return
        shutil.rmtree(session_dir)
        await channel.send_response(ws, req_id, ok=True, payload={"session_id": target})

    async def _forward_rewind_e2a(params: ForwardRewindE2AParams) -> bool:
        """Try to forward a rewind request to AgentServer via E2A.

        Returns True if the request was fully handled (success or error response
        sent to the client).  Returns False if E2A is unavailable and the caller
        should fall back to local-only processing.
        """
        from jiuwenswarm.common.e2a.gateway_normalize import e2a_from_agent_fields

        real_client = (
            agent_client.get("value")
            if isinstance(agent_client, dict)
            else agent_client
        )
        if real_client is None:
            return False

        try:
            env = e2a_from_agent_fields(
                request_id=params.req_id,
                channel_id="tui",
                session_id=params.target_sid,
                req_method=params.req_method,
                params={"session_id": params.target_sid, "turn_index": params.turn_index},
                is_stream=False,
                timestamp=time.time(),
            )
            resp = await real_client.send_request(env)
            if resp.ok:
                pl = resp.payload if isinstance(resp.payload, dict) else {}
                await channel.send_response(params.ws, params.req_id, ok=True, payload=pl)
                return True
            pl = resp.payload if isinstance(resp.payload, dict) else {}
            err = pl.get("error", params.error_label)
            code = pl.get("code") or None
            if isinstance(code, str) and not code.strip():
                code = None
            await channel.send_response(params.ws, params.req_id, ok=False, error=str(err), code=code)
            return True
        except Exception as e:
            logger.warning("[cli %s] forward to agent failed, fallback local: %s", params.error_label, e)
            return False

    async def _session_rewind(ws, req_id, params, session_id):
        from jiuwenswarm.agents.harness.common.session_ops_service import rewind_session
        """session.rewind: 优先转发到 AgentServer（同时截断 history + context），失败则 fallback 本地仅截断 history."""
        from jiuwenswarm.common.schema.message import ReqMethod

        if not isinstance(params, dict):
            await channel.send_response(
                ws, req_id, ok=False, error="params must be object", code="BAD_REQUEST"
            )
            return
        target_sid = str(params.get("session_id") or session_id or "").strip()
        turn_index = params.get("turn_index")
        if not target_sid:
            await channel.send_response(
                ws, req_id, ok=False, error="session_id is required", code="BAD_REQUEST"
            )
            return
        if turn_index is None:
            await channel.send_response(
                ws, req_id, ok=False, error="turn_index is required", code="BAD_REQUEST"
            )
            return
        try:
            turn_index = int(turn_index)
        except (ValueError, TypeError):
            await channel.send_response(
                ws, req_id, ok=False, error="turn_index must be an integer", code="BAD_REQUEST"
            )
            return

        # 优先通过 E2A 转发到 AgentServer（同时处理 history + context 截断）
        if await _forward_rewind_e2a(
            ForwardRewindE2AParams(
                ws=ws,
                req_id=req_id,
                target_sid=target_sid,
                turn_index=turn_index,
                req_method=ReqMethod.SESSION_REWIND,
                error_label="session.rewind failed",
            )
        ):
            return

        # Fallback: 仅本地截断 history.json（不含 context 截断）
        from jiuwenswarm.agents.harness.common.session_ops_service import rewind_session
        try:
            result = rewind_session(session_id=target_sid, turn_index=turn_index)
            await channel.send_response(ws, req_id, ok=True, payload=result)
        except ValueError as e:
            await channel.send_response(ws, req_id, ok=False, error=str(e), code="BAD_REQUEST")
        except Exception as e:
            await channel.send_response(ws, req_id, ok=False, error=str(e), code="INTERNAL_ERROR")

    async def _history_list_turns(ws, req_id, params, session_id):
        from jiuwenswarm.agents.harness.common.session_ops_service import list_session_turns

        if not isinstance(params, dict):
            params = {}
        target_sid = str(params.get("session_id") or session_id or "").strip()
        if not target_sid:
            await channel.send_response(
                ws, req_id, ok=False, error="session_id is required", code="BAD_REQUEST"
            )
            return
        try:
            result = list_session_turns(session_id=target_sid)
            await channel.send_response(ws, req_id, ok=True, payload=result)
        except Exception as e:
            await channel.send_response(ws, req_id, ok=False, error=str(e), code="INTERNAL_ERROR")

    async def _session_rewind_and_restore(ws, req_id, params, session_id):
        """session.rewind_and_restore: 截断对话 + 恢复文件."""
        from jiuwenswarm.agents.harness.common.session_ops_service import (
            restore_session_files,
            rewind_session,
        )
        """session.rewind_and_restore: 优先转发到 AgentServer（history + context + 文件恢复），失败则 fallback 本地."""
        from jiuwenswarm.common.schema.message import ReqMethod

        if not isinstance(params, dict):
            await channel.send_response(
                ws, req_id, ok=False, error="params must be object", code="BAD_REQUEST"
            )
            return
        target_sid = str(params.get("session_id") or session_id or "").strip()
        turn_index = params.get("turn_index")
        if not target_sid:
            await channel.send_response(
                ws, req_id, ok=False, error="session_id is required", code="BAD_REQUEST"
            )
            return
        if turn_index is None:
            await channel.send_response(
                ws, req_id, ok=False, error="turn_index is required", code="BAD_REQUEST"
            )
            return
        try:
            turn_index = int(turn_index)
        except (ValueError, TypeError):
            await channel.send_response(
                ws, req_id, ok=False, error="turn_index must be an integer", code="BAD_REQUEST"
            )
            return

        # 优先通过 E2A 转发到 AgentServer
        if await _forward_rewind_e2a(
            ForwardRewindE2AParams(
                ws=ws,
                req_id=req_id,
                target_sid=target_sid,
                turn_index=turn_index,
                req_method=ReqMethod.SESSION_REWIND_AND_RESTORE,
                error_label="session.rewind_and_restore failed",
            )
        ):
            return

        # Fallback: 仅本地操作
        from jiuwenswarm.agents.harness.common.session_ops_service import (
            restore_session_files,
            rewind_session,
        )
        try:
            restore_result = restore_session_files(session_id=target_sid, turn_index=turn_index)
            rewind_result = rewind_session(session_id=target_sid, turn_index=turn_index)
            combined = {
                **rewind_result,
                "restored_files": restore_result.get("restored_files", []),
                "deleted_files": restore_result.get("deleted_files", []),
                "restore_errors": restore_result.get("errors", []),
            }
            await channel.send_response(ws, req_id, ok=True, payload=combined)
        except ValueError as e:
            await channel.send_response(ws, req_id, ok=False, error=str(e), code="BAD_REQUEST")
        except Exception as e:
            await channel.send_response(ws, req_id, ok=False, error=str(e), code="INTERNAL_ERROR")

    async def _session_restore_files(ws, req_id, params, session_id):
        """session.restore_files: 仅恢复文件，不截断对话."""
        from jiuwenswarm.agents.harness.common.session_ops_service import restore_session_files

        if not isinstance(params, dict):
            await channel.send_response(
                ws, req_id, ok=False, error="params must be object", code="BAD_REQUEST"
            )
            return
        target_sid = str(params.get("session_id") or session_id or "").strip()
        turn_index = params.get("turn_index")
        if not target_sid:
            await channel.send_response(
                ws, req_id, ok=False, error="session_id is required", code="BAD_REQUEST"
            )
            return
        if turn_index is None:
            await channel.send_response(
                ws, req_id, ok=False, error="turn_index is required", code="BAD_REQUEST"
            )
            return
        try:
            turn_index = int(turn_index)
        except (ValueError, TypeError):
            await channel.send_response(
                ws, req_id, ok=False, error="turn_index must be an integer", code="BAD_REQUEST"
            )
            return
        try:
            result = restore_session_files(session_id=target_sid, turn_index=turn_index)
            await channel.send_response(ws, req_id, ok=True, payload=result)
        except ValueError as e:
            await channel.send_response(ws, req_id, ok=False, error=str(e), code="BAD_REQUEST")
        except Exception as e:
            await channel.send_response(ws, req_id, ok=False, error=str(e), code="INTERNAL_ERROR")

    async def _session_rename(ws, req_id, params, session_id):
        """优先经 E2A 转发至 AgentWebSocketServer._handle_session_rename；无 agent 或转发失败时本地回退。"""
        from jiuwenswarm.server.runtime.session.session_rename import apply_session_rename
        from jiuwenswarm.common.e2a.gateway_normalize import e2a_from_agent_fields
        from jiuwenswarm.common.schema.message import ReqMethod

        real_client = (
            agent_client.get("value")
            if isinstance(agent_client, dict)
            else agent_client
        )
        if real_client is not None:
            try:
                env = e2a_from_agent_fields(
                    request_id=req_id,
                    channel_id="tui",
                    session_id=session_id,
                    req_method=ReqMethod.SESSION_RENAME,
                    params=params if isinstance(params, dict) else {},
                    is_stream=False,
                    timestamp=time.time(),
                )
                resp = await real_client.send_request(env)
                if resp.ok:
                    pl = resp.payload if isinstance(resp.payload, dict) else {}
                    await channel.send_response(ws, req_id, ok=True, payload=pl)
                    return
                pl = resp.payload if isinstance(resp.payload, dict) else {}
                err = pl.get("error", "session.rename failed")
                code = pl.get("code") or None
                if isinstance(code, str) and not code.strip():
                    code = None
                await channel.send_response(
                    ws, req_id, ok=False, error=str(err), code=code
                )
                return
            except Exception as e:
                logger.warning(
                    "[cli session.rename] forward to agent failed, fallback local: %s",
                    e,
                )

        ok, payload, err, code = apply_session_rename(
            params,
            session_id,
            init_channel_id="tui",
        )
        if ok:
            await channel.send_response(ws, req_id, ok=True, payload=payload or {})
        else:
            await channel.send_response(
                ws,
                req_id,
                ok=False,
                error=err or "session.rename failed",
                code=code,
            )

    async def _session_color_set(ws, req_id, params, session_id):
        """设置 session 的 accent_color。"""
        from jiuwenswarm.server.runtime.session.session_metadata import (
            get_session_metadata,
            _write_metadata_sync,
            _read_metadata,
        )

        if not isinstance(params, dict):
            await channel.send_response(
                ws, req_id, ok=False, error="params must be object", code="BAD_REQUEST"
            )
            return
        target = str(params.get("session_id") or session_id).strip()
        if not target:
            await channel.send_response(
                ws, req_id, ok=False, error="session_id is required", code="BAD_REQUEST"
            )
            return

        color = params.get("color")
        valid_colors = ["default", "blue", "green", "pink", "purple", "red", "yellow"]

        if color is None:
            # 查询模式
            metadata = get_session_metadata(target)
            accent_color = metadata.get("accent_color", "default") if metadata else "default"
            await channel.send_response(
                ws, req_id, ok=True,
                payload={"session_id": target, "accent_color": accent_color}
            )
            return

        if str(color) not in valid_colors:
            await channel.send_response(
                ws, req_id, ok=False, error=f"invalid color: {color}", code="BAD_REQUEST"
            )
            return

        # 设置模式 - 同步写入确保跨进程可见
        metadata = _read_metadata(target)
        metadata["accent_color"] = str(color)
        _write_metadata_sync(target, metadata)
        await channel.send_response(
            ws, req_id, ok=True,
            payload={"session_id": target, "accent_color": str(color)}
        )

    async def _chat_send(ws, req_id, params, session_id):
        await channel.send_response(
            ws, req_id, ok=True, payload={"accepted": True, "session_id": session_id}
        )

    async def _chat_resume(ws, req_id, params, session_id):
        await channel.send_response(
            ws, req_id, ok=True, payload={"accepted": True, "session_id": session_id}
        )

    async def _chat_interrupt(ws, req_id, params, session_id):
        intent = params.get("intent") if isinstance(params, dict) else None
        payload = {"accepted": True, "session_id": session_id}
        if isinstance(intent, str) and intent:
            payload["intent"] = intent
        await channel.send_response(ws, req_id, ok=True, payload=payload)

    async def _chat_user_answer(ws, req_id, params, session_id):
        payload = {"accepted": True, "session_id": session_id}
        request_id = params.get("request_id") if isinstance(params, dict) else None
        if isinstance(request_id, str) and request_id:
            payload["request_id"] = request_id
        await channel.send_response(ws, req_id, ok=True, payload=payload)

    async def _history_get(ws, req_id, params, session_id):
        payload = {"accepted": True, "session_id": session_id}
        if isinstance(params, dict):
            if "session_id" in params:
                payload["session_id"] = params.get("session_id")
            if "page_idx" in params:
                payload["page_idx"] = params.get("page_idx")
        await channel.send_response(ws, req_id, ok=True, payload=payload)

    async def _command_model(ws, req_id, params, session_id):
        from jiuwenswarm.common.e2a.gateway_normalize import e2a_from_agent_fields
        from jiuwenswarm.common.schema.message import ReqMethod

        if not isinstance(params, dict):
            params = {}
        action = params.get("action")
        model_name = params.get("model")

        real_client = (
            agent_client.get("value")
            if isinstance(agent_client, dict)
            else agent_client
        )
        if real_client is None:
            await channel.send_response(
                ws, req_id, ok=False, error="agent client not available"
            )
            return

        if action == "add_model":
            target = str(params.get("target", "")).strip()
            configs = params.get("config", {})
            if not target:
                await channel.send_response(
                    ws, req_id, ok=False, error="Target model name (target) is required"
                )
                return
            client_cfg = {}
            key_map = {
                "model": "model_name",
                "provider": "client_provider",
                "api_key": "api_key",
                "key": "api_key",
                "api_base": "api_base",
                "url": "api_base",
                "base_url": "api_base",
                "timeout": "timeout",
                "verify_ssl": "verify_ssl",
                "ssl_cert": "ssl_cert",
                "alias": "alias",
            }
            # target 可能是 "model=gpt-5" 形式（前端把第一个 key=value 当作 name 参数解析）
            if "=" in target:
                _eq = target.index("=")
                _k, _v = target[:_eq].strip().lower(), target[_eq + 1:].strip()
                client_cfg[key_map.get(_k, _k)] = _v
                if _k in ("model", "model_name"):
                    target = _v
            for k, v in configs.items():
                mapped_k = key_map.get(k.lower(), k)
                client_cfg[mapped_k] = v
            if "verify_ssl" not in client_cfg:
                client_cfg["verify_ssl"] = False
            if "timeout" not in client_cfg:
                client_cfg["timeout"] = 1800
            model_cfg_obj = configs.get("model_config_obj", {})
            if not model_cfg_obj:
                model_cfg_obj = {"temperature": 0.95}
            # target 作为 model_name 的回退：若未通过 model= 参数指定，则以 target 为准
            if not client_cfg.get("model_name"):
                client_cfg["model_name"] = target
            effective_name = client_cfg["model_name"]

            # alias 为顶层字段，从 client_cfg 中提取；提前计算最终值确保唯一性校验基于实际存储值
            entry_alias = client_cfg.pop("alias", None)
            effective_alias = str(entry_alias).strip() if entry_alias else ""

            new_entry = {
                "model_client_config": client_cfg,
                "model_config_obj": model_cfg_obj,
            }
            new_entry["alias"] = effective_alias
            try:
                # 统一使用 defaults 列表格式（旧格式自动迁移）
                _raw_defs = ensure_defaults_list_in_config()
                # 与web端一致：允许同名 model_name 多条目（不同 api_key/api_base 即为不同配置），
                # 仅拒绝完全相同的配置重复添加（model_name + api_base + api_key 全部一致）
                _is_duplicate = False
                _effective_api_base = resolve_env_vars(str(client_cfg.get("api_base", "")))
                _effective_api_key = resolve_env_vars(str(client_cfg.get("api_key", "")))
                for _e in _raw_defs:
                    if not isinstance(_e, dict):
                        continue
                    _emn = resolve_env_vars(str((_e.get("model_client_config") or {}).get("model_name", "")))
                    _eab = resolve_env_vars(str((_e.get("model_client_config") or {}).get("api_base", "")))
                    _eak = resolve_env_vars(str((_e.get("model_client_config") or {}).get("api_key", "")))
                    _ea = resolve_env_vars(str(_e.get("alias", ""))) if _e.get("alias") else ""
                    _same_config = _emn == effective_name and _eab == _effective_api_base and _eak == _effective_api_key
                    _same_alias = _ea and _ea == effective_alias
                    if _same_config or _same_alias:
                        _is_duplicate = True
                        break
                # 完全重复时拒绝添加
                if _is_duplicate:
                    await channel.send_response(
                        ws, req_id, ok=False,
                        error=f"Model '{effective_name}' with the same api_base and api_key already exists",
                    )
                    return
                # 新增模型时校验四个必填字段
                _required = {
                    "api_key": "api_key",
                    "api_base": "api_base",
                    "model_name": "model_name/model",
                    "client_provider": "client_provider/provider",
                }
                _missing = []
                for field, display in _required.items():
                    _val = resolve_env_vars(str(client_cfg.get(field, "")))
                    if not _val:
                        _missing.append(display)
                if _missing:
                    _err_msg = (
                        f"Failed to add model '{effective_name}'. "
                        f"Required fields missing: {', '.join(_missing)}. "
                        f"Usage: /model add <name> "
                        f"api_base=xxx api_key=xxx "
                        f"model=<name> provider=<provider>"
                    )
                    await channel.send_response(
                        ws, req_id, ok=False,
                        error=_err_msg,
                    )
                    return
                # alias 唯一性校验（仅在 alias 非空时执行）
                if effective_alias:
                    for _e in _raw_defs:
                        if not isinstance(_e, dict):
                            continue
                        _emn = resolve_env_vars(str((_e.get("model_client_config") or {}).get("model_name", "")))
                        _ea = resolve_env_vars(str(_e.get("alias", "")))
                        if _ea == effective_alias:
                            await channel.send_response(
                                ws, req_id, ok=False,
                                error=f"Alias '{effective_alias}' is already used by model '{_emn}'",
                            )
                            return
                        if _emn == effective_alias:
                            await channel.send_response(
                                ws, req_id, ok=False,
                                error=f"Alias '{effective_alias}' conflicts with model name '{_emn}'",
                            )
                            return
                _raw_defs.append(new_entry)
                update_default_models_in_config(_raw_defs)
                logger.info(
                    "[cli command.model] 新增模型: name=%s, "
                    "client_cfg=%s, model_config_obj=%s",
                    effective_name, client_cfg, model_cfg_obj,
                )
            except Exception as e:
                await channel.send_response(ws, req_id, ok=False, error=str(e))
                return
            _reload_env = e2a_from_agent_fields(
                request_id=req_id,
                channel_id="cli",
                session_id=session_id,
                req_method=ReqMethod.AGENT_RELOAD_CONFIG,
                params={},
                is_stream=False,
                timestamp=time.time(),
            )
            await real_client.send_request(_reload_env)
            await channel.send_response(
                ws, req_id, ok=True,
                payload={"type": "model_added", "name": target},
            )
            return

        if not model_name or not str(model_name).strip():
            names = get_model_names()
            logger.info(
                "[cli command.model] 列出模型: names=%s, current=%s",
                names,
                os.getenv("MODEL_NAME", "unknown"),
            )
            env = e2a_from_agent_fields(
                request_id=req_id,
                channel_id="cli",
                session_id=session_id,
                req_method=ReqMethod.COMMAND_MODEL,
                params={},
                is_stream=False,
                timestamp=time.time(),
            )
            resp = await real_client.send_request(env)
            payload = resp.payload if resp.ok else {}
            payload["available_models"] = names
            _raw = get_config_raw()
            _defs = (_raw.get("models") or {}).get("defaults")
            if isinstance(_defs, list) and _defs:
                _first_name = resolve_env_vars(str((_defs[0].get("model_client_config") or {}).get("model_name", "")))
                _first_alias = resolve_env_vars(str(_defs[0].get("alias", ""))) if _defs[0].get("alias") else ""
                payload["current"] = _first_alias or _first_name or os.getenv("MODEL_NAME", "unknown")
                payload["current_model_name"] = _first_name or os.getenv("MODEL_NAME", "unknown")
                payload["models"] = [
                    {
                        "name": resolve_env_vars(str(e.get("alias", ""))) or
                                resolve_env_vars(str((e.get("model_client_config") or {}).get("model_name", ""))),
                        "model_name": resolve_env_vars(str((e.get("model_client_config") or {}).get("model_name", ""))),
                        "api_base": resolve_env_vars(str((e.get("model_client_config") or {}).get("api_base", ""))),
                    }
                    for e in _defs if isinstance(e, dict)
                ]
            else:
                payload["current"] = os.getenv("MODEL_NAME", "unknown")
            await channel.send_response(ws, req_id, ok=True, payload=payload)
            return

        target = str(model_name).strip()
        logger.info("[cli command.model] 切换模型: target=%s", target)
        _raw_defs_check = (get_config_raw().get("models") or {}).get("defaults") or []
        _valid_names: set[str] = set()
        for _e in _raw_defs_check:
            if isinstance(_e, dict):
                _mn = resolve_env_vars(str((_e.get("model_client_config") or {}).get("model_name", "")))
                _al = resolve_env_vars(str(_e.get("alias", ""))) if _e.get("alias") else ""
                if _mn:
                    _valid_names.add(_mn)
                if _al:
                    _valid_names.add(_al)
        if not _valid_names:
            _valid_names = set(get_model_names())
        if target not in _valid_names:
            logger.warning(
                "[cli command.model] 模型不存在: %s, 可用: %s",
                target,
                get_model_names(),
            )
            _avail_parts = []
            for _e in _raw_defs_check:
                if not isinstance(_e, dict):
                    continue
                _mn = resolve_env_vars(str((_e.get("model_client_config") or {}).get("model_name", "")))
                _al = resolve_env_vars(str(_e.get("alias", ""))) if _e.get("alias") else ""
                if _al and _mn and _al != _mn:
                    _avail_parts.append(f"{_al} ({_mn})")
                elif _mn:
                    _avail_parts.append(_mn)
            await channel.send_response(
                ws,
                req_id,
                ok=False,
                error=(
                    f"Model '{target}' not found. "
                    f"Available: {', '.join(_avail_parts) or ', '.join(get_model_names())}"
                ),
            )
            return

        # 统一使用 defaults 列表格式（旧格式自动迁移）
        _raw_defaults = ensure_defaults_list_in_config()
        _target_entry = None
        _target_idx = None
        for _i, _e in enumerate(_raw_defaults):
            if not isinstance(_e, dict):
                continue
            _ename = resolve_env_vars(str((_e.get("model_client_config") or {}).get("model_name", "")))
            _ealias = resolve_env_vars(str(_e.get("alias", ""))) if _e.get("alias") else ""
            if _ename == target or _ealias == target:
                _target_entry = _e
                _target_idx = _i
                break
        _other_entries = [_e for _i, _e in enumerate(_raw_defaults) if _i != _target_idx]
        if _target_entry is None:
            await channel.send_response(ws, req_id, ok=False, error=f"Model '{target}' config not found")
            return
        # 校验必填字段
        _target_mcc = _target_entry.get("model_client_config") or {}
        _missing_fields = []
        for _req_field, _display in [
            ("api_key", "api_key"),
            ("api_base", "api_base"),
            ("model_name", "model_name"),
            ("client_provider", "client_provider"),
        ]:
            _val = resolve_env_vars(str(_target_mcc.get(_req_field, "")))
            if not _val:
                _missing_fields.append(_display)
        if _missing_fields:
            await channel.send_response(
                ws, req_id, ok=False,
                error=f"Model '{target}' missing required config: {', '.join(_missing_fields)}",
            )
            return
        update_default_models_in_config([_target_entry] + _other_entries)
        logger.info("[cli command.model] 切换，已更新 models.defaults 首位: %s", target)
        _reload_env = e2a_from_agent_fields(
            request_id=req_id,
            channel_id="cli",
            session_id=session_id,
            req_method=ReqMethod.AGENT_RELOAD_CONFIG,
            params={},
            is_stream=False,
            timestamp=time.time(),
        )
        await real_client.send_request(_reload_env)
        if on_config_saved:
            try:
                _cb = on_config_saved(set(), env_updates={}, config_payload=get_config())
                if inspect.isawaitable(_cb):
                    await _cb
            except Exception as _e2:
                logger.warning("[cli model.switch] on_config_saved failed: %s", _e2)
        _target_model_name = resolve_env_vars(
            str((_target_entry.get("model_client_config") or {}).get("model_name", target)))
        logger.info("[cli command.model] 切换完成: current=%s", _target_model_name)
        await channel.send_response(ws, req_id, ok=True, payload={
            "current": _target_model_name,
            "requested": target,
            "type": "switched",
            "applied": True,
        })
        return

    async def _models_list(ws, req_id, params, session_id):
        try:
            config = get_config()
            models = get_default_models(config)
            result = []
            for entry in models:
                mcc = entry.get("model_client_config", {})
                mco = entry.get("model_config_obj", {})
                model_name = mcc.get("model_name", "")
                # 解析模型的上下文窗口大小
                context_window_tokens = 0
                try:
                    from openjiuwen.core.context_engine.context.context_utils import ContextUtils
                    context_window_tokens = ContextUtils.resolve_context_max(model_name=model_name)
                except Exception:
                    logger.debug("Failed to resolve context_window_tokens for model %s", model_name, exc_info=True)
                result.append({
                    "model_name": model_name,
                    "api_base": mcc.get("api_base", ""),
                    "api_key": mcc.get("api_key", ""),
                    "model_provider": mcc.get("client_provider", ""),
                    "temperature": mco.get("temperature", 0.95),
                    "alias": entry.get("alias", ""),
                    "context_window_tokens": context_window_tokens,
                })
            active_model = result[0]["model_name"] if result else ""
            await channel.send_response(ws, req_id, ok=True, payload={
                "models": result,
                "active_model": active_model,
            })
        except Exception as exc:
            logger.warning("[models.list] %s", exc)
            await channel.send_response(ws, req_id, ok=False, error=str(exc), code="INTERNAL_ERROR")

    channel.register_local_handler(path, "config.get", _config_get)
    channel.register_local_handler(path, "config.set", _config_set)
    channel.register_local_handler(path, "config.validate_model", _config_validate_model)
    channel.register_local_handler(path, "models.list", _models_list)
    channel.register_local_handler(path, "session.list", _session_list)
    channel.register_local_handler(path, "session.create", _session_create)
    channel.register_local_handler(path, "session.delete", _session_delete)
    channel.register_local_handler(path, "session.rename", _session_rename)
    channel.register_local_handler(path, "session.color_set", _session_color_set)
    channel.register_local_handler(path, "session.rewind", _session_rewind)
    channel.register_local_handler(path, "session.rewind_and_restore", _session_rewind_and_restore)
    channel.register_local_handler(path, "session.restore_files", _session_restore_files)
    channel.register_local_handler(path, "history.list_turns", _history_list_turns)
    channel.register_local_handler(path, "chat.send", _chat_send)
    channel.register_local_handler(path, "chat.resume", _chat_resume)
    channel.register_local_handler(path, "chat.interrupt", _chat_interrupt)
    channel.register_local_handler(path, "chat.user_answer", _chat_user_answer)
    channel.register_local_handler(path, "history.get", _history_get)
    channel.register_local_handler(path, "command.model", _command_model)

    # ── Hooks RPC handlers ─────────────────────────────────────────────
    async def _hooks_list(ws, req_id, params, session_id):
        from jiuwenswarm.common.hooks_config import load_hooks_config
        try:
            hooks_config = load_hooks_config(get_config())
            summary = hooks_config.get_event_summary()
            await channel.send_response(ws, req_id, ok=True,
                                        payload={
                                            "events": summary,
                                            "disable_all_hooks": hooks_config.disable_all_hooks,
                                            "source": "config.yaml",
                                        })
        except Exception as exc:
            await channel.send_response(ws, req_id, ok=False, error=str(exc), code="INTERNAL_ERROR")

    channel.register_local_handler(path, "hooks.list", _hooks_list)

    # ── Memory RPC handlers ────────────────────────────────────────────
    from jiuwenswarm.agents.harness.common.memory_rpc import (
        handle_memory_list,
        handle_memory_edit,
        handle_memory_status,
        handle_memory_toggle,
        handle_memory_open,
    )
    from jiuwenswarm.common.utils import get_agent_workspace_dir

    def _resolve_project_dir(params):
        project_dir = params.get("project_dir")
        if isinstance(project_dir, str) and project_dir:
            return project_dir
        trusted_dirs = params.get("trusted_dirs")
        if isinstance(trusted_dirs, list) and trusted_dirs:
            return str(trusted_dirs[0])
        cwd = params.get("cwd")
        if isinstance(cwd, str) and cwd:
            return cwd
        return None

    async def _memory_list(ws, req_id, params, session_id):
        workspace = str(get_agent_workspace_dir())
        mode = params.get("mode", "plan")
        project_dir = _resolve_project_dir(params)
        if project_dir:
            params = {**params, "project_dir": project_dir}
        try:
            result = await handle_memory_list(workspace, mode, params)
            await channel.send_response(ws, req_id, ok=True, payload=result)
        except Exception as exc:
            logger.warning("[memory.list] %s", exc)
            await channel.send_response(ws, req_id, ok=False, error=str(exc), code="INTERNAL_ERROR")

    async def _memory_edit(ws, req_id, params, session_id):
        workspace = str(get_agent_workspace_dir())
        project_dir = _resolve_project_dir(params)
        if project_dir:
            params = {**params, "project_dir": project_dir}
        try:
            result = await handle_memory_edit(workspace, params)
            await channel.send_response(ws, req_id, ok=True, payload=result)
        except Exception as exc:
            logger.warning("[memory.edit] %s", exc)
            await channel.send_response(ws, req_id, ok=False, error=str(exc), code="INTERNAL_ERROR")

    async def _memory_status(ws, req_id, params, session_id):
        workspace = str(get_agent_workspace_dir())
        mode = params.get("mode", "plan")
        project_dir = _resolve_project_dir(params)
        if project_dir:
            params = {**params, "project_dir": project_dir}
        try:
            result = await handle_memory_status(workspace, mode, params)
            await channel.send_response(ws, req_id, ok=True, payload=result)
        except Exception as exc:
            logger.warning("[memory.status] %s", exc)
            await channel.send_response(ws, req_id, ok=False, error=str(exc), code="INTERNAL_ERROR")

    async def _memory_toggle(ws, req_id, params, session_id):
        workspace = str(get_agent_workspace_dir())
        mode = params.get("mode", "plan")
        try:
            result = await handle_memory_toggle(workspace, mode, params)
            await channel.send_response(ws, req_id, ok=True, payload=result)
        except Exception as exc:
            logger.warning("[memory.toggle] %s", exc)
            await channel.send_response(ws, req_id, ok=False, error=str(exc), code="INTERNAL_ERROR")

    async def _memory_open(ws, req_id, params, session_id):
        workspace = str(get_agent_workspace_dir())
        project_dir = _resolve_project_dir(params)
        if project_dir:
            params = {**params, "project_dir": project_dir}
        try:
            result = await handle_memory_open(workspace, params)
            await channel.send_response(ws, req_id, ok=True, payload=result)
        except Exception as exc:
            logger.warning("[memory.open] %s", exc)
            await channel.send_response(ws, req_id, ok=False, error=str(exc), code="INTERNAL_ERROR")

    channel.register_local_handler(path, "memory.list", _memory_list)
    channel.register_local_handler(path, "memory.edit", _memory_edit)
    channel.register_local_handler(path, "memory.status", _memory_status)
    channel.register_local_handler(path, "memory.toggle", _memory_toggle)
    channel.register_local_handler(path, "memory.open", _memory_open)

    # ── Cron RPC handlers ────────────────────────────────────────────

    def _get_cron():
        """Resolve cron_controller from ref dict or direct instance."""
        if isinstance(cron_controller_ref, dict):
            return cron_controller_ref.get("value")
        return cron_controller_ref

    async def _cron_job_list(ws, req_id, params, session_id):
        cc = _get_cron()
        if cc is None:
            await channel.send_response(ws, req_id, ok=False, error="cron not available", code="INTERNAL_ERROR")
            return
        try:
            jobs = await cc.list_jobs()
            await channel.send_response(ws, req_id, ok=True, payload={"jobs": jobs})
        except Exception as exc:
            logger.warning("[cron.job.list] %s", exc)
            await channel.send_response(ws, req_id, ok=False, error=str(exc), code="INTERNAL_ERROR")

    async def _cron_job_get(ws, req_id, params, session_id):
        cc = _get_cron()
        if cc is None:
            await channel.send_response(ws, req_id, ok=False, error="cron not available", code="INTERNAL_ERROR")
            return
        if not isinstance(params, dict):
            await channel.send_response(ws, req_id, ok=False, error="params must be object", code="BAD_REQUEST")
            return
        job_id = str(params.get("id") or "").strip()
        if not job_id:
            await channel.send_response(ws, req_id, ok=False, error="id is required", code="BAD_REQUEST")
            return
        try:
            job = await cc.get_job(job_id)
            if job is None:
                await channel.send_response(ws, req_id, ok=False, error="job not found", code="NOT_FOUND")
                return
            await channel.send_response(ws, req_id, ok=True, payload={"job": job})
        except Exception as exc:
            logger.warning("[cron.job.get] %s", exc)
            await channel.send_response(ws, req_id, ok=False, error=str(exc), code="INTERNAL_ERROR")

    async def _cron_job_create(ws, req_id, params, session_id):
        cc = _get_cron()
        if cc is None:
            await channel.send_response(ws, req_id, ok=False, error="cron not available", code="INTERNAL_ERROR")
            return
        if not isinstance(params, dict):
            await channel.send_response(ws, req_id, ok=False, error="params must be object", code="BAD_REQUEST")
            return
        try:
            if session_id:
                params["session_id"] = session_id
            job = await cc.create_job(params)
            await channel.send_response(ws, req_id, ok=True, payload={"job": job})
        except Exception as exc:
            logger.warning("[cron.job.create] %s", exc)
            await channel.send_response(ws, req_id, ok=False, error=str(exc), code="BAD_REQUEST")

    async def _cron_job_update(ws, req_id, params, session_id):
        cc = _get_cron()
        if cc is None:
            await channel.send_response(ws, req_id, ok=False, error="cron not available", code="INTERNAL_ERROR")
            return
        if not isinstance(params, dict):
            await channel.send_response(ws, req_id, ok=False, error="params must be object", code="BAD_REQUEST")
            return
        job_id = str(params.get("id") or "").strip()
        patch = params.get("patch") or {}
        if not job_id:
            await channel.send_response(ws, req_id, ok=False, error="id is required", code="BAD_REQUEST")
            return
        if not isinstance(patch, dict):
            await channel.send_response(ws, req_id, ok=False, error="patch must be object", code="BAD_REQUEST")
            return
        try:
            job = await cc.update_job(job_id, patch)
            await channel.send_response(ws, req_id, ok=True, payload={"job": job})
        except KeyError:
            await channel.send_response(ws, req_id, ok=False, error="job not found", code="NOT_FOUND")
        except Exception as exc:
            logger.warning("[cron.job.update] %s", exc)
            await channel.send_response(ws, req_id, ok=False, error=str(exc), code="BAD_REQUEST")

    async def _cron_job_delete(ws, req_id, params, session_id):
        cc = _get_cron()
        if cc is None:
            await channel.send_response(ws, req_id, ok=False, error="cron not available", code="INTERNAL_ERROR")
            return
        if not isinstance(params, dict):
            await channel.send_response(ws, req_id, ok=False, error="params must be object", code="BAD_REQUEST")
            return
        job_id = str(params.get("id") or "").strip()
        if not job_id:
            await channel.send_response(ws, req_id, ok=False, error="id is required", code="BAD_REQUEST")
            return
        try:
            deleted = await cc.delete_job(job_id)
            if not deleted:
                await channel.send_response(ws, req_id, ok=False, error="job not found", code="NOT_FOUND")
                return
            await channel.send_response(ws, req_id, ok=True, payload={"deleted": True})
        except Exception as exc:
            logger.warning("[cron.job.delete] %s", exc)
            await channel.send_response(ws, req_id, ok=False, error=str(exc), code="INTERNAL_ERROR")

    async def _cron_job_toggle(ws, req_id, params, session_id):
        cc = _get_cron()
        if cc is None:
            await channel.send_response(ws, req_id, ok=False, error="cron not available", code="INTERNAL_ERROR")
            return
        if not isinstance(params, dict):
            await channel.send_response(ws, req_id, ok=False, error="params must be object", code="BAD_REQUEST")
            return
        job_id = str(params.get("id") or "").strip()
        enabled = params.get("enabled", None)
        if not job_id:
            await channel.send_response(ws, req_id, ok=False, error="id is required", code="BAD_REQUEST")
            return
        if enabled is None:
            await channel.send_response(ws, req_id, ok=False, error="enabled is required", code="BAD_REQUEST")
            return
        try:
            job = await cc.toggle_job(job_id, bool(enabled))
            await channel.send_response(ws, req_id, ok=True, payload={"job": job})
        except KeyError:
            await channel.send_response(ws, req_id, ok=False, error="job not found", code="NOT_FOUND")
        except Exception as exc:
            logger.warning("[cron.job.toggle] %s", exc)
            await channel.send_response(ws, req_id, ok=False, error=str(exc), code="INTERNAL_ERROR")

    async def _cron_job_preview(ws, req_id, params, session_id):
        cc = _get_cron()
        if cc is None:
            await channel.send_response(ws, req_id, ok=False, error="cron not available", code="INTERNAL_ERROR")
            return
        if not isinstance(params, dict):
            await channel.send_response(ws, req_id, ok=False, error="params must be object", code="BAD_REQUEST")
            return
        job_id = str(params.get("id") or "").strip()
        count = params.get("count", 5)
        if not job_id:
            await channel.send_response(ws, req_id, ok=False, error="id is required", code="BAD_REQUEST")
            return
        try:
            next_runs = await cc.preview_job(job_id, int(count) if count is not None else 5)
            await channel.send_response(ws, req_id, ok=True, payload={"next": next_runs})
        except KeyError:
            await channel.send_response(ws, req_id, ok=False, error="job not found", code="NOT_FOUND")
        except Exception as exc:
            logger.warning("[cron.job.preview] %s", exc)
            await channel.send_response(ws, req_id, ok=False, error=str(exc), code="BAD_REQUEST")

    async def _cron_job_run_now(ws, req_id, params, session_id):
        cc = _get_cron()
        if cc is None:
            await channel.send_response(ws, req_id, ok=False, error="cron not available", code="INTERNAL_ERROR")
            return
        if not isinstance(params, dict):
            await channel.send_response(ws, req_id, ok=False, error="params must be object", code="BAD_REQUEST")
            return
        job_id = str(params.get("id") or "").strip()
        if not job_id:
            await channel.send_response(ws, req_id, ok=False, error="id is required", code="BAD_REQUEST")
            return
        try:
            run_id = await cc.run_now(job_id)
            await channel.send_response(ws, req_id, ok=True, payload={"run_id": run_id})
        except KeyError:
            await channel.send_response(ws, req_id, ok=False, error="job not found", code="NOT_FOUND")
        except Exception as exc:
            logger.warning("[cron.job.run_now] %s", exc)
            await channel.send_response(ws, req_id, ok=False, error=str(exc), code="INTERNAL_ERROR")

    channel.register_local_handler(path, "cron.job.list", _cron_job_list)
    channel.register_local_handler(path, "cron.job.get", _cron_job_get)
    channel.register_local_handler(path, "cron.job.create", _cron_job_create)
    channel.register_local_handler(path, "cron.job.update", _cron_job_update)
    channel.register_local_handler(path, "cron.job.delete", _cron_job_delete)
    channel.register_local_handler(path, "cron.job.toggle", _cron_job_toggle)
    channel.register_local_handler(path, "cron.job.preview", _cron_job_preview)
    channel.register_local_handler(path, "cron.job.run_now", _cron_job_run_now)


def build_cli_route_binding(bind: CliRouteBindParams) -> GatewayRouteBinding:
    def _install(channel: Any) -> None:
        register_cli_handlers(
            CliHandlersBindParams(
                channel=channel,
                agent_client=bind.agent_client,
                message_handler=bind.message_handler,
                on_config_saved=bind.on_config_saved,
                path=bind.path,
                cron_controller=bind.cron_controller,
            )
        )

    async def _tui_disconnect(
        _ws: Any,
        stale_session_keys: list[tuple[str, str]],
        stale_request_keys: list[tuple[str, str]] | None = None,
    ) -> None:
        mh = bind.message_handler
        if mh is None:
            return
        # NOTE: do not early-return on empty stale_session_keys; in-flight streams
        # may still be tracked under stale_request_keys even when _session_to_client
        # was overwritten by a later reconnect on the same session_id.
        request_keys = stale_request_keys or []
        if not stale_session_keys and not request_keys:
            return
        await mh.cancel_agent_sessions_on_disconnect(
            stale_session_keys,
            stale_request_keys=request_keys,
        )

    return GatewayRouteBinding(
        path=bind.path,
        channel_id=bind.channel_id,
        forward_methods=CLI_FORWARD_REQ_METHODS,
        forward_no_local_handler_methods=CLI_FORWARD_NO_LOCAL_HANDLER_METHODS,
        install=_install,
        disconnect_handler=_tui_disconnect,
    )
