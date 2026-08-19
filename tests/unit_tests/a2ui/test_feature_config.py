# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations


def test_a2ui_config_defaults_disabled():
    from jiuwenswarm.server.runtime.a2ui.config import get_a2ui_config

    cfg = get_a2ui_config({})

    assert cfg.enabled is False
    assert cfg.generation_enabled is False
    assert cfg.rendering_enabled is False
    assert cfg.protocol_version == "0.8"
    assert cfg.stream_validation_enabled is True
    assert cfg.non_web_fallback_enabled is False
    assert cfg.dev_smoke_tools_enabled is False


def test_a2ui_config_env_can_enable_feature(monkeypatch):
    from jiuwenswarm.server.runtime.a2ui.config import get_a2ui_config

    monkeypatch.setenv("JIUWENSWARM_A2UI_ENABLED", "true")

    cfg = get_a2ui_config({"a2ui": {"enabled": False}})

    assert cfg.enabled is True
    assert cfg.rendering_enabled is True


def test_a2ui_legacy_yaml_initializes_both_switches():
    """The legacy YAML switch should seed both new capabilities."""
    from jiuwenswarm.server.runtime.a2ui.config import get_a2ui_config

    cfg = get_a2ui_config({"a2ui": {"enabled": True}})

    assert cfg.generation_enabled is True
    assert cfg.rendering_enabled is True


def test_a2ui_new_yaml_switches_are_independent():
    """New YAML switches should override the legacy seed independently."""
    from jiuwenswarm.server.runtime.a2ui.config import get_a2ui_config

    cfg = get_a2ui_config({
        "a2ui": {
            "enabled": True,
            "generation_enabled": False,
            "rendering_enabled": True,
        },
    })

    assert cfg.generation_enabled is False
    assert cfg.rendering_enabled is True


def test_a2ui_dedicated_env_switches_override_legacy_env(monkeypatch):
    """Dedicated environment variables should win over the legacy alias."""
    from jiuwenswarm.server.runtime.a2ui.config import get_a2ui_config

    monkeypatch.setenv("JIUWENSWARM_A2UI_ENABLED", "true")
    monkeypatch.setenv("JIUWENSWARM_A2UI_GENERATION_ENABLED", "false")
    monkeypatch.setenv("JIUWENSWARM_A2UI_RENDERING_ENABLED", "false")

    cfg = get_a2ui_config({"a2ui": {"rendering_enabled": False}})

    assert cfg.generation_enabled is False
    assert cfg.rendering_enabled is False


def test_legacy_jiuwenclaw_env_alias_no_longer_overrides_config(monkeypatch):
    from jiuwenswarm.server.runtime.a2ui.config import get_a2ui_config

    monkeypatch.delenv("JIUWENSWARM_A2UI_ENABLED", raising=False)
    monkeypatch.setenv("JIUWENCLAW_A2UI_ENABLED", "false")

    cfg = get_a2ui_config({"a2ui": {"enabled": True}})

    assert cfg.enabled is True
    assert cfg.rendering_enabled is True


def test_a2ui_config_rejects_unknown_protocol_version():
    import pytest

    from jiuwenswarm.server.runtime.a2ui.config import get_a2ui_config

    with pytest.raises(ValueError, match="Unsupported A2UI protocol version"):
        get_a2ui_config({"a2ui": {"protocol_version": "0.9"}})
