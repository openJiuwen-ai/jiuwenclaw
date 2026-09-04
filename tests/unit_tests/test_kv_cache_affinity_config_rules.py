# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from types import SimpleNamespace

from jiuwenswarm.common import config as config_module
from jiuwenswarm.common.kv_cache_affinity_config import (
    build_kv_cache_affinity_config,
    model_provider,
    normalize_affinity_request,
    validate_affinity_invariant,
)


def _config(
    provider: str,
    *,
    affinity: bool = True,
    release: bool = False,
) -> dict:
    return {
        "models": {
            "defaults": [
                {
                    "is_default": True,
                    "model_client_config": {"client_provider": provider},
                }
            ]
        },
        "react": {
            "kv_cache_affinity_config": {
                "enable_kv_cache_affinity": affinity,
                "enable_kv_cache_release": release,
            }
        },
        "channels": {},
    }


def test_runtime_config_fails_closed_for_non_ascend_provider() -> None:
    config = _config("OpenAI")

    config_module._normalize_config(config)

    assert (
        config["react"]["kv_cache_affinity_config"]["enable_kv_cache_affinity"]
        is False
    )


def test_affinity_invariant_reports_all_failures() -> None:
    valid, failures = validate_affinity_invariant(
        _config("OpenAI", release=True)
    )

    assert valid is False
    assert failures == [
        "enable_kv_cache_release must be false",
        "default provider must be AscendAffinity, got OpenAI",
    ]


def test_affinity_request_selects_ascend_provider() -> None:
    params = {"kv_cache_affinity_enabled": "true"}

    normalize_affinity_request(params)

    assert params["model_provider"] == "AscendAffinity"


def test_explicit_other_provider_disables_affinity() -> None:
    params = {
        "kv_cache_affinity_enabled": "true",
        "model_provider": "OpenAI",
    }

    normalize_affinity_request(params)

    assert params["kv_cache_affinity_enabled"] == "false"


def test_runtime_policy_preserves_ascend_affinity() -> None:
    model = SimpleNamespace(
        model_client_config=SimpleNamespace(client_provider="AscendAffinity")
    )

    result = build_kv_cache_affinity_config(
        _config("AscendAffinity")["react"],
        provider=model_provider(model),
    )

    assert result.enable_kv_cache_affinity is True
    assert result.enable_kv_cache_release is False


def test_model_provider_prefers_original_provider_after_transport_normalization() -> None:
    model = SimpleNamespace(
        model_client_config=SimpleNamespace(
            client_provider="OpenAI",
            legacy_client_provider="DeepSeek",
        )
    )

    assert model_provider(model) == "DeepSeek"


def test_default_model_provider_prefers_original_provider_after_normalization() -> None:
    config = _config("OpenAI")
    config["models"]["defaults"][0]["model_client_config"][
        "legacy_client_provider"
    ] = "DeepSeek"

    assert config_module.get_default_model_provider(config) == "DeepSeek"


def test_runtime_policy_fails_closed_for_other_provider() -> None:
    result = build_kv_cache_affinity_config(
        _config("OpenAI", release=True)["react"],
        provider="OpenAI",
    )

    assert result.enable_kv_cache_affinity is False
    assert result.enable_kv_cache_release is True
