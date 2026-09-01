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

    def __init__(self, tasks_root: Path, *, worker_push_callbacks: dict[str, Any] | None = None) -> None:
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
        # 薄服务
        self.task_service = RsiTaskService(self.store)
        self.dataset_service = RsiDatasetService()
        self.tree_service = RsiTreeService(self.projector)
        self.usage_service = RsiUsageService(self.usage_recorder)
        self.report_service = RsiReportService(self.store, self.projector, self.usage_recorder, self.artifact_service)
        self.artifact_download_service = RsiArtifactDownloadService(self.artifact_service, self.store)

    def bind_task_service(self, *, adapter: Any = None, harness_refs_provider: Callable[[], str | None] | None = None) -> None:
        self.task_service.adapter = adapter
        self.task_service.harness_refs_provider = harness_refs_provider

    def bind_dataset_service(self, adapter: Any) -> None:
        self.dataset_service.adapter = adapter

    def register_adapters(self, adapters: dict[str, Any]) -> None:
        """注册场景适配器（HARNESS→HarnessEngineAdapter / ARTIFACT→ArtifactEngineAdapter）。"""
        self.worker.adapters.update(adapters)

    def register_worker_push(self, push_callbacks: dict[str, Any]) -> None:
        self.worker._push_callbacks.update(push_callbacks)  # noqa: SLF001 - 组合根内装配

    def ensure_root(self, task_id: str) -> None:
        self.projector.register_root(task_id)


def build_rsi_service_context(tasks_root: Any) -> RsiServiceContext:
    """默认组合根（tasks_root 缺省取 ``workspace/rsi/tasks``）。"""
    if tasks_root is None:
        from jiuwenswarm.common.utils import get_user_workspace_dir

        tasks_root = get_user_workspace_dir() / "rsi" / "tasks"
    return RsiServiceContext(Path(tasks_root))