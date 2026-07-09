#!/usr/bin/env python3
"""解析 bench 完整上下文：repo_root、skills_root、占位符与展开后的 gate 命令。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from bench_context import (
    BenchContextError,
    expand_gate_checks,
    load_bench_file,
    resolve_bench_context,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="解析 bench JSON 的运行时上下文。")
    parser.add_argument("--bench", required=True, help="bench JSON 路径")
    parser.add_argument(
        "--side",
        choices=("baseline", "candidate"),
        default="baseline",
        help="用于 module / analysis_type 的侧（默认 baseline）",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    bench_path = Path(args.bench)
    try:
        ctx = resolve_bench_context(bench_path, side=args.side)
        data = load_bench_file(bench_path)
        gates = data.get("optional_gate_checks") or {}
        expanded_gates = {}
        if isinstance(gates, dict):
            expanded_gates = expand_gate_checks(
                {k: str(v) for k, v in gates.items()},
                ctx.placeholders,
            )

        payload = {
            "repo_root": ctx.repo_root.path,
            "repo_root_detail": {
                "source": ctx.repo_root.source,
                "workspace_name": ctx.repo_root.workspace_name,
                "current_branch": ctx.repo_root.current_branch,
                "notes": ctx.repo_root.notes,
            },
            "skills_root": ctx.skills_root,
            "bench_runner_root": ctx.bench_runner_root,
            "gitcode_config": ctx.gitcode_config,
            "placeholders": ctx.placeholders,
            "optional_gate_checks_expanded": expanded_gates,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    except BenchContextError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
