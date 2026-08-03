# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import importlib.util
import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jiuwenclaw.local_env_config import (
    ENV_CONFIG_DICT,
    bind_agent_env_ns,
    bind_task_env_overlay,
    clear_staged_env,
    reset_agent_env_ns,
    reset_local_env_state_for_tests,
    reset_task_env_overlay,
    stage_env_overrides,
)

_TOOLS_PKG = "jiuwenclaw.agentserver.tools"
_TOOLS_MODULE = f"{_TOOLS_PKG}.task_tools"
_TOOLS_PKG_DIR = (
    Path(__file__).resolve().parents[3] / "jiuwenclaw" / "agentserver" / "tools"
)

task_tools = None  # populated by module fixture below


def _load_task_tools_module():
    """Load task_tools without executing tools/__init__.py (heavy optional deps)."""
    pkg_name = _TOOLS_PKG
    if pkg_name not in sys.modules:
        tools_pkg = types.ModuleType(pkg_name)
        tools_pkg.__path__ = [str(_TOOLS_PKG_DIR)]
        tools_pkg.AddMemoryRequest = MagicMock
        tools_pkg.JSONFileConnector = MagicMock
        tools_pkg.TaskMemoryService = MagicMock
        tools_pkg.ce_config = MagicMock()

        def _tool_decorator(*args, **kwargs):
            if args and callable(args[0]):
                return args[0]

            def _wrap(fn):
                return fn

            return _wrap

        tools_pkg.tool = _tool_decorator
        sys.modules[pkg_name] = tools_pkg

    module_name = _TOOLS_MODULE
    if module_name in sys.modules:
        module = sys.modules[module_name]
        importlib.reload(module)
        return module

    module_path = _TOOLS_PKG_DIR / "task_tools.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module", autouse=True)
def _task_tools_module():
    """Isolate stub tools package; restore real package after this file's tests."""
    global task_tools
    prefix = _TOOLS_PKG + "."
    snapshot = {
        k: sys.modules[k]
        for k in list(sys.modules)
        if k == _TOOLS_PKG or k.startswith(prefix)
    }
    for key in snapshot:
        del sys.modules[key]
    task_tools = _load_task_tools_module()
    try:
        yield task_tools
    finally:
        for key in list(sys.modules):
            if key == _TOOLS_PKG or key.startswith(prefix):
                del sys.modules[key]
        sys.modules.update(snapshot)


@pytest.fixture(autouse=True)
def _reset_task_tools_state():
    saved_environ = dict(os.environ)
    reset_local_env_state_for_tests()
    ENV_CONFIG_DICT.clear()
    clear_staged_env()
    task_tools.clear_task_memory_service(clear_all=True)
    yield
    reset_local_env_state_for_tests()
    ENV_CONFIG_DICT.clear()
    clear_staged_env()
    task_tools.clear_task_memory_service(clear_all=True)
    os.environ.clear()
    os.environ.update(saved_environ)


class TestResolveTaskMemoryConfig:
    @staticmethod
    def test_read_env_overlay_for_embed_api_key():
        ENV_CONFIG_DICT["EMBED_API_KEY"] = "active-key"
        stage_env_overrides({"EMBED_API_KEY": "staged-key"})
        token = bind_task_env_overlay({"EMBED_API_KEY": "overlay-key"})
        try:
            cfg = {
                "embed": {},
                "react": {"model_name": "gpt-4"},
            }
            resolved = task_tools.resolve_task_memory_config(cfg)
            assert resolved.api_key == "overlay-key"
        finally:
            reset_task_env_overlay(token)

    @staticmethod
    def test_task_memory_yaml_overrides_embed_env():
        ENV_CONFIG_DICT["EMBED_API_KEY"] = "env-key"
        cfg = {
            "task_memory": {"api_key": "task-key", "llm_model": "task-llm"},
            "embed": {"embed_api_key": "yaml-embed"},
            "react": {"model_name": "gpt-4"},
        }
        resolved = task_tools.resolve_task_memory_config(cfg)
        assert resolved.api_key == "task-key"
        assert resolved.llm_model == "task-llm"


class TestTaskMemoryFingerprint:
    @staticmethod
    def test_fingerprint_changes_when_embed_model_changes():
        cfg_a = {
            "embed": {"embed_api_key": "k", "embed_model": "m1", "embed_base_url": "u"},
            "react": {"model_name": "llm"},
        }
        cfg_b = {
            "embed": {"embed_api_key": "k", "embed_model": "m2", "embed_base_url": "u"},
            "react": {"model_name": "llm"},
        }
        assert task_tools.task_memory_config_fingerprint(cfg_a) != task_tools.task_memory_config_fingerprint(cfg_b)


class TestTaskMemoryServiceCache:
    @staticmethod
    def test_clear_task_memory_service_resets_cache():
        cfg = {
            "embed": {
                "embed_api_key": "key1",
                "embed_model": "embed-a",
                "embed_base_url": "http://a",
            },
            "react": {"model_name": "llm-a"},
        }
        mock_svc = MagicMock(name="TaskMemoryServiceInstance")
        mock_svc2 = MagicMock(name="TaskMemoryServiceInstance2")
        mock_cls = MagicMock(side_effect=[mock_svc, mock_svc2])

        with patch("jiuwenclaw.config.get_config", return_value=cfg):
            with patch.object(task_tools, "TaskMemoryService", mock_cls):
                first = task_tools.get_task_memory_service(
                    service_id="default", agent_id="default"
                )
                assert first is mock_svc
                assert task_tools.task_memory_service_cache_size() == 1
                task_tools.clear_task_memory_service(
                    service_id="default", agent_id="default"
                )
                assert task_tools.task_memory_service_cache_size() == 0
                second = task_tools.get_task_memory_service(
                    service_id="default", agent_id="default"
                )
                assert second is mock_svc2
                assert mock_cls.call_count == 2

    @staticmethod
    def test_same_fingerprint_different_tenants_are_isolated():
        cfg = {
            "embed": {
                "embed_api_key": "same-key",
                "embed_model": "embed-a",
                "embed_base_url": "http://a",
            },
            "react": {"model_name": "llm-a"},
        }
        mock_svc_a = MagicMock(name="TaskMemoryServiceA")
        mock_svc_b = MagicMock(name="TaskMemoryServiceB")
        mock_cls = MagicMock(side_effect=[mock_svc_a, mock_svc_b])

        with patch("jiuwenclaw.config.get_config", return_value=cfg):
            with patch.object(task_tools, "TaskMemoryService", mock_cls):
                a = task_tools.get_task_memory_service(
                    service_id="svc", agent_id="office"
                )
                b = task_tools.get_task_memory_service(
                    service_id="svc", agent_id="assistant"
                )
                assert a is mock_svc_a
                assert b is mock_svc_b
                assert a is not b
                assert task_tools.task_memory_service_cache_size() == 2

                task_tools.clear_task_memory_service(
                    service_id="svc", agent_id="office"
                )
                assert task_tools.task_memory_service_cache_size() == 1
                assert (
                    task_tools.get_task_memory_service(
                        service_id="svc", agent_id="assistant"
                    )
                    is mock_svc_b
                )

    @staticmethod
    def test_get_service_requires_scope():
        with pytest.raises(TypeError, match="tenant scope is required"):
            task_tools.get_task_memory_service()

    @staticmethod
    def test_get_service_uses_bound_env_ns():
        cfg = {
            "embed": {
                "embed_api_key": "key1",
                "embed_model": "embed-a",
                "embed_base_url": "http://a",
            },
            "react": {"model_name": "llm-a"},
        }
        mock_svc = MagicMock(name="TaskMemoryServiceBound")
        tok = bind_agent_env_ns("sid1", "aid1")
        try:
            with patch("jiuwenclaw.config.get_config", return_value=cfg):
                with patch.object(task_tools, "TaskMemoryService", return_value=mock_svc):
                    assert task_tools.get_task_memory_service() is mock_svc
        finally:
            reset_agent_env_ns(tok)

    @staticmethod
    def test_get_service_pools_multiple_fingerprints_without_thrashing():
        cfg_a = {
            "embed": {
                "embed_api_key": "key-a",
                "embed_model": "embed-a",
                "embed_base_url": "http://a",
            },
            "react": {"model_name": "llm-a"},
        }
        cfg_b = {
            "embed": {
                "embed_api_key": "key-b",
                "embed_model": "embed-a",
                "embed_base_url": "http://a",
            },
            "react": {"model_name": "llm-a"},
        }
        mock_svc_a = MagicMock(name="TaskMemoryServiceA")
        mock_svc_b = MagicMock(name="TaskMemoryServiceB")
        mock_cls = MagicMock(side_effect=[mock_svc_a, mock_svc_b])

        with patch("jiuwenclaw.config.get_config", side_effect=[cfg_a, cfg_b, cfg_a, cfg_b]):
            with patch.object(task_tools, "TaskMemoryService", mock_cls):
                first_a = task_tools.get_task_memory_service(
                    service_id="default", agent_id="default"
                )
                first_b = task_tools.get_task_memory_service(
                    service_id="default", agent_id="default"
                )
                second_a = task_tools.get_task_memory_service(
                    service_id="default", agent_id="default"
                )
                second_b = task_tools.get_task_memory_service(
                    service_id="default", agent_id="default"
                )

                assert first_a is mock_svc_a
                assert first_b is mock_svc_b
                assert second_a is mock_svc_a
                assert second_b is mock_svc_b
                assert mock_cls.call_count == 2
                assert task_tools.task_memory_service_cache_size() == 2

    @staticmethod
    def test_get_service_rebuilds_when_fingerprint_changes():
        cfg = {
            "embed": {
                "embed_api_key": "key1",
                "embed_model": "embed-a",
                "embed_base_url": "http://a",
            },
            "react": {"model_name": "llm-a"},
        }
        cfg_changed = {
            "embed": {
                "embed_api_key": "key2",
                "embed_model": "embed-a",
                "embed_base_url": "http://a",
            },
            "react": {"model_name": "llm-a"},
        }
        mock_svc = MagicMock(name="TaskMemoryServiceInstance")
        mock_svc2 = MagicMock(name="TaskMemoryServiceInstance2")

        with patch("jiuwenclaw.config.get_config", side_effect=[cfg, cfg_changed]):
            with patch.object(
                task_tools, "TaskMemoryService", side_effect=[mock_svc, mock_svc2]
            ):
                first = task_tools.get_task_memory_service(
                    service_id="default", agent_id="default"
                )
                assert first is mock_svc

                second = task_tools.get_task_memory_service(
                    service_id="default", agent_id="default"
                )
                assert second is mock_svc2
                assert second is not first
                assert task_tools.task_memory_service_cache_size() == 2


class TestTaskDataPath:
    def test_task_data_path_uses_tenant_workspace(self, tmp_path: Path, monkeypatch):
        from jiuwenclaw.local_env_config import bind_agent_env_ns, reset_agent_env_ns

        monkeypatch.setattr(
            "jiuwenclaw.utils.get_user_workspace_dir",
            lambda: tmp_path,
        )
        token = bind_agent_env_ns("default", "office")
        try:
            assert task_tools._get_task_data_path() == str(
                tmp_path
                / "service_default"
                / "agent_office"
                / "agent"
                / "jiuwenclaw_workspace"
                / "task-data.json"
            )
        finally:
            reset_agent_env_ns(token)
