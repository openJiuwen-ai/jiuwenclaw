from __future__ import annotations

import sys
from pathlib import Path

from scripts.skill_ops import find_skill_root, package_validated_skill


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: python -m scripts.package <workspace>")
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
