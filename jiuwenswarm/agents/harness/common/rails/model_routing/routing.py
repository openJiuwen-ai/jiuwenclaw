"""model_routing.routing — _decide_and_select + _has_image."""
from __future__ import annotations
from typing import Optional
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from .capability import ModelCapability


def _has_image(ctx: AgentCallbackContext) -> bool:
    """请求是否含图片输入（多模态消息里有 image/image_url part，或 interface 层挂了 _multimodal_image_files）。"""
    inputs = getattr(ctx, "inputs", None)
    messages = getattr(inputs, "messages", None) or []
    for m in messages:
        content = getattr(m, "content", None)
        if content is None and isinstance(m, dict):
            content = m.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") in ("image", "image_url"):
                    return True
    extra = getattr(ctx, "extra", None)
    if isinstance(extra, dict):
        files = extra.get("_multimodal_image_files")
        if isinstance(files, list) and files:
            return True
    return False


def _model_score(cap: ModelCapability) -> float:
    """模型综合评分（0-100）；非数/缺失/NaN -> 0。"""
    try:
        v = float(cap.model_score)
    except (TypeError, ValueError):
        return 0.0
    return v if v == v else 0.0  # NaN -> 0


def _pick_closest_score(caps: list[ModelCapability], target: float) -> ModelCapability:
    """选 model_score 最接近 target 的模型；等距时偏高分（质量优先）。"""
    return min(caps, key=lambda c: (abs(_model_score(c) - target), -_model_score(c)))


def _decide_and_select(
    target_score: float,
    capability_table: list[ModelCapability],
    ctx: AgentCallbackContext,
    *,
    privacy_trusted_only: bool = False,
) -> tuple[Optional[ModelCapability], str]:
    """按目标分数匹配 model_score 最接近的模型。

    - target_score: 分类器返回的目标分数（0-100）；50 为兜底。
    - privacy_trusted_only：隐私命中时候选限到 is_trusted=True（无 trusted 则保持原表兜底）。
    - vision：含图请求 -> 候选限到 model_type=="vision"；非含图 -> 排除 vision 候选。
    - 在候选里取 |model_score - target| 最小者，等距偏高分。
    """
    if not capability_table:
        return None, "empty capability table"

    # 隐私约束：隐私命中时只选 trusted 模型（无 trusted 则保持原表兜底）
    if privacy_trusted_only:
        trusted_caps = [c for c in capability_table if c.is_trusted]
        if trusted_caps:
            capability_table = trusted_caps

    if _has_image(ctx):
        # 含图请求：候选限到 model_type=="vision"（无则保持原表兜底）
        vision_caps = [c for c in capability_table if c.model_type == "vision"]
        if vision_caps:
            capability_table = vision_caps
    else:
        # 非含图请求：vision 专用模型不参与；全为 vision 时保持原表兜底
        non_vision = [c for c in capability_table if c.model_type != "vision"]
        if non_vision:
            capability_table = non_vision

    pick = _pick_closest_score(capability_table, float(target_score))
    return pick, f"score match: target={target_score} -> {pick.model_name}(score={_model_score(pick):g})"
