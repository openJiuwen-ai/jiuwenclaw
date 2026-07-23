# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Helpers for sync_agents_configs protocol validation and materialization."""

from __future__ import annotations

import copy
import hashlib
import json
import logging
from dataclasses import asdict, dataclass
from typing import Any

from jiuwenclaw.agentserver.tenant_catalog_registry import TenantAgentSpec
from jiuwenclaw.local_env_config import EnvNsIdError, normalize_env_ns_id

logger = logging.getLogger(__name__)

SYNC_ENV_SCHEMA: frozenset[str] = frozenset(
    {
        "API_KEY",
        "API_BASE",
        "MEMORY_ENGINE",
        "EVOLUTION_ENABLED",
        "EMBED_API_KEY",
        "EMBED_API_BASE",
        "EMBED_MODEL",
        "MODEL_NAME",
        "MODEL_PROVIDER",
        "TOOL_CALLING_GUARD_ENABLED",
        "TOOL_CALLING_GUARD_DISABLE",
        "TOOL_CALLING_GUARD_STRIP_REASON",
        "ENABLED_SKILLS",
        "DISABLED_SKILLS",
        "JIUWENCLAW_DISABLED_SKILLS",
        "JIUWENCLAW_RUNTIME_SKILLS_DIR",
        "JIUWENCLAW_SHARED_SKILLS_DIRS",
        "BOCHA_API_KEY",
        "JINA_API_KEY",
        "PERPLEXITY_API_KEY",
        "SERPER_API_KEY",
        "default_headers",
        "VISION_API_KEY",
        "VISION_API_BASE",
        "VISION_PROVIDER",
        "VISION_MODEL_NAME",
        "VISION_DEFAULT_HEADERS",
        "IMAGE_GEN_API_KEY",
        "IMAGE_GEN_API_BASE",
        "IMAGE_GEN_PROVIDER",
        "IMAGE_GEN_MODEL_NAME",
        "IMAGE_GEN_DEFAULT_HEADERS",
    }
)


def materialize_sync_env(env_dict: dict[str, Any]) -> dict[str, str]:
    """Build active-tip map: keep empty strings, omit nulls (deletes)."""
    if not isinstance(env_dict, dict):
        raise ValueError("agent env must be an object")
    return {str(k): str(v) for k, v in env_dict.items() if v is not None}


def _config_memory_engine(config: dict[str, Any]) -> str:
    from jiuwenclaw.agentserver.memory.external_memory_config import get_memory_engine

    return get_memory_engine(config)


def _config_evolution_enabled(config: dict[str, Any]) -> bool:
    react = config.get("react")
    if isinstance(react, dict):
        evo = react.get("evolution")
        if isinstance(evo, dict):
            return bool(evo.get("enabled", False))
    evo = config.get("evolution")
    if isinstance(evo, dict):
        return bool(evo.get("enabled", False))
    return False


def _env_bool(value: Any) -> bool | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off", ""}:
        return False
    return None


def synthesize_config(
    config: Any,
    env: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Ensure memory/evolution authority lives in config; warn on env disagreement."""
    if not isinstance(config, dict):
        raise ValueError("agent config must be an object")
    result = copy.deepcopy(config)

    memory = result.setdefault("memory", {})
    if not isinstance(memory, dict):
        memory = {}
        result["memory"] = memory
    memory.setdefault("engine", "builtin")

    react = result.get("react")
    if isinstance(react, dict):
        evolution = react.setdefault("evolution", {})
        if isinstance(evolution, dict):
            evolution.setdefault("enabled", False)
    else:
        evolution_top = result.setdefault("evolution", {})
        if isinstance(evolution_top, dict):
            evolution_top.setdefault("enabled", False)

    if isinstance(env, dict):
        cfg_engine = _config_memory_engine(result)
        env_engine = env.get("MEMORY_ENGINE")
        if env_engine is not None:
            env_engine_text = str(env_engine).strip().lower()
            if env_engine_text and env_engine_text != cfg_engine:
                logger.warning(
                    "sync_agents_configs: MEMORY_ENGINE env=%r disagrees with config engine=%r; keeping config",
                    env_engine_text,
                    cfg_engine,
                )

        cfg_evo = _config_evolution_enabled(result)
        env_evo = _env_bool(env.get("EVOLUTION_ENABLED"))
        if env_evo is not None and env_evo != cfg_evo:
            logger.warning(
                "sync_agents_configs: EVOLUTION_ENABLED env=%r disagrees with config enabled=%r; keeping config",
                env_evo,
                cfg_evo,
            )

    return result


def compute_content_hash(
    *,
    config: dict[str, Any],
    env: dict[str, Any],
    runtime: dict[str, Any],
) -> str:
    """Stable SHA-256 of config + env + runtime JSON."""
    payload = json.dumps(
        {"config": config, "env": env, "runtime": runtime},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _validate_env_schema(env: Any, *, agent_id: str) -> None:
    if not isinstance(env, dict):
        raise ValueError(f"agent {agent_id!r}: env must be an object")
    missing = sorted(SYNC_ENV_SCHEMA - {str(k) for k in env})
    if missing:
        raise ValueError(
            f"agent {agent_id!r}: env missing required keys: {', '.join(missing)}"
        )
    extra = sorted(str(k) for k in env if str(k) not in SYNC_ENV_SCHEMA)
    if extra:
        logger.debug(
            "sync_agents_configs: agent %r env has extra keys (accepted): %s",
            agent_id,
            ", ".join(extra),
        )


def _validate_shared_env(shared_env: Any) -> None:
    if not isinstance(shared_env, dict):
        raise ValueError("shared_env must be an object when provided")
    # Track A contract: MVP log/validate presence only — never mutate spawn env in sync.
    from jiuwenclaw.local_env_config import SPAWN_ENV_KEYS

    unknown = sorted(str(k) for k in shared_env if str(k) not in SPAWN_ENV_KEYS)
    if unknown:
        logger.info(
            "sync_agents_configs: shared_env keys outside SPAWN table (ignored): %s",
            ", ".join(unknown),
        )
    logger.info(
        "sync_agents_configs: shared_env received (%d keys); spawn mutation skipped",
        len(shared_env),
    )


def validate_sync_payload(params: Any) -> dict[str, Any]:
    """Validate sync_agents_configs params; raise ValueError on protocol errors."""
    if not isinstance(params, dict):
        raise ValueError("sync_agents_configs params must be an object")

    revision = params.get("revision")
    if not isinstance(revision, str) or not revision.strip():
        raise ValueError("revision is required and must be a non-empty string")
    revision = revision.strip()

    service_id_raw = params.get("service_id")
    if not isinstance(service_id_raw, str) or not str(service_id_raw).strip():
        raise ValueError("service_id is required and must be a non-empty string")
    try:
        service_id = normalize_env_ns_id(str(service_id_raw).strip(), default="")
    except EnvNsIdError as exc:
        raise ValueError(f"invalid service_id: {exc}") from exc
    if not service_id:
        raise ValueError("service_id is required and must be a non-empty string")

    agents = params.get("agents")
    if not isinstance(agents, list):
        raise ValueError("agents is required and must be an array")

    shared_env = params.get("shared_env")
    if shared_env is not None:
        _validate_shared_env(shared_env)

    seen_ids: set[str] = set()
    normalized_agents: list[dict[str, Any]] = []
    for index, entry in enumerate(agents):
        if not isinstance(entry, dict):
            raise ValueError(f"agents[{index}] must be an object")
        agent_id_raw = entry.get("agent_id")
        if not isinstance(agent_id_raw, str) or not str(agent_id_raw).strip():
            raise ValueError(f"agents[{index}].agent_id is required")
        try:
            agent_id = normalize_env_ns_id(str(agent_id_raw).strip(), default="")
        except EnvNsIdError as exc:
            raise ValueError(f"agents[{index}].agent_id invalid: {exc}") from exc
        if not agent_id:
            raise ValueError(f"agents[{index}].agent_id is required")
        if agent_id in seen_ids:
            raise ValueError(f"duplicate agent_id in sync payload: {agent_id!r}")
        seen_ids.add(agent_id)

        config = entry.get("config")
        env = entry.get("env")
        runtime = entry.get("runtime")
        if config is None:
            raise ValueError(f"agent {agent_id!r}: config is required")
        if env is None:
            raise ValueError(f"agent {agent_id!r}: env is required")
        if runtime is None:
            raise ValueError(f"agent {agent_id!r}: runtime is required")
        if not isinstance(runtime, dict):
            raise ValueError(f"agent {agent_id!r}: runtime must be an object")

        _validate_env_schema(env, agent_id=agent_id)
        normalized_agents.append(
            {
                "agent_id": agent_id,
                "config": config,
                "env": env,
                "runtime": runtime,
            }
        )

    return {
        "revision": revision,
        "service_id": service_id,
        "agents": normalized_agents,
        "shared_env": shared_env,
    }


def build_agent_spec(
    *,
    service_id: str,
    agent_id: str,
    config: dict[str, Any],
    env: dict[str, Any],
    runtime: dict[str, Any],
    revision: str,
) -> TenantAgentSpec:
    synthesized = synthesize_config(config, env)
    content_hash = compute_content_hash(
        config=synthesized,
        env=env,
        runtime=runtime,
    )
    return TenantAgentSpec(
        service_id=service_id,
        agent_id=agent_id,
        config=synthesized,
        env=env,
        runtime=runtime,
        revision=revision,
        content_hash=content_hash,
    )


@dataclass
class AgentSyncResultItem:
    """Per-agent sync_agents_configs result fields (G.FNM.03)."""

    agent_id: str
    action: str
    ok: bool
    error: str | None = None
    warmup: dict[str, Any] | None = None
    reload: dict[str, Any] | None = None


def build_agent_result(item: AgentSyncResultItem) -> dict[str, Any]:
    return asdict(item)
