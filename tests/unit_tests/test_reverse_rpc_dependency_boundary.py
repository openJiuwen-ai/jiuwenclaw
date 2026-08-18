from __future__ import annotations

import ast
from pathlib import Path


GENERIC_ROOTS = (
    Path("jiuwenswarm/common/reverse_rpc"),
    Path("jiuwenswarm/server/reverse_rpc"),
    Path("jiuwenswarm/gateway/reverse_rpc"),
)

FORBIDDEN_IMPORT_PARTS = (
    "common.schema.agent",
    "device_rpc",
    "gui_rpc",
    "xiaoyi",
    "invocation_context",
    "request_context",
    "channel_manager",
)


def test_reverse_rpc_core_has_no_business_imports() -> None:
    violations: list[str] = []
    for root in GENERIC_ROOTS:
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                modules: list[str] = []
                if isinstance(node, ast.Import):
                    modules.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    modules.append(node.module or "")
                for module in modules:
                    if any(part in module.lower() for part in FORBIDDEN_IMPORT_PARTS):
                        violations.append(f"{path}:{node.lineno}:{module}")
    assert violations == []


def test_reverse_rpc_core_contains_no_business_method_names() -> None:
    forbidden = (
        "xiaoyi.device",
        "xiaoyi.gui",
        "device_command",
        "gui_rpc",
    )
    violations: list[str] = []
    for root in GENERIC_ROOTS:
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8").lower()
            for marker in forbidden:
                if marker in text:
                    violations.append(f"{path}:{marker}")
    assert violations == []


def test_no_production_reverse_rpc_capability_is_registered() -> None:
    violations: list[str] = []
    for path in Path("jiuwenswarm").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name) and func.id == "CapabilitySpec":
                violations.append(f"{path}:{node.lineno}")
            elif isinstance(func, ast.Attribute) and func.attr == "CapabilitySpec":
                violations.append(f"{path}:{node.lineno}")
    assert violations == []
