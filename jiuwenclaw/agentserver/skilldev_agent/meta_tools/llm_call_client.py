# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Sandbox-mode LLM client for simulating invoke tool/agent responses."""

from __future__ import annotations

import json
import logging
from typing import Any

from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.core.foundation.llm.schema.message import SystemMessage, UserMessage

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
你是沙箱环境中的工具/Agent 响应模拟器。
根据给定的 funcName、调用参数 params 以及 outputSchema，生成符合 schema 的模拟响应。
只输出最终响应正文，不要解释、不要 markdown 代码块包裹。"""

_model_cache: Model | None = None


def _build_model() -> Model:
    from jiuwenclaw.config import get_config, get_default_models

    config = get_config()
    for entry in get_default_models(config):
        mcc = dict(entry.get("model_client_config") or {})
        if not mcc.get("model_name"):
            continue
        mcc["claw_config"] = config
        mco = entry.get("model_config_obj") or {}
        name = str(mcc.pop("model_name", "") or "gpt-4")
        request_config = ModelRequestConfig(
            model=name,
            temperature=mco.get("temperature", 0.95),
        )
        return Model(
            model_client_config=ModelClientConfig(**mcc),
            model_config=request_config,
        )

    default_model_config = config.get("models", {}).get("default", {})
    react_config = config.get("react", {})
    mcc = dict(default_model_config.get("model_client_config") or react_config.get("model_client_config") or {})
    model_name = mcc.get("model_name") or react_config.get("model_name") or "gpt-4"
    mcc.setdefault("model_name", model_name)
    mcc["claw_config"] = config
    mco = default_model_config.get("model_config_obj") or react_config.get("model_config_obj") or {}
    name = str(mcc.pop("model_name", "") or model_name)
    request_config = ModelRequestConfig(
        model=name,
        temperature=mco.get("temperature", 0.95),
    )
    return Model(
        model_client_config=ModelClientConfig(**mcc),
        model_config=request_config,
    )


def _get_model() -> Model:
    global _model_cache
    if _model_cache is None:
        _model_cache = _build_model()
    return _model_cache


def _extract_message_content(response: object) -> str:
    content = getattr(response, "content", None)
    if content is not None:
        return str(content)
    return str(response)


def _build_user_prompt(
    *,
    func_name: str,
    params: Any,
    output_schema: Any | None,
) -> str:
    parts = [
        f"funcName: {func_name}",
        f"params: {json.dumps(params, ensure_ascii=False)}",
    ]
    if output_schema is not None:
        parts.append(
            f"outputSchema: {json.dumps(output_schema, ensure_ascii=False)}"
        )
    else:
        parts.append("outputSchema: （未提供，请返回合理的 JSON 对象）")
    parts.append("请生成符合 outputSchema 的模拟响应。")
    return "\n".join(parts)


async def call_llm(
    *,
    func_name: str,
    params: Any,
    output_schema: Any | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Call LLM to simulate invoke output in sandbox mode.

    Returns a dict with at least a ``content`` field.
    """
    user_prompt = _build_user_prompt(
        func_name=func_name,
        params=params,
        output_schema=output_schema,
    )
    logger.info(
        "[llm_call_client] call_llm funcName=%s sessionId=%s outputSchema=%s",
        func_name,
        session_id or "",
        "set" if output_schema is not None else "missing",
    )
    try:
        model = _get_model()
        response = await model.invoke(
            messages=[
                SystemMessage(content=_SYSTEM_PROMPT),
                UserMessage(content=user_prompt),
            ],
        )
        content = _extract_message_content(response).strip()
        return {"content": content}
    except Exception as exc:
        logger.warning("[llm_call_client] call_llm failed: %s", exc)
        return {
            "content": "",
            "success": False,
            "error": f"沙箱 LLM 模拟调用失败: {exc}",
        }


class LlmCallClient:
    """Thin wrapper around :func:`call_llm` for dependency injection in tests."""

    async def call_llm(
        self,
        *,
        func_name: str,
        params: Any,
        output_schema: Any | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        return await call_llm(
            func_name=func_name,
            params=params,
            output_schema=output_schema,
            session_id=session_id,
        )


__all__ = ["LlmCallClient", "call_llm"]
