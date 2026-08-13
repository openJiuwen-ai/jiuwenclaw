from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
import yaml

from openjiuwen.core.proactive_context import PCS

from jiuwenswarm.server.proactive_context import host_api as host_module
from jiuwenswarm.server.proactive_context.host_api import PCSHostAPI


HOST_API_PATH = (
    Path(__file__).parents[3]
    / "jiuwenswarm"
    / "server"
    / "proactive_context"
    / "host_api.py"
)


def _config(
    *,
    enabled: bool = True,
    fetching_enabled: bool = True,
    root_dir: Path | None = None,
    interval: float = 60.0,
) -> dict[str, object]:
    root = root_dir or Path.cwd()
    return {
        "enabled": enabled,
        "fetching_enabled": fetching_enabled,
        "strategy_profile": "rules",
        "fetch_services": [
            {
                "service_id": "local-notes",
                "provider": "local_files",
                "enabled": True,
                "interval_seconds": interval,
                "source": {"root_dir": str(root)},
                "credentials": {},
            }
        ],
    }


class _FakeStatus:
    state = "RUNNING"

    def model_dump(self, *, mode: str) -> dict[str, object]:
        assert mode == "json"
        return {"state": self.state, "configured": True}


class FakeCore:
    Config = None

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.snapshot_result: object = object()
        self.deactivate_error: BaseException | None = None
        self.set_error: BaseException | None = None
        self.activate_error: BaseException | None = None
        self.deactivate_started: asyncio.Event | None = None
        self.deactivate_release: asyncio.Event | None = None

    async def set_configuration(self, config: object) -> None:
        self.calls.append(("set_configuration", config))
        if self.set_error is not None:
            error = self.set_error
            self.set_error = None
            raise error

    async def activate_runtime(self) -> None:
        self.calls.append(("activate_runtime", None))
        if self.activate_error is not None:
            error = self.activate_error
            self.activate_error = None
            raise error

    async def deactivate_runtime(self, *, timeout_seconds: float = 30.0) -> None:
        self.calls.append(("deactivate_runtime", timeout_seconds))
        if self.deactivate_started is not None:
            self.deactivate_started.set()
        if self.deactivate_release is not None:
            await self.deactivate_release.wait()
        if self.deactivate_error is not None:
            error = self.deactivate_error
            self.deactivate_error = None
            raise error

    async def snapshot(self) -> object:
        self.calls.append(("snapshot", None))
        return self.snapshot_result

    async def authorize_provider(self, provider: str) -> dict[str, object]:
        self.calls.append(("authorize_provider", provider))
        return {
            "provider": provider,
            "state": "ready",
            "verification_url": None,
            "expires_at": None,
        }

    async def run_fetch(
        self,
        *,
        service_id: str | None = None,
    ) -> dict[str, object]:
        self.calls.append(("run_fetch", service_id))
        return {
            "state": "accepted",
            "service_ids": [service_id or "local-notes"],
        }

    async def get_graph(self) -> dict[str, object]:
        self.calls.append(("get_graph", None))
        return {"context_ready": True, "nodes": [], "edges": []}

    async def search_graph(self, query: str) -> dict[str, object]:
        self.calls.append(("search_graph", query))
        return {
            "results": [
                {
                    "node_id": "page:topics/pcs.md",
                    "title": "主动上下文",
                    "path": "topics/pcs.md",
                    "snippet": "主动上下文",
                }
            ]
        }

    async def get_graph_page(self, node_id: str) -> dict[str, object]:
        self.calls.append(("get_graph_page", node_id))
        return {
            "node_id": node_id,
            "title": "主动上下文",
            "path": "topics/pcs.md",
            "markdown": "# 主动上下文\n",
        }


@pytest.fixture
def fake_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[PCSHostAPI, FakeCore]:
    host = PCSHostAPI(home=tmp_path / "pcs")
    fake = FakeCore()
    monkeypatch.setattr(host, "_pcs", fake)
    return host, fake


def test_host_module_imports_only_pcs_from_core() -> None:
    tree = ast.parse(HOST_API_PATH.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == "openjiuwen.core.proactive_context"
        ):
            imported.extend(alias.name for alias in node.names)
    assert imported == ["PCS"]


def test_constructor_does_not_read_or_write_yaml(tmp_path: Path) -> None:
    home = tmp_path / "pcs"
    host = PCSHostAPI(home=home)
    assert not home.exists()
    assert not (home / "pcs.yaml").exists()
    assert host._config is None


@pytest.mark.asyncio
async def test_start_without_yaml_keeps_host_unconfigured(tmp_path: Path) -> None:
    host = PCSHostAPI(home=tmp_path / "pcs")
    await host.start()
    status = await host.get_status()
    assert status.configured is False
    assert status.state == "CREATED"


@pytest.mark.asyncio
async def test_configure_writes_yaml_and_starts_enabled_core(
    fake_host: tuple[PCSHostAPI, FakeCore], tmp_path: Path
) -> None:
    host, core = fake_host
    await host.configure(_config(root_dir=tmp_path))
    assert (host._home / "pcs.yaml").is_file()
    saved = yaml.safe_load((host._home / "pcs.yaml").read_text(encoding="utf-8"))
    assert saved["enabled"] is True
    assert [name for name, _ in core.calls] == ["set_configuration", "activate_runtime"]


@pytest.mark.asyncio
async def test_authorize_provider_delegates_to_configured_core(
    fake_host: tuple[PCSHostAPI, FakeCore], tmp_path: Path
) -> None:
    host, core = fake_host
    await host.configure(_config(enabled=False, root_dir=tmp_path))
    core.calls.clear()

    result = await host.authorize_provider("feishu")

    assert result["state"] == "ready"
    assert core.calls == [("authorize_provider", "feishu")]


@pytest.mark.asyncio
async def test_get_graph_delegates_to_core(fake_host: tuple[PCSHostAPI, FakeCore]) -> None:
    host, core = fake_host

    result = await host.get_graph()

    assert result == {"context_ready": True, "nodes": [], "edges": []}
    assert core.calls == [("get_graph", None)]


@pytest.mark.asyncio
async def test_search_graph_delegates_to_core(fake_host: tuple[PCSHostAPI, FakeCore]) -> None:
    host, core = fake_host

    result = await host.search_graph("主动上下文")

    assert result["results"][0]["node_id"] == "page:topics/pcs.md"
    assert core.calls == [("search_graph", "主动上下文")]


@pytest.mark.asyncio
async def test_get_graph_page_delegates_to_core(fake_host: tuple[PCSHostAPI, FakeCore]) -> None:
    host, core = fake_host

    result = await host.get_graph_page("page:topics/pcs.md")

    assert result["markdown"] == "# 主动上下文\n"
    assert core.calls == [("get_graph_page", "page:topics/pcs.md")]


@pytest.mark.asyncio
async def test_configure_same_semantics_only_replaces_yaml(
    fake_host: tuple[PCSHostAPI, FakeCore], tmp_path: Path
) -> None:
    host, core = fake_host
    config = _config(root_dir=tmp_path)
    await host.configure(config)
    core.calls.clear()
    await host.configure(config)
    assert [name for name, _ in core.calls] == []


@pytest.mark.asyncio
async def test_configure_changed_semantics_stops_sets_and_restarts(
    fake_host: tuple[PCSHostAPI, FakeCore], tmp_path: Path
) -> None:
    host, core = fake_host
    await host.configure(_config(root_dir=tmp_path, interval=60.0))
    core.calls.clear()
    await host.configure(_config(root_dir=tmp_path, interval=120.0))
    assert [name for name, _ in core.calls] == [
        "snapshot",
        "deactivate_runtime",
        "set_configuration",
        "activate_runtime",
    ]


@pytest.mark.asyncio
async def test_disabled_configuration_does_not_activate_or_delete_context(
    fake_host: tuple[PCSHostAPI, FakeCore], tmp_path: Path
) -> None:
    host, core = fake_host
    context = host._home / "workspace" / "context"
    context.mkdir(parents=True)
    description = context / "description.md"
    description.write_text("keep", encoding="utf-8")
    await host.configure(_config(enabled=False, root_dir=tmp_path))
    assert description.read_text(encoding="utf-8") == "keep"
    assert [name for name, _ in core.calls] == ["set_configuration"]


@pytest.mark.asyncio
async def test_start_loads_existing_yaml_only_once(tmp_path: Path) -> None:
    home = tmp_path / "pcs"
    home.mkdir()
    config = _config(enabled=False, root_dir=tmp_path)
    (home / "pcs.yaml").write_text(
        yaml.safe_dump(config, sort_keys=False), encoding="utf-8"
    )
    host = PCSHostAPI(home=home)
    await host.start()
    assert host._config is not None
    (home / "pcs.yaml").write_text("not: a PCS config", encoding="utf-8")
    await host.start()
    assert host._config is not None


@pytest.mark.asyncio
async def test_get_status_does_not_wait_for_operation_lock(
    fake_host: tuple[PCSHostAPI, FakeCore],
) -> None:
    host, core = fake_host
    core.snapshot_result = "status"
    await host._operation_lock.acquire()
    try:
        assert await asyncio.wait_for(host.get_status(), timeout=0.1) == "status"
    finally:
        host._operation_lock.release()


@pytest.mark.asyncio
async def test_get_overview_returns_full_config_including_credentials(
    fake_host: tuple[PCSHostAPI, FakeCore],
    tmp_path: Path,
) -> None:
    host, core = fake_host
    config = _config(enabled=False, root_dir=tmp_path)
    service = cast(list[dict[str, object]], config["fetch_services"])[0]
    service["provider"] = "github"
    service["source"] = {
        "owner": "openjiuwen",
        "repo": "agent-core",
        "resources": ["readme", "issues", "pull_requests", "commits", "code"],
    }
    service["credentials"] = {"token": "plain-token"}
    core.snapshot_result = _FakeStatus()

    await host.configure(config)
    overview = await host.get_overview()

    assert overview["configured"] is True
    assert overview["config"]["fetch_services"][0]["credentials"] == {
        "token": "plain-token"
    }
    assert overview["status"] == {"state": "RUNNING", "configured": True}


@pytest.mark.asyncio
async def test_patch_runtime_configuration_changes_only_strategy_profile(
    fake_host: tuple[PCSHostAPI, FakeCore],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        host_module,
        "get_default_models",
        lambda: [
            {
                "model_client_config": {
                    "client_provider": "OpenAI",
                    "api_key": "key",
                    "api_base": "https://example.invalid/v1",
                    "model_name": "model-a",
                },
                "model_config_obj": {"temperature": 0.2},
            }
        ],
    )
    host, _core = fake_host
    await host.configure(_config(enabled=False, root_dir=tmp_path))
    await host.select_model(0)
    before = await host.get_runtime_config()

    after = await host.patch_runtime_config({"strategy_profile": "balanced"})

    assert after["strategy_profile"] == "balanced"
    assert after["fetch_services"] == before["fetch_services"]
    assert after["model_origin_index"] == 0


@pytest.mark.asyncio
async def test_patch_runtime_configuration_rejects_unknown_field_without_writing(
    fake_host: tuple[PCSHostAPI, FakeCore],
    tmp_path: Path,
) -> None:
    host, _core = fake_host
    await host.configure(_config(enabled=False, root_dir=tmp_path))
    before = host._config_path.read_bytes()

    with pytest.raises(PCS.Error):
        await host.patch_runtime_config({"enabled": True})

    assert host._config_path.read_bytes() == before


@pytest.mark.asyncio
async def test_select_model_persists_only_origin_index(
    fake_host: tuple[PCSHostAPI, FakeCore],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        host_module,
        "get_default_models",
        lambda: [
            {
                "model_client_config": {
                    "client_provider": "OpenAI",
                    "api_key": "key",
                    "api_base": "https://example.invalid/v1",
                    "model_name": "model-a",
                },
                "model_config_obj": {"temperature": 0.2},
            }
        ],
    )
    host, core = fake_host
    await host.configure(_config(enabled=False, root_dir=tmp_path))
    core.calls.clear()

    result = await host.select_model(0)

    saved = yaml.safe_load(host._config_path.read_text(encoding="utf-8"))
    applied = next(
        value for name, value in reversed(core.calls) if name == "set_configuration"
    )
    assert result["model_origin_index"] == 0
    assert saved["model_origin_index"] == 0
    assert "model_client" not in saved
    assert "model_request" not in saved
    assert applied.model_request.model_name == "model-a"


@pytest.mark.asyncio
async def test_select_model_rejects_missing_model_without_writing(
    fake_host: tuple[PCSHostAPI, FakeCore],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(host_module, "get_default_models", lambda: [])
    host, _core = fake_host
    await host.configure(_config(enabled=False, root_dir=tmp_path))
    before = host._config_path.read_bytes()

    with pytest.raises(PCS.Error):
        await host.select_model(0)

    assert host._config_path.read_bytes() == before


@pytest.mark.asyncio
async def test_runtime_model_strategy_requires_selected_model_without_writing(
    fake_host: tuple[PCSHostAPI, FakeCore],
    tmp_path: Path,
) -> None:
    host, _core = fake_host
    await host.configure(_config(enabled=False, root_dir=tmp_path))
    before = host._config_path.read_bytes()

    with pytest.raises(PCS.Error):
        await host.patch_runtime_config({"strategy_profile": "agent"})

    assert host._config_path.read_bytes() == before


@pytest.mark.asyncio
async def test_start_rejects_saved_model_index_when_model_was_removed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "pcs"
    home.mkdir()
    stored = _config(enabled=True, root_dir=tmp_path)
    stored["strategy_profile"] = "balanced"
    stored["model_origin_index"] = 0
    (home / "pcs.yaml").write_text(
        yaml.safe_dump(stored, sort_keys=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(host_module, "get_default_models", lambda: [])
    host = PCSHostAPI(home=home)

    with pytest.raises(PCS.Error):
        await host.start()

    assert host._config is None
    assert host._stored_config is None


@pytest.mark.asyncio
async def test_set_runtime_enabled_persists_and_applies_switch(
    fake_host: tuple[PCSHostAPI, FakeCore],
    tmp_path: Path,
) -> None:
    host, core = fake_host
    core.snapshot_result = _FakeStatus()
    await host.configure(_config(enabled=False, root_dir=tmp_path))
    core.calls.clear()

    started = await host.set_runtime_enabled(True)
    stopped = await host.set_runtime_enabled(False)

    assert started["enabled"] is True
    assert stopped["enabled"] is False
    saved = yaml.safe_load(host._config_path.read_text(encoding="utf-8"))
    assert saved["enabled"] is False
    assert [name for name, _ in core.calls].count("activate_runtime") == 1


@pytest.mark.asyncio
async def test_runtime_start_failure_rolls_back_file_and_memory(
    fake_host: tuple[PCSHostAPI, FakeCore],
    tmp_path: Path,
) -> None:
    host, core = fake_host
    core.snapshot_result = _FakeStatus()
    await host.configure(_config(enabled=False, root_dir=tmp_path))
    old_yaml = host._config_path.read_bytes()
    old_config = host._config
    old_stored = host._stored_config
    core.activate_error = RuntimeError("start failed")

    with pytest.raises(PCS.Error):
        await host.set_runtime_enabled(True)

    assert host._config == old_config
    assert host._stored_config == old_stored
    assert host._config_path.read_bytes() == old_yaml


@pytest.mark.asyncio
async def test_runtime_operations_reject_unconfigured_host(
    fake_host: tuple[PCSHostAPI, FakeCore],
) -> None:
    host, _core = fake_host

    with pytest.raises(PCS.Error):
        await host.get_runtime_config()
    with pytest.raises(PCS.Error):
        await host.patch_runtime_config({"strategy_profile": "rules"})
    with pytest.raises(PCS.Error):
        await host.select_model(0)
    with pytest.raises(PCS.Error):
        await host.set_runtime_enabled(True)


@pytest.mark.asyncio
async def test_list_fetch_services_combines_config_state_and_error(
    fake_host: tuple[PCSHostAPI, FakeCore],
    tmp_path: Path,
) -> None:
    host, core = fake_host
    await host.configure(_config(enabled=False, root_dir=tmp_path))
    core.snapshot_result = SimpleNamespace(
        fetch_service_states={"local-notes": "RUNNING"},
        fetch_service_errors={"local-notes": "last failure"},
    )

    services = await host.list_fetch_services()

    assert services == [
        {
            "service_id": "local-notes",
            "provider": "local_files",
            "enabled": True,
            "interval_seconds": 60.0,
            "max_items_per_run": None,
            "source": {"root_dir": str(tmp_path)},
            "credentials": {},
            "state": "RUNNING",
            "last_error": "last failure",
        }
    ]


@pytest.mark.asyncio
async def test_patch_existing_fetch_service_without_changing_identity(
    fake_host: tuple[PCSHostAPI, FakeCore],
    tmp_path: Path,
) -> None:
    host, _core = fake_host
    await host.configure(_config(enabled=False, root_dir=tmp_path))

    result = await host.patch_fetch_service(
        "local-notes",
        {"interval_seconds": 10_800.0, "max_items_per_run": 50},
    )

    assert result["service_id"] == "local-notes"
    assert result["provider"] == "local_files"
    assert result["enabled"] is True
    assert result["interval_seconds"] == 10_800.0
    assert result["max_items_per_run"] == 50


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["service_id", "provider", "enabled"])
async def test_patch_fetch_service_rejects_identity_and_switch_fields(
    fake_host: tuple[PCSHostAPI, FakeCore],
    tmp_path: Path,
    field: str,
) -> None:
    host, _core = fake_host
    await host.configure(_config(enabled=False, root_dir=tmp_path))
    before = host._config_path.read_bytes()

    with pytest.raises(PCS.Error):
        await host.patch_fetch_service("local-notes", {field: "changed"})

    assert host._config_path.read_bytes() == before


@pytest.mark.asyncio
async def test_patch_fetch_service_never_creates_missing_service(
    fake_host: tuple[PCSHostAPI, FakeCore],
    tmp_path: Path,
) -> None:
    host, _core = fake_host
    await host.configure(_config(enabled=False, root_dir=tmp_path))
    before = host._config_path.read_bytes()

    with pytest.raises(PCS.Error, match="unknown PCS fetch service"):
        await host.patch_fetch_service(
            "new-service",
            {"interval_seconds": 10_800.0},
        )

    assert host._config_path.read_bytes() == before


@pytest.mark.asyncio
async def test_get_fetch_run_status_returns_all_or_one_service(
    fake_host: tuple[PCSHostAPI, FakeCore],
    tmp_path: Path,
) -> None:
    host, core = fake_host
    await host.configure(_config(enabled=False, root_dir=tmp_path))
    core.snapshot_result = SimpleNamespace(
        fetch_service_states={"local-notes": "RUNNING"},
        fetch_service_errors={},
    )

    all_status = await host.get_fetch_run_status()
    one_status = await host.get_fetch_run_status("local-notes")

    assert all_status == {
        "services": [
            {
                "service_id": "local-notes",
                "state": "RUNNING",
                "last_error": None,
            }
        ]
    }
    assert one_status == {
        "service_id": "local-notes",
        "state": "RUNNING",
        "last_error": None,
    }


@pytest.mark.asyncio
async def test_set_fetching_updates_global_switch_through_full_restart(
    fake_host: tuple[PCSHostAPI, FakeCore],
    tmp_path: Path,
) -> None:
    host, core = fake_host
    core.snapshot_result = _FakeStatus()
    await host.configure(_config(root_dir=tmp_path))
    core.calls.clear()

    await host.set_fetching(enabled=False)

    assert host._config.fetching_enabled is False
    saved = yaml.safe_load((host._home / "pcs.yaml").read_text(encoding="utf-8"))
    assert saved["fetching_enabled"] is False
    assert [name for name, _ in core.calls] == [
        "snapshot",
        "deactivate_runtime",
        "set_configuration",
        "activate_runtime",
    ]


@pytest.mark.asyncio
async def test_set_fetching_updates_only_named_service(
    fake_host: tuple[PCSHostAPI, FakeCore],
    tmp_path: Path,
) -> None:
    host, core = fake_host
    core.snapshot_result = _FakeStatus()
    await host.configure(_config(root_dir=tmp_path))

    await host.set_fetching(enabled=False, service_id="local-notes")

    assert host._config.fetching_enabled is True
    assert host._config.fetch_services[0].enabled is False


@pytest.mark.asyncio
async def test_set_fetching_rejects_unconfigured_or_unknown_service(
    fake_host: tuple[PCSHostAPI, FakeCore],
) -> None:
    host, _core = fake_host
    with pytest.raises(PCS.Error):
        await host.set_fetching(enabled=False)
    with pytest.raises(PCS.Error):
        await host.set_fetching(enabled=False, service_id="missing")


@pytest.mark.asyncio
async def test_run_fetch_delegates_without_modifying_yaml(
    fake_host: tuple[PCSHostAPI, FakeCore],
    tmp_path: Path,
) -> None:
    host, core = fake_host
    await host.configure(_config(enabled=False, root_dir=tmp_path))
    config_path = host._home / "pcs.yaml"
    saved = config_path.read_bytes()
    configured = host._config
    core.calls.clear()

    result = await host.run_fetch(service_id="local-notes")

    assert result == {
        "state": "accepted",
        "service_ids": ["local-notes"],
    }
    assert core.calls == [("run_fetch", "local-notes")]
    assert host._config is configured
    assert config_path.read_bytes() == saved


@pytest.mark.asyncio
async def test_run_fetch_is_serialized_with_configuration_operations(
    fake_host: tuple[PCSHostAPI, FakeCore],
    tmp_path: Path,
) -> None:
    host, core = fake_host
    await host.configure(_config(enabled=False, root_dir=tmp_path))
    core.calls.clear()
    await host._operation_lock.acquire()

    task = asyncio.create_task(host.run_fetch())
    await asyncio.sleep(0)
    assert core.calls == []

    host._operation_lock.release()
    result = await task
    assert result == {
        "state": "accepted",
        "service_ids": ["local-notes"],
    }
    assert core.calls == [("run_fetch", None)]


@pytest.mark.asyncio
async def test_stop_calls_core_and_preserves_configuration(
    fake_host: tuple[PCSHostAPI, FakeCore], tmp_path: Path
) -> None:
    host, core = fake_host
    await host.configure(_config(enabled=False, root_dir=tmp_path))
    core.calls.clear()
    await host.stop(timeout_seconds=1.5)
    assert core.calls == [("deactivate_runtime", 1.5)]
    assert host._config is not None
    assert (host._home / "pcs.yaml").is_file()


@pytest.mark.asyncio
async def test_temporary_yaml_write_failure_does_not_call_core(
    fake_host: tuple[PCSHostAPI, FakeCore],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host, core = fake_host

    def fail_temporary(*_args: object, **_kwargs: object) -> None:
        raise OSError("temporary write failed")

    monkeypatch.setattr(
        "jiuwenswarm.server.proactive_context.host_api.tempfile.NamedTemporaryFile",
        fail_temporary,
    )
    with pytest.raises(Exception) as caught:
        await host.configure(_config(enabled=False, root_dir=tmp_path))
    assert core.calls == []
    assert caught.value.status.name == "CONTEXT_PROACTIVE_FILE_EXECUTION_ERROR"
    assert not (host._home / "pcs.yaml").exists()


@pytest.mark.asyncio
async def test_previous_stop_failure_preserves_old_configuration(
    fake_host: tuple[PCSHostAPI, FakeCore], tmp_path: Path
) -> None:
    host, core = fake_host
    old_config = _config(enabled=True, root_dir=tmp_path)
    await host.configure(old_config)
    old_core_config = host._config
    old_stored_config = host._stored_config
    old_yaml = (host._home / "pcs.yaml").read_bytes()
    core.calls.clear()
    core.snapshot_result = SimpleNamespace(state="RUNNING")
    core.deactivate_error = RuntimeError("stop failed")
    with pytest.raises(Exception) as caught:
        await host.configure(_config(enabled=True, root_dir=tmp_path, interval=120.0))
    assert [name for name, _ in core.calls] == ["snapshot", "deactivate_runtime"]
    assert host._config == old_core_config
    assert host._stored_config == old_stored_config
    assert (host._home / "pcs.yaml").read_bytes() == old_yaml
    assert caught.value.status.name == "CONTEXT_PROACTIVE_STATE_INVALID"


@pytest.mark.asyncio
async def test_set_failure_restores_old_configuration_and_active_runtime(
    fake_host: tuple[PCSHostAPI, FakeCore], tmp_path: Path
) -> None:
    host, core = fake_host
    old_config = _config(enabled=True, root_dir=tmp_path)
    await host.configure(old_config)
    old_core_config = host._config
    old_stored_config = host._stored_config
    old_yaml = (host._home / "pcs.yaml").read_bytes()
    core.calls.clear()
    core.snapshot_result = SimpleNamespace(state="RUNNING")
    core.set_error = RuntimeError("set failed")
    with pytest.raises(Exception) as caught:
        await host.configure(_config(enabled=True, root_dir=tmp_path, interval=120.0))
    assert [name for name, _ in core.calls] == [
        "snapshot",
        "deactivate_runtime",
        "set_configuration",
        "deactivate_runtime",
        "set_configuration",
        "activate_runtime",
    ]
    assert host._config == old_core_config
    assert host._stored_config == old_stored_config
    assert (host._home / "pcs.yaml").read_bytes() == old_yaml
    assert caught.value.status.name == "CONTEXT_PROACTIVE_CONFIG_INVALID"


@pytest.mark.asyncio
async def test_replace_failure_restores_old_configuration_and_active_runtime(
    fake_host: tuple[PCSHostAPI, FakeCore],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host, core = fake_host
    old_config = _config(enabled=True, root_dir=tmp_path)
    await host.configure(old_config)
    old_core_config = host._config
    old_stored_config = host._stored_config
    old_yaml = (host._home / "pcs.yaml").read_bytes()
    core.calls.clear()
    core.snapshot_result = SimpleNamespace(state="RUNNING")

    def fail_replace(_source: object, _target: object) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(
        "jiuwenswarm.server.proactive_context.host_api.os.replace", fail_replace
    )
    with pytest.raises(Exception) as caught:
        await host.configure(_config(enabled=True, root_dir=tmp_path, interval=120.0))
    assert [name for name, _ in core.calls] == [
        "snapshot",
        "deactivate_runtime",
        "set_configuration",
        "activate_runtime",
        "deactivate_runtime",
        "set_configuration",
        "activate_runtime",
    ]
    assert host._config == old_core_config
    assert host._stored_config == old_stored_config
    assert (host._home / "pcs.yaml").read_bytes() == old_yaml
    assert caught.value.status.name == "CONTEXT_PROACTIVE_FILE_EXECUTION_ERROR"


@pytest.mark.asyncio
async def test_new_start_failure_keeps_host_unconfigured(
    fake_host: tuple[PCSHostAPI, FakeCore], tmp_path: Path
) -> None:
    host, core = fake_host
    core.activate_error = RuntimeError("start failed")
    with pytest.raises(Exception) as caught:
        await host.configure(_config(root_dir=tmp_path))
    assert host._config is None
    assert host._stored_config is None
    assert not (host._home / "pcs.yaml").exists()
    assert caught.value.status.name == "CONTEXT_PROACTIVE_STATE_INVALID"


@pytest.mark.asyncio
async def test_stop_rejects_non_positive_timeout_without_calling_core(
    fake_host: tuple[PCSHostAPI, FakeCore],
) -> None:
    host, core = fake_host
    with pytest.raises(Exception) as caught:
        await host.stop(timeout_seconds=0)
    assert core.calls == []
    assert caught.value.status.name == "CONTEXT_PROACTIVE_RUNTIME_TIMEOUT"


@pytest.mark.asyncio
async def test_concurrent_operations_are_serialized(
    fake_host: tuple[PCSHostAPI, FakeCore], tmp_path: Path
) -> None:
    host, core = fake_host
    started = asyncio.Event()
    release = asyncio.Event()
    core.deactivate_started = started
    core.deactivate_release = release
    await host.configure(_config(enabled=False, root_dir=tmp_path))
    task = asyncio.create_task(host.stop())
    await started.wait()
    configure_task = asyncio.create_task(
        host.configure(_config(enabled=False, root_dir=tmp_path, interval=120.0))
    )
    await asyncio.sleep(0)
    assert not configure_task.done()
    release.set()
    await task
    await configure_task
    assert [name for name, _ in core.calls].count("deactivate_runtime") == 2
