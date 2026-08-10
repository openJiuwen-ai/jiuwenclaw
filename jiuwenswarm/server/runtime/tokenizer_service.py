# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""AgentServer-side tokenizer cache warm-up service.

The Gateway only persists model configuration and sends a reload notification.
Tokenizer resolution belongs to the AgentServer process because that is where
the context engine runs and where the downloaded files will be consumed.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jiuwenswarm.common.config import get_config, get_default_models
from jiuwenswarm.common.utils import get_user_workspace_dir

logger = logging.getLogger(__name__)

_DEFAULT_CACHE_DIR = Path("tokenizers")
_NATIVE_WARM_SOURCES = frozenset({"native_tokenizer", "family_tokenizer_fallback"})
_MODEL_VARIANT_SEPARATORS = frozenset({"_", "-", ":", "."})

# These are model-vendor identities, not API client providers.  A model may be
# served through an OpenAI-compatible or another provider adapter, so the
# mapping deliberately resolves by model name and then records the configured
# provider on the generated spec.
_DEFAULT_TOKENIZER_REPOSITORIES: tuple[tuple[str, str], ...] = (
    ("glm-5.2", "zai-org/GLM-5.2"),
    ("glm-5", "zai-org/GLM-5"),
    ("deepseek-v4-flash", "deepseek-ai/DeepSeek-V4-Flash"),
)
_TOKENIZER_WARMUP_MAX_ATTEMPTS = 3
_TOKENIZER_WARMUP_RETRY_DELAYS_SECONDS = (1.0, 3.0)


@dataclass(frozen=True)
class TokenizerProfile:
    """One model profile that may need a tokenizer warm-up."""

    provider: str
    model: str
    spec: dict[str, Any] | None = None


@dataclass(frozen=True)
class TokenizerWarmupSettings:
    enabled: bool
    cache_dir: Path
    offline: bool
    proxy: str | None
    registry: tuple[dict[str, Any], ...]


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    normalized = str(value).strip().casefold()
    if normalized in {"1", "true", "yes", "on", "enabled"}:
        return True
    if normalized in {"0", "false", "no", "off", "disabled"}:
        return False
    return default


def _context_engine_config(config: dict[str, Any]) -> dict[str, Any]:
    react = config.get("react") if isinstance(config, dict) else None
    if not isinstance(react, dict):
        return {}
    context_config = react.get("context_engine_config")
    return context_config if isinstance(context_config, dict) else {}


def _model_variant_match(base: str, requested: str) -> bool:
    """Match a delimited model variant without matching arbitrary substrings."""
    return (
        bool(base)
        and requested != base
        and requested.startswith(base)
        and len(requested) > len(base)
        and requested[len(base)] in _MODEL_VARIANT_SEPARATORS
    )


def _default_tokenizer_spec(*, provider: str, model: str) -> dict[str, Any] | None:
    """Infer a trusted tokenizer source for known models or repo-shaped IDs.

    The configured API model name remains the source of truth for selecting the
    model client. This helper only supplies a tokenizer artifact identity when
    it is deterministic: a known vendor model family or an explicit
    ``org/model`` repository-shaped name. Arbitrary aliases are left unresolved.
    """
    normalized_model = model.strip().casefold()
    if not normalized_model:
        return None

    candidates: list[tuple[int, str, str]] = []
    for base, tokenizer_id in _DEFAULT_TOKENIZER_REPOSITORIES:
        if normalized_model == base or _model_variant_match(base, normalized_model):
            candidates.append((len(base), base, tokenizer_id))
    if candidates:
        _, base, tokenizer_id = max(candidates, key=lambda item: item[0])
        return {
            "provider": provider,
            "model": base,
            "id": tokenizer_id,
            "source": "huggingface",
        }

    # A slash-delimited model name is already in the canonical repository form.
    # Keep this inference limited to HuggingFace-compatible IDs; plain aliases
    # such as ``glm-5.2`` must use the controlled mapping above.
    if "/" in model and not model.startswith(("/", "./", "../")):
        return {
            "provider": provider,
            "model": model,
            "id": model,
            "source": "huggingface",
        }
    return None


def resolve_tokenizer_cache_dir(config: dict[str, Any] | None = None) -> Path:
    """Resolve the single cache directory shared by Swarm and agent-core.

    A configured relative path is rooted in the JiuwenSwarm data directory;
    an unset value defaults to ``~/.jiuwenswarm/tokenizers`` (or the
    directory selected by ``JIUWENSWARM_DATA_DIR``).
    """
    effective_config = config if isinstance(config, dict) else {}
    context_config = _context_engine_config(effective_config)
    raw_dir = context_config.get("tokenizer_cache_dir")
    if raw_dir in (None, ""):
        raw_dir = os.getenv("JIUWENSWARM_TOKENIZER_CACHE_DIR")

    if raw_dir in (None, ""):
        return get_user_workspace_dir() / _DEFAULT_CACHE_DIR

    path = Path(str(raw_dir)).expanduser()
    if not path.is_absolute():
        path = get_user_workspace_dir() / path
    return path


def tokenizer_warmup_settings(config: dict[str, Any] | None = None) -> TokenizerWarmupSettings:
    """Extract the AgentServer tokenizer policy from the resolved config."""
    effective_config = config if isinstance(config, dict) else get_config()
    context_config = _context_engine_config(effective_config)
    registry = context_config.get("tokenizer_registry")
    normalized_registry = tuple(
        dict(item) for item in registry if isinstance(item, dict)
    ) if isinstance(registry, list) else ()
    raw_proxy = context_config.get("tokenizer_proxy")
    if raw_proxy is None:
        raw_proxy = os.getenv("JIUWENSWARM_TOKENIZER_PROXY")
    tokenizer_proxy = str(raw_proxy).strip() if raw_proxy not in (None, "") else None
    return TokenizerWarmupSettings(
        enabled=_as_bool(context_config.get("enable_tiktoken_counter"), default=False),
        cache_dir=resolve_tokenizer_cache_dir(effective_config),
        offline=_as_bool(context_config.get("tokenizer_offline"), default=False),
        proxy=tokenizer_proxy,
        registry=normalized_registry,
    )


def _entry_tokenizer_spec(
    entry: dict[str, Any],
    *,
    provider: str,
    model: str,
    infer_defaults: bool = True,
) -> dict[str, Any] | None:
    """Read tokenizer metadata declared alongside a model profile.

    Both ``tokenizer`` and ``tokenizer_spec`` are accepted so existing model
    profile naming conventions can be used. Flat ``tokenizer_id``,
    ``tokenizer_source``, ``tokenizer_path``, ``tokenizer_engine``, and
    ``tokenizer_family`` fields are accepted as well. A string is treated as
    the tokenizer id; a mapping is passed through to agent-core.
    """
    model_client_config = entry.get("model_client_config")
    candidates = [
        entry.get("tokenizer"),
        entry.get("tokenizer_spec"),
        model_client_config.get("tokenizer")
        if isinstance(model_client_config, dict)
        else None,
        model_client_config.get("tokenizer_spec")
        if isinstance(model_client_config, dict)
        else None,
    ]
    raw_spec = next((candidate for candidate in candidates if candidate is not None), None)
    if raw_spec is None:
        # Accept tokenizer metadata alongside the model client without forcing
        # callers to construct a nested TokenizerSpec object. Explicit
        # metadata is preferred; only known model families and
        # repository-shaped IDs are inferred below.
        metadata_owner = model_client_config if isinstance(model_client_config, dict) else {}
        tokenizer_id = (
            entry.get("tokenizer_id")
            or metadata_owner.get("tokenizer_id")
        )
        tokenizer_path = (
            entry.get("tokenizer_path")
            or metadata_owner.get("tokenizer_path")
        )
        model_path = (
            entry.get("model_path")
            or metadata_owner.get("model_path")
        )
        tokenizer_source = (
            entry.get("tokenizer_source")
            or metadata_owner.get("tokenizer_source")
        )
        tokenizer_engine = (
            entry.get("tokenizer_engine")
            or metadata_owner.get("tokenizer_engine")
        )
        tokenizer_family = (
            entry.get("tokenizer_family")
            or metadata_owner.get("tokenizer_family")
        )
        local_path = tokenizer_path or model_path
        if local_path:
            raw_spec = {"source": "local", "artifact_path": local_path}
        elif Path(model).expanduser().exists():
            raw_spec = {"source": "local", "artifact_path": model}
        elif tokenizer_id:
            raw_spec = {"id": tokenizer_id}
        elif tokenizer_source:
            # A source without an ID is useful only when the model name itself
            # is a valid provider repository ID (for example org/model).
            raw_spec = {"id": model, "source": tokenizer_source}
        elif infer_defaults:
            raw_spec = _default_tokenizer_spec(provider=provider, model=model)
        if raw_spec is not None:
            if tokenizer_source and "source" not in raw_spec:
                raw_spec["source"] = tokenizer_source
            if tokenizer_engine:
                raw_spec["engine"] = tokenizer_engine
            if tokenizer_family:
                raw_spec["family"] = tokenizer_family
    if raw_spec is None:
        return None
    if isinstance(raw_spec, str):
        raw_spec = {"id": raw_spec}
    if not isinstance(raw_spec, dict):
        return None
    spec = dict(raw_spec)
    spec.setdefault("provider", provider)
    spec.setdefault("model", model)
    return spec


def configured_tokenizer_profiles(config: dict[str, Any] | None = None) -> list[TokenizerProfile]:
    """Return distinct text-model profiles from ``models.defaults``.

    The helper deliberately does not construct an LLM client, so warming a
    tokenizer cannot make an API request or require model credentials.
    """
    effective_config = config if isinstance(config, dict) else get_config()
    context_config = _context_engine_config(effective_config)
    raw_registry = context_config.get("tokenizer_registry")
    registry_specs = [
        dict(item) for item in raw_registry if isinstance(item, dict)
    ] if isinstance(raw_registry, list) else []
    tokenizer_registry = None
    if registry_specs:
        try:
            from openjiuwen.core.context_engine import TokenizerRegistry

            tokenizer_registry = TokenizerRegistry(registry_specs)
        except Exception:  # noqa: BLE001 - optional registry enrichment
            tokenizer_registry = None

    def resolve_profile_spec(
        entry: dict[str, Any],
        *,
        provider: str,
        model: str,
    ) -> dict[str, Any] | None:
        # Explicit per-model metadata wins over the registry. For models with
        # no explicit metadata, a user registry entry wins over built-in
        # vendor mappings, then the automatic mapping is attempted.
        spec = _entry_tokenizer_spec(
            entry,
            provider=provider,
            model=model,
            infer_defaults=False,
        )
        if spec is not None:
            return spec
        if tokenizer_registry is not None:
            try:
                match = tokenizer_registry.resolve_match(provider, model)
                if match is not None:
                    return match.spec.model_dump(mode="json", by_alias=True)
            except Exception:  # noqa: BLE001 - optional registry enrichment
                pass
        return _default_tokenizer_spec(provider=provider, model=model)

    profiles: list[TokenizerProfile] = []
    seen: set[str] = set()
    for entry in get_default_models(effective_config):
        if not isinstance(entry, dict):
            continue
        model_client_config = entry.get("model_client_config")
        model_client_config = model_client_config if isinstance(model_client_config, dict) else {}
        model = str(
            model_client_config.get("model_name")
            or entry.get("model_name")
            or ""
        ).strip()
        if not model:
            continue
        provider = str(
            model_client_config.get("client_provider")
            or model_client_config.get("provider")
            or entry.get("provider")
            or ""
        ).strip()
        spec = resolve_profile_spec(entry, provider=provider, model=model)
        identity = json.dumps(
            {"provider": provider.casefold(), "model": model.casefold(), "spec": _identity_value(spec)},
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
        if identity in seen:
            continue
        seen.add(identity)
        profiles.append(TokenizerProfile(provider=provider, model=model, spec=spec))

    # A small legacy configuration can define only ``react.model_name``. Keep
    # that form warmable as well; normal installations use models.defaults.
    if not profiles:
        react = effective_config.get("react") if isinstance(effective_config, dict) else None
        react = react if isinstance(react, dict) else {}
        model = str(react.get("model_name") or "").strip()
        if model:
            model_client_config = react.get("model_client_config")
            model_client_config = model_client_config if isinstance(model_client_config, dict) else {}
            provider = str(
                react.get("model_provider")
                or model_client_config.get("client_provider")
                or ""
            ).strip()
            profiles.append(
                TokenizerProfile(
                    provider=provider,
                    model=model,
                    spec=resolve_profile_spec(react, provider=provider, model=model),
                )
            )
    return profiles


def _stable_key(
    profile: TokenizerProfile,
    settings: TokenizerWarmupSettings,
) -> str:
    tokenizer_identity: Any = _identity_value(profile.spec)
    resolved_from_registry = False
    if profile.spec is None and settings.registry:
        # A model variant and its registered base model share the same
        # artifact. Use the resolved spec for service-level deduplication so a
        # reload does not schedule a second download for the same tokenizer.
        try:
            from openjiuwen.core.context_engine import TokenizerRegistry

            match = TokenizerRegistry(settings.registry).resolve_match(
                profile.provider,
                profile.model,
            )
            if match is not None:
                tokenizer_identity = _identity_value(
                    match.spec.model_dump(mode="json", by_alias=True)
                )
                resolved_from_registry = True
        except Exception:  # noqa: BLE001 - dedup must not block warm-up
            pass
    has_explicit_artifact_identity = isinstance(profile.spec, dict) and any(
        profile.spec.get(field)
        for field in ("id", "tokenizer_id", "artifact_path")
    )
    same_artifact = resolved_from_registry or has_explicit_artifact_identity
    payload = {
        "provider": "" if same_artifact else profile.provider.strip().casefold(),
        "model": "" if same_artifact else profile.model.strip().casefold(),
        "spec": tokenizer_identity,
        "registry": _identity_value(settings.registry),
        "cache_dir": str(settings.cache_dir),
        "offline": settings.offline,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()


def _identity_value(value: Any, *, _key: str = "") -> Any:
    """Normalize matching metadata without changing IDs, paths, or revisions."""
    if isinstance(value, dict):
        return {
            str(key): _identity_value(item, _key=str(key).casefold())
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_identity_value(item, _key=_key) for item in value]
    if isinstance(value, str) and _key in {"provider", "model", "source", "engine", "family"}:
        return value.strip().casefold()
    return value


def _is_remote_tokenizer_spec(spec: dict[str, Any] | None) -> bool:
    if not isinstance(spec, dict):
        return False
    return str(spec.get("source") or "").strip().casefold() in {
        "huggingface",
        "modelscope",
    }


class TokenizerService:
    """Own tokenizer prewarming for one AgentServer process.

    Calls are serialized at the service level so a startup warm-up and a
    simultaneous model reload cannot duplicate downloads. Individual models
    are resolved concurrently in worker threads.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._warmed_keys: set[str] = set()
        self._last_result: dict[str, Any] = {}

    @property
    def last_result(self) -> dict[str, Any]:
        return dict(self._last_result)

    async def warm(
        self,
        config: dict[str, Any] | None = None,
        *,
        reason: str,
    ) -> dict[str, Any]:
        """Warm configured model tokenizers and return a compact status."""
        effective_config = self._merge_config(config)
        settings = tokenizer_warmup_settings(effective_config)
        profiles = configured_tokenizer_profiles(effective_config)
        if not settings.enabled:
            # The switch is the application-level master switch: disabled
            # means no tokenizer warm-up/download and the ContextEngine will
            # use StringLengthCounter directly, even if an old artifact is
            # still present in the cache.
            self._warmed_keys.clear()
            result = {
                "enabled": False,
                "counter_enabled": False,
                "cache_dir": str(settings.cache_dir),
                "total": len(profiles),
                "warmed": 0,
                "degraded": 0,
                "failed": 0,
                "skipped": len(profiles),
                "reason": reason,
                "models": [],
            }
            self._last_result = result
            logger.info(
                "[TokenizerService] tokenizer warm-up disabled (%s): skipping %d "
                "configured profile(s); no download; counter_enabled=False",
                reason,
                len(profiles),
            )
            return result

        if not profiles:
            self._warmed_keys.clear()
            result = {
                "enabled": False,
                "counter_enabled": settings.enabled,
                "cache_dir": str(settings.cache_dir),
                "total": 0,
                "warmed": 0,
                "degraded": 0,
                "failed": 0,
                "reason": reason,
            }
            self._last_result = result
            logger.info(
                "[TokenizerService] no configured text models (%s): warm-up not needed; "
                "counter_enabled=%s",
                reason,
                settings.enabled,
            )
            return result

        settings.cache_dir.mkdir(parents=True, exist_ok=True)

        async with self._lock:
            pending: list[tuple[str, TokenizerProfile]] = []
            pending_keys = set(self._warmed_keys)
            for profile in profiles:
                key = _stable_key(profile, settings)
                if key not in pending_keys:
                    pending.append((key, profile))
                    pending_keys.add(key)

            if not pending:
                result = {
                    "enabled": True,
                    "counter_enabled": settings.enabled,
                    "cache_dir": str(settings.cache_dir),
                    "total": len(profiles),
                    "warmed": 0,
                    "degraded": 0,
                    "failed": 0,
                    "skipped": len(profiles),
                    "reason": reason,
                }
                self._last_result = result
                logger.info(
                    "[TokenizerService] no new tokenizer profiles (%s): total=%d cache=%s",
                    reason,
                    len(profiles),
                    settings.cache_dir,
                )
                return result

            logger.info(
                "[TokenizerService] warming %d tokenizer profile(s) (%s), cache=%s "
                "offline=%s counter_enabled=%s proxy_configured=%s",
                len(pending),
                reason,
                settings.cache_dir,
                settings.offline,
                settings.enabled,
                bool(settings.proxy),
            )

            outcomes = await asyncio.gather(
                *(
                    self._warm_one(profile, settings)
                    for _, profile in pending
                ),
                return_exceptions=True,
            )

            warmed = 0
            degraded = 0
            failed = 0
            statuses: list[dict[str, Any]] = []
            for (key, profile), outcome in zip(pending, outcomes):
                if isinstance(outcome, BaseException):
                    failed += 1
                    logger.warning(
                        "[TokenizerService] warm failed for provider=%s model=%s: %s",
                        profile.provider,
                        profile.model,
                        outcome,
                    )
                    statuses.append(
                        {
                            "provider": profile.provider,
                            "model": profile.model,
                            "ok": False,
                            "status": "failed",
                            "error": str(outcome),
                        }
                    )
                    continue
                source = getattr(outcome, "measurement_source", None)
                is_native = source in _NATIVE_WARM_SOURCES
                if is_native:
                    warmed += 1
                    self._warmed_keys.add(key)
                else:
                    # Selector fallback is a usable degraded result, but it
                    # is not a tokenizer warm hit. Keep it retryable so a
                    # later config reload can warm a newly supplied spec.
                    degraded += 1
                fallback_reason = getattr(outcome, "measurement_fallback_reason", None)
                status = (
                    "unresolved"
                    if fallback_reason == "model_tokenizer_spec_missing"
                    else "native_warmed" if is_native else "fallback"
                )
                statuses.append(
                    {
                        "provider": profile.provider,
                        "model": profile.model,
                        "ok": is_native,
                        "status": status,
                        "source": source,
                        "tokenizer": getattr(outcome, "measurement_tokenizer", None),
                        "fallback_reason": fallback_reason,
                    }
                )
                logger.info(
                    "[TokenizerService] model result provider=%s model=%s status=%s "
                    "source=%s tokenizer=%s fallback_reason=%s",
                    profile.provider,
                    profile.model,
                    status,
                    source,
                    getattr(outcome, "measurement_tokenizer", None),
                    fallback_reason,
                )

            result = {
                "enabled": True,
                "counter_enabled": settings.enabled,
                "cache_dir": str(settings.cache_dir),
                "total": len(profiles),
                "warmed": warmed,
                "degraded": degraded,
                "failed": failed,
                "skipped": max(len(profiles) - len(pending), 0),
                "reason": reason,
                "models": statuses,
            }
            self._last_result = result
            return result

    @staticmethod
    def _merge_config(config: dict[str, Any] | None) -> dict[str, Any]:
        """Fill partial reload payloads with the current runtime policy."""
        if not isinstance(config, dict):
            return get_config()
        if "react" in config and "models" in config:
            return config

        current = get_config()
        merged = dict(current) if isinstance(current, dict) else {}
        merged.update(config)
        if (
            isinstance(current, dict)
            and isinstance(current.get("react"), dict)
            and isinstance(config.get("react"), dict)
        ):
            react = dict(current["react"])
            react.update(config["react"])
            if isinstance(current["react"].get("context_engine_config"), dict) and isinstance(
                config["react"].get("context_engine_config"), dict
            ):
                context_config = dict(current["react"]["context_engine_config"])
                context_config.update(config["react"]["context_engine_config"])
                react["context_engine_config"] = context_config
            merged["react"] = react
        return merged

    async def _warm_one(
        self,
        profile: TokenizerProfile,
        settings: TokenizerWarmupSettings,
    ) -> Any:
        # Lazy import keeps the server importable with an older core package;
        # the feature is activated when configured model profiles exist and
        # the adapted agent-core is present.
        from openjiuwen.core.context_engine import (
            TokenizerArtifactManager,
            TokenizerRegistry,
            TokenizerSelector,
        )

        should_retry = _is_remote_tokenizer_spec(profile.spec) and not settings.offline
        max_attempts = _TOKENIZER_WARMUP_MAX_ATTEMPTS if should_retry else 1
        last_outcome: Any = None

        for attempt in range(1, max_attempts + 1):
            manager = TokenizerArtifactManager(
                cache_dir=str(settings.cache_dir),
                enable_download=True,
                offline=settings.offline,
                proxy=settings.proxy,
            )
            selector = TokenizerSelector(
                provider=profile.provider,
                model=profile.model,
                spec=profile.spec,
                registry=TokenizerRegistry(settings.registry),
                manager=manager,
                allow_tiktoken_fallback=False,
            )
            # Artifact resolution may download; keep the event loop responsive.
            last_outcome = await asyncio.to_thread(selector.select)
            source = getattr(last_outcome, "measurement_source", None)
            if source in _NATIVE_WARM_SOURCES:
                if attempt > 1:
                    logger.info(
                        "[TokenizerService] tokenizer retry succeeded for provider=%s "
                        "model=%s attempt=%d/%d",
                        profile.provider,
                        profile.model,
                        attempt,
                        max_attempts,
                    )
                return last_outcome

            error = (
                getattr(manager, "last_error", None)
                or getattr(last_outcome, "measurement_fallback_reason", None)
                or "native_tokenizer_unavailable"
            )
            if attempt >= max_attempts:
                logger.warning(
                    "[TokenizerService] tokenizer warm-up exhausted for provider=%s "
                    "model=%s attempts=%d error=%s",
                    profile.provider,
                    profile.model,
                    attempt,
                    error,
                )
                return last_outcome

            delay = _TOKENIZER_WARMUP_RETRY_DELAYS_SECONDS[attempt - 1]
            logger.warning(
                "[TokenizerService] tokenizer warm-up attempt failed for provider=%s "
                "model=%s attempt=%d/%d error=%s; retrying in %.1fs",
                profile.provider,
                profile.model,
                attempt,
                max_attempts,
                error,
                delay,
            )
            await asyncio.sleep(delay)

        # max_attempts is positive, so this is defensive only.
        return last_outcome


__all__ = [
    "TokenizerProfile",
    "TokenizerService",
    "TokenizerWarmupSettings",
    "configured_tokenizer_profiles",
    "resolve_tokenizer_cache_dir",
    "tokenizer_warmup_settings",
]
