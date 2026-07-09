# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Pluggable Proposal generators."""

from jiuwenswarm.evolve.proposal_generators.base import ProposalGenerator
from jiuwenswarm.evolve.proposal_generators.rule_proposer import RuleProposer

__all__ = ["ProposalGenerator", "RuleProposer"]
