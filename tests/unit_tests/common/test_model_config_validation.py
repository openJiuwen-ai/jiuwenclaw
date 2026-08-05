from jiuwenswarm.common.model_config_validation import (
    default_model_preflight_error,
    is_model_client_config_usable,
    is_placeholder_api_base,
)


def test_is_placeholder_api_base_detects_documentation_domains():
    assert is_placeholder_api_base("https://example.com/compatible-mode/v1")
    assert is_placeholder_api_base("https://api.example.com/v1")
    assert is_placeholder_api_base("https://example.org")
    assert is_placeholder_api_base("https://docs.example.net/v1")


def test_is_placeholder_api_base_allows_real_domains_and_empty_values():
    assert not is_placeholder_api_base("https://real.provider.test/v1")
    assert not is_placeholder_api_base("https://example.test/v1")
    assert not is_placeholder_api_base("")


def test_is_model_client_config_usable_rejects_missing_or_placeholder_base():
    assert not is_model_client_config_usable({})
    assert not is_model_client_config_usable({"api_base": "", "api_key": "sk-real"})
    assert not is_model_client_config_usable(
        {"api_base": "https://example.com/compatible-mode/v1", "api_key": "sk-real"}
    )


def test_is_model_client_config_usable_requires_api_key_for_key_providers():
    assert not is_model_client_config_usable(
        {"api_base": "https://real.provider.test/v1", "api_key": "", "client_provider": "OpenAI"}
    )
    assert is_model_client_config_usable(
        {"api_base": "https://real.provider.test/v1", "api_key": "sk-real", "client_provider": "OpenAI"}
    )


def test_is_model_client_config_usable_allows_openai_account_without_key():
    assert is_model_client_config_usable(
        {"api_base": "https://real.provider.test/v1", "api_key": "", "client_provider": "OpenAIAccount"}
    )


def _config_with_defaults(entries):
    return {"models": {"defaults": entries}}


def test_default_model_preflight_error_warns_when_no_model_usable():
    config = _config_with_defaults(
        [
            {"model_client_config": {"api_base": "", "api_key": ""}},
            {
                "model_client_config": {
                    "api_base": "https://example.com/compatible-mode/v1",
                    "api_key": "sk-xxxxxxxxx",
                }
            },
        ]
    )
    message = default_model_preflight_error(config)
    assert message is not None
    assert "API_BASE" in message


def test_default_model_preflight_error_none_when_one_model_usable():
    config = _config_with_defaults(
        [
            {"model_client_config": {"api_base": "", "api_key": ""}},
            {
                "model_client_config": {
                    "api_base": "https://real.provider.test/v1",
                    "api_key": "sk-real",
                    "client_provider": "OpenAI",
                }
            },
        ]
    )
    assert default_model_preflight_error(config) is None
