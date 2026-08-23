# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for QualityGateRail.

Runtime-free: each test drives ``after_task_iteration`` with a synthetic
``AgentCallbackContext`` and asserts the gate's decision, query rewrite,
force-finish payload, verdict recording and JSON-lines log. No DeepAgent /
model runtime is stood up, so the tests are fast and deterministic.
"""

from __future__ import annotations

import json
import unittest
from typing import Optional

from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    TaskIterationInputs,
)

from jiuwenswarm.agents.harness.common.rails.quality_gate_rail import (
    GateVerdict,
    QualityGateRail,
)


def _make_ctx(output: str = "produced text", query: str = "write the section") -> AgentCallbackContext:
    inputs = TaskIterationInputs(
        iteration=1,
        loop_event=None,
        conversation_id="conv-1",
        query=query,
        result={"output": output, "result_type": "final"},
    )
    return AgentCallbackContext(agent=object(), inputs=inputs, extra={})


def _fixed_scorer(scores, feedback="add baselines B1 and B2"):
    """Return a stateful scorer yielding *scores* in order (last repeats)."""
    seq = list(scores) if isinstance(scores, (list, tuple)) else [scores]
    calls = {"n": 0}
    seen = []

    def scorer(text, context):
        seen.append((text, context))
        i = min(calls["n"], len(seq) - 1)
        calls["n"] += 1
        score = seq[i]
        return GateVerdict(
            score=score,
            passed=score >= 0.6,
            feedback=feedback,
            details={"call": calls["n"]},
        )

    scorer.seen = seen  # type: ignore[attr-defined]
    scorer.calls = calls  # type: ignore[attr-defined]
    return scorer


class TestQualityGateRail(unittest.IsolatedAsyncioTestCase):
    async def test_pass(self) -> None:
        rail = QualityGateRail(_fixed_scorer(0.9), threshold=0.6, gate_name="hypothesis_gate")
        ctx = _make_ctx()
        await rail.after_task_iteration(ctx)

        self.assertEqual(rail.last_decision, "pass")
        self.assertFalse(ctx.has_force_finish_request)
        self.assertIn("quality_gate", ctx.extra)
        self.assertTrue(ctx.extra["quality_gate"]["passed"])
        self.assertEqual(len(rail.verdicts), 1)
        self.assertAlmostEqual(rail.last_verdict.score, 0.9)

    async def test_scorer_injection_receives_output_and_context(self) -> None:
        scorer = _fixed_scorer(0.9)
        rail = QualityGateRail(scorer, threshold=0.6, gate_name="g")
        ctx = _make_ctx(output="THE PRODUCED TEXT", query="Q")
        await rail.after_task_iteration(ctx)

        self.assertEqual(len(scorer.seen), 1)  # type: ignore[attr-defined]
        text, context = scorer.seen[0]  # type: ignore[attr-defined]
        self.assertEqual(text, "THE PRODUCED TEXT")
        self.assertEqual(context["gate"], "g")
        self.assertEqual(context["query"], "Q")
        self.assertEqual(context["threshold"], 0.6)
        self.assertIn("result", context)

    async def test_revise_rewrites_query_and_forces_finish(self) -> None:
        rail = QualityGateRail(
            _fixed_scorer(0.3, feedback="strengthen the ablation study"),
            threshold=0.6,
            max_retries=1,
            on_fail="revise",
            gate_name="result_gate",
        )
        ctx = _make_ctx(query="run the experiments")
        await rail.after_task_iteration(ctx)

        self.assertEqual(rail.last_decision, "revise")
        # Query rewritten in place with the feedback (spec: inject into next round).
        self.assertIn("strengthen the ablation study", ctx.inputs.query)
        self.assertIn("run the experiments", ctx.inputs.query)
        self.assertIn("result_gate", ctx.inputs.query)
        # force-finish payload carries needs_revision + the revised query.
        self.assertTrue(ctx.has_force_finish_request)
        payload = ctx.consume_force_finish().result
        self.assertEqual(payload["status"], "needs_revision")
        self.assertEqual(payload["attempt"], 1)
        self.assertIn("strengthen the ablation study", payload["revised_query"])
        # revision also surfaced on ctx.extra
        self.assertIn("quality_gate_revision", ctx.extra)

    async def test_revise_then_pass(self) -> None:
        scorer = _fixed_scorer([0.3, 0.8])
        rail = QualityGateRail(scorer, threshold=0.6, max_retries=1, on_fail="revise", gate_name="g")

        ctx1 = _make_ctx()
        await rail.after_task_iteration(ctx1)
        self.assertEqual(rail.last_decision, "revise")

        ctx2 = _make_ctx()  # same gate instance -> retry counter persists
        await rail.after_task_iteration(ctx2)
        self.assertEqual(rail.last_decision, "pass")
        self.assertIn("quality_gate", ctx2.extra)

    async def test_revise_then_reject_when_exhausted(self) -> None:
        scorer = _fixed_scorer([0.3, 0.4])
        rail = QualityGateRail(scorer, threshold=0.6, max_retries=1, on_fail="revise", gate_name="g")

        await rail.after_task_iteration(_make_ctx())
        self.assertEqual(rail.last_decision, "revise")

        ctx2 = _make_ctx()
        await rail.after_task_iteration(ctx2)
        self.assertEqual(rail.last_decision, "reject")
        payload = ctx2.consume_force_finish().result
        self.assertEqual(payload["status"], "rejected")
        self.assertEqual(payload["reason"], "retries_exhausted")

    async def test_reject_immediate_when_on_fail_reject(self) -> None:
        rail = QualityGateRail(_fixed_scorer(0.3), threshold=0.6, on_fail="reject", gate_name="g")
        ctx = _make_ctx()
        await rail.after_task_iteration(ctx)

        self.assertEqual(rail.last_decision, "reject")
        payload = ctx.consume_force_finish().result
        self.assertEqual(payload["status"], "rejected")
        self.assertEqual(payload["reason"], "on_fail_reject")
        self.assertEqual(payload["attempt"], 0)

    async def test_threshold_is_authority_over_scorer_passed(self) -> None:
        # scorer claims passed=True but score below threshold -> gate fails.
        def scorer(text, context):
            return GateVerdict(score=0.5, passed=True, feedback="fix it")

        rail = QualityGateRail(scorer, threshold=0.6, on_fail="reject", gate_name="g")
        ctx = _make_ctx()
        await rail.after_task_iteration(ctx)
        self.assertEqual(rail.last_decision, "reject")

    async def test_scorer_exception_is_safe_reject(self) -> None:
        def boom(text, context):
            raise RuntimeError("scorer blew up")

        rail = QualityGateRail(boom, threshold=0.6, on_fail="reject", gate_name="g")
        ctx = _make_ctx()
        await rail.after_task_iteration(ctx)  # must not raise
        self.assertEqual(rail.last_decision, "reject")
        self.assertEqual(rail.last_verdict.score, 0.0)

    async def test_log_written_as_jsonl(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as d:
            log_path = Path(d) / "sub" / "gate.jsonl"
            rail = QualityGateRail(_fixed_scorer([0.3, 0.9]), threshold=0.6, max_retries=1, log_path=log_path, gate_name="g")
            await rail.after_task_iteration(_make_ctx())
            await rail.after_task_iteration(_make_ctx())
            lines = log_path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 2)
            rec0 = json.loads(lines[0])
            self.assertEqual(rec0["decision"], "revise")
            self.assertEqual(rec0["gate"], "g")
            self.assertAlmostEqual(rec0["score"], 0.3)
            rec1 = json.loads(lines[1])
            self.assertEqual(rec1["decision"], "pass")

    async def test_reset_clears_state(self) -> None:
        rail = QualityGateRail(_fixed_scorer([0.3, 0.3]), threshold=0.6, max_retries=1, gate_name="g")
        await rail.after_task_iteration(_make_ctx())
        self.assertEqual(rail._retries_used, 1)
        rail.reset()
        self.assertEqual(rail._retries_used, 0)
        self.assertEqual(rail.verdicts, [])

    def test_non_callable_scorer_rejected(self) -> None:
        with self.assertRaises(TypeError):
            QualityGateRail("not callable", threshold=0.6)  # type: ignore[arg-type]

    async def test_reject_decision_recorded_for_host(self) -> None:
        # Host-contract regression: request_force_finish is a no-op inside
        # after_task_iteration under current agent-core, so the host runner
        # must be able to drive reject purely off the rail instance's fields.
        rail = QualityGateRail(_fixed_scorer(0.1), threshold=0.6, on_fail="reject", gate_name="host_gate")
        ctx = _make_ctx()

        await rail.after_task_iteration(ctx)  # must not raise

        self.assertEqual(rail.last_decision, "reject")
        self.assertIsNotNone(rail.last_payload)
        self.assertEqual(rail.last_payload["status"], "rejected")
        self.assertEqual(rail.last_payload["gate"], "host_gate")
        self.assertEqual(rail.last_verdict.score, 0.1)
        self.assertEqual(rail.verdicts[-1], rail.last_verdict)


if __name__ == "__main__":
    unittest.main()
