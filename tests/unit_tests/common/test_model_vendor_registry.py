from jiuwenswarm.common.model_vendor_registry import (
    PlanKind,
    get_preset,
    to_frontend_payload,
)


def test_orcarouter_preset_registered_in_custom_api_plan():
    preset = get_preset("orcarouter", PlanKind.CUSTOM_API)
    assert preset is not None
    assert preset.client_provider == "OpenAI"
    assert preset.api_base == "https://api.orcarouter.ai/v1"
    assert preset.default_model == "orcarouter/auto"
    assert "orcarouter/auto" in preset.model_options
    assert preset.icon_key == "orcarouter"
    assert preset.models_endpoint == "https://api.orcarouter.ai/v1/models"
    # OpenAI 兼容网关,无专属方言
    assert preset.endpoint_profile is None
    # Anthropic 协议 base 不带 /v1(SDK 自行追加)
    assert preset.anthropic_base == "https://api.orcarouter.ai"


def test_orcarouter_preset_exposed_in_frontend_payload():
    payload = to_frontend_payload()
    custom_api = payload[PlanKind.CUSTOM_API.value]
    orca = next((p for p in custom_api if p["vendor_key"] == "orcarouter"), None)
    assert orca is not None
    assert orca["api_base"] == "https://api.orcarouter.ai/v1"
    assert orca["icon_key"] == "orcarouter"
    assert orca["supports_anthropic"] is True
    assert orca["anthropic_base"] == "https://api.orcarouter.ai"
    assert orca["models_needs_key"] is True
