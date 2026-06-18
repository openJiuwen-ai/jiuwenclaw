# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Post-run finalization helpers: report loading, stale-package detection, and repackaging."""

from __future__ import annotations

import fnmatch
import json
import logging
import zipfile
from pathlib import Path
from typing import Any

from jiuwenclaw.agentserver.skilldev_agent.utils.direct_import import find_skill_root

logger = logging.getLogger(__name__)

_PKG_EXCLUDE_DIRS: frozenset[str] = frozenset({"__pycache__", "node_modules"})
_PKG_EXCLUDE_GLOBS: frozenset[str] = frozenset({"*.pyc", "*.swp", "*.bak-*"})
_PKG_EXCLUDE_FILES: frozenset[str] = frozenset({".DS_Store"})
_PKG_ROOT_EXCLUDE_DIRS: frozenset[str] = frozenset({"evals", "output"})


def _should_exclude_from_package(rel_path: Path) -> bool:
    parts = rel_path.parts
    if any(part in _PKG_EXCLUDE_DIRS for part in parts):
        return True
    if len(parts) > 1 and parts[1] in _PKG_ROOT_EXCLUDE_DIRS:
        return True
    name = rel_path.name
    if name in _PKG_EXCLUDE_FILES:
        return True
    return any(fnmatch.fnmatch(name, pat) for pat in _PKG_EXCLUDE_GLOBS)


def collect_output_packages(output_dir: Path) -> list[Path]:
    """Return .skill/.zip files under *output_dir*, or an empty list if it does not exist."""
    if not output_dir.exists():
        return []
    return [f for f in output_dir.iterdir() if f.is_file() and f.suffix in (".skill", ".zip")]


def repackage_if_stale(task_workspace: Path, existing_packages: list[Path]) -> list[Path]:
    """Re-run packaging when skill source is newer than the existing archive.

    Returns the updated package list (new package on repack, original list otherwise).
    """
    skill_dir = task_workspace / "skill"
    if not skill_dir.is_dir():
        return existing_packages

    skill_root = find_skill_root(skill_dir)
    if skill_root is None:
        return existing_packages

    pkg_mtime = min(f.stat().st_mtime for f in existing_packages)
    src_mtime = max(
        (f.stat().st_mtime for f in skill_root.rglob("*") if f.is_file()),
        default=0.0,
    )
    if src_mtime <= pkg_mtime:
        return existing_packages

    logger.info("[SkillDevDeepAdapter] Skill source is newer than existing package, repackaging …")
    for stale in existing_packages:
        stale.unlink(missing_ok=True)
        logger.info("[SkillDevDeepAdapter] Removed stale package: %s", stale.name)

    output_dir = task_workspace / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    skill_name = skill_root.name
    zip_path = output_dir / f"{skill_name}.zip"

    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for file_path in skill_root.rglob("*"):
                if not file_path.is_file():
                    continue
                arcname = Path(skill_name) / file_path.relative_to(skill_root)
                if _should_exclude_from_package(arcname):
                    continue
                zf.write(file_path, arcname)
        logger.info("[SkillDevDeepAdapter] Repackaged skill to %s", zip_path)
        return [zip_path]
    except Exception:
        logger.exception("[SkillDevDeepAdapter] Repackaging failed, falling back to stale packages")
        return existing_packages


def get_static_review_report(
    task_workspace: Path,
) -> tuple[dict[str, Any] | None, str | None]:
    """Load static_report.json / static_report.md from evals/static.

    Returns (static_result_dict, report_markdown).
    Marks the report as reviewed on success so it is not surfaced again.
    """
    static_dir = task_workspace / "evals" / "static"
    report_json = static_dir / "static_report.json"
    if not report_json.is_file():
        return None, None

    try:
        static_result = json.loads(report_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("[SkillDevDeepAdapter] static report load failed: %s", exc)
        return None, None

    if not isinstance(static_result, dict) or static_result.get("reviewed", False):
        return None, None

    updated = {**static_result, "reviewed": True}
    try:
        report_json.write_text(
            json.dumps(updated, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("[SkillDevDeepAdapter] static report mark reviewed failed: %s", exc)
        return None, None

    report_md = static_dir / "static_report.md"
    report = report_md.read_text(encoding="utf-8") if report_md.is_file() else ""
    return static_result, report


def get_review_benchmark(
    task_workspace: Path,
) -> tuple[dict[str, Any] | None, str | None, int]:
    """Load the latest benchmark.json / benchmark.md from the evals dir.

    Returns (benchmark_dict, report_markdown, iteration_number).
    Marks the benchmark as reviewed on success so it is not surfaced again.
    """
    evals_dir = task_workspace / "evals"

    iter_dirs = [
        d for d in evals_dir.iterdir()
        if d.is_dir() and d.name.startswith("iteration-")
    ] if evals_dir.is_dir() else []

    if iter_dirs:
        def _iter_num(d: Path) -> int:
            try:
                return int(d.name.split("-", 1)[1])
            except (IndexError, ValueError):
                return -1

        latest = max(iter_dirs, key=_iter_num)
        iteration = _iter_num(latest)
        target_dir = latest
    else:
        iteration = 0
        target_dir = evals_dir

    benchmark: dict[str, Any] | None = None
    report: str | None = None

    bm_json = target_dir / "benchmark.json"
    if bm_json.is_file():
        benchmark = json.loads(bm_json.read_text(encoding="utf-8"))
        try:
            benchmark["run_summary"]["with_skill"]["pass_rate"]["mean"]
            benchmark["run_summary"]["without_skill"]["pass_rate"]["mean"]
            benchmark["run_summary"]["delta"]["pass_rate"]
            has_required_summary = True
        except (KeyError, TypeError):
            has_required_summary = False
        if not has_required_summary or benchmark.get("reviewed"):
            return None, None, -1
        updated = {**benchmark, "reviewed": True}
        bm_json.write_text(
            json.dumps(updated, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    bm_md = target_dir / "benchmark.md"
    if bm_md.is_file():
        report = bm_md.read_text(encoding="utf-8")

    return benchmark, report, iteration
