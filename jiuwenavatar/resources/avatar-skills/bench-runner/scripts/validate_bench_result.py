#!/usr/bin/env python3
"""校验 bench 评分结果 JSON 的基本结构与 verdict 一致性。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

from bench_context import BenchContextError, load_bench_file

VERDICT_THRESHOLDS = (
    (1.05, "better"),
    (0.95, "equal"),
    (float("-inf"), "worse"),
)

SCORE_KEY_SETS = {
    "agent_pr_relative_score.six_stage.v1": ("baseline", "candidate"),
    "agent_pr_relative_score.six_stage.v2": ("baseline", "candidate"),
    "pr_relative_score.six_stage.v1": ("human", "agent"),
    "pr_relative_score.six_stage.v2": ("human", "agent"),
}


def expected_verdict(overall: float) -> str:
    for threshold, verdict in VERDICT_THRESHOLDS:
        if overall > threshold:
            return verdict
    return "worse"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="校验 bench_result JSON。")
    parser.add_argument("--bench", help="可选，用于推断 schema_id 与分数键名")
    parser.add_argument("--result", required=True, help="bench_result JSON 路径")
    return parser.parse_args()


def _score_keys(schema_id: str) -> Tuple[str, str]:
    keys = SCORE_KEY_SETS.get(schema_id)
    if not keys:
        raise BenchContextError(f"不支持的 schema_id: {schema_id}")
    return keys


def validate_result(bench: Dict[str, Any], result: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    schema_id = str(bench.get("schema_id") or "")
    baseline_key, candidate_key = _score_keys(schema_id)

    scores = result.get("scores")
    if not isinstance(scores, dict):
        return ["scores 必须是对象"]

    for key in (baseline_key, candidate_key, "relative"):
        if key not in scores:
            errors.append(f"scores 缺少键: {key}")

    relative = scores.get("relative") if isinstance(scores, dict) else None
    overall = None
    if isinstance(relative, dict):
        overall = relative.get("overall")

    verdict = result.get("verdict")
    if overall is not None and verdict:
        try:
            overall_f = float(overall)
            expected = expected_verdict(overall_f)
            if str(verdict) != expected:
                errors.append(
                    f"verdict={verdict!r} 与 overall_relative={overall_f} 不一致，"
                    f"期望 {expected!r}"
                )
        except (TypeError, ValueError):
            errors.append("relative.overall 必须是数字")

    if not result.get("evaluated_at"):
        errors.append("evaluated_at 为空")

    if not result.get("one_line_summary"):
        errors.append("one_line_summary 为空")

    dimensions = bench.get("dimensions") or []
    if isinstance(dimensions, list) and isinstance(relative, dict):
        by_sub = relative.get("by_sub")
        if isinstance(by_sub, dict):
            expected_ids = {
                sp["id"]
                for dim in dimensions
                if isinstance(dim, dict)
                for sp in (dim.get("subpoints") or [])
                if isinstance(sp, dict) and sp.get("id")
            }
            missing = sorted(expected_ids - set(by_sub.keys()))
            if missing:
                errors.append(f"relative.by_sub 缺少子点: {missing}")

    return errors


def main() -> int:
    args = parse_args()
    result_path = Path(args.result)
    try:
        with result_path.open(encoding="utf-8") as fh:
            result = json.load(fh)

        bench: Dict[str, Any] = {}
        if args.bench:
            bench = load_bench_file(Path(args.bench))

        errors = validate_result(bench, result)
        if errors:
            print(
                json.dumps({"valid": False, "errors": errors}, ensure_ascii=False, indent=2)
            )
            return 1

        print(json.dumps({"valid": True}, ensure_ascii=False, indent=2))
        return 0
    except (BenchContextError, json.JSONDecodeError, OSError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
