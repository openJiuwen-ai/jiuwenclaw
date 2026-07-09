from __future__ import annotations

import json

from jiuwenavatar.server.runtime.skill.skilldev.state_utils import (
    get_registered_skill_names,
    get_skill_enabled,
    list_disabled_skills,
    list_execution_disabled_skills,
    normalize_local_skills,
    normalize_skill_configs,
    set_skill_enabled,
)
from jiuwenavatar.server.runtime.skill.skill_manager import SkillManager


def test_skill_manager_default_initialization_uses_global_state_file(monkeypatch, tmp_path):
    """Default SkillManager initialization should resolve the global state file."""
    skills_dir = tmp_path / "skills"
    monkeypatch.setattr(
        "jiuwenavatar.server.runtime.skill.skill_manager.get_agent_skills_dir",
        lambda: skills_dir,
    )
    monkeypatch.setattr(
        "jiuwenavatar.server.runtime.skill.skilldev.state_utils.get_agent_skills_dir",
        lambda: skills_dir,
    )

    manager = SkillManager()
    manager.set_skill_enabled("global-state-skill", False)
    state = json.loads((skills_dir / "skills_state.json").read_text(encoding="utf-8"))

    assert get_skill_enabled(state, "global-state-skill") is False
    assert skills_dir.is_dir()


def test_normalize_skill_configs_defaults_enabled_true():
    normalized = normalize_skill_configs(
        {
            "plugin-skill": {},
            "local-skill": {"enabled": False},
            " ": {"enabled": False},
            123: {"enabled": False},
        }
    )

    assert normalized == {
        "plugin-skill": {"enabled": True},
        "local-skill": {"enabled": False},
    }


def test_normalize_skill_configs_treats_missing_enabled_as_true():
    normalized = normalize_skill_configs(
        {
            "builtin-candidate": {"note": "no enabled field"},
        }
    )

    assert normalized["builtin-candidate"]["enabled"] is True


def test_registered_skill_names_covers_installed_plugins_and_local_skills():
    state = {
        "installed_plugins": [
            {"name": "builtin-skill"},
            {"name": "market-skill"},
        ],
        "local_skills": [
            {"name": "imported-skill"},
        ],
    }

    assert get_registered_skill_names(state) == {
        "builtin-skill",
        "market-skill",
        "imported-skill",
    }


def test_normalize_local_skills_drops_stale_records():
    local_skills = [
        {"name": "kept-skill", "origin": "C:\\keep", "source": "local"},
        {"name": "stale-skill", "origin": "C:\\stale", "source": "local"},
        {"name": "", "origin": "C:\\bad", "source": "local"},
    ]

    normalized = normalize_local_skills(local_skills, {"kept-skill"})

    assert normalized == [
        {"name": "kept-skill", "origin": "C:\\keep", "source": "local"},
    ]


def test_set_skill_enabled_supports_plugin_and_local_skill_records():
    state = {
        "installed_plugins": [{"name": "builtin-skill"}],
        "local_skills": [{"name": "imported-skill"}],
    }

    set_skill_enabled(state, "builtin-skill", False)
    set_skill_enabled(state, "imported-skill", False)

    assert get_skill_enabled(state, "builtin-skill") is False
    assert get_skill_enabled(state, "imported-skill") is False
    assert list_disabled_skills(state) == ["builtin-skill", "imported-skill"]


def test_set_skill_enabled_also_supports_uninstalled_skill():
    state = {
        "installed_plugins": [],
        "local_skills": [],
    }

    set_skill_enabled(state, "builtin-candidate", False)

    assert get_skill_enabled(state, "builtin-candidate") is False
    assert list_disabled_skills(state) == ["builtin-candidate"]
    assert list_execution_disabled_skills(state) == []


def test_get_skill_enabled_defaults_true_for_legacy_state():
    legacy_state = {
        "installed_plugins": [{"name": "legacy-plugin"}],
        "local_skills": [{"name": "legacy-local"}],
    }

    assert get_skill_enabled(legacy_state, "legacy-plugin") is True
    assert get_skill_enabled(legacy_state, "legacy-local") is True
