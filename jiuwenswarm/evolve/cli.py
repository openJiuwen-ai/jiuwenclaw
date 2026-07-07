# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""CLI entry point for manual evolution triggering and inspection.

Usage::

    jiuwenswarm-evolve run --latest 20
    jiuwenswarm-evolve run --since "2026-06-10T00:00:00"
    jiuwenswarm-evolve run --trace-ids "abc123,def456,ghi789"
    jiuwenswarm-evolve run --trace-ids "abc123" --ahe
    jiuwenswarm-evolve list
    jiuwenswarm-evolve show <batch-id>
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def _build_pipeline_from_config(config: dict, use_ahe: bool = False) -> object:
    """Build an EvolutionPipeline from config.

    Args:
        config: Main jiuwenswarm config dict.
        use_ahe: If True, use PDA-style AHE algorithm instead of default generators.
    """
    from jiuwenswarm.evolve.pipeline import EvolutionPipeline
    from jiuwenswarm.evolve.proposal_generators.llm_proposer import LLMProposer  # noqa: F401  注册副作用
    from jiuwenswarm.evolve.decision_policies.rule_policy import RulePolicy  # noqa: F401  注册副作用
    from jiuwenswarm.evolve.decision_policies.eval_policy import EvalPolicy  # noqa: F401  注册副作用
    from jiuwenswarm.evolve.apply_writers.skill_writer import (
        SkillExperienceWriter,  # noqa: F401  注册副作用
    )
    from jiuwenswarm.evolve.apply_writers.memory_writer import (
        MemoryPolicyWriter,  # noqa: F401  注册副作用
    )
    from jiuwenswarm.evolve.apply_writers.training_writer import (
        TrainingCandidateWriter,  # noqa: F401  注册副作用
    )
    from jiuwenswarm.evolve.registry import (
        proposal_generators,
        decision_policies,
        apply_writers,
    )
    from jiuwenswarm.evolve import get_evolve_config
    from jiuwenswarm.evolve.storage import create_evolution_store

    evolve_cfg = get_evolve_config()
    pipeline_cfg = evolve_cfg.get("pipeline", {})

    # Build store
    store = create_evolution_store(config)

    # Trace reader for generators
    trace_reader = store._sqlite

    # Build generators from config (only llm_proposer supported)
    generator_names = pipeline_cfg.get("proposal_generators", ["llm_proposer"])
    generators: list = []
    for name in generator_names:
        if name == "llm_proposer" and name in proposal_generators:
            cls = proposal_generators.get(name)
            generators.append(cls(trace_reader=trace_reader))
        elif name == "rule_proposer":
            logger.info("rule_proposer is disabled, skipping")

    # Build writers (used by both default and PDA paths)
    writer_names = pipeline_cfg.get(
        "apply_writers", ["skill_writer", "memory_writer", "training_writer"]
    )
    writers: list = []
    for name in writer_names:
        if name in apply_writers:
            cls = apply_writers.get(name)
            if name == "training_writer":
                writers.append(cls(store=store))
            else:
                writers.append(cls())

    # Limits from config
    limits = pipeline_cfg.get("limits", {})
    max_proposals = limits.get("max_proposals_per_batch", 3)
    max_behavior = limits.get("max_behavior_proposals", 2)

    # If --ahe flag is set, use AHE algorithm instead of configured generators
    if use_ahe:
        from jiuwenswarm.evolve.ahe.proposer import AheProposer

        # Use the same resolved traces_db_path as SqliteStore so
        # OtelTraceAdapter reads from the identical database file.
        resolved_traces_db = getattr(
            trace_reader, '_traces_db_path', None,
        ) or pipeline_cfg.get("traces_db_path", "traces.db")

        ahe_proposer = AheProposer(
            trace_reader=trace_reader,
            store=store,
            skills_dir=str(Path(getattr(store, '_skills_dir', "skills"))),
            traces_db_path=resolved_traces_db,
        )
        generators = [ahe_proposer]
        logger.info("Using AHE algorithm: AheProposer")
        # Use AHE decision policy when --ahe is active
        from jiuwenswarm.evolve.ahe.decision_policy import AheDecisionPolicy

        policies = [AheDecisionPolicy(governor=None, model=None)]
        logger.info("Using AHE decision policy: AheDecisionPolicy")
        return EvolutionPipeline(
            generators=generators,
            policies=policies,
            writers=writers,
            store=store,
            max_proposals=limits.get("max_proposals_per_batch", 3),
            max_behavior_proposals=limits.get("max_behavior_proposals", 2),
        )

    # Build policies from config, or use defaults
    policy_names = pipeline_cfg.get("decision_policies", ["rule_policy", "eval_policy"])
    policies: list = []
    for name in policy_names:
        if name in decision_policies:
            policies.append(decision_policies.get(name)())

    return EvolutionPipeline(
        generators=generators,
        policies=policies,
        writers=writers,
        store=store,
        max_proposals=max_proposals,
        max_behavior_proposals=max_behavior,
    )


def _run_command(args: argparse.Namespace) -> int:
    """Execute ``jiuwenswarm-evolve run``."""
    from jiuwenswarm.common.config import get_config
    from jiuwenswarm.evolve.models import TraceBatch

    # Check for mutually exclusive selection parameters
    selection_params = [
        args.latest, args.since, args.benchmark_run_id, args.trace_ids
    ]
    used_params = [p for p in selection_params if p is not None]

    if len(used_params) > 1:
        print(
            "Error: --trace-ids cannot be used with --latest, --since, or --benchmark-run-id",
            file=sys.stderr
        )
        return 1

    if args.trace_ids:
        # Parse comma-separated trace IDs
        trace_ids = [id.strip() for id in args.trace_ids.split(",")]

        if not trace_ids:
            print("Error: No trace IDs provided", file=sys.stderr)
            return 1

        config = get_config()
        pipeline = _build_pipeline_from_config(config, use_ahe=args.ahe)
        trace_reader = pipeline._store._sqlite

        # Validate trace IDs exist in traces.db
        all_valid, missing_ids = trace_reader.validate_trace_ids(trace_ids)
        if not all_valid:
            print(
                "Error: The following trace IDs do not exist in traces.db:",
                file=sys.stderr
            )
            for missing in missing_ids:
                print(f"  - {missing}", file=sys.stderr)
            print("\nPlease verify the trace IDs and try again.", file=sys.stderr)
            return 1

        # Create TraceBatch
        batch = TraceBatch(trace_ids=trace_ids, source="manual")

        print(f"Running evolution pipeline on {len(trace_ids)} traces (batch={batch.batch_id})...")

        async def _run():
            return await pipeline.run(batch)

        result = asyncio.run(_run())

        print(f"\nPipeline complete: batch={result.batch_id}")
        print(f"  Proposals: {len(result.proposals)} "
              f"(active={result.active_count}, rejected={result.rejected_count})")
        print(f"  Decisions: {len(result.decision_results)}")
        print(f"  Applied:   {result.applied_count}")
        if result.errors:
            print(f"  Errors:    {len(result.errors)}")
            for e in result.errors:
                print(f"    - {e}")

        # Show proposals
        for p in result.proposals:
            print(f"\n  [{p.state.value}] {p.proposal_type} ({p.target_type.value})")
            print(f"    Root cause: {p.root_cause[:120]}")
            print(f"    Fix: {p.targeted_fix.get('action', 'N/A')}")
            print(f"    ID:  {p.proposal_id}")

        return 0

    # Original logic for --latest, --since, --benchmark-run-id
    config = get_config()
    pipeline = _build_pipeline_from_config(config, use_ahe=args.ahe)
    trace_reader = pipeline._store._sqlite  # type: ignore[union-attr]

    # Build TraceBatch from CLI args
    if args.latest:
        trace_ids = trace_reader.get_recent_trace_ids(limit=args.latest)
        batch = TraceBatch(trace_ids=trace_ids, source="manual")
    elif args.since:
        trace_ids = trace_reader.get_trace_ids_since(
            since=args.since,
            limit=args.latest or 100,
        )
        batch = TraceBatch(trace_ids=trace_ids, source="manual")
    elif args.benchmark_run_id:
        trace_ids = trace_reader.get_trace_ids_by_benchmark(
            benchmark_run_id=args.benchmark_run_id,
        )
        batch = TraceBatch(
            trace_ids=trace_ids,
            source="benchmark",
            metadata={"benchmark_run_id": args.benchmark_run_id},
        )
    else:
        print("Error: specify --latest, --since, --benchmark-run-id, or --trace-ids", file=sys.stderr)
        return 1

    if not trace_ids:
        print("No traces found matching criteria.", file=sys.stderr)
        return 0

    print(f"Running evolution pipeline on {len(trace_ids)} traces (batch={batch.batch_id})...")

    async def _run():
        return await pipeline.run(batch)

    result = asyncio.run(_run())

    print(f"\nPipeline complete: batch={result.batch_id}")
    print(f"  Proposals: {len(result.proposals)} "
          f"(active={result.active_count}, rejected={result.rejected_count})")
    print(f"  Decisions: {len(result.decision_results)}")
    print(f"  Applied:   {result.applied_count}")
    if result.errors:
        print(f"  Errors:    {len(result.errors)}")
        for e in result.errors:
            print(f"    - {e}")

    # Show proposals
    for p in result.proposals:
        print(f"\n  [{p.state.value}] {p.proposal_type} ({p.target_type.value})")
        print(f"    Root cause: {p.root_cause[:120]}")
        print(f"    Fix: {p.targeted_fix.get('action', 'N/A')}")
        print(f"    ID:  {p.proposal_id}")

    return 0


def _list_command() -> int:
    """Execute ``jiuwenswarm-evolve list``."""
    from jiuwenswarm.common.config import get_config
    from jiuwenswarm.evolve.storage import create_evolution_store

    config = get_config()
    store = create_evolution_store(config)

    batches = store.list_batches()
    if not batches:
        print("No evolution batches found.")
        return 0

    print(f"{'Batch ID':<36} {'Source':<12} {'Traces':>6} {'Proposals':>10}  Created")
    print("-" * 90)
    for b in batches:
        print(
            f"{b['batch_id']:<36} {b.get('source',''):<12} "
            f"{len(b.get('trace_ids',[])):>6} {b.get('proposal_count',0):>10}  "
            f"{b.get('created_at','')}"
        )
    return 0


def _show_command(batch_id: str) -> int:
    """Execute ``jiuwenswarm-evolve show <batch-id>``."""
    from jiuwenswarm.common.config import get_config
    from jiuwenswarm.evolve.storage import create_evolution_store

    config = get_config()
    store = create_evolution_store(config)

    data = store.get_batch(batch_id)
    if data is None:
        print(f"Batch not found: {batch_id}", file=sys.stderr)
        return 1

    batch = data["batch"]
    print(f"Batch: {batch['batch_id']}")
    print(f"Source: {batch.get('source')}")
    print(f"Created: {batch.get('created_at')}")
    print(f"Traces: {len(data.get('proposals', []))} proposals generated")
    print()

    for prop in data.get("proposals", []):
        state = prop.get("state", "?")
        icon = {"active": "✓", "rejected": "✗", "candidate": "○"}.get(state, "?")
        print(f"  [{icon} {state}] {prop.get('proposal_type')}")
        print(f"    Root cause: {prop.get('root_cause', '')[:200]}")
        print(f"    ID: {prop.get('proposal_id')}")
        print()

    for ar in data.get("apply_records", []):
        print(f"  Apply: {ar.get('status')} → {ar.get('target_store')}")
        print(f"    Reason: {ar.get('reason', '')}")
        print()

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="jiuwenswarm-evolve",
        description="JiuwenSwarm self-evolution CLI",
    )
    sub = parser.add_subparsers(dest="command")

    # run
    run_parser = sub.add_parser("run", help="Manually trigger evolution")
    run_parser.add_argument(
        "--latest", type=int, metavar="N",
        help="Process the N most recent traces",
    )
    run_parser.add_argument(
        "--since", type=str, metavar="ISO_TIMESTAMP",
        help="Process traces since this time",
    )
    run_parser.add_argument(
        "--benchmark-run-id", type=str, metavar="ID",
        help="Process traces from a benchmark run",
    )
    run_parser.add_argument(
        "--trace-ids", type=str, metavar="IDS",
        help="Comma-separated trace IDs to evolve (mutually exclusive with --latest/--since/--benchmark-run-id)",
    )
    run_parser.add_argument(
        "--ahe", action="store_true",
        help="Use PDA-style AHE algorithm instead of default proposal generators",
    )

    # list
    sub.add_parser("list", help="List past evolution batches")

    # show
    show_parser = sub.add_parser("show", help="Show batch details")
    show_parser.add_argument("batch_id", help="Batch ID to inspect")

    args = parser.parse_args()

    if args.command == "run":
        code = _run_command(args)
    elif args.command == "list":
        code = _list_command()
    elif args.command == "show":
        code = _show_command(args.batch_id)
    else:
        parser.print_help()
        code = 0

    raise SystemExit(code)


if __name__ == "__main__":
    main()
