# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for interface_deep evolution-related functionality."""

import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from jiuwenclaw.agentserver.deep_agent import interface_deep as interface_deep_module
from jiuwenclaw.agentserver.deep_agent.interface_deep import JiuWenClawDeepAdapter


class DeepAdapterHarness(JiuWenClawDeepAdapter):
    """Test harness to expose protected methods."""
    
    def build_skill_evolution_rail_for_test(self, config: dict[str, Any]):
        """Expose _build_skill_evolution_rail for testing."""
        return self._build_skill_evolution_rail(config)


@pytest.fixture
def adapter():
    """Create a test adapter instance."""
    return DeepAdapterHarness()


def _setup_mocks(monkeypatch: pytest.MonkeyPatch):
    """Set up mocks for SkillEvolutionRail and related classes."""
    captured_args = []
    captured_kwargs = []
    
    def mock_skill_evolution_rail(*args, **kwargs):
        captured_args.append(args)
        captured_kwargs.append(kwargs)
        return MagicMock()
    
    monkeypatch.setattr(
        interface_deep_module,
        "SkillEvolutionRail",
        mock_skill_evolution_rail
    )
    
    # Mock other dependencies
    monkeypatch.setattr(
        interface_deep_module,
        "FileTrajectoryStore",
        lambda *args, **kwargs: MagicMock()
    )
    
    # Mock get_agent_registered_skill_dirs (replaces legacy _resolve_skill_dirs)
    monkeypatch.setattr(
        interface_deep_module,
        "get_agent_registered_skill_dirs",
        lambda: [Path("mock_skills_dir")],
    )

    # Mock _resolve_evolution_trajectory_dir - fix: use instance method signature
    def mock_resolve_evolution_trajectory_dir(self):
        return Path("/mock/trajectory/path")
    
    monkeypatch.setattr(
        JiuWenClawDeepAdapter,
        "_resolve_evolution_trajectory_dir",
        mock_resolve_evolution_trajectory_dir
    )
    
    return captured_args, captured_kwargs


@pytest.mark.unit
def test_config_explicit_auto_save_true(adapter, monkeypatch):
    """Test config explicitly sets evolution.auto_save = true."""
    captured_args, captured_kwargs = _setup_mocks(monkeypatch)
    config = {
        "evolution": {
            "auto_save": True,
            "auto_scan": False,
        }
    }
    
    adapter.build_skill_evolution_rail_for_test(config)
    
    assert captured_kwargs[0].get("auto_save") is True


@pytest.mark.unit
def test_config_explicit_auto_save_false(adapter, monkeypatch):
    """Test config explicitly sets evolution.auto_save = false."""
    captured_args, captured_kwargs = _setup_mocks(monkeypatch)
    config = {
        "evolution": {
            "auto_save": False,
            "auto_scan": False,
        }
    }
    
    adapter.build_skill_evolution_rail_for_test(config)
    
    assert captured_kwargs[0].get("auto_save") is False


@pytest.mark.unit
def test_config_default_auto_save_true(adapter, monkeypatch):
    """Test config doesn't set evolution.auto_save, default should be true."""
    captured_args, captured_kwargs = _setup_mocks(monkeypatch)
    config = {
        "evolution": {
            "auto_scan": False,
        }
    }
    
    adapter.build_skill_evolution_rail_for_test(config)
    
    assert captured_kwargs[0].get("auto_save") is True



