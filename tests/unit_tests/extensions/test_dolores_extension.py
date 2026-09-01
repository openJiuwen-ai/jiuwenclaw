"""Regression tests for the dev-stable/Dolores compatibility layer."""

from types import SimpleNamespace


def test_dolores_runtime_config_uses_dev_stable_merged_baseline(monkeypatch) -> None:
    from jiuwenswarm.extensions.dolores import extension
    from jiuwenswarm.extensions.dolores.server.runtime.agent_adapter import (
        interface_deep as dolores_interface,
    )

    expected = {"react": {"max_iterations": 100}}
    monkeypatch.setattr(
        "jiuwenswarm.common.config.get_config",
        lambda: expected,
    )
    monkeypatch.setattr(
        dolores_interface,
        "get_config",
        lambda: {"react": {}},
    )

    extension._patch_dolores_runtime_config_baseline()

    assert dolores_interface.get_config() is expected
    assert (
        dolores_interface.get_config._dolores_uses_dev_stable_config_baseline
        is True
    )


def test_root_agent_loops_use_isolated_callback_namespaces() -> None:
    from jiuwenswarm.extensions.dolores.extension import (
        _patch_agent_loop_callback_namespace_isolation,
    )
    from jiuwenswarm.extensions.dolores.server.runtime.agent_adapter.agent_loop import (
        AgentLoop,
    )

    original_init = AgentLoop.__init__
    try:
        _patch_agent_loop_callback_namespace_isolation()
        card = SimpleNamespace(id="jiuwenswarm", name="test")
        common = {
            "card": card,
            "context_engine": object(),
            "system_prompt_builder": object(),
        }

        first = AgentLoop(**common)
        second = AgentLoop(**common)
        explicit = AgentLoop(**common, runtime_id="explicit.subagent.runtime")

        assert first._runtime_id.startswith("jiuwenswarm.instance.")
        assert second._runtime_id.startswith("jiuwenswarm.instance.")
        assert first._runtime_id != second._runtime_id
        assert first._agent_callback_manager.event_namespace == first._runtime_id
        assert second._agent_callback_manager.event_namespace == second._runtime_id
        assert explicit._runtime_id == "explicit.subagent.runtime"
    finally:
        AgentLoop.__init__ = original_init
