# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Plan-aligned catalog tools for team members (declarative BuiltinToolSpec providers).

Core web / vision / audio come from openjiuwen ``core.*`` elements. ENT extras
(video / image_gen / deepresearch) are grouped under ``swarm.platform_catalog_tools``.
"""

from __future__ import annotations

import logging
from typing import Any

from openjiuwen.agent_teams.harness.manifest import ElementKind, harness_element
from openjiuwen.harness.manifest.builtin_elements import (
    AUDIO,
    VISION,
    WEB_FETCH,
    WEB_PAID_SEARCH,
    WEB_SEARCH,
)

from jiuwenclaw.agentserver.tools.multimodal_config import (
    apply_audio_model_config_from_yaml,
    apply_video_model_config_from_yaml,
    apply_vision_model_config_from_yaml,
    dedicated_multimodal_model_configured,
)
from jiuwenclaw.local_env_config import read_env

logger = logging.getLogger(__name__)

PLATFORM_CATALOG_TOOLS = "swarm.platform_catalog_tools"

# Re-export core names for config_specs.
CORE_WEB_SEARCH = WEB_SEARCH
CORE_WEB_FETCH = WEB_FETCH
CORE_WEB_PAID_SEARCH = WEB_PAID_SEARCH
CORE_VISION = VISION
CORE_AUDIO = AUDIO


def _parse_int(raw: Any, default: int) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def vision_tool_params(config: dict[str, Any] | None) -> dict[str, Any]:
    """Bake ``VisionModelConfig`` kwargs; empty disables ``core.vision``."""
    cfg = config if isinstance(config, dict) else {}
    if not dedicated_multimodal_model_configured(cfg, "vision"):
        return {"vision_model_config": {}}
    apply_vision_model_config_from_yaml(cfg)
    api_key = str(read_env("VISION_API_KEY")).strip()
    base_url = str(read_env("VISION_BASE_URL") or read_env("VISION_API_BASE")).strip()
    model_name = str(read_env("VISION_MODEL") or read_env("VISION_MODEL_NAME")).strip()
    if not api_key or not base_url or not model_name:
        return {"vision_model_config": {}}
    return {
        "vision_model_config": {
            "api_key": api_key,
            "base_url": base_url,
            "model": model_name,
            "max_retries": _parse_int(read_env("VISION_MAX_RETRIES", "3"), 3),
        }
    }


def audio_tool_params(config: dict[str, Any] | None) -> dict[str, Any]:
    """Bake audio params; match plan: no dedicated key → do not mount audio tools.

    ``core.audio`` always yields ``audio_metadata`` when ``dedicated=False``; we
    omit the BuiltinToolSpec entirely in that case from config_specs instead.
    """
    cfg = config if isinstance(config, dict) else {}
    if not dedicated_multimodal_model_configured(cfg, "audio"):
        return {"dedicated": False, "audio_model_config": {}}
    apply_audio_model_config_from_yaml(cfg)
    api_key = str(read_env("AUDIO_API_KEY")).strip()
    base_url = str(read_env("AUDIO_BASE_URL") or read_env("AUDIO_API_BASE")).strip()
    if not api_key or not base_url:
        return {"dedicated": False, "audio_model_config": {}}
    transcription_model = str(
        read_env("AUDIO_TRANSCRIPTION_MODEL") or read_env("AUDIO_MODEL_NAME")
    ).strip()
    question_answering_model = str(
        read_env("AUDIO_QUESTION_ANSWERING_MODEL") or read_env("AUDIO_MODEL_NAME")
    ).strip()
    audio_cfg: dict[str, Any] = {
        "api_key": api_key,
        "base_url": base_url,
        "max_retries": _parse_int(read_env("AUDIO_MAX_RETRIES", "3"), 3),
        "http_timeout": _parse_int(read_env("AUDIO_HTTP_TIMEOUT", "20"), 20),
        "max_audio_bytes": _parse_int(
            read_env("AUDIO_MAX_AUDIO_BYTES", str(25 * 1024 * 1024)),
            25 * 1024 * 1024,
        ),
    }
    acr_access_key = str(read_env("ACR_ACCESS_KEY", "")).strip()
    acr_access_secret = str(read_env("ACR_ACCESS_SECRET", "")).strip()
    acr_base_url = str(read_env("ACR_BASE_URL", "")).strip()
    if acr_access_key:
        audio_cfg["acr_access_key"] = acr_access_key
    if acr_access_secret:
        audio_cfg["acr_access_secret"] = acr_access_secret
    if acr_base_url:
        audio_cfg["acr_base_url"] = acr_base_url
    if transcription_model:
        audio_cfg["transcription_model"] = transcription_model
    if question_answering_model:
        audio_cfg["question_answering_model"] = question_answering_model
    return {"dedicated": True, "audio_model_config": audio_cfg}


def platform_catalog_tool_params(config: dict[str, Any] | None) -> dict[str, Any]:
    """Flags for ENT extras (video / image_gen / deepresearch)."""
    cfg = config if isinstance(config, dict) else {}
    enable_video = bool(dedicated_multimodal_model_configured(cfg, "video"))
    if enable_video:
        apply_video_model_config_from_yaml(cfg)
    enable_image_gen = bool(dedicated_multimodal_model_configured(cfg, "image_gen"))
    return {
        "enable_video": enable_video,
        "enable_image_gen": enable_image_gen,
        "enable_deepresearch": True,
    }


@harness_element(
    kind=ElementKind.TOOL,
    name=PLATFORM_CATALOG_TOOLS,
    description="ENT plan-parity catalog extras: video, image_gen, deepresearch.",
)
def _build_platform_catalog_tools(params: dict[str, Any], context: Any) -> list[Any]:
    """Build ENT catalog tools that are not covered by ``core.*`` elements."""
    tools: list[Any] = []
    p = params if isinstance(params, dict) else {}
    agent_id = str(
        getattr(context, "member_card_id", None)
        or getattr(context, "member_name", None)
        or ""
    ).strip() or None

    if p.get("enable_video"):
        try:
            from jiuwenclaw.agentserver.tools.video_tools import video_understanding

            tools.append(video_understanding)
        except Exception as exc:
            logger.warning("[swarm.catalog_tools] video_understanding failed: %s", exc)

    if p.get("enable_image_gen"):
        try:
            from jiuwenclaw.agentserver.tools.image_gen_tools import (
                create_session_text_to_image_tool,
            )

            tools.append(create_session_text_to_image_tool(agent_id or "team_member"))
        except Exception as exc:
            logger.warning("[swarm.catalog_tools] image_gen failed: %s", exc)

    if p.get("enable_deepresearch", True):
        try:
            from jiuwenclaw.agentserver.tools.deepresearch_tools import get_deepresearch_tools

            tools.extend(list(get_deepresearch_tools() or []))
        except Exception as exc:
            logger.warning("[swarm.catalog_tools] deepresearch failed: %s", exc)

    logger.info(
        "[swarm.catalog_tools] built %d tools (video=%s image_gen=%s deepresearch=%s)",
        len(tools),
        bool(p.get("enable_video")),
        bool(p.get("enable_image_gen")),
        bool(p.get("enable_deepresearch", True)),
    )
    return tools


__all__ = [
    "PLATFORM_CATALOG_TOOLS",
    "CORE_WEB_SEARCH",
    "CORE_WEB_FETCH",
    "CORE_WEB_PAID_SEARCH",
    "CORE_VISION",
    "CORE_AUDIO",
    "vision_tool_params",
    "audio_tool_params",
    "platform_catalog_tool_params",
]
