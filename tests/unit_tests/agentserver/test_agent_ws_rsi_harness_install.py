# -*- coding: utf-8 -*-
"""AgentServer composition and RSI Harness baseline tests."""

from jiuwenswarm.agents.harness.common.rsi.harness_activation import RsiHarnessActivationStore
from jiuwenswarm.server.agent_ws_server import AgentWebSocketServer


def _bare_server(manager):
    server = object.__new__(AgentWebSocketServer)
    server._rsi_handlers = None
    server._rsi_harness_provider = None
    server._agent_manager = manager
    return server


def test_rsi_context_binds_installer_to_agent_manager(monkeypatch, tmp_path):
    class FakeManager:
        pass

    manager = FakeManager()
    monkeypatch.setenv("RSI_PROVIDER_MODE", "mock")
    monkeypatch.setattr(
        "jiuwenswarm.common.utils.get_user_workspace_dir",
        lambda: tmp_path,
    )

    handlers = _bare_server(manager)._get_rsi_handlers()

    assert handlers.context.harness_installer.agent_manager is manager


def test_rsi_active_harness_precedes_generic_registry(monkeypatch, tmp_path):
    runtime_path = (
        tmp_path
        / "workspace"
        / "rsi"
        / "rsi-task"
        / "harness"
        / "versions"
        / "install-a"
        / "validation_harness"
    )
    runtime_path.mkdir(parents=True)
    store = RsiHarnessActivationStore(tmp_path / "workspace" / "rsi")
    store.commit(
        {
            "installation_id": "install-a",
            "task_id": "rsi-task",
            "runtime_path": str(runtime_path),
            "sha256": "a" * 64,
        }
    )
    monkeypatch.setattr(
        "jiuwenswarm.common.utils.get_user_workspace_dir",
        lambda: tmp_path,
    )

    server = _bare_server(object())

    assert server._rsi_harness_refs_provider() == str(runtime_path.resolve())


def test_rsi_initial_refs_are_a_trusted_input_fallback(monkeypatch, tmp_path):
    initial_refs = tmp_path / "rsi" / "harness" / "initial_harness_refs.yaml"
    initial_refs.parent.mkdir(parents=True)
    initial_refs.write_text("version: 1\nharness_refs: {}\n", encoding="utf-8")
    monkeypatch.setattr(
        "jiuwenswarm.common.utils.get_user_workspace_dir",
        lambda: tmp_path,
    )

    server = _bare_server(object())

    assert server._rsi_harness_refs_provider() == str(initial_refs.resolve())
