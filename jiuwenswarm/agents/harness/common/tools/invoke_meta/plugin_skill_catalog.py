# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Known PluginSkillExecTool cloud capabilities from skills/*.md."""

from __future__ import annotations

import json
from typing import Any

# bundleName for Seedream image + Seedance video (skills/seedance-image-gen.md, seedance-video-gen.md)
_ATOMIC_BUNDLE = "com.atomicservice.5765880207845681341"
# bundleName for image understanding (skills/xiaoyi-image-understanding.md)
_XIAOYI_BUNDLE = "xiaoyi"

# functionName (arguments.functionName) → required bundleName
PLUGIN_SKILL_CATALOG: dict[str, str] = {
    # 生图 seedream-image-gen / seedance-image-gen.md
    "seedreamLite4Skill": _ATOMIC_BUNDLE,
    "SeedreamPro4Skill": _ATOMIC_BUNDLE,
    # 生视频 seedance-video-gen.md
    "seedanceMiniTask": _ATOMIC_BUNDLE,
    "seedanceMiniTaskQuery": _ATOMIC_BUNDLE,
    # 图像理解 xiaoyi-image-understanding.md
    "imageUnderStandStream": _XIAOYI_BUNDLE,
}

_CATALOG_HELP = (
    "仅允许 skill 文档中的云端能力，禁止臆造 bundleName/functionName。\n"
    "生图：functionName=seedreamLite4Skill|SeedreamPro4Skill，"
    f"bundleName={_ATOMIC_BUNDLE}，必填 prompt；"
    "size 仅 1K|2K（1024x1024→1K，2048x2048→2K）；"
    "max_images 仅 Lite（1~15），Pro 勿传。\n"
    "生视频：functionName=seedanceMiniTask（提交，必填 content；默认提交后自动轮询 "
    "seedanceMiniTaskQuery 直到成片，arguments.wait=false 则只返回 task_id）或 "
    "seedanceMiniTaskQuery（查询，必填 id），"
    f"bundleName={_ATOMIC_BUNDLE}。\n"
    f"图像理解：functionName=imageUnderStandStream，bundleName={_XIAOYI_BUNDLE}，必填 imageUrl；可选 text。"
)

_SEEDREAM_FUNCS = ("seedreamLite4Skill", "SeedreamPro4Skill")
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
    """Map skill-doc / pixel aliases to 1K|2K. None = omitted or invalid."""
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


def normalize_plugin_skill_args(
    func_name: str, params: dict[str, Any]
) -> tuple[dict[str, Any], str | None]:
    """Coerce seedream size / drop Pro max_images. Returns (params, error)."""
    out = dict(params)
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


def extract_seedance_task_id(result: dict[str, Any]) -> str:
    """Read task_id from seedanceMiniTask invoke result."""
    payload = parse_plugin_json_payload(result.get("content"))
    if not payload and isinstance(result, dict):
        payload = {k: v for k, v in result.items() if k != "frames"}
    nested = payload.get("content") if isinstance(payload.get("content"), dict) else {}
    for candidate in (payload, nested):
        if not isinstance(candidate, dict):
            continue
        for key in ("task_id", "id", "taskId"):
            value = str(candidate.get(key) or "").strip()
            if value:
                return value
    return ""


def extract_seedance_query_state(result: dict[str, Any]) -> tuple[str, str]:
    """Return (status, video_url) from seedanceMiniTaskQuery result."""
    payload = parse_plugin_json_payload(result.get("content"))
    if not payload and isinstance(result, dict):
        payload = {k: v for k, v in result.items() if k != "frames"}
    nested = payload.get("content") if isinstance(payload.get("content"), dict) else {}
    status = str(payload.get("status") or nested.get("status") or "").strip().lower()
    video_url = str(
        nested.get("video_url")
        or nested.get("videoUrl")
        or payload.get("video_url")
        or payload.get("videoUrl")
        or ""
    ).strip()
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

    return None


def invoke_tool_description() -> str:
    """ToolCard description aligned with skills/*.md call contracts."""
    return (
        "经桌面 CloudWsRelay 复用 /ws/link 调用云端 PluginSkillExec 能力，"
        "或 functionName=agent_as_a_tool 调用远程 Agent。"
        "调用形态与 skill 文档一致：顶层 functionName 固定为 PluginSkillExecTool，"
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
        "只要 task_id 时传 arguments.wait=false，再用 seedanceMiniTaskQuery（id）。"
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
