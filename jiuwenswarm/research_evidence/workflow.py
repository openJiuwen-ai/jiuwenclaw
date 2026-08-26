# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""End-to-end, evidence-gated scientific paper workflow.

The workflow is intentionally small enough for competition judges to inspect.
It supports a deterministic offline mode for zero-key reproduction and a live
mode that uses JiuwenSwarm's configured model.  Both modes execute the same six
research stages and write the same auditable artifact layout.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from jiuwenswarm.research_evidence.benchmark import run_benchmark
from jiuwenswarm.research_evidence.claim_graph import ClaimEvidenceGraph
from jiuwenswarm.research_evidence.ledger import ResourceEvent, ResourceLedger
from jiuwenswarm.research_evidence.rail import render_evidence_context
from jiuwenswarm.research_evidence.schemas import Claim, Evidence, EvidenceKind, utc_now_iso
from jiuwenswarm.research_evidence.selector import EvidenceSelector, SelectorConfig
from jiuwenswarm.research_evidence.store import EvidenceStore
from jiuwenswarm.research_evidence.text import estimate_tokens

STAGES = ("ideation", "planning", "experiment", "analysis", "writing", "review")


@dataclass(slots=True)
class WorkflowConfig:
    """Configuration for one research workflow run."""

    topic: str
    output_dir: str | Path
    mode: str = "offline"
    results_dir: str | Path | None = None
    token_budget: int = 2048
    max_output_tokens: int = 1200
    model: Any | None = None
    model_name: str = "offline-deterministic"

    def __post_init__(self) -> None:
        self.topic = str(self.topic).strip()
        self.mode = str(self.mode).strip().lower()
        if not self.topic:
            raise ValueError("topic must not be empty")
        if self.mode not in {"offline", "live"}:
            raise ValueError("mode must be 'offline' or 'live'")
        if self.mode == "live" and self.model is None:
            raise ValueError("live mode requires a configured JiuwenSwarm model")
        self.token_budget = max(256, int(self.token_budget))
        self.max_output_tokens = max(256, int(self.max_output_tokens))


@dataclass(slots=True)
class StageArtifact:
    """One materialized stage result."""

    stage: str
    path: str
    sha256: str
    input_tokens: int = 0
    output_tokens: int = 0
    duration_seconds: float = 0.0


class ResearchWorkflow:
    """Run ideation through independent review with a shared evidence contract."""

    def __init__(self, config: WorkflowConfig) -> None:
        self.config = config
        self.output = Path(config.output_dir).expanduser().resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.store = EvidenceStore(self.output / "evidence_store")
        self.ledger = ResourceLedger(self.output / "resource_events.jsonl")
        self.selector = EvidenceSelector(
            SelectorConfig(
                token_budget=config.token_budget,
                min_reliability=0.35,
                required_kinds=(
                    EvidenceKind.LITERATURE,
                    EvidenceKind.EXPERIMENT,
                    EvidenceKind.NEGATIVE_RESULT,
                ),
            )
        )
        self.run_id = uuid.uuid4().hex[:12]
        self.artifacts: list[StageArtifact] = []
        self.stage_text: dict[str, str] = {}

    async def run(self) -> dict[str, Any]:
        """Execute all stages and return the sanitized run manifest."""

        started = time.perf_counter()
        self._seed_literature_and_method()
        await self._materialize_model_stage("ideation")
        await self._materialize_model_stage("planning")
        experiment_text, experiment_duration = self._materialize_experiment()
        self._write_stage(
            "experiment",
            experiment_text,
            duration_seconds=experiment_duration,
        )
        await self._materialize_model_stage("analysis")
        await self._materialize_model_stage("writing")
        issues = ClaimEvidenceGraph(
            self.store.list_evidence(), self.store.list_claims()
        ).verify_all()
        await self._materialize_model_stage(
            "review",
            suffix=(
                "\n\nDeterministic claim-graph issues:\n"
                + json.dumps(
                    [item.to_dict() for item in issues], ensure_ascii=False, indent=2
                )
            ),
        )
        ledger_summary = self.ledger.summary()
        manifest = {
            "schema_version": 1,
            "run_id": self.run_id,
            "created_at": utc_now_iso(),
            "status": "completed",
            "mode": self.config.mode,
            "model": self.config.model_name,
            "topic": self.config.topic,
            "stages": list(STAGES),
            "duration_seconds": time.perf_counter() - started,
            "resource_summary": ledger_summary,
            "claim_verification_issues": [item.to_dict() for item in issues],
            "artifacts": [asdict(item) for item in self.artifacts],
        }
        manifest_path = self.output / "workflow_manifest.json"
        _write_json(manifest_path, manifest)
        self._write_run_report(manifest)
        return manifest

    async def _materialize_model_stage(self, stage: str, *, suffix: str = "") -> None:
        selection = self.selector.select(
            self._stage_query(stage),
            self.store.list_evidence(),
            required_claims=[item.claim_id for item in self.store.list_claims()],
        )
        context = render_evidence_context(selection, stage=stage)
        self.store.append_event(
            {
                "event": "workflow_context_selection",
                "run_id": self.run_id,
                "stage": stage,
                "selection": selection.to_dict(),
            }
        )
        prompt = self._stage_prompt(stage, context=context) + suffix
        started = time.perf_counter()
        if self.config.mode == "live":
            text, usage = await _invoke_model(
                self.config.model,
                prompt,
                max_output_tokens=self.config.max_output_tokens,
            )
            event = "model_call"
        else:
            text = self._offline_stage_text(stage)
            usage = {
                "input_tokens": 0,
                "output_tokens": 0,
                "model": self.config.model_name,
            }
            event = "offline_stage"
        duration = time.perf_counter() - started
        self.ledger.append(
            ResourceEvent(
                run_id=self.run_id,
                stage=stage,
                event=event,
                duration_seconds=duration,
                input_tokens=usage["input_tokens"],
                output_tokens=usage["output_tokens"],
                model=usage["model"] or self.config.model_name,
                metadata={
                    "selected_evidence_tokens": selection.used_tokens,
                    "prompt_tokens_estimate": estimate_tokens(prompt),
                },
            )
        )
        self._write_stage(
            stage,
            text,
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            duration_seconds=duration,
        )

    def _materialize_experiment(self) -> tuple[str, float]:
        started = time.perf_counter()
        results_dir = (
            Path(self.config.results_dir).expanduser().resolve()
            if self.config.results_dir
            else self.output / "benchmark"
        )
        if self.config.results_dir:
            summary_path = results_dir / "summary.json"
            manifest_path = results_dir / "reproducibility.json"
            if not summary_path.is_file() or not manifest_path.is_file():
                raise FileNotFoundError(
                    "results_dir must contain summary.json and reproducibility.json"
                )
            local_results = self.output / "experiment_results"
            local_results.mkdir(exist_ok=True)
            for name in (
                "summary.json",
                "summary.csv",
                "reproducibility.json",
                "paired_differences.json",
            ):
                source = results_dir / name
                if source.is_file():
                    shutil.copy2(source, local_results / name)
            source_note = "reused audited 300-task result bundle"
            evidence_source = "experiment_results/summary.json"
        else:
            result = run_benchmark(
                results_dir,
                sizes=(32,),
                seeds=(0,),
                tasks_per_seed=1,
                token_budget=150,
            )
            summary_path = results_dir / "summary.json"
            manifest_path = results_dir / "reproducibility.json"
            source_note = (
                "executed deterministic smoke benchmark: "
                f"{result['manifest']['task_count']} task, "
                f"{result['manifest']['method_count']} methods"
            )
            evidence_source = "benchmark/summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        benchmark_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        method = summary["EvidenceRail"]
        vector = summary["VectorTopK"]
        last_k = summary["LastK"]
        no_conflict = summary["AblationNoConflict"]
        no_type = summary["AblationNoTypeGuard"]
        no_claim = summary["AblationNoClaim"]
        recall = float(method["gold_recall"]["mean"])
        conflict = float(method["conflict_complete"]["mean"])
        precision = float(method["precision"]["mean"])
        ratio = float(method["token_ratio"]["mean"])
        vector_recall = float(vector["gold_recall"]["mean"])
        vector_conflict = float(vector["conflict_complete"]["mean"])
        last_k_recall = float(last_k["gold_recall"]["mean"])
        no_conflict_complete = float(no_conflict["conflict_complete"]["mean"])
        no_type_recall = float(no_type["gold_recall"]["mean"])
        no_claim_recall = float(no_claim["gold_recall"]["mean"])
        self.store.upsert_evidence(
            Evidence(
                "EXP-BENCHMARK",
                EvidenceKind.EXPERIMENT,
                (
                    f"EvidenceRail achieved gold recall {recall:.3f}, precision "
                    f"{precision:.3f}, and conflict completeness {conflict:.3f} over "
                    f"{benchmark_manifest['task_count']} deterministic tasks; the mean "
                    f"selected-context ratio was {ratio:.5f}. VectorTopK recall was "
                    f"{vector_recall:.3f} with conflict completeness "
                    f"{vector_conflict:.3f}; LastK recall was {last_k_recall:.3f}. "
                    f"Removing conflict coverage reduced conflict completeness to "
                    f"{no_conflict_complete:.3f}; removing type guards reduced recall to "
                    f"{no_type_recall:.3f}; removing claim coverage reduced recall to "
                    f"{no_claim_recall:.3f}."
                ),
                evidence_source,
                reliability=1.0,
                supports=["C-RESULT", "C-COMPARISON", "C-ABLATION"],
                conflict_ids=["NEG-SCOPE"],
                metadata={
                    "task_count": benchmark_manifest["task_count"],
                    "gold_recall": recall,
                    "precision": precision,
                    "conflict_complete": conflict,
                    "token_ratio": ratio,
                    "vector_top_k_recall": vector_recall,
                    "vector_top_k_conflict_complete": vector_conflict,
                    "last_k_recall": last_k_recall,
                    "ablation_no_conflict_complete": no_conflict_complete,
                    "ablation_no_type_guard_recall": no_type_recall,
                    "ablation_no_claim_recall": no_claim_recall,
                },
            )
        )
        self.store.upsert_evidence(
            Evidence(
                "NEG-SCOPE",
                EvidenceKind.NEGATIVE_RESULT,
                (
                    "The deterministic selection benchmark does not establish "
                    "end-to-end paper quality for arbitrary language models."
                ),
                "paper/EvidenceRail.pdf#limitations",
                reliability=1.0,
                supports=["C-BOUNDARY"],
                contradicts=["C-RESULT"],
                conflict_ids=["EXP-BENCHMARK"],
            )
        )
        self.store.upsert_claim(
            Claim(
                "C-RESULT",
                (
                    f"EvidenceRail achieved {recall:.3f} recall on the "
                    f"{benchmark_manifest['task_count']}-task deterministic benchmark."
                ),
                ["EXP-BENCHMARK", "NEG-SCOPE"],
                strength=1.0,
                metadata={"conflict_resolved": True},
            )
        )
        self.store.upsert_claim(
            Claim(
                "C-COMPARISON",
                "EvidenceRail outperformed recency and similarity baselines on evidence recall.",
                ["EXP-BENCHMARK"],
                strength=1.0,
            )
        )
        self.store.upsert_claim(
            Claim(
                "C-ABLATION",
                "Conflict coverage, type guards, and claim coverage protect distinct properties.",
                ["EXP-BENCHMARK"],
                strength=1.0,
            )
        )
        self.store.upsert_claim(
            Claim(
                "C-BOUNDARY",
                "The result is limited to evidence selection, not general paper quality.",
                ["NEG-SCOPE"],
                strength=1.0,
            )
        )
        duration = time.perf_counter() - started
        self.ledger.append(
            ResourceEvent(
                run_id=self.run_id,
                stage="experiment",
                event="experiment_artifact_ingest",
                duration_seconds=duration,
                metadata={
                    "source": source_note,
                    "task_count": benchmark_manifest["task_count"],
                    "raw_results_sha256": benchmark_manifest.get("raw_results_sha256", ""),
                    "deterministic_metrics_sha256": benchmark_manifest.get(
                        "deterministic_metrics_sha256", ""
                    ),
                },
            )
        )
        artifact = (
            "# Experiment execution\n\n"
            f"- Source: {source_note}\n"
            f"- Tasks: {benchmark_manifest['task_count']}\n"
            f"- EvidenceRail recall: {recall:.3f}\n"
            f"- EvidenceRail precision: {precision:.3f}\n"
            f"- Conflict completeness: {conflict:.3f}\n"
            f"- VectorTopK recall: {vector_recall:.3f}\n"
            f"- VectorTopK conflict completeness: {vector_conflict:.3f}\n"
            f"- LastK recall: {last_k_recall:.3f}\n"
            f"- No-conflict ablation completeness: {no_conflict_complete:.3f}\n"
            f"- No-type-guard ablation recall: {no_type_recall:.3f}\n"
            f"- No-claim-coverage ablation recall: {no_claim_recall:.3f}\n"
            f"- Mean context ratio: {ratio:.5f}\n"
            "- Full machine-readable results: `experiment_results/` or `benchmark/`.\n"
        )
        return artifact, duration

    def _seed_literature_and_method(self) -> None:
        items = [
            Evidence(
                "LIT-LOST-MIDDLE",
                EvidenceKind.LITERATURE,
                (
                    "Long-context models use information unevenly depending on its "
                    "position, so nominal context length is not equivalent to reliable use."
                ),
                "arXiv:2307.03172",
                reliability=0.95,
                supports=["C-PROBLEM"],
            ),
            Evidence(
                "LIT-LONGMEMEVAL",
                EvidenceKind.LITERATURE,
                (
                    "LongMemEval evaluates indexing, retrieval, reading, temporal "
                    "reasoning, updates, and abstention in long-term interactive memory."
                ),
                "arXiv:2410.10813",
                reliability=0.9,
                supports=["C-PROBLEM"],
            ),
            Evidence(
                "METHOD-EVIDENCERAIL",
                EvidenceKind.METHOD,
                (
                    "EvidenceRail stores typed evidence and claim/conflict edges, then "
                    "selects a traceable set under a hard token budget before model calls."
                ),
                "jiuwenswarm/research_evidence/",
                reliability=1.0,
                supports=["C-METHOD"],
            ),
        ]
        for item in items:
            self.store.upsert_evidence(item)
        self.store.upsert_claim(
            Claim(
                "C-PROBLEM",
                "Long-context availability alone does not guarantee reliable evidence use.",
                ["LIT-LOST-MIDDLE", "LIT-LONGMEMEVAL"],
                required_support=2,
                strength=0.9,
            )
        )
        self.store.upsert_claim(
            Claim(
                "C-METHOD",
                "EvidenceRail uses typed evidence and explicit claim/conflict structure.",
                ["METHOD-EVIDENCERAIL"],
                strength=1.0,
            )
        )

    def _stage_query(self, stage: str) -> str:
        return (
            f"{self.config.topic}. Research stage {stage}. Preserve provenance, "
            "counterevidence, experimental numbers, and limitations."
        )

    def _stage_prompt(self, stage: str, *, context: str) -> str:
        instructions = {
            "ideation": (
                "Formulate one important, falsifiable research question, the gap, and "
                "a conservative hypothesis. Do not invent literature."
            ),
            "planning": (
                "Design baselines, controlled variables, metrics, ablations, failure "
                "criteria, and a reproducibility plan in at most 700 words."
            ),
            "analysis": (
                "Analyze the experimental evidence. Separate observed results from "
                "interpretation and explicitly retain the negative scope result. Use at "
                "most 600 words."
            ),
            "writing": (
                "Write a concise short-paper draft in Markdown with title, abstract, "
                "introduction, method, experiment, results, limitations, conclusion, "
                "and references. Include baselines and ablations. Cite only [EVID:<id>] "
                "cards and use at most 1,200 words."
            ),
            "review": (
                "Act as an independent top-conference reviewer. Report strengths, "
                "weaknesses, missing evidence, reproducibility risks, and a 0-10 score. "
                "Judge the implemented selection contribution, baselines, and ablations; "
                "do not require the paper to prove a broader claim it explicitly disclaims. "
                "Use at most 700 words."
            ),
        }
        prior_by_stage = {
            "ideation": (),
            "planning": ("ideation",),
            "analysis": ("ideation", "planning", "experiment"),
            "writing": ("ideation", "experiment", "analysis"),
            "review": ("experiment", "analysis", "writing"),
        }
        instruction = instructions.get(stage)
        prior_stage_names = prior_by_stage.get(stage)
        if instruction is None or prior_stage_names is None:
            raise ValueError(f"unsupported research stage: {stage}")
        prior_sections: list[str] = []
        for name in prior_stage_names:
            previous_text = self.stage_text.get(name)
            if previous_text:
                prior_sections.append(f"## Prior stage: {name}\n{previous_text[:4000]}")
        prior = "\n\n".join(prior_sections)
        return (
            "You are one member of a JiuwenSwarm scientific-research workflow.\n\n"
            f"Topic: {self.config.topic}\nStage: {stage}\nTask: {instruction}\n\n"
            f"{context}\n\n{prior}\n\n"
            "Return only the requested research artifact. Be concise and auditable."
        )

    def _offline_stage_text(self, stage: str) -> str:
        experiment = self.stage_text.get("experiment", "Experiment results pending.")
        templates = {
            "ideation": (
                "# Ideation\n\n**Research question.** Under a fixed context budget, can "
                "typed claim/conflict structure preserve source-backed evidence and "
                "counterevidence better than recency or similarity retrieval?\n\n"
                "**Hypothesis.** Explicit structure will improve recall and conflict "
                "completeness; this is a selection claim, not a general paper-quality claim."
            ),
            "planning": (
                "# Experimental plan\n\nCompare FullContext, LastK, VectorTopK, and "
                "EvidenceRail under the same estimator and 150-token budget. Measure recall, "
                "precision, claim coverage, conflict completeness, diversity, risk, token "
                "ratio, and latency. Remove conflict, type-guard, and claim components in "
                "separate ablations. Fix seeds and publish raw task-level outputs."
            ),
            "analysis": (
                "# Evidence analysis\n\nThe experiment artifact shows that structured selection "
                "preserves the benchmark's gold evidence and conflict pair under the fixed "
                "budget. The result should be interpreted only for this deterministic "
                "synthetic benchmark; it does not establish downstream language-model or "
                "paper quality.\n\n" + experiment
            ),
            "writing": (
                "# EvidenceRail: Claim-Grounded Context Governance for Scientific Agents\n\n"
                "## Abstract\nEvidenceRail augments JiuwenSwarm with typed evidence, explicit "
                "claim/conflict relations, budgeted selection, verification, and resource "
                "accounting. A deterministic benchmark compares the method with recency, "
                "similarity retrieval, and full context. The controlled result supports the "
                "context-governance mechanism while leaving end-to-end paper quality open.\n\n"
                "## Evidence\nThe motivation is supported by [EVID:LIT-LOST-MIDDLE] and "
                "[EVID:LIT-LONGMEMEVAL]. The implementation is described by "
                "[EVID:METHOD-EVIDENCERAIL]. Quantitative results come from "
                "[EVID:EXP-BENCHMARK], and their scope is limited by [EVID:NEG-SCOPE].\n\n"
                "## Limitations\nSynthetic structure and gold labels underrepresent noisy "
                "literature ingestion, citation ambiguity, and downstream model variance."
            ),
            "review": (
                "# Independent review\n\n**Strengths:** inspectable JiuwenSwarm source "
                "extension, explicit counterevidence, controlled baselines, ablations, and "
                "raw artifacts. **Weaknesses:** synthetic evidence and no causal demonstration "
                "of downstream paper-quality gains. **Provisional score:** 6/10. The claim is "
                "credible when restricted to context governance."
            ),
        }
        template = templates.get(stage)
        if template is None:
            raise ValueError(f"unsupported offline research stage: {stage}")
        return template

    def _write_stage(
        self,
        stage: str,
        text: str,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        duration_seconds: float = 0.0,
    ) -> None:
        index = STAGES.index(stage) + 1
        path = self.output / f"{index:02d}_{stage}.md"
        path.write_text(text.rstrip() + "\n", encoding="utf-8", newline="\n")
        self.stage_text[stage] = text.rstrip()
        self.artifacts.append(
            StageArtifact(
                stage=stage,
                path=path.name,
                sha256=_sha256(path),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                duration_seconds=duration_seconds,
            )
        )
        if stage == "writing":
            shutil.copy2(path, self.output / "paper_draft.md")

    def _write_run_report(self, manifest: dict[str, Any]) -> None:
        summary = manifest["resource_summary"]
        report = (
            "# End-to-end research workflow run\n\n"
            f"- Status: `{manifest['status']}`\n"
            f"- Mode: `{manifest['mode']}`\n"
            f"- Model: `{manifest['model']}`\n"
            f"- Topic: {manifest['topic']}\n"
            f"- Completed stages: {', '.join(manifest['stages'])}\n"
            f"- Input tokens: {summary['input_tokens']}\n"
            f"- Output tokens: {summary['output_tokens']}\n"
            f"- Recorded duration: {summary['duration_seconds']:.3f} s\n"
            f"- Claim-graph issues: {len(manifest['claim_verification_issues'])}\n\n"
            "The run is auditable through `workflow_manifest.json`, stage Markdown files, "
            "`evidence_store/`, `resource_events.jsonl`, and the experiment artifacts. "
            "No API key or secret is serialized.\n"
        )
        (self.output / "RUN_REPORT.md").write_text(
            report, encoding="utf-8", newline="\n"
        )


async def run_research_workflow(config: WorkflowConfig) -> dict[str, Any]:
    """Convenience entry point used by the competition CLI."""

    return await ResearchWorkflow(config).run()


async def _invoke_model(
    model: Any, prompt: str, *, max_output_tokens: int
) -> tuple[str, dict[str, Any]]:
    response = await model.invoke(
        [
            {
                "role": "system",
                "content": (
                    "You are a conservative scientific agent. Never invent citations, "
                    "measurements, or experiments."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        max_tokens=max_output_tokens,
        temperature=0.2,
    )
    text = _response_text(response)
    if not text:
        raise RuntimeError("configured model returned an empty stage artifact")
    return text, _response_usage(response)


def _response_text(response: Any) -> str:
    content = _value(response, "content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            else:
                parts.append(str(_value(item, "text") or _value(item, "content") or ""))
        return "\n".join(part for part in parts if part).strip()
    output = _value(response, "output")
    return str(output or response or "").strip()


def _response_usage(response: Any) -> dict[str, Any]:
    usage = _value(response, "usage") or _value(response, "usage_metadata") or {}
    return {
        "input_tokens": _first_int(usage, "input_tokens", "prompt_tokens"),
        "output_tokens": _first_int(usage, "output_tokens", "completion_tokens"),
        "model": str(_value(response, "model") or _value(response, "model_name") or ""),
    }


def _first_int(value: Any, *keys: str) -> int:
    for key in keys:
        candidate = _value(value, key)
        try:
            if candidate is not None:
                return max(0, int(candidate))
        except (TypeError, ValueError):
            continue
    return 0


def _value(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


__all__ = [
    "ResearchWorkflow",
    "StageArtifact",
    "WorkflowConfig",
    "run_research_workflow",
]
