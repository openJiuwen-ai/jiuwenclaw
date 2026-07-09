#!/usr/bin/env python3
"""解析 bench inputs.repo_root：支持 name 或 path，校验 Git 仓库。"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from bench_context import BenchContextError, load_bench_inputs, resolve_repo_root


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="解析 bench inputs.repo_root（name 或 path）并校验 Git 仓库。"
    )
    parser.add_argument("--bench", help="bench JSON 路径（读取 inputs.repo_root）")
    parser.add_argument("--name", help="gitcode-repo.json workspaces[].name")
    parser.add_argument("--path", help="本地 clone 绝对路径")
    parser.add_argument(
        "--gitcode-config",
        default="",
        help="gitcode-repo.json 路径；仅 name 时用于查找 local_repo.path",
    )
    parser.add_argument(
        "--format",
        choices=("json", "path"),
        default="json",
        help="输出格式：json（默认）或仅输出解析后的 path",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.bench:
            inputs = load_bench_inputs(Path(args.bench))
            spec = inputs.get("repo_root")
            if not isinstance(spec, dict):
                raise BenchContextError(
                    f"{args.bench} 的 inputs.repo_root 必须是对象"
                )
        else:
            spec = {
                "name": args.name or "",
                "path": args.path or "",
                "gitcode_config": args.gitcode_config or "",
            }

        result = resolve_repo_root(spec)
        if args.format == "path":
            print(result.path)
        else:
            print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
        return 0
    except BenchContextError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
