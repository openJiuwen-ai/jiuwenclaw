# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Regression coverage for in-process DeepResearch TLS initialization."""

import asyncio
import json
import os
import time
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
from jiuwenclaw.agentserver.tools.deepresearch_task_manager import (
    DeepResearchTask,
    DeepResearchTaskManager,
    TaskStatus,
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
