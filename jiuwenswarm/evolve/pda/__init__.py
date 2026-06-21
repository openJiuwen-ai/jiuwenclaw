# Copyright (c) Huawei Technologies, Co., Ltd. 2026. All rights reserved.
"""PDA-style One-shot AHE module — pluggable evolution algorithm.

PDA (Process/Decision/Action) algorithm independently implements the
Trace→Clean→Eval→Diagnose→Propose→Decide→Apply→Record closed loop.
Zero code overlap with existing algorithms (LLMProposer, RulePolicy, EvalPolicy).

Only shared contracts:
- ProposalGenerator.generate(batch) -> list[Proposal]
- DecisionPolicy.evaluate(proposal) -> DecisionResult
"""

from jiuwenswarm.evolve.pda.proposer import PdaProposer
from jiuwenswarm.evolve.pda.decision_policy import PdaDecisionPolicy
from jiuwenswarm.evolve.pda.experience_governor import ExperienceGovernor
from jiuwenswarm.evolve.pda.evaluator import TraceOutcomeEvaluator, TaskNameInferrer

__all__ = [
    "PdaProposer",
    "PdaDecisionPolicy",
    "ExperienceGovernor",
    "TraceOutcomeEvaluator",
    "TaskNameInferrer",
]
