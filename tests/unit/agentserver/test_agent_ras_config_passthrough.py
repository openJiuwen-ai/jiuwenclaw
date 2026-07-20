"""Unit tests for Agent RAS YAML passthrough / transitional cleanup (PR #3697).

Avoids a full ``interface_deep`` import side-effect chain: memory → jieba →
``pkg_resources`` UserWarning, which fails collection when
``filterwarnings = error`` (see pytest.ini).
"""

from __future__ import annotations

import importlib
import inspect
import sys
import types

import pytest


def _stub_jieba_before_interface_deep_import() -> None:
    """Stub jieba so importing interface_deep does not load pkg_resources."""
    if "jieba" in sys.modules:
        return
    jieba = types.ModuleType("jieba")
    jieba.__path__ = []
    sys.modules["jieba"] = jieba
    sys.modules["jieba.finalseg"] = types.ModuleType("jieba.finalseg")
    sys.modules["jieba._compat"] = types.ModuleType("jieba._compat")


_stub_jieba_before_interface_deep_import()

interface_module = importlib.import_module(
    "jiuwenclaw.agentserver.deep_agent.interface_deep"
)
JiuWenClawDeepAdapter = interface_module.JiuWenClawDeepAdapter
_agent_ras_kwargs_from_config = interface_module._agent_ras_kwargs_from_config


@pytest.fixture
def require_agent_ras_config():
    if interface_module.AgentRASConfig is None:
        pytest.skip("openjiuwen.harness.agent_ras not installed")


def test_agent_ras_kwargs_missing_section_returns_empty() -> None:
    assert _agent_ras_kwargs_from_config({}) == {}
    assert _agent_ras_kwargs_from_config(None) == {}


def test_agent_ras_kwargs_non_dict_ignored() -> None:
    assert _agent_ras_kwargs_from_config({"agent_ras": "bad"}) == {}
    assert _agent_ras_kwargs_from_config({"agent_ras": [1, 2]}) == {}


def test_agent_ras_kwargs_valid_config(require_agent_ras_config) -> None:
    kwargs = _agent_ras_kwargs_from_config(
        {
            "agent_ras": {
                "enabled": True,
                "detectors": {
                    "repeat_tool": {"warning_threshold": 6},
                },
            }
        }
    )
    assert "agent_ras" in kwargs
    cfg = kwargs["agent_ras"]
    assert cfg["enabled"] is True
    assert cfg["detectors"]["repeat_tool"]["warning_threshold"] == 6


def test_agent_ras_kwargs_invalid_key_raises(require_agent_ras_config) -> None:
    with pytest.raises(ValueError, match="invalid agent_ras config"):
        _agent_ras_kwargs_from_config({"agent_ras": {"foo": 1}})


def test_agent_ras_kwargs_unavailable_returns_empty(monkeypatch) -> None:
    """Older openjiuwen without agent_ras: degrade to empty kwargs."""
    monkeypatch.setattr(interface_module, "AgentRASConfig", None)
    monkeypatch.setattr(interface_module, "_AGENT_RAS_UNAVAILABLE_WARNED", False)
    assert _agent_ras_kwargs_from_config({}) == {}
    assert _agent_ras_kwargs_from_config({"agent_ras": {"enabled": False}}) == {}


def test_cleanup_circuit_breaker_session_noop() -> None:
    # staticmethod no-op: call via class (no instance / heavy init needed)
    JiuWenClawDeepAdapter._cleanup_circuit_breaker_session(None)
    JiuWenClawDeepAdapter._cleanup_circuit_breaker_session("officeclaw_test_session")


def test_agent_ras_update_before_create_code_agent() -> None:
    """Regression: code mode must receive agent_ras (PR #3697 review)."""
    src = inspect.getsource(JiuWenClawDeepAdapter._init_agent_instance_sync)
    update_idx = src.find("common_kwargs.update(_agent_ras_kwargs_from_config")
    code_idx = src.find("create_code_agent(**common_kwargs)")
    assert update_idx >= 0, "missing agent_ras common_kwargs.update"
    assert code_idx >= 0, "missing create_code_agent call"
    assert update_idx < code_idx, "agent_ras must be merged before create_code_agent"
