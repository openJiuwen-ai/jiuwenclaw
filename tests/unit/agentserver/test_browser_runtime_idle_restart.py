# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Multi-generation browser runtime: pin old requests, route new ones, GC at idle."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

from jiuwenclaw.local_env_config import (
    apply_env_overrides_to_active,
    clear_agent_env_ns,
    reset_local_env_state_for_tests,
)


def _install_openjiuwen_stubs() -> None:
    """Avoid heavy openjiuwen import chain when loading browser_tools in isolation."""
    stubs = {
        "openjiuwen": ModuleType("openjiuwen"),
        "openjiuwen.core": ModuleType("openjiuwen.core"),
        "openjiuwen.core.foundation": ModuleType("openjiuwen.core.foundation"),
        "openjiuwen.core.foundation.tool": ModuleType("openjiuwen.core.foundation.tool"),
        "openjiuwen.core.runner": ModuleType("openjiuwen.core.runner"),
    }
    stubs["openjiuwen.core.foundation.tool"].McpServerConfig = MagicMock
    stubs["openjiuwen.core.runner"].Runner = MagicMock
    for name, mod in stubs.items():
        sys.modules.setdefault(name, mod)


def _load_browser_tools():
    _install_openjiuwen_stubs()
    mod_name = "jiuwenclaw_browser_tools_idle_restart_under_test"
    if mod_name in sys.modules:
        # Reload so tests pick up source edits without restarting pytest process.
        del sys.modules[mod_name]
    path = (
        Path(__file__).resolve().parents[3]
        / "jiuwenclaw"
        / "agentserver"
        / "tools"
        / "browser_tools.py"
    )
    spec = importlib.util.spec_from_file_location(mod_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


bt = _load_browser_tools()

# Tenants touched by this module; must clear_agent_env_ns *before* bag reset
# so namespaced os.environ keys are popped while active bags still list them.
_BROWSER_ENV_NS_PAIRS = (
    ("default", "default"),
    ("svc-a", "agent-a"),
    ("svc-b", "agent-b"),
)


def _clear_browser_env_ns() -> None:
    for service_id, agent_id in _BROWSER_ENV_NS_PAIRS:
        clear_agent_env_ns(service_id, agent_id)


@pytest.fixture(autouse=True)
def _reset_browser_runtime_state():
    bt.stop_all_browser_runtime_servers()
    bt.reset_browser_runtime_reload_state_for_tests()
    _clear_browser_env_ns()
    reset_local_env_state_for_tests()
    yield
    bt.stop_all_browser_runtime_servers()
    bt.reset_browser_runtime_reload_state_for_tests()
    # Pop ns keys while bags still know them; reset alone leaves os.environ dirty.
    _clear_browser_env_ns()
    reset_local_env_state_for_tests()


def _fake_alive_process() -> MagicMock:
    proc = MagicMock()
    proc.poll.return_value = None
    return proc


def _apply_creds(
    *,
    api_key: str = "k1",
    model_name: str = "m1",
    service_id: str = "default",
    agent_id: str = "default",
    extra: dict | None = None,
) -> None:
    payload = {
        "API_KEY": api_key,
        "API_BASE": "https://api.example/v1",
        "MODEL_NAME": model_name,
        "MODEL_PROVIDER": "openai",
    }
    if extra:
        payload.update(extra)
    apply_env_overrides_to_active(
        payload,
        service_id=service_id,
        agent_id=agent_id,
    )


def _seed_alive_slot(
    *,
    service_id: str = "default",
    agent_id: str = "default",
    url: str = "http://127.0.0.1:8940/mcp",
    env_hash: str | None = None,
) -> bt.BrowserRuntimeSlot:
    key = (service_id, agent_id)
    hash_value = env_hash or bt._model_credential_fingerprint(
        service_id=service_id,
        agent_id=agent_id,
    )
    slot = bt._get_or_create_slot(key, hash_value)
    slot.process = _fake_alive_process()
    slot.server_url = url
    slot.env_hash = hash_value
    return slot


def test_mark_diverged_when_model_credentials_change():
    _apply_creds()
    slot = _seed_alive_slot(
        env_hash=bt._model_credential_fingerprint(
            service_id="default", agent_id="default"
        ),
    )

    apply_env_overrides_to_active(
        {"MODEL_NAME": "m2"},
        service_id="default",
        agent_id="default",
    )
    assert (
        bt.mark_browser_runtime_stale_if_credentials_changed(
            service_id="default",
            agent_id="default",
        )
        is True
    )
    assert bt._slot_process_alive(slot) is True


def test_skills_change_does_not_diverge():
    _apply_creds(extra={"ENABLED_SKILLS": "a,b"})
    slot = _seed_alive_slot(
        env_hash=bt._model_credential_fingerprint(
            service_id="default", agent_id="default"
        ),
    )

    apply_env_overrides_to_active(
        {"ENABLED_SKILLS": "a,b,c"},
        service_id="default",
        agent_id="default",
    )
    assert (
        bt.mark_browser_runtime_stale_if_credentials_changed(
            service_id="default",
            agent_id="default",
        )
        is False
    )
    assert bt._slot_process_alive(slot) is True


def test_notify_idle_gcs_obsolete_generation():
    _apply_creds()
    old_hash = bt._model_credential_fingerprint(
        service_id="default", agent_id="default"
    )
    slot = _seed_alive_slot(env_hash=old_hash)
    proc = slot.process

    apply_env_overrides_to_active(
        {"API_KEY": "k2"},
        service_id="default",
        agent_id="default",
    )

    bt.notify_browser_runtime_after_reload(
        idle=True,
        service_id="default",
        agent_id="default",
    )

    assert old_hash not in bt.list_browser_runtime_env_hashes(
        service_id="default", agent_id="default"
    )
    proc.terminate.assert_called()


def test_notify_busy_keeps_old_generation():
    _apply_creds()
    old_hash = bt._model_credential_fingerprint(
        service_id="default", agent_id="default"
    )
    slot = _seed_alive_slot(env_hash=old_hash)
    proc = slot.process

    apply_env_overrides_to_active(
        {"MODEL_NAME": "m2"},
        service_id="default",
        agent_id="default",
    )
    with patch.object(bt, "_stop_slot") as stop:
        bt.notify_browser_runtime_after_reload(
            idle=False,
            service_id="default",
            agent_id="default",
        )

    stop.assert_not_called()
    assert bt._slot_process_alive(slot) is True
    assert slot.process is proc
    assert old_hash in bt.list_browser_runtime_env_hashes(
        service_id="default", agent_id="default"
    )


def test_ensure_keeps_old_and_starts_new_for_tip():
    _apply_creds()
    old_hash = bt._model_credential_fingerprint(
        service_id="default", agent_id="default"
    )
    old_slot = _seed_alive_slot(
        url="http://127.0.0.1:8940/old",
        env_hash=old_hash,
    )
    old_proc = old_slot.process

    apply_env_overrides_to_active(
        {"MODEL_NAME": "m2"},
        service_id="default",
        agent_id="default",
    )
    new_hash = bt._model_credential_fingerprint(
        service_id="default", agent_id="default"
    )
    assert new_hash != old_hash

    with (
        patch.object(bt, "_start_local_server", return_value="http://127.0.0.1:8941/new") as start,
        patch.object(bt, "_pick_available_port", return_value=8941),
        patch.object(bt, "_runtime_host", return_value="127.0.0.1"),
        patch.object(bt, "_runtime_port", return_value="8940"),
        patch.object(bt, "_runtime_path", return_value="/mcp"),
    ):
        url = bt._ensure_local_server_started(
            "streamable-http",
            service_id="default",
            agent_id="default",
        )

    assert url == "http://127.0.0.1:8941/new"
    start.assert_called_once()
    assert start.call_args.kwargs.get("env_hash") == new_hash
    assert bt._slot_process_alive(old_slot) is True
    assert old_slot.process is old_proc


def test_pinned_request_routes_to_old_generation_after_tip_change():
    _apply_creds()
    old_hash = bt._model_credential_fingerprint(
        service_id="default", agent_id="default"
    )
    _seed_alive_slot(url="http://127.0.0.1:8940/old", env_hash=old_hash)

    pin = bt.pin_browser_runtime_generation(service_id="default", agent_id="default")
    assert pin.env_hash == old_hash

    apply_env_overrides_to_active(
        {"MODEL_NAME": "m2"},
        service_id="default",
        agent_id="default",
    )

    with patch.object(bt, "_start_local_server") as start:
        url = bt._ensure_local_server_started(
            "streamable-http",
            service_id="default",
            agent_id="default",
        )

    assert url == "http://127.0.0.1:8940/old"
    start.assert_not_called()

    bt.reset_browser_runtime_generation(pin)


def test_new_request_after_reload_uses_new_generation():
    _apply_creds()
    old_hash = bt._model_credential_fingerprint(
        service_id="default", agent_id="default"
    )
    _seed_alive_slot(url="http://127.0.0.1:8940/old", env_hash=old_hash)

    apply_env_overrides_to_active(
        {"MODEL_NAME": "m2"},
        service_id="default",
        agent_id="default",
    )
    new_hash = bt._model_credential_fingerprint(
        service_id="default", agent_id="default"
    )
    pin = bt.pin_browser_runtime_generation(service_id="default", agent_id="default")
    assert pin.env_hash == new_hash

    with (
        patch.object(bt, "_start_local_server", return_value="http://127.0.0.1:8941/new") as start,
        patch.object(bt, "_pick_available_port", return_value=8941),
        patch.object(bt, "_runtime_host", return_value="127.0.0.1"),
        patch.object(bt, "_runtime_port", return_value="8940"),
        patch.object(bt, "_runtime_path", return_value="/mcp"),
    ):
        url = bt._ensure_local_server_started(
            "streamable-http",
            service_id="default",
            agent_id="default",
        )

    assert url == "http://127.0.0.1:8941/new"
    assert start.call_args.kwargs.get("env_hash") == new_hash
    bt.reset_browser_runtime_generation(pin)


def test_gc_skips_pinned_obsolete_generation():
    _apply_creds()
    old_hash = bt._model_credential_fingerprint(
        service_id="default", agent_id="default"
    )
    slot = _seed_alive_slot(url="http://127.0.0.1:8940/old", env_hash=old_hash)
    pin = bt.pin_browser_runtime_generation(service_id="default", agent_id="default")

    apply_env_overrides_to_active(
        {"API_KEY": "k2"},
        service_id="default",
        agent_id="default",
    )
    stopped = bt.gc_obsolete_browser_runtime_slots(
        service_id="default",
        agent_id="default",
    )
    assert stopped == 0
    assert bt._slot_process_alive(slot) is True

    bt.reset_browser_runtime_generation(pin)
    # unpin of obsolete generation triggers opportunistic GC
    assert old_hash not in bt.list_browser_runtime_env_hashes(
        service_id="default", agent_id="default"
    )


def test_build_subprocess_env_prefers_tip_over_bare_os(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "bare-stale-key")
    apply_env_overrides_to_active(
        {
            "API_KEY": "tip-key",
            "API_BASE": "https://api.openai.com/v1",
            "MODEL_NAME": "gpt-test",
            "MODEL_PROVIDER": "openai",
        },
        service_id="default",
        agent_id="default",
    )
    env = bt._build_browser_runtime_subprocess_env(
        service_id="default",
        agent_id="default",
    )
    assert env.get("API_KEY") == "tip-key"
    assert env.get("OPENAI_API_KEY") == "tip-key"
    assert env.get("MODEL_NAME") == "gpt-test"
    assert env.get("OPENAI_API_KEY") != "bare-stale-key"


def test_tenant_isolation_notify_idle_does_not_stop_other_slot():
    _apply_creds(service_id="svc-a", agent_id="agent-a")
    _apply_creds(service_id="svc-b", agent_id="agent-b")
    slot_a = _seed_alive_slot(
        service_id="svc-a",
        agent_id="agent-a",
        url="http://127.0.0.1:9001/mcp",
    )
    slot_b = _seed_alive_slot(
        service_id="svc-b",
        agent_id="agent-b",
        url="http://127.0.0.1:9002/mcp",
    )
    proc_b = slot_b.process

    apply_env_overrides_to_active(
        {"API_KEY": "k2"},
        service_id="svc-a",
        agent_id="agent-a",
    )

    bt.notify_browser_runtime_after_reload(
        idle=True,
        service_id="svc-a",
        agent_id="agent-a",
    )

    assert slot_a.env_hash not in bt.list_browser_runtime_env_hashes(
        service_id="svc-a", agent_id="agent-a"
    )
    assert bt._slot_process_alive(slot_b) is True
    assert slot_b.process is proc_b
