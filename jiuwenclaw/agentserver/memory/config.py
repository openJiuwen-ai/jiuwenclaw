# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Memory configuration for JiuWenClaw.

Configuration is loaded from config/config.yaml.
Embedding API settings are in the 'embed' section.
"""

import logging
import os
import re
import copy
from typing import Any, Optional, Dict, List
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from jiuwenclaw.utils import get_config_file, get_agent_workspace_dir

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = str(get_config_file())
DEFAULT_WORKSPACE_DIR = str(get_agent_workspace_dir())

_config_cache: Optional[Dict[str, Any]] = None
_embed_config_db_cache: Optional[Dict[str, Any]] = None
_task_memory_config_db_cache: Optional[Dict[str, Any]] = None
_memory_config_db_cache: Optional[Dict[str, Any]] = None
_memory_config_cache_source: str | None = None

MEMORY_CONFIG_TABLE = "memory_config"
MEMORY_CONFIG_SOURCE_DB = "gateway_db"
MEMORY_CONFIG_SOURCE_YAML = "config.yaml"


def is_enterprise_memory_config_enabled() -> bool:
    """Manager 下发的 memory_config 仅在企业级部署（``AGENT_RUNTIME`` 非空）生效。"""
    return bool(os.getenv("AGENT_RUNTIME", "").strip())


def _resolve_env_vars(value: Any) -> Any:
    """Recursively resolve environment variables in config values."""
    if isinstance(value, str):
        pattern = r'\$\{([^:}]+)(?::-([^}]*))?\}'
        def replace_env(match):
            var_name = match.group(1)
            default = match.group(2) if match.group(2) is not None else ""
            return os.getenv(var_name, default)
        return re.sub(pattern, replace_env, value)
    elif isinstance(value, dict):
        return {k: _resolve_env_vars(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [_resolve_env_vars(item) for item in value]
    else:
        return value


def clear_config_cache() -> None:
    """清除配置缓存，使下次 _load_config() 重新从 config.yaml 读取并解析环境变量."""
    global _config_cache
    _config_cache = None


def clear_memory_config_db_cache() -> None:
    """清除 memory_config 的 DB 缓存，使下次重新从 DB 或 YAML 读取."""
    global _memory_config_db_cache, _memory_config_cache_source
    _memory_config_db_cache = None
    _memory_config_cache_source = None


def _deep_merge_dict(dst: Dict[str, Any], src: Dict[str, Any]) -> None:
    for key, value in src.items():
        if (
            key in dst
            and isinstance(dst[key], dict)
            and isinstance(value, dict)
        ):
            _deep_merge_dict(dst[key], value)
        else:
            dst[key] = copy.deepcopy(value)


def get_memory_config_overlay() -> Dict[str, Any] | None:
    """Return Manager 下发的 memory 段 overlay（非企业级或未下发时为 None）。"""
    if not is_enterprise_memory_config_enabled():
        return None
    if _memory_config_db_cache is not None:
        return copy.deepcopy(_memory_config_db_cache)
    return None


def get_memory_section(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """读取 memory 段。

    企业级（``AGENT_RUNTIME`` 非空）：Gateway DB overlay > config.yaml。
    其他场景：仅 config.yaml。
    """
    base_cfg = config if config is not None else _load_config()
    yaml_mem = (base_cfg or {}).get("memory", {})
    if not is_enterprise_memory_config_enabled():
        return copy.deepcopy(yaml_mem) if isinstance(yaml_mem, dict) else {}

    merged = copy.deepcopy(yaml_mem) if isinstance(yaml_mem, dict) else {}
    overlay = get_memory_config_overlay()
    if overlay:
        _deep_merge_dict(merged, overlay)
    return merged


def merge_memory_config_into_config(config_base: Dict[str, Any]) -> Dict[str, Any]:
    """深拷贝 config；企业级时将 memory 段与 DB overlay 合并。"""
    if not is_enterprise_memory_config_enabled():
        return copy.deepcopy(config_base)
    merged = copy.deepcopy(config_base)
    merged["memory"] = get_memory_section(config_base)
    return merged


def apply_memory_config_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    """应用 Manager/Gateway 下发的 memory_config（热更新入口，仅企业级生效）。"""
    global _memory_config_db_cache, _memory_config_cache_source

    if not is_enterprise_memory_config_enabled():
        return {"ok": True, "source": MEMORY_CONFIG_SOURCE_YAML}

    clear_config_cache()

    if not isinstance(payload, dict):
        _memory_config_db_cache = None
        _memory_config_cache_source = None
        return {"ok": True, "source": MEMORY_CONFIG_SOURCE_YAML}

    op = str(payload.get("op") or "").strip().lower()
    if op == "delete":
        _memory_config_db_cache = None
        _memory_config_cache_source = None
        return {"ok": True, "source": MEMORY_CONFIG_SOURCE_YAML}

    body = payload.get("body")
    if body is None and op == "upsert":
        body = {k: v for k, v in payload.items() if k not in {"op", "source", "revision"}}
    if not isinstance(body, dict):
        _memory_config_db_cache = None
        _memory_config_cache_source = None
        return {"ok": True, "source": MEMORY_CONFIG_SOURCE_YAML}

    _memory_config_db_cache = copy.deepcopy(body)
    _memory_config_cache_source = MEMORY_CONFIG_SOURCE_DB
    return {"ok": True, "source": MEMORY_CONFIG_SOURCE_DB}


def _memory_config_row_to_body(obj: Any) -> Dict[str, Any] | None:
    if obj is None:
        return None
    body = getattr(obj, "body", None)
    if isinstance(body, dict) and body:
        return copy.deepcopy(body)
    return None


async def reload_memory_config_from_gateway_db() -> dict[str, Any]:
    """从 Gateway 库加载 ``memory_config`` 更新缓存（冷启动/热重载，仅企业级）。"""
    if not is_enterprise_memory_config_enabled():
        return {"ok": True, "source": MEMORY_CONFIG_SOURCE_YAML}

    global _memory_config_db_cache, _memory_config_cache_source
    try:
        from jiuwenclaw.infrastructure.module_importer import (
            import_manager_ws_client_module,
        )

        db_mod = import_manager_ws_client_module("infrastructure.db")
        handler = await db_mod.ensure_db_handler(log_prefix="memory_config")

        jid = (os.getenv("JIUWENCLAW_ID") or "").strip()
        if not jid:
            _memory_config_db_cache = None
            _memory_config_cache_source = None
            return apply_memory_config_payload({"op": "delete"})

        row = await handler.get(MEMORY_CONFIG_TABLE, {"jiuwenclaw_id": jid})
        body = _memory_config_row_to_body(row)
        if body is not None:
            return apply_memory_config_payload({"op": "upsert", "body": body})

        _memory_config_db_cache = None
        _memory_config_cache_source = None
        clear_config_cache()
        return {"ok": True, "source": MEMORY_CONFIG_SOURCE_YAML}
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "memory_config read failed: %s",
            exc,
            exc_info=True,
        )
        return {"ok": False, "source": _memory_config_cache_source or MEMORY_CONFIG_SOURCE_YAML}


def _load_config() -> Dict[str, Any]:
    """Load configuration from YAML file."""
    global _config_cache

    if _config_cache is not None:
        return _config_cache
    
    config_path = Path(DEFAULT_CONFIG_PATH)
    
    if not config_path.exists():
        logger.warning(f"Config file not found: {config_path}")
        _config_cache = {}
        return _config_cache
    
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    
    config = _resolve_env_vars(config)
    _config_cache = config
    return config


def clear_embed_config_db_cache() -> None:
    """清除 embed_config 的 DB 缓存，使下次重新从 DB 读取."""
    global _embed_config_db_cache
    _embed_config_db_cache = None


def get_embed_config() -> Dict[str, str]:
    """Get embedding configuration.
    
    Priority: DB(embed_config table) > YAML (config.yaml embed section).
    """
    global _embed_config_db_cache
    if _embed_config_db_cache is not None:
        return _embed_config_db_cache

    config = _load_config()
    embed_config = config.get("embed", {})
    
    return {
        "api_key": embed_config.get("embed_api_key"),
        "base_url": embed_config.get("embed_base_url"),
        "model": embed_config.get("embed_model"),
    }


EMBED_API_KEY = property(lambda self: get_embed_config()["api_key"])
EMBED_BASE_URL = property(lambda self: get_embed_config()["base_url"])
EMBED_MODEL = property(lambda self: get_embed_config()["model"])


@dataclass
class MemorySettings:
    """Memory configuration settings."""
    provider: str = "openai_compatible"
    model: str = "text-embedding-v3"
    fallback: str = "mock"
    sources: List[str] = field(default_factory=lambda: ["memory", "sessions"])
    extraPaths: List[str] = field(default_factory=list)
    
    chunking: Dict[str, int] = field(default_factory=lambda: {"tokens": 256, "overlap": 32})
    
    query: Dict[str, Any] = field(default_factory=lambda: {
        "maxResults": 10,
        "minScore": 0.3,
        "hybrid": {
            "enabled": True,
            "vectorWeight": 0.7,
            "textWeight": 0.3,
            "candidateMultiplier": 2.0
        }
    })
    
    store: Dict[str, Any] = field(default_factory=lambda: {
        # 相对于 workspace_dir/memory/ 目录
        "path": "memory.db",
        "vector": {"enabled": True},
        "fts": {"enabled": True}
    })
    
    sync: Dict[str, Any] = field(default_factory=lambda: {
        "watch": True,
        "watchDebounceMs": 2000,
        "onSearch": True,
        "onSessionStart": True,
        "intervalMinutes": 0
    })
    
    cache: Dict[str, Any] = field(default_factory=lambda: {
        "enabled": True,
        "maxEntries": 10000
    })


def create_memory_settings(
    workspace_dir: str = DEFAULT_WORKSPACE_DIR,
    **overrides
) -> MemorySettings:
    """Create MemorySettings instance.
    
    Args:
        workspace_dir: Workspace directory
        **overrides: Override default settings
    
    Returns:
        MemorySettings instance
    """
    config = _load_config()
    embed_config = get_embed_config()
    memory_config = config.get("memory", {})
    
    settings = MemorySettings()
    
    settings.model = embed_config.get("model", settings.model)
    
    if memory_config:
        if "provider" in memory_config:
            settings.provider = memory_config["provider"]
        if "fallback" in memory_config:
            settings.fallback = memory_config["fallback"]
        if "sources" in memory_config:
            settings.sources = memory_config["sources"]
        if "extraPaths" in memory_config:
            settings.extraPaths = memory_config["extraPaths"]
        if "chunking" in memory_config:
            settings.chunking = memory_config["chunking"]
        if "query" in memory_config:
            settings.query = memory_config["query"]
        if "sync" in memory_config:
            settings.sync = memory_config["sync"]
        if "cache" in memory_config:
            settings.cache = memory_config["cache"]
    
    if "store" not in overrides:
        store_config = memory_config.get("store", {})
        # 向量数据库索引文件存放在与 MEMORY.md 同目录 (workspace_dir/memory/memory.db)
        # 只使用文件名，让 manager.py 的 _resolve_db_path 处理完整路径
        overrides["store"] = {
            "path": store_config.get("path", "memory.db"),
            "vector": store_config.get("vector", {"enabled": True}),
            "fts": store_config.get("fts", {"enabled": True}),
        }
    
    for key, value in overrides.items():
        if hasattr(settings, key):
            setattr(settings, key, value)
    
    return settings


def _resolve_mode_memory(mode: str, config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Locate the `memory:` block under modes for a given mode token.

    Accepts several mode formats used across the codebase:
      - "agent.plan" / "agent.fast"  -> modes.agent.plan / modes.agent.fast
      - "plan" / "fast"              -> modes.agent.plan / modes.agent.fast
      - "code"                       -> modes.code
    Returns {} when no block is found (callers treat missing as disabled).
    """
    modes_cfg = (config or {}).get("modes", {}) if isinstance(config, dict) else {}
    if not isinstance(modes_cfg, dict):
        return {}

    token = (mode or "").strip()
    if "." in token:
        top, sub = token.split(".", 1)
        node = modes_cfg.get(top, {})
        if isinstance(node, dict):
            node = node.get(sub, {})
    elif token == "code":
        node = modes_cfg.get("code", {})
    else:
        agent_node = modes_cfg.get("agent", {}) if isinstance(modes_cfg.get("agent"), dict) else {}
        node = agent_node.get(token, {})

    if not isinstance(node, dict):
        return {}
    mem = node.get("memory", {})
    return mem if isinstance(mem, dict) else {}


def is_memory_enabled(mode: str, config: Optional[Dict[str, Any]] = None) -> bool:
    """Check if built-in memory is enabled for the given mode.

    Reads `modes.agent.<plan|fast>.memory.enabled` (or `modes.code.memory.enabled`).

    Args:
        config: Optional config dict. If provided, reads from it directly
                (avoids stale cache). Otherwise reads from config.yaml.
    """
    try:
        return bool(_resolve_mode_memory(mode, config).get("enabled", False))
    except Exception as e:
        logger.warning(f"Invalid memory config, disable memory, error: {e}")
        return False


def is_proactive_memory(mode: str, config: Optional[Dict[str, Any]] = None) -> bool:
    """Check if proactive memory is enabled for the given mode.

    When True: agent auto-records everything and searches before every response.
    When False (default): agent only records/searches when user explicitly asks.
    """
    try:
        return bool(_resolve_mode_memory(mode, config).get("is_proactive", False))
    except Exception as e:
        logger.warning(f"Invalid memory config, disable proactive memory, error: {e}")
        return False


def get_memory_mode(config: Optional[Dict[str, Any]] = None) -> str:
    """读取 ``memory.mode``：``cloud`` 或 ``local``（默认）。"""
    if config is None:
        memory_cfg = get_memory_section()
    else:
        memory_cfg = get_memory_section(config)
    mode = str(memory_cfg.get("mode") or "local").strip().lower()
    return "cloud" if mode == "cloud" else "local"


async def reload_embed_config_from_gateway_db() -> None:
    """从 Gateway 库加载 ``embed_config`` 更新缓存"""
    global _embed_config_db_cache
    try:
        from jiuwenclaw.infrastructure.module_importer import (
            import_manager_ws_client_module,
        )

        db_mod = import_manager_ws_client_module("infrastructure.db")
        handler = await db_mod.ensure_db_handler(log_prefix="embed_config")

        jid = (os.getenv("JIUWENCLAW_ID") or "").strip()
        if not jid:
            _embed_config_db_cache = None
            return

        row = await handler.get("embed_config", {"jiuwenclaw_id": jid})
        if row is not None:
            _embed_config_db_cache = {
                "api_key": getattr(row, "embed_api_key", None),
                "base_url": getattr(row, "embed_base_url", None),
                "model": getattr(row, "embed_model", None),
            }
        else:
            _embed_config_db_cache = None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "embed_config read failed: %s",
            exc,
            exc_info=True,
        )


def clear_task_memory_config_db_cache() -> None:
    """清除 task_memory_config 的 DB 缓存，使下次重新从 DB 读取."""
    global _task_memory_config_db_cache
    _task_memory_config_db_cache = None


def get_task_memory_config() -> Dict[str, Any]:
    """Get task_memory configuration.

    Priority: DB(task_memory_config table) > YAML (config.yaml task_memory section).
    """
    global _task_memory_config_db_cache
    if _task_memory_config_db_cache is not None:
        return _task_memory_config_db_cache

    config = _load_config()
    task_memory_cfg = config.get("task_memory", {})

    return {
        "enabled": task_memory_cfg.get("enabled", False),
        "llm_model": task_memory_cfg.get("llm_model"),
        "embedding_model": task_memory_cfg.get("embedding_model"),
        "api_key": task_memory_cfg.get("api_key"),
        "api_base": task_memory_cfg.get("api_base"),
        "retrieval_algo": task_memory_cfg.get("retrieval_algo"),
        "summary_algo": task_memory_cfg.get("summary_algo"),
    }


async def reload_task_memory_config_from_gateway_db() -> None:
    """从 Gateway 库加载 ``task_memory_config`` 更新缓存"""
    global _task_memory_config_db_cache
    try:
        from jiuwenclaw.infrastructure.module_importer import (
            import_manager_ws_client_module,
        )

        db_mod = import_manager_ws_client_module("infrastructure.db")
        handler = await db_mod.ensure_db_handler(log_prefix="task_memory_config")

        jid = (os.getenv("JIUWENCLAW_ID") or "").strip()
        if not jid:
            _task_memory_config_db_cache = None
            return

        row = await handler.get("task_memory_config", {"jiuwenclaw_id": jid})
        if row is not None:
            _task_memory_config_db_cache = {
                "enabled": getattr(row, "enabled", False),
                "llm_model": getattr(row, "llm_model", None),
                "embedding_model": getattr(row, "embedding_model", None),
                "api_key": getattr(row, "api_key", None),
                "api_base": getattr(row, "api_base", None),
                "retrieval_algo": getattr(row, "retrieval_algo", None),
                "summary_algo": getattr(row, "summary_algo", None),
            }
        else:
            _task_memory_config_db_cache = None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "task_memory_config read failed: %s",
            exc,
            exc_info=True,
        )
