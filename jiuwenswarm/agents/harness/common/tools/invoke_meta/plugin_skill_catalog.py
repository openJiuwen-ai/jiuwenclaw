# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Known PluginSkillExecTool cloud capabilities (functionName to bundleName)."""

from __future__ import annotations

import json
from typing import Any

# bundleName for Seedream image + Seedance video
_ATOMIC_BUNDLE = "com.atomicservice.5765880207845681341"
# bundleName for image understanding
_XIAOYI_BUNDLE = "xiaoyi"

# functionName (arguments.functionName) → required bundleName
PLUGIN_SKILL_CATALOG: dict[str, str] = {
    # 生图 Seedream
    "seedreamLite4Skill": _ATOMIC_BUNDLE,
    "SeedreamPro4Skill": _ATOMIC_BUNDLE,
    # 生视频 Seedance
    "seedanceMiniTask": _ATOMIC_BUNDLE,
    "seedanceMiniTaskQuery": _ATOMIC_BUNDLE,
    # 生音乐 MiniMax
    "lyricsGeneration": _ATOMIC_BUNDLE,
    "musicGeneration": _ATOMIC_BUNDLE,
    # 图像理解
    "imageUnderStandStream": _XIAOYI_BUNDLE,
}

_CATALOG_HELP = (
    "仅允许白名单中的云端能力，禁止臆造 bundleName/functionName。\n"
    "生图：functionName=seedreamLite4Skill|SeedreamPro4Skill，"
    f"bundleName={_ATOMIC_BUNDLE}，必填 prompt；"
    "size 仅 1K|2K（1024x1024→1K，2048x2048→2K）；"
    "max_images 仅 Lite（1~15），Pro 勿传。\n"
    "生视频：functionName=seedanceMiniTask（提交，必填 content；默认提交后自动轮询 "
    "seedanceMiniTaskQuery 直到成片，arguments.wait=false 则只返回 task_id）或 "
    "seedanceMiniTaskQuery（查询，必填 id），"
    f"bundleName={_ATOMIC_BUNDLE}。\n"
    "生音乐：functionName=lyricsGeneration（写词/改词，content.prompt 必填；"
    "mode=write_full_song|edit，edit 须带 content.lyrics）或 "
    "musicGeneration（成曲，content.prompt 必填；人声须 lyrics 或 lyrics_optimizer=true，"
    "器乐 is_instrumental=true 时不能带 lyrics/optimizer），"
    f"bundleName={_ATOMIC_BUNDLE}，业务参数只放 content。\n"
    f"图像理解：functionName=imageUnderStandStream，bundleName={_XIAOYI_BUNDLE}，必填 imageUrl；可选 text。"
)

_SEEDREAM_FUNCS = ("seedreamLite4Skill", "SeedreamPro4Skill")
_LYRICS_FUNC = "lyricsGeneration"
_MUSIC_FUNC = "musicGeneration"
_LYRICS_MODES = ("write_full_song", "edit")
_MUSIC_FLAG_TRUE = {"1", "true", "yes", "on"}
_MUSIC_TOP_LEVEL_KEYS = (
    "prompt",
    "lyrics",
    "mode",
    "lyrics_optimizer",
    "lyrics-optimizer",
    "is_instrumental",
    "instrumental",
    "audio_setting",
    "aigc_watermark",
)
_DEFAULT_AUDIO_SETTING = {
    "sample_rate": 44100,
    "bitrate": 256000,
    "format": "mp3",
}
_SEEDREAM_SIZE_MAP = {
    "1k": "1K",
    "1024": "1K",
    "1024x1024": "1K",
    "1024*1024": "1K",
    "2k": "2K",
    "2048": "2K",
    "2048x2048": "2K",
    "2048*2048": "2K",
}


def _canonical_seedream_size(raw: Any) -> str | None:
    """Map pixel aliases to 1K|2K. None = omitted or invalid."""
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        key = str(int(raw))
    else:
        key = str(raw).strip().lower().replace(" ", "").replace("×", "x")
        if not key:
            return None
    return _SEEDREAM_SIZE_MAP.get(key)


def _as_bool(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in _MUSIC_FLAG_TRUE


def _pop_top_level_music_keys(out: dict[str, Any]) -> None:
    for key in _MUSIC_TOP_LEVEL_KEYS:
        out.pop(key, None)


def _normalize_lyrics_args(out: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    """Fold prompt/mode/lyrics into content for lyricsGeneration."""
    content = out.get("content")
    bag: dict[str, Any] = dict(content) if isinstance(content, dict) else {}
    prompt = out.get("prompt")
    if prompt is not None and str(prompt).strip():
        bag["prompt"] = str(prompt).strip()
    mode = out.get("mode")
    if mode is not None and str(mode).strip():
        bag["mode"] = str(mode).strip()
    elif not str(bag.get("mode") or "").strip():
        bag["mode"] = "write_full_song"
    lyrics = out.get("lyrics")
    if lyrics is not None and str(lyrics).strip():
        bag["lyrics"] = str(lyrics).strip()
    if not str(bag.get("lyrics") or "").strip():
        bag.pop("lyrics", None)
    _pop_top_level_music_keys(out)
    out["content"] = bag
    return out, None


def _normalize_music_args(out: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    """Fold MiniMax music fields into content; fill audio_setting / watermark defaults."""
    content = out.get("content")
    bag: dict[str, Any] = dict(content) if isinstance(content, dict) else {}
    prompt = out.get("prompt")
    if prompt is not None and str(prompt).strip():
        bag["prompt"] = str(prompt).strip()
    lyrics = out.get("lyrics")
    if lyrics is not None and str(lyrics).strip():
        bag["lyrics"] = str(lyrics).strip()
    if "lyrics-optimizer" in out:
        bag["lyrics_optimizer"] = _as_bool(out.get("lyrics-optimizer"))
    elif "lyrics_optimizer" in out:
        bag["lyrics_optimizer"] = _as_bool(out.get("lyrics_optimizer"))
    if "instrumental" in out:
        bag["is_instrumental"] = _as_bool(out.get("instrumental"))
    elif "is_instrumental" in out:
        bag["is_instrumental"] = _as_bool(out.get("is_instrumental"))
    audio_over = out.get("audio_setting")
    if isinstance(audio_over, dict):
        bag_audio = bag.get("audio_setting") if isinstance(bag.get("audio_setting"), dict) else {}
        bag["audio_setting"] = {**bag_audio, **audio_over}
    if "aigc_watermark" in out:
        bag["aigc_watermark"] = _as_bool(out.get("aigc_watermark"))

    audio_base = bag.get("audio_setting") if isinstance(bag.get("audio_setting"), dict) else {}
    bag["audio_setting"] = {**_DEFAULT_AUDIO_SETTING, **audio_base}
    if "aigc_watermark" not in bag or bag["aigc_watermark"] is None:
        bag["aigc_watermark"] = True
    else:
        bag["aigc_watermark"] = _as_bool(bag.get("aigc_watermark"))
    bag["is_instrumental"] = _as_bool(bag.get("is_instrumental", False))
    bag["lyrics_optimizer"] = _as_bool(bag.get("lyrics_optimizer", False))
    if not str(bag.get("lyrics") or "").strip():
        bag.pop("lyrics", None)

    _pop_top_level_music_keys(out)
    out["content"] = bag
    return out, None


def normalize_plugin_skill_args(
    func_name: str, params: dict[str, Any]
) -> tuple[dict[str, Any], str | None]:
    """Coerce seedream size / drop Pro max_images / wrap seedance text content / fold music content."""
    out = dict(params)
    if func_name == "seedanceMiniTask":
        content = out.get("content")
        if isinstance(content, str) and content.strip():
            out["content"] = [{"type": "text", "text": content.strip()}]
        return out, None
    if func_name == _LYRICS_FUNC:
        return _normalize_lyrics_args(out)
    if func_name == _MUSIC_FUNC:
        return _normalize_music_args(out)
    if func_name not in _SEEDREAM_FUNCS:
        return out, None

    if "size" in out and out["size"] is not None and str(out["size"]).strip() != "":
        mapped = _canonical_seedream_size(out["size"])
        if mapped is None:
            return out, (
                f"arguments.size={out['size']!r} 无效，仅允许 1K 或 2K"
                "（像素写法 1024x1024→1K、2048x2048→2K）。"
            )
        out["size"] = mapped

    if func_name == "SeedreamPro4Skill":
        out.pop("max_images", None)
        return out, None

    if "max_images" not in out or out["max_images"] is None:
        return out, None
    try:
        count = int(out["max_images"])
    except (TypeError, ValueError):
        return out, "arguments.max_images 须为 1~15 的整数（仅 Lite 可用）。"
    if count < 1 or count > 15:
        return out, "arguments.max_images 取值范围 1~15（仅 Lite 可用）。"
    out["max_images"] = count
    return out, None


def parse_plugin_json_payload(raw: Any) -> dict[str, Any]:
    """Parse cloud plugin content that may be a JSON string or dict."""
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _looks_like_http_url(value: str) -> bool:
    lower = value.strip().lower()
    return lower.startswith("http://") or lower.startswith("https://")


def _payload_dicts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    bags: list[dict[str, Any]] = [payload]
    nested = payload.get("content")
    if isinstance(nested, dict):
        bags.append(nested)
    for bag in list(bags):
        items = bag.get("items")
        if isinstance(items, list):
            bags.extend(item for item in items if isinstance(item, dict))
    return bags


def _payload_item_strings(payload: dict[str, Any]) -> list[str]:
    texts: list[str] = []
    for bag in (payload, payload.get("content") if isinstance(payload.get("content"), dict) else {}):
        if not isinstance(bag, dict):
            continue
        items = bag.get("items")
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, str) and item.strip():
                texts.append(item.strip())
    return texts


def extract_seedance_task_id(result: dict[str, Any]) -> str:
    """Read task_id from seedanceMiniTask invoke result."""
    payload = parse_plugin_json_payload(result.get("content"))
    if not payload and isinstance(result, dict):
        payload = {k: v for k, v in result.items() if k != "frames"}
    for candidate in _payload_dicts(payload):
        for key in ("task_id", "id", "taskId"):
            value = str(candidate.get(key) or "").strip()
            if value and not _looks_like_http_url(value):
                return value
    for text in _payload_item_strings(payload):
        if not _looks_like_http_url(text):
            return text
    return ""


def extract_seedance_query_state(result: dict[str, Any]) -> tuple[str, str]:
    """Return (status, video_url) from seedanceMiniTaskQuery result."""
    payload = parse_plugin_json_payload(result.get("content"))
    if not payload and isinstance(result, dict):
        payload = {k: v for k, v in result.items() if k != "frames"}
    status = ""
    video_url = ""
    for candidate in _payload_dicts(payload):
        if not status:
            status = str(candidate.get("status") or "").strip().lower()
        if not video_url:
            video_url = str(
                candidate.get("video_url") or candidate.get("videoUrl") or ""
            ).strip()
        nested = candidate.get("content")
        if isinstance(nested, dict):
            if not status:
                status = str(nested.get("status") or "").strip().lower()
            if not video_url:
                video_url = str(
                    nested.get("video_url") or nested.get("videoUrl") or ""
                ).strip()
        if status and video_url:
            break
    if not video_url:
        for text in _payload_item_strings(payload):
            if _looks_like_http_url(text):
                video_url = text
                break
    return status, video_url


def want_seedance_wait(params: dict[str, Any]) -> bool:
    """Default True; arguments.wait=false skips auto-poll after submit."""
    if "wait" not in params:
        return True
    val = params.get("wait")
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() not in {"0", "false", "no", "off"}


def validate_plugin_skill_args(func_name: str, params: dict[str, Any]) -> str | None:
    """Return error message if functionName/bundleName are not in the skill catalog; else None."""
    expected_bundle = PLUGIN_SKILL_CATALOG.get(func_name)
    if expected_bundle is None:
        allowed = ", ".join(sorted(PLUGIN_SKILL_CATALOG))
        return (
            f"不支持的 arguments.functionName={func_name!r}。"
            f"允许值：{allowed}。\n{_CATALOG_HELP}"
        )

    bundle = str(params.get("bundleName") or "").strip()
    if not bundle:
        return (
            f"缺少 arguments.bundleName；{func_name} 须为 {expected_bundle!r}。\n{_CATALOG_HELP}"
        )
    if bundle != expected_bundle:
        return (
            f"arguments.bundleName={bundle!r} 与 {func_name} 不匹配，"
            f"须为 {expected_bundle!r}（勿使用 image-generation/text-to-image/ai-draw 等臆造名）。\n"
            f"{_CATALOG_HELP}"
        )

    if func_name in _SEEDREAM_FUNCS:
        if not str(params.get("prompt") or "").strip():
            return f"{func_name} 须提供 arguments.prompt（见 seedance-image-gen / 生图 skill）。"
    elif func_name == "imageUnderStandStream":
        if not str(params.get("imageUrl") or "").strip():
            return "imageUnderStandStream 须提供 arguments.imageUrl（公网 http/https URL）。"
    elif func_name == "seedanceMiniTask":
        content = params.get("content")
        if not isinstance(content, list) or not content:
            return (
                "seedanceMiniTask 须提供 arguments.content 数组"
                "（首项 type=text 的提示词，见 seedance-video-gen）。"
            )
    elif func_name == "seedanceMiniTaskQuery":
        if not str(params.get("id") or "").strip():
            return "seedanceMiniTaskQuery 须提供 arguments.id（seedanceMiniTask 返回的 task_id）。"
    elif func_name == _LYRICS_FUNC:
        content = params.get("content")
        if not isinstance(content, dict):
            return "lyricsGeneration 须提供 arguments.content 对象（必填 prompt，见 music-generation）。"
        if not str(content.get("prompt") or "").strip():
            return "lyricsGeneration 须提供 content.prompt。"
        mode = str(content.get("mode") or "write_full_song").strip()
        if mode not in _LYRICS_MODES:
            return (
                f"lyricsGeneration content.mode={mode!r} 无效，"
                "仅允许 write_full_song 或 edit。"
            )
        if mode == "edit" and not str(content.get("lyrics") or "").strip():
            return "lyricsGeneration mode=edit 时须提供 content.lyrics。"
    elif func_name == _MUSIC_FUNC:
        content = params.get("content")
        if not isinstance(content, dict):
            return "musicGeneration 须提供 arguments.content 对象（必填 prompt，见 music-generation）。"
        if not str(content.get("prompt") or "").strip():
            return "musicGeneration 须提供 content.prompt。"
        instrumental = _as_bool(content.get("is_instrumental", False))
        optimizer = _as_bool(content.get("lyrics_optimizer", False))
        has_lyrics = bool(str(content.get("lyrics") or "").strip())
        if instrumental:
            if has_lyrics:
                return "musicGeneration is_instrumental=true 时不能传 lyrics（纯器乐无人声）。"
            if optimizer:
                return "musicGeneration is_instrumental=true 时 lyrics_optimizer 须为 false。"
        elif not has_lyrics and not optimizer:
            return (
                "musicGeneration is_instrumental=false 时须提供 content.lyrics，"
                "或设置 lyrics_optimizer=true。"
            )

    return None


def invoke_tool_description() -> str:
    """ToolCard description for PluginSkillExecTool cloud capabilities."""
    return (
        "调用云端 PluginSkillExec 能力，"
        "或 functionName=agent_as_a_tool 调用远程 Agent。"
        "调用形态：顶层 functionName 固定为 PluginSkillExecTool，"
        "真实能力写在 arguments.functionName，并带对应 bundleName 与业务参数。"
        "禁止臆造 image-generation / text-to-image / ai-draw / generate 等名称。\n"
        f"{_CATALOG_HELP}\n"
        "示例生图：{\"functionName\":\"PluginSkillExecTool\",\"arguments\":{"
        "\"functionName\":\"seedreamLite4Skill\","
        f"\"bundleName\":\"{_ATOMIC_BUNDLE}\","
        "\"prompt\":\"一只柯基在滑板上\"}}。\n"
        "示例图像理解：{\"functionName\":\"PluginSkillExecTool\",\"arguments\":{"
        "\"functionName\":\"imageUnderStandStream\","
        f"\"bundleName\":\"{_XIAOYI_BUNDLE}\","
        "\"imageUrl\":\"https://...\",\"text\":\"描述图片\"}}。\n"
        "示例生视频：seedanceMiniTask（content）默认会轮询到 video_url；"
        "只要 task_id 时传 arguments.wait=false，再用 seedanceMiniTaskQuery（id）。\n"
        "示例生音乐：先可选 lyricsGeneration（content.prompt + mode），"
        "再 musicGeneration（content.prompt；人声带 lyrics 或 lyrics_optimizer，"
        "器乐 is_instrumental=true）。"
    )


__all__ = [
    "PLUGIN_SKILL_CATALOG",
    "extract_seedance_query_state",
    "extract_seedance_task_id",
    "invoke_tool_description",
    "normalize_plugin_skill_args",
    "parse_plugin_json_payload",
    "validate_plugin_skill_args",
    "want_seedance_wait",
]
