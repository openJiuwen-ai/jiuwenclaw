# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path


RUNTIME_DIR = Path(__file__).parents[3] / "jiuwenswarm" / "runtime"
PROJECT_ROOT = Path(__file__).parents[3]
FORBIDDEN_IMPORT_PREFIXES = (
    "jiuwenswarm.gateway",
    "jiuwenswarm.server.agent_ws_server",
    "websockets",
)
FORBIDDEN_RUNTIME_TYPES = {"CliAgentPool", "CoreAgentBackend"}


def _runtime_sources() -> list[Path]:
    return sorted(RUNTIME_DIR.rglob("*.py"))


def _runtime_dependency_sources() -> list[Path]:
    package = PROJECT_ROOT / "jiuwenswarm"
    sources = list(_runtime_sources())
    for folder in ("cron", "xiaoyi_phone_tools"):
        sources.extend(
            (package / "agents" / "harness" / "common" / "tools" / folder).glob(
                "*.py"
            )
        )
    sources.append(
        package / "server" / "runtime" / "agent_adapter" / "interface_deep.py"
    )
    return sorted(set(sources))


def test_runtime_public_api_has_no_transport_imports() -> None:
    violations: list[str] = []
    for source in _runtime_sources():
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            imported: list[str] = []
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported = [node.module]
            for module in imported:
                if module.startswith(FORBIDDEN_IMPORT_PREFIXES):
                    violations.append(f"{source.name}:{node.lineno}: {module}")

    assert violations == []


def test_runtime_execution_dependencies_have_no_transport_imports() -> None:
    violations: list[str] = []
    for source in _runtime_dependency_sources():
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            for module in modules:
                if module.startswith(FORBIDDEN_IMPORT_PREFIXES) or module.startswith(
                    "jiuwenswarm.server.gateway_push"
                ):
                    violations.append(f"{source}:{node.lineno}: {module}")
    assert violations == []


def test_runtime_start_does_not_load_gateway_or_agentserver_modules() -> None:
    script = """
import asyncio
import sys
from jiuwenswarm.runtime import AgentRuntime

async def main():
    runtime = AgentRuntime()
    try:
        await runtime.start()
        names = sorted(
            name for name in sys.modules
            if name.startswith('jiuwenswarm.gateway')
            or name.startswith('jiuwenswarm.server.agent_ws_server')
        )
        print('TRANSPORT_MODULES=' + repr(names))
    finally:
        await runtime.close()

asyncio.run(main())
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "TRANSPORT_MODULES=[]" in result.stdout


def test_runtime_does_not_reintroduce_alternate_agent_implementations() -> None:
    declared: set[str] = set()
    for source in _runtime_sources():
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        declared.update(
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        )

    assert declared.isdisjoint(FORBIDDEN_RUNTIME_TYPES)
