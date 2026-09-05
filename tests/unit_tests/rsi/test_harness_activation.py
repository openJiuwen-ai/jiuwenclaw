# -*- coding: utf-8 -*-
"""RSI published Harness refs and activation persistence tests."""

from pathlib import Path

import pytest
import yaml

from jiuwenswarm.agents.harness.common.rsi.harness_activation import (
    RsiHarnessInstaller,
    RsiHarnessActivationStore,
    hash_harness_package,
    parse_published_harness_refs,
)
from jiuwenswarm.agents.harness.common.rsi.errors import (
    RsiHarnessInstallFailed,
    RsiHarnessInvalid,
)
from jiuwenswarm.agents.harness.common.rsi.models import RsiTask, TaskStatus, utcnow_iso
from jiuwenswarm.agents.harness.common.rsi import build_rsi_service_context


def _write_package(root: Path, name: str = "policy_harness") -> Path:
    package = root / "member_optimizations" / "current_harnesses" / name
    package.mkdir(parents=True)
    (package / "harness_config.yaml").write_text(
        "extension_name: validation_harness\nschema_version: expert_harness.v1\n",
        encoding="utf-8",
    )
    (package / "prompt.md").write_text("hello\n", encoding="utf-8")
    return package


def test_published_refs_resolve_relative_to_refs_parent(tmp_path):
    package = _write_package(tmp_path)
    refs = package.parent.parent / "current_harness_refs.yaml"
    refs.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "harness_refs": {"policy_harness": "current_harnesses/policy_harness"},
                "roles": [
                    {
                        "role": "policy_harness",
                        "member_name": "policy_harness",
                        "description": "published policy harness",
                        "harness_ref_path": "current_harnesses/policy_harness",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    parsed = parse_published_harness_refs(refs, task_run_root=tmp_path)

    assert parsed.role == "policy_harness"
    assert parsed.package_path == package.resolve()
    assert parsed.metadata["member_name"] == "policy_harness"
    assert parsed.refs_payload["version"] == 1


def test_published_refs_reject_path_outside_task_run(tmp_path):
    outside = tmp_path.parent / "outside-harness"
    outside.mkdir()
    (outside / "harness_config.yaml").write_text("extension_name: outside\n", encoding="utf-8")
    refs = tmp_path / "run" / "current_harness_refs.yaml"
    refs.parent.mkdir()
    refs.write_text(
        yaml.safe_dump({"harness_refs": {"policy": "../../outside-harness"}}),
        encoding="utf-8",
    )

    with pytest.raises(RsiHarnessInvalid, match="任务 run"):
        parse_published_harness_refs(refs, task_run_root=tmp_path / "run")


def test_published_refs_require_single_role_and_manifest(tmp_path):
    first = _write_package(tmp_path, "first")
    _write_package(tmp_path, "second")
    refs = tmp_path / "member_optimizations" / "current_harness_refs.yaml"
    refs.write_text(
        yaml.safe_dump(
            {
                "harness_refs": {
                    "first": "current_harnesses/first",
                    "second": "current_harnesses/second",
                }
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RsiHarnessInvalid, match="恰好一个"):
        parse_published_harness_refs(refs, task_run_root=tmp_path)

    (first / "harness_config.yaml").unlink()
    refs.write_text(
        yaml.safe_dump({"harness_refs": {"first": "current_harnesses/first"}}),
        encoding="utf-8",
    )
    with pytest.raises(RsiHarnessInvalid, match="配置文件"):
        parse_published_harness_refs(refs, task_run_root=tmp_path)


def test_package_hash_is_deterministic_and_content_sensitive(tmp_path):
    package = _write_package(tmp_path)
    first = hash_harness_package(package)
    (package / "prompt.md").write_text("changed\n", encoding="utf-8")
    second = hash_harness_package(package)

    assert len(first) == 64
    assert first != second


def test_activation_store_writes_active_pointer_and_survives_new_instance(tmp_path):
    root = tmp_path / "rsi" / "harness"
    store = RsiHarnessActivationStore(root)
    runtime_path = root / "versions" / "install-a" / "validation_harness"
    runtime_path.mkdir(parents=True)
    record = {
        "installation_id": "install-a",
        "runtime_path": str(runtime_path),
        "sha256": "a" * 64,
    }

    store.commit(record)

    assert store.get_active()["installation_id"] == "install-a"
    assert RsiHarnessActivationStore(root).get_active()["installation_id"] == "install-a"
    assert RsiHarnessActivationStore(root).list_history() == []
    assert not list(root.glob(".activation.*.tmp"))


def test_activation_store_rejects_runtime_path_outside_root(tmp_path):
    store = RsiHarnessActivationStore(tmp_path / "rsi" / "harness")

    with pytest.raises(ValueError, match="root"):
        store.commit(
            {
                "installation_id": "bad",
                "runtime_path": str(tmp_path / "outside"),
                "sha256": "b" * 64,
            }
        )


def _completed_harness_task(context, task_id: str) -> RsiTask:
    run_dir = context.tasks_root / task_id / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    return RsiTask(
        task_id=task_id,
        name="rsi install test",
        scenario="HARNESS",
        status=TaskStatus.COMPLETED.value,
        created_at=utcnow_iso(),
        input_file=str(run_dir / "input.json"),
        model_refs={"optimizer": "optimizer", "tester": "tester"},
        config={},
        run_dir=str(run_dir),
    )


def _write_published_state(context, task_id: str, *, extension_name: str) -> Path:
    run_dir = Path(context.store.get(task_id).run_dir)
    package = run_dir / "member_optimizations" / "current_harnesses" / "policy_harness"
    package.mkdir(parents=True, exist_ok=True)
    (package / "harness_config.yaml").write_text(
        f"extension_name: {extension_name}\nschema_version: expert_harness.v1\n",
        encoding="utf-8",
    )
    (package / "prompt.md").write_text("optimized\n", encoding="utf-8")
    refs = package.parent.parent / "current_harness_refs.yaml"
    refs.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "harness_refs": {"policy_harness": "current_harnesses/policy_harness"},
                "roles": [
                    {
                        "role": "policy_harness",
                        "member_name": "policy_harness",
                        "description": "published",
                        "harness_ref_path": "current_harnesses/policy_harness",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (run_dir / "single_harness_state.yaml").write_text(
        yaml.safe_dump(
            {
                "publication_status": "published",
                "published_harness_refs_path": str(refs),
                "final_node_id": "candidate-1",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return refs


@pytest.mark.asyncio
async def test_installer_publishes_and_is_idempotent(tmp_path):
    tasks_root = tmp_path / "rsi" / "tasks"
    context = build_rsi_service_context(tasks_root, enable_harness_materialization=False)
    task_id = "rsi-install-01"
    context.store.create(_completed_harness_task(context, task_id))
    _write_published_state(context, task_id, extension_name="validation_harness")

    class FakeRsiAgentManager:
        def __init__(self):
            self.calls = []

        async def broadcast_rsi_harness_change(self, old_installation, new_installation):
            self.calls.append((old_installation, new_installation))
            return {"attempted": 1, "succeeded": 1, "failed": []}

    manager = FakeRsiAgentManager()
    installer = RsiHarnessInstaller(
        context.store,
        context.adapter_for_task,
        manager,
        activation_root=tmp_path / "rsi" / "harness",
    )

    first = await installer.install(task_id)
    second = await installer.install(task_id)

    assert first["status"] == "ACTIVE"
    assert first["already_active"] is False
    assert second["already_active"] is True
    assert len(manager.calls) == 1
    assert Path(context.harness_activation_store.get_active()["runtime_path"]).name == "validation_harness"
    assert context.store.get(task_id).config["rsi_installation"]["installation_id"] == first["installation_id"]


@pytest.mark.asyncio
async def test_installer_rolls_back_live_and_pointer_when_provenance_write_fails(tmp_path):
    tasks_root = tmp_path / "rsi" / "tasks"
    context = build_rsi_service_context(tasks_root, enable_harness_materialization=False)
    task_id = "rsi-install-provenance-failure"
    context.store.create(_completed_harness_task(context, task_id))
    _write_published_state(context, task_id, extension_name="validation_harness")

    class FakeRsiAgentManager:
        def __init__(self):
            self.calls = []

        async def broadcast_rsi_harness_change(self, old_installation, new_installation):
            self.calls.append((old_installation, new_installation))
            return {"attempted": 1, "succeeded": 1, "failed": []}

    def fail_provenance(*_args, **_kwargs):
        raise OSError("task.json is temporarily read-only")

    context.store.merge_config = fail_provenance
    manager = FakeRsiAgentManager()
    activation_root = tmp_path / "rsi" / "harness"
    installer = RsiHarnessInstaller(
        context.store,
        context.adapter_for_task,
        manager,
        activation_root=activation_root,
    )

    with pytest.raises(RsiHarnessInstallFailed, match="provenance"):
        await installer.install(task_id)

    assert context.harness_activation_store.get_active() is None
    assert len(manager.calls) == 2
    assert manager.calls[1][0]["installation_id"] == manager.calls[0][1]["installation_id"]
    assert manager.calls[1][1] is None
    assert not list((activation_root / "versions").glob("**/validation_harness"))
