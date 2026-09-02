"""RSI 服务域组合根：装配 store/worker/projector/artifact/usage/薄服务（内部 v3 §7 复用清单）。

AgentServer 侧用 ``build_rsi_service_context(tasks_root)`` 一次性构建，
再注入推送回调（send_push 包装）与 harness_refs 快照提供方。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from jiuwenswarm.agents.harness.common.rsi.artifact_service import RsiArtifactService
from jiuwenswarm.agents.harness.common.rsi.projector import RsiProjector
from jiuwenswarm.agents.harness.common.rsi.services import (
    RsiArtifactDownloadService,
    RsiDatasetService,
    RsiReportService,
    RsiTaskService,
    RsiTreeService,
    RsiUsageService,
)
from jiuwenswarm.agents.harness.common.rsi.task_store import RsiTaskStore
from jiuwenswarm.agents.harness.common.rsi.usage_recorder import RsiUsageRecorder
from jiuwenswarm.agents.harness.common.rsi.worker import RsiWorker


class RsiServiceContext:
    """服务域组件容器（组合根）。"""

    def __init__(
        self,
        tasks_root: Path,
        *,
        worker_push_callbacks: dict[str, Any] | None = None,
        adapters: dict[str, Any] | None = None,
    ) -> None:
        self.tasks_root = Path(tasks_root)
        self.store = RsiTaskStore(self.tasks_root)
        self.usage_recorder = RsiUsageRecorder()
        self.projector = RsiProjector(self.tasks_root)
        self.artifact_service = RsiArtifactService(self.tasks_root)
        self.worker = RsiWorker(
            store=self.store,
            adapters={},  # C5 HarnessEngineAdapter / ArtifactEngineAdapter 注入（⚠️外部）
            usage_recorder=self.usage_recorder,
            projector=self.projector,
            artifact_service=self.artifact_service,
            push_callbacks=worker_push_callbacks or {},
        )
        self.adapters = self.worker.adapters
        if adapters:
            self.register_adapters(adapters)
        # 薄服务
        self.task_service = RsiTaskService(self.store, adapter_resolver=self.adapter_for)
        self.dataset_service = RsiDatasetService(adapter_resolver=self.adapter_for)
        self.tree_service = RsiTreeService(
            self.projector,
            store=self.store,
            adapter_resolver=self.adapter_for,
        )
        self.usage_service = RsiUsageService(
            self.usage_recorder,
            store=self.store,
            adapter_resolver=self.adapter_for,
        )
        self.report_service = RsiReportService(
            self.store,
            self.projector,
            self.usage_recorder,
            self.artifact_service,
            adapter_resolver=self.adapter_for,
        )
        self.artifact_download_service = RsiArtifactDownloadService(
            self.artifact_service,
            self.store,
            adapter_resolver=self.adapter_for,
        )

    def bind_task_service(self, *, adapter: Any = None, harness_refs_provider: Callable[[], str | None] | None = None) -> None:
        self.task_service.adapter = adapter
        self.task_service.harness_refs_provider = harness_refs_provider
        if adapter is not None:
            # Backward-compatible harness adapter injection.  Artifact
            # adapters should use ``register_adapters`` with an explicit type.
            self.register_adapters({"HARNESS": adapter})

    def bind_dataset_service(self, adapter: Any) -> None:
        self.dataset_service.adapter = adapter

    def register_adapters(self, adapters: dict[str, Any]) -> None:
        """注册场景适配器（HARNESS→HarnessEngineAdapter / ARTIFACT→ArtifactEngineAdapter）。"""
        self.adapters.update(adapters)

    def adapter_for(self, scenario: str | None, artifact_type: str | None = None) -> Any:
        """Resolve an adapter using the task's public scene/type pair."""
        scenario_key = str(scenario or "").strip().upper()
        artifact_key = str(artifact_type or "").strip().upper()
        candidates = []
        if scenario_key == "ARTIFACT":
            if artifact_key:
                candidates.extend(
                    [
                        f"ARTIFACT:{artifact_key}",
                        f"{scenario_key}:{artifact_key}",
                        f"artifact:{artifact_key.lower()}",
                    ]
                )
            candidates.append("ARTIFACT")
        elif scenario_key:
            candidates.extend([scenario_key, scenario_key.lower()])
        for key in candidates:
            adapter = self.adapters.get(key)
            if adapter is not None:
                return adapter
        return None

    def adapter_for_task(self, task_id: str) -> Any:
        if not task_id:
            return None
        task = self.store.get(task_id)
        return self.adapter_for(task.scenario, task.artifact_type)

    def install_mock_artifact_adapters(self, *, model_resolver: Any = None) -> dict[str, Any]:
        """Install deterministic artifact Providers for local service E2E tests."""
        from jiuwenswarm.agents.harness.common.rsi.mock_artifact_provider import (
            build_mock_artifact_adapters,
        )

        adapters = build_mock_artifact_adapters(
            self.tasks_root,
            model_resolver=model_resolver,
        )
        self.register_adapters(adapters)
        return adapters

    def register_artifact_providers(
        self,
        *,
        program: Any = None,
        paper: Any = None,
        model_resolver: Any = None,
    ) -> dict[str, Any]:
        """Wrap concrete program/paper Providers at the AgentServer seam."""
        from jiuwenswarm.agents.harness.common.rsi.artifact_adapter import (
            ArtifactEngineAdapter,
        )

        adapters: dict[str, Any] = {}
        if program is not None:
            adapters["ARTIFACT:PROGRAM"] = ArtifactEngineAdapter(
                "PROGRAM", program, model_resolver=model_resolver
            )
        if paper is not None:
            adapters["ARTIFACT:PAPER"] = ArtifactEngineAdapter(
                "PAPER", paper, model_resolver=model_resolver
            )
        self.register_adapters(adapters)
        return adapters

    def register_worker_push(self, push_callbacks: dict[str, Any]) -> None:
        self.worker._push_callbacks.update(push_callbacks)  # noqa: SLF001 - 组合根内装配

    def ensure_root(self, task_id: str) -> None:
        self.projector.register_root(task_id)


def build_rsi_service_context(
    tasks_root: Any,
    *,
    adapters: dict[str, Any] | None = None,
) -> RsiServiceContext:
    """默认组合根（tasks_root 缺省取 ``workspace/rsi/tasks``）。"""
    if tasks_root is None:
        from jiuwenswarm.common.utils import get_user_workspace_dir

        tasks_root = get_user_workspace_dir() / "rsi" / "tasks"
    return RsiServiceContext(Path(tasks_root), adapters=adapters)
