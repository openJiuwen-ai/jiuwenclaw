"""派生薄封装服务（内部 v3 §4.7）：RsiDatasetService / RsiTaskService / RsiReportService / RsiTreeService。

- 薄封装：委托 store / projector / usage / artifact / adapter。
- 场景校验、错误码映射在服务层（web §3.5 语义）。
- I7/I8/I9 + C1 为**中优先级预留**：接口签名与服务方法已落位，引擎衔接/单价算法不实现。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jiuwenswarm.agents.harness.common.rsi.adapter import validate_scenario
from jiuwenswarm.agents.harness.common.rsi.errors import (
    RsiBadRequest,
    RsiPathInvalid,
)
from jiuwenswarm.agents.harness.common.rsi.models import (
    ArtifactType,
    RsiDatasetResult,
    RsiTask,
    Scenario,
    TaskStatus,
    generate_task_id,
    utcnow_iso,
)


class RsiTaskService:
    """任务命令/查询（I2/I3/I4/I5）。"""

    def __init__(self, store: Any, *, adapter: Any = None, harness_refs_provider: Any = None) -> None:
        self.store = store
        self.adapter = adapter
        self.harness_refs_provider = harness_refs_provider

    # -- I2 create --

    def create(self, params: dict[str, Any]) -> dict[str, Any]:
        """``rsi.task.create``（web §6.1 三分支校验）。返回 {task_id, status=CREATED}。"""
        scenario_raw = str(params.get("scenario") or "").strip()
        artifact_type_raw = str(params.get("artifact_type") or "").strip() or None
        scenario, artifact_type = validate_scenario(
            scenario_raw, artifact_type_raw,
            artifact_type_required=(scenario_raw == Scenario.ARTIFACT.value),
        )
        name = str(params.get("name") or "").strip()
        if not name or len(name) > 100:
            raise RsiBadRequest("实验名称必填且 1–100 字符")
        model_refs = params.get("model_refs")
        if not isinstance(model_refs, dict):
            raise RsiBadRequest("model_refs 必填")
        optimizer = str(model_refs.get("optimizer") or "").strip()
        if not optimizer:
            raise RsiBadRequest("model_refs.optimizer 必填")
        if scenario is Scenario.HARNESS:
            tester = str(model_refs.get("tester") or "").strip()
            if not tester:
                raise RsiBadRequest("harness 优化必填 model_refs.tester")
        max_iterations = _positive_int(params.get("max_iterations"), default=1, field="max_iterations")
        search_width = _positive_int(params.get("search_width"), default=1, field="search_width")
        optimization_instruction = params.get("optimization_instruction")
        if optimization_instruction is not None:
            optimization_instruction = str(optimization_instruction).strip()
            if len(str(optimization_instruction)) > 1000:
                raise RsiBadRequest("optimization_instruction 至多 1000 字符")

        # 场景字段校验（web §6.1 规则②③）
        input_file: str | None = None
        artifact_path: str | None = None
        if scenario is Scenario.HARNESS:
            input_file = str(params.get("input_file") or "").strip()
            if not input_file:
                raise RsiBadRequest("harness 优化必填 input_file（数据集）")
            if params.get("artifact_path"):
                raise RsiBadRequest("harness 分支不接受 artifact_path")
        else:
            artifact_path = str(params.get("artifact_path") or "").strip()
            if not artifact_path:
                raise RsiBadRequest(f"{artifact_type.value} 必填 artifact_path")
            if not Path(artifact_path).expanduser().is_file():
                raise RsiPathInvalid(f"产物路径不存在: {artifact_path}")
            if artifact_type is ArtifactType.PAPER and not artifact_path.lower().endswith(".zip"):
                raise RsiPathInvalid("PAPER 产物必须是 .zip")
            if artifact_type is ArtifactType.PAPER:
                input_file = str(params.get("input_file") or "").strip() or None

        # 优化指令仅产物·论文可填
        if scenario is Scenario.HARNESS and optimization_instruction:
            raise RsiBadRequest("harness 优化不接受 optimization_instruction")
        if scenario is Scenario.ARTIFACT and artifact_type is ArtifactType.PROGRAM and optimization_instruction:
            raise RsiBadRequest("PROGRAM 优化不接受 optimization_instruction")

        task_id = generate_task_id()
        run_dir = str(self.store.tasks_root / task_id / "run")
        harness_refs_path: str | None = None
        if scenario is Scenario.HARNESS and self.harness_refs_provider is not None:
            harness_refs_path = self.harness_refs_provider()
        config: dict[str, Any] = {
            "harness_refs_path": harness_refs_path,
            "artifact_path": artifact_path,
            "results": {},
            "active_ref_released": False,
        }
        task = RsiTask(
            task_id=task_id,
            name=name,
            scenario=scenario.value,
            artifact_type=artifact_type.value if artifact_type else None,
            input_file=input_file,
            model_refs={
                "optimizer": optimizer,
                "tester": str(model_refs.get("tester") or "") or None,
            },
            max_iterations=max_iterations,
            search_width=search_width,
            optimization_instruction=optimization_instruction,
            artifact_path=artifact_path,
            config=config,
            run_dir=run_dir,
            status=TaskStatus.CREATED.value,
            created_at=utcnow_iso(),
        )
        # run_dir 建好（引擎产出落点）
        Path(run_dir).mkdir(parents=True, exist_ok=True)
        self.store.create(task)
        # 进度树根注册（拉取 I4/tree + 推送 P2/P3 同源；基线缺省 None）
        return {"task_id": task_id, "status": TaskStatus.CREATED.value}

    # -- I3 list --

    def list(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        """``rsi.task.list``（web §6.2 六字段投影 + 可选过滤）。"""
        scenario_raw = params.get("scenario")
        artifact_type_raw = params.get("artifact_type")
        scenario: Scenario | None = None
        artifact_type: ArtifactType | None = None
        if scenario_raw:
            scenario, artifact_type = validate_scenario(
                str(scenario_raw), str(artifact_type_raw) if artifact_type_raw else None,
                artifact_type_required=False,
            )
            if scenario is Scenario.HARNESS and artifact_type is not None:
                raise RsiBadRequest("harness 过滤不接受 artifact_type")
        elif artifact_type_raw:
            try:
                artifact_type = ArtifactType(str(artifact_type_raw))
            except ValueError as exc:
                raise RsiBadRequest(f"artifact_type 非法: {artifact_type_raw}") from exc
        tasks = self.store.list(
            scenario=scenario.value if scenario else None,
            artifact_type=artifact_type.value if artifact_type else None,
        )
        return [task.list_projection() for task in tasks]

    # -- I4 get --

    def get(self, params: dict[str, Any], *, projector: Any, usage_recorder: Any, artifact_service: Any) -> dict[str, Any]:
        """``rsi.task.get``（web §6.3：config + progress + best_artifact + usage）。"""
        task_id = str(params.get("task_id") or "").strip()
        if not task_id:
            raise RsiBadRequest("task_id 必填")
        task = self.store.get(task_id)
        progress: dict[str, Any] | None = None
        try:
            progress = projector.derive_progress(task_id)
        except Exception:  # noqa: BLE001 - 进度投影失败不阻断详情
            progress = None
        usage = usage_recorder.usage_summary(task_id)
        best_artifact = artifact_service.best_artifact(task_id)
        return task.get_projection(progress=progress, usage=usage, best_artifact=best_artifact)

    # -- I5 delete --

    def delete(self, params: dict[str, Any]) -> dict[str, Any]:
        """``rsi.task.delete``（web §6.4，一致性规则 §8.2）。"""
        task_id = str(params.get("task_id") or "").strip()
        if not task_id:
            raise RsiBadRequest("task_id 必填")
        self.store.delete(task_id, forbid_running=True, forbid_active_artifact=True)
        return {"ok": True}

    # -- 训练控制（I6 start 主体；I7/I8/I9 中优先级预留） --

    def start(self, params: dict[str, Any], *, worker: Any) -> dict[str, Any]:
        """``rsi.training.start``（web §7.1）。状态机 + 入队；引擎装配看 adapter。"""
        task_id = str(params.get("task_id") or "").strip()
        if not task_id:
            raise RsiBadRequest("task_id 必填")
        self.store.get(task_id)  # 存在性校验 → TASK_NOT_FOUND
        status = worker.enqueue(task_id)
        return {"status": status}

    def pause(self, params: dict[str, Any], *, worker: Any) -> dict[str, Any]:
        """``rsi.training.pause``（中优先级 I7；协程衔接 TODO）。"""
        task_id = str(params.get("task_id") or "").strip()
        if not task_id:
            raise RsiBadRequest("task_id 必填")
        status = worker.cancel(task_id, "pause")
        return {"status": status}

    def resume(self, params: dict[str, Any], *, worker: Any) -> dict[str, Any]:
        """``rsi.training.resume``（中优先级 I8；fingerprint 校验在 C2）。"""
        task_id = str(params.get("task_id") or "").strip()
        if not task_id:
            raise RsiBadRequest("task_id 必填")
        status = worker.resume(task_id, fingerprint_check=True)
        return {"status": status}

    def terminate(self, params: dict[str, Any], *, worker: Any) -> dict[str, Any]:
        """``rsi.training.terminate``（中优先级 I9）。"""
        task_id = str(params.get("task_id") or "").strip()
        if not task_id:
            raise RsiBadRequest("task_id 必填")
        status = worker.cancel(task_id, "terminate")
        return {"status": status}


class RsiDatasetService:
    """``rsi.dataset.validate``（I1 主入口；真实校验委托 adapter，⚠️外部）。"""

    def __init__(self, adapter: Any = None) -> None:
        self.adapter = adapter

    def validate(self, params: dict[str, Any]) -> dict[str, Any]:
        """web §5.1 出参：{valid, sample_count, errors[]}。"""
        input_file = str(params.get("input_file") or "").strip()
        if not input_file:
            raise RsiBadRequest("input_file 必填")
        scenario = str(params.get("scenario") or "").strip()
        artifact_type = str(params.get("artifact_type") or "").strip() or None
        validate_scenario(
            scenario, artifact_type,
            artifact_type_required=(scenario == "ARTIFACT"),
        )
        path = Path(input_file).expanduser()
        if not path.is_file():
            raise RsiPathInvalid(f"本地路径不存在: {path}")
        result: RsiDatasetResult
        if self.adapter is not None and hasattr(self.adapter, "validate_input"):
            result = self.adapter.validate_input(str(path), scenario=scenario, artifact_type=artifact_type)
        else:
            from jiuwenswarm.agents.harness.common.rsi.adapter import default_validate_input

            result = default_validate_input(str(path))
        return result.to_dict()


class RsiReportService:
    """``rsi.report.get``（I10，⚠️外部：真值来源 C3 read_report / 事件投影）。"""

    def __init__(self, store: Any, projector: Any, usage_recorder: Any, artifact_service: Any) -> None:
        self.store = store
        self.projector = projector
        self.usage_recorder = usage_recorder
        self.artifact_service = artifact_service

    def get(self, params: dict[str, Any]) -> dict[str, Any]:
        """web §8.1 出参。未运行（无引擎 report）时提供任务级兜底投影。"""
        task_id = str(params.get("task_id") or "").strip()
        if not task_id:
            raise RsiBadRequest("task_id 必填")
        task = self.store.get(task_id)
        progress = self.projector.derive_progress(task_id)
        usage = self.usage_recorder.usage_summary(task_id)
        best_artifact = self.artifact_service.best_artifact(task_id)
        config = task.config or {}
        results = config.get("results") or {}
        return {
            "status": task.status,
            "best_score": results.get("best_score"),
            "baseline": progress.get("baseline"),
            "metrics": {
                "eval_passed": 0,
                "eval_total": 0,
                "pruned_count": 0,
                "iterations": progress.get("iteration") or 0,
            },
            "usage": usage,
            "best_artifact": best_artifact,
            "report_summary": "",
            "markdown": None,
        }


class RsiTreeService:
    """``rsi.tree.get``（I13，⚠️外部：真值来源事件投影 + C3 快照重建）。"""

    def __init__(self, projector: Any) -> None:
        self.projector = projector

    def get(self, params: dict[str, Any]) -> dict[str, Any]:
        task_id = str(params.get("task_id") or "").strip()
        if not task_id:
            raise RsiBadRequest("task_id 必填")
        return self.projector.derive_tree(task_id)


class RsiUsageService:
    """``rsi.usage.get``（I11，⚠️外部：usage 插桩后收尾；C1 单价预留）。"""

    def __init__(self, usage_recorder: Any) -> None:
        self.usage_recorder = usage_recorder

    def get(self, params: dict[str, Any]) -> dict[str, Any]:
        task_id = str(params.get("task_id") or "").strip()
        if not task_id:
            raise RsiBadRequest("task_id 必填")
        return self.usage_recorder.get(task_id)


class RsiArtifactDownloadService:
    """``rsi.artifact.download`` 定位（I12；下载通道复用 Gateway HTTP Range bridge）。"""

    def __init__(self, artifact_service: Any, store: Any) -> None:
        self.artifact_service = artifact_service
        self.store = store

    def locate(self, params: dict[str, Any]) -> dict[str, Any]:
        """返回可下载文件信息（zip path/kind/is_best）；HTTP 流由通道层完成。"""
        task_id = str(params.get("task_id") or "").strip()
        if not task_id:
            raise RsiBadRequest("task_id 必填")
        artifact_id = str(params.get("artifact_id") or "").strip() or None
        artifact = self.artifact_service.locate(task_id, artifact_id)
        # 消费成功 → 放行后续 delete（在用产物语义：下载即消费）
        if artifact_id is None or artifact.is_best:
            try:
                self.store.mark_active_ref_released(task_id)
            except Exception:  # noqa: BLE001
                pass
        return {
            "path": artifact.path,
            "kind": artifact.kind,
            "is_best": artifact.is_best,
            "filename": Path(artifact.path).name,
        }


def _positive_int(raw: Any, *, default: int, field: str) -> int:
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise RsiBadRequest(f"{field} 必须是正整数") from exc
    if value < 1:
        raise RsiBadRequest(f"{field} 必须是正整数")
    return value