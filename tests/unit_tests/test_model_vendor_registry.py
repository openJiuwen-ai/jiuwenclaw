# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from jiuwenswarm.common.model_vendor_registry import PlanKind, get_preset, to_frontend_payload


def test_frontend_payload_contains_protocol_scoped_reasoning_capabilities() -> None:
    payload = to_frontend_payload()
    alibaba = next(item for item in payload["token_plan"] if item["vendor_key"] == "alibaba")
    qwen = alibaba["reasoning_capabilities"]["qwen3.8-max"]

    assert qwen["openai"] == {
        "options": ["off", "low", "medium", "xhigh"],
        "recommended": "xhigh",
    }
    assert qwen["anthropic"] == {
        "options": ["off", "low", "medium", "xhigh"],
        "recommended": "xhigh",
    }


def test_frontend_payload_contains_provider_scoped_reasoning_rules() -> None:
    payload = to_frontend_payload()
    qianfan = next(item for item in payload["custom_api"] if item["vendor_key"] == "baidu")
    glm52 = next(rule for rule in qianfan["reasoning_rules"] if "glm-5.2*" in rule["patterns"])

    # Qianfan proxies GLM with a plain toggle; the cross-vendor fallback for
    # glm-5.2 exposes off/high/max instead. The preset rule must win so the
    # frontend matches the backend save validation.
    assert glm52["capabilities"]["openai"]["options"] == ["off", "on"]


def test_frontend_payload_contains_custom_model_fallbacks() -> None:
    reasoning = to_frontend_payload()["reasoning"]
    glm = next(item for item in reasoning["model_fallbacks"] if "glm-5.2*" in item["patterns"])

    assert reasoning["protocol_defaults"]["openai"]["options"] == ["off", "low", "medium", "high"]
    assert glm["capabilities"]["openai"]["options"] == ["off", "high", "max"]


def test_modelarts_presets_use_current_v2_model_ids() -> None:
    token_plan = get_preset("maas", PlanKind.TOKEN_PLAN)
    custom_api = get_preset("maas", PlanKind.CUSTOM_API)

    assert token_plan is not None
    assert token_plan.model_options == ("glm-5.1", "kimi-k2.6", "deepseek-v4-flash")
    assert custom_api is not None
    assert custom_api.default_model == "openpangu-2.0-pro"
    assert "pangu-large" not in custom_api.model_options
