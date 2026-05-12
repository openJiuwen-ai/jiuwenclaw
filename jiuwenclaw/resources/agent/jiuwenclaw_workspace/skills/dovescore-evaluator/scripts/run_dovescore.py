"""Run DoveScore on a source/target text pair."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any


def _read_text(value: str | None, file_path: str | None, label: str) -> str:
    if value and file_path:
        raise SystemExit(f"Pass either --{label} or --{label}-file, not both.")
    if file_path:
        return Path(file_path).expanduser().read_text(encoding="utf-8")
    if value:
        return value
    raise SystemExit(f"Missing input: pass --{label} or --{label}-file.")


def _flatten_text(text: str) -> str:
    return " ".join(text.split())


def _json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if hasattr(value, "item"):
        return value.item()
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate source-target information alignment with DoveScore."
    )
    parser.add_argument("--source", help="Reference/source text.")
    parser.add_argument("--target", help="Target text to evaluate.")
    parser.add_argument("--source-file", help="UTF-8 file containing source text.")
    parser.add_argument("--target-file", help="UTF-8 file containing target text.")
    parser.add_argument(
        "--api-key",
        default=os.getenv("DOVESCORE_API_KEY") or os.getenv("OPENAI_API_KEY"),
        help="OpenAI API key. Defaults to DOVESCORE_API_KEY or OPENAI_API_KEY.",
    )
    parser.add_argument("--backbone", default="gpt-4o-mini", help="OpenAI model name.")
    parser.add_argument("--output", help="Optional path to write JSON result.")
    parser.add_argument(
        "--pretty",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Pretty-print JSON output.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source = _flatten_text(_read_text(args.source, args.source_file, "source"))
    target = _flatten_text(_read_text(args.target, args.target_file, "target"))

    try:
        from DoveScore import DoveScoreEvaluator
    except ImportError as exc:
        print(
            "DoveScore is not installed. Install JiuwenClaw with "
            "`pip install -e \".[dovescore]\"` or install the local DoveScore repo "
            "with `pip install -e /path/to/DoveScore`.",
            file=sys.stderr,
        )
        print(f"Import error: {exc}", file=sys.stderr)
        return 2

    if not args.api_key:
        print(
            "Missing API key. Set OPENAI_API_KEY or DOVESCORE_API_KEY, "
            "or pass --api-key.",
            file=sys.stderr,
        )
        return 2

    evaluator_args = SimpleNamespace(api_key=args.api_key, backbone=args.backbone)
    evaluator = DoveScoreEvaluator(evaluator_args)
    result = _json_ready(evaluator.evaluate(source, target))

    indent = 2 if args.pretty else None
    rendered = json.dumps(result, ensure_ascii=False, indent=indent)
    if args.output:
        Path(args.output).expanduser().write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
