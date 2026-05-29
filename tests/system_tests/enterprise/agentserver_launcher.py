# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Launch AgentServer for enterprise system tests with optional dependency stubs."""

from __future__ import annotations

import sys
import types
from contextvars import ContextVar
from enum import Enum
from types import SimpleNamespace


def _install_test_dependency_stubs() -> None:
    pypandoc = types.ModuleType("pypandoc")
    pypandoc.convert_file = lambda *args, **kwargs: None
    pypandoc.get_pandoc_version = lambda: "0.0.0"
    sys.modules["pypandoc"] = pypandoc


def _install_trafilatura_stub() -> None:
    trafilatura = types.ModuleType("trafilatura")
    trafilatura.extract = lambda *args, **kwargs: ""
    trafilatura.fetch_url = lambda *args, **kwargs: ""
    sys.modules["trafilatura"] = trafilatura


def _install_openjiuwen_deepsearch_stubs() -> None:
    def ensure_package(name: str) -> types.ModuleType:
        module = sys.modules.get(name)
        if module is None:
            module = types.ModuleType(name)
            module.__path__ = []
            sys.modules[name] = module
        return module

    for package_name in [
        "openjiuwen_deepsearch",
        "openjiuwen_deepsearch.config",
        "openjiuwen_deepsearch.framework",
        "openjiuwen_deepsearch.framework.openjiuwen",
        "openjiuwen_deepsearch.framework.openjiuwen.agent",
        "openjiuwen_deepsearch.utils",
        "openjiuwen_deepsearch.utils.log_utils",
    ]:
        ensure_package(package_name)

    config_module = types.ModuleType("openjiuwen_deepsearch.config.config")

    class Config:
        def __init__(self) -> None:
            self.agent_config = SimpleNamespace(
                model_dump=lambda: {
                    "llm_config": {"general": {}},
                    "web_search_engine_config": {},
                }
            )

    config_module.Config = Config
    sys.modules[config_module.__name__] = config_module

    method_module = types.ModuleType("openjiuwen_deepsearch.config.method")

    class ExecutionMethod(str, Enum):
        DEPENDENCY_DRIVING = "dependency_driving"
        PARALLEL = "parallel"

    method_module.ExecutionMethod = ExecutionMethod
    sys.modules[method_module.__name__] = method_module

    agent_factory_module = types.ModuleType(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.agent_factory"
    )

    class AgentFactory:
        @staticmethod
        def create_agent(*_args, **_kwargs):
            raise RuntimeError("openjiuwen_deepsearch is stubbed in enterprise system tests")

    agent_factory_module.AgentFactory = AgentFactory
    sys.modules[agent_factory_module.__name__] = agent_factory_module

    workflow_module = types.ModuleType(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.workflow"
    )
    workflow_module.parse_endnode_content = lambda _chunk_content: None
    sys.modules[workflow_module.__name__] = workflow_module

    log_common_module = types.ModuleType(
        "openjiuwen_deepsearch.utils.log_utils.log_common"
    )
    log_common_module.session_id_ctx = ContextVar("session_id", default="")
    sys.modules[log_common_module.__name__] = log_common_module

    log_manager_module = types.ModuleType(
        "openjiuwen_deepsearch.utils.log_utils.log_manager"
    )

    class LogManager:
        _initialized = False

        @classmethod
        def init(cls, *_args, **_kwargs) -> None:
            cls._initialized = True

    log_manager_module.LogManager = LogManager
    sys.modules[log_manager_module.__name__] = log_manager_module


def main() -> None:
    _install_test_dependency_stubs()
    _install_trafilatura_stub()
    _install_openjiuwen_deepsearch_stubs()
    from jiuwenclaw.app_agentserver import main as agentserver_main

    agentserver_main()


if __name__ == "__main__":
    main()
