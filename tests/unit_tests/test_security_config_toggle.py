# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for the security configuration toggle script."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml


def _load_script_module():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "security_config_toggle.py"
    spec = importlib.util.spec_from_file_location("security_config_toggle", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_enable_security_config_sets_review_and_permissions(tmp_path: Path):
    module = _load_script_module()
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
react:
  security_review:
    enabled: false
    runtime_advice: false
permissions:
  enabled: false
  schema: legacy
  permission_mode: normal
  defaults:
    "*": allow
""".lstrip(),
        encoding="utf-8",
    )

    result = module.apply_security_config(config_path, enabled=True)

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert result["mode"] == "on"
    assert raw["react"]["security_review"]["enabled"] is True
    assert raw["react"]["security_review"]["runtime_advice"] is True
    assert raw["react"]["security_review"]["async_review"] is True
    assert raw["react"]["security_review"]["evolve_security_skills"] is True
    assert raw["react"]["security_review"]["propose_policy_rules"] is True
    assert raw["react"]["security_review"]["timely_tool_failure_review"] is True
    assert raw["permissions"]["enabled"] is True
    assert raw["permissions"]["schema"] == "tiered_policy"
    assert raw["permissions"]["permission_mode"] == "normal"
    assert raw["permissions"]["defaults"]["*"] == "allow"
    backups = list(tmp_path.glob("config.yaml.bak-security-*"))
    assert len(backups) == 1


def test_disable_security_config_turns_off_only_master_switches(tmp_path: Path):
    module = _load_script_module()
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
react:
  security_review:
    enabled: true
    runtime_advice: true
    evolve_security_skills: true
permissions:
  enabled: true
  schema: tiered_policy
  permission_mode: strict
""".lstrip(),
        encoding="utf-8",
    )

    result = module.apply_security_config(config_path, enabled=False)

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert result["mode"] == "off"
    assert raw["react"]["security_review"]["enabled"] is False
    assert raw["react"]["security_review"]["runtime_advice"] is True
    assert raw["react"]["security_review"]["evolve_security_skills"] is True
    assert raw["permissions"]["enabled"] is False
    assert raw["permissions"]["schema"] == "tiered_policy"
    assert raw["permissions"]["permission_mode"] == "strict"


def test_status_reports_security_review_and_permission_state(tmp_path: Path):
    module = _load_script_module()
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
react:
  security_review:
    enabled: true
permissions:
  enabled: false
  schema: tiered_policy
""".lstrip(),
        encoding="utf-8",
    )

    status = module.read_security_config_status(config_path)

    assert status == {
        "config": str(config_path),
        "security_review_enabled": True,
        "runtime_advice_enabled": None,
        "permissions_enabled": False,
        "permissions_schema": "tiered_policy",
        "permission_mode": None,
    }


def test_enable_security_config_creates_missing_sections(tmp_path: Path):
    module = _load_script_module()
    config_path = tmp_path / "config.yaml"
    config_path.write_text("preferred_language: zh\n", encoding="utf-8")

    module.apply_security_config(config_path, enabled=True)

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert raw["react"]["security_review"]["enabled"] is True
    assert raw["permissions"]["enabled"] is True
    assert raw["permissions"]["defaults"]["*"] == "allow"
