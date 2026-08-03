from __future__ import annotations

import importlib.util
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from jiuwenclaw.local_env_config import (
    ENV_CONFIG_DICT,
    clear_staged_env,
    get_local_config,
    read_env_if_set,
    reset_local_env_state_for_tests,
)

_MM_PATH = (
    Path(__file__).resolve().parents[3]
    / "jiuwenclaw"
    / "agentserver"
    / "tools"
    / "multimodal_config.py"
)
_spec = importlib.util.spec_from_file_location("multimodal_config_under_test", _MM_PATH)
assert _spec and _spec.loader
_multimodal_config = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_multimodal_config)

MULTIMODAL_ENV_GROUP_KEYS = _multimodal_config.MULTIMODAL_ENV_GROUP_KEYS
apply_audio_model_config_from_yaml = _multimodal_config.apply_audio_model_config_from_yaml
apply_image_gen_model_config_from_yaml = (
    _multimodal_config.apply_image_gen_model_config_from_yaml
)
apply_video_model_config_from_yaml = _multimodal_config.apply_video_model_config_from_yaml
apply_vision_model_config_from_yaml = _multimodal_config.apply_vision_model_config_from_yaml
dedicated_multimodal_model_configured = (
    _multimodal_config.dedicated_multimodal_model_configured
)
sync_multimodal_env_omission_state = _multimodal_config.sync_multimodal_env_omission_state
reset_multimodal_env_omission_disabled = (
    _multimodal_config.reset_multimodal_env_omission_disabled
)

_APPLY_FN_BY_GROUP: dict[str, Callable[[dict[str, Any] | None], None]] = {
    "audio": apply_audio_model_config_from_yaml,
    "vision": apply_vision_model_config_from_yaml,
    "video": apply_video_model_config_from_yaml,
    "image_gen": apply_image_gen_model_config_from_yaml,
}

_MULTIMODAL_GROUP_PARAMS = [
    pytest.param(
        group,
        MULTIMODAL_ENV_GROUP_KEYS[group][0],
        MULTIMODAL_ENV_GROUP_KEYS[group][2],
        _APPLY_FN_BY_GROUP[group],
        id=group,
    )
    for group in ("audio", "vision", "video", "image_gen")
]

_ALL_MULTIMODAL_ENV_KEYS = tuple(
    key for keys in MULTIMODAL_ENV_GROUP_KEYS.values() for key in keys
)


@pytest.fixture(autouse=True)
def _clear_multimodal_env() -> None:
    import os

    from jiuwenclaw.local_env_config import make_env_ns_key

    reset_multimodal_env_omission_disabled()
    reset_local_env_state_for_tests()
    ENV_CONFIG_DICT.clear()
    clear_staged_env()
    for keys in MULTIMODAL_ENV_GROUP_KEYS.values():
        for key in keys:
            os.environ.pop(key, None)
            os.environ.pop(make_env_ns_key("default", "default", key), None)


def _model_config(group: str, **fields: str) -> dict[str, Any]:
    return {"models": {group: {"model_client_config": dict(fields)}}}


@pytest.mark.parametrize(
    ("group", "anchor_key", "model_name_key", "apply_fn"),
    _MULTIMODAL_GROUP_PARAMS,
)
def test_apply_literal_yaml_bootstraps_env_without_preexisting_anchor(
    group: str,
    anchor_key: str,
    model_name_key: str,
    apply_fn: Callable[[dict[str, Any] | None], None],
) -> None:
    config = _model_config(
        group,
        api_key="group-key",
        api_base=f"https://{group}.example/v1",
        model_name=f"{group}-model",
        model_provider="OpenAI",
    )
    apply_fn(config)
    assert get_local_config(anchor_key) == "group-key"
    assert get_local_config(model_name_key) == f"{group}-model"


@pytest.mark.parametrize(
    ("group", "anchor_key", "model_name_key", "apply_fn"),
    _MULTIMODAL_GROUP_PARAMS,
)
def test_apply_skips_when_env_omission_disabled(
    group: str,
    anchor_key: str,
    model_name_key: str,
    apply_fn: Callable[[dict[str, Any] | None], None],
    caplog: pytest.LogCaptureFixture,
) -> None:
    sync_multimodal_env_omission_state({anchor_key: None}, None)
    config = _model_config(
        group,
        api_key="group-key",
        model_name=f"{group}-model",
    )
    with caplog.at_level(logging.DEBUG, logger=_multimodal_config.logger.name):
        apply_fn(config)

    assert read_env_if_set(anchor_key) is None
    assert read_env_if_set(model_name_key) is None
    assert "disabled by env omission reconcile" in caplog.text


@pytest.mark.parametrize(
    ("group", "anchor_key", "model_name_key", "apply_fn"),
    _MULTIMODAL_GROUP_PARAMS,
)
def test_apply_literal_yaml_falls_back_to_main_api(
    group: str,
    anchor_key: str,
    model_name_key: str,
    apply_fn: Callable[[dict[str, Any] | None], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "jiuwenclaw.config.get_config_raw",
        lambda: {"models": {group: {"model_client_config": {}}}},
    )
    ENV_CONFIG_DICT["API_KEY"] = "main-key"
    ENV_CONFIG_DICT["API_BASE"] = "https://main.example/v1"
    ENV_CONFIG_DICT["MODEL_NAME"] = "main-model"
    ENV_CONFIG_DICT["MODEL_PROVIDER"] = "OpenAI"
    apply_fn(_model_config(group))
    assert get_local_config(anchor_key) == "main-key"
    assert get_local_config(model_name_key) == "main-model"


def test_apply_env_bound_skips_main_api_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ENV_CONFIG_DICT["API_KEY"] = "main-key"
    raw_config = _model_config("image_gen", api_key="${IMAGE_GEN_API_KEY}")
    resolved_config = _model_config("image_gen", api_key="")
    monkeypatch.setattr(
        "jiuwenclaw.config.get_config_raw",
        lambda: raw_config,
    )
    apply_image_gen_model_config_from_yaml(resolved_config)
    assert read_env_if_set("IMAGE_GEN_API_KEY") is None


@pytest.mark.parametrize("group", ["audio", "vision", "video", "image_gen"])
def test_dedicated_multimodal_model_configured_from_literal_yaml(group: str) -> None:
    with_key = _model_config(group, api_key="dedicated")
    without_key = _model_config(group, api_key="")

    assert dedicated_multimodal_model_configured(with_key, group) is True
    assert dedicated_multimodal_model_configured(without_key, group) is False
    assert dedicated_multimodal_model_configured(None, group) is False


@pytest.mark.parametrize("group", ["audio", "vision", "video", "image_gen"])
def test_dedicated_false_when_env_omission_disabled_despite_literal_yaml(
    group: str,
) -> None:
    anchor_key = MULTIMODAL_ENV_GROUP_KEYS[group][0]
    sync_multimodal_env_omission_state({anchor_key: None}, None)
    config = _model_config(group, api_key="dedicated")

    assert dedicated_multimodal_model_configured(config, group) is False


def test_dedicated_placeholder_resolves_from_env() -> None:
    config = {
        "models": {
            "image_gen": {
                "model_client_config": {"api_key": "${IMAGE_GEN_API_KEY}"},
            }
        }
    }
    assert dedicated_multimodal_model_configured(config, "image_gen") is False
    ENV_CONFIG_DICT["IMAGE_GEN_API_KEY"] = "resolved-key"
    assert dedicated_multimodal_model_configured(config, "image_gen") is True


def test_sync_omission_clears_disable_when_anchor_reappears() -> None:
    sync_multimodal_env_omission_state({"IMAGE_GEN_API_KEY": None}, None)
    config = _model_config("image_gen", api_key="dedicated")
    assert dedicated_multimodal_model_configured(config, "image_gen") is False

    sync_multimodal_env_omission_state(
        {},
        _full_snapshot_with_image_gen(),
    )
    assert dedicated_multimodal_model_configured(config, "image_gen") is True


def _full_snapshot_with_image_gen() -> dict[str, str]:
    return {
        "API_KEY": "main-key",
        "MODEL_NAME": "glm-5.1",
        "API_BASE": "https://example/v1",
        "IMAGE_GEN_API_KEY": "img-key",
    }
