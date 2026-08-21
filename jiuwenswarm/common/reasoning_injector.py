# coding: utf-8
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from jiuwenswarm.common.reasoning_config import (
    ANTHROPIC_BUDGET_TOKENS,
    ANTHROPIC_MAX_TOKENS_HEADROOM,
    ReasoningLevel,
    normalize_reasoning_level,
    resolve_reasoning_target,
)


def _model_config_to_dict(model_config_obj: Any) -> dict[str, Any]:
    if model_config_obj is None:
        return {}
    if isinstance(model_config_obj, dict):
        return dict(model_config_obj)
    if hasattr(model_config_obj, "model_dump"):
        return model_config_obj.model_dump(exclude_none=True)
    if isinstance(model_config_obj, Mapping):
        return dict(model_config_obj)
    return {}


def _resolve_model_name(model_name: str, model_config_obj: Any) -> str:
    if model_name:
        return str(model_name).strip()
    if isinstance(model_config_obj, Mapping):
        configured_name = model_config_obj.get("model") or model_config_obj.get("model_name")
        return str(configured_name or "").strip()
    return ""


def _copy_extra_body(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {}


def _runtime_config_copy(model_config_dict: dict[str, Any]) -> dict[str, Any]:
    runtime_model_config = dict(model_config_dict)
    # Internal hint only; must not be sent as a raw OpenAI SDK parameter.
    runtime_model_config.pop("reasoning_level", None)
    return runtime_model_config


def inject_deepseek_official_payload(
    model_config_obj: dict[str, Any],
    level: ReasoningLevel,
) -> None:
    """DeepSeek takes an on/off switch, plus an effort hint only at ``high``.

    ``low`` and ``medium`` enable thinking and send no effort. They used to send
    ``reasoning_effort="high"``, which discarded the level the user picked.
    """
    model_config_obj.pop("reasoning_effort", None)

    extra_body = _copy_extra_body(model_config_obj.get("extra_body"))
    extra_body["thinking"] = {
        "type": "disabled" if level == "off" else "enabled",
    }
    model_config_obj["extra_body"] = extra_body

    if level == "high":
        model_config_obj["reasoning_effort"] = "high"


def inject_dashscope_bailian_payload(
    model_config_obj: dict[str, Any],
    level: ReasoningLevel,
) -> None:
    """Bailian spells the same switch as ``enable_thinking``."""
    model_config_obj.pop("reasoning_effort", None)

    extra_body = _copy_extra_body(model_config_obj.get("extra_body"))
    extra_body["enable_thinking"] = level != "off"
    model_config_obj["extra_body"] = extra_body

    if level == "high":
        model_config_obj["reasoning_effort"] = "high"


def inject_openai_reasoning_payload(
    model_config_obj: dict[str, Any],
    level: ReasoningLevel,
) -> None:
    """OpenAI's enum is the product axis, so the level passes straight through.

    ``off`` omits the field rather than sending ``"none"``: omission is what
    every model accepts, and the caller has already established that this model
    is reasoning-capable.
    """
    model_config_obj.pop("reasoning_effort", None)
    if level == "off":
        return
    model_config_obj["reasoning_effort"] = level


def _coerce_positive_int(value: Any) -> int | None:
    """Parse a configured ceiling; reject bools and non-numeric junk."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = int(text)
        except ValueError:
            return None
        return parsed
    return None


def inject_anthropic_thinking_payload(
    model_config_obj: dict[str, Any],
    level: ReasoningLevel,
) -> None:
    """Anthropic wants an integer budget, and it must fit inside ``max_tokens``.

    The budget is thinking tokens taken *out of* ``max_tokens``, so a maximum
    that cannot hold the budget *and* leave answer headroom is raised to
    ``budget`` plus :data:`ANTHROPIC_MAX_TOKENS_HEADROOM`. This applies to an
    unset maximum too, which is the case that looks safe and is not: the client
    substitutes 8192, and against that default ``high`` (16000) is rejected
    outright while ``medium`` (8000) is *accepted* and leaves 192 tokens for the
    actual answer. A ceiling of ``budget + 1`` is the same trap. A 400 is
    recoverable; a near-empty reply that reads like the model had nothing to
    say is not.

    Extended thinking also rejects non-default sampling: ``temperature`` may
    only be ``1`` (or omitted), and ``top_p`` / ``top_k`` are incompatible.
    Stale OpenAI ``reasoning_effort`` is cleared for the same reason the other
    injectors clear it -- an OpenAI-only knob on a Messages request is a hard
    error once agent-core forwards extras.

    ``off`` omits the block instead of sending ``{"type": "disabled"}``, because
    a model without extended thinking rejects the field either way.
    """
    model_config_obj.pop("thinking", None)
    model_config_obj.pop("reasoning_effort", None)
    if level == "off":
        return

    budget = ANTHROPIC_BUDGET_TOKENS[level]
    configured = _coerce_positive_int(model_config_obj.get("max_tokens"))
    if configured is None or configured - budget < ANTHROPIC_MAX_TOKENS_HEADROOM:
        model_config_obj["max_tokens"] = budget + ANTHROPIC_MAX_TOKENS_HEADROOM
    else:
        # Normalise string/float ceilings so the request carries an int.
        model_config_obj["max_tokens"] = configured

    # Thinking is incompatible with custom sampling. Omit rather than force 1:
    # omission is what every Messages model accepts.
    model_config_obj.pop("temperature", None)
    model_config_obj.pop("top_p", None)
    model_config_obj.pop("top_k", None)

    model_config_obj["thinking"] = {"type": "enabled", "budget_tokens": budget}


def inject_reasoning_params(
    *,
    model_client_config: dict[str, Any],
    model_config_obj: Any,
) -> dict[str, Any]:
    model_config_dict = _model_config_to_dict(model_config_obj)
    level = normalize_reasoning_level(model_config_dict.get("reasoning_level"))
    runtime_model_config = _runtime_config_copy(model_config_dict)
    if level is None:
        return runtime_model_config

    target = resolve_reasoning_target(
        client_provider=model_client_config.get("client_provider"),
        api_base=(
            model_client_config.get("api_base")
            or model_client_config.get("base_url")
        ),
        model_name=model_client_config.get("model_name"),
    )
    if target is None:
        return runtime_model_config

    provider_kind, _model = target

    if provider_kind == "deepseek_official":
        inject_deepseek_official_payload(runtime_model_config, level)
    elif provider_kind == "dashscope_bailian":
        inject_dashscope_bailian_payload(runtime_model_config, level)
    elif provider_kind == "openai_reasoning":
        inject_openai_reasoning_payload(runtime_model_config, level)
    elif provider_kind == "anthropic":
        inject_anthropic_thinking_payload(runtime_model_config, level)

    return runtime_model_config


def _build_model_request_kwargs(
    *,
    model_name: str,
    model_config_obj: Any,
) -> dict[str, Any]:
    request_kwargs = _model_config_to_dict(model_config_obj)
    request_kwargs.pop("model", None)
    request_kwargs.pop("model_name", None)
    request_kwargs.pop("reasoning_level", None)
    # _source 是 jiuwenswarm 内部标记（如 agentos 备份模型），不得进入 core 的
    # ModelRequestConfig；core 侧 extra="allow" 会静默收下它，但下游 SDK 调
    # AsyncCompletions.create(**params) 时不认该 kwarg 会抛 "unexpected keyword
    # argument"。统一在此清理，覆盖 build_model_from_entry / config.validate_model
    # / image_modality_warmup 等所有走本函数的路径。
    is_agentos = request_kwargs.get("_source") == "agentos"
    request_kwargs.pop("_source", None)
    # agentos 的 max_tokens 是"输入侧上下文窗口"别名（-> ContextEngineConfig，
    # 不发厂商），绝不能进 core 的 ModelRequestConfig.max_tokens（那是"输出
    # token 上限"语义、会发厂商）。此处是所有路径的公共出口，统一在此 pop，
    # 保证 web validate 等绕过 build_model_from_entry 的路径也不会把它误当成
    # 输出上限。build_model_from_entry 会从原始 mco 取该值，挂到 Model 的普通属性
    # _agentos_ctx_window（不进 ModelRequestConfig 的 extra，故不经 model_dump
    # 流到 SDK），供 _deep_agent_context_engine_config 路径 A 读取；不依赖此处的 pop。
    if is_agentos:
        request_kwargs.pop("max_tokens", None)
    request_kwargs["model"] = _resolve_model_name(model_name, model_config_obj)
    return request_kwargs


def build_reasoning_model_request_kwargs(
    *,
    model_client_config: dict[str, Any],
    model_config_obj: Any,
    model_name: str,
) -> dict[str, Any]:
    effective_model_name = _resolve_model_name(model_name, model_config_obj)
    reasoning_client_config = dict(model_client_config or {})
    if effective_model_name:
        reasoning_client_config["model_name"] = effective_model_name
    runtime_model_config = inject_reasoning_params(
        model_client_config=reasoning_client_config,
        model_config_obj=model_config_obj,
    )
    return _build_model_request_kwargs(
        model_name=effective_model_name,
        model_config_obj=runtime_model_config,
    )


__all__ = [
    "build_reasoning_model_request_kwargs",
    "inject_anthropic_thinking_payload",
    "inject_dashscope_bailian_payload",
    "inject_deepseek_official_payload",
    "inject_openai_reasoning_payload",
    "inject_reasoning_params",
]
