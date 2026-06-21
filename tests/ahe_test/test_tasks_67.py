#!/usr/bin/env python3
"""Validate Tasks 6-7 inline (sync-only tests)."""
import asyncio
from jiuwenswarm.evolve.ahe.proposer import AheProposer
from jiuwenswarm.evolve.ahe.decision_policy import AheDecisionPolicy
from jiuwenswarm.evolve.models import (
    Proposal, ProposalTargetType, ProposalState, EvidenceRef, DecisionSuggestion
)
from jiuwenswarm.evolve.ahe.experience_governor import ExperienceGovernor

# AheProposer enforce_limits
prop = AheProposer.__new__(AheProposer)
prop._max_proposals = 3
prop._max_skill_proposals = 2

proposals = [
    Proposal(target_type=ProposalTargetType.SKILL, proposal_type='test',
             failure_evidence=[EvidenceRef(trace_id='a', description='e')],
             root_cause='r', targeted_fix={}, predicted_impact='p',
             proposal_id='p1', proposer_name='pda', state=ProposalState.CANDIDATE,
             metadata={'max_score': 0.9}),
    Proposal(target_type=ProposalTargetType.SKILL, proposal_type='test',
             failure_evidence=[EvidenceRef(trace_id='a', description='e')],
             root_cause='r', targeted_fix={}, predicted_impact='p',
             proposal_id='p2', proposer_name='pda', state=ProposalState.CANDIDATE,
             metadata={'max_score': 0.8}),
    Proposal(target_type=ProposalTargetType.SKILL, proposal_type='test',
             failure_evidence=[EvidenceRef(trace_id='a', description='e')],
             root_cause='r', targeted_fix={}, predicted_impact='p',
             proposal_id='p3', proposer_name='pda', state=ProposalState.CANDIDATE,
             metadata={'max_score': 0.7}),
]
result = prop._enforce_limits(proposals)
assert len(result) == 2, 'Expected 2 active, got %d' % len(result)
print('PASS: AheProposer limit enforcement')

# Proposal parsing
raw = [{'target_id': 'bash', 'target_type': 'skill', 'proposal_type': 'add',
        'failure_evidence': [{'trace_id': 'abc', 'description': 'err'}],
        'root_cause': 'rc', 'targeted_fix': {'action': 'fix'}, 'predicted_impact': 'pi',
        'operations': [{'op': 'add', 'new_content': 'nc', 'reason': 'r', 'evidence_refs': []}]}]
parsed = prop._parse_proposals(raw, 'batch-001')
assert len(parsed) == 1
assert parsed[0].proposer_name == 'pda_proposer'
assert 'operations' in parsed[0].metadata
print('PASS: AheProposer proposal parsing')

# AheDecisionPolicy RuleGate (sync)
policy = AheDecisionPolicy(governor=ExperienceGovernor(), model=None)

p_valid = Proposal(target_type=ProposalTargetType.SKILL, proposal_type='test',
                   failure_evidence=[EvidenceRef(trace_id='a', description='e')],
                   root_cause='r', targeted_fix={}, predicted_impact='p',
                   proposer_name='pda')
r = policy._rule_gate(p_valid)
assert r.blocking is False
print('PASS: RuleGate valid proposal passes')

p_empty = Proposal(target_type=ProposalTargetType.SKILL, proposal_type='test',
                   failure_evidence=[], root_cause='', targeted_fix={}, predicted_impact='',
                   proposer_name='pda')
r2 = policy._rule_gate(p_empty)
assert r2.blocking is True
assert 'empty_failure_evidence' in r2.failed_checks
assert 'empty_root_cause' in r2.failed_checks
assert 'empty_predicted_impact' in r2.failed_checks
print('PASS: RuleGate empty proposal blocked')

p_memory = Proposal(target_type=ProposalTargetType.MEMORY, proposal_type='test',
                    failure_evidence=[EvidenceRef(trace_id='a', description='e')],
                    root_cause='r', targeted_fix={}, predicted_impact='p',
                    proposer_name='pda')
r3 = policy._rule_gate(p_memory)
assert r3.blocking is True
assert 'unsupported_target_type_memory' in r3.failed_checks
print('PASS: RuleGate unsupported type blocked')

# LLM JSON parsing
parsed_json = AheDecisionPolicy._parse_llm_json('{"score": 0.8, "suggestion": "active", "reason": "ok"}')
assert parsed_json['score'] == 0.8
print('PASS: LLM JSON parsing')

parsed_md = AheDecisionPolicy._parse_llm_json(
    '```json\n{"score": 0.3, "suggestion": "rejected"}\n```')
assert parsed_md['score'] == 0.3
print('PASS: Markdown JSON parsing')

# Async: evaluate with no model should fallback gracefully
async def test_evaluate():
    r = await policy.evaluate(p_valid)
    assert isinstance(r, object)
    assert hasattr(r, 'blocking')
    print('PASS: evaluate() async works')

asyncio.run(test_evaluate())

# AheProposer parse_llm_json
parsed2 = AheProposer._parse_llm_json('{"proposals": [{"target_id": "test"}]}')
assert len(parsed2.get('proposals', [])) == 1
print('PASS: AheProposer JSON parsing')

# AheProposer summaries
from jiuwenswarm.evolve.ahe.models import TraceOutcome
failed = [
    ({"trace_id": "abc123", "input": {"message": "help"}, "output": {"content": "ok"}},
     TraceOutcome(trace_id="abc123", outcome="fail", score=0.1, reason="incomplete")),
]
summaries = prop._build_trace_summaries(failed)
assert summaries[0]["trace_id"] == "abc123"
assert summaries[0]["outcome"] == "fail"
print('PASS: trace summary building')

from jiuwenswarm.evolve.ahe.diagnosis.models import DiagnosisResult, DiagnosisIssue
diag = DiagnosisResult(mode="diagnose", issues=[
    DiagnosisIssue(issue_type="工具错误", summary="bash error", evidence="s7",
                   trace_id="abc123", span_index=7, root_cause="Missing path",
                   suggested_fix="Add path"),
], response="Found 1", iterations=5)
summary = AheProposer._build_diagnosis_summary(diag)
assert "bash error" in summary and "Missing path" in summary
print('PASS: diagnosis summary building')

from jiuwenswarm.evolve.models import GovernanceContext, ExperienceOperationType
ctx = GovernanceContext(skill_name="bash", current_count=5, max_count=10, can_add=True,
                        allowed_operations=[ExperienceOperationType.ADD])
gov_summary = AheProposer._build_governance_summary({"bash": ctx})
assert "bash" in gov_summary and "5/10" in gov_summary
print('PASS: governance summary building')

print()
print('ALL TASKS 6-7 TESTS PASSED')
