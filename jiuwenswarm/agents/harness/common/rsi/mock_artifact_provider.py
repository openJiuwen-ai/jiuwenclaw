"""Small in-process artifact Providers for service-layer integration.

The real ScienceDiscovery and autoResearch implementations are owned by
other repositories.  These Providers intentionally exercise only the
AgentServer contract: durable state/report/tree/artifact snapshots and the
four public event shapes.  Replacing them later must not require changes to
the common RSI service or Web/Gateway routing.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import zipfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

from openjiuwen.rsi.artifact_rsi.request import ArtifactEngineRequest
from openjiuwen.rsi.events import EventNode, EventProgress, EventStatus, OnEvent
from openjiuwen.rsi.schema import (
    ArtifactRef,
    ArtifactValidationResult,
    EngineReport,
    EngineResult,
    EngineState,
    RsiChange,
    RsiUsage,
    RsiUsageTokens,
    RsiTreeNode,
    TreeResponse,
)


class MockArtifactProvider:
    """Deterministic Provider used to close the AgentServer service loop."""

    def __init__(self, tasks_root: str | Path, artifact_type: str) -> None:
        normalized = str(artifact_type or "").strip().lower()
        if normalized not in {"program", "paper"}:
            raise ValueError(f"unsupported mock artifact type: {artifact_type}")
        self.artifact_type = normalized
        self.tasks_root = Path(tasks_root)

    # -- public Provider contract -----------------------------------------

    def validate_input(self, artifact_path: str | None) -> ArtifactValidationResult:
        if self.artifact_type == "paper" and not artifact_path:
            # Paper optimization may be instruction-only; the AgentServer
            # validates that the instruction exists on task creation.
            return ArtifactValidationResult(valid=True, errors=[])
        if not artifact_path:
            return ArtifactValidationResult(
                valid=False,
                errors=[{"code": "ARTIFACT_PATH_REQUIRED", "message": "artifact_path is required"}],
            )
        path = Path(artifact_path).expanduser()
        if not path.exists():
            return ArtifactValidationResult(
                valid=False,
                errors=[{"code": "PATH_INVALID", "message": f"artifact path does not exist: {path}"}],
            )
        if self.artifact_type == "paper" and path.is_file() and path.suffix.lower() != ".zip":
            return ArtifactValidationResult(
                valid=False,
                errors=[{"code": "PATH_INVALID", "message": "paper artifact must be a .zip file"}],
            )
        return ArtifactValidationResult(valid=True, errors=[])

    async def run(self, request: ArtifactEngineRequest, on_event: OnEvent | None = None) -> EngineResult:
        return await self._run(request, on_event=on_event, start_iteration=1, create_root=True)

    async def pause(self, task_id: str, on_event: OnEvent | None = None) -> EngineResult:
        if self.artifact_type == "paper":
            return self._unsupported(task_id, "paper artifact optimization does not support pause")
        state = self._load_state(task_id)
        if state is None:
            return self._result(task_id, "failed", error_code="TASK_NOT_FOUND", error_message="task snapshot missing")
        state["status"] = "paused"
        self._save_state(task_id, state)
        report = self._load_json(task_id, "mock_artifact_report.json")
        if report is not None:
            report["status"] = "paused"
            self._save_json(task_id, "mock_artifact_report.json", report)
        await _emit(on_event, EventStatus(status="paused"))
        return self._result(task_id, "paused", final_node_id=state.get("best_node_id"))

    async def resume(self, request: ArtifactEngineRequest, on_event: OnEvent | None = None) -> EngineResult:
        if self.artifact_type == "paper":
            return self._unsupported(request.task_id, "paper artifact optimization does not support resume")
        state = self._load_state(request.task_id)
        if state is None:
            # A queued task can be paused before the Provider has created its
            # first checkpoint.  Resuming that task is equivalent to a fresh
            # run and still keeps the same AgentServer task identity.
            return await self._run(
                request,
                on_event=on_event,
                start_iteration=1,
                create_root=True,
            )
        start_iteration = max(1, int(state.get("iteration", 0) or 0) + 1)
        if start_iteration > request.max_iterations:
            state["status"] = "completed"
            self._save_state(request.task_id, state)
            report = self._load_json(request.task_id, "mock_artifact_report.json")
            if report is not None:
                report["status"] = "completed"
                self._save_json(request.task_id, "mock_artifact_report.json", report)
            return self._result(request.task_id, "completed", final_node_id=state.get("best_node_id"))
        return await self._run(
            request,
            on_event=on_event,
            start_iteration=start_iteration,
            create_root=False,
        )

    def read_state(self, task_id: str) -> EngineState:
        state = self._load_state(task_id)
        if state is None:
            raise FileNotFoundError(f"mock artifact state not found: {task_id}")
        usage = _usage_from_dict(state.get("usage"))
        return EngineState(
            task_id=task_id,
            status=str(state.get("status") or "created"),
            iteration=int(state.get("iteration", 0) or 0),
            total_iterations=int(state.get("total_iterations", 0) or 0),
            score=state.get("score"),
            baseline=state.get("baseline"),
            usage=usage,
            updated_at=str(state.get("updated_at") or ""),
            error_code=state.get("error_code"),
            error_message=state.get("error_message"),
        )

    def read_report(self, task_id: str) -> EngineReport:
        report = self._load_json(task_id, "mock_artifact_report.json")
        if report is None:
            raise FileNotFoundError(f"mock artifact report not found: {task_id}")
        refs = [ArtifactRef(**item) for item in report.get("artifact_index") or []]
        return EngineReport(
            task_id=task_id,
            status=str(report.get("status") or "created"),
            best_node_id=report.get("best_node_id"),
            usage=_usage_from_dict(report.get("usage")),
            artifact_index=refs,
            summary=report.get("summary"),
        )

    def get_tree(self, task_id: str) -> TreeResponse:
        tree = self._load_json(task_id, "mock_artifact_tree.json")
        if tree is None:
            raise FileNotFoundError(f"mock artifact tree not found: {task_id}")
        nodes = [_node_from_dict(item) for item in tree.get("nodes") or []]
        return TreeResponse(
            nodes=nodes,
            depth=int(tree.get("depth", 0) or 0),
            iteration=int(tree.get("iteration", 0) or 0),
        )

    def locate_artifact(self, task_id: str, artifact_id: str | None = None) -> ArtifactRef:
        report = self.read_report(task_id)
        if artifact_id is None:
            for ref in reversed(report.artifact_index):
                if ref.node_id == report.best_node_id:
                    return ref
            if report.artifact_index:
                return report.artifact_index[-1]
        else:
            for ref in report.artifact_index:
                if ref.artifact_id == artifact_id:
                    return ref
        raise FileNotFoundError(f"mock artifact not found: {task_id}/{artifact_id or '<best>'}")

    async def terminate(self, task_id: str, on_event: OnEvent | None = None) -> EngineResult:
        state = self._load_state(task_id)
        if state is None:
            return self._result(task_id, "failed", error_code="TASK_NOT_FOUND", error_message="task snapshot missing")
        state["status"] = "terminated"
        self._save_state(task_id, state)
        report = self._load_json(task_id, "mock_artifact_report.json") or {
            "task_id": task_id,
            "best_node_id": state.get("best_node_id"),
            "usage": state.get("usage"),
            "artifact_index": [],
            "summary": None,
        }
        report["status"] = "terminated"
        self._save_json(task_id, "mock_artifact_report.json", report)
        await _emit(on_event, EventStatus(status="terminated"))
        return self._result(task_id, "terminated", final_node_id=state.get("best_node_id"))

    # -- mock execution ----------------------------------------------------

    async def _run(
        self,
        request: ArtifactEngineRequest,
        *,
        on_event: OnEvent | None,
        start_iteration: int,
        create_root: bool,
    ) -> EngineResult:
        task_id = request.task_id
        run_dir = Path(request.run_dir).expanduser().resolve()
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "artifacts").mkdir(parents=True, exist_ok=True)

        state = self._load_state(task_id) or self._new_state(task_id, request.max_iterations)
        tree = self._load_json(task_id, "mock_artifact_tree.json") or {"nodes": [], "iteration": 0, "depth": 0}
        report = self._load_json(task_id, "mock_artifact_report.json") or {
            "task_id": task_id,
            "status": "created",
            "best_node_id": None,
            "usage": None,
            "artifact_index": [],
            "summary": None,
        }

        state.update(
            {
                "status": "running",
                "total_iterations": request.max_iterations,
                "error_code": None,
                "error_message": None,
            }
        )
        if create_root and not tree["nodes"]:
            root = self._root_node()
            tree["nodes"].append(asdict(root))
            tree["depth"] = 0
            tree["iteration"] = 0
        self._save_state(task_id, state)
        self._save_json(task_id, "mock_artifact_tree.json", tree)
        self._save_json(task_id, "mock_artifact_report.json", report)
        await _emit(on_event, EventStatus(status="running"))

        previous_node_id = state.get("best_node_id") or "ROOT"
        artifact_index = [dict(item) for item in report.get("artifact_index") or []]
        for iteration in range(start_iteration, request.max_iterations + 1):
            # Give a concurrently scheduled AgentServer pause/terminate
            # command a checkpoint at which it can take effect.  The real
            # Provider owns the equivalent safe-point semantics.
            await asyncio.sleep(0)
            control_state = self._load_state(task_id) or {}
            if control_state.get("status") in {"paused", "terminated"}:
                status = str(control_state["status"])
                await _emit(on_event, EventStatus(status=status))
                return self._result(task_id, status, final_node_id=control_state.get("best_node_id"))
            node_id = f"{task_id}:node:{iteration}"
            artifact_id = f"{task_id}:artifact:{iteration}"
            artifact_path = run_dir / "artifacts" / f"{self.artifact_type}-{iteration:03d}.zip"
            self._write_artifact(artifact_path, request, iteration)
            artifact_ref = ArtifactRef(
                artifact_id=artifact_id,
                node_id=node_id,
                name=artifact_path.name,
                kind=f"{self.artifact_type}_snapshot",
                path=str(artifact_path),
                sha256=_sha256(artifact_path),
                download_url=None,
            )
            score = None if self.artifact_type == "paper" else round(
                0.5 + 0.5 * iteration / max(request.max_iterations, 1), 4
            )
            group = self.artifact_type
            node = RsiTreeNode(
                node_id=node_id,
                iteration=iteration,
                parent_id=previous_node_id,
                type="adopted",
                adopted=True,
                score=score,
                summary=(
                    f"mock {self.artifact_type} optimization iteration {iteration}"
                ),
                snapshot_artifact_id=artifact_id,
                reason=None,
                failure_class=None,
                changes=[
                    RsiChange(
                        group=group,
                        operation="mock",
                        function=None,
                        target=str(request.artifact_path or "instruction"),
                        summary=f"mock {self.artifact_type} artifact change",
                    )
                ],
                extra={
                    group: {
                        "logical_kind": "adopted",
                        "iteration": iteration,
                        "artifacts": [asdict(artifact_ref)],
                    }
                },
            )
            tree["nodes"] = [item for item in tree["nodes"] if item.get("node_id") != node_id]
            tree["nodes"].append(asdict(node))
            tree["iteration"] = iteration
            tree["depth"] = max(int(tree.get("depth", 0) or 0), iteration)
            artifact_index = [item for item in artifact_index if item.get("artifact_id") != artifact_id]
            artifact_index.append(asdict(artifact_ref))
            usage = _usage_for_iteration(iteration)
            state.update(
                {
                    "status": "running",
                    "iteration": iteration,
                    "best_node_id": node_id,
                    "score": score,
                    "baseline": None if self.artifact_type == "paper" else 0.5,
                    "usage": asdict(usage),
                }
            )
            report.update(
                {
                    "status": "running",
                    "best_node_id": node_id,
                    "usage": state["usage"],
                    "artifact_index": artifact_index,
                    "summary": f"mock {self.artifact_type} optimization is running",
                }
            )
            self._save_state(task_id, state)
            self._save_json(task_id, "mock_artifact_tree.json", tree)
            self._save_json(task_id, "mock_artifact_report.json", report)
            await _emit(on_event, EventNode(node=node))
            await _emit(
                on_event,
                EventProgress(
                    iteration=iteration,
                    total_iterations=request.max_iterations,
                    score=score,
                    baseline=state["baseline"],
                    usage=usage,
                ),
            )
            previous_node_id = node_id

        await asyncio.sleep(0)
        control_state = self._load_state(task_id) or state
        if control_state.get("status") in {"paused", "terminated"}:
            status = str(control_state["status"])
            await _emit(on_event, EventStatus(status=status))
            return self._result(task_id, status, final_node_id=control_state.get("best_node_id"))
        state["status"] = "completed"
        report["status"] = "completed"
        report["summary"] = f"mock {self.artifact_type} optimization completed"
        self._save_state(task_id, state)
        self._save_json(task_id, "mock_artifact_report.json", report)
        await _emit(on_event, EventStatus(status="completed"))
        return self._result(task_id, "completed", final_node_id=state.get("best_node_id"))

    # -- durable snapshots -------------------------------------------------

    def _root_node(self) -> RsiTreeNode:
        return RsiTreeNode(
            node_id="ROOT",
            iteration=0,
            parent_id=None,
            type="root",
            adopted=True,
            score=None,
            summary="artifact optimization baseline",
            snapshot_artifact_id=None,
            reason=None,
            failure_class=None,
            changes=[],
            extra={self.artifact_type: {"logical_kind": "root", "artifacts": []}},
        )

    def _new_state(self, task_id: str, total_iterations: int) -> dict[str, Any]:
        return {
            "task_id": task_id,
            "status": "created",
            "iteration": 0,
            "total_iterations": total_iterations,
            "best_node_id": None,
            "score": None,
            "baseline": None,
            "usage": None,
            "updated_at": "",
            "error_code": None,
            "error_message": None,
        }

    def _run_dir(self, task_id: str) -> Path:
        return self.tasks_root / task_id / "run"

    def _load_state(self, task_id: str) -> dict[str, Any] | None:
        return self._load_json(task_id, "mock_artifact_state.json")

    def _load_json(self, task_id: str, filename: str) -> dict[str, Any] | None:
        path = self._run_dir(task_id) / filename
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def _save_state(self, task_id: str, state: dict[str, Any]) -> None:
        from datetime import datetime, timezone

        state["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._save_json(task_id, "mock_artifact_state.json", state)

    def _save_json(self, task_id: str, filename: str, data: dict[str, Any]) -> None:
        path = self._run_dir(task_id) / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)

    def _write_artifact(self, target: Path, request: ArtifactEngineRequest, iteration: int) -> None:
        source = Path(request.artifact_path).expanduser() if request.artifact_path else None
        target.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            if source is not None and source.is_file():
                archive.write(source, arcname=source.name)
            elif source is not None and source.is_dir():
                files = sorted(item for item in source.rglob("*") if item.is_file())
                for item in files:
                    archive.write(item, arcname=str(item.relative_to(source)))
            else:
                archive.writestr("README.md", str(request.optimization_instruction or "mock artifact"))
            archive.writestr(
                "mock-optimization.json",
                json.dumps(
                    {"artifact_type": self.artifact_type, "iteration": iteration},
                    ensure_ascii=False,
                ),
            )

    def _result(
        self,
        task_id: str,
        status: str,
        *,
        final_node_id: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> EngineResult:
        return EngineResult(
            task_id=task_id,
            status=status,
            final_node_id=final_node_id,
            error_code=error_code,
            error_message=error_message,
        )

    def _unsupported(self, task_id: str, message: str) -> EngineResult:
        return self._result(
            task_id,
            "created",
            error_code="SCENARIO_NOT_SUPPORTED",
            error_message=message,
        )


def build_mock_artifact_adapters(
    tasks_root: str | Path,
    *,
    model_resolver: Any = None,
) -> dict[str, Any]:
    """Build the replaceable program/paper adapter pair for AgentServer."""

    from jiuwenswarm.agents.harness.common.rsi.artifact_adapter import ArtifactEngineAdapter

    return {
        "ARTIFACT:PROGRAM": ArtifactEngineAdapter(
            "PROGRAM",
            MockArtifactProvider(tasks_root, "program"),
            model_resolver=model_resolver,
        ),
        "ARTIFACT:PAPER": ArtifactEngineAdapter(
            "PAPER",
            MockArtifactProvider(tasks_root, "paper"),
            model_resolver=model_resolver,
        ),
    }


async def _emit(on_event: OnEvent | None, event: Any) -> None:
    if on_event is not None:
        await on_event(event)


def _usage_for_iteration(iteration: int) -> RsiUsage:
    return RsiUsage(
        tokens=RsiUsageTokens(
            input=100 * iteration,
            output=40 * iteration,
            cache_hit=10 * max(iteration - 1, 0),
        ),
        cost_estimate=0.0,
        call_count=iteration,
    )


def _usage_from_dict(raw: Any) -> RsiUsage | None:
    if not isinstance(raw, dict):
        return None
    tokens = raw.get("tokens") if isinstance(raw.get("tokens"), dict) else {}
    return RsiUsage(
        tokens=RsiUsageTokens(
            input=int(tokens.get("input", 0) or 0),
            output=int(tokens.get("output", 0) or 0),
            cache_hit=int(tokens.get("cache_hit", 0) or 0),
        ),
        cost_estimate=float(raw.get("cost_estimate", 0.0) or 0.0),
        call_count=int(raw.get("call_count", 0) or 0),
    )


def _node_from_dict(raw: Any) -> RsiTreeNode:
    data = raw if isinstance(raw, dict) else {}
    changes = [RsiChange(**item) for item in data.get("changes") or [] if isinstance(item, dict)]
    return RsiTreeNode(
        node_id=str(data.get("node_id") or ""),
        iteration=int(data.get("iteration", 0) or 0),
        parent_id=data.get("parent_id"),
        type=str(data.get("type") or "rejected"),
        adopted=bool(data.get("adopted")),
        score=data.get("score"),
        summary=data.get("summary"),
        snapshot_artifact_id=data.get("snapshot_artifact_id"),
        reason=data.get("reason"),
        failure_class=data.get("failure_class"),
        changes=changes,
        extra=data.get("extra") if isinstance(data.get("extra"), dict) else {},
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = ["MockArtifactProvider", "build_mock_artifact_adapters"]
