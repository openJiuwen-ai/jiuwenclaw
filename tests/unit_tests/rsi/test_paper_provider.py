from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from jiuwenswarm.agents.harness.common.rsi.paper_provider import (
    PaperProvider,
    _ExecutionOutcome,
    _safe_reporting_resource_paths,
)
from jiuwenswarm.agents.harness.common.rsi.provider_factory import build_rsi_adapters
from openjiuwen.rsi.artifact_rsi.request import ArtifactEngineRequest


def _request(tasks_root: Path, task_id: str = "rsi-paper") -> ArtifactEngineRequest:
    run_dir = tasks_root / task_id / "run"
    return ArtifactEngineRequest(
        task_id=task_id,
        run_dir=str(run_dir),
        artifact_path=None,
        model=object(),
        max_iterations=1,
        optimization_instruction="improve the paper",
    )


def test_real_factory_registers_paper_provider(tmp_path: Path):
    adapters = build_rsi_adapters(tmp_path / "tasks", mode="real")

    assert set(adapters) == {"ARTIFACT:PAPER"}
    assert isinstance(adapters["ARTIFACT:PAPER"].provider, PaperProvider)


def test_paper_provider_wires_the_bundled_autoresearch_runtime(tmp_path: Path):
    captured: dict[str, object] = {}

    class FakeRuntime:
        def __init__(self, config, **kwargs):
            captured["config"] = config
            captured["components"] = kwargs

        async def arun(self, **kwargs):
            captured["request"] = kwargs
            return SimpleNamespace(status="complete", summary="dry run")

    model = SimpleNamespace(
        model_client_config=SimpleNamespace(
            client_provider="OpenAI",
            api_key="test-key",
            api_base="http://127.0.0.1/v1",
            timeout=17,
        ),
        model_config=SimpleNamespace(model_name="test-model"),
    )
    tasks_root = tmp_path / "tasks"
    run_dir = tasks_root / "rsi-paper" / "run"
    request = ArtifactEngineRequest(
        task_id="rsi-paper",
        run_dir=str(run_dir),
        artifact_path=None,
        model=model,
        max_iterations=1,
        optimization_instruction="improve the paper",
    )

    provider = PaperProvider(tasks_root)
    with patch(
        "openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.pipeline.manager.ManagerRuntime",
        FakeRuntime,
    ):
        terminal = provider._run_manager(
            request,
            run_dir=run_dir,
            manager_run_id="rsi-paper-iteration-001",
            topic="improve the paper",
            initial_prompt="task prompt",
            research_paths=[],
        )

    assert terminal.status == "complete"
    assert captured["config"]["openjiuwen"] == {  # type: ignore[index]
        "base_url": "http://127.0.0.1/v1",
        "model": "test-model",
        "provider": "OpenAI",
        "timeout": 17.0,
    }
    assert captured["request"] == {  # type: ignore[index]
        "topic": "improve the paper",
        "research_paths": [],
        "run_id": "rsi-paper-iteration-001",
        "objective": "improve the paper",
        "initial_prompt": "task prompt",
        "task_mode": "create_new_paper",
    }


def test_paper_provider_accepts_a_regular_file_and_stages_it(tmp_path: Path):
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"paper input")
    tasks_root = tmp_path / "tasks"
    run_dir = tasks_root / "rsi-paper" / "run"
    provider = PaperProvider(tasks_root)

    validation = provider.validate_input(str(source))
    assert validation.valid is True

    staged = provider._stage_input_file(source, run_dir)  # noqa: SLF001 - input staging contract
    assert staged == [run_dir / "input" / "paper" / "paper.pdf"]
    assert staged[0].read_bytes() == b"paper input"


def test_paper_provider_accepts_and_stages_a_paper_directory(tmp_path: Path):
    source = tmp_path / "paper"
    (source / "sections").mkdir(parents=True)
    (source / "main.tex").write_text(
        "\\documentclass{article}\n\\begin{document}\nPaper\n\\end{document}\n",
        encoding="utf-8",
    )
    (source / "sections" / "method.tex").write_text("\\section{Method}\n", encoding="utf-8")

    run_dir = tmp_path / "tasks" / "rsi-paper" / "run"
    provider = PaperProvider(tmp_path / "tasks")

    assert provider.validate_input(str(source)).valid is True
    staged = provider._stage_input_file(source, run_dir)  # noqa: SLF001 - input staging contract

    assert staged == [run_dir / "input" / "paper" / "paper"]
    assert (staged[0] / "main.tex").is_file()
    assert (staged[0] / "sections" / "method.tex").is_file()


def test_reporting_resource_order_keeps_binary_paper_from_utf8_reader():
    paths = [
        "input/paper/paper",
        "data/outputs/topic_survey/run/research_summary.md",
        "data/outputs/topic_survey/run/source.pdf",
    ]

    assert _safe_reporting_resource_paths(paths) == [
        "data/outputs/topic_survey/run/research_summary.md",
        "input/paper/paper",
        "data/outputs/topic_survey/run/source.pdf",
    ]


def test_reporting_resource_order_preserves_a_paper_source_directory():
    paths = ["input/paper/paper"]

    assert _safe_reporting_resource_paths(paths) == paths


@pytest.mark.asyncio
async def test_paper_provider_projects_live_manager_reports_and_downloadable_package(
    tmp_path: Path,
):
    tasks_root = tmp_path / "tasks"
    task_id = "rsi-paper"
    run_dir = tasks_root / task_id / "run"
    provider = PaperProvider(tasks_root, poll_interval=0.05)
    report_written = threading.Event()
    release = threading.Event()

    def fake_execution(request: ArtifactEngineRequest, cancel_event: threading.Event):
        del request, cancel_event
        manager_root = run_dir / "experiments" / f"{task_id}-iteration-001"
        artifact = manager_root / "design" / "report.md"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("# generated paper design\n", encoding="utf-8")
        manager_state = manager_root / "manager" / "state.json"
        manager_state.parent.mkdir(parents=True, exist_ok=True)
        manager_state.write_text(
            json.dumps(
                {
                    "reports": [
                        {
                            "report_id": "reporting:1:1",
                            "module": "reporting",
                            "mode": "run",
                            "attempt": 1,
                            "outcome": "succeeded",
                            "retryable": False,
                            "runtime_failure": "none",
                            "summary": "paper report compiled",
                            "artifact_paths": [
                                "experiments/"
                                f"{task_id}-iteration-001/design/report.md"
                            ],
                            "handoff": {"report_path": "design/report.md"},
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        report_written.set()
        release.wait(timeout=5)
        provider._make_iteration_package(  # noqa: SLF001 - seed the provider's final artifact
            run_dir,
            f"{task_id}-iteration-001",
            1,
        )
        return _ExecutionOutcome(
            "completed",
            "paper optimization completed",
            (f"{task_id}-iteration-001",),
        )

    provider._execute_request = fake_execution  # type: ignore[method-assign]
    events: list[str] = []

    async def on_event(event):
        events.append(event.event_type)

    running = asyncio.create_task(provider.run(_request(tasks_root), on_event=on_event))
    assert await asyncio.to_thread(report_written.wait, 2)
    for _ in range(20):
        if "node" in events:
            break
        await asyncio.sleep(0.05)
    assert "node" in events
    node = provider.get_tree(task_id).nodes[-1]
    assert node.summary == "paper report compiled"
    assert node.snapshot_artifact_id
    assert not node.extra["artifact_path"].endswith(".zip")
    assert Path(node.extra["artifact_path"]).is_dir()
    assert node.extra["artifacts"][0]["node_id"] == node.node_id

    release.set()
    result = await running

    assert result.status == "completed"
    artifact = provider.locate_artifact(task_id)
    assert artifact.name == "paper-optimization-001"
    assert Path(artifact.path).is_dir()
    assert provider.read_state(task_id).best_node_id != "ROOT"
