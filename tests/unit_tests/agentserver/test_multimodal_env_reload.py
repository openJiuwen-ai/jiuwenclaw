# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for multimodal env omission reconcile on agent.reload_config."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

_MM_PATH = (
    Path(__file__).resolve().parents[3]
    / "jiuwenclaw"
    / "agentserver"
    / "tools"
    / "multimodal_config.py"
)
_spec = importlib.util.spec_from_file_location("multimodal_config_env_reload_test", _MM_PATH)
assert _spec and _spec.loader
_mm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mm)

dedicated_multimodal_model_configured = _mm.dedicated_multimodal_model_configured
infer_multimodal_env_removals = _mm.infer_multimodal_env_removals
is_full_env_reload_snapshot = _mm.is_full_env_reload_snapshot
sync_multimodal_env_omission_state = _mm.sync_multimodal_env_omission_state
reset_multimodal_env_omission_disabled = _mm.reset_multimodal_env_omission_disabled
apply_image_gen_model_config_from_yaml = _mm.apply_image_gen_model_config_from_yaml

from jiuwenclaw.local_env_config import (
    ENV_CONFIG_DICT,
    apply_env_overrides_to_active,
    clear_staged_env,
    promote_staged_env,
    read_env,
    stage_env_overrides,
)


@pytest.fixture(autouse=True)
def _reset_env_state():
    saved = dict(os.environ)
    reset_multimodal_env_omission_disabled()
    ENV_CONFIG_DICT.clear()
    clear_staged_env()
    for keys in _mm.MULTIMODAL_ENV_GROUP_KEYS.values():
        for key in keys:
            os.environ.pop(key, None)
    yield
    ENV_CONFIG_DICT.clear()
    clear_staged_env()
    os.environ.clear()
    os.environ.update(saved)


def _full_snapshot_base(**extra: str) -> dict[str, str]:
    base = {
        "API_KEY": "main-key",
        "MODEL_NAME": "glm-5.1",
        "API_BASE": "https://example/v1",
    }
    base.update(extra)
    return base


def test_is_full_env_reload_snapshot() -> None:
    assert is_full_env_reload_snapshot(_full_snapshot_base()) is True
    assert is_full_env_reload_snapshot({"MODEL_NAME": "m"}) is False
    assert is_full_env_reload_snapshot({}) is False
    assert is_full_env_reload_snapshot(None) is False


def test_infer_removals_image_gen_when_omitted_from_full_snapshot() -> None:
    previous = _full_snapshot_base(
        IMAGE_GEN_API_KEY="img-key",
        IMAGE_GEN_API_BASE="https://img/v1",
        IMAGE_GEN_MODEL_NAME="wanx",
        IMAGE_GEN_PROVIDER="OpenAI",
    )
    new_env = _full_snapshot_base()

    removals = infer_multimodal_env_removals(previous, new_env, active_env={})

    assert set(removals.keys()) == {
        "IMAGE_GEN_API_KEY",
        "IMAGE_GEN_API_BASE",
        "IMAGE_GEN_MODEL_NAME",
        "IMAGE_GEN_PROVIDER",
    }
    assert all(v is None for v in removals.values())


def test_infer_removals_vision_when_omitted() -> None:
    previous = _full_snapshot_base(
        VISION_API_KEY="vis-key",
        VISION_API_BASE="https://vis/v1",
        VISION_MODEL_NAME="qwen-vl",
        VISION_PROVIDER="OpenAI",
    )
    new_env = _full_snapshot_base()

    removals = infer_multimodal_env_removals(previous, new_env, active_env={})

    assert "VISION_API_KEY" in removals
    assert "VISION_MODEL_NAME" in removals


def test_infer_skips_partial_env_patch() -> None:
    previous = _full_snapshot_base(IMAGE_GEN_API_KEY="img-key")
    ENV_CONFIG_DICT["IMAGE_GEN_API_KEY"] = "img-key"

    removals = infer_multimodal_env_removals(
        previous,
        {"MODEL_NAME": "new-model"},
        active_env=ENV_CONFIG_DICT,
    )

    assert removals == {}


def test_infer_skips_when_never_had_multimodal() -> None:
    new_env = _full_snapshot_base()
    removals = infer_multimodal_env_removals(None, new_env, active_env={})
    assert removals == {}


def test_stage_and_promote_clears_omitted_image_gen() -> None:
    ENV_CONFIG_DICT["IMAGE_GEN_API_KEY"] = "old-key"
    ENV_CONFIG_DICT["IMAGE_GEN_API_BASE"] = "https://old/v1"
    os.environ["IMAGE_GEN_API_KEY"] = "old-key"
    os.environ["IMAGE_GEN_API_BASE"] = "https://old/v1"

    previous = _full_snapshot_base(
        IMAGE_GEN_API_KEY="old-key",
        IMAGE_GEN_API_BASE="https://old/v1",
        IMAGE_GEN_MODEL_NAME="wanx",
        IMAGE_GEN_PROVIDER="OpenAI",
    )
    new_env = _full_snapshot_base()
    removals = infer_multimodal_env_removals(previous, new_env, active_env=ENV_CONFIG_DICT)
    apply_env_overrides_to_active(removals)
    stage_env_overrides(new_env)
    promote_staged_env()

    assert read_env("IMAGE_GEN_API_KEY") == ""
    assert "IMAGE_GEN_API_KEY" not in ENV_CONFIG_DICT
    assert os.environ.get("IMAGE_GEN_API_KEY") is None


def test_dedicated_multimodal_resolves_empty_env_placeholder() -> None:
    config = {
        "models": {
            "image_gen": {
                "model_client_config": {
                    "api_key": "${IMAGE_GEN_API_KEY}",
                }
            }
        }
    }
    assert dedicated_multimodal_model_configured(config, "image_gen") is False

    ENV_CONFIG_DICT["IMAGE_GEN_API_KEY"] = "resolved-key"
    assert dedicated_multimodal_model_configured(config, "image_gen") is True


def test_sync_omission_state_disables_literal_yaml_group() -> None:
    literal_config = {
        "models": {
            "image_gen": {
                "model_client_config": {"api_key": "literal-img-key"},
            }
        }
    }
    assert dedicated_multimodal_model_configured(literal_config, "image_gen") is True

    previous = _full_snapshot_base(
        IMAGE_GEN_API_KEY="img-key",
        IMAGE_GEN_API_BASE="https://img/v1",
    )
    new_env = _full_snapshot_base()
    removals = infer_multimodal_env_removals(previous, new_env, active_env={})
    sync_multimodal_env_omission_state(removals, new_env)

    assert dedicated_multimodal_model_configured(literal_config, "image_gen") is False
    apply_image_gen_model_config_from_yaml(literal_config)
    assert os.environ.get("IMAGE_GEN_API_KEY") is None
