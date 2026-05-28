from __future__ import annotations

import sys
from pathlib import Path

from .quick_validate import validate_skill
from .skill_ops import find_skill_root


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: python -m scripts.validate <workspace>")
        return 2

    workspace = Path(argv[1]).resolve()
    skill_root = find_skill_root(workspace / "skill")
    if skill_root is None:
        print("Validation failed: cannot find skill root under <workspace>/skill/")
        return 1

    valid, message = validate_skill(skill_root)
    if not valid:
        print("Validation failed:")
        print(message)
        return 1

    print("Validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
