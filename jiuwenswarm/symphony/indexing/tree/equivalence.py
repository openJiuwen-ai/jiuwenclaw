"""Strict terminal-branch Skill equivalence normalization.

The normal taxonomy builder decides where a Skill belongs.  This module runs
after that work and only asks a narrower question inside each terminal branch:
which individual Skills are mutually substitutable for the same user request?
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from itertools import combinations
import json
from pathlib import Path
import re
from time import perf_counter
from typing import Callable, Iterable, Mapping, TYPE_CHECKING
import unicodedata

from .prompts import (
    EQUIVALENCE_CANDIDATE_PROMPT,
    EQUIVALENCE_CORRECTION_PROMPT,
    EQUIVALENCE_GROUP_AUDIT_PROMPT,
    EQUIVALENCE_PAIRWISE_PROMPT,
)
from .schema import Skill, TreeNode, parse_json_from_response

if TYPE_CHECKING:
    from .builder import TreeBuilder


EQUIVALENCE_PROTOCOL_VERSION = "terminal-skill-equivalence-v1"
_PROTOCOL_TEXT = "\n".join(
    (
        EQUIVALENCE_PROTOCOL_VERSION,
        EQUIVALENCE_CANDIDATE_PROMPT,
        EQUIVALENCE_PAIRWISE_PROMPT,
        EQUIVALENCE_GROUP_AUDIT_PROMPT,
        EQUIVALENCE_CORRECTION_PROMPT,
    )
)
EQUIVALENCE_PROTOCOL_HASH = hashlib.sha256(_PROTOCOL_TEXT.encode("utf-8")).hexdigest()

_HARD_DIMENSIONS = (
    "primary_action",
    "target_object",
    "input_precondition",
    "result_or_side_effect",
    "specialized_scope",
    "user_visible_platform",
    "bundle_breadth",
)
_DIMENSION_VALUES = {"same", "different", "unknown"}
_VERDICTS = {"equivalent", "not_equivalent", "insufficient_evidence"}
_REASON_CODES = {
    "mutual_substitute",
    "action_mismatch",
    "object_mismatch",
    "input_mismatch",
    "output_or_side_effect_mismatch",
    "scope_mismatch",
    "platform_mismatch",
    "bundle_mismatch",
    "not_mutually_substitutable",
    "insufficient_description",
}
_PAIR_BATCH_SIZE = 10
_CANDIDATE_ANCHOR_BATCH_SIZE = 24
_MAX_CORRECTION_ECHO_CHARS = 6000
_CONTENT_SUMMARY_CHARS = 1600
_EQUIVALENCE_GROUP_ID_RE = re.compile(r"^equiv-[0-9a-f]{16}$")
_CAPABILITY_FIELD_LIMITS = {
    "name": 80,
    "description": 300,
    "select_when": 300,
    "dont_select_when": 300,
}


class EquivalenceProtocolError(RuntimeError):
    """The model response or resulting grouping violated the equivalence protocol."""


@dataclass(frozen=True)
class _Scope:
    node: TreeNode
    path: tuple[str, ...]
    skills: tuple[Skill, ...]

    @property
    def path_text(self) -> str:
        return "/".join(self.path)


@dataclass(frozen=True)
class _GroupPlan:
    group_id: str
    name: str
    description: str
    select_when: str
    dont_select_when: str
    members: tuple[Skill, ...]
    audit_passed: bool


@dataclass
class _ScopePlan:
    scope: _Scope
    groups: list[_GroupPlan]
    positive_edges: set[tuple[str, str]]
    state: dict


def _canonical_pair(left: str, right: str) -> tuple[str, str]:
    return (left, right) if left < right else (right, left)


def equivalence_skill_hash(skill: Skill) -> str:
    """Return the stable content hash used by incremental equivalence caching."""
    payload = {
        "id": skill.id,
        "name": skill.name,
        "description": skill.description,
        "source_description": skill.source_description,
        "select_when": skill.select_when,
        "dont_select_when": skill.dont_select_when,
        "content": skill.content,
    }
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _exact_keys(payload: Mapping, expected: set[str], label: str) -> None:
    actual = set(payload)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise EquivalenceProtocolError(f"{label} keys invalid; missing={missing}, unknown={unknown}")


def _require_json_string(value: object, label: str, *, strip: bool = False) -> str:
    """Return a JSON string without coercing malformed structured values."""

    if not isinstance(value, str):
        raise EquivalenceProtocolError(f"{label} must be a string")
    return value.strip() if strip else value


def summarize_equivalence_scopes(
    scopes: Iterable[dict],
    *,
    status: str,
    expected_input_count: int,
) -> dict:
    """Return explicit coverage, mapping, clique, and audit summary fields."""

    normalized_scopes = [dict(scope) for scope in scopes if isinstance(scope, dict)]
    input_ids: list[str] = []
    output_ids: list[str] = []
    skill_mapping: list[dict] = []
    group_count = 0
    clique_complete = True
    audit_complete = True
    for scope in normalized_scopes:
        scope_path = str(scope.get("scope_path") or "")
        scope_cid = scope.get("scope_cid")
        input_ids.extend(
            str(item.get("skill_id") or "")
            for item in scope.get("skills", [])
            if isinstance(item, dict) and str(item.get("skill_id") or "")
        )
        effective_pairs = {
            _canonical_pair(
                str(item.get("left_skill_id") or ""),
                str(item.get("right_skill_id") or ""),
            )
            for item in scope.get("pairwise_decisions", [])
            if isinstance(item, dict)
            and str(item.get("effective_verdict") or item.get("verdict") or "") == "equivalent"
            and str(item.get("left_skill_id") or "")
            and str(item.get("right_skill_id") or "")
        }
        for group in scope.get("groups", []):
            if not isinstance(group, dict):
                clique_complete = False
                audit_complete = False
                continue
            group_count += 1
            group_id = str(group.get("group_id") or "")
            members = [str(value) for value in group.get("member_skill_ids", []) if str(value)]
            output_ids.extend(members)
            if len(members) > 1:
                clique_complete = clique_complete and all(
                    _canonical_pair(left, right) in effective_pairs
                    for left, right in combinations(sorted(members), 2)
                )
                audit_complete = audit_complete and bool(group.get("audit_passed"))
            for skill_id in members:
                skill_mapping.append(
                    {
                        "skill_id": skill_id,
                        "scope_path": scope_path,
                        "scope_cid": scope_cid,
                        "group_id": group_id,
                    }
                )
    unique_membership = len(output_ids) == len(set(output_ids))
    coverage_complete = (
        status == "complete"
        and unique_membership
        and sorted(output_ids) == sorted(input_ids)
        and len(input_ids) == int(expected_input_count)
    )
    return {
        "input_skill_count": int(expected_input_count),
        "output_skill_count": len(output_ids),
        "scope_count": len(normalized_scopes),
        "group_count": group_count,
        "skill_mapping": sorted(
            skill_mapping,
            key=lambda item: (item["skill_id"], item["scope_path"], item["group_id"]),
        ),
        "invariants": {
            "validated": status == "complete",
            "skill_coverage_complete": coverage_complete,
            "unique_skill_membership": status == "complete" and unique_membership,
            "complete_link_cliques": status == "complete" and clique_complete,
            "multi_member_groups_audited": status == "complete" and audit_complete,
        },
    }


def validate_equivalence_report(
    report: object,
    *,
    expected_protocol_hash: str = EQUIVALENCE_PROTOCOL_HASH,
    expected_incremental_signature: str | None = None,
) -> None:
    """Validate persisted sidecar state before reuse or publication."""

    if not isinstance(report, dict) or report.get("status") != "complete":
        raise EquivalenceProtocolError("equivalence report is missing or incomplete")
    if str(report.get("protocol_hash") or "") != str(expected_protocol_hash):
        raise EquivalenceProtocolError("equivalence report protocol hash is incompatible")
    if expected_incremental_signature is not None and str(
        report.get("incremental_signature") or ""
    ) != str(expected_incremental_signature):
        raise EquivalenceProtocolError("equivalence report incremental signature is incompatible")
    scopes = report.get("scopes")
    if not isinstance(scopes, list) or not scopes:
        raise EquivalenceProtocolError("equivalence report has no scopes")

    all_skill_ids: list[str] = []
    seen_scope_cids: set[str] = set()
    seen_group_ids: set[str] = set()
    for scope_index, scope in enumerate(scopes):
        if not isinstance(scope, dict):
            raise EquivalenceProtocolError(f"equivalence scope {scope_index} is not an object")
        scope_cid = str(scope.get("scope_cid") or "").strip()
        if not scope_cid or scope_cid in seen_scope_cids:
            raise EquivalenceProtocolError(f"equivalence scope {scope_index} has an invalid scope CID")
        seen_scope_cids.add(scope_cid)
        raw_skills = scope.get("skills")
        if not isinstance(raw_skills, list):
            raise EquivalenceProtocolError(f"equivalence scope {scope_index} has no skills array")
        skill_ids = [
            str(item.get("skill_id") or "")
            for item in raw_skills
            if isinstance(item, dict) and str(item.get("skill_id") or "")
        ]
        if len(skill_ids) != len(raw_skills) or len(skill_ids) != len(set(skill_ids)):
            raise EquivalenceProtocolError(f"equivalence scope {scope_index} has invalid Skill ids")
        skill_set = set(skill_ids)
        all_skill_ids.extend(skill_ids)
        skill_hashes = scope.get("skill_hashes")
        if not isinstance(skill_hashes, dict) or set(map(str, skill_hashes)) != skill_set:
            raise EquivalenceProtocolError(f"equivalence scope {scope_index} has incomplete Skill hashes")

        seen_decisions: set[tuple[str, str]] = set()
        for decision in scope.get("pairwise_decisions") or []:
            if not isinstance(decision, dict):
                raise EquivalenceProtocolError(f"equivalence scope {scope_index} has an invalid pair decision")
            left = str(decision.get("left_skill_id") or "")
            right = str(decision.get("right_skill_id") or "")
            if left not in skill_set or right not in skill_set or left == right:
                raise EquivalenceProtocolError(f"equivalence scope {scope_index} has an out-of-scope pair")
            pair = _canonical_pair(left, right)
            if pair in seen_decisions:
                raise EquivalenceProtocolError(f"equivalence scope {scope_index} has duplicate pair decisions")
            seen_decisions.add(pair)
            if str(decision.get("protocol_hash") or "") != str(expected_protocol_hash):
                raise EquivalenceProtocolError(f"equivalence scope {scope_index} has a stale pair decision")
            if str(decision.get("effective_verdict") or "") not in _VERDICTS:
                raise EquivalenceProtocolError(f"equivalence scope {scope_index} has an invalid effective verdict")
        groups = scope.get("groups")
        if not isinstance(groups, list):
            raise EquivalenceProtocolError(f"equivalence scope {scope_index} has no groups array")
        for group in groups:
            if not isinstance(group, dict):
                raise EquivalenceProtocolError(f"equivalence scope {scope_index} has an invalid group")
            group_id = str(group.get("group_id") or "").strip()
            if not group_id or group_id in seen_group_ids:
                raise EquivalenceProtocolError(f"equivalence scope {scope_index} has an invalid group id")
            seen_group_ids.add(group_id)

    if len(all_skill_ids) != len(set(all_skill_ids)):
        raise EquivalenceProtocolError("equivalence report duplicates Skills across scopes")
    expected_input_count = int(report.get("input_skill_count") or len(all_skill_ids))
    summary = summarize_equivalence_scopes(
        scopes,
        status="complete",
        expected_input_count=expected_input_count,
    )
    if not all(summary["invariants"].values()):
        raise EquivalenceProtocolError("equivalence report failed coverage/clique/audit invariants")
    for field in ("input_skill_count", "output_skill_count", "scope_count", "group_count"):
        if int(report.get(field) or 0) != int(summary[field]):
            raise EquivalenceProtocolError(f"equivalence report summary field '{field}' is inconsistent")


def equivalence_build_complete_event(report: Mapping) -> dict:
    """Return a terminal audit event bound to the exact persisted scope state."""

    scopes = report.get("scopes") if isinstance(report, Mapping) else None
    normalized_scopes = scopes if isinstance(scopes, list) else []
    scopes_payload = json.dumps(
        normalized_scopes,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "event": "build_complete",
        "status": str(report.get("status") or ""),
        "protocol_hash": str(report.get("protocol_hash") or ""),
        "incremental_signature": str(report.get("incremental_signature") or ""),
        "scope_count": len(normalized_scopes),
        "input_skill_count": int(report.get("input_skill_count") or 0),
        "output_skill_count": int(report.get("output_skill_count") or 0),
        "scopes_sha256": hashlib.sha256(scopes_payload.encode("utf-8")).hexdigest(),
    }


def validate_equivalence_audit(
    events: object,
    *,
    report: Mapping,
) -> None:
    """Require a complete JSONL audit event bound to the current report."""

    if not isinstance(events, list) or not events or not all(isinstance(event, dict) for event in events):
        raise EquivalenceProtocolError("equivalence audit is empty or malformed")
    expected = equivalence_build_complete_event(report)
    if not any(
        event.get("event") == "build_complete"
        and all(event.get(key) == value for key, value in expected.items())
        for event in events
    ):
        raise EquivalenceProtocolError("equivalence audit has no completion event for the current report")


def read_last_equivalence_audit_event(path: Path, *, tail_bytes: int = 65_536) -> dict:
    """Read only the final JSONL event; prompts/raw responses may make the file large."""

    with path.open("rb") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        handle.seek(max(0, size - max(1024, int(tail_bytes))))
        tail = handle.read()
    for raw_line in reversed(tail.splitlines()):
        if not raw_line.strip():
            continue
        event = json.loads(raw_line.decode("utf-8"))
        if not isinstance(event, dict):
            raise EquivalenceProtocolError("final equivalence audit event is not an object")
        return event
    raise EquivalenceProtocolError("equivalence audit is empty")


class EquivalenceNormalizer:
    """Build and apply deterministic equivalence groups within terminal branches."""

    def __init__(
        self,
        builder: "TreeBuilder",
        *,
        audit_path=None,
        report_path=None,
    ) -> None:
        self._builder = builder
        build_cfg = builder._manager_config.build
        self.all_pairs_scope_limit = max(1, int(getattr(build_cfg, "equivalence_all_pairs_scope_limit", 12)))
        self.candidate_neighbors = max(1, int(getattr(build_cfg, "equivalence_candidate_neighbors", 8)))
        self.max_pairwise_pairs = max(1, int(getattr(build_cfg, "equivalence_max_pairwise_pairs", 10000)))
        output_dir = builder.output_path.parent
        self.audit_path = audit_path or output_dir / "equivalence_audit.jsonl"
        self.report_path = report_path or output_dir / "equivalence_report.json"
        self._reset_run()

    @property
    def protocol_hash(self) -> str:
        return EQUIVALENCE_PROTOCOL_HASH

    def _reset_run(self) -> None:
        self._audit_events: list[dict] = []
        self._scope_states: list[dict] = []
        self._pair_budget_used = 0
        self._pair_budget_limit = self.max_pairwise_pairs
        self._current_stage = "initializing"
        self._started_at = perf_counter()
        self._llm_calls_start = int(getattr(self._builder, "_llm_calls", 0) or 0)
        self._metrics = {
            "scopes": 0,
            "skills": 0,
            "candidate_pairs": 0,
            "pairwise_equivalent": 0,
            "pairwise_not_equivalent": 0,
            "pairwise_insufficient_evidence": 0,
            "pairwise_cache_hits": 0,
            "protocol_validation_errors": 0,
            "correction_attempts": 0,
            "audit_calls": 0,
            "audit_conflicts": 0,
            "audit_reclusters": 0,
            "singleton_groups": 0,
            "multi_member_groups": 0,
        }

    def normalize(self, root: TreeNode, verbose: bool = False) -> dict:
        """Normalize every terminal branch, mutating the tree only after all plans pass."""
        self._reset_run()
        try:
            self._current_stage = "scope_discovery"
            scopes = self._collect_scopes(root)
            self._validate_unique_skills(scopes)
            self._metrics["scopes"] = len(scopes)
            self._metrics["skills"] = sum(len(scope.skills) for scope in scopes)

            plans: list[_ScopePlan] = []
            for scope in scopes:
                plans.append(self._plan_scope(scope, cached_state=None))

            self._current_stage = "tree_rewrite"
            for plan in plans:
                self._apply_plan(plan)
            self._validate_final_tree(root, scopes, plans)
        except Exception as exc:
            report = self._build_report(status="failed", error=str(exc), failure_stage=self._current_stage)
            self._write_artifacts(report)
            raise

        report = self._build_report(status="complete")
        self._write_artifacts(report)
        if verbose:
            print(
                "Equivalence normalization: "
                f"{self._metrics['skills']} skills in {self._metrics['scopes']} terminal branches, "
                f"{self._metrics['multi_member_groups']} multi-member groups."
            )
        return report

    def normalize_scope(
        self,
        scope: TreeNode,
        scope_path: str | tuple[str, ...],
        *,
        cached_state: dict | None = None,
        max_pairwise_pairs: int | None = None,
    ) -> dict:
        """Normalize one branch for branch-local incremental rebuilds.

        The returned state contains the audit events needed by an incremental
        workflow.  No global sidecar is written by this method.
        """
        self._reset_run()
        try:
            if max_pairwise_pairs is not None:
                self._pair_budget_limit = max(0, int(max_pairwise_pairs))
            path = self._normalize_scope_path(scope_path, scope.id)
            skills = self._skills_from_scope_node(scope)
            scoped = _Scope(scope, path, tuple(sorted(skills, key=lambda item: item.id)))
            self._validate_unique_skills([scoped])
            self._metrics["scopes"] = 1
            self._metrics["skills"] = len(scoped.skills)
            plan = self._plan_scope(scoped, cached_state=cached_state)
            self._apply_plan(plan)
        except Exception as exc:
            report = self._build_report(
                status="failed",
                error=str(exc),
                failure_stage=self._current_stage,
            )
            self._write_artifacts(report)
            raise
        state = dict(plan.state)
        state["audit_events"] = list(self._audit_events)
        state["run_metrics"] = self._run_metrics()
        state["status"] = "complete"
        state["model"] = str(getattr(self._builder, "model", "") or "")
        return state

    @staticmethod
    def _normalize_scope_path(scope_path: str | tuple[str, ...], fallback: str) -> tuple[str, ...]:
        if isinstance(scope_path, str):
            parts = tuple(part for part in scope_path.split("/") if part)
        else:
            parts = tuple(str(part) for part in scope_path if str(part))
        return parts or (fallback,)

    def _collect_scopes(self, root: TreeNode) -> list[_Scope]:
        scopes: list[_Scope] = []

        def walk(
            node: TreeNode,
            parent_path: tuple[str, ...],
            *,
            synthetic_root: bool = False,
        ) -> None:
            path = (*parent_path, str(node.id))
            normalized_children = bool(node.children) and all(
                self._is_equivalence_group(child) and child.is_leaf for child in node.children
            )
            terminal_skill_children = (
                bool(node.children)
                and all(child.is_leaf and bool(child.skills) for child in node.children)
                and all(str(child.id) != "uncategorized" for child in node.children)
            )
            # ``root`` is a serialization container, not a taxonomy boundary.
            # Treating it as a scope would merge unrelated top-level categories
            # and replace the taxonomy itself on small trees.
            if not synthetic_root and (normalized_children or terminal_skill_children):
                skills = list(node.skills)
                for child in node.children:
                    skills.extend(child.skills)
                if skills:
                    scopes.append(_Scope(node, path, tuple(sorted(skills, key=lambda item: item.id))))
                return
            if synthetic_root and node.skills:
                raise EquivalenceProtocolError(
                    "synthetic root contains direct Skills; a taxonomy scope is required before equivalence"
                )
            if node.skills:
                if node.children:
                    raise EquivalenceProtocolError(
                        f"terminal branch '{'/'.join(path)}' contains both Skills and taxonomy children"
                    )
                scopes.append(_Scope(node, path, tuple(sorted(node.skills, key=lambda item: item.id))))
                return
            for child in sorted(node.children, key=lambda item: (str(item.id), str(item.name))):
                walk(child, path)

        walk(root, (), synthetic_root=True)
        return sorted(scopes, key=lambda item: item.path)

    @staticmethod
    def _is_equivalence_group(node: TreeNode) -> bool:
        return bool(_EQUIVALENCE_GROUP_ID_RE.fullmatch(str(node.id)))

    def _skills_from_scope_node(self, scope: TreeNode) -> list[Skill]:
        if scope.children and not all(self._is_equivalence_group(child) and child.is_leaf for child in scope.children):
            raise EquivalenceProtocolError(f"scope '{scope.id}' has non-equivalence taxonomy children")
        skills = list(scope.skills)
        for child in scope.children:
            skills.extend(child.skills)
        return skills

    @staticmethod
    def _validate_unique_skills(scopes: Iterable[_Scope]) -> None:
        locations: dict[str, str] = {}
        for scope in scopes:
            for skill in scope.skills:
                skill_id = str(skill.id).strip()
                if not skill_id:
                    raise EquivalenceProtocolError(f"scope '{scope.path_text}' contains a Skill without an id")
                if skill_id in locations:
                    raise EquivalenceProtocolError(
                        f"Skill id '{skill_id}' appears more than once: {locations[skill_id]} and {scope.path_text}"
                    )
                locations[skill_id] = scope.path_text

    def _plan_scope(self, scope: _Scope, cached_state: dict | None) -> _ScopePlan:
        self._current_stage = f"scope:{scope.path_text}:profiles"
        alias_by_skill_id = {skill.id: f"s{index:06d}" for index, skill in enumerate(scope.skills, start=1)}
        skill_by_alias = {alias_by_skill_id[skill.id]: skill for skill in scope.skills}
        profiles = {alias: self._skill_profile(alias, skill) for alias, skill in skill_by_alias.items()}
        skill_hashes = {skill.id: self._skill_hash(skill) for skill in scope.skills}
        self._audit_events.append(
            {
                "event": "skill_aliases",
                "protocol_hash": self.protocol_hash,
                "scope_path": scope.path_text,
                "aliases": [
                    {
                        "ref": alias,
                        "skill_id": skill_by_alias[alias].id,
                        "content_hash": skill_hashes[skill_by_alias[alias].id],
                    }
                    for alias in sorted(skill_by_alias)
                ],
            }
        )

        cached_decisions, cached_groups = self._load_cached_state(
            cached_state,
            alias_by_skill_id=alias_by_skill_id,
            skill_hashes=skill_hashes,
        )
        pure_delete_partition = self._pure_delete_partition(
            cached_state,
            alias_by_skill_id=alias_by_skill_id,
            skill_hashes=skill_hashes,
        )
        self._current_stage = f"scope:{scope.path_text}:candidates"
        candidate_sources = self._candidate_pairs(
            scope,
            profiles,
            cached_state=cached_state,
            alias_by_skill_id=alias_by_skill_id,
            skill_hashes=skill_hashes,
        )
        self._pair_budget_used += len(candidate_sources)
        if self._pair_budget_used > self._pair_budget_limit:
            raise EquivalenceProtocolError(
                "equivalence candidate pair budget exceeded before pairwise verification: "
                f"{self._pair_budget_used} > {self._pair_budget_limit}"
            )
        self._metrics["candidate_pairs"] += len(candidate_sources)
        candidate_event = {
            "event": "candidate_pairs",
            "scope_path": scope.path_text,
            "pairs": [
                {
                    "left": left,
                    "right": right,
                    "left_skill_id": skill_by_alias[left].id,
                    "right_skill_id": skill_by_alias[right].id,
                    "sources": sorted(sources),
                }
                for (left, right), sources in sorted(candidate_sources.items())
            ],
        }
        self._audit_events.append(candidate_event)

        self._current_stage = f"scope:{scope.path_text}:pairwise"
        decisions = self._decide_pairs(
            scope,
            profiles,
            sorted(candidate_sources),
            cached_decisions=cached_decisions,
        )
        positive_edges = {
            pair
            for pair, decision in decisions.items()
            if decision["verdict"] == "equivalent" and decision.get("_effective_equivalent", True)
        }
        if pure_delete_partition is None:
            groups = self._complete_link_clusters(tuple(sorted(skill_by_alias)), positive_edges)
            partition_source = "complete_link"
        else:
            groups, retained_capabilities = pure_delete_partition
            partition_source = "pure_delete_cache"
            cached_groups.update(retained_capabilities)
            allowed_internal_pairs = {
                _canonical_pair(left, right)
                for group in groups
                for left, right in combinations(group, 2)
            }
            boundary_rejected = positive_edges - allowed_internal_pairs
            for pair in boundary_rejected:
                decisions[pair]["_effective_rejection_reason"] = "cached_group_boundary"
            positive_edges.intersection_update(allowed_internal_pairs)
            self._audit_events.append(
                {
                    "event": "pure_delete_partition_reuse",
                    "scope_path": scope.path_text,
                    "groups": [list(group) for group in groups],
                    "boundary_rejected_pairs": [list(pair) for pair in sorted(boundary_rejected)],
                }
            )

        self._audit_events.append(
            {
                "event": "clique_partition",
                "scope_path": scope.path_text,
                "source": partition_source,
                "groups": [list(group) for group in groups],
            }
        )

        self._current_stage = f"scope:{scope.path_text}:group_audit"
        groups, group_capabilities = self._audit_until_stable(
            scope,
            profiles,
            decisions,
            positive_edges,
            groups,
            cached_groups=cached_groups,
        )
        self._validate_clusters(tuple(sorted(skill_by_alias)), groups, positive_edges)

        group_plans: list[_GroupPlan] = []
        final_groups: list[dict] = []
        for member_aliases in groups:
            members = tuple(sorted((skill_by_alias[alias] for alias in member_aliases), key=lambda item: item.id))
            representative = members[0]
            group_id = self._stable_group_id(scope.path, tuple(skill.id for skill in members))
            is_multi = len(members) > 1
            if is_multi:
                self._metrics["multi_member_groups"] += 1
            else:
                self._metrics["singleton_groups"] += 1
            member_set = frozenset(member_aliases)
            audit_passed = not is_multi or member_set in group_capabilities
            capability = group_capabilities.get(member_set, {}) if is_multi else {}
            plan = _GroupPlan(
                group_id=group_id,
                name=str(capability.get("name") or representative.name or representative.id),
                description=str(
                    capability.get("description")
                    or representative.description
                    or representative.source_description
                    or representative.name
                ),
                select_when=str(capability.get("select_when") or representative.select_when),
                dont_select_when=str(capability.get("dont_select_when") or representative.dont_select_when),
                members=members,
                audit_passed=audit_passed,
            )
            group_plans.append(plan)
            final_groups.append(
                {
                    "group_id": group_id,
                    "name": plan.name,
                    "description": plan.description,
                    "select_when": plan.select_when,
                    "dont_select_when": plan.dont_select_when,
                    "member_skill_ids": [skill.id for skill in members],
                    "audit_passed": audit_passed,
                }
            )

        pairwise_state = [
            self._decision_state(
                pair,
                decision,
                skill_by_alias=skill_by_alias,
                skill_hashes=skill_hashes,
                positive_edges=positive_edges,
            )
            for pair, decision in sorted(decisions.items())
        ]
        state = {
            "protocol_hash": self.protocol_hash,
            "model": str(getattr(self._builder, "model", "") or ""),
            "scope_path": scope.path_text,
            "scope_path_parts": list(scope.path),
            # A workflow that knows the final flattened CID may augment this field.
            "scope_cid": None,
            "skill_hashes": dict(sorted(skill_hashes.items())),
            "skills": [
                {"skill_id": skill.id, "content_hash": skill_hashes[skill.id]} for skill in scope.skills
            ],
            "candidate_pairs": candidate_event["pairs"],
            "pairwise_pair_count": len(candidate_event["pairs"]),
            "pairwise_decisions": pairwise_state,
            "audit_rejected_pairs": [
                {
                    "left_skill_id": skill_by_alias[left].id,
                    "right_skill_id": skill_by_alias[right].id,
                    "reason": str(
                        decisions[(left, right)].get("_effective_rejection_reason") or "group_audit"
                    ),
                }
                for left, right in sorted(
                    pair
                    for pair, decision in decisions.items()
                    if decision["verdict"] == "equivalent" and pair not in positive_edges
                )
            ],
            "groups": sorted(final_groups, key=lambda item: item["group_id"]),
        }
        self._scope_states.append(state)
        self._audit_events.append(
            {
                "event": "final_groups",
                "scope_path": scope.path_text,
                "groups": state["groups"],
            }
        )
        return _ScopePlan(
            scope=scope,
            groups=sorted(group_plans, key=lambda item: item.group_id),
            positive_edges=positive_edges,
            state=state,
        )

    def _pure_delete_partition(
        self,
        cached_state: dict | None,
        *,
        alias_by_skill_id: dict[str, str],
        skill_hashes: dict[str, str],
    ) -> tuple[list[tuple[str, ...]], dict[frozenset[str], dict[str, str]]] | None:
        if not isinstance(cached_state, dict):
            return None
        old_hashes = cached_state.get("skill_hashes")
        compatible = (
            cached_state.get("protocol_hash") == self.protocol_hash
            and str(cached_state.get("model") or "") == str(getattr(self._builder, "model", "") or "")
            and isinstance(old_hashes, dict)
        )
        if not compatible:
            return None
        current_ids = set(skill_hashes)
        old_ids = set(old_hashes)
        unchanged = all(old_hashes.get(skill_id) == digest for skill_id, digest in skill_hashes.items())
        if not (current_ids < old_ids and unchanged):
            return None

        raw_groups = cached_state.get("groups")
        if not isinstance(raw_groups, list) or not raw_groups:
            raise EquivalenceProtocolError("pure-delete cache has no final group partition")
        groups: list[tuple[str, ...]] = []
        capabilities: dict[frozenset[str], dict[str, str]] = {}
        covered: set[str] = set()
        for raw_group in raw_groups:
            if not isinstance(raw_group, dict):
                raise EquivalenceProtocolError("pure-delete cache contains a non-object final group")
            raw_member_ids = raw_group.get("member_skill_ids")
            if not isinstance(raw_member_ids, list):
                raise EquivalenceProtocolError("pure-delete cache group has no member_skill_ids array")
            retained_ids = sorted(
                {str(member_id) for member_id in raw_member_ids if str(member_id) in current_ids}
            )
            if not retained_ids:
                continue
            duplicate_ids = covered.intersection(retained_ids)
            if duplicate_ids:
                raise EquivalenceProtocolError(
                    f"pure-delete cache duplicates Skills across final groups: {sorted(duplicate_ids)}"
                )
            covered.update(retained_ids)
            aliases = tuple(sorted(alias_by_skill_id[skill_id] for skill_id in retained_ids))
            groups.append(aliases)
            if len(aliases) > 1:
                capability = self._cached_group_capability(raw_group)
                if not bool(raw_group.get("audit_passed")) or capability is None:
                    raise EquivalenceProtocolError(
                        "pure-delete cache has an unaudited multi-member final group"
                    )
                capabilities[frozenset(aliases)] = capability
        if covered != current_ids:
            raise EquivalenceProtocolError(
                f"pure-delete cached partition is incomplete; missing={sorted(current_ids - covered)}"
            )
        return sorted(groups), capabilities

    def _candidate_pairs(
        self,
        scope: _Scope,
        profiles: dict[str, dict],
        *,
        cached_state: dict | None,
        alias_by_skill_id: dict[str, str],
        skill_hashes: dict[str, str],
    ) -> dict[tuple[str, str], set[str]]:
        aliases = tuple(sorted(profiles))
        sources: dict[tuple[str, str], set[str]] = {}
        compatible_cache = (
            isinstance(cached_state, dict)
            and cached_state.get("protocol_hash") == self.protocol_hash
            and str(cached_state.get("model") or "") == str(getattr(self._builder, "model", "") or "")
            and isinstance(cached_state.get("skill_hashes"), dict)
        )
        old_hashes = cached_state["skill_hashes"] if compatible_cache else {}
        unchanged_ids = {
            skill_id
            for skill_id, digest in skill_hashes.items()
            if old_hashes.get(skill_id) == digest
        }
        old_ids = set(old_hashes)
        current_ids = set(skill_hashes)
        pure_delete = bool(compatible_cache and current_ids < old_ids and unchanged_ids == current_ids)

        def reuse_cached_candidates() -> None:
            for row in cached_state.get("candidate_pairs", []) or []:
                if not isinstance(row, dict):
                    continue
                left_id = str(row.get("left_skill_id") or "")
                right_id = str(row.get("right_skill_id") or "")
                if left_id not in unchanged_ids or right_id not in unchanged_ids:
                    continue
                pair = _canonical_pair(alias_by_skill_id[left_id], alias_by_skill_id[right_id])
                raw_sources = row.get("sources")
                cached_sources = (
                    {str(value) for value in raw_sources if str(value)}
                    if isinstance(raw_sources, list)
                    else {"llm"}
                )
                sources.setdefault(pair, set()).update(cached_sources)

        if pure_delete:
            # Deletion cannot create a new semantic overlap.  Preserve all
            # surviving candidate evidence even if the scope crosses the
            # configured all-pairs threshold after removal.
            reuse_cached_candidates()
        elif len(aliases) <= self.all_pairs_scope_limit:
            for pair in combinations(aliases, 2):
                sources.setdefault(pair, set()).add("all_pairs")
        else:
            anchors_to_query = list(aliases)
            if compatible_cache:
                anchors_to_query = sorted(
                    alias_by_skill_id[skill_id] for skill_id in skill_hashes if skill_id not in unchanged_ids
                )
                reuse_cached_candidates()
            compact_profiles = [
                {
                    "ref": alias,
                    "name": profiles[alias]["name"],
                    "description": profiles[alias]["description"],
                    "select_when": profiles[alias]["select_when"],
                    "dont_select_when": profiles[alias]["dont_select_when"],
                }
                for alias in aliases
            ]
            for index in range(0, len(anchors_to_query), _CANDIDATE_ANCHOR_BATCH_SIZE):
                anchors = tuple(anchors_to_query[index:index + _CANDIDATE_ANCHOR_BATCH_SIZE])
                prompt = EQUIVALENCE_CANDIDATE_PROMPT.format(
                    scope_json=self._json(self._scope_payload(scope)),
                    skills_json=self._json(compact_profiles),
                    anchor_refs_json=self._json(list(anchors)),
                    max_neighbors=self.candidate_neighbors,
                )
                rows = self._exchange(
                    stage="candidate_recall",
                    scope=scope,
                    prompt=prompt,
                    validator=lambda payload, expected=anchors: self._validate_candidate_response(
                        payload,
                        expected_anchors=expected,
                        all_aliases=set(aliases),
                    ),
                )
                for row in rows:
                    anchor = row["anchor"]
                    for neighbor in row["neighbors"]:
                        sources.setdefault(_canonical_pair(anchor, neighbor), set()).add("llm")

        by_name: dict[str, list[str]] = {}
        for alias, profile in profiles.items():
            normalized = self._normalized_name(profile["name"])
            if normalized:
                by_name.setdefault(normalized, []).append(alias)
        for same_name_aliases in by_name.values():
            for pair in combinations(sorted(same_name_aliases), 2):
                sources.setdefault(pair, set()).add("normalized_name")
        return sources

    def _validate_candidate_response(
        self,
        payload: object,
        *,
        expected_anchors: tuple[str, ...],
        all_aliases: set[str],
    ) -> list[dict]:
        if not isinstance(payload, dict):
            raise EquivalenceProtocolError("candidate response must be an object")
        _exact_keys(payload, {"candidates"}, "candidate response")
        rows = payload["candidates"]
        if not isinstance(rows, list):
            raise EquivalenceProtocolError("candidates must be an array")
        expected = set(expected_anchors)
        seen: set[str] = set()
        normalized: list[dict] = []
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise EquivalenceProtocolError(f"candidate row {index} must be an object")
            _exact_keys(row, {"anchor", "neighbors"}, f"candidate row {index}")
            anchor = _require_json_string(row["anchor"], f"candidate row {index} anchor")
            if anchor not in expected:
                raise EquivalenceProtocolError(f"unknown candidate anchor '{anchor}'")
            if anchor in seen:
                raise EquivalenceProtocolError(f"duplicate candidate anchor '{anchor}'")
            seen.add(anchor)
            neighbors = row["neighbors"]
            if not isinstance(neighbors, list):
                raise EquivalenceProtocolError(f"neighbors for '{anchor}' must be an array")
            if len(neighbors) > self.candidate_neighbors:
                raise EquivalenceProtocolError(
                    f"anchor '{anchor}' returned {len(neighbors)} neighbors; maximum is {self.candidate_neighbors}"
                )
            normalized_neighbors = [
                _require_json_string(value, f"neighbor {neighbor_index} for '{anchor}'")
                for neighbor_index, value in enumerate(neighbors)
            ]
            if len(set(normalized_neighbors)) != len(normalized_neighbors):
                raise EquivalenceProtocolError(f"anchor '{anchor}' returned duplicate neighbors")
            for neighbor in normalized_neighbors:
                if neighbor not in all_aliases:
                    raise EquivalenceProtocolError(f"anchor '{anchor}' returned unknown neighbor '{neighbor}'")
                if neighbor == anchor:
                    raise EquivalenceProtocolError(f"anchor '{anchor}' returned itself as a neighbor")
            normalized.append({"anchor": anchor, "neighbors": sorted(normalized_neighbors)})
        if seen != expected:
            raise EquivalenceProtocolError(f"candidate anchors incomplete; missing={sorted(expected - seen)}")
        return sorted(normalized, key=lambda item: item["anchor"])

    def _decide_pairs(
        self,
        scope: _Scope,
        profiles: dict[str, dict],
        candidate_pairs: list[tuple[str, str]],
        *,
        cached_decisions: dict[tuple[str, str], dict],
    ) -> dict[tuple[str, str], dict]:
        decisions = {
            pair: dict(decision, _source="cache")
            for pair, decision in cached_decisions.items()
            if pair in candidate_pairs
        }
        self._metrics["pairwise_cache_hits"] += len(decisions)
        missing_pairs = [pair for pair in candidate_pairs if pair not in decisions]
        for index in range(0, len(missing_pairs), _PAIR_BATCH_SIZE):
            batch = tuple(missing_pairs[index:index + _PAIR_BATCH_SIZE])
            used_aliases = sorted({alias for pair in batch for alias in pair})
            prompt = EQUIVALENCE_PAIRWISE_PROMPT.format(
                scope_json=self._json(self._scope_payload(scope)),
                skills_json=self._json([profiles[alias] for alias in used_aliases]),
                pairs_json=self._json([{"left": left, "right": right} for left, right in batch]),
            )
            batch_decisions = self._exchange(
                stage="pairwise",
                scope=scope,
                prompt=prompt,
                validator=lambda payload, expected=batch: self._validate_pairwise_response(payload, expected),
            )
            for pair, decision in batch_decisions.items():
                decisions[pair] = dict(decision, _source="llm")

        if set(decisions) != set(candidate_pairs):
            raise EquivalenceProtocolError("internal pairwise coverage invariant failed")
        for decision in decisions.values():
            key = f"pairwise_{decision['verdict']}"
            self._metrics[key] += 1
        self._audit_events.append(
            {
                "event": "pairwise_decisions",
                "scope_path": scope.path_text,
                "decisions": [self._public_decision(decision) for _, decision in sorted(decisions.items())],
            }
        )
        return decisions

    def _validate_pairwise_response(
        self,
        payload: object,
        expected_pairs: tuple[tuple[str, str], ...],
    ) -> dict[tuple[str, str], dict]:
        if not isinstance(payload, dict):
            raise EquivalenceProtocolError("pairwise response must be an object")
        _exact_keys(payload, {"decisions"}, "pairwise response")
        rows = payload["decisions"]
        if not isinstance(rows, list):
            raise EquivalenceProtocolError("decisions must be an array")
        expected = set(expected_pairs)
        decisions: dict[tuple[str, str], dict] = {}
        required = {
            "left",
            "right",
            "verdict",
            "left_replaces_right",
            "right_replaces_left",
            "dimensions",
            "common_request",
            "distinguishing_request",
            "reason_code",
            "reason",
        }
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise EquivalenceProtocolError(f"decision row {index} must be an object")
            _exact_keys(row, required, f"decision row {index}")
            left = _require_json_string(row["left"], f"decision row {index} left")
            right = _require_json_string(row["right"], f"decision row {index} right")
            pair = (left, right)
            if pair not in expected:
                raise EquivalenceProtocolError(f"unknown or non-canonical pair {pair}")
            if pair in decisions:
                raise EquivalenceProtocolError(f"duplicate pair {pair}")
            verdict = _require_json_string(row["verdict"], f"pair {pair} verdict")
            if verdict not in _VERDICTS:
                raise EquivalenceProtocolError(f"pair {pair} has invalid verdict '{verdict}'")
            left_replaces = row["left_replaces_right"]
            right_replaces = row["right_replaces_left"]
            if not (isinstance(left_replaces, bool) or left_replaces is None) or not (
                isinstance(right_replaces, bool) or right_replaces is None
            ):
                raise EquivalenceProtocolError(f"pair {pair} replacement flags must be boolean or null")
            dimensions = row["dimensions"]
            if not isinstance(dimensions, dict):
                raise EquivalenceProtocolError(f"pair {pair} dimensions must be an object")
            _exact_keys(dimensions, set(_HARD_DIMENSIONS), f"pair {pair} dimensions")
            for name, value in dimensions.items():
                if not isinstance(value, str) or value not in _DIMENSION_VALUES:
                    raise EquivalenceProtocolError(f"pair {pair} dimension '{name}' has invalid value '{value}'")
            common_request = _require_json_string(
                row["common_request"], f"pair {pair} common_request", strip=True
            )
            distinguishing_request = _require_json_string(
                row["distinguishing_request"], f"pair {pair} distinguishing_request", strip=True
            )
            reason_code = _require_json_string(row["reason_code"], f"pair {pair} reason_code")
            reason = _require_json_string(row["reason"], f"pair {pair} reason", strip=True)
            if reason_code not in _REASON_CODES or not reason:
                raise EquivalenceProtocolError(f"pair {pair} has invalid reason metadata")
            if verdict == "equivalent":
                if left_replaces is not True or right_replaces is not True:
                    raise EquivalenceProtocolError(f"equivalent pair {pair} is not bidirectionally replaceable")
                if any(value != "same" for value in dimensions.values()):
                    raise EquivalenceProtocolError(f"equivalent pair {pair} has a hard dimension conflict or unknown")
                if not common_request or distinguishing_request:
                    raise EquivalenceProtocolError(f"equivalent pair {pair} lacks valid request evidence")
                if reason_code != "mutual_substitute":
                    raise EquivalenceProtocolError(f"equivalent pair {pair} has invalid reason code")
            elif verdict == "not_equivalent":
                if left_replaces is True and right_replaces is True:
                    raise EquivalenceProtocolError(
                        f"not-equivalent pair {pair} cannot be bidirectionally replaceable"
                    )
                if not distinguishing_request:
                    raise EquivalenceProtocolError(f"not-equivalent pair {pair} needs a distinguishing request")
                if reason_code in {"mutual_substitute", "insufficient_description"}:
                    raise EquivalenceProtocolError(f"not-equivalent pair {pair} has invalid reason code")
            else:
                if left_replaces is not None or right_replaces is not None:
                    raise EquivalenceProtocolError(
                        f"insufficient-evidence pair {pair} replacement flags must be null"
                    )
                if "unknown" not in dimensions.values():
                    raise EquivalenceProtocolError(
                        f"insufficient-evidence pair {pair} needs an unknown hard dimension"
                    )
                if common_request or distinguishing_request:
                    raise EquivalenceProtocolError(
                        f"insufficient-evidence pair {pair} cannot assert request evidence"
                    )
                if reason_code != "insufficient_description":
                    raise EquivalenceProtocolError(f"insufficient-evidence pair {pair} has invalid reason code")
            normalized = dict(row)
            normalized.update(
                left=left,
                right=right,
                verdict=verdict,
                common_request=common_request,
                distinguishing_request=distinguishing_request,
                reason_code=reason_code,
                reason=reason,
                dimensions={name: str(dimensions[name]) for name in _HARD_DIMENSIONS},
            )
            decisions[pair] = normalized
        if set(decisions) != expected:
            missing = sorted(expected - set(decisions))
            raise EquivalenceProtocolError(f"pairwise response incomplete; missing={missing}")
        return decisions

    @staticmethod
    def _complete_link_clusters(
        aliases: tuple[str, ...],
        positive_edges: set[tuple[str, str]],
    ) -> list[tuple[str, ...]]:
        clusters: list[tuple[str, ...]] = [(alias,) for alias in sorted(aliases)]
        while True:
            merged = False
            for left_index in range(len(clusters)):
                for right_index in range(left_index + 1, len(clusters)):
                    left_cluster = clusters[left_index]
                    right_cluster = clusters[right_index]
                    if all(
                        _canonical_pair(left, right) in positive_edges
                        for left in left_cluster
                        for right in right_cluster
                    ):
                        combined = tuple(sorted((*left_cluster, *right_cluster)))
                        clusters = [
                            cluster
                            for index, cluster in enumerate(clusters)
                            if index not in {left_index, right_index}
                        ]
                        clusters.append(combined)
                        clusters.sort()
                        merged = True
                        break
                if merged:
                    break
            if not merged:
                return sorted(clusters)

    def _audit_until_stable(
        self,
        scope: _Scope,
        profiles: dict[str, dict],
        decisions: dict[tuple[str, str], dict],
        positive_edges: set[tuple[str, str]],
        groups: list[tuple[str, ...]],
        *,
        cached_groups: dict[frozenset[str], dict[str, str]],
    ) -> tuple[list[tuple[str, ...]], dict[frozenset[str], dict[str, str]]]:
        approved: dict[frozenset[str], dict[str, str]] = {}
        reusable = sorted(cached_groups.items(), key=lambda item: sorted(item[0]))
        for audit_round in range(1, 3):
            conflicts: set[tuple[str, str]] = set()
            multi_groups = [group for group in groups if len(group) > 1]
            if not multi_groups:
                return groups, approved
            for group in multi_groups:
                member_set = frozenset(group)
                cached_capability = next(
                    (capability for prior, capability in reusable if member_set <= prior),
                    None,
                )
                if member_set in approved or cached_capability is not None:
                    if member_set not in approved:
                        approved[member_set] = dict(cached_capability or {})
                    self._audit_events.append(
                        {
                            "event": "group_audit",
                            "scope_path": scope.path_text,
                            "round": audit_round,
                            "members": list(group),
                            "result": "pass",
                            "source": "cache",
                            "conflicts": [],
                            "capability": approved[member_set],
                        }
                    )
                    continue
                group_pairs = tuple(combinations(group, 2))
                prompt = EQUIVALENCE_GROUP_AUDIT_PROMPT.format(
                    scope_json=self._json(self._scope_payload(scope)),
                    skills_json=self._json([profiles[alias] for alias in group]),
                    decisions_json=self._json(
                        [self._public_decision(decisions[pair]) for pair in group_pairs]
                    ),
                )
                audit = self._exchange(
                    stage="group_audit",
                    scope=scope,
                    prompt=prompt,
                    validator=lambda payload, members=group: self._validate_group_audit(payload, members),
                )
                self._metrics["audit_calls"] += 1
                self._audit_events.append(
                    {
                        "event": "group_audit",
                        "scope_path": scope.path_text,
                        "round": audit_round,
                        "members": list(group),
                        "result": audit["result"],
                        "source": "llm",
                        "conflicts": audit["conflicts"],
                        "capability": audit["capability"],
                    }
                )
                if audit["result"] == "pass":
                    approved[member_set] = audit["capability"]
                else:
                    conflicts.update((item["left"], item["right"]) for item in audit["conflicts"])

            if not conflicts:
                return groups, approved
            self._metrics["audit_conflicts"] += len(conflicts)
            old_groups = groups
            positive_edges.difference_update(conflicts)
            groups = self._complete_link_clusters(tuple(sorted(profiles)), positive_edges)
            self._metrics["audit_reclusters"] += 1
            if groups == old_groups:
                raise EquivalenceProtocolError("group audit conflicts did not change complete-link clustering")
            if not any(len(group) > 1 for group in groups):
                return groups, approved
            if audit_round == 2:
                raise EquivalenceProtocolError("single-function group audit did not converge after two rounds")
        raise EquivalenceProtocolError("single-function group audit did not converge")

    def _validate_group_audit(self, payload: object, members: tuple[str, ...]) -> dict:
        if not isinstance(payload, dict):
            raise EquivalenceProtocolError("group audit response must be an object")
        _exact_keys(payload, {"result", "capability", "conflicts"}, "group audit response")
        result = _require_json_string(payload["result"], "group audit result")
        if result not in {"pass", "conflict"}:
            raise EquivalenceProtocolError(f"group audit result '{result}' is invalid")
        raw_conflicts = payload["conflicts"]
        if not isinstance(raw_conflicts, list):
            raise EquivalenceProtocolError("group audit conflicts must be an array")
        raw_capability = payload["capability"]
        if not isinstance(raw_capability, dict):
            raise EquivalenceProtocolError("group audit capability must be an object")
        _exact_keys(raw_capability, set(_CAPABILITY_FIELD_LIMITS), "group audit capability")
        capability = {
            key: _require_json_string(
                raw_capability[key],
                f"group audit capability '{key}'",
                strip=True,
            )
            for key in _CAPABILITY_FIELD_LIMITS
        }
        if result == "pass":
            for key, limit in _CAPABILITY_FIELD_LIMITS.items():
                value = capability[key]
                if not value:
                    raise EquivalenceProtocolError(f"passing group audit capability '{key}' must be non-empty")
                if len(value) > limit:
                    raise EquivalenceProtocolError(
                        f"group audit capability '{key}' exceeds {limit} characters"
                    )
        elif any(capability.values()):
            raise EquivalenceProtocolError("conflicting group audit must leave capability fields empty")
        member_set = set(members)
        seen: set[tuple[str, str]] = set()
        conflicts: list[dict] = []
        for index, row in enumerate(raw_conflicts):
            if not isinstance(row, dict):
                raise EquivalenceProtocolError(f"group audit conflict {index} must be an object")
            _exact_keys(row, {"left", "right", "reason"}, f"group audit conflict {index}")
            left = _require_json_string(row["left"], f"group audit conflict {index} left")
            right = _require_json_string(row["right"], f"group audit conflict {index} right")
            pair = _canonical_pair(left, right)
            if pair[0] not in member_set or pair[1] not in member_set or pair[0] == pair[1]:
                raise EquivalenceProtocolError(f"group audit returned out-of-group pair {pair}")
            if pair in seen:
                raise EquivalenceProtocolError(f"group audit returned duplicate pair {pair}")
            reason = _require_json_string(
                row["reason"],
                f"group audit conflict {pair} reason",
                strip=True,
            )
            if not reason:
                raise EquivalenceProtocolError(f"group audit conflict {pair} has no reason")
            seen.add(pair)
            conflicts.append({"left": pair[0], "right": pair[1], "reason": reason})
        if result == "pass" and conflicts:
            raise EquivalenceProtocolError("passing group audit must not return conflicts")
        if result == "conflict" and not conflicts:
            raise EquivalenceProtocolError("conflicting group audit must return at least one pair")
        return {"result": result, "capability": capability, "conflicts": conflicts}

    def _exchange(
        self,
        *,
        stage: str,
        scope: _Scope,
        prompt: str,
        validator: Callable[[object], object],
    ):
        previous_raw = ""
        previous_error = ""
        for attempt in (1, 2):
            actual_prompt = prompt
            if attempt == 2:
                self._metrics["correction_attempts"] += 1
                actual_prompt = EQUIVALENCE_CORRECTION_PROMPT.format(
                    validation_error=previous_error,
                    original_prompt=prompt,
                    invalid_response=previous_raw[:_MAX_CORRECTION_ECHO_CHARS],
                )
            raw = ""
            try:
                raw = self._builder._call_llm(actual_prompt, is_retry=attempt == 2)
                if bool(getattr(getattr(self._builder, "_thread_local", object()), "truncated", False)):
                    raise EquivalenceProtocolError("model output was truncated")
                sentinel = object()
                parsed = parse_json_from_response(raw, default=sentinel)
                if parsed is sentinel:
                    raise EquivalenceProtocolError("response does not contain a valid JSON value")
                validated = validator(parsed)
            except EquivalenceProtocolError as exc:
                self._metrics["protocol_validation_errors"] += 1
                previous_raw = raw
                previous_error = str(exc)
                self._audit_events.append(
                    {
                        "event": "llm_exchange",
                        "stage": stage,
                        "scope_path": scope.path_text,
                        "attempt": attempt,
                        "prompt": actual_prompt,
                        "raw_response": raw,
                        "validation_error": previous_error,
                    }
                )
                if attempt == 2:
                    raise EquivalenceProtocolError(
                        f"{stage} protocol failed after one correction: {previous_error}"
                    ) from exc
                continue
            except RuntimeError as exc:
                # The runtime already exhausts configured transport retries.  Only
                # an empty model response is a correctable domain-protocol failure.
                if "empty response" not in str(exc).lower():
                    self._audit_events.append(
                        {
                            "event": "llm_exchange",
                            "stage": stage,
                            "scope_path": scope.path_text,
                            "attempt": attempt,
                            "prompt": actual_prompt,
                            "raw_response": raw,
                            "call_error": str(exc),
                        }
                    )
                    raise
                self._metrics["protocol_validation_errors"] += 1
                previous_raw = raw
                previous_error = str(exc)
                self._audit_events.append(
                    {
                        "event": "llm_exchange",
                        "stage": stage,
                        "scope_path": scope.path_text,
                        "attempt": attempt,
                        "prompt": actual_prompt,
                        "raw_response": raw,
                        "validation_error": previous_error,
                    }
                )
                if attempt == 2:
                    raise EquivalenceProtocolError(
                        f"{stage} protocol failed after one correction: {previous_error}"
                    ) from exc
                continue
            self._audit_events.append(
                {
                    "event": "llm_exchange",
                    "stage": stage,
                    "scope_path": scope.path_text,
                    "attempt": attempt,
                    "prompt": actual_prompt,
                    "raw_response": raw,
                    "validation_error": None,
                }
            )
            return validated
        raise EquivalenceProtocolError(f"{stage} protocol failed")

    @staticmethod
    def _cached_group_capability(group: dict) -> dict[str, str] | None:
        if any(not isinstance(group.get(key), str) for key in _CAPABILITY_FIELD_LIMITS):
            return None
        capability = {
            key: group[key].strip()
            for key in _CAPABILITY_FIELD_LIMITS
        }
        invalid = any(
            not capability[key] or len(capability[key]) > limit
            for key, limit in _CAPABILITY_FIELD_LIMITS.items()
        )
        return None if invalid else capability

    def _load_cached_state(
        self,
        cached_state: dict | None,
        *,
        alias_by_skill_id: dict[str, str],
        skill_hashes: dict[str, str],
    ) -> tuple[dict[tuple[str, str], dict], dict[frozenset[str], dict[str, str]]]:
        if not isinstance(cached_state, dict):
            return {}, {}
        if cached_state.get("protocol_hash") != self.protocol_hash:
            return {}, {}
        if str(cached_state.get("model") or "") != str(getattr(self._builder, "model", "") or ""):
            return {}, {}
        old_hashes = cached_state.get("skill_hashes")
        if not isinstance(old_hashes, dict):
            return {}, {}

        cached_decisions: dict[tuple[str, str], dict] = {}
        for row in cached_state.get("pairwise_decisions", []) or []:
            if not isinstance(row, dict):
                continue
            left_id = str(row.get("left_skill_id") or "")
            right_id = str(row.get("right_skill_id") or "")
            if left_id not in alias_by_skill_id or right_id not in alias_by_skill_id:
                continue
            if old_hashes.get(left_id) != skill_hashes[left_id] or old_hashes.get(right_id) != skill_hashes[right_id]:
                continue
            left_alias = alias_by_skill_id[left_id]
            right_alias = alias_by_skill_id[right_id]
            pair = _canonical_pair(left_alias, right_alias)
            candidate = {
                key: row.get(key)
                for key in (
                    "verdict",
                    "left_replaces_right",
                    "right_replaces_left",
                    "dimensions",
                    "common_request",
                    "distinguishing_request",
                    "reason_code",
                    "reason",
                )
            }
            candidate.update(left=pair[0], right=pair[1])
            try:
                validated = self._validate_pairwise_response({"decisions": [candidate]}, (pair,))
            except EquivalenceProtocolError:
                continue
            effective_verdict = str(row.get("effective_verdict") or "")
            if effective_verdict not in _VERDICTS:
                # Old or incomplete state cannot safely restore an audited edge.
                continue
            cached_decision = validated[pair]
            cached_decision["_effective_equivalent"] = effective_verdict == "equivalent"
            cached_decisions[pair] = cached_decision

        cached_groups: dict[frozenset[str], dict[str, str]] = {}
        for group in cached_state.get("groups", []) or []:
            if not isinstance(group, dict) or not bool(group.get("audit_passed")):
                continue
            member_ids = [
                str(value)
                for value in group.get("member_skill_ids", []) or []
                if str(value) in alias_by_skill_id
            ]
            if len(member_ids) < 2:
                continue
            if any(old_hashes.get(member_id) != skill_hashes[member_id] for member_id in member_ids):
                continue
            capability = self._cached_group_capability(group)
            if capability is None:
                continue
            cached_groups[frozenset(alias_by_skill_id[member_id] for member_id in member_ids)] = capability
        return cached_decisions, cached_groups

    def _apply_plan(self, plan: _ScopePlan) -> None:
        scope = plan.scope.node
        children: list[TreeNode] = []
        for group in plan.groups:
            node = TreeNode(
                id=group.group_id,
                name=group.name,
                description=group.description,
                select_when=group.select_when,
                dont_select_when=group.dont_select_when,
                depth=scope.depth + 1,
                parent_id=scope.id,
                skills=list(group.members),
            )
            for skill in node.skills:
                skill.path = node.id
            children.append(node)
        scope.skills = []
        scope.children = sorted(children, key=lambda item: item.id)

    def _validate_final_tree(
        self,
        root: TreeNode,
        scopes: list[_Scope],
        plans: list[_ScopePlan],
    ) -> None:
        expected = sorted(skill.id for scope in scopes for skill in scope.skills)
        actual: list[str] = []
        stack = [root]
        while stack:
            node = stack.pop()
            if node.skills and node.children:
                raise EquivalenceProtocolError(f"node '{node.id}' contains both Skill members and children")
            actual.extend(skill.id for skill in node.skills)
            stack.extend(node.children)
        if sorted(actual) != expected or len(actual) != len(set(actual)):
            raise EquivalenceProtocolError("final equivalence tree does not contain every Skill exactly once")
        if len(plans) != len(scopes):
            raise EquivalenceProtocolError("final equivalence scope coverage invariant failed")

    @staticmethod
    def _validate_clusters(
        aliases: tuple[str, ...],
        groups: list[tuple[str, ...]],
        positive_edges: set[tuple[str, str]],
    ) -> None:
        flattened = [alias for group in groups for alias in group]
        if sorted(flattened) != sorted(aliases) or len(flattened) != len(set(flattened)):
            raise EquivalenceProtocolError("equivalence groups do not partition the scope exactly once")
        for group in groups:
            for pair in combinations(group, 2):
                if _canonical_pair(*pair) not in positive_edges:
                    raise EquivalenceProtocolError(f"multi-member group is not a complete-link clique: {group}")

    @staticmethod
    def _stable_group_id(scope_path: tuple[str, ...], member_ids: tuple[str, ...]) -> str:
        payload = json.dumps(
            {"scope_path": list(scope_path), "member_skill_ids": sorted(member_ids)},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return "equiv-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def _skill_profile(self, alias: str, skill: Skill) -> dict:
        return {
            "ref": alias,
            "name": self._compact(skill.name or skill.id, 200),
            "description": self._compact(skill.description, 600),
            "source_description": self._compact(skill.source_description, 600),
            "select_when": self._compact(skill.select_when, 300),
            "dont_select_when": self._compact(skill.dont_select_when, 300),
            "skill_md_summary": self._compact(skill.content, _CONTENT_SUMMARY_CHARS),
        }

    @classmethod
    def _scope_payload(cls, scope: _Scope) -> dict:
        return {
            "path": cls._compact(scope.path_text, 400),
            "id": cls._compact(scope.node.id, 200),
            "name": cls._compact(scope.node.name, 200),
            "description": cls._compact(scope.node.description, 600),
            "select_when": cls._compact(scope.node.select_when, 300),
            "dont_select_when": cls._compact(scope.node.dont_select_when, 300),
        }

    def _skill_hash(self, skill: Skill) -> str:
        return equivalence_skill_hash(skill)

    @staticmethod
    def _compact(value: object, limit: int) -> str:
        normalized = unicodedata.normalize("NFKC", str(value or ""))
        without_controls = "".join(
            " " if unicodedata.category(character).startswith("C") else character
            for character in normalized
        )
        text = " ".join(without_controls.split())
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 3)].rstrip() + "..."

    @staticmethod
    def _normalized_name(value: object) -> str:
        normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
        return "".join(character for character in normalized if character.isalnum())

    @staticmethod
    def _json(payload: object) -> str:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _public_decision(decision: dict) -> dict:
        return {key: value for key, value in decision.items() if not key.startswith("_")}

    def _decision_state(
        self,
        pair: tuple[str, str],
        decision: dict,
        *,
        skill_by_alias: dict[str, Skill],
        skill_hashes: dict[str, str],
        positive_edges: set[tuple[str, str]],
    ) -> dict:
        left_id = skill_by_alias[pair[0]].id
        right_id = skill_by_alias[pair[1]].id
        state = self._public_decision(decision)
        effective_equivalent = pair in positive_edges
        effective_verdict = "equivalent" if effective_equivalent else str(decision["verdict"])
        if decision["verdict"] == "equivalent" and not effective_equivalent:
            effective_verdict = "not_equivalent"
        rejection_reason = None
        if decision["verdict"] == "equivalent" and not effective_equivalent:
            rejection_reason = str(decision.get("_effective_rejection_reason") or "group_audit")
        state.update(
            protocol_hash=self.protocol_hash,
            left_skill_id=left_id,
            right_skill_id=right_id,
            left_content_hash=skill_hashes[left_id],
            right_content_hash=skill_hashes[right_id],
            source=decision.get("_source", "llm"),
            effective_verdict=effective_verdict,
            audit_rejected=bool(decision["verdict"] == "equivalent" and not effective_equivalent),
            effective_rejection_reason=rejection_reason,
        )
        return state

    def _run_metrics(self) -> dict:
        return {
            **self._metrics,
            "llm_calls": int(getattr(self._builder, "_llm_calls", 0) or 0) - self._llm_calls_start,
            "elapsed_ms": round((perf_counter() - self._started_at) * 1000.0, 3),
            "tokens": None,
        }

    def _report_summary(self, *, status: str) -> dict:
        return summarize_equivalence_scopes(
            self._scope_states,
            status=status,
            expected_input_count=int(self._metrics.get("skills") or 0),
        )

    def _build_report(self, *, status: str, error: str | None = None, failure_stage: str | None = None) -> dict:
        summary = self._report_summary(status=status)
        return {
            "status": status,
            "protocol_version": EQUIVALENCE_PROTOCOL_VERSION,
            "protocol_hash": self.protocol_hash,
            "model": str(getattr(self._builder, "model", "") or ""),
            "incremental_signature": hashlib.sha256(
                self._json(
                    {
                        "protocol_hash": self.protocol_hash,
                        "model": str(getattr(self._builder, "model", "") or ""),
                        "config": {
                            "all_pairs_scope_limit": self.all_pairs_scope_limit,
                            "candidate_neighbors": self.candidate_neighbors,
                            "max_pairwise_pairs": self.max_pairwise_pairs,
                        },
                    }
                ).encode("utf-8")
            ).hexdigest(),
            "config": {
                "all_pairs_scope_limit": self.all_pairs_scope_limit,
                "candidate_neighbors": self.candidate_neighbors,
                "max_pairwise_pairs": self.max_pairwise_pairs,
            },
            **summary,
            "metrics": self._run_metrics(),
            "failure_stage": failure_stage,
            "error": error,
            "scopes": self._scope_states,
        }

    def _write_artifacts(self, report: dict) -> None:
        audit_path = self.audit_path
        report_path = self.report_path
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        events = [*self._audit_events, equivalence_build_complete_event(report)]
        audit_payload = "".join(
            json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n" for event in events
        )
        self._atomic_write_text(audit_path, audit_payload)
        self._atomic_write_text(
            report_path,
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )

    @staticmethod
    def _atomic_write_text(path, content: str) -> None:
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
