"""Run DoveScore on a source/target text pair."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

logger = logging.getLogger(__name__)


def _emit_result(rendered: str) -> None:
    result_logger = logging.getLogger("dovescore.result")
    if not result_logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        result_logger.addHandler(handler)
    result_logger.setLevel(logging.INFO)
    result_logger.propagate = False
    result_logger.info("%s", rendered)


def _read_text(value: str | None, file_path: str | None, label: str) -> str:
    if value and file_path:
        raise ValueError(f"Pass either --{label} or --{label}-file, not both.")
    if file_path:
        return Path(file_path).expanduser().read_text(encoding="utf-8")
    if value:
        return value
    raise ValueError(f"Missing input: pass --{label} or --{label}-file.")


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


def _score_value(result: dict[str, Any], key: str) -> float | None:
    value = result.get(key)
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _alignment_level(score: float | None) -> str:
    if score is None:
        return "unknown"
    if score >= 0.85:
        return "high"
    if score >= 0.60:
        return "medium"
    return "low"


def _summarize_result(result: dict[str, Any], include_details: bool) -> dict[str, Any]:
    total_score = _score_value(result, "total_score")
    event_score = _score_value(result, "event_score")
    order_score = _score_value(result, "order_score")
    descriptive_score = _score_value(result, "descriptive_score")
    summary: dict[str, Any] = {
        "metric": "dovescore",
        "total_score": total_score,
        "alignment_level": _alignment_level(total_score),
        "event_score": event_score,
        "order_score": order_score,
        "descriptive_score": descriptive_score,
        "interpretation": (
            "DoveScore evaluates whether the target is supported by the source, "
            "including factual alignment and event-order consistency."
        ),
        "note": "DoveScore is an information-alignment metric, not a fluency or style score.",
    }
    if include_details:
        summary["details"] = result
    return summary


def _demo_result() -> dict[str, Any]:
    return {
        "demo": "dovescore_contrast",
        "question": "Does the target faithfully preserve the source facts?",
        "source": (
            "The Eiffel Tower is in Paris. It was completed in 1889 for the "
            "Exposition Universelle."
        ),
        "target": (
            "The Eiffel Tower is in Paris. It was completed in 1989 for the "
            "Exposition Universelle."
        ),
        "without_skill": {
            "likely_judgment": "Looks faithful because almost every word overlaps.",
            "missed_problem": "The year changed from 1889 to 1989.",
        },
        "with_dovescore": {
            "metric": "dovescore",
            "total_score": 0.5,
            "alignment_level": "low",
            "event_score": 1.0,
            "order_score": 1.0,
            "descriptive_score": 0.0,
            "finding": "The target is fluent and similar, but one descriptive fact is unsupported.",
        },
        "takeaway": (
            "DoveScore catches source-target factual mismatches that surface similarity "
            "or quick reading can miss."
        ),
    }


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
        "--demo",
        action="store_true",
        help="Run a deterministic UI demo without DoveScore, API key, or external calls.",
    )
    parser.add_argument(
        "--include-details",
        action="store_true",
        help="Include raw DoveScore events, descriptives, order lists, and per-fact scores.",
    )
    parser.add_argument(
        "--pretty",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Pretty-print JSON output.",
    )
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.ERROR, format="%(message)s")
    args = parse_args()
    if args.demo:
        result = _demo_result()
        indent = 2 if args.pretty else None
        rendered = json.dumps(result, ensure_ascii=False, indent=indent)
        if args.output:
            Path(args.output).expanduser().write_text(rendered + "\n", encoding="utf-8")
        _emit_result(rendered)
        return 0

    try:
        source = _flatten_text(_read_text(args.source, args.source_file, "source"))
        target = _flatten_text(_read_text(args.target, args.target_file, "target"))
    except (OSError, ValueError) as exc:
        logger.error("%s", exc)
        return 2

    try:
        from DoveScore import DoveScoreEvaluator
    except ImportError as exc:
        logger.error(
            "DoveScore is not installed. Install it with "
            "`pip install git+https://github.com/dannalily/DoveScore.git` "
            "or install a local DoveScore checkout with "
            "`pip install -e /path/to/DoveScore`."
        )
        logger.error("Import error: %s", exc)
        return 2

    if not args.api_key:
        logger.error(
            "Missing API key. Set OPENAI_API_KEY or DOVESCORE_API_KEY, "
            "or pass --api-key."
        )
        return 2

    evaluator_args = SimpleNamespace(api_key=args.api_key, backbone=args.backbone)
    evaluator = DoveScoreEvaluator(evaluator_args)
    result = _summarize_result(
        _json_ready(evaluator.evaluate(source, target)),
        args.include_details,
    )

    indent = 2 if args.pretty else None
    rendered = json.dumps(result, ensure_ascii=False, indent=indent)
    if args.output:
        Path(args.output).expanduser().write_text(rendered + "\n", encoding="utf-8")
    _emit_result(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
