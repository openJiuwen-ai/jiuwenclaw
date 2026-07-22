# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Regression coverage for in-process DeepResearch TLS initialization."""

import asyncio
import base64
import io
import json
import os
import time
import zipfile
from contextlib import asynccontextmanager, nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from openjiuwen_deepsearch.framework.openjiuwen.llm.llm_model_factory import (
    LLMModelFactory,
    LLMModelParams,
)

from jiuwenclaw.agentserver.tools import deepresearch_task_manager as manager_module
from jiuwenclaw.agentserver.tools import deepresearch_tools as dt
from jiuwenclaw.agentserver.tools import _deepresearch_tls as tls_module
from jiuwenclaw.agentserver.tools.deepresearch_task_manager import (
    DeepResearchTask,
    DeepResearchTaskManager,
    TaskStatus,
)
from jiuwenclaw.agentserver.tools.deepresearch_plugin import (
    styled_html_export as styled_export_module,
)


_TLS_ENV = {
    "LLM_SSL_VERIFY": "false",
    "LLM_SSL_CERT": "",
    "TOOL_SSL_VERIFY": "false",
    "TOOL_SSL_CERT": "",
}
_ORIGINAL_TLS_ENV = {
    "LLM_SSL_VERIFY": "external-llm-verify",
    "LLM_SSL_CERT": "external-llm-cert",
    "TOOL_SSL_VERIFY": "external-tool-verify",
    "TOOL_SSL_CERT": "external-tool-cert",
}


def _set_original_tls_env(monkeypatch):
    for key, value in _ORIGINAL_TLS_ENV.items():
        monkeypatch.setenv(key, value)


def _make_manager():
    manager = object.__new__(DeepResearchTaskManager)
    manager._tasks = {}
    manager._task_handles = {}
    return manager


def _base_config():
    return {
        "LLM_MODEL_NAME": "test-model",
        "LLM_MODEL_TYPE": "openai",
        "LLM_BASE_URL": "https://llm.example/v1",
        "LLM_API_KEY": "test-key",
        "WEB_SEARCH_ENGINE_NAME": "bocha",
        "WEB_SEARCH_API_KEY": "search-key",
        "WEB_SEARCH_URL": "",
        "MAX_WEB_SEARCH_RESULTS": "5",
        "OUTLINER_MAX_SECTION_NUM": "5",
        "WORKFLOW_HUMAN_IN_THE_LOOP": "False",
        "OUTLINE_INTERACTION_ENABLED": "False",
        "SOURCE_TRACER_INFER_SWITCHES": "False",
        "VLM_CHART_GENERATOR_ENABLE": "False",
        "VLM_CHART_GENERATOR_MAX_ITERATIONS": "1",
        "EXECUTION_METHOD": "parallel",
    }


def _styled_llm_config():
    return {
        "general": {
            "model_name": "test-model",
            "model_type": "openai",
            "base_url": "https://llm.example/v1",
            "api_key": bytearray(b"test-key"),
        }
    }


def _styled_bundle_payload():
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, "w") as archive:
        archive.writestr("report_bundle/report.html", "<html>styled</html>")
    return base64.b64encode(archive_buffer.getvalue()).decode("ascii")


def _patch_task_path_dependencies(
    monkeypatch,
    manager,
    observed_factory_tls,
    observed_tls,
    observed_model_verify_ssl,
):
    class FakeAgent:
        async def run(self, **_kwargs):
            observed_tls.append({key: os.environ.get(key) for key in _TLS_ENV})
            model = LLMModelFactory.get_model(LLMModelParams(
                model_provider="openai",
                api_key="test-key",
                api_base="https://llm.example/v1",
                timeout=600,
            ))
            observed_model_verify_ssl.append(model.model_client_config.verify_ssl)
            yield json.dumps({
                "agent": "reporter",
                "event": "done",
                "content": "report body",
            })

    class FakeAgentFactory:
        def create_agent(self, _agent_config):
            observed_factory_tls.append({
                key: os.environ.get(key) for key in _TLS_ENV
            })
            return FakeAgent()

    class FakeConfig:
        def __init__(self):
            self.agent_config = SimpleNamespace(
                model_dump=lambda: {
                    "llm_config": {},
                    "web_search_engine_config": {},
                }
            )

    monkeypatch.setattr(
        DeepResearchTaskManager,
        "_load_config",
        staticmethod(lambda: _base_config()),
    )
    monkeypatch.setattr(
        DeepResearchTaskManager,
        "_validate_config",
        staticmethod(lambda _config: (True, "")),
    )
    monkeypatch.setattr(manager_module, "Config", FakeConfig)
    monkeypatch.setattr(manager_module, "AgentFactory", FakeAgentFactory)
    monkeypatch.setattr(
        manager_module,
        "parse_endnode_content",
        lambda _chunk: "report body",
    )
    monkeypatch.setattr(
        manager_module,
        "get_effective_request_workspace_dir",
        lambda: "/tmp/deepresearch-tls-test",
    )
    monkeypatch.setattr(manager, "_log_capture_scope", lambda _task_id: nullcontext())
    monkeypatch.setattr(
        manager,
        "_write_report_artifacts",
        AsyncMock(return_value={"md": "/tmp/deepresearch-tls-test/report.md"}),
    )
    monkeypatch.setattr(manager, "_notify_completion", AsyncMock())


@pytest.mark.asyncio
async def test_styled_tls_scope_preserves_concurrent_external_update(monkeypatch):
    monkeypatch.setenv("LLM_SSL_VERIFY", "ambient")
    monkeypatch.setattr(
        dt,
        "_build_bridge_env",
        lambda _source: {"LLM_SSL_VERIFY": "resolved-by-bridge"},
    )
    entry_started = asyncio.Event()
    allow_entry = asyncio.Event()

    @asynccontextmanager
    async def fake_context_factory(_llm_config):
        assert os.environ.get("LLM_SSL_VERIFY") == "resolved-by-bridge"
        entry_started.set()
        await allow_entry.wait()
        yield "runtime-llm"

    async def use_context():
        async with dt._scoped_report_style_llm_context(
            fake_context_factory, {"general": {}}
        ):
            assert os.environ.get("LLM_SSL_VERIFY") == "external-update"

    task = asyncio.create_task(use_context())
    await asyncio.wait_for(entry_started.wait(), timeout=0.1)
    os.environ["LLM_SSL_VERIFY"] = "external-update"
    allow_entry.set()
    await asyncio.wait_for(task, timeout=0.1)

    assert os.environ.get("LLM_SSL_VERIFY") == "external-update"


@pytest.mark.asyncio
@pytest.mark.parametrize("task_path", ["background", "blocking"])
async def test_task_manager_paths_scope_tls_to_agent_initialization(
    monkeypatch, task_path
):
    _set_original_tls_env(monkeypatch)
    observed_factory_tls = []
    observed_tls = []
    observed_model_verify_ssl = []
    manager = _make_manager()
    _patch_task_path_dependencies(
        monkeypatch,
        manager,
        observed_factory_tls,
        observed_tls,
        observed_model_verify_ssl,
    )

    if task_path == "background":
        task = DeepResearchTask(
            task_id="task-1",
            query="query",
            file_name="report",
            status=TaskStatus.RUNNING,
            created_at=time.time(),
        )
        manager._tasks[task.task_id] = task
        await manager._execute_task(task.task_id, task.query, task.file_name)
        assert task.status == TaskStatus.COMPLETED
    else:
        result = await manager.run_task_direct("query", "report")
        assert "/tmp/deepresearch-tls-test/report.md" in result

    assert observed_factory_tls == [_TLS_ENV]
    assert observed_tls == [_TLS_ENV]
    assert observed_model_verify_ssl == [False]
    assert {key: os.environ.get(key) for key in _TLS_ENV} == _ORIGINAL_TLS_ENV


@pytest.mark.asyncio
async def test_task_manager_agent_initialization_error_restores_tls(monkeypatch):
    _set_original_tls_env(monkeypatch)
    observed_tls = []
    manager = _make_manager()

    class FailingAgent:
        async def run(self, **_kwargs):
            observed_tls.append({key: os.environ.get(key) for key in _TLS_ENV})
            raise RuntimeError("agent initialization failed")
            yield  # pragma: no cover

    monkeypatch.setattr(
        manager_module,
        "AgentFactory",
        lambda: SimpleNamespace(create_agent=lambda _config: FailingAgent()),
    )

    with pytest.raises(RuntimeError, match="agent initialization failed"):
        await manager._run_jiuwen_workflow("query", {"llm_config": {}}, "")

    assert observed_tls == [_TLS_ENV]
    assert {key: os.environ.get(key) for key in _TLS_ENV} == _ORIGINAL_TLS_ENV


@pytest.mark.asyncio
async def test_task_manager_agent_initialization_cancellation_restores_tls(monkeypatch):
    _set_original_tls_env(monkeypatch)
    observed_tls = []
    initialization_started = asyncio.Event()
    manager = _make_manager()

    class BlockingAgent:
        async def run(self, **_kwargs):
            observed_tls.append({key: os.environ.get(key) for key in _TLS_ENV})
            initialization_started.set()
            await asyncio.Event().wait()
            yield  # pragma: no cover

    monkeypatch.setattr(
        manager_module,
        "AgentFactory",
        lambda: SimpleNamespace(create_agent=lambda _config: BlockingAgent()),
    )

    task = asyncio.create_task(
        manager._run_jiuwen_workflow("query", {"llm_config": {}}, "")
    )
    await asyncio.wait_for(initialization_started.wait(), timeout=0.1)
    task.cancel()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=0.1)

    assert observed_tls == [_TLS_ENV]
    assert {key: os.environ.get(key) for key in _TLS_ENV} == _ORIGINAL_TLS_ENV


@pytest.mark.asyncio
@pytest.mark.parametrize("initial_verify", ["true", None])
async def test_write_report_artifacts_scopes_real_styled_context_tls(
    monkeypatch, tmp_path, initial_verify
):
    if initial_verify is None:
        monkeypatch.delenv("LLM_SSL_VERIFY", raising=False)
    else:
        monkeypatch.setenv("LLM_SSL_VERIFY", initial_verify)
    observed_verify_ssl = []

    async def fake_stylize(_final_result, llm):
        observed_verify_ssl.append(llm["model"].model_client_config.verify_ssl)
        await asyncio.sleep(0.02)
        return SimpleNamespace(
            convert_content=_styled_bundle_payload(),
            style_status="styled",
            style_applied=True,
        )

    monkeypatch.setattr(styled_export_module, "stylize_report", fake_stylize)

    async def write_report(index):
        return await DeepResearchTaskManager._write_report_artifacts(
            {"response_content": "# report"},
            f"report-{index}",
            str(tmp_path),
            task_id=f"task-{index}",
            llm_config=_styled_llm_config(),
        )

    artifacts = await asyncio.wait_for(
        asyncio.gather(write_report(1), write_report(2)),
        timeout=1,
    )

    assert observed_verify_ssl == [False, False]
    assert all("html" in item for item in artifacts)
    assert all(os.path.isfile(item["html"]) for item in artifacts)
    if initial_verify is None:
        assert "LLM_SSL_VERIFY" not in os.environ
    else:
        assert os.environ.get("LLM_SSL_VERIFY") == initial_verify


@pytest.mark.asyncio
async def test_export_styled_html_context_entry_uses_shared_tls_mutex(
    monkeypatch, tmp_path
):
    context_entered = asyncio.Event()

    @asynccontextmanager
    async def fake_report_style_context(_llm_config):
        context_entered.set()
        yield "runtime-llm"

    async def fake_stylize(_final_result, _llm):
        return SimpleNamespace(
            convert_content=_styled_bundle_payload(),
            style_status="styled",
            style_applied=True,
        )

    monkeypatch.setattr(
        styled_export_module,
        "report_style_llm_context",
        fake_report_style_context,
    )
    monkeypatch.setattr(styled_export_module, "stylize_report", fake_stylize)
    html_path = tmp_path / "report.html"

    async with tls_module.scoped_deepresearch_tls_env(_TLS_ENV):
        export_task = asyncio.create_task(styled_export_module.export_styled_html(
            {"response_content": "# report"},
            _styled_llm_config(),
            html_path=html_path,
        ))
        await asyncio.sleep(0.02)
        assert context_entered.is_set() is False

    await asyncio.wait_for(export_task, timeout=0.1)
    assert context_entered.is_set() is True
    assert html_path.read_text(encoding="utf-8") == "<html>styled</html>"


class _IteratorPrimaryError(RuntimeError):
    pass


class _IteratorCleanupError(RuntimeError):
    pass


class _TrackingAsyncIterator:
    def __init__(self, values=(), *, first_error=None, close_error=None):
        self._values = iter(values)
        self._first_error = first_error
        self._close_error = close_error
        self._next_calls = 0
        self.close_calls = 0
        self.waiting = asyncio.Event()

    def __aiter__(self):
        return self

    async def __anext__(self):
        self._next_calls += 1
        if self._next_calls == 1 and self._first_error is not None:
            raise self._first_error
        try:
            return next(self._values)
        except StopIteration:
            raise StopAsyncIteration from None

    async def aclose(self):
        self.close_calls += 1
        if self._close_error is not None:
            raise self._close_error


def _patch_workflow_iterator(monkeypatch, source):
    agent = SimpleNamespace(run=lambda **_kwargs: source)
    factory = SimpleNamespace(create_agent=lambda _config: agent)
    monkeypatch.setattr(manager_module, "AgentFactory", lambda: factory)


@pytest.mark.asyncio
async def test_task_manager_closes_workflow_iterator_after_json_error(monkeypatch):
    source = _TrackingAsyncIterator(["not-json"])
    _patch_workflow_iterator(monkeypatch, source)
    manager = _make_manager()

    with pytest.raises(json.JSONDecodeError):
        await manager._run_jiuwen_workflow("query", {"llm_config": {}}, "")

    assert source.close_calls == 1


@pytest.mark.asyncio
async def test_task_manager_closes_workflow_iterator_after_progress_error(monkeypatch):
    source = _TrackingAsyncIterator([
        json.dumps({"agent": "outline", "event": "start", "content": "outline"})
    ])
    _patch_workflow_iterator(monkeypatch, source)
    manager = _make_manager()
    monkeypatch.setattr(
        manager,
        "_send_progress_push",
        AsyncMock(side_effect=RuntimeError("progress failed")),
    )

    with pytest.raises(RuntimeError, match="progress failed"):
        await manager._run_jiuwen_workflow("query", {"llm_config": {}}, "")

    assert source.close_calls == 1


@pytest.mark.asyncio
async def test_task_manager_closes_workflow_iterator_after_body_cancellation(
    monkeypatch,
):
    source = _TrackingAsyncIterator([
        json.dumps({"agent": "outline", "event": "start", "content": "outline"})
    ])
    _patch_workflow_iterator(monkeypatch, source)
    manager = _make_manager()
    push_started = asyncio.Event()

    async def blocking_progress_push(*_args, **_kwargs):
        push_started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(manager, "_send_progress_push", blocking_progress_push)
    task = asyncio.create_task(
        manager._run_jiuwen_workflow("query", {"llm_config": {}}, "")
    )
    await asyncio.wait_for(push_started.wait(), timeout=0.1)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=0.1)

    assert source.close_calls == 1


@pytest.mark.asyncio
async def test_scoped_tls_iterator_closes_after_first_item_error():
    source = _TrackingAsyncIterator(first_error=_IteratorPrimaryError("first failed"))
    wrapped = tls_module.iterate_with_scoped_tls_initialization(source, _TLS_ENV)

    with pytest.raises(_IteratorPrimaryError, match="first failed"):
        await anext(wrapped)

    assert source.close_calls == 1


@pytest.mark.asyncio
async def test_scoped_tls_iterator_closes_after_consumer_error():
    source = _TrackingAsyncIterator([1, 2])
    wrapped = tls_module.iterate_with_scoped_tls_initialization(source, _TLS_ENV)

    with pytest.raises(RuntimeError, match="consumer failed"):
        try:
            async for _item in wrapped:
                raise RuntimeError("consumer failed")
        finally:
            await wrapped.aclose()

    assert source.close_calls == 1


@pytest.mark.asyncio
async def test_scoped_tls_iterator_closes_after_break_and_explicit_close():
    source = _TrackingAsyncIterator([1, 2])
    wrapped = tls_module.iterate_with_scoped_tls_initialization(source, _TLS_ENV)

    async for _item in wrapped:
        break
    await wrapped.aclose()

    assert source.close_calls == 1


@pytest.mark.asyncio
async def test_scoped_tls_iterator_closes_after_cancellation():
    class BlockingIterator(_TrackingAsyncIterator):
        async def __anext__(self):
            self._next_calls += 1
            if self._next_calls == 1:
                return 1
            self.waiting.set()
            await asyncio.Event().wait()

    source = BlockingIterator()
    wrapped = tls_module.iterate_with_scoped_tls_initialization(source, _TLS_ENV)

    async def consume():
        async for _item in wrapped:
            pass

    task = asyncio.create_task(consume())
    await asyncio.wait_for(source.waiting.wait(), timeout=0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=0.1)

    assert source.close_calls == 1


@pytest.mark.asyncio
async def test_scoped_tls_iterator_empty_stream_propagates_cleanup_error():
    source = _TrackingAsyncIterator(close_error=_IteratorCleanupError("close failed"))
    wrapped = tls_module.iterate_with_scoped_tls_initialization(source, _TLS_ENV)

    with pytest.raises(_IteratorCleanupError, match="close failed"):
        await anext(wrapped)

    assert source.close_calls == 1


@pytest.mark.asyncio
async def test_scoped_tls_iterator_cleanup_does_not_mask_first_item_error():
    source = _TrackingAsyncIterator(
        first_error=_IteratorPrimaryError("first failed"),
        close_error=_IteratorCleanupError("close failed"),
    )
    wrapped = tls_module.iterate_with_scoped_tls_initialization(source, _TLS_ENV)

    with pytest.raises(_IteratorPrimaryError, match="first failed"):
        await anext(wrapped)

    assert source.close_calls == 1
