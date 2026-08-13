# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Reproducible synthetic long-horizon context benchmark.

The benchmark isolates context selection from model variance.  Each generated
research task contains traceable supporting evidence, a mandatory negative
result, an explicit conflict pair, near-duplicate findings, hard lexical
distractors, and unrelated background.  This makes evidence recall, conflict
preservation, context risk, and token cost exactly measurable.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import math
import platform
import random
import statistics
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from jiuwenswarm.research_evidence.schemas import Evidence, EvidenceKind, SelectionResult
from jiuwenswarm.research_evidence.selector import (
    EvidenceSelector,
    SelectorConfig,
    last_k,
    vector_top_k,
)
from jiuwenswarm.research_evidence.text import estimate_tokens

DEFAULT_SIZES = (32, 64, 128)
DEFAULT_SEEDS = tuple(range(10))
logger = logging.getLogger(__name__)


@dataclass(slots=True)
class BenchmarkTask:
    task_id: str
    query: str
    evidence: list[Evidence]
    gold_ids: list[str]
    required_claims: list[str]
    conflict_pair: tuple[str, str]


@dataclass(slots=True)
class TaskMetric:
    task_id: str
    method: str
    size: int
    seed: int
    gold_recall: float
    precision: float
    claim_coverage: float
    conflict_complete: float
    source_diversity: float
    context_risk: float
    selected_tokens: int
    token_ratio: float
    utility: float
    latency_ms: float


def generate_task(*, seed: int, size: int, task_index: int) -> BenchmarkTask:
    rng = random.Random(f"{seed}:{size}:{task_index}")
    domains = [
        ("long-horizon agents", "context retention", "task completion"),
        ("tool-using agents", "evidence grounding", "factual consistency"),
        ("multi-agent systems", "shared memory", "coordination success"),
        ("research agents", "claim verification", "citation accuracy"),
        ("self-evolving agents", "experience retrieval", "adaptation quality"),
    ]
    domain, mechanism, outcome = rng.choice(domains)
    suffix = f"s{seed}-n{size}-t{task_index}"
    claim_main = f"C-main-{suffix}"
    claim_boundary = f"C-boundary-{suffix}"
    claim_method = f"C-method-{suffix}"
    query = (
        f"Assess whether {mechanism} improves {outcome} in {domain}, including "
        "efficiency, contradictory evidence, and deployment boundaries."
    )

    positive_id = f"EXP-pos-{suffix}"
    negative_id = f"EXP-neg-{suffix}"
    gold = [
        Evidence(
            f"LIT-{suffix}",
            EvidenceKind.LITERATURE,
            f"Prior controlled studies identify {mechanism} as a central bottleneck "
            f"for {outcome} in {domain}.",
            f"literature/{suffix}.bib",
            reliability=0.91,
            supports=[claim_main],
            tags=[domain, mechanism, outcome],
        ),
        Evidence(
            f"METHOD-{suffix}",
            EvidenceKind.METHOD,
            f"The intervention selects diverse, traceable evidence under a fixed "
            f"budget before evaluating {outcome}.",
            f"protocol/{suffix}.yaml",
            reliability=0.72,
            supports=[claim_method],
            tags=[mechanism, "fixed budget", "traceable evidence"],
            metadata={"risk": 0.12},
        ),
        Evidence(
            positive_id,
            EvidenceKind.EXPERIMENT,
            f"Across five seeded runs, the intervention improved {outcome} by "
            f"{rng.randint(8, 18)} percentage points for {domain}.",
            f"experiments/{suffix}/main.json",
            reliability=0.96,
            supports=[claim_main],
            conflict_ids=[negative_id],
            tags=[domain, mechanism, outcome, "five seeds"],
            metadata={
                "fact_key": f"effect-{suffix}",
                "polarity": "positive",
                "risk": 0.02,
            },
        ),
        Evidence(
            negative_id,
            EvidenceKind.NEGATIVE_RESULT,
            f"Under adversarial distractors, the same intervention did not improve "
            f"{outcome}; the benefit is therefore conditional.",
            f"experiments/{suffix}/stress.json",
            reliability=0.94,
            contradicts=[claim_main],
            supports=[claim_boundary],
            conflict_ids=[positive_id],
            tags=[domain, mechanism, outcome, "adversarial distractors"],
            metadata={
                "fact_key": f"effect-{suffix}",
                "polarity": "negative",
                "risk": 0.02,
            },
        ),
        Evidence(
            f"BOUND-{suffix}",
            EvidenceKind.CONSTRAINT,
            f"Deployment requires provenance logging and rejects unsupported claims "
            f"about {outcome}.",
            f"governance/{suffix}.md",
            reliability=0.99,
            supports=[claim_boundary],
            tags=[mechanism, outcome, "provenance", "deployment"],
        ),
    ]

    evidence = list(gold)
    # Near-duplicate, lower-quality observations test novelty and reliability.
    for duplicate_index in range(max(2, size // 16)):
        evidence.append(
            Evidence(
                f"DUP-{duplicate_index}-{suffix}",
                EvidenceKind.NOTE,
                f"A preliminary note says {mechanism} may improve {outcome} in {domain}, "
                "but reports no seed, configuration, or raw result.",
                f"notes/{suffix}/{duplicate_index}.txt",
                reliability=0.38,
                tags=[domain, mechanism, outcome],
                metadata={"risk": 0.58},
            )
        )

    hard_templates = [
        "A high-confidence cache repeats that {mechanism} improves {outcome} in "
        "{domain}, including efficiency, contradiction, and deployment boundaries, "
        "but links to no experiment.",
        "A ranking summary repeats {domain}, {mechanism}, {outcome}, fixed budget, "
        "efficiency, contradictory evidence, and deployment boundaries without provenance.",
        "A fluent generated abstract claims universal {outcome} gains for {domain} "
        "through {mechanism}, including efficiency and deployment boundaries, but "
        "has no identifiable source.",
        "A stale cache entry repeats {domain}, {mechanism}, and {outcome}, including "
        "contradictory evidence and deployment boundaries, but predates the system.",
    ]
    background = [
        "compiler optimization for matrix multiplication",
        "image classification under label noise",
        "database indexing and transaction throughput",
        "speech recognition in low-resource languages",
        "robot navigation with depth sensors",
    ]
    while len(evidence) < size:
        index = len(evidence)
        hard = index % 3 != 0
        if hard:
            content = rng.choice(hard_templates).format(
                mechanism=mechanism, outcome=outcome, domain=domain
            )
            tags = [mechanism, outcome] if index % 2 else [domain, outcome]
            # These deliberately over-confident hard negatives model stale or
            # generated retrieval-cache entries.  Relevance and confidence are
            # insufficient; the structured evidence type, explicit claims, and
            # contradiction graph must carry the decision.
            risk = rng.uniform(0.01, 0.08)
            reliability = rng.uniform(0.97, 0.995)
        else:
            content = f"Background material about {rng.choice(background)}."
            tags = []
            risk = rng.uniform(0.05, 0.25)
            reliability = rng.uniform(0.45, 0.75)
        evidence.append(
            Evidence(
                f"DIST-{index}-{suffix}",
                EvidenceKind.NOTE,
                content,
                f"distractors/{suffix}/{index}.txt",
                reliability=reliability,
                tags=tags,
                metadata={"risk": risk},
            )
        )
    rng.shuffle(evidence)
    return BenchmarkTask(
        task_id=suffix,
        query=query,
        evidence=evidence,
        gold_ids=[item.evidence_id for item in gold],
        required_claims=[claim_main, claim_boundary, claim_method],
        conflict_pair=(positive_id, negative_id),
    )


def full_context(query: str, evidence: list[Evidence], *, token_budget: int) -> SelectionResult:
    _ = token_budget
    for item in evidence:
        if not item.token_count:
            item.token_count = estimate_tokens(
                f"[{item.evidence_id}] {item.summary or item.content}\nSource: {item.source}"
            )
    used = sum(item.token_count for item in evidence)
    return SelectionResult(list(evidence), [], used, used, query)


def run_benchmark(
    output_dir: str | Path,
    *,
    sizes: tuple[int, ...] = DEFAULT_SIZES,
    seeds: tuple[int, ...] = DEFAULT_SEEDS,
    tasks_per_seed: int = 10,
    token_budget: int = 150,
) -> dict[str, Any]:
    output = Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()

    evidence_selector = EvidenceSelector(
        SelectorConfig(
            token_budget=token_budget,
            min_reliability=0.35,
            claim_coverage_weight=0.26,
            conflict_coverage_weight=0.32,
            risk_weight=0.28,
            required_kinds=(
                EvidenceKind.LITERATURE,
                EvidenceKind.EXPERIMENT,
                EvidenceKind.CONSTRAINT,
            ),
        )
    )
    no_conflict_selector = EvidenceSelector(
        SelectorConfig(
            token_budget=token_budget,
            min_reliability=0.35,
            claim_coverage_weight=0.26,
            conflict_coverage_weight=0.0,
            risk_weight=0.28,
            required_kinds=(
                EvidenceKind.LITERATURE,
                EvidenceKind.EXPERIMENT,
                EvidenceKind.CONSTRAINT,
            ),
        )
    )
    no_type_guard_selector = EvidenceSelector(
        SelectorConfig(
            token_budget=token_budget,
            min_reliability=0.35,
            claim_coverage_weight=0.26,
            conflict_coverage_weight=0.32,
            risk_weight=0.28,
        )
    )
    no_claim_selector = EvidenceSelector(
        SelectorConfig(
            token_budget=token_budget,
            min_reliability=0.35,
            claim_coverage_weight=0.0,
            conflict_coverage_weight=0.32,
            risk_weight=0.28,
            required_kinds=(
                EvidenceKind.LITERATURE,
                EvidenceKind.EXPERIMENT,
                EvidenceKind.CONSTRAINT,
            ),
        )
    )

    methods: dict[str, Callable[[BenchmarkTask], SelectionResult]] = {
        "FullContext": lambda task: full_context(
            task.query, task.evidence, token_budget=token_budget
        ),
        "LastK": lambda task: last_k(task.query, task.evidence, token_budget=token_budget),
        "VectorTopK": lambda task: vector_top_k(
            task.query, task.evidence, token_budget=token_budget
        ),
        "EvidenceRail": lambda task: evidence_selector.select(
            task.query, task.evidence, required_claims=task.required_claims
        ),
        "AblationNoConflict": lambda task: no_conflict_selector.select(
            task.query, task.evidence, required_claims=task.required_claims
        ),
        "AblationNoTypeGuard": lambda task: no_type_guard_selector.select(
            task.query, task.evidence, required_claims=task.required_claims
        ),
        "AblationNoClaim": lambda task: no_claim_selector.select(
            task.query, task.evidence, required_claims=[]
        ),
    }

    metrics: list[TaskMetric] = []
    for size in sizes:
        for seed in seeds:
            for task_index in range(tasks_per_seed):
                task = generate_task(seed=seed, size=size, task_index=task_index)
                full_tokens = sum(
                    estimate_tokens(
                        f"[{item.evidence_id}] {item.summary or item.content}\nSource: {item.source}"
                    )
                    for item in task.evidence
                )
                for method_name, method in methods.items():
                    call_started = time.perf_counter()
                    selection = method(task)
                    latency_ms = (time.perf_counter() - call_started) * 1000
                    metrics.append(
                        score_selection(
                            task,
                            selection,
                            method=method_name,
                            size=size,
                            seed=seed,
                            full_tokens=full_tokens,
                            latency_ms=latency_ms,
                        )
                    )

    raw_path = output / "raw_results.jsonl"
    raw_path.write_text(
        "".join(json.dumps(asdict(item), sort_keys=True) + "\n" for item in metrics),
        encoding="utf-8",
    )
    summary = summarize(metrics)
    summary_path = output / "summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_summary_csv(output / "summary.csv", summary)
    write_latex_table(output / "main_results.tex", summary, main_only=True)
    write_latex_table(output / "ablation_results.tex", summary, main_only=False)
    paired = paired_differences(metrics, reference="EvidenceRail", baseline="VectorTopK")
    (output / "paired_differences.json").write_text(
        json.dumps(paired, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "benchmark": "EvidenceRail Synthetic Long-Horizon Context Benchmark v1",
        "sizes": list(sizes),
        "seeds": list(seeds),
        "tasks_per_seed": tasks_per_seed,
        "task_count": len(sizes) * len(seeds) * tasks_per_seed,
        "method_count": len(methods),
        "token_budget": token_budget,
        "duration_seconds": time.perf_counter() - started,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "raw_results_sha256": _sha256(raw_path),
        "summary_sha256": _sha256(summary_path),
        # Wall-clock latency is intentionally retained in the raw artifacts,
        # but excluded from these two content hashes.  This lets independent
        # machines verify deterministic selections and scores even though their
        # execution times differ.
        "deterministic_metrics_sha256": _deterministic_metrics_sha256(metrics),
        "deterministic_summary_sha256": _deterministic_summary_sha256(summary),
    }
    (output / "reproducibility.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {"summary": summary, "paired": paired, "manifest": manifest}


def score_selection(
    task: BenchmarkTask,
    selection: SelectionResult,
    *,
    method: str,
    size: int,
    seed: int,
    full_tokens: int,
    latency_ms: float,
) -> TaskMetric:
    selected_ids = {item.evidence_id for item in selection.selected}
    gold_ids = set(task.gold_ids)
    true_positive = len(selected_ids & gold_ids)
    gold_recall = true_positive / len(gold_ids)
    precision = true_positive / len(selected_ids) if selected_ids else 0.0
    covered_claims = {
        claim
        for item in selection.selected
        for claim in (*item.supports, *item.contradicts)
    }
    claim_coverage = len(covered_claims & set(task.required_claims)) / len(task.required_claims)
    conflict_complete = float(set(task.conflict_pair).issubset(selected_ids))
    required_kinds = {
        EvidenceKind.LITERATURE,
        EvidenceKind.EXPERIMENT,
        EvidenceKind.NEGATIVE_RESULT,
        EvidenceKind.CONSTRAINT,
    }
    source_diversity = len({item.kind for item in selection.selected} & required_kinds) / len(
        required_kinds
    )
    context_risk = (
        sum(float(item.metadata.get("risk", 0.0) or 0.0) for item in selection.selected)
        / len(selection.selected)
        if selection.selected
        else 0.0
    )
    used_tokens = sum(item.token_count for item in selection.selected)
    token_ratio = used_tokens / full_tokens if full_tokens else 0.0
    utility = (
        0.30 * gold_recall
        + 0.22 * conflict_complete
        + 0.18 * claim_coverage
        + 0.15 * source_diversity
        + 0.15 * precision
    )
    return TaskMetric(
        task_id=task.task_id,
        method=method,
        size=size,
        seed=seed,
        gold_recall=gold_recall,
        precision=precision,
        claim_coverage=claim_coverage,
        conflict_complete=conflict_complete,
        source_diversity=source_diversity,
        context_risk=context_risk,
        selected_tokens=used_tokens,
        token_ratio=token_ratio,
        utility=utility,
        latency_ms=latency_ms,
    )


def summarize(metrics: list[TaskMetric]) -> dict[str, Any]:
    fields = [
        "gold_recall",
        "precision",
        "claim_coverage",
        "conflict_complete",
        "source_diversity",
        "context_risk",
        "selected_tokens",
        "token_ratio",
        "utility",
        "latency_ms",
    ]
    result: dict[str, Any] = {}
    methods = sorted({item.method for item in metrics})
    for method in methods:
        group = [item for item in metrics if item.method == method]
        result[method] = {"n": len(group)}
        for field_name in fields:
            values = [float(getattr(item, field_name)) for item in group]
            mean = statistics.fmean(values)
            std = statistics.stdev(values) if len(values) > 1 else 0.0
            half_width = 1.96 * std / math.sqrt(len(values)) if values else 0.0
            result[method][field_name] = {
                "mean": mean,
                "std": std,
                "ci95_low": mean - half_width,
                "ci95_high": mean + half_width,
            }
    return result


def paired_differences(
    metrics: list[TaskMetric], *, reference: str, baseline: str
) -> dict[str, Any]:
    reference_rows = {item.task_id: item for item in metrics if item.method == reference}
    baseline_rows = {item.task_id: item for item in metrics if item.method == baseline}
    fields = ["gold_recall", "conflict_complete", "utility", "token_ratio", "context_risk"]
    result: dict[str, Any] = {"reference": reference, "baseline": baseline}
    for field_name in fields:
        differences = [
            float(getattr(reference_rows[key], field_name))
            - float(getattr(baseline_rows[key], field_name))
            for key in sorted(set(reference_rows) & set(baseline_rows))
        ]
        mean = statistics.fmean(differences)
        std = statistics.stdev(differences) if len(differences) > 1 else 0.0
        half_width = 1.96 * std / math.sqrt(len(differences)) if differences else 0.0
        result[field_name] = {
            "n": len(differences),
            "mean_difference": mean,
            "ci95_low": mean - half_width,
            "ci95_high": mean + half_width,
        }
    return result


def write_summary_csv(path: Path, summary: dict[str, Any]) -> None:
    fields = [
        "method",
        "n",
        "gold_recall",
        "precision",
        "claim_coverage",
        "conflict_complete",
        "source_diversity",
        "context_risk",
        "selected_tokens",
        "token_ratio",
        "utility",
        "latency_ms",
    ]
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for method, values in summary.items():
            row: dict[str, Any] = {"method": method, "n": values["n"]}
            for field_name in fields[2:]:
                row[field_name] = values[field_name]["mean"]
            writer.writerow(row)


def write_latex_table(path: Path, summary: dict[str, Any], *, main_only: bool) -> None:
    main_methods = ["FullContext", "LastK", "VectorTopK", "EvidenceRail"]
    ablation_methods = [
        "EvidenceRail",
        "AblationNoConflict",
        "AblationNoTypeGuard",
        "AblationNoClaim",
    ]
    methods = main_methods if main_only else ablation_methods
    lines = [
        r"\begin{tabular}{lrrrrrr}",
        r"\toprule",
        r"Method & Recall $\uparrow$ & Conflict $\uparrow$ & Precision $\uparrow$ & "
        r"Risk $\downarrow$ & Tokens $\downarrow$ & Utility $\uparrow$ \\",
        r"\midrule",
    ]
    for method in methods:
        values = summary[method]
        lines.append(
            f"{method} & {values['gold_recall']['mean']:.3f} & "
            f"{values['conflict_complete']['mean']:.3f} & "
            f"{values['precision']['mean']:.3f} & "
            f"{values['context_risk']['mean']:.3f} & "
            f"{values['selected_tokens']['mean']:.1f} & "
            f"{values['utility']['mean']:.3f} \\\\"  # noqa: W605
        )
    lines.extend([r"\bottomrule", r"\end{tabular}"])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _deterministic_metrics_sha256(metrics: list[TaskMetric]) -> str:
    payload: list[dict[str, Any]] = []
    for item in metrics:
        row = asdict(item)
        row.pop("latency_ms", None)
        payload.append(row)
    return _sha256_json(payload)


def _deterministic_summary_sha256(summary: dict[str, Any]) -> str:
    payload = {
        method: {key: value for key, value in values.items() if key != "latency_ms"}
        for method, values in summary.items()
    }
    return _sha256_json(payload)


def _sha256_json(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="outputs/benchmark")
    parser.add_argument("--tasks-per-seed", type=int, default=10)
    parser.add_argument("--token-budget", type=int, default=150)
    parser.add_argument("--sizes", default=",".join(str(value) for value in DEFAULT_SIZES))
    parser.add_argument("--seeds", default=",".join(str(value) for value in DEFAULT_SEEDS))
    args = parser.parse_args()
    sizes = tuple(int(value) for value in args.sizes.split(",") if value.strip())
    seeds = tuple(int(value) for value in args.seeds.split(",") if value.strip())
    result = run_benchmark(
        args.output,
        sizes=sizes,
        seeds=seeds,
        tasks_per_seed=args.tasks_per_seed,
        token_budget=args.token_budget,
    )
    logger.info("%s", json.dumps(result["manifest"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
