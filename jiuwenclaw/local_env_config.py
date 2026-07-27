"""Process env tip + track-B namespaced os.environ helpers.

Isolation dimension is always ``(service_id, agent_id)`` (request-side).
Tip bags live here; Manager ``_latest_*`` is write-through only.

Tip formula B (effective tip)::
    active[(sid, aid)] ∪ staged[(sid, aid)]   # staged wins on key clash

Task seal: when overlay is bound (including ``{}``), readers only see overlay.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import Iterable, Iterator, Mapping, MutableMapping
from contextvars import ContextVar, Token
from typing import Any

DEFAULT_HEADERS_ENV_KEY = "default_headers"
_DEFAULT_HEADERS_ALIASES = (
    DEFAULT_HEADERS_ENV_KEY,
    "DEFAULT_HEADERS",
    "OPENAI_DEFAULT_HEADERS",
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Track A / mirror / P2 key tables (authoritative MVP inventories)
# ---------------------------------------------------------------------------

SPAWN_ENV_KEYS: frozenset[str] = frozenset(
    {
        "HOME",
        "JIUWENCLAW_DATA_DIR",
        "JIUWENCLAW_AGENT_ROOT",
        "PYTHONUNBUFFERED",
        "WEB_HOST",
        # Align with relay-claw launchEnv / sync_agents_configs shared_env (short names).
        "OFFICE_CLAW_MCP_SERVER_PATH",
        "OFFICE_CLAW_MCP_COMMAND",
        "OFFICE_CLAW_MCP_ARGS_JSON",
        "OFFICE_CLAW_MCP_CWD",
        "OFFICE_CLAW_MCP_EXCLUDED_TOOLS",
        # Legacy aliases (pre-alignment SPAWN table); accept so old shared_env is not ignored.
        "OFFICE_CLAW_MCP_SERVER_COMMAND",
        "OFFICE_CLAW_MCP_SERVER_ARGS_JSON",
        "OFFICE_CLAW_MCP_SERVER_CWD",
        "OTEL_ENABLED",
        "OTEL_TRACES_EXPORTER",
        "OTEL_METRICS_EXPORTER",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_EXPORTER_OTLP_PROTOCOL",
        "OTEL_SERVICE_NAME",
        "OTEL_LOG_MESSAGES",
        "PATH",
        "AGENT_RUNTIME",
    }
)

BUSINESS_MIRROR_KEYS: frozenset[str] = frozenset(
    {
        # A. sync agents[].env schema
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
        "PETAL_SEARCH_URL",
        "PETAL_SEARCH_HEADERS",
        "default_headers",
        "DEFAULT_HEADERS",
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
        # B. Gateway/CLI extensions
        "AUDIO_API_KEY",
        "AUDIO_API_BASE",
        "AUDIO_PROVIDER",
        "AUDIO_MODEL_NAME",
        "VIDEO_API_KEY",
        "VIDEO_API_BASE",
        "VIDEO_PROVIDER",
        "VIDEO_MODEL_NAME",
        "EMAIL_ADDRESS",
        "EMAIL_TOKEN",
        "GITHUB_TOKEN",
        "FREE_SEARCH_PROXY_URL",
        # Web config.set free-search flags (distinct from JIUWENCLAW_ENABLE_*)
        "FREE_SEARCH_DDG_ENABLED",
        "FREE_SEARCH_BING_ENABLED",
        # Web config.set DeepSearch / deepresearch
        "LLM_MODEL_NAME",
        "LLM_MODEL_TYPE",
        "LLM_BASE_URL",
        "LLM_API_KEY",
        "WEB_SEARCH_ENGINE_NAME",
        "WEB_SEARCH_API_KEY",
        "WEB_SEARCH_URL",
        "EXECUTION_METHOD",
        "TAVILY_API_KEY",
        # ACRCloud (audio_tools / read_env)
        "ACR_ACCESS_KEY",
        "ACR_ACCESS_SECRET",
        "ACR_BASE_URL",
        # SkillNet / OpenJiuwen market (skill_manager)
        "SKILLNET_DOWNLOAD_TIMEOUT",
        "SKILLNET_MAX_RETRIES",
        "OPENJIUWEN_MARKET_TIMEOUT",
        "OPENJIUWEN_MARKET_BASE_URL",
        "OPENJIUWEN_ALLOWED_DOWNLOAD_HOSTS",
        "IMPORT_LOCAL_REMOTE_TIMEOUT",
        "IMPORT_LOCAL_ALLOWED_DOWNLOAD_HOSTS",
        "BROWSER_DRIVER",
        "BROWSER_PROFILE_NAME",
        "BROWSER_MANAGED_BINARY",
        "BROWSER_TIMEOUT_S",
        "BROWSER_ALLOW_SHORT_TIMEOUT_OVERRIDE",
        "MEMORY_MODE",
        "JIUWENCLAW_ENABLE_DDG_SEARCH",
        "JIUWENCLAW_ENABLE_JINA_SEARCH",
        "JIUWENCLAW_ENABLE_JINA_FETCH",
        "JIUWENCLAW_SSL_VERIFY",
    }
)

PROCESS_UNIQUE_ENV_KEYS: frozenset[str] = frozenset()

# ---------------------------------------------------------------------------
# Id / bag helpers
# ---------------------------------------------------------------------------

_DEFAULT_SERVICE_ID = "default"
_DEFAULT_AGENT_ID = "default"
EnvNsKey = tuple[str, str]

_active_bags: dict[EnvNsKey, dict[str, Any]] = {}
_staged_bags: dict[EnvNsKey, dict[str, Any]] = {}

# Unbound sentinel: distinguish "not bound" from bound empty dict ``{}``.
_UNBOUND: object = object()

_task_env_overlay: ContextVar[Any] = ContextVar(
    "jiuwenclaw_task_env_overlay", default=_UNBOUND
)
_agent_env_ns: ContextVar[EnvNsKey | None] = ContextVar(
    "jiuwenclaw_agent_env_ns", default=None
)

_mirrored_once = False


class EnvNsIdError(ValueError):
    """Raised when service_id / agent_id contains ``__`` or is otherwise invalid."""


def normalize_env_ns_id(value: str | None, *, default: str = _DEFAULT_AGENT_ID) -> str:
    if value is None:
        text = default
    else:
        text = str(value).strip() or default
    if "__" in text:
        raise EnvNsIdError(f"env ns id must not contain '__': {text!r}")
    return text


def get_bound_agent_env_ns() -> EnvNsKey | None:
    """Return the currently bound (service_id, agent_id), or None if unbound."""
    return _agent_env_ns.get()


def resolve_env_ns(
    service_id: str | None = None,
    agent_id: str | None = None,
) -> EnvNsKey:
    """Resolve bag key: explicit args > ContextVar > default/default."""
    bound = _agent_env_ns.get()
    if service_id is None and agent_id is None and bound is not None:
        return bound
    sid = normalize_env_ns_id(
        service_id if service_id is not None else (bound[0] if bound else _DEFAULT_SERVICE_ID),
        default=_DEFAULT_SERVICE_ID,
    )
    aid = normalize_env_ns_id(
        agent_id if agent_id is not None else (bound[1] if bound else _DEFAULT_AGENT_ID),
        default=_DEFAULT_AGENT_ID,
    )
    return sid, aid


def make_env_ns_key(service_id: str, agent_id: str, name: str) -> str:
    sid = normalize_env_ns_id(service_id, default=_DEFAULT_SERVICE_ID)
    aid = normalize_env_ns_id(agent_id, default=_DEFAULT_AGENT_ID)
    logical = str(name)
    if "__" in logical:
        raise EnvNsIdError(f"logical env key must not contain '__': {logical!r}")
    return f"{sid}__{aid}__{logical}"


def parse_env_ns_key(full_key: str) -> tuple[str, str, str] | None:
    parts = str(full_key).split("__", 2)
    if len(parts) != 3:
        return None
    sid, aid, logical = parts
    if not sid or not aid or not logical:
        return None
    if "__" in sid or "__" in aid:
        return None
    try:
        normalize_env_ns_id(sid)
        normalize_env_ns_id(aid)
    except EnvNsIdError:
        return None
    return sid, aid, logical


def _bag(store: dict[EnvNsKey, dict[str, Any]], key: EnvNsKey) -> dict[str, Any]:
    bag = store.get(key)
    if bag is None:
        bag = {}
        store[key] = bag
    return bag


def get_active_env(
    service_id: str | None = None,
    agent_id: str | None = None,
) -> dict[str, Any]:
    return dict(_bag(_active_bags, resolve_env_ns(service_id, agent_id)))


def get_staged_env(
    service_id: str | None = None,
    agent_id: str | None = None,
) -> dict[str, Any]:
    """Return a copy of staged env overrides for the resolved ``(sid, aid)``."""
    return dict(_bag(_staged_bags, resolve_env_ns(service_id, agent_id)))


def clear_staged_env(
    service_id: str | None = None,
    agent_id: str | None = None,
) -> None:
    key = resolve_env_ns(service_id, agent_id)
    _staged_bags.pop(key, None)


def effective_tip(
    service_id: str | None = None,
    agent_id: str | None = None,
) -> dict[str, Any]:
    """Formula B: ``active ∪ staged`` (staged wins)."""
    key = resolve_env_ns(service_id, agent_id)
    merged = dict(_bag(_active_bags, key))
    merged.update(_bag(_staged_bags, key))
    return merged


def stage_env_overrides(
    env_overrides: dict[str, Any] | None,
    *,
    service_id: str | None = None,
    agent_id: str | None = None,
) -> None:
    """Merge env reload payload into staged bag without touching active."""
    if not isinstance(env_overrides, dict):
        return
    bag = _bag(_staged_bags, resolve_env_ns(service_id, agent_id))
    for env_key, env_value in env_overrides.items():
        key = str(env_key)
        if key in SPAWN_ENV_KEYS:
            logger.warning("拒绝 stage 轨道 A 键: %s", key)
            continue
        if env_value is None:
            bag.pop(key, None)
        else:
            text = str(env_value)
            if key in _EMPTY_OMIT_ENV_KEYS and not text.strip():
                continue
            bag[key] = text


def promote_staged_env(
    *,
    service_id: str | None = None,
    agent_id: str | None = None,
) -> None:
    """Promote staged bag into active + namespaced os.environ for this pair."""
    key = resolve_env_ns(service_id, agent_id)
    staged = _staged_bags.get(key)
    if not staged:
        return
    active = _bag(_active_bags, key)
    sid, aid = key
    for name, value in list(staged.items()):
        if value is None:
            active.pop(name, None)
            _pop_ns_os(sid, aid, name)
        else:
            active[name] = value
            _set_ns_os(sid, aid, name, value)
    _staged_bags.pop(key, None)


# Incremental reload must not seal empty model credentials into tip (OfficeClaw
# often sends API_BASE="" when callbackEnv is not yet resolved). Null still deletes.
_EMPTY_OMIT_ENV_KEYS: frozenset[str] = frozenset(
    {
        "API_BASE",
        "API_KEY",
        "MODEL_PROVIDER",
        "EMBED_API_BASE",
        "EMBED_API_KEY",
    }
)


def apply_env_overrides_to_active(
    env_overrides: dict[str, Any] | None,
    *,
    service_id: str | None = None,
    agent_id: str | None = None,
) -> None:
    """Write env overrides directly to active + ns (cold start / sync replace)."""
    if not isinstance(env_overrides, dict):
        return
    key = resolve_env_ns(service_id, agent_id)
    active = _bag(_active_bags, key)
    sid, aid = key
    for env_key, env_value in env_overrides.items():
        name = str(env_key)
        if name in SPAWN_ENV_KEYS:
            logger.warning(
                "拒绝将轨道 A 键写入 active tip: %s (sid=%s aid=%s)", name, sid, aid
            )
            continue
        if env_value is None:
            active.pop(name, None)
            _pop_ns_os(sid, aid, name)
        else:
            value = str(env_value)
            if name in _EMPTY_OMIT_ENV_KEYS and not value.strip():
                continue
            active[name] = value
            _set_ns_os(sid, aid, name, value)


def replace_active_env(
    env_overrides: dict[str, Any] | None,
    *,
    service_id: str | None = None,
    agent_id: str | None = None,
    clear_staged: bool = True,
) -> None:
    """Full-replace active tip + ns for one ``(sid, aid)`` (sync path)."""
    key = resolve_env_ns(service_id, agent_id)
    sid, aid = key
    previous = dict(_bag(_active_bags, key))
    new_map: dict[str, Any] = {}
    if isinstance(env_overrides, dict):
        for env_key, env_value in env_overrides.items():
            name = str(env_key)
            if name in SPAWN_ENV_KEYS:
                continue
            if env_value is None:
                continue
            new_map[name] = str(env_value)
    _active_bags[key] = new_map
    # Drop keys that disappeared
    for name in previous:
        if name not in new_map:
            _pop_ns_os(sid, aid, name)
    for name, value in new_map.items():
        _set_ns_os(sid, aid, name, value)
    if clear_staged:
        _staged_bags.pop(key, None)


def clear_agent_env_ns(service_id: str, agent_id: str) -> None:
    """Wipe staged + active tip/ns for one ``(service_id, agent_id)`` pair."""
    clear_staged_env(service_id=service_id, agent_id=agent_id)
    replace_active_env(
        {},
        service_id=service_id,
        agent_id=agent_id,
        clear_staged=True,
    )


def apply_env_removals(
    removals: dict[str, None] | None,
    *,
    service_id: str | None = None,
    agent_id: str | None = None,
) -> None:
    """Remove env keys from active, staged, and namespaced os.environ for one pair."""
    if not isinstance(removals, dict) or not removals:
        return
    key = resolve_env_ns(service_id, agent_id)
    sid, aid = key
    active = _bag(_active_bags, key)
    staged = _bag(_staged_bags, key)
    for env_key in removals:
        name = str(env_key)
        active.pop(name, None)
        staged.pop(name, None)
        _pop_ns_os(sid, aid, name)
        # Legacy bare-key cleanup only for default/default (Gateway .env compat).
        if sid == "default" and aid == "default":
            os.environ.pop(name, None)


def build_effective_env_overlay(
    *extra: dict[str, Any] | None,
    service_id: str | None = None,
    agent_id: str | None = None,
) -> dict[str, Any]:
    """Formula B tip, then merge optional extras (extras win; ``None`` pops)."""
    merged = effective_tip(service_id, agent_id)
    for part in extra:
        if isinstance(part, dict):
            for key, value in part.items():
                k = str(key)
                if value is None:
                    merged.pop(k, None)
                else:
                    text = str(value)
                    if k in _EMPTY_OMIT_ENV_KEYS and not text.strip():
                        # Omit empty credentials from sealed overlay so they do not
                        # block fallthrough; do not actively clear a good tip value.
                        continue
                    merged[k] = text
    # Drop empty credential keys already present in tip so seal does not pin "".
    for k in _EMPTY_OMIT_ENV_KEYS:
        if k in merged and not str(merged.get(k) or "").strip():
            merged.pop(k, None)
    return merged


def bind_agent_env_ns(service_id: str, agent_id: str) -> Token:
    key = resolve_env_ns(service_id, agent_id)
    return _agent_env_ns.set(key)


def reset_agent_env_ns(token: Token) -> None:
    _agent_env_ns.reset(token)


def bind_task_env_overlay(overlay: dict[str, Any] | None) -> Token:
    """Bind task-scoped overlay. Always binds a dict (``None`` → ``{}``).

    Callers must not use truthiness of the return/overlay to skip bind.
    Use :func:`reset_task_env_overlay` to unbind.
    """
    bound: dict[str, Any] = {} if overlay is None else dict(overlay)
    return _task_env_overlay.set(bound)


def reset_task_env_overlay(token: Token) -> None:
    _task_env_overlay.reset(token)


def get_task_env_overlay() -> dict[str, Any] | None:
    """Return current overlay if bound; ``None`` when unbound."""
    value = _task_env_overlay.get()
    if value is _UNBOUND:
        return None
    return value


def is_task_env_overlay_bound() -> bool:
    return _task_env_overlay.get() is not _UNBOUND


# ---------------------------------------------------------------------------
# Compat view: ENV_CONFIG_DICT → active[default,default] (tests / legacy)
# ---------------------------------------------------------------------------


class _ActiveEnvDict(MutableMapping[str, Any]):
    """MutableMapping proxy over the resolved active bag (default: default/default)."""

    def _target(self) -> dict[str, Any]:
        return _bag(_active_bags, resolve_env_ns())

    def __getitem__(self, key: str) -> Any:
        return self._target()[str(key)]

    def __setitem__(self, key: str, value: Any) -> None:
        name = str(key)
        bag = self._target()
        if value is None:
            bag.pop(name, None)
        else:
            bag[name] = value

    def __delitem__(self, key: str) -> None:
        del self._target()[str(key)]

    def __iter__(self) -> Iterator[str]:
        return iter(self._target())

    def __len__(self) -> int:
        return len(self._target())

    def clear(self) -> None:
        # Test helper: wipe all bags (active + staged) for isolation.
        _active_bags.clear()
        _staged_bags.clear()

    def update(
        self,
        other: Mapping[Any, Any] | Iterable[tuple[Any, Any]] = (),
        /,
        **kwargs: Any,
    ) -> None:
        bag = self._target()
        if isinstance(other, Mapping):
            items: Iterable[tuple[Any, Any]] = other.items()
        else:
            items = other
        for k, v in items:
            bag[str(k)] = v
        for k, v in kwargs.items():
            bag[str(k)] = v


ENV_CONFIG_DICT: MutableMapping[str, Any] = _ActiveEnvDict()


# ---------------------------------------------------------------------------
# Namespaced os.environ
# ---------------------------------------------------------------------------


def _set_ns_os(service_id: str, agent_id: str, name: str, value: str) -> None:
    if name in SPAWN_ENV_KEYS:
        logger.warning("拒绝 set_os_environ 轨道 A 键: %s", name)
        return
    os.environ[make_env_ns_key(service_id, agent_id, name)] = str(value)


def _pop_ns_os(service_id: str, agent_id: str, name: str) -> None:
    try:
        os.environ.pop(make_env_ns_key(service_id, agent_id, name), None)
    except EnvNsIdError:
        return


def set_os_environ(
    name: str,
    value: Any,
    *,
    service_id: str | None = None,
    agent_id: str | None = None,
) -> None:
    """Write track-B namespaced os.environ (+ active tip write-through)."""
    if name in SPAWN_ENV_KEYS:
        logger.warning("拒绝 set_os_environ 轨道 A 键: %s", name)
        return
    key = resolve_env_ns(service_id, agent_id)
    sid, aid = key
    active = _bag(_active_bags, key)
    if value is None:
        active.pop(str(name), None)
        _pop_ns_os(sid, aid, str(name))
        return
    text = str(value)
    active[str(name)] = text
    _set_ns_os(sid, aid, str(name), text)


def get_os_environ(
    name: str,
    default: Any = None,
    *,
    service_id: str | None = None,
    agent_id: str | None = None,
) -> Any:
    """Read track-B namespaced key only (no bare logical-key fallback)."""
    if name in SPAWN_ENV_KEYS:
        logger.warning("get_os_environ 不服务轨道 A 键: %s —— 请直读 spawn 环境", name)
        return default
    key = resolve_env_ns(service_id, agent_id)
    sid, aid = key
    try:
        ns_key = make_env_ns_key(sid, aid, name)
    except EnvNsIdError:
        return default
    if ns_key in os.environ:
        return decrypt(name, os.environ[ns_key])
    return default


def export_agent_environ(
    service_id: str,
    agent_id: str,
) -> dict[str, str]:
    """B (de-prefixed tip+ns) ∪ A (present spawn keys) ∪ C for child ``env=``.

    On Windows, also pass through platform vars (SYSTEMROOT/SystemDrive/windir/
    TEMP/COMSPEC/PATHEXT/USERPROFILE/...) that ``WSAStartup`` and ``CreateProcess``
    need; without ``SYSTEMROOT`` the child's ``import asyncio`` -> ``import
    _overlapped`` fails with WinError 10106 because the WinSock provider cannot
    initialize (mswsock.dll lives under ``%SystemRoot%\\System32``).
    """
    out: dict[str, str] = {}
    tip = effective_tip(service_id, agent_id)
    for k, v in tip.items():
        if v is None:
            continue
        out[str(k)] = str(v)
    sid = normalize_env_ns_id(service_id, default=_DEFAULT_SERVICE_ID)
    aid = normalize_env_ns_id(agent_id, default=_DEFAULT_AGENT_ID)
    prefix = f"{sid}__{aid}__"
    for ek, ev in os.environ.items():
        if ek.startswith(prefix):
            logical = ek[len(prefix):]
            if logical and logical not in out:
                out[logical] = ev
    for k in SPAWN_ENV_KEYS:
        if k in os.environ:
            out[k] = os.environ[k]
    for k in PROCESS_UNIQUE_ENV_KEYS:
        if k in os.environ:
            out[k] = os.environ[k]
    _ensure_windows_platform_env(out)
    return out


def _ensure_windows_platform_env(out: dict[str, str]) -> None:
    """Pass through OS-level vars a Windows child process needs to function.

    The curated allowlist (B/A/C) only carries business + runtime config; it
    intentionally omits platform vars. On Windows, ``WSAStartup`` (called by
    ``import _overlapped`` -> ``asyncio``) loads the WinSock provider from
    ``%SystemRoot%\\System32``; if ``SYSTEMROOT`` is absent the provider init
    fails (WinError 10106) and the child cannot even ``import asyncio``.
    Copy these through from ``os.environ`` when present and not already set,
    so business/tip config always wins over the inherited OS value.
    """
    if os.name != "nt":
        return
    for k in (
        "SYSTEMROOT",
        "SystemDrive",
        "windir",
        "TEMP",
        "TMP",
        "COMSPEC",
        "PATHEXT",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "HOMEDRIVE",
        "HOMEPATH",
        "NUMBER_OF_PROCESSORS",
        "PROCESSOR_ARCHITECTURE",
    ):
        v = os.environ.get(k)
        if v and k not in out:
            out[k] = v


def mirror_bare_business_env_to_default_ns(*, force: bool = False) -> None:
    """Whitelist-only bare → ``default__default__*`` once after load_dotenv."""
    global _mirrored_once
    if _mirrored_once and not force:
        return
    for key in BUSINESS_MIRROR_KEYS:
        if key in SPAWN_ENV_KEYS:
            continue
        ns_key = make_env_ns_key(_DEFAULT_SERVICE_ID, _DEFAULT_AGENT_ID, key)
        if ns_key in os.environ:
            continue
        if key not in os.environ:
            continue
        raw = os.environ[key]
        # Do not seal empty credentials into default tip (spawn often has API_BASE="").
        if key in _EMPTY_OMIT_ENV_KEYS and not str(raw).strip():
            continue
        os.environ[ns_key] = raw
        active = _bag(_active_bags, (_DEFAULT_SERVICE_ID, _DEFAULT_AGENT_ID))
        active.setdefault(key, raw)
    _mirrored_once = True


# ---------------------------------------------------------------------------
# Readers (seal + formula B)
# ---------------------------------------------------------------------------


def _read_from_mapping(name: str, mapping: dict[str, Any], default: Any = None) -> Any:
    if name not in mapping:
        return default
    value = mapping[name]
    if value is None or value == "":
        return default
    return decrypt(name, value) if isinstance(value, str) else value


def get_local_config(name: str, default=None):
    """Track-B reader: bound overlay (seal) → formula B tip → namespaced os."""
    if name in SPAWN_ENV_KEYS:
        logger.warning(
            "get_local_config 不服务轨道 A 键 %s —— 请直读 spawn/path API", name
        )
        return default

    overlay = _task_env_overlay.get()
    if overlay is not _UNBOUND:
        # Seal: miss => unset (no fallthrough to live tip/ns)
        return _read_from_mapping(name, overlay, default)

    tip = effective_tip()
    if name in tip:
        return _read_from_mapping(name, tip, default)

    ns_val = get_os_environ(name, default=None)
    if ns_val is not None:
        return ns_val
    return default


def read_env(name: str, default: str = "") -> str:
    """Overlay-aware ``os.environ.get`` for hot-reload paths."""
    value = get_local_config(name, default or None)
    if value is None:
        return default
    text = str(value)
    return text if text else default


def read_env_if_set(name: str) -> str | None:
    """Return env value when *name* is explicitly set.

    Bound overlay (incl. ``{}``): only overlay; miss → ``None`` (seal).
    Unbound: formula B tip, then namespaced os.environ.
    """
    overlay = _task_env_overlay.get()
    if overlay is not _UNBOUND:
        if name not in overlay:
            return None
        value = overlay[name]
        if value is None:
            return ""
        if isinstance(value, str):
            return decrypt(name, value)
        return str(value)

    tip = effective_tip()
    if name in tip:
        value = tip[name]
        if value is None:
            return ""
        if isinstance(value, str):
            return decrypt(name, value)
        return str(value)

    ns_val = get_os_environ(name, default=None)
    if ns_val is not None:
        return str(ns_val)
    return None


def read_default_headers_raw() -> str:
    """Overlay-aware raw JSON string for default HTTP headers."""
    for env_key in _DEFAULT_HEADERS_ALIASES:
        raw = read_env(env_key, "")
        if raw.strip():
            return raw.strip()
    return ""


def parse_default_headers(raw: str) -> dict[str, str] | None:
    """Parse and validate default_headers JSON; return None when empty."""
    text = (raw or "").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"default_headers is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("default_headers must be a JSON object")
    return {str(k): str(v) for k, v in parsed.items() if v is not None}


def read_default_headers() -> dict[str, str] | None:
    """Read overlay-aware default_headers as a header map."""
    return parse_default_headers(read_default_headers_raw())


def is_sensitive_env_name(name: str) -> bool:
    lower = name.lower()
    return (
        "api_key" in lower
        or "token" in lower
        or lower == DEFAULT_HEADERS_ENV_KEY
        or "header" in lower
    )


def set_local_config(name: str, value) -> None:
    """Legacy tip write for current ns (prefer :func:`set_os_environ`)."""
    set_os_environ(name, value if value else None)


def decrypt(name, cipher):
    reg_mod = sys.modules.get("jiuwenclaw.extensions.registry")
    if reg_mod is not None and hasattr(reg_mod, "ExtensionRegistry"):
        try:
            crypto = reg_mod.ExtensionRegistry.get_instance().get_crypto_provider()
            if is_sensitive_env_name(name) and crypto:
                return crypto.decrypt(cipher)
        except Exception as e:
            logger.warning(f"Decryption failed exception: {e}")
    return cipher


def encrypt(name, text):
    reg_mod = sys.modules.get("jiuwenclaw.extensions.registry")
    if reg_mod is not None and hasattr(reg_mod, "ExtensionRegistry"):
        try:
            crypto = reg_mod.ExtensionRegistry.get_instance().get_crypto_provider()
            if is_sensitive_env_name(name) and crypto:
                return crypto.encrypt(text)
        except Exception as e:
            logger.warning(f"Encryption failed exception: {e}")
    return text


def reset_local_env_state_for_tests() -> None:
    """Clear bags + unbound overlay/ns ContextVars (unit tests only)."""
    global _mirrored_once
    _active_bags.clear()
    _staged_bags.clear()
    _mirrored_once = False
    # Best-effort: cannot fully reset ContextVar without tokens; set unbound.
    _task_env_overlay.set(_UNBOUND)
    _agent_env_ns.set(None)
