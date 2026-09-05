"""生产 HarnessProvider 单元测试（设计 §2.1/§2.2 归一化与校验闭环）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from jiuwenswarm.agents.harness.common.rsi.errors import (
    RsiNotReady,
    RsiPathNotAllowed,
    RsiResumeInputChanged,
    RsiResumeMismatch,
)
from jiuwenswarm.agents.harness.common.rsi.harness_adapter import HarnessEngineRequest
from jiuwenswarm.agents.harness.common.rsi.harness_provider import HarnessProvider


def _request(tmp_path: Path, *, resume: bool = False) -> HarnessEngineRequest:
    return HarnessEngineRequest(
        task_id="rsi-test0001",
        dataset_files=(str(tmp_path / "cases.json"),),
        harness_refs_path=str(tmp_path / "refs" / "harness_refs.yaml"),
        output_dir=str(tmp_path / "rsi-test0001" / "run"),
        dataset_id="single_harness_benchmark",
        max_iterations=3,
        search_width=1,
        model_refs={},
        resume=resume,
    )


class _StubOrchestrator:
    """记录 engine request 并按状态文件收敛的最小编排器替身。"""

    def __init__(self, state: dict[str, Any]) -> None:
        self.state = state
        self.requests: list[Any] = []
        self.on_event_seen: list[Any] = []

    async def run(self, request: Any, *, on_event: Any = None) -> None:
        self.requests.append(request)
        self.on_event_seen.append(on_event)
        self.state["status"] = "completed"
        state_path = Path(request.output_dir) / "single_harness_state.yaml"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        with open(state_path, "w", encoding="utf-8") as fh:
            yaml.safe_dump(self.state, fh, allow_unicode=True)


def _write_state(tasks_root: Path, task_id: str, state: dict[str, Any]) -> None:
    task_dir = tasks_root / task_id / "run"
    task_dir.mkdir(parents=True, exist_ok=True)
    with open(task_dir / "single_harness_state.yaml", "w", encoding="utf-8") as fh:
        yaml.safe_dump(state, fh, allow_unicode=True)


def _state_dict() -> dict[str, Any]:
    return {
        "status": "completed",
        "best_score": 0.8,
        "baseline_score": 0.5,
        "best_harness_refs_path": "/tmp/refs.yaml",
        "published_harness_refs_path": "/tmp/published.yaml",
        "candidate_gates": [
            {
                "candidate_id": "C1",
                "status": "accepted",
                "accepted": True,
                "reason": "candidate_passed_batch_gate",
                "candidate_score": 0.8,
                "before_harness_refs_path": "/tmp/source.yaml",
                "candidate_harness_refs_path": "/tmp/refs.yaml",
            },
            {
                "candidate_id": "C2",
                "status": "rejected",
                "accepted": False,
                "reason": "score_regression",
                "candidate_score": 0.3,
                "before_harness_refs_path": "/tmp/refs.yaml",
                "candidate_harness_refs_path": "/tmp/c2.yaml",
            },
        ],
    }


def test_validate_input_real_dataset(tmp_path: Path) -> None:
    dataset = tmp_path / "cases.json"
    dataset.write_text(
        json.dumps(
            [
                {"case_id": "a", "question": "q1"},
                {"case_id": "b", "question": "q2"},
            ]
        ),
        encoding="utf-8",
    )
    provider = HarnessProvider(tmp_path)
    result = provider.validate_input(str(dataset))
    assert result == {"valid": True, "sample_count": 2, "errors": []}


def test_validate_input_missing_path(tmp_path: Path) -> None:
    provider = HarnessProvider(tmp_path)
    result = provider.validate_input(str(tmp_path / "missing.json"))
    assert result["valid"] is False
    assert result["errors"][0]["code"] == "PATH_INVALID"


def test_validate_input_bad_case_shape(tmp_path: Path) -> None:
    dataset = tmp_path / "cases.json"
    dataset.write_text(json.dumps(["a", "b"]), encoding="utf-8")
    provider = HarnessProvider(tmp_path)
    result = provider.validate_input(str(dataset))
    assert result["valid"] is False
    assert result["errors"][0]["code"] == "DATASET_INVALID"
    assert result["sample_count"] is None


def test_validate_input_duplicate_case_ids(tmp_path: Path) -> None:
    dataset = tmp_path / "cases.json"
    dataset.write_text(json.dumps([{"case_id": "a"}, {"case_id": "a"}]), encoding="utf-8")
    result = HarnessProvider(tmp_path).validate_input(str(dataset))
    assert result["valid"] is False
    assert result["errors"][0]["code"] == "DATASET_INVALID"


def test_validate_input_requires_path() -> None:
    result = HarnessProvider(Path(".")).validate_input(None)
    assert result["valid"] is False
    assert result["errors"][0]["code"] == "DATASET_REQUIRED"


def test_run_translates_request_and_result(tmp_path: Path) -> None:
    state = _state_dict()
    stub = _StubOrchestrator(state)
    provider = HarnessProvider(tmp_path, orchestrator=stub)
    events: list[Any] = []

    async def _sink(event: Any) -> None:
        events.append(event)

    import asyncio

    result = asyncio.run(provider.run(_request(tmp_path), on_event=_sink))
    assert result.status == "completed"
    assert result.final_node_id == "C1"
    assert len(stub.requests) == 1
    engine_request = stub.requests[0]
    assert engine_request.dataset_files == [str(tmp_path / "cases.json")]
    assert engine_request.resume is False
    assert len(stub.on_event_seen) == 1 and callable(stub.on_event_seen[0])


def test_resume_fingerprint_conflict_maps_to_resume_mismatch(tmp_path: Path) -> None:
    class _Conflicting:
        async def run(self, request: Any, *, on_event: Any = None) -> None:
            raise ValueError("resume inputs do not match single-harness state")

    provider = HarnessProvider(tmp_path, orchestrator=_Conflicting())
    import asyncio

    with pytest.raises(RsiResumeMismatch):
        asyncio.run(provider.resume(_request(tmp_path, resume=True)))


def test_pause_and_terminate_report_capability_boundary(tmp_path: Path) -> None:
    provider = HarnessProvider(tmp_path)
    assert provider.supports_pause is False
    import asyncio

    with pytest.raises(RsiNotReady):
        asyncio.run(provider.pause("rsi-test0001"))
    with pytest.raises(RsiNotReady):
        asyncio.run(provider.terminate("rsi-test0001"))


def test_read_state_and_report(tmp_path: Path) -> None:
    _write_state(tmp_path, "rsi-test0001", _state_dict())
    (tmp_path / "rsi-test0001" / "run" / "single_harness_report.yaml").write_text(
        yaml.safe_dump({"status": "completed", "best_score": 0.8, "candidate_gates": []}),
        encoding="utf-8",
    )
    provider = HarnessProvider(tmp_path)
    state = provider.read_state("rsi-test0001")
    assert state.status == "completed"
    assert state.best_node_id == "C1"
    assert state.score == pytest.approx(0.8)
    assert state.baseline == pytest.approx(0.5)
    assert state.iteration == 2
    report = provider.read_report("rsi-test0001")
    assert report.status == "completed"
    assert report.best_node_id == "C1"


def test_get_tree_derives_parent_by_refs_reversal(tmp_path: Path) -> None:
    state = _state_dict()
    state["candidate_gates"][1]["before_harness_refs_path"] = "/tmp/refs.yaml"
    _write_state(tmp_path, "rsi-test0001", state)
    tree = HarnessProvider(tmp_path).get_tree("rsi-test0001")
    node_ids = [node.node_id for node in tree.nodes]
    assert node_ids == ["C1", "C2"]
    assert tree.nodes[0].parent_id is None
    assert tree.nodes[1].parent_id == "C1"
    assert tree.nodes[0].adopted is True
    assert tree.nodes[1].adopted is False


def test_locate_artifact_by_gate_id(tmp_path: Path) -> None:
    _write_state(tmp_path, "rsi-test0001", _state_dict())
    ref = HarnessProvider(tmp_path).locate_artifact("rsi-test0001", "C1")
    assert ref.artifact_id == "C1"
    assert ref.path == "/tmp/refs.yaml"


def test_materialized_request_is_validated_against_task_snapshots(tmp_path: Path) -> None:
    task_id = "rsi-materialized"
    task_root = tmp_path / task_id
    input_dir = task_root / "input"
    harness_dir = task_root / "harness"
    config_dir = task_root / "config"
    run_dir = task_root / "run"
    for directory in (input_dir, harness_dir, config_dir, run_dir):
        directory.mkdir(parents=True, exist_ok=True)
    dataset = input_dir / "cases.json"
    dataset.write_text('[{"case_id": "a"}]', encoding="utf-8")
    source_harness = tmp_path / "source_harness.yaml"
    source_harness.write_text("name: demo\n", encoding="utf-8")
    refs = harness_dir / "harness_refs.yaml"
    refs.write_text(
        yaml.safe_dump({"harness_refs": {"validation_harness": str(source_harness.resolve())}}),
        encoding="utf-8",
    )
    profile = config_dir / "harness_orchestrator.yaml"
    profile.write_text("workspace_dir: %s\n" % run_dir, encoding="utf-8")
    model_dir = task_root / "models"
    model_dir.mkdir()
    model = model_dir / "evaluation.yaml"
    model.write_text("model_client_config: {}\n", encoding="utf-8")
    import hashlib

    def digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    materials = {
        "input_snapshot": {"sha256": digest(dataset)},
        "harness_snapshot": {
            "sha256": digest(refs),
            "source_path": str(source_harness.resolve()),
            "source_sha256": digest(source_harness),
        },
        "profile": {"sha256": digest(profile)},
        "models": {"evaluation": {"path": str(model), "config_sha256": digest(model)}},
    }
    (task_root / "task.json").write_text(
        json.dumps({"config": {"rsi_materials": materials}}),
        encoding="utf-8",
    )

    request = HarnessEngineRequest(
        task_id=task_id,
        dataset_files=(str(dataset),),
        harness_refs_path=str(refs),
        output_dir=str(run_dir),
        dataset_id="single_harness_benchmark",
        max_iterations=1,
        search_width=1,
        model_refs={},
        orchestrator_config_path=str(profile),
    )
    provider = HarnessProvider(tmp_path, orchestrator=_StubOrchestrator(_state_dict()))
    import asyncio

    result = asyncio.run(provider.run(request))
    assert result.status == "completed"

    source_harness.write_text("name: changed\n", encoding="utf-8")
    with pytest.raises(RsiPathNotAllowed):
        asyncio.run(provider.run(request))
    with pytest.raises(RsiResumeInputChanged):
        asyncio.run(provider.resume(request))


def test_analysis_reuses_optimizer_model() -> None:
    from openjiuwen.rsi import AutoCoordinatingHarnessConfig

    config = HarnessProvider._apply_model_refs(
        AutoCoordinatingHarnessConfig(),
        {"optimizer": "optimizer.yaml", "tester": "tester.yaml"},
    )

    assert config.model_configs.evaluation == "tester.yaml"
    assert config.evaluator.model_config_ref == "tester.yaml"
    assert config.model_configs.analysis == "optimizer.yaml"
    assert config.evaluation_result_analyzer.model_config_ref == "optimizer.yaml"
    assert config.model_configs.member_optimization == "optimizer.yaml"
    assert config.member_optimizer.model_config_ref == "optimizer.yaml"
