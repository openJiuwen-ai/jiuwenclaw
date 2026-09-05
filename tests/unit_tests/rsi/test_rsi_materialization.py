"""Tests for the production Harness task materialization boundary."""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from jiuwenswarm.agents.harness.common.rsi.errors import (
    RsiDatasetInvalid,
    RsiModelNotFound,
    RsiUnsupportedParameter,
)
from jiuwenswarm.agents.harness.common.rsi import build_rsi_service_context
from jiuwenswarm.agents.harness.common.rsi.harness_provider import HarnessProvider
from jiuwenswarm.agents.harness.common.rsi.materializer import RsiTaskMaterializer
from jiuwenswarm.agents.harness.common.rsi.model_resolver import RsiModelConfigResolver


def _entry(name: str, *, alias: str = "", is_default: bool = False, api_base: str = "https://example.test/v1"):
    return {
        "model_client_config": {
            "model_name": name,
            "client_provider": "OpenAI",
            "api_base": api_base,
            "api_key": f"secret-{name}",
        },
        "model_config_obj": {"temperature": 0.2},
        "alias": alias,
        "is_default": is_default,
    }


class _FakeModelConfig:
    def __init__(self, values: dict):
        self.values = values

    def model_dump(self, **_: object) -> dict:
        return dict(self.values)


def _tree_digest(path: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(
        (candidate for candidate in path.rglob("*") if candidate.is_file()),
        key=lambda candidate: candidate.relative_to(path).as_posix(),
    ):
        relative = item.relative_to(path).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(hashlib.sha256(item.read_bytes()).hexdigest()))
    return digest.hexdigest()


def test_model_resolver_uses_models_list_global_origin_index(tmp_path: Path) -> None:
    entries = [
        _entry("same", alias="first", is_default=True, api_base="https://one.test/v1"),
        _entry("same", alias="second", api_base="https://two.test/v1"),
    ]

    def build_model(mcc: dict, mco: dict) -> SimpleNamespace:
        return SimpleNamespace(
            model_client_config=_FakeModelConfig(mcc),
            model_config=_FakeModelConfig({"model_name": mcc["model_name"], **mco}),
        )

    resolver = RsiModelConfigResolver(
        config_loader=lambda: {},
        defaults_loader=lambda _: entries,
        zen_loader=lambda: [],
        model_builder=build_model,
    )

    manifest = resolver.resolve_to_file("same#1", "tester", tmp_path)
    payload = yaml.safe_load((tmp_path / "tester.yaml").read_text(encoding="utf-8"))

    assert manifest["origin_index"] == 1
    assert manifest["model_name"] == "same"
    assert payload["model_client_config"]["api_base"] == "https://two.test/v1"
    assert payload["model_client_config"]["max_retries"] == 0
    assert payload["model_request_config"]["model"] == "same"
    assert payload["model_client_config"]["api_key"] == "secret-same"


def test_model_resolver_rejects_unknown_reference_without_default_fallback(tmp_path: Path) -> None:
    resolver = RsiModelConfigResolver(
        config_loader=lambda: {},
        defaults_loader=lambda _: [_entry("configured", is_default=True)],
        zen_loader=lambda: [],
        model_builder=lambda mcc, mco: SimpleNamespace(
            model_client_config=_FakeModelConfig(mcc),
            model_config=_FakeModelConfig({"model_name": mcc["model_name"], **mco}),
        ),
    )

    with pytest.raises(RsiModelNotFound):
        resolver.resolve_to_file("missing", "tester", tmp_path)


def test_materializer_copies_dataset_wraps_single_harness_and_writes_validation_profile(tmp_path: Path) -> None:
    source_dataset = tmp_path / "source" / "validation.json"
    source_dataset.parent.mkdir()
    source_dataset.write_text('{"cases": [{"case_id": "a"}]}', encoding="utf-8")
    source_harness = tmp_path / "harness" / "harness_config.yaml"
    source_harness.parent.mkdir()
    source_harness.write_text("name: demo\n", encoding="utf-8")

    materializer = RsiTaskMaterializer(tmp_path / "tasks")
    task_id = "rsi-materialized"
    dataset = materializer.materialize_dataset(task_id, source_dataset)
    refs = materializer.materialize_harness_refs(task_id, source_harness)
    models_dir = tmp_path / "tasks" / task_id / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    model_paths = {
        "evaluation": str(models_dir / "evaluation.yaml"),
        "analysis": str(models_dir / "analysis.yaml"),
        "member_optimization": str(models_dir / "member_optimization.yaml"),
    }
    for model_path in model_paths.values():
        Path(model_path).write_text("model_client_config: {}\n", encoding="utf-8")
    profile = materializer.materialize_validation_profile(
        task_id,
        model_paths,
    )

    dataset_path = Path(dataset["path"])
    refs_path = Path(refs["path"])
    profile_path = Path(profile["path"])
    refs_payload = yaml.safe_load(refs_path.read_text(encoding="utf-8"))
    profile_payload = yaml.safe_load(profile_path.read_text(encoding="utf-8"))

    assert dataset_path.is_file()
    assert dataset_path.read_text(encoding="utf-8") == source_dataset.read_text(encoding="utf-8")
    assert dataset["sha256"] == hashlib.sha256(dataset_path.read_bytes()).hexdigest()
    assert refs_payload["harness_refs"] == {"validation_harness": str(source_harness.resolve())}
    assert profile_payload["max_epochs"] == 1
    assert profile_payload["data_loader"]["batch_size"] == 8
    assert profile_payload["member_optimizer"]["sibling_candidate_count"] == 2
    assert profile_payload["member_optimizer"]["max_issue_attempts_per_batch"] == 8
    assert profile_payload["member_optimizer"]["max_repair_rounds_per_batch"] == 1
    assert profile_payload["evaluation_result_analyzer"]["diagnosis_agent_max_concurrency"] == 5
    assert profile["sha256"] == hashlib.sha256(profile_path.read_bytes()).hexdigest()


def test_materializer_preserves_harness_package_directory_for_engine_checkpoints(tmp_path: Path) -> None:
    package = tmp_path / "harness" / "demo"
    package.mkdir(parents=True)
    config = package / "harness_config.yaml"
    config.write_text("name: demo\n", encoding="utf-8")
    (package / "prompt_sections").mkdir()
    (package / "prompt_sections" / "sections.yaml").write_text("sections: []\n", encoding="utf-8")

    result = RsiTaskMaterializer(tmp_path / "tasks").materialize_harness_refs(
        "rsi-package",
        package,
    )

    refs = yaml.safe_load(Path(result["path"]).read_text(encoding="utf-8"))
    assert refs["harness_refs"]["validation_harness"] == str(package.resolve())
    assert result["source_path"] == str(package.resolve())
    assert result["source_config_path"] == str(config.resolve())
    assert result["source_sha256"] == _tree_digest(package)


def test_task_service_materializes_private_validation_inputs_and_keeps_manifest_non_secret(
    tmp_path: Path,
) -> None:
    source_dataset = tmp_path / "source" / "validation.json"
    source_dataset.parent.mkdir()
    source_dataset.write_text('{"cases": [{"case_id": "a"}]}', encoding="utf-8")
    source_harness = tmp_path / "harness" / "harness_config.yaml"
    source_harness.parent.mkdir()
    source_harness.write_text("name: demo\n", encoding="utf-8")

    def build_model(mcc: dict, mco: dict) -> SimpleNamespace:
        return SimpleNamespace(
            model_client_config=_FakeModelConfig(mcc),
            model_config=_FakeModelConfig({"model_name": mcc["model_name"], **mco}),
        )

    resolver = RsiModelConfigResolver(
        config_loader=lambda: {},
        defaults_loader=lambda _: [_entry("optimizer", is_default=True), _entry("tester")],
        zen_loader=lambda: [],
        model_builder=build_model,
    )
    tasks_root = tmp_path / "tasks"
    context = build_rsi_service_context(
        tasks_root,
        enable_harness_materialization=True,
        harness_materializer=RsiTaskMaterializer(tasks_root),
        model_resolver=resolver,
    )
    context.register_harness_provider(HarnessProvider(tasks_root))
    context.bind_task_service(harness_refs_provider=lambda: str(source_harness))

    result = context.task_service.create(
        {
            "scenario": "HARNESS",
            "name": "validation",
            "dataset_path": str(source_dataset),
            "model_refs": {"optimizer": "optimizer", "tester": "tester"},
        }
    )
    task = context.store.get(result["task_id"])
    task_root = tasks_root / task.task_id
    assert Path(task.input_file).parent == task_root / "input"
    assert Path(task.config["harness_refs_path"]).parent == task_root / "harness"
    assert Path(task.config["orchestrator_config_path"]).parent == task_root / "config"
    assert task.config["dataset_id"] == "single_harness_benchmark"
    assert "api_key" not in (task_root / "task.json").read_text(encoding="utf-8")
    assert (task_root / "models" / "evaluation.yaml").is_file()
    assert (task_root / "models" / "analysis.yaml").is_file()
    assert (task_root / "models" / "member_optimization.yaml").is_file()
    for role, expected_model in {
        "evaluation": "tester",
        "analysis": "optimizer",
        "member_optimization": "optimizer",
    }.items():
        payload = yaml.safe_load((task_root / "models" / f"{role}.yaml").read_text(encoding="utf-8"))
        assert payload["model_request_config"]["model"] == expected_model
    assert task.config["active_ref_released"] is True

    context.store.update_status(
        task.task_id,
        ["CREATED"],
        "TERMINATED",
        cause="test.cleanup",
    )
    context.task_service.delete({"task_id": task.task_id})
    assert not task_root.exists()


def test_task_service_rejects_non_default_validation_search_controls(tmp_path: Path) -> None:
    tasks_root = tmp_path / "tasks"
    materializer = RsiTaskMaterializer(tasks_root)
    context = build_rsi_service_context(
        tasks_root,
        enable_harness_materialization=True,
        harness_materializer=materializer,
        model_resolver=object(),
    )
    with pytest.raises(RsiUnsupportedParameter):
        context.task_service.create(
            {
                "scenario": "HARNESS",
                "name": "validation",
                "input_file": str(tmp_path / "missing.json"),
                "model_refs": {"optimizer": "optimizer", "tester": "optimizer"},
                "max_iterations": 2,
            }
        )


def test_task_service_rejects_invalid_dataset_before_writing_task_materials(tmp_path: Path) -> None:
    source_dataset = tmp_path / "source" / "duplicate.json"
    source_dataset.parent.mkdir()
    source_dataset.write_text(
        '[{"case_id": "same"}, {"case_id": "same"}]',
        encoding="utf-8",
    )
    source_harness = tmp_path / "harness" / "harness_config.yaml"
    source_harness.parent.mkdir()
    source_harness.write_text("name: demo\n", encoding="utf-8")

    resolver = RsiModelConfigResolver(
        config_loader=lambda: {},
        defaults_loader=lambda _: [_entry("optimizer", is_default=True)],
        zen_loader=lambda: [],
        model_builder=lambda mcc, mco: SimpleNamespace(
            model_client_config=_FakeModelConfig(mcc),
            model_config=_FakeModelConfig({"model_name": mcc["model_name"], **mco}),
        ),
    )
    tasks_root = tmp_path / "tasks"
    context = build_rsi_service_context(
        tasks_root,
        enable_harness_materialization=True,
        harness_materializer=RsiTaskMaterializer(tasks_root),
        model_resolver=resolver,
    )
    context.register_harness_provider(HarnessProvider(tasks_root))
    context.bind_task_service(harness_refs_provider=lambda: str(source_harness))

    with pytest.raises(RsiDatasetInvalid):
        context.task_service.create(
            {
                "scenario": "HARNESS",
                "name": "invalid-dataset",
                "input_file": str(source_dataset),
                "model_refs": {"optimizer": "optimizer", "tester": "optimizer"},
            }
        )

    assert not list(tasks_root.glob("rsi-*/task.json"))
