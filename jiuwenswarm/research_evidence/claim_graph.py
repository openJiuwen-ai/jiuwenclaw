# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Claim-evidence graph and deterministic grounding checks."""

from __future__ import annotations

from jiuwenswarm.research_evidence.conflicts import conflict_map
from jiuwenswarm.research_evidence.schemas import Claim, Evidence, VerificationIssue
from jiuwenswarm.research_evidence.text import extract_numbers


class ClaimEvidenceGraph:
    """Validate that research claims are supported, bounded, and traceable."""

    def __init__(self, evidence: list[Evidence], claims: list[Claim]) -> None:
        self.evidence = {item.evidence_id: item for item in evidence}
        self.claims = {claim.claim_id: claim for claim in claims}
        self.conflicts = conflict_map(evidence)

    def verify_all(self) -> list[VerificationIssue]:
        issues: list[VerificationIssue] = []
        for claim_id in sorted(self.claims):
            issues.extend(self.verify_claim(self.claims[claim_id]))
        return issues

    def verify_claim(self, claim: Claim) -> list[VerificationIssue]:
        issues: list[VerificationIssue] = []
        linked = [self.evidence[item] for item in claim.evidence_ids if item in self.evidence]
        missing = [item for item in claim.evidence_ids if item not in self.evidence]
        if missing:
            issues.append(
                VerificationIssue(
                    "missing_evidence",
                    claim.claim_id,
                    f"Referenced evidence does not exist: {', '.join(missing)}",
                    evidence_ids=missing,
                )
            )
        supporting = [item for item in linked if claim.claim_id in item.supports]
        contradicting = [item for item in linked if claim.claim_id in item.contradicts]
        if len(supporting) < claim.required_support:
            issues.append(
                VerificationIssue(
                    "insufficient_support",
                    claim.claim_id,
                    f"Requires {claim.required_support} supporting item(s), found {len(supporting)}.",
                    evidence_ids=[item.evidence_id for item in supporting],
                )
            )
        if contradicting and not bool(claim.metadata.get("conflict_resolved")):
            issues.append(
                VerificationIssue(
                    "unresolved_contradiction",
                    claim.claim_id,
                    "Linked evidence contains an unresolved contradiction.",
                    evidence_ids=[item.evidence_id for item in contradicting],
                )
            )

        claim_numbers = extract_numbers(claim.text)
        evidence_text = " ".join(
            f"{item.content} {item.summary} {item.metadata}" for item in linked
        )
        evidence_numbers = set(extract_numbers(evidence_text))
        unsupported_numbers = [value for value in claim_numbers if value not in evidence_numbers]
        if unsupported_numbers:
            issues.append(
                VerificationIssue(
                    "unsupported_number",
                    claim.claim_id,
                    "Numeric literals are absent from linked evidence: "
                    + ", ".join(unsupported_numbers),
                    evidence_ids=[item.evidence_id for item in linked],
                )
            )

        if supporting:
            mean_reliability = sum(item.reliability for item in supporting) / len(supporting)
            if claim.strength > mean_reliability + 0.15:
                issues.append(
                    VerificationIssue(
                        "overstated_claim",
                        claim.claim_id,
                        "Claim strength exceeds the reliability of its supporting evidence.",
                        severity="warning",
                        evidence_ids=[item.evidence_id for item in supporting],
                    )
                )
        return issues

    def to_mermaid(self) -> str:
        """Render a compact graph for documentation and human audit."""

        lines = ["graph LR"]
        for claim in sorted(self.claims.values(), key=lambda item: item.claim_id):
            lines.append(f'  {self._node(claim.claim_id)}["{_escape(claim.claim_id)}"]')
            for evidence_id in claim.evidence_ids:
                lines.append(f'  {self._node(evidence_id)}["{_escape(evidence_id)}"]')
                relation = "supports"
                evidence = self.evidence.get(evidence_id)
                if evidence and claim.claim_id in evidence.contradicts:
                    relation = "contradicts"
                lines.append(
                    f"  {self._node(evidence_id)} -->|{relation}| {self._node(claim.claim_id)}"
                )
        return "\n".join(lines) + "\n"

    @staticmethod
    def _node(identifier: str) -> str:
        return "n" + "".join(character if character.isalnum() else "_" for character in identifier)


def _escape(text: str) -> str:
    return str(text).replace('"', "'")
