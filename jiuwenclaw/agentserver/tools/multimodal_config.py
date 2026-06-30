# coding: utf-8
"""
多模态工具配置管理模块

配置优先级:
1. models.{audio/vision/video/image_gen}.model_config
2. embed.{audio_model/video_model/vision_model/image_gen_model} 和 embed.embed_api_key/embed_api_base
3. 环境变量 MODEL_NAME, API_KEY, API_BASE
"""
import logging
import os
from collections.abc import Iterable, Mapping
from typing import Any

from jiuwenclaw.local_env_config import read_env
from jiuwenclaw.utils import resolve_env_vars

logger = logging.getLogger(__name__)

# Full env reload snapshots from officeclaw include main LLM credentials.
_FULL_ENV_SNAPSHOT_MARKERS = ("API_KEY", "MODEL_NAME")

# Anchor key per multimodal group; omission in a full snapshot means the group was removed.
MULTIMODAL_ENV_ANCHOR_KEYS: dict[str, str] = {
    "image_gen": "IMAGE_GEN_API_KEY",
    "vision": "VISION_API_KEY",
    "audio": "AUDIO_API_KEY",
    "video": "VIDEO_API_KEY",
}

MULTIMODAL_ENV_GROUP_KEYS: dict[str, tuple[str, ...]] = {
    "image_gen": (
        "IMAGE_GEN_API_KEY",
        "IMAGE_GEN_API_BASE",
        "IMAGE_GEN_MODEL_NAME",
        "IMAGE_GEN_PROVIDER",
    ),
    "vision": (
        "VISION_API_KEY",
        "VISION_API_BASE",
        "VISION_MODEL_NAME",
        "VISION_PROVIDER",
    ),
    "audio": (
        "AUDIO_API_KEY",
        "AUDIO_API_BASE",
        "AUDIO_MODEL_NAME",
        "AUDIO_PROVIDER",
    ),
    "video": (
        "VIDEO_API_KEY",
        "VIDEO_API_BASE",
        "VIDEO_MODEL_NAME",
        "VIDEO_PROVIDER",
    ),
}

# Groups disabled by UI env omission reconcile (yaml literal must not re-enable).
_MULTIMODAL_ENV_OMISSION_DISABLED: set[str] = set()


def is_full_env_reload_snapshot(env: dict[str, Any] | None) -> bool:
    """True when env looks like a full credential snapshot (not a partial patch)."""
    if not isinstance(env, dict) or not env:
        return False
    return all(marker in env for marker in _FULL_ENV_SNAPSHOT_MARKERS)


def _anchor_value_non_empty(env: dict[str, Any] | None, anchor: str) -> bool:
    if not isinstance(env, dict):
        return False
    current = env.get(anchor)
    return current is not None and str(current).strip() != ""


def build_multimodal_reconcile_env(
        *,
        active_env: dict[str, Any] | None = None,
        staged_env: dict[str, Any] | None = None,
        os_environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Merge active, staged, and multimodal anchor ``os.environ`` for omission reconcile."""
    from jiuwenclaw.local_env_config import ENV_CONFIG_DICT, get_staged_env

    merged: dict[str, str] = {}
    source_active = active_env if active_env is not None else ENV_CONFIG_DICT
    if isinstance(source_active, dict):
        for key, value in source_active.items():
            if value is not None:
                merged[str(key)] = str(value)
    source_staged = staged_env if staged_env is not None else get_staged_env()
    if isinstance(source_staged, dict):
        for key, value in source_staged.items():
            if value is not None:
                merged[str(key)] = str(value)
    environ = os_environ if os_environ is not None else os.environ
    for keys in MULTIMODAL_ENV_GROUP_KEYS.values():
        anchor = keys[0]
        raw = environ.get(anchor)
        if raw is not None and str(raw).strip():
            merged[anchor] = str(raw)
    return merged


def _multimodal_anchor_was_active(
        anchor: str,
        previous_env: dict[str, Any] | None,
        active_env: dict[str, Any] | None,
) -> bool:
    if _anchor_value_non_empty(previous_env, anchor):
        return True
    return _anchor_value_non_empty(active_env, anchor)


def merge_reload_env_snapshot(
        previous: dict[str, Any] | None,
        env: Any,
) -> dict[str, Any]:
    """Update cached full env snapshot; preserve on config-only reload (``env is None``)."""
    if env is None:
        return dict(previous) if isinstance(previous, dict) else {}
    if not isinstance(env, dict) or not env:
        return dict(previous) if isinstance(previous, dict) else {}
    if is_full_env_reload_snapshot(env):
        return dict(env)
    base = dict(previous) if isinstance(previous, dict) else {}
    base.update(env)
    return base


def multimodal_env_anchor_present(group: str) -> bool:
    """Return True when the group's anchor key is set in effective env (incl. overlay)."""
    anchor = MULTIMODAL_ENV_ANCHOR_KEYS.get(group)
    if not anchor:
        return False
    return bool(str(read_env(anchor) or "").strip())


def multimodal_env_omission_disabled(group: str) -> bool:
    """Return True when the group was disabled via env omission reconcile."""
    return group in _MULTIMODAL_ENV_OMISSION_DISABLED


def reset_multimodal_env_omission_disabled() -> None:
    """Clear omission-disabled flags (e.g. between tests)."""
    _MULTIMODAL_ENV_OMISSION_DISABLED.clear()


def sync_multimodal_env_omission_state(
    removals: dict[str, None],
    new_env: dict[str, Any] | None,
) -> None:
    """Mark groups disabled on env omission; re-enable when anchor reappears in snapshot."""
    if removals:
        for group, keys in MULTIMODAL_ENV_GROUP_KEYS.items():
            anchor = keys[0]
            if anchor in removals:
                _MULTIMODAL_ENV_OMISSION_DISABLED.add(group)
    if not isinstance(new_env, dict):
        return
    for group, anchor in MULTIMODAL_ENV_ANCHOR_KEYS.items():
        value = new_env.get(anchor)
        if value is not None and str(value).strip():
            _MULTIMODAL_ENV_OMISSION_DISABLED.discard(group)


def _api_key_is_env_bound(raw_api_key: Any) -> bool:
    """Return True when raw yaml api_key uses env placeholder syntax."""
    return isinstance(raw_api_key, str) and "${" in raw_api_key


def _raw_model_api_key(model_type: str) -> Any:
    """Read unresolved api_key from raw config.yaml snapshot."""
    from jiuwenclaw.config import get_config_raw

    return _get_model_config(get_config_raw(), model_type).get("api_key")


def _allow_embed_main_api_fallback(
    model_type: str,
    config_base: dict[str, Any],
    *,
    strict: bool,
) -> bool:
    """Env-bound yaml api_key with empty resolution must not fall back to main API."""
    if strict:
        return True
    mc = _get_model_config(config_base, model_type)
    resolved = str(mc.get("api_key") or "").strip()
    if resolved:
        return True
    raw_passed = mc.get("api_key")
    if isinstance(raw_passed, str) and _api_key_is_env_bound(raw_passed):
        return False
    raw_yaml = _raw_model_api_key(model_type)
    if _api_key_is_env_bound(raw_yaml):
        return False
    return True


def _skip_apply_after_env_omission(group: str, *, caller: str) -> bool:
    """Return True when apply should skip because env omission disabled the group."""
    if not multimodal_env_omission_disabled(group):
        return False
    logger.debug(
        "%s skipped: group %s disabled by env omission reconcile",
        caller,
        group,
    )
    return True


def clear_multimodal_env_groups(group_names: Iterable[str]) -> None:
    """Remove env keys for the given multimodal groups from all env layers."""
    from jiuwenclaw.local_env_config import apply_env_removals

    removals: dict[str, None] = {}
    for group in group_names:
        keys = MULTIMODAL_ENV_GROUP_KEYS.get(group)
        if not keys:
            continue
        for key in keys:
            removals[key] = None
    apply_env_removals(removals)


def infer_multimodal_env_removals(
        previous_env: dict[str, Any] | None,
        new_env: dict[str, Any] | None,
        *,
        active_env: dict[str, Any] | None = None,
) -> dict[str, None]:
    """Infer multimodal env keys to clear when frontend omits them from a full reload snapshot."""
    if not is_full_env_reload_snapshot(new_env):
        return {}
    if not isinstance(new_env, dict):
        return {}
    reconcile_env = (
        active_env
        if active_env is not None
        else build_multimodal_reconcile_env()
    )
    removals: dict[str, None] = {}
    for _group, keys in MULTIMODAL_ENV_GROUP_KEYS.items():
        anchor = keys[0]
        if anchor in new_env:
            continue
        if not _multimodal_anchor_was_active(anchor, previous_env, reconcile_env):
            continue
        for key in keys:
            removals[key] = None
    return removals


def _parse_bool(val: Any, default: bool = False) -> bool:
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    s = str(val).strip().lower()
    return s in ("1", "true", "yes", "on")


def _get_embed_config(config_base: dict[str, Any]) -> dict[str, Any]:
    embed = config_base.get("embed", {})
    return embed if isinstance(embed, dict) else {}


def _get_model_section(config_base: dict[str, Any], model_type: str) -> dict[str, Any]:
    """Return the full ``models.{model_type}`` block (not only model_client_config)."""
    if not isinstance(config_base, dict):
        return {}

    raw_models = config_base.get("models")
    if isinstance(raw_models, dict):
        inner = raw_models.get(model_type)
        return inner if isinstance(inner, dict) else {}

    if not isinstance(raw_models, list):
        return {}

    for block in raw_models:
        if isinstance(block, dict) and model_type in block:
            inner = block.get(model_type)
            return inner if isinstance(inner, dict) else {}
    return {}


def _get_model_config(config_base: dict[str, Any], model_type: str) -> dict[str, Any]:
    """
    从 config.yaml 中读取指定类型的模型配置

    Args:
        config_base: 配置字典
        model_type: 模型类型，如 'audio', 'vision', 'video', 'image_gen'

    Returns:
        模型配置字典
    """
    inner = _get_model_section(config_base, model_type)
    if not inner:
        return {}
    mc = inner.get("model_config") or inner.get("model_client_config")
    return mc if isinstance(mc, dict) else {}


_EMBED_MODEL_KEY_MAP = {
    "audio": "audio_model",
    "vision": "vision_model",
    "video": "video_model",
    "image_gen": "image_gen_model",
}


def dedicated_multimodal_model_configured(
    config_base: dict[str, Any] | None, model_type: str
) -> bool:
    """Whether ``models.{model_type}`` has its own non-empty ``api_key`` (after YAML env resolution).

    Used to gate image / video / **audio** tools (含 ``audio_metadata`` 与 LLM 音频能力)，在未配置
    ``models.{type}.model_config`` 独立 ``api_key`` 时不挂载，避免仅存在主对话 ``API_KEY`` 时误注册。
    Groups disabled via env omission reconcile return False even when yaml still has literal ``api_key``.
    （``apply_*_model_config_from_yaml`` 仍可能回落到 embed / 主 API 写环境变量，与是否注册工具无关。）
    与 ``get_mcp_tools`` 仅注册 ``web_search`` 作为搜索入口同理。
    """
    if model_type not in ("audio", "vision", "video", "image_gen"):
        return False
    if not isinstance(config_base, dict):
        return False
    if multimodal_env_omission_disabled(model_type):
        return False
    mc = _get_model_config(config_base, model_type)
    raw_api_key = mc.get("api_key")
    api_key = str(resolve_env_vars(raw_api_key) if raw_api_key is not None else "").strip()
    return bool(api_key)


def _get_embed_model_name(embed_cfg: dict[str, Any], model_type: str) -> str:
    """
    从 embed 配置中获取指定类型的模型名称

    Args:
        embed_cfg: embed 配置字典
        model_type: 模型类型，如 'audio', 'vision', 'video', 'image_gen'

    Returns:
        模型名称字符串
    """
    key = _EMBED_MODEL_KEY_MAP.get(model_type)
    if key and isinstance(embed_cfg, dict):
        return str(embed_cfg.get(key) or "").strip()
    return ""


def apply_audio_model_config_from_yaml(config_base: dict[str, Any] | None) -> None:
    """
    从 config.yaml 读取音频模型配置并设置环境变量

    配置优先级:
    1. models.audio.model_config
    2. embed.audio_model + embed.embed_api_key/embed_api_base
    3. 环境变量 MODEL_NAME, API_KEY, API_BASE
    """
    if not isinstance(config_base, dict):
        return
    if _skip_apply_after_env_omission(
        "audio", caller="apply_audio_model_config_from_yaml"
    ):
        return

    mc = _get_model_config(config_base, "audio")
    embed_cfg = _get_embed_config(config_base)

    api_key = str(mc.get("api_key") or "").strip()
    api_base = str(mc.get("api_base") or "").strip()
    model_name = str(mc.get("model_name") or mc.get("model") or "").strip()
    provider = str(mc.get("model_provider") or "").strip()
    strict = _parse_bool(mc.get("strict"), default=False)
    allow_fallback = _allow_embed_main_api_fallback(
        "audio", config_base, strict=strict
    )

    if not strict:
        if not api_key and allow_fallback:
            api_key = str(
                embed_cfg.get("embed_api_key") or read_env("API_KEY", "")
            ).strip()
        if not api_base and allow_fallback:
            api_base = str(
                embed_cfg.get("embed_api_base") or read_env("API_BASE", "")
            ).strip()
        if not model_name and allow_fallback:
            model_name = (
                _get_embed_model_name(embed_cfg, "audio")
                or read_env("MODEL_NAME", "").strip()
            )
        if not provider and allow_fallback:
            provider = read_env("MODEL_PROVIDER", "").strip()

    if api_key:
        os.environ["AUDIO_API_KEY"] = api_key
    if api_base:
        os.environ["AUDIO_API_BASE"] = api_base
    if model_name:
        os.environ["AUDIO_MODEL_NAME"] = model_name
    if provider:
        os.environ["AUDIO_PROVIDER"] = provider


def apply_vision_model_config_from_yaml(config_base: dict[str, Any] | None) -> None:
    """
    从 config.yaml 读取图像模型配置并设置环境变量

    配置优先级:
    1. models.vision.model_config
    2. embed.vision_model + embed.embed_api_key/embed_api_base
    3. 环境变量 MODEL_NAME, API_KEY, API_BASE
    """
    if not isinstance(config_base, dict):
        return
    if _skip_apply_after_env_omission(
        "vision", caller="apply_vision_model_config_from_yaml"
    ):
        return

    mc = _get_model_config(config_base, "vision")
    embed_cfg = _get_embed_config(config_base)

    api_key = str(mc.get("api_key") or "").strip()
    api_base = str(mc.get("api_base") or "").strip()
    model_name = str(mc.get("model_name") or mc.get("model") or "").strip()
    provider = str(mc.get("model_provider") or "").strip()
    strict = _parse_bool(mc.get("strict"), default=False)
    allow_fallback = _allow_embed_main_api_fallback(
        "vision", config_base, strict=strict
    )

    if not strict:
        if not api_key and allow_fallback:
            api_key = str(
                embed_cfg.get("embed_api_key") or read_env("API_KEY", "")
            ).strip()
        if not api_base and allow_fallback:
            api_base = str(
                embed_cfg.get("embed_api_base") or read_env("API_BASE", "")
            ).strip()
        if not model_name and allow_fallback:
            model_name = (
                _get_embed_model_name(embed_cfg, "vision")
                or read_env("MODEL_NAME", "").strip()
            )
        if not provider and allow_fallback:
            provider = read_env("MODEL_PROVIDER", "").strip()

    if api_key:
        os.environ["VISION_API_KEY"] = api_key
    if api_base:
        os.environ["VISION_API_BASE"] = api_base
    if model_name:
        os.environ["VISION_MODEL_NAME"] = model_name
    if provider:
        os.environ["VISION_PROVIDER"] = provider


def apply_video_model_config_from_yaml(config_base: dict[str, Any] | None) -> None:
    """
    从 config.yaml 读取视频模型配置并设置环境变量

    配置优先级:
    1. models.video.model_config
    2. embed.video_model + embed.embed_api_key/embed_api_base
    3. 环境变量 MODEL_NAME, API_KEY, API_BASE
    """
    if not isinstance(config_base, dict):
        os.environ.pop("VIDEO_UNDERSTANDING_STRICT", None)
        return
    if _skip_apply_after_env_omission(
        "video", caller="apply_video_model_config_from_yaml"
    ):
        return

    mc = _get_model_config(config_base, "video")
    embed_cfg = _get_embed_config(config_base)

    api_key = str(mc.get("api_key") or "").strip()
    api_base = str(mc.get("api_base") or "").strip()
    model_name = str(mc.get("model_name") or mc.get("model") or "").strip()
    provider = str(mc.get("model_provider") or "").strip()
    strict = _parse_bool(mc.get("strict"), default=False)
    allow_fallback = _allow_embed_main_api_fallback(
        "video", config_base, strict=strict
    )

    if strict:
        os.environ["VIDEO_UNDERSTANDING_STRICT"] = "1"
    else:
        os.environ.pop("VIDEO_UNDERSTANDING_STRICT", None)
        if not api_key and allow_fallback:
            api_key = str(
                embed_cfg.get("embed_api_key") or read_env("API_KEY", "")
            ).strip()
        if not api_base and allow_fallback:
            api_base = str(
                embed_cfg.get("embed_api_base") or read_env("API_BASE", "")
            ).strip()
        if not model_name and allow_fallback:
            model_name = (
                _get_embed_model_name(embed_cfg, "video")
                or read_env("MODEL_NAME", "").strip()
            )
        if not provider and allow_fallback:
            provider = read_env("MODEL_PROVIDER", "").strip()

    if api_key:
        os.environ["VIDEO_API_KEY"] = api_key
    if api_base:
        os.environ["VIDEO_API_BASE"] = api_base
    if model_name:
        os.environ["VIDEO_MODEL_NAME"] = model_name
    if provider:
        os.environ["VIDEO_PROVIDER"] = provider


def apply_image_gen_model_config_from_yaml(config_base: dict[str, Any] | None) -> None:
    """
    从 config.yaml 读取图像生成模型配置并设置环境变量

    配置优先级:
    1. models.image_gen.model_config
    2. embed.image_gen_model + embed.embed_api_key/embed_api_base
    3. 环境变量 MODEL_NAME, API_KEY, API_BASE
    """
    if not isinstance(config_base, dict):
        return
    if _skip_apply_after_env_omission(
        "image_gen", caller="apply_image_gen_model_config_from_yaml"
    ):
        return

    mc = _get_model_config(config_base, "image_gen")
    embed_cfg = _get_embed_config(config_base)

    api_key = str(mc.get("api_key") or "").strip()
    api_base = str(mc.get("api_base") or "").strip()
    model_name = str(mc.get("model_name") or mc.get("model") or "").strip()
    provider = str(mc.get("model_provider") or mc.get("client_provider") or "").strip()
    strict = _parse_bool(mc.get("strict"), default=False)
    allow_fallback = _allow_embed_main_api_fallback(
        "image_gen", config_base, strict=strict
    )

    if not strict:
        if not api_key and allow_fallback:
            api_key = str(
                embed_cfg.get("embed_api_key") or read_env("API_KEY", "")
            ).strip()
        if not api_base and allow_fallback:
            api_base = str(
                embed_cfg.get("embed_api_base") or read_env("API_BASE", "")
            ).strip()
        if not model_name and allow_fallback:
            model_name = (
                _get_embed_model_name(embed_cfg, "image_gen")
                or read_env("MODEL_NAME", "").strip()
            )
        if not provider and allow_fallback:
            provider = read_env("MODEL_PROVIDER", "").strip()

    if api_key:
        os.environ["IMAGE_GEN_API_KEY"] = api_key
    if api_base:
        os.environ["IMAGE_GEN_API_BASE"] = api_base
    if model_name:
        os.environ["IMAGE_GEN_MODEL_NAME"] = model_name
    if provider:
        os.environ["IMAGE_GEN_PROVIDER"] = provider
