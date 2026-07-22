#!/usr/bin/env python3
"""Import verification and sync test runner for all AHE modules."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))

import importlib

errored = []
ok = []

def check_import(path, name):
    try:
        mod = importlib.import_module(path)
        if name:
            getattr(mod, name)
        ok.append(f"  OK: {path}" + (f".{name}" if name else ""))
    except Exception as e:
        errored.append(f"  FAIL: {path}.{name}: {e}")

def run_sync_tests(module_path, test_classes):
    """Import a module and run its sync test methods."""
    try:
        mod = importlib.import_module(module_path)
        for cls_name in test_classes:
            cls = getattr(mod, cls_name)
            instance = cls()
            for method_name in dir(cls):
                if method_name.startswith("test_") and not method_name.startswith("test_async"):
                    method = getattr(instance, method_name)
                    method()
                    ok.append(f"  PASS: {module_path}.{cls_name}.{method_name}")
    except Exception as e:
        errored.append(f"  FAIL: {module_path}: {e}")

# Layer 0: Models
check_import("jiuwenswarm.evolve.models", "Proposal")
check_import("jiuwenswarm.evolve.models", "DecisionResult")

# Layer 1: AHE models
check_import("jiuwenswarm.evolve.ahe.models", "TraceOutcome")

# Layer 2: OtelAdapter
check_import("jiuwenswarm.evolve.ahe.otel_adapter", "OtelTraceAdapter")

# Layer 3: DiagnosisAgent
check_import("jiuwenswarm.evolve.ahe.diagnosis", "DiagnosisAgent")
check_import("jiuwenswarm.evolve.ahe.diagnosis.tools", "DiagnosisToolExecutor")
check_import("jiuwenswarm.evolve.ahe.diagnosis.tools", "_truncate_tool_output")

# Layer 4: Evaluator
check_import("jiuwenswarm.evolve.ahe.evaluator", "TraceOutcomeEvaluator")
check_import("jiuwenswarm.evolve.ahe.evaluator", "TaskNameInferrer")

# Layer 5: Proposer, DecisionPolicy
check_import("jiuwenswarm.evolve.ahe.proposer", "AheProposer")
check_import("jiuwenswarm.evolve.ahe.decision_policy", "AheDecisionPolicy")

# Layer 6: CLI
check_import("jiuwenswarm.evolve.cli", "_build_pipeline_from_config")

# Framework models
check_import("jiuwenswarm.evolve.models", "EvidenceRef")
check_import("jiuwenswarm.evolve.models", "TraceBatch")

print("\n=== Import Verification ===")
for r in ok:
    print(r)
for r in errored:
    print(r)

if errored:
    print(f"\nFAILED: {len(errored)} imports failed")
    sys.exit(1)
else:
    print(f"\nALL IMPORTS OK ({len(ok)} checks)")

# Run sync tests
print("\n=== Running Sync Tests ===")
sync_errored = []

def test_ahe_models():
    from jiuwenswarm.evolve.ahe.models import TraceOutcome
    to = TraceOutcome(trace_id="t1", outcome="pass", score=0.9)
    assert to.outcome == "pass"
    try:
        TraceOutcome(trace_id="t1", outcome="invalid", score=0.5)
        assert False, "should raise"
    except ValueError:
        pass
    print("  PASS: AHE models")

def test_truncation():
    from jiuwenswarm.evolve.ahe.diagnosis.tools import _truncate_tool_output
    assert _truncate_tool_output("short", max_chars=1000) == "short"
    # Short enough to not trigger truncation
    short = _truncate_tool_output("hello world", max_chars=10000)
    assert short == "hello world"
    # Long content should be truncated
    long_content = "\n".join([f"line {i} padding data here for length" for i in range(2000)])
    result = _truncate_tool_output(long_content, max_chars=5000)
    assert "truncated" in result
    print("  PASS: Truncation")

def test_tool_executor():
    from jiuwenswarm.evolve.ahe.diagnosis.tools import DiagnosisToolExecutor
    nt = [{"id": "t1", "trace_id": "t1", "messages": [
        {"role": "user", "content": "hello", "tool_calls": []},
        {"role": "assistant", "content": "world", "tool_calls": [{"name": "bash", "input": "ls"}]},
    ], "input": {"message": "hello"}, "output": {"content": "world"}, "subagents": [], "total_tokens": 10}]
    executor = DiagnosisToolExecutor(normalized_traces=nt)
    r = executor.execute("read_trace", {"trace_id": "t1", "target": "overview"})
    assert r["message_count"] == 2
    r2 = executor.execute("read_trace", {"trace_id": "t1", "target": "messages"})
    assert r2["total_messages"] == 2
    r3 = executor.execute("read_trace", {"trace_id": "t1", "target": "tool_calls"})
    assert r3["total_tool_calls"] == 1
    r4 = executor.execute("search_trace", {"trace_id": "t1", "pattern": "hello"})
    assert len(r4["matches"]) >= 1
    r5 = executor.execute("list_traces", {})
    assert len(r5["traces"]) == 1
    r6 = executor.execute("submit_result", {"result": "{}"})
    assert r6 == "TASK_COMPLETED"
    r7 = executor.execute("unknown_tool", {})
    assert "error" in r7
    print("  PASS: Tool executor")

def test_task_name_inferrer():
    from jiuwenswarm.evolve.ahe.evaluator import TaskNameInferrer
    assert "skill_bash_abc123de" in TaskNameInferrer.infer({"id": "abc123def", "skill_name": "bash"})
    assert "task_" in TaskNameInferrer.infer({"id": "abc123def", "messages": [{"role": "user", "content": "帮我写"}]})
    assert TaskNameInferrer.infer({"id": "xyz"}) == "xyz"
    print("  PASS: TaskNameInferrer")

def test_evaluator_fast():
    from jiuwenswarm.evolve.ahe.evaluator import TraceOutcomeEvaluator
    ev = TraceOutcomeEvaluator()
    r = ev.evaluate_fast({"id": "t1", "status_code": "ERROR", "status_description": "timeout"})
    assert r.outcome == "fail"
    r2 = ev.evaluate_fast({"id": "t1", "output": ""})
    assert r2.outcome == "uncertain"
    print("  PASS: Evaluator fast")

def test_rule_gate():
    from jiuwenswarm.evolve.ahe.decision_policy import AheDecisionPolicy
    from jiuwenswarm.evolve.models import Proposal, ProposalTargetType, EvidenceRef
    policy = AheDecisionPolicy(model=None)
    p = Proposal(target_type=ProposalTargetType.SKILL, proposal_type="test",
                 failure_evidence=[EvidenceRef(trace_id="a", description="e")],
                 root_cause="r", targeted_fix={"suggestion": "fix"}, predicted_impact="p",
                 proposer_name="ahe")
    r = policy._rule_gate(p)
    assert r.blocking is False
    p2 = Proposal(target_type=ProposalTargetType.SKILL, proposal_type="test",
                  failure_evidence=[], root_cause="", targeted_fix={}, predicted_impact="",
                  proposer_name="ahe")
    r2 = policy._rule_gate(p2)
    assert r2.blocking is True
    print("  PASS: RuleGate")

def test_llm_parse():
    from jiuwenswarm.evolve.ahe.decision_policy import AheDecisionPolicy
    r = AheDecisionPolicy._parse_llm_json('{"score": 0.8, "suggestion": "active", "reason": "good"}')
    assert r["score"] == 0.8
    r2 = AheDecisionPolicy._parse_llm_json("```json\n{\"score\": 0.3}\n```")
    assert r2["score"] == 0.3
    r3 = AheDecisionPolicy._parse_llm_json("garbage")
    assert r3["score"] == 0.5
    print("  PASS: LLM JSON parse")

def test_proposer_limits():
    from jiuwenswarm.evolve.ahe.proposer import AheProposer
    from jiuwenswarm.evolve.models import Proposal, ProposalTargetType, ProposalState, EvidenceRef
    prop = AheProposer.__new__(AheProposer)
    prop._max_proposals = 3
    prop._max_skill_proposals = 2
    proposals = [Proposal(target_type=ProposalTargetType.SKILL, proposal_type="t",
                          failure_evidence=[EvidenceRef(trace_id="a", description="e")],
                          root_cause="r", targeted_fix={}, predicted_impact="p",
                          proposal_id=f"p{i}", proposer_name="ahe", state=ProposalState.CANDIDATE,
                          metadata={"max_score": 1.0 - i * 0.1}) for i in range(5)]
    result = prop._enforce_limits(proposals)
    assert len(result) == 2
    print("  PASS: Proposer limits")

def test_proposer_parse():
    from jiuwenswarm.evolve.ahe.proposer import AheProposer
    prop = AheProposer.__new__(AheProposer)
    raw = [{"target_id": "bash", "target_type": "skill", "proposal_type": "add",
            "failure_evidence": [{"trace_id": "abc", "description": "err"}],
            "root_cause": "rc", "targeted_fix": {"action": "fix"}, "predicted_impact": "pi",
            "operations": [{"op": "add", "new_content": "nc", "reason": "r", "evidence_refs": []}]}]
    parsed = prop._parse_proposals(raw, "batch-001")
    assert len(parsed) == 1
    assert parsed[0].proposer_name == "ahe_proposer"
    print("  PASS: Proposer parse")

sync_tests = [
    test_ahe_models,
    test_truncation,
    test_tool_executor,
    test_task_name_inferrer,
    test_evaluator_fast,
    test_rule_gate,
    test_llm_parse,
    test_proposer_limits,
    test_proposer_parse,
]

for test_fn in sync_tests:
    try:
        test_fn()
    except Exception as e:
        import traceback
        sync_errored.append((test_fn.__name__, str(e)))
        traceback.print_exc()

print("\n=== Sync Test Results ===")
passed = len(sync_tests) - len(sync_errored)
failed = len(sync_errored)
print(f"Passed: {passed}, Failed: {failed}")
if sync_errored:
    for name, err in sync_errored:
        print(f"  FAIL: {name}: {err}")
    sys.exit(1)
else:
    print("ALL SYNC TESTS PASSED")
