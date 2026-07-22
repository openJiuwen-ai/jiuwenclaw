# Copyright (c) Huawei Technologies, Co., Ltd. 2026. All rights reserved.
"""PDA-style One-shot AHE module — pluggable evolution algorithm.

PDA (Process/Decision/Action) algorithm independently implements the
Trace→Clean→Eval→Diagnose→Propose→Decide→Apply→Record closed loop.
Zero code overlap with existing algorithms (LLMProposer, RulePolicy, EvalPolicy).

Only shared contracts:
- ProposalGenerator.generate(batch) -> list[Proposal]
- DecisionPolicy.evaluate(proposal) -> DecisionResult
"""

from jiuwenswarm.evolve.ahe.proposer import AheProposer
from jiuwenswarm.evolve.ahe.decision_policy import AheDecisionPolicy
from jiuwenswarm.evolve.ahe.evaluator import TraceOutcomeEvaluator, TaskNameInferrer

__all__ = [
    "AheProposer",
    "AheDecisionPolicy",
    "TraceOutcomeEvaluator",
    "TaskNameInferrer",
]
