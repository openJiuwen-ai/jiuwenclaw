#!/usr/bin/env python3
"""Probe business repo-root Python/Node toolchain for Aidlc G0 (env-setup).

Read-only by default: does not create venv or install dependencies.
Prints JSON to stdout; exit 0 when ok=true, else 1.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 30) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()
    except subprocess.TimeoutExpired as exc:
        return 124, "", str(exc)
    except OSError as exc:
        return 127, "", str(exc)


def _venv_python(repo: Path, venv_dir: str) -> Path | None:
    base = repo / venv_dir
    if platform.system() == "Windows":
        exe = base / "Scripts" / "python.exe"
    else:
        exe = base / "bin" / "python"
    return exe if exe.is_file() else None


def _resolve_python(repo: Path) -> dict[str, Any]:
    errors: list[str] = []
    candidates: list[str] = []
    resolved: str | None = None
    in_venv = False
    version: str | None = None

    for name in (".venv", "venv"):
        exe = _venv_python(repo, name)
        if exe:
            candidates.append(name)
            if resolved is None:
                resolved = str(exe.resolve())

    if resolved is None:
        errors.append("no project .venv/venv python found under repo-root")
    else:
        code, out, err = _run([resolved, "-c", "import sys; print(sys.executable)"], cwd=repo)
        if code != 0:
            errors.append(f"python self-check failed: {err or out}")
        else:
            in_venv = ".venv" in out.replace("\\", "/") or "/venv/" in out.replace("\\", "/")
        code, out, _ = _run([resolved, "--version"], cwd=repo)
        if code == 0:
            version = out.splitlines()[0] if out else None

    return {
        "required": True,
        "venv_dirs_found": candidates,
        "resolved": resolved,
        "in_venv": in_venv,
        "version": version,
        "ok": resolved is not None and not errors,
        "errors": errors,
    }


def _detect_pm(node_root: Path) -> str | None:
    if (node_root / "pnpm-lock.yaml").is_file():
        return "pnpm"
    if (node_root / "yarn.lock").is_file():
        return "yarn"
    if (node_root / "package-lock.json").is_file() or (node_root / "package.json").is_file():
        return "npm"
    return None


def _pm_cmd(pm: str) -> str | None:
    return shutil.which(pm)


def _resolve_node(repo: Path) -> dict[str, Any]:
    errors: list[str] = []
    pkg = repo / "package.json"
    if not pkg.is_file():
        return {
            "required": False,
            "node_root": None,
            "pm": None,
            "ok": True,
            "errors": [],
        }

    node_root = repo.resolve()
    pm = _detect_pm(node_root)
    node_exe = shutil.which("node")
    node_version: str | None = None
    if node_exe:
        code, out, _ = _run([node_exe, "-v"], cwd=node_root)
        if code == 0:
            node_version = out.strip()

    if not node_exe:
        errors.append("node not found on PATH")

    pm_ok = False
    if pm:
        if _pm_cmd(pm):
            pm_ok = True
        else:
            errors.append(f"{pm} not found on PATH (lockfile suggests {pm})")
    else:
        errors.append("package.json present but no lockfile / pm could be inferred")

    lockfiles = {
        "package-lock.json": (node_root / "package-lock.json").is_file(),
        "pnpm-lock.yaml": (node_root / "pnpm-lock.yaml").is_file(),
        "yarn.lock": (node_root / "yarn.lock").is_file(),
    }

    return {
        "required": True,
        "node_root": str(node_root),
        "pm": pm,
        "lockfiles": lockfiles,
        "node_executable": node_exe,
        "node_version": node_version,
        "pm_available": pm_ok,
        "ok": bool(node_exe) and pm_ok and not errors,
        "errors": errors,
    }


def _needs_python(repo: Path) -> bool:
    """Aidlc G0 always needs Python for Gate scripts; also if repo looks Pythonic."""
    markers = (
        "pyproject.toml",
        "requirements.txt",
        "setup.py",
        "setup.cfg",
        "Pipfile",
    )
    if any((repo / m).exists() for m in markers):
        return True
    doc = repo / "doc"
    if doc.is_dir() and any(doc.iterdir()):
        return True
    return True  # default: Aidlc pipeline assumes Python venv at repo-root


def probe(repo_root: Path) -> dict[str, Any]:
    repo = repo_root.resolve()
    if not repo.is_dir():
        return {
            "ok": False,
            "errors": [f"repo-root is not a directory: {repo}"],
        }

    py_required = _needs_python(repo)
    python = _resolve_python(repo) if py_required else {
        "required": False,
        "ok": True,
        "errors": [],
    }
    node = _resolve_node(repo)

    errors: list[str] = []
    if py_required and not python.get("ok"):
        errors.extend(python.get("errors") or [])
    if node.get("required") and not node.get("ok"):
        errors.extend(node.get("errors") or [])

    snippet: dict[str, Any] = {}
    if python.get("resolved"):
        snippet["python"] = python["resolved"]
    if node.get("node_root"):
        snippet["node_root"] = node["node_root"]
    if node.get("pm"):
        snippet["pm"] = node["pm"]
    if node.get("node_version"):
        snippet["node_version"] = node["node_version"]

    return {
        "repo_root": str(repo),
        "platform": platform.system(),
        "signals": {
            "needs_python": py_required,
            "needs_node": bool(node.get("required")),
        },
        "python": python,
        "node": node,
        "task_card_env": snippet,
        "ok": len(errors) == 0,
        "errors": errors,
        "hints": _hints(python, node),
    }


def _hints(python: dict[str, Any], node: dict[str, Any]) -> list[str]:
    hints: list[str] = []
    if python.get("required") and not python.get("ok"):
        hints.append("Create venv: uv venv .venv && uv sync (see skills/env-setup/references/python-env.md)")
    if node.get("required") and not node.get("ok"):
        pm = node.get("pm") or "npm"
        hints.append(f"Install Node deps in node_root with {pm} (see skills/env-setup/references/node-env.md)")
    return hints


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo-root", required=True, help="Business repository root (absolute or relative)")
    p.add_argument(
        "--format",
        choices=("json", "task-card"),
        default="json",
        help="json: full report; task-card: only task_card_env object",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    report = probe(Path(args.repo_root))
    if args.format == "task-card":
        payload = report.get("task_card_env") or {}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
