from __future__ import annotations

import ast
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
GENERIC_ROOTS = (
    REPOSITORY_ROOT / "jiuwenswarm/common/reverse_rpc",
    REPOSITORY_ROOT / "jiuwenswarm/server/reverse_rpc",
    REPOSITORY_ROOT / "jiuwenswarm/gateway/reverse_rpc",
)
PRODUCTION_CAPABILITY_ALLOWLIST: frozenset[str] = frozenset()

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


def test_production_reverse_rpc_capabilities_are_explicitly_allowlisted() -> None:
    violations: list[str] = []
    production_root = REPOSITORY_ROOT / "jiuwenswarm"
    for path in production_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            is_capability_spec = (
                isinstance(func, ast.Name) and func.id == "CapabilitySpec"
            ) or (
                isinstance(func, ast.Attribute) and func.attr == "CapabilitySpec"
            )
            if not is_capability_spec:
                continue
            method_node = next(
                (keyword.value for keyword in node.keywords if keyword.arg == "method"),
                None,
            )
            if not isinstance(method_node, ast.Constant) or not isinstance(
                method_node.value, str
            ):
                violations.append(
                    f"{path}:{node.lineno}:CapabilitySpec method must be a string literal"
                )
                continue
            if method_node.value not in PRODUCTION_CAPABILITY_ALLOWLIST:
                violations.append(f"{path}:{node.lineno}:{method_node.value}")
    assert violations == []
