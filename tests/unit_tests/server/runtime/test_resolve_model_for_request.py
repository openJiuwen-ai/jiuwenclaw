# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""按次指定模型（请求级完整覆盖）单测。

验证 _resolve_model_for_request 的请求级覆盖分支：params 带 api_base+api_key+model
时现场构造临时 Model，仅本次生效；不带四件套时行为不变（走缓存/默认）。
用 fake self 隔离，不依赖完整 adapter 初始化。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest


def _make_fake_adapter(default_model: Any = "DEFAULT",
                       cache: dict | None = None,
                       name_to_keys: dict | None = None) -> SimpleNamespace:
    """构造一个最小 fake adapter，仅含 _resolve_model_for_request 依赖的属性。"""
    from jiuwenswarm.server.runtime.agent_adapter.interface_deep import (
        JiuWenSwarmDeepAdapter,
    )
    return SimpleNamespace(
        _build_model_from_entry=JiuWenSwarmDeepAdapter._build_model_from_entry,
        _model=default_model,
        _model_cache=cache or {},
        _model_name_to_keys=name_to_keys or {},
    )


def _request(params: dict) -> Any:
    from jiuwenswarm.common.schema.agent import AgentRequest
    return AgentRequest(
        request_id="t-req", channel_id="t", session_id="t-sess",
        params=params, metadata={},
    )


def _resolve(adapter: SimpleNamespace, params: dict) -> Any:
    from jiuwenswarm.server.runtime.agent_adapter.interface_deep import (
        JiuWenSwarmDeepAdapter,
    )
    return JiuWenSwarmDeepAdapter._resolve_model_for_request(adapter, _request(params))


def test_quartet_builds_per_call_model():
    """apibase+key+model 齐全 → 构造临时 Model，字段回填正确。"""
    fake = _make_fake_adapter(default_model="DEFAULT")
    model = _resolve(fake, {
        "api_base": "https://infer.example.com/v1",
        "api_key": "sk-test",
        "model": "glm-5-rl",
        "modelprovider": "InferenceAffinity",
    })
    mcc = model.model_client_config
    assert mcc.api_base == "https://infer.example.com/v1"
    assert mcc.api_key == "sk-test"
    assert mcc.client_provider == "InferenceAffinity"
    # model_name 在 ModelRequestConfig 上（ModelClientConfig 不持有）
    assert model.model_config.model_name == "glm-5-rl"


def test_quartet_accepts_user_facing_field_names():
    """apibase/key/model/modelprovider 等用户向字段名同样生效。"""
    fake = _make_fake_adapter(default_model="DEFAULT")
    model = _resolve(fake, {
        "apibase": "https://infer.example.com/v1",
        "key": "sk-test",
        "model": "qwen3-rl",
        "modelprovider": "openai",
    })
    assert model.model_client_config.api_base == "https://infer.example.com/v1"
    assert model.model_config.model_name == "qwen3-rl"
    # ModelClientConfig 会把 provider 规范化（如 openai→OpenAI），忽略大小写比较
    assert model.model_client_config.client_provider.lower() == "openai"


def test_quartet_missing_key_falls_back_to_default():
    """缺 api_key → 不触发覆盖，回退默认模型。"""
    fake = _make_fake_adapter(default_model="DEFAULT")
    model = _resolve(fake, {
        "api_base": "https://infer.example.com/v1",
        "model": "glm-5-rl",  # 缺 key
    })
    assert model == "DEFAULT"


def test_no_quartet_uses_cached_model_name():
    """不带四件套、只带 model_name → 走原有缓存命中逻辑。"""
    cached_model = SimpleNamespace(name="cached")
    fake = _make_fake_adapter(
        default_model="DEFAULT",
        cache={"glm-5": cached_model},
        name_to_keys={"glm-5": ["glm-5#0"]},
    )
    model = _resolve(fake, {"model_name": "glm-5"})
    assert model is cached_model


def test_no_quartet_unknown_name_falls_back_to_default():
    """不带四件套、model_name 不在缓存 → 回退默认。"""
    fake = _make_fake_adapter(default_model="DEFAULT")
    model = _resolve(fake, {"model_name": "nonexistent"})
    assert model == "DEFAULT"


def test_no_model_field_returns_default():
    """什么模型字段都不带 → 默认模型。"""
    fake = _make_fake_adapter(default_model="DEFAULT")
    model = _resolve(fake, {"query": "hi"})
    assert model == "DEFAULT"
