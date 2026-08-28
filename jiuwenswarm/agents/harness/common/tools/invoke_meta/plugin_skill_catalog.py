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
    "生音乐：functionName=lyricsGeneration（写词/改词，必填 prompt；"
    "mode=write_full_song|edit，edit 须带 lyrics）或 "
    "musicGeneration（成曲，必填 prompt；人声须 lyrics 或 lyrics_optimizer=true，"
    "器乐 is_instrumental=true 时不能带 lyrics/optimizer），"
    f"bundleName={_ATOMIC_BUNDLE}，业务字段与 bundleName 平铺，不要包 content。\n"
    "流程：先判断人声还是纯器乐、一句话（基础）还是要改词（高级）。"
    "基础器乐只用 musicGeneration+is_instrumental=true；基础人声用 lyrics_optimizer=true。"
    "高级人声先 lyricsGeneration（改词 mode=edit+lyrics），确认歌词后再 musicGeneration 带 lyrics。"
    "成曲前向用户展示类型/语言/prompt/歌词并得到明确确认。"
    "中文输入用中文 prompt 与歌词，英文同理，其它语言先问用户。"
    "prompt 写成完整句子（情绪+流派+人声或乐器+叙事/场景），不要逗号关键词列表。\n"
    f"图像理解：functionName=imageUnderStandStream，bundleName={_XIAOYI_BUNDLE}，必填 imageUrl；可选 text。"
)

_SEEDREAM_FUNCS = ("seedreamLite4Skill", "SeedreamPro4Skill")
_LYRICS_FUNC = "lyricsGeneration"
_MUSIC_FUNC = "musicGeneration"
_LYRICS_MODES = ("write_full_song", "edit")
_MUSIC_FLAG_TRUE = {"1", "true", "yes", "on"}
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


def _has_top_level_value(val: Any) -> bool:
    if val is None:
        return False
    if isinstance(val, str) and not val.strip():
        return False
    return True


def _lift_legacy_content(out: dict[str, Any]) -> None:
    """Raise nested content fields to top-level; existing top-level values win."""
    content = out.pop("content", None)
    if not isinstance(content, dict):
        return
    for key, val in content.items():
        if not _has_top_level_value(out.get(key)):
            out[key] = val


def _normalize_lyrics_args(out: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    """Flatten lyricsGeneration fields; lift legacy content; default mode=write_full_song."""
    _lift_legacy_content(out)
    prompt = out.get("prompt")
    if prompt is not None and str(prompt).strip():
        out["prompt"] = str(prompt).strip()
    mode = out.get("mode")
    if mode is not None and str(mode).strip():
        out["mode"] = str(mode).strip()
    else:
        out["mode"] = "write_full_song"
    lyrics = out.get("lyrics")
    if lyrics is not None and str(lyrics).strip():
        out["lyrics"] = str(lyrics).strip()
    else:
        out.pop("lyrics", None)
    return out, None


def _normalize_music_args(out: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    """Flatten MiniMax music fields; lift legacy content; fill audio_setting / watermark."""
    _lift_legacy_content(out)
    prompt = out.get("prompt")
    if prompt is not None and str(prompt).strip():
        out["prompt"] = str(prompt).strip()
    lyrics = out.get("lyrics")
    if lyrics is not None and str(lyrics).strip():
        out["lyrics"] = str(lyrics).strip()
    else:
        out.pop("lyrics", None)
    if "lyrics-optimizer" in out:
        alias = out.pop("lyrics-optimizer")
        if not _has_top_level_value(out.get("lyrics_optimizer")):
            out["lyrics_optimizer"] = alias
    out["lyrics_optimizer"] = _as_bool(out.get("lyrics_optimizer", False))
    if "instrumental" in out:
        alias = out.pop("instrumental")
        if not _has_top_level_value(out.get("is_instrumental")):
            out["is_instrumental"] = alias
    out["is_instrumental"] = _as_bool(out.get("is_instrumental", False))
    audio_over = out.get("audio_setting")
    audio_base = audio_over if isinstance(audio_over, dict) else {}
    out["audio_setting"] = {**_DEFAULT_AUDIO_SETTING, **audio_base}
    if "aigc_watermark" not in out or out["aigc_watermark"] is None:
        out["aigc_watermark"] = True
    else:
        out["aigc_watermark"] = _as_bool(out.get("aigc_watermark"))
    return out, None


def normalize_plugin_skill_args(
    func_name: str, params: dict[str, Any]
) -> tuple[dict[str, Any], str | None]:
    """Coerce seedream size / drop Pro max_images / wrap seedance text content / flatten music fields."""
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
            return f"{func_name} 须提供 arguments.prompt。"
    elif func_name == "imageUnderStandStream":
        if not str(params.get("imageUrl") or "").strip():
            return "imageUnderStandStream 须提供 arguments.imageUrl（公网 http/https URL）。"
    elif func_name == "seedanceMiniTask":
        content = params.get("content")
        if not isinstance(content, list) or not content:
            return (
                "seedanceMiniTask 须提供 arguments.content 数组"
                "（首项 type=text 的提示词）。"
            )
    elif func_name == "seedanceMiniTaskQuery":
        if not str(params.get("id") or "").strip():
            return "seedanceMiniTaskQuery 须提供 arguments.id（seedanceMiniTask 返回的 task_id）。"
    elif func_name == _LYRICS_FUNC:
        if not str(params.get("prompt") or "").strip():
            return "lyricsGeneration 须提供 arguments.prompt。"
        mode = str(params.get("mode") or "write_full_song").strip()
        if mode not in _LYRICS_MODES:
            return (
                f"lyricsGeneration mode={mode!r} 无效，"
                "仅允许 write_full_song 或 edit。"
            )
        if mode == "edit" and not str(params.get("lyrics") or "").strip():
            return "lyricsGeneration mode=edit 时须提供 lyrics。"
    elif func_name == _MUSIC_FUNC:
        if not str(params.get("prompt") or "").strip():
            return "musicGeneration 须提供 arguments.prompt。"
        instrumental = _as_bool(params.get("is_instrumental", False))
        optimizer = _as_bool(params.get("lyrics_optimizer", False))
        has_lyrics = bool(str(params.get("lyrics") or "").strip())
        if instrumental:
            if has_lyrics:
                return "musicGeneration is_instrumental=true 时不能传 lyrics（纯器乐无人声）。"
            if optimizer:
                return "musicGeneration is_instrumental=true 时 lyrics_optimizer 须为 false。"
        elif not has_lyrics and not optimizer:
            return (
                "musicGeneration is_instrumental=false 时须提供 lyrics，"
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
        "示例生音乐：先可选 lyricsGeneration（prompt + mode），"
        "再 musicGeneration（prompt；人声带 lyrics 或 lyrics_optimizer，"
        "器乐 is_instrumental=true）。业务字段平铺，不要包 content。"
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
