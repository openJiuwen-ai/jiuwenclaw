# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""External memory configuration helpers.

Reads `memory.external` from config.yaml. For provider=openjiuwen, also
maps the jiuwenclaw-shaped config (`memory.external.stores` + top-level
`embed`) into the config dict that OpenJiuwenMemoryProvider expects.
Concrete Store / Embedding instances are built inside the provider — not
here.
"""

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

from jiuwenclaw.common.utils import get_user_workspace_dir

from .config import _load_config, get_embed_config

logger = logging.getLogger(__name__)

_DEFAULT_USER = "__default__"
_DEFAULT_SCOPE = "__default__"
_LTM_SUBDIR = "memory/ltm"

_VALID_ENGINES = {"builtin", "external", "both", "none"}


def get_memory_engine(config: Optional[Dict[str, Any]] = None) -> str:
    """Return the memory engine policy: builtin | external | both | none.

    Controls which memory subsystems are allowed to mount.
    Default: builtin (backward-compatible with configs that predate this flag).
    """
    cfg = config if config is not None else _load_config()
    mem = (cfg or {}).get("memory", {}) if isinstance(cfg, dict) else {}
    value = str(mem.get("engine") or "builtin").strip().lower()
    return value if value in _VALID_ENGINES else "builtin"


def is_builtin_memory_allowed(config: Optional[Dict[str, Any]] = None) -> bool:
    """Engine-level gate for the built-in MemoryRail."""
    return get_memory_engine(config) in {"builtin", "both"}


def is_external_memory_allowed(config: Optional[Dict[str, Any]] = None) -> bool:
    """Engine-level gate for the ExternalMemoryRail."""
    return get_memory_engine(config) in {"external", "both"}


def get_external_memory_config(
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Return the `memory.external` section with defaults filled in."""
    cfg = config if config is not None else _load_config()
    mem = (cfg or {}).get("memory", {}) if isinstance(cfg, dict) else {}
    ext = mem.get("external", {}) if isinstance(mem, dict) else {}
    if not isinstance(ext, dict):
        ext = {}

    return {
        "provider": (ext.get("provider") or "").strip(),
        "user_id": ext.get("user_id") or _DEFAULT_USER,
        "scope_id": ext.get("scope_id") or _DEFAULT_SCOPE,
        "allowed_plugins": ext.get("allowed_plugins") or [],
        "openjiuwen": ext.get("openjiuwen") or {},
        "mem0": ext.get("mem0") or {},
        "openviking": ext.get("openviking") or {},
    }


def is_external_memory_enabled(config: Optional[Dict[str, Any]] = None) -> bool:
    """Return True iff external memory is both engine-allowed and has a provider."""
    if not is_external_memory_allowed(config):
        return False
    return bool(get_external_memory_config(config).get("provider"))


def _resolve_ltm_dir() -> Path:
    """Default LTM data dir under {workspace_dir}/memory/ltm."""
    base = get_user_workspace_dir() / _LTM_SUBDIR
    base.mkdir(parents=True, exist_ok=True)
    return base


def build_openjiuwen_provider_config(ext_cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Map jiuwenclaw config into the dict OpenJiuwenMemoryProvider expects.

    Provider-expected shape:
        {
          "kv":        {"backend": "shelve|memory|sqlite", "path": "..."},
          "vector":    {"backend": "chroma", "persist_directory": "..."},
          "db":        {"backend": "sqlite", "path": "..."},
          "embedding": {"model_name": "...", "base_url": "...", "api_key": "..."},
        }
    """
    oj_cfg = ext_cfg.get("openjiuwen") or {}
    ltm_dir = _resolve_ltm_dir()

    kv_backend = (oj_cfg.get("kv_type") or "shelve").lower()
    kv_path = oj_cfg.get("kv_path") or str(ltm_dir / "kv")

    vector_backend = (oj_cfg.get("vector_type") or "chroma").lower()
    vector_dir = oj_cfg.get("vector_persist_dir") or str(ltm_dir / "chroma")

    db_backend = (oj_cfg.get("db_type") or "sqlite").lower()
    db_path = oj_cfg.get("db_path") or str(ltm_dir / "ltm.db")

    embed_cfg = get_embed_config() or {}
    embedding = {
        "model_name": embed_cfg.get("model") or os.getenv("EMBED_MODEL", ""),
        "base_url": embed_cfg.get("base_url") or os.getenv("EMBED_BASE_URL", ""),
        "api_key": embed_cfg.get("api_key") or os.getenv("EMBED_API_KEY", ""),
    }
    if not embedding["model_name"]:
        logger.warning(
            "[external_memory] Embedding not configured — OpenJiuwen LTM will skip vector search"
        )

    return {
        "kv": {"backend": kv_backend, "path": kv_path},
        "vector": {"backend": vector_backend, "persist_directory": vector_dir},
        "db": {"backend": db_backend, "path": db_path},
        "embedding": embedding,
    }
