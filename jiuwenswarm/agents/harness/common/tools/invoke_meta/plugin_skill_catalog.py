# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Known PluginSkillExecTool cloud capabilities from skills/*.md."""

from __future__ import annotations

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
    f"bundleName={_ATOMIC_BUNDLE}，必填 prompt；可选 reference_images/max_images/size 等。\n"
    "生视频：functionName=seedanceMiniTask（提交，必填 content）或 "
    "seedanceMiniTaskQuery（查询，必填 id），"
    f"bundleName={_ATOMIC_BUNDLE}；成片须先 task 再 query。\n"
    f"图像理解：functionName=imageUnderStandStream，bundleName={_XIAOYI_BUNDLE}，必填 imageUrl；可选 text。"
)


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

    if func_name in ("seedreamLite4Skill", "SeedreamPro4Skill"):
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
        "示例生视频：先 seedanceMiniTask（content），再 seedanceMiniTaskQuery（id）取 video_url。"
    )


__all__ = [
    "PLUGIN_SKILL_CATALOG",
    "invoke_tool_description",
    "validate_plugin_skill_args",
]
