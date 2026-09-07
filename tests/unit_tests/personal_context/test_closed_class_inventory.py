"""Clean-cut checks for the single JiuwenSwarm PersonalContext host class."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).parents[3]
HOST = ROOT / "jiuwenswarm" / "server" / "personal_context"
LEGACY_PACKAGE = ROOT / "jiuwenswarm" / "server" / "proactive_harness"


def _classes(path: Path) -> set[str]:
    result: set[str] = set()
    for file in sorted(path.rglob("*.py")):
        tree = ast.parse(file.read_text(encoding="utf-8"))
        result.update(
            node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
        )
    return result


def test_jiuwen_swarm_declares_only_the_host_class() -> None:
    assert _classes(HOST) == {"PersonalContextHostAPI"}


def test_legacy_host_package_is_removed() -> None:
    assert not any(LEGACY_PACKAGE.rglob("*.py"))


def test_host_has_no_legacy_transport_imports() -> None:
    source = "\n".join(file.read_text(encoding="utf-8") for file in HOST.rglob("*.py"))
    for forbidden in (
        "ProactiveHarness",
        "ProactiveContextService",
        "proactive_harness",
        "open_description_reader",
        "FastAPI",
        "uvicorn",
    ):
        assert forbidden not in source
