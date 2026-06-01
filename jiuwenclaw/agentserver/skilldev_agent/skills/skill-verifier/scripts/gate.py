#!/usr/bin/env python3
"""Full verification gate with short-circuit.

Pipeline: validate → package → upload → safety_scan
If any stage fails, the pipeline stops immediately.
"""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

import yaml

from scripts.validate import find_skill_root, validate_skill
from scripts.package import package_skill
from scripts.upload_skill import upload_file
from scripts.safety_scan import scan_url, _get_conclusion, _format_failure

logger = logging.getLogger(__name__)


def _read_skill_name(skill_root: Path) -> str | None:
    skill_md = skill_root / "SKILL.md"
    if not skill_md.is_file():
        return None
    content = skill_md.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return None
    fm = yaml.safe_load(match.group(1))
    if isinstance(fm, dict):
        return str(fm.get("name") or "").strip() or None
    return None


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Gate failed:")
        print("Usage: python3 -m scripts.gate <workspace>")
        return 2

    workspace = Path(argv[1]).resolve()
    skill_root = find_skill_root(workspace / "skill")
    if skill_root is None:
        print("Gate failed: cannot find skill root under <workspace>/skill/")
        return 1

    # Stage 1: validate (short-circuit on failure)
    valid, message = validate_skill(skill_root)
    if not valid:
        print("Gate failed at stage [validate]:")
        print(message)
        return 1
    print("Stage [validate]: passed")

    # Stage 2: package
    output_dir = workspace / "output"
    packaged = package_skill(skill_root, output_dir)
    if packaged is None:
        print("Gate failed at stage [package].")
        return 1
    print(f"Stage [package]: {packaged}")

    # Stage 3: upload
    import asyncio
    url = asyncio.run(upload_file(str(packaged)))
    if url is None:
        print("Gate failed at stage [upload].")
        return 1
    print(f"Stage [upload]: {url}")

    # Stage 4: safety_scan
    skill_name = _read_skill_name(skill_root) or skill_root.name
    raw_result = scan_url(skill_name=skill_name, url=url)
    conclusion = _get_conclusion(raw_result)

    if str(conclusion).upper() == "BENIGN":
        print("Stage [safety_scan]: passed")
        print(f"Gate passed. Packaged: {packaged} | URL: {url}")
        return 0

    print("Gate failed at stage [safety_scan]:")
    print(_format_failure(raw_result))
    return 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    raise SystemExit(main(sys.argv))
