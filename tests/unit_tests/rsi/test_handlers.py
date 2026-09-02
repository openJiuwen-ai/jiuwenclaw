# -*- coding: utf-8 -*-
"""RSI AgentServer 分发层 + Gateway 注册单测（B0/B2 + P1 推送）。"""
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from jiuwenswarm.agents.harness.common.rsi import build_rsi_service_context
from jiuwenswarm.agents.harness.common.rsi.errors import RsiPathInvalid
from jiuwenswarm.agents.harness.common.rsi.services import _ensure_provider_valid
from jiuwenswarm.common.schema.message import ReqMethod
from jiuwenswarm.server.rsi import RsiAgentServerHandlers

RSI_METHOD_NAMES = {
    "rsi.dataset.validate",
    "rsi.task.create",
    "rsi.task.list",
    "rsi.task.get",
    "rsi.task.delete",
    "rsi.training.start",
    "rsi.training.pause",
    "rsi.training.resume",
    "rsi.training.terminate",
    "rsi.report.get",
    "rsi.usage.get",
    "rsi.artifact.download",
    "rsi.tree.get",
}


class TestReqMethod:
    def test_13_rsi_methods_registered(self):
        present = {m.value for m in ReqMethod if m.value.startswith("rsi.")}
        assert present == RSI_METHOD_NAMES


class FakeRequest:
    def __init__(self, method, params=None):
        self.req_method = method
        self.params = params or {}


@pytest.fixture
def handlers():
    with tempfile.TemporaryDirectory() as tmp:
        ctx = build_rsi_service_context(Path(tmp))
        pushes = []
        h = RsiAgentServerHandlers(
            ctx,
            send_push=lambda msg: (pushes.append(msg), True)[1],
            harness_refs_provider=lambda: None,
        )
        yield h, ctx, pushes


class TestDispatch:
    def test_unknown_method(self, handlers):
        h, _, _ = handlers
        result = h.handle(FakeRequest(ReqMethod.MODELS_LIST))
        assert result["ok"] is False
        assert result["code"] == "BAD_REQUEST"

    def test_create_list_delete_roundtrip(self, handlers):
        h, ctx, _ = handlers
        result = h.handle(FakeRequest(ReqMethod.RSI_TASK_CREATE, {
            "scenario": "HARNESS", "name": "t1", "input_file": "C:/d.json",
            "model_refs": {"optimizer": "o", "tester": "e"},
        }))
        assert result["ok"]
        task_id = result["payload"]["task_id"]

        result = h.handle(FakeRequest(ReqMethod.RSI_TASK_LIST, {}))
        assert result["ok"] and len(result["payload"]) == 1

        result = h.handle(FakeRequest(ReqMethod.RSI_TASK_DELETE, {"task_id": task_id}))
        assert result["ok"]
        assert result["payload"] == {"ok": True}

    def test_dataset_validate_path_invalid(self, handlers):
        h, _, _ = handlers
        result = h.handle(FakeRequest(ReqMethod.RSI_DATASET_VALIDATE, {
            "input_file": "C:/missing.json", "scenario": "HARNESS",
        }))
        assert result["ok"] is False
        assert result["code"] == "PATH_INVALID"

    def test_dataset_validate_ok(self, handlers, tmp_path: Path):
        h, _, _ = handlers
        dataset = tmp_path / "dataset.json"
        dataset.write_text('{"cases": [{"case_id": "a"}, {"case_id": "b"}]}', encoding="utf-8")
        result = h.handle(FakeRequest(ReqMethod.RSI_DATASET_VALIDATE, {
            "input_file": str(dataset), "scenario": "HARNESS",
        }))
        assert result["ok"] is True
        assert result["payload"]["sample_count"] == 2
        assert result["payload"]["valid"] is True

    def test_provider_path_error_uses_matching_error_reason(self):
        result = SimpleNamespace(
            valid=False,
            errors=[
                {"code": "ARTIFACT_PATH_REQUIRED", "message": "artifact path required"},
                {"code": "PATH_INVALID", "message": "path is not readable"},
            ],
        )

        with pytest.raises(RsiPathInvalid, match="path is not readable"):
            _ensure_provider_valid(result)


class TestP1Push:
    def test_status_changed_push(self, handlers):
        h, ctx, pushes = handlers
        result = h.handle(FakeRequest(ReqMethod.RSI_TASK_CREATE, {
            "scenario": "HARNESS", "name": "t", "input_file": "C:/d.json",
            "model_refs": {"optimizer": "o", "tester": "e"},
        }))
        task_id = result["payload"]["task_id"]
        pushes.clear()
        ctx.store.update_status(task_id, ["CREATED"], "QUEUED", cause="start")
        ctx.store.update_status(task_id, ["QUEUED"], "RUNNING", cause="start")
        assert len(pushes) == 2
        first = pushes[0]
        assert first["payload"]["event_type"] == "rsi.training.status.changed"
        assert first["payload"]["task_id"] == task_id
        assert first["payload"]["old_status"] == "CREATED"
        assert first["payload"]["new_status"] == "QUEUED"


class TestTrainingControl:
    def test_pause_queued_semantics(self, handlers):
        h, ctx, _ = handlers
        result = h.handle(FakeRequest(ReqMethod.RSI_TASK_CREATE, {
            "scenario": "HARNESS", "name": "t", "input_file": "C:/d.json",
            "model_refs": {"optimizer": "o", "tester": "e"},
        }))
        task_id = result["payload"]["task_id"]
        # CREATED → pause 应冲突（适用 RUNNING/QUEUED）
        result = h.handle(FakeRequest(ReqMethod.RSI_TRAINING_PAUSE, {"task_id": task_id}))
        assert result["ok"] is False
        assert result["code"] == "TASK_STATE_CONFLICT"

    def test_terminate_created(self, handlers):
        h, ctx, _ = handlers
        result = h.handle(FakeRequest(ReqMethod.RSI_TASK_CREATE, {
            "scenario": "HARNESS", "name": "t", "input_file": "C:/d.json",
            "model_refs": {"optimizer": "o", "tester": "e"},
        }))
        task_id = result["payload"]["task_id"]
        result = h.handle(FakeRequest(ReqMethod.RSI_TRAINING_TERMINATE, {"task_id": task_id}))
        # 状态机允许 CREATED → TERMINATED
        assert result["ok"] is True
        assert result["payload"]["status"] == "TERMINATED"

    def test_start_requires_adapter(self, handlers):
        """无 adapter 注册时 start 仍入队；执行时 FAILED（引擎装配 ⚠️外部）。"""
        h, ctx, _ = handlers
        result = h.handle(FakeRequest(ReqMethod.RSI_TASK_CREATE, {
            "scenario": "HARNESS", "name": "t", "input_file": "C:/d.json",
            "model_refs": {"optimizer": "o", "tester": "e"},
        }))
        task_id = result["payload"]["task_id"]
        result = h.handle(FakeRequest(ReqMethod.RSI_TRAINING_START, {"task_id": task_id}))
        assert result["ok"] is True
        assert result["payload"]["status"] in {"QUEUED", "RUNNING"}
