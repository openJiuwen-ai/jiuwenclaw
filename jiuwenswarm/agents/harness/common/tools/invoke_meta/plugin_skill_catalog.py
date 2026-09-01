# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Known PluginSkillExecTool cloud capabilities (functionName to bundleName)."""

from __future__ import annotations

import json
import os
import re
from typing import Any
from urllib.parse import urlparse

# 蓝区/绿区：原子服务包
_ATOMIC_BUNDLE = "com.atomicservice.5765880207845681341"
# 现网：视频/音乐/生图 Pro
_PLUGIN_PLATFORM = "com.huawei.pluginPlatform"
# 现网：生图 Lite
_LITE_BUNDLE = "com.example.aikitdemo"
# bundleName for image understanding（各区相同）
_XIAOYI_BUNDLE = "xiaoyi"

_TEST_CATALOG: dict[str, str] = {
    "seedreamLite4Skill": _ATOMIC_BUNDLE,
    "SeedreamPro4Skill": _ATOMIC_BUNDLE,
    "seedanceMiniTask": _ATOMIC_BUNDLE,
    "seedanceMiniTaskQuery": _ATOMIC_BUNDLE,
    "lyricsGeneration": _ATOMIC_BUNDLE,
    "musicGeneration": _ATOMIC_BUNDLE,
    "imageUnderStandStream": _XIAOYI_BUNDLE,
}

_PROD_CATALOG: dict[str, str] = {
    "seedreamBatch5": _LITE_BUNDLE,
    "SeedreamPro_5": _PLUGIN_PLATFORM,
    "seedanceMiniTask": _PLUGIN_PLATFORM,
    "seedanceMiniTaskQuery": _PLUGIN_PLATFORM,
    "lyricsGeneration": _PLUGIN_PLATFORM,
    "musicGeneration": _PLUGIN_PLATFORM,
    "imageUnderStandStream": _XIAOYI_BUNDLE,
}

# 两套 functionName 并集，仅用于「是否为云端 skill」判断；bundle 必须走 active_plugin_skill_catalog()
PLUGIN_SKILL_CATALOG: dict[str, str] = {**_TEST_CATALOG, **_PROD_CATALOG}

_PROD_MCP_HOST_MARKERS = ("hag-drcn", "dbankcloud.com", "huawei.com")

_SEEDREAM_LITE_FUNCS = frozenset({"seedreamLite4Skill", "seedreamBatch5"})
_SEEDREAM_PRO_FUNCS = frozenset({"SeedreamPro4Skill", "SeedreamPro_5"})
_SEEDREAM_FUNCS = _SEEDREAM_LITE_FUNCS | _SEEDREAM_PRO_FUNCS
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
# 现网 Lite（seedreamBatch5）透传上游 Seedream 5：WIDTHxHEIGHT / 2k / 3k / 4k
_SEEDREAM_BATCH5_FUNC = "seedreamBatch5"
_SEEDREAM_WXH_RE = re.compile(r"^\d+x\d+$")
_SEEDREAM_BATCH5_SIZE_MAP = {
    "1k": "1024x1024",
    "1024": "1024x1024",
    "1024x1024": "1024x1024",
    "1024*1024": "1024x1024",
    "2k": "2k",
    "2048": "2k",
    "2048x2048": "2k",
    "2048*2048": "2k",
    "3k": "3k",
    "4k": "4k",
}
_SEEDREAM_SIZE_HINT = "仅允许 1K 或 2K（像素写法 1024x1024→1K、2048x2048→2K）。"
_SEEDREAM_BATCH5_SIZE_HINT = (
    "仅允许 WIDTHxHEIGHT、2k、3k 或 4k（1K→1024x1024，2K→2k，4K→4k）。"
)


def is_prod_plugin_runtime(url: str | None = None) -> bool:
    """True when mcp/run points at 现网 (hag-drcn / dbankcloud / huawei.com)."""
    raw = (url if url is not None else os.environ.get("AGENT_RUNTIME_MCP_RUN") or "").strip()
    if not raw:
        return False
    host = ""
    try:
        host = (urlparse(raw).hostname or "").lower()
    except ValueError:
        host = ""
    haystack = f"{host} {raw.lower()}"
    return any(marker in haystack for marker in _PROD_MCP_HOST_MARKERS)


def active_plugin_skill_catalog() -> dict[str, str]:
    """Current-zone whitelist: 现网用 _PROD_CATALOG，蓝/绿（及未配置）用 _TEST_CATALOG."""
    return dict(_PROD_CATALOG if is_prod_plugin_runtime() else _TEST_CATALOG)


def is_plugin_skill_function(func_name: str) -> bool:
    return func_name in _TEST_CATALOG or func_name in _PROD_CATALOG


def _name_in_catalog(catalog: dict[str, str], names: frozenset[str]) -> str:
    for name in names:
        if name in catalog:
            return name
    return next(iter(names))


def seedream_lite_function_name(catalog: dict[str, str] | None = None) -> str:
    return _name_in_catalog(catalog or active_plugin_skill_catalog(), _SEEDREAM_LITE_FUNCS)


def seedream_pro_function_name(catalog: dict[str, str] | None = None) -> str:
    return _name_in_catalog(catalog or active_plugin_skill_catalog(), _SEEDREAM_PRO_FUNCS)


def plugin_skill_bundle(func_name: str, catalog: dict[str, str] | None = None) -> str:
    active = catalog or active_plugin_skill_catalog()
    return str(active.get(func_name) or "")


def catalog_help(catalog: dict[str, str] | None = None) -> str:
    """LLM-facing whitelist text for the current (or given) zone."""
    active = catalog or active_plugin_skill_catalog()
    lite = seedream_lite_function_name(active)
    pro = seedream_pro_function_name(active)
    lite_bundle = active[lite]
    pro_bundle = active[pro]
    video_bundle = active["seedanceMiniTask"]
    music_bundle = active["musicGeneration"]
    if lite_bundle == pro_bundle:
        image_line = (
            f"生图：functionName={lite}|{pro}，bundleName={lite_bundle}，必填 prompt；"
        )
    else:
        image_line = (
            f"生图 Lite：functionName={lite}，bundleName={lite_bundle}；"
            f"生图 Pro：functionName={pro}，bundleName={pro_bundle}；必填 prompt；"
        )
    if lite == _SEEDREAM_BATCH5_FUNC:
        size_line = (
            "Lite size 仅 WIDTHxHEIGHT|2k|3k|4k（1K→1024x1024，2K→2k）；"
            "Pro size 仅 1K|2K（1024x1024→1K，2048x2048→2K）；"
        )
    else:
        size_line = "size 仅 1K|2K（1024x1024→1K，2048x2048→2K）；"
    return (
        "仅允许白名单中的云端能力，禁止臆造 bundleName/functionName。\n"
        f"{image_line}"
        f"{size_line}"
        "max_images 仅 Lite（1~15），Pro 勿传。\n"
        "生视频：functionName=seedanceMiniTask（提交，必填 content；默认提交后自动轮询 "
        "seedanceMiniTaskQuery 直到成片，arguments.wait=false 则只返回 task_id）或 "
        "seedanceMiniTaskQuery（查询，必填 id），"
        f"bundleName={video_bundle}。\n"
        "生音乐：functionName=lyricsGeneration（写词/改词，必填 prompt；"
        "mode=write_full_song|edit，edit 须带 lyrics）或 "
        "musicGeneration（成曲，必填 prompt；人声须 lyrics 或 lyrics_optimizer=true，"
        "器乐 is_instrumental=true 时不能带 lyrics/optimizer），"
        f"bundleName={music_bundle}，业务字段与 bundleName 平铺，不要包 content。\n"
        "流程：先判断人声还是纯器乐、一句话（基础）还是要改词（高级）。"
        "基础器乐只用 musicGeneration+is_instrumental=true；基础人声用 lyrics_optimizer=true。"
        "高级人声先 lyricsGeneration（改词 mode=edit+lyrics），确认歌词后再 musicGeneration 带 lyrics。"
        "成曲前向用户展示类型/语言/prompt/歌词并得到明确确认。"
        "中文输入用中文 prompt 与歌词，英文同理，其它语言先问用户。"
        "prompt 写成完整句子（情绪+流派+人声或乐器+叙事/场景），不要逗号关键词列表。\n"
        f"图像理解：functionName=imageUnderStandStream，bundleName={_XIAOYI_BUNDLE}，必填 imageUrl；可选 text。"
    )


def _seedream_size_key(raw: Any) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return str(int(raw))
    key = str(raw).strip().lower().replace(" ", "").replace("×", "x")
    return key or None


def _canonical_seedream_size(raw: Any, *, batch5: bool = False) -> str | None:
    """Map aliases. Blue-zone: 1K|2K. seedreamBatch5: WIDTHxHEIGHT|2k|3k|4k. None = invalid."""
    key = _seedream_size_key(raw)
    if key is None:
        return None
    if batch5:
        mapped = _SEEDREAM_BATCH5_SIZE_MAP.get(key)
        if mapped:
            return mapped
        if _SEEDREAM_WXH_RE.fullmatch(key):
            return key
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
        batch5 = func_name == _SEEDREAM_BATCH5_FUNC
        mapped = _canonical_seedream_size(out["size"], batch5=batch5)
        if mapped is None:
            hint = _SEEDREAM_BATCH5_SIZE_HINT if batch5 else _SEEDREAM_SIZE_HINT
            return out, f"arguments.size={out['size']!r} 无效，{hint}"
        out["size"] = mapped

    if func_name in _SEEDREAM_PRO_FUNCS:
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
    catalog = active_plugin_skill_catalog()
    help_text = catalog_help(catalog)
    expected_bundle = catalog.get(func_name)
    if expected_bundle is None:
        allowed = ", ".join(sorted(catalog))
        return (
            f"不支持的 arguments.functionName={func_name!r}。"
            f"允许值：{allowed}。\n{help_text}"
        )

    bundle = str(params.get("bundleName") or "").strip()
    if not bundle:
        return (
            f"缺少 arguments.bundleName；{func_name} 须为 {expected_bundle!r}。\n{help_text}"
        )
    if bundle != expected_bundle:
        return (
            f"arguments.bundleName={bundle!r} 与 {func_name} 不匹配，"
            f"须为 {expected_bundle!r}（勿使用 image-generation/text-to-image/ai-draw 等臆造名）。\n"
            f"{help_text}"
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


def invoke_arguments_description() -> str:
    """ToolCard arguments.description for the current zone."""
    catalog = active_plugin_skill_catalog()
    lite = seedream_lite_function_name(catalog)
    pro = seedream_pro_function_name(catalog)
    lite_bundle = catalog[lite]
    pro_bundle = catalog[pro]
    video_bundle = catalog["seedanceMiniTask"]
    if lite_bundle == pro_bundle:
        image_line = (
            f"生图：bundleName={lite_bundle}，functionName={lite}|{pro}，prompt=...；"
        )
    else:
        image_line = (
            f"生图 Lite：bundleName={lite_bundle}，functionName={lite}；"
            f"生图 Pro：bundleName={pro_bundle}，functionName={pro}；prompt=...；"
        )
    return (
        "必含 bundleName + functionName（真实能力名）+ 业务字段。"
        f"{image_line}"
        "图像理解：bundleName=xiaoyi，functionName=imageUnderStandStream，imageUrl=...；"
        f"生视频：bundleName={video_bundle}，seedanceMiniTask 用 content"
        "（默认自动轮询到 video_url；wait=false 则只返回 task_id），"
        "seedanceMiniTaskQuery 用 id；"
        f"生音乐：bundleName={catalog['musicGeneration']}，业务字段与 bundleName 平铺，不要包 content。"
        "基础器乐只用 musicGeneration+is_instrumental=true；"
        "基础人声 lyrics_optimizer=true；"
        "高级人声先 lyricsGeneration（write_full_song，改词 edit+lyrics），"
        "确认歌词后再 musicGeneration 带 lyrics。"
        "成曲前向用户展示类型/语言/prompt/歌词并得到明确确认。"
        "中文输入用中文 prompt 与歌词，英文同理，其它语言先问用户。"
        "prompt 写成完整句子（情绪+流派+人声或乐器+叙事/场景），"
        "不要逗号关键词列表。勿臆造其它 bundleName。"
    )


def invoke_function_name_description() -> str:
    """ToolCard functionName.description for the current zone."""
    catalog = active_plugin_skill_catalog()
    lite = seedream_lite_function_name(catalog)
    pro = seedream_pro_function_name(catalog)
    return (
        "云端 skill：固定 PluginSkillExecTool；"
        "远程 Agent：agent_as_a_tool。"
        "arguments.functionName 才是具体能力"
        f"（{lite} / {pro} / "
        "imageUnderStandStream / seedanceMiniTask / seedanceMiniTaskQuery / "
        "lyricsGeneration / musicGeneration）。"
    )


def invoke_tool_description() -> str:
    """ToolCard description for PluginSkillExecTool cloud capabilities."""
    catalog = active_plugin_skill_catalog()
    lite = seedream_lite_function_name(catalog)
    lite_bundle = catalog[lite]
    return (
        "调用云端 PluginSkillExec 能力，"
        "或 functionName=agent_as_a_tool 调用远程 Agent。"
        "调用形态：顶层 functionName 固定为 PluginSkillExecTool，"
        "真实能力写在 arguments.functionName，并带对应 bundleName 与业务参数。"
        "禁止臆造 image-generation / text-to-image / ai-draw / generate 等名称。\n"
        f"{catalog_help(catalog)}\n"
        "示例生图：{\"functionName\":\"PluginSkillExecTool\",\"arguments\":{"
        f"\"functionName\":\"{lite}\","
        f"\"bundleName\":\"{lite_bundle}\","
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
    "active_plugin_skill_catalog",
    "catalog_help",
    "extract_seedance_query_state",
    "extract_seedance_task_id",
    "invoke_arguments_description",
    "invoke_function_name_description",
    "invoke_tool_description",
    "is_plugin_skill_function",
    "is_prod_plugin_runtime",
    "normalize_plugin_skill_args",
    "parse_plugin_json_payload",
    "plugin_skill_bundle",
    "seedream_lite_function_name",
    "seedream_pro_function_name",
    "validate_plugin_skill_args",
    "want_seedance_wait",
]
