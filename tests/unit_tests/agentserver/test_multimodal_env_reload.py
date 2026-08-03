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
build_multimodal_reconcile_env = _mm.build_multimodal_reconcile_env
sync_multimodal_env_omission_state = _mm.sync_multimodal_env_omission_state
multimodal_env_omission_disabled = _mm.multimodal_env_omission_disabled
reset_multimodal_env_omission_disabled = _mm.reset_multimodal_env_omission_disabled
apply_image_gen_model_config_from_yaml = _mm.apply_image_gen_model_config_from_yaml
clear_multimodal_env_groups = _mm.clear_multimodal_env_groups

from jiuwenclaw.local_env_config import (
    ENV_CONFIG_DICT,
    apply_env_overrides_to_active,
    clear_staged_env,
    get_active_env,
    make_env_ns_key,
    promote_staged_env,
    read_env,
    reset_local_env_state_for_tests,
    stage_env_overrides,
)


@pytest.fixture(autouse=True)
def _reset_env_state():
    saved = dict(os.environ)
    reset_multimodal_env_omission_disabled()
    reset_local_env_state_for_tests()
    for keys in _mm.MULTIMODAL_ENV_GROUP_KEYS.values():
        for key in keys:
            os.environ.pop(key, None)
            for aid in ("default", "office", "assistant"):
                os.environ.pop(make_env_ns_key("default", aid, key), None)
    yield
    reset_local_env_state_for_tests()
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
    apply_env_overrides_to_active(
        {
            "IMAGE_GEN_API_KEY": "old-key",
            "IMAGE_GEN_API_BASE": "https://old/v1",
        }
    )

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
    assert os.environ.get(make_env_ns_key("default", "default", "IMAGE_GEN_API_KEY")) is None


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
    sync_multimodal_env_omission_state(
        removals,
        new_env,
        service_id="default",
        agent_id="default",
    )

    assert (
        dedicated_multimodal_model_configured(
            literal_config, "image_gen", service_id="default", agent_id="default"
        )
        is False
    )
    apply_image_gen_model_config_from_yaml(literal_config)
    assert os.environ.get("IMAGE_GEN_API_KEY") is None


def test_omission_disabled_state_is_scoped_per_agent() -> None:
    """Office omitting vision must not disable assistant yaml vision."""
    literal_config = {
        "models": {
            "vision": {
                "model_client_config": {"api_key": "literal-vis-key"},
            }
        }
    }
    previous = _full_snapshot_base(
        VISION_API_KEY="office-vis",
        VISION_API_BASE="https://office/v1",
        VISION_MODEL_NAME="vl",
        VISION_PROVIDER="OpenAI",
    )
    new_env = _full_snapshot_base()
    removals = infer_multimodal_env_removals(
        previous,
        new_env,
        active_env=previous,
        service_id="default",
        agent_id="office",
    )
    sync_multimodal_env_omission_state(
        removals,
        new_env,
        service_id="default",
        agent_id="office",
    )

    assert multimodal_env_omission_disabled(
        "vision", service_id="default", agent_id="office"
    )
    assert not multimodal_env_omission_disabled(
        "vision", service_id="default", agent_id="assistant"
    )
    assert (
        dedicated_multimodal_model_configured(
            literal_config, "vision", service_id="default", agent_id="office"
        )
        is False
    )
    assert (
        dedicated_multimodal_model_configured(
            literal_config, "vision", service_id="default", agent_id="assistant"
        )
        is True
    )


def test_bare_vision_residue_does_not_affect_other_agent_omission() -> None:
    """Bare os.environ VISION_* must not make assistant look like it had vision."""
    os.environ["VISION_API_KEY"] = "bare-from-dotenv"
    new_env = _full_snapshot_base()

    removals = infer_multimodal_env_removals(
        None,
        new_env,
        service_id="default",
        agent_id="assistant",
    )
    assert removals == {}

    reconcile = build_multimodal_reconcile_env(
        service_id="default",
        agent_id="assistant",
    )
    assert "VISION_API_KEY" not in reconcile


def test_ns_vision_still_triggers_omission_for_that_agent() -> None:
    """Per-agent ns / tip Vision must still be omitted on full snapshot without Vision."""
    apply_env_overrides_to_active(
        {
            "VISION_API_KEY": "office-vis",
            "VISION_API_BASE": "https://office/v1",
            "VISION_MODEL_NAME": "vl",
            "VISION_PROVIDER": "OpenAI",
        },
        service_id="default",
        agent_id="office",
    )
    os.environ["VISION_API_KEY"] = "bare-should-not-matter"
    previous = _full_snapshot_base(
        VISION_API_KEY="office-vis",
        VISION_API_BASE="https://office/v1",
        VISION_MODEL_NAME="vl",
        VISION_PROVIDER="OpenAI",
    )
    new_env = _full_snapshot_base()

    removals = infer_multimodal_env_removals(
        previous,
        new_env,
        service_id="default",
        agent_id="office",
    )
    assert "VISION_API_KEY" in removals
    assert "VISION_MODEL_NAME" in removals


def test_empty_tip_uses_ns_anchor_with_sid_aid() -> None:
    """When tip is empty, reconcile must still see namespaced multimodal anchors."""
    from jiuwenclaw.local_env_config import make_env_ns_key

    os.environ[make_env_ns_key("default", "office", "VISION_API_KEY")] = "ns-only-vis"
    new_env = _full_snapshot_base()

    reconcile = build_multimodal_reconcile_env(
        service_id="default",
        agent_id="office",
    )
    assert reconcile.get("VISION_API_KEY") == "ns-only-vis"

    removals = infer_multimodal_env_removals(
        None,
        new_env,
        active_env=None,
        service_id="default",
        agent_id="office",
    )
    assert "VISION_API_KEY" in removals


def test_clear_multimodal_group_only_mutates_explicit_agent_namespace() -> None:
    """Creating an agent without Vision must not clear another agent's Vision bag."""
    apply_env_overrides_to_active(
        {"VISION_API_KEY": "default-vis"},
        service_id="default",
        agent_id="default",
    )
    apply_env_overrides_to_active(
        {"VISION_API_KEY": "office-vis"},
        service_id="default",
        agent_id="office",
    )

    clear_multimodal_env_groups(
        ["vision"],
        service_id="default",
        agent_id="office",
    )

    assert get_active_env("default", "office").get("VISION_API_KEY") is None
    assert (
        get_active_env("default", "default").get("VISION_API_KEY")
        == "default-vis"
    )
