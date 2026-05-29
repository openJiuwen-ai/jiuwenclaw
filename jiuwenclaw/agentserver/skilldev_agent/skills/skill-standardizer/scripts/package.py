#!/usr/bin/env python3
"""Package a validated skill under <workspace>/skill/ to <workspace>/output/."""

from __future__ import annotations

import fnmatch
import logging
import sys
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

EXCLUDE_DIRS = {"__pycache__", "node_modules"}
EXCLUDE_GLOBS = {"*.pyc", "*.swp"}
EXCLUDE_FILES = {".DS_Store"}
ROOT_EXCLUDE_DIRS = {"evals", "output"}


def find_skill_root(skill_dir: Path) -> Path | None:
    """Locate the skill root directory containing SKILL.md under skill/."""
    if not skill_dir.is_dir():
        return None
    if (skill_dir / "SKILL.md").is_file():
        return skill_dir

    subdirs = [
        child
        for child in skill_dir.iterdir()
        if child.is_dir() and (child / "SKILL.md").is_file()
    ]
    if len(subdirs) == 1:
        return subdirs[0]
    if len(subdirs) > 1:
        names = ", ".join(sorted(d.name for d in subdirs))
        logger.warning("multiple skill roots under %s: %s", skill_dir, names)
        return None

    for skill_md in skill_dir.rglob("SKILL.md"):
        return skill_md.parent
    return None


def _should_exclude(rel_path: Path) -> bool:
    parts = rel_path.parts
    if any(part in EXCLUDE_DIRS for part in parts):
        return True
    if len(parts) > 1 and parts[1] in ROOT_EXCLUDE_DIRS:
        return True
    name = rel_path.name
    if name in EXCLUDE_FILES:
        return True
    return any(fnmatch.fnmatch(name, pat) for pat in EXCLUDE_GLOBS)


def package_validated_skill(skill_root: Path, output_dir: Path) -> Path | None:
    """Package a skill directory into a zip under output_dir."""
    skill_root = skill_root.resolve()
    skill_md = skill_root / "SKILL.md"
    if not skill_md.is_file():
        logger.error("package failed: SKILL.md missing in %s", skill_root)
        return None

    output_dir.mkdir(parents=True, exist_ok=True)
    skill_name = skill_root.name
    skill_filename = output_dir / f"{skill_name}.zip"

    files_to_package: list[tuple[Path, Path]] = []
    for file_path in skill_root.rglob("*"):
        if not file_path.is_file():
            continue
        arcname = Path(skill_name) / file_path.relative_to(skill_root)
        if _should_exclude(arcname):
            continue
        files_to_package.append((file_path, arcname))

    try:
        with zipfile.ZipFile(skill_filename, "w", zipfile.ZIP_DEFLATED) as zipf:
            for file_path, arcname in files_to_package:
                zipf.write(file_path, arcname)
        logger.info("packaged skill to %s", skill_filename)
        return skill_filename
    except Exception as exc:
        logger.exception("package failed: %s", exc)
        return None


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: python3 -m scripts.package <workspace>")
        return 2

    workspace = Path(argv[1]).resolve()
    skill_root = find_skill_root(workspace / "skill")
    if skill_root is None:
        print("Package failed: cannot find skill root under <workspace>/skill/")
        return 1

    output_dir = workspace / "output"
    packaged = package_validated_skill(skill_root, output_dir)
    if packaged is None:
        print("Package failed.")
        return 1

    print(f"Packaged: {packaged}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
