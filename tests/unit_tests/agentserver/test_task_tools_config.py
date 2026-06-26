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
    bind_task_env_overlay,
    clear_staged_env,
    reset_task_env_overlay,
    stage_env_overrides,
)


def _load_task_tools_module():
    """Load task_tools without executing tools/__init__.py (heavy optional deps)."""
    pkg_name = "jiuwenclaw.agentserver.tools"
    if pkg_name not in sys.modules:
        tools_pkg = types.ModuleType(pkg_name)
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

    module_name = f"{pkg_name}.task_tools"
    if module_name in sys.modules:
        return sys.modules[module_name]

    module_path = (
        Path(__file__).resolve().parents[3]
        / "jiuwenclaw"
        / "agentserver"
        / "tools"
        / "task_tools.py"
    )
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


task_tools = _load_task_tools_module()


@pytest.fixture(autouse=True)
def _reset_task_tools_state():
    saved_environ = dict(os.environ)
    ENV_CONFIG_DICT.clear()
    clear_staged_env()
    task_tools.clear_task_memory_service()
    yield
    ENV_CONFIG_DICT.clear()
    clear_staged_env()
    task_tools.clear_task_memory_service()
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
        os.environ["EMBED_API_KEY"] = "env-key"
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
                first = task_tools.get_task_memory_service()
                assert first is mock_svc
                assert task_tools.task_memory_service_cache_size() == 1
                task_tools.clear_task_memory_service()
                assert task_tools.task_memory_service_cache_size() == 0
                second = task_tools.get_task_memory_service()
                assert second is mock_svc2
                assert mock_cls.call_count == 2

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
                first_a = task_tools.get_task_memory_service()
                first_b = task_tools.get_task_memory_service()
                second_a = task_tools.get_task_memory_service()
                second_b = task_tools.get_task_memory_service()

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
                first = task_tools.get_task_memory_service()
                assert first is mock_svc

                second = task_tools.get_task_memory_service()
                assert second is mock_svc2
                assert second is not first
                assert task_tools.task_memory_service_cache_size() == 2
