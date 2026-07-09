#!/usr/bin/env python3
"""Append a suspected bug to PENDING_BUGS.md."""

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

SCRIPT_DIR = Path(__file__).resolve().parent


def find_project_root(start: Optional[Path] = None) -> Path:
    """Resolve jiuwenswarm root; prefer get_root_dir() when running inside jiuwenclaw."""
    try:
        from jiuwenclaw.common.utils import get_root_dir

        return get_root_dir()
    except ImportError:
        pass

    start = (start or Path.cwd()).resolve()
    for path in (start, *start.parents):
        if (path / ".git").is_dir():
            return path
    return SCRIPT_DIR.parents[2]


def main() -> int:
    parser = argparse.ArgumentParser(description="Record a suspected bug to PENDING_BUGS.md")
    parser.add_argument("--title", required=True, help="Bug title")
    parser.add_argument("--file", required=True, dest="file_path", help="Related file path, e.g. pkg/foo.py:123")
    parser.add_argument("--module", required=True, help="Product module name")
    parser.add_argument("--severity", required=True, choices=["高", "中", "低"])
    parser.add_argument("--desc", required=True, help="One-line description")
    parser.add_argument("--analysis", required=True, help="Technical analysis")
    parser.add_argument("--fix", default="", help="Optional fix suggestion")
    parser.add_argument(
        "--root",
        default=None,
        help="Project root containing PENDING_BUGS.md (default: jiuwenswarm root)",
    )
    args = parser.parse_args()

    root = Path(args.root).resolve() if args.root else find_project_root()

    from jiuwenclaw.agents.harness.common.bug_recording import record_bug

    result = record_bug(
        title=args.title,
        file_path=args.file_path,
        module=args.module,
        severity=args.severity,
        description=args.desc,
        analysis=args.analysis,
        fix_suggestion=args.fix,
        root=root,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())
