# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Memory configuration for JiuWenClaw.

Uses merged runtime config (package template + user ``config/config.yaml`` override),
same as ``jiuwenclaw.config.get_config``. Embedding API settings are in the ``embed`` section.
"""

import logging
from typing import Any, Optional, Dict, List
from dataclasses import dataclass, field

from jiuwenclaw.config import get_config
from jiuwenclaw.utils import get_agent_workspace_dir

logger = logging.getLogger(__name__)

DEFAULT_WORKSPACE_DIR = str(get_agent_workspace_dir())

_config_cache: Optional[Dict[str, Any]] = None


def clear_config_cache() -> None:
    """清除配置缓存，使下次 _load_config() 重新读取合并后的配置（含环境变量解析）。"""
    global _config_cache
    _config_cache = None


def _load_config() -> Dict[str, Any]:
    """加载包内模板与用户 override 合并后的配置（与 ``get_config()`` 一致）。"""
    global _config_cache

    if _config_cache is not None:
        return _config_cache

    try:
        cfg = get_config()
        _config_cache = cfg if isinstance(cfg, dict) else {}
    except Exception as e:
        logger.warning("Failed to load merged config for memory module: %s", e)
        _config_cache = {}
    return _config_cache


def get_embed_config() -> Dict[str, str]:
    """Get embedding configuration from config file.
    
    Returns embedding API configuration from config.yaml embed section.
    """
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
    memory_cfg = (config or {}).get("memory", {})
    mode = str(memory_cfg.get("mode") or "local").strip().lower()
    return "cloud" if mode == "cloud" else "local"
