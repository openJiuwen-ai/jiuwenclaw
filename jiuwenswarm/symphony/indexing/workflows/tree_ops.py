from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Sequence

from indexing.models import CatalogRecord


class EquivalenceIncrementalStateError(ValueError):
    """Raised when a persisted equivalence state cannot be updated safely."""


@dataclass(frozen=True)
class IncrementalEquivalenceResult:
    """Result of a branch-local equivalence update."""

    nodes: List[Dict[str, object]]
    report: Dict[str, object]
    audit_events: List[Dict[str, object]]
    affected_scope_cids: tuple[str, ...]


NormalizeEquivalenceScope = Callable[[str, dict, list[dict], int | None], dict]
RouteSkillToScope = Callable[[dict, Sequence[dict], Sequence[object]], str]


def scan_skill_paths(item_paths: Sequence[Path]) -> Dict[str, dict]:
    from indexing.scanners import SkillScanner

    skill_map: Dict[str, dict] = {}
    for path in item_paths:
        scanner = SkillScanner(path.parent if path.name == "skills" else path.parent)
        for item in scanner.to_dict_list():
            if str(item.get("id") or "") == path.name:
                skill_map[path.name] = item
    return skill_map


def merge_added_skills_into_tree(*, nodes: Sequence[object], added_skills: Dict[str, dict]) -> List[Dict[str, object]]:
    normalized = [dict(node) for node in nodes if isinstance(node, dict)]
    used_cids = {str(node.get("cid") or "") for node in normalized}
    branch_nodes = [node for node in normalized if str(node.get("type") or "") == "branch"]
    branch_token_cache = {
        str(node.get("cid") or ""): text_tokens(str(node.get("cid") or ""), str(node.get("description") or ""))
        for node in branch_nodes
    }
    for worker_id, skill in sorted(added_skills.items()):
        selected_parent_cid = choose_parent_branch_for_skill(
            skill=skill, branch_nodes=branch_nodes, branch_token_cache=branch_token_cache
        )
        cid = unique_child_cid(
            parent=selected_parent_cid,
            segment=slug_term(worker_id, fallback="skill"),
            used=used_cids,
        )
        node = {
            "cid": cid,
            "type": "leaf",
            "description": str(skill.get("description") or "").strip(),
            "worker_id": worker_id,
        }
        normalized.append(node)
        used_cids.add(cid)
    return normalized


def choose_parent_branch_for_skill(
    *, skill: dict, branch_nodes: Sequence[Dict[str, object]], branch_token_cache: Dict[str, set[str]]
) -> str:
    if not branch_nodes:
        return ""
    skill_tokens = text_tokens(
        str(skill.get("name") or ""),
        str(skill.get("description") or ""),
        str(skill.get("content") or ""),
    )
    best_cid = ""
    best_score = -1
    for node in branch_nodes:
        cid = str(node.get("cid") or "")
        overlap = len(skill_tokens & branch_token_cache.get(cid, set()))
        depth_bonus = len(cid.split(".")) if cid else 0
        score = overlap * 100 + depth_bonus
        if score > best_score:
            best_score = score
            best_cid = cid
    return best_cid


def update_equivalence_scopes(
    *,
    nodes: Sequence[object],
    report: dict,
    skills_by_id: Dict[str, dict],
    added_worker_ids: set[str],
    removed_worker_ids: set[str],
    normalize_scope: NormalizeEquivalenceScope,
    route_skill: RouteSkillToScope,
    max_pairwise_pairs: int | None = None,
) -> IncrementalEquivalenceResult:
    """Recompute only terminal taxonomy scopes affected by an incremental change.

    The persisted report is treated as the source of truth for equivalence-group
    membership.  A caller-provided normalizer performs the LLM-backed work for
    one scope and may reuse pairwise decisions from ``cached_state``.  This
    helper owns deterministic membership changes, flat-preset replacement, and
    end-to-end coverage validation.

    Args:
        nodes: Existing flat tree preset nodes.
        report: Complete ``equivalence_report.json`` payload from the base index.
        skills_by_id: All Skills that must remain in the resulting index.
        added_worker_ids: New or updated Skill ids.
        removed_worker_ids: Deleted Skill ids.  An id may occur in both sets for
            an update; the updated semantics are routed again and may move to a
            different taxonomy scope.
        normalize_scope: Callback
            ``(scope_cid, cached_state, skills, remaining_pair_budget) -> state``.
        route_skill: Callback used for every new or semantically updated Skill.
        max_pairwise_pairs: Optional hard budget accumulated across all affected
            scopes in this incremental operation.

    Returns:
        Updated nodes, report, audit summaries, and affected scope ids.

    Raises:
        EquivalenceIncrementalStateError: If the persisted state is incomplete,
            ambiguous, or the local result violates coverage invariants.
    """

    normalized_skills = {
        str(worker_id).strip(): dict(skill)
        for worker_id, skill in skills_by_id.items()
        if str(worker_id).strip() and isinstance(skill, dict)
    }
    target_worker_ids = set(normalized_skills)
    added_ids = {str(item).strip() for item in added_worker_ids if str(item).strip()}
    removed_ids = {str(item).strip() for item in removed_worker_ids if str(item).strip()}
    if not added_ids <= target_worker_ids:
        missing = sorted(added_ids - target_worker_ids)
        raise EquivalenceIncrementalStateError(
            f"Added Skills are missing from the incremental input: {missing[:5]}"
        )

    scopes = _validated_equivalence_scopes(report)
    state_by_scope = {str(scope["scope_cid"]): scope for scope in scopes}
    old_scope_by_worker: dict[str, str] = {}
    for scope_cid, state in state_by_scope.items():
        for worker_id in _scope_skill_ids(state):
            previous = old_scope_by_worker.setdefault(worker_id, scope_cid)
            if previous != scope_cid:
                raise EquivalenceIncrementalStateError(
                    f"Skill {worker_id!r} appears in multiple equivalence scopes: "
                    f"{previous!r}, {scope_cid!r}"
                )

    unknown_removed = removed_ids - set(old_scope_by_worker)
    if unknown_removed:
        raise EquivalenceIncrementalStateError(
            "Deleted Skills are absent from the persisted equivalence state: "
            f"{sorted(unknown_removed)[:5]}"
        )

    expected_target = (set(old_scope_by_worker) - removed_ids) | added_ids
    if expected_target != target_worker_ids:
        missing = sorted(target_worker_ids - expected_target)
        stale = sorted(expected_target - target_worker_ids)
        raise EquivalenceIncrementalStateError(
            "Persisted equivalence coverage does not match the base catalog "
            f"(missing={missing[:5]}, stale={stale[:5]})."
        )

    members_by_scope = {
        scope_cid: [
            worker_id
            for worker_id in _scope_skill_ids(state)
            if worker_id not in removed_ids or worker_id in added_ids
        ]
        for scope_cid, state in state_by_scope.items()
    }
    affected_scope_cids = {
        old_scope_by_worker[worker_id]
        for worker_id in (removed_ids | added_ids)
        if worker_id in old_scope_by_worker
    }
    moved_workers: dict[str, dict[str, str]] = {}

    for worker_id in sorted(added_ids):
        scope_cid = str(
            route_skill(normalized_skills[worker_id], list(state_by_scope.values()), nodes)
        ).strip()
        if scope_cid not in state_by_scope:
            raise EquivalenceIncrementalStateError(
                f"Incremental routing selected unknown taxonomy scope {scope_cid!r} "
                f"for Skill {worker_id!r}."
            )
        old_scope_cid = old_scope_by_worker.get(worker_id)
        if old_scope_cid and old_scope_cid != scope_cid:
            members_by_scope[old_scope_cid] = [
                item for item in members_by_scope[old_scope_cid] if item != worker_id
            ]
            affected_scope_cids.add(old_scope_cid)
            moved_workers[worker_id] = {"from_scope_cid": old_scope_cid, "to_scope_cid": scope_cid}
        if worker_id not in members_by_scope[scope_cid]:
            members_by_scope[scope_cid].append(worker_id)
        affected_scope_cids.add(scope_cid)

    updated_state_by_scope = {scope_cid: dict(state) for scope_cid, state in state_by_scope.items()}
    updated_nodes = [dict(node) for node in nodes if isinstance(node, dict)]
    audit_events: list[dict[str, object]] = []
    incremental_pair_count = 0

    for scope_cid in sorted(affected_scope_cids):
        cached_state = state_by_scope[scope_cid]
        member_ids = sorted(set(members_by_scope.get(scope_cid, ())))
        if member_ids:
            scope_skills = [normalized_skills[worker_id] for worker_id in member_ids]
            remaining_pair_budget = (
                None
                if max_pairwise_pairs is None
                else max(0, int(max_pairwise_pairs) - incremental_pair_count)
            )
            new_state = normalize_scope(
                scope_cid,
                cached_state,
                scope_skills,
                remaining_pair_budget,
            )
            if not isinstance(new_state, dict):
                raise EquivalenceIncrementalStateError(
                    f"Equivalence normalizer returned no state for scope {scope_cid!r}."
                )
            new_state = dict(new_state)
            new_state["scope_cid"] = scope_cid
            _validate_normalized_scope_state(new_state, expected_worker_ids=set(member_ids))
            scope_pair_count = int(
                new_state.get("pairwise_pair_count", len(new_state.get("candidate_pairs") or [])) or 0
            )
            if scope_pair_count < 0:
                raise EquivalenceIncrementalStateError(
                    f"Scope {scope_cid!r} returned a negative pairwise pair count."
                )
            incremental_pair_count += scope_pair_count
            if max_pairwise_pairs is not None and incremental_pair_count > max(0, int(max_pairwise_pairs)):
                raise EquivalenceIncrementalStateError(
                    "Incremental equivalence candidate pair budget exceeded across affected scopes: "
                    f"{incremental_pair_count} > {max(0, int(max_pairwise_pairs))}."
                )
            updated_state_by_scope[scope_cid] = new_state
        else:
            new_state = _empty_equivalence_scope_state(cached_state, scope_cid=scope_cid)
            updated_state_by_scope[scope_cid] = new_state

        updated_nodes = replace_equivalence_scope_nodes(
            nodes=updated_nodes,
            scope_cid=scope_cid,
            scope_state=new_state,
            skills_by_id=normalized_skills,
        )
        audit_events.append(
            {
                "event": "incremental_scope_update",
                "scope_cid": scope_cid,
                "added_worker_ids": sorted(
                    worker_id for worker_id in added_ids if worker_id in member_ids
                ),
                "removed_worker_ids": sorted(
                    worker_id
                    for worker_id in (removed_ids | set(moved_workers))
                    if old_scope_by_worker.get(worker_id) == scope_cid
                    and (
                        worker_id not in added_ids
                        or moved_workers.get(worker_id, {}).get("from_scope_cid") == scope_cid
                    )
                ),
                "final_worker_ids": member_ids,
                "pairwise_decision_count": len((new_state or {}).get("pairwise_decisions") or []),
                "candidate_pair_count": len((new_state or {}).get("candidate_pairs") or []),
                "pairwise_pair_count": int((new_state or {}).get("pairwise_pair_count") or 0),
                "group_count": len((new_state or {}).get("groups") or []),
            }
        )

    final_scopes = [updated_state_by_scope[key] for key in sorted(updated_state_by_scope)]
    _validate_scope_coverage(final_scopes, expected_worker_ids=target_worker_ids)
    _validate_flat_worker_coverage(updated_nodes, expected_worker_ids=target_worker_ids)

    updated_report = dict(report)
    updated_report["status"] = "complete"
    updated_report["scopes"] = final_scopes
    updated_report["scope_count"] = len(final_scopes)
    updated_report["skill_count"] = len(target_worker_ids)
    updated_report["group_count"] = sum(len(scope.get("groups") or []) for scope in final_scopes)
    updated_report["pairwise_decision_count"] = sum(
        len(scope.get("pairwise_decisions") or []) for scope in final_scopes
    )
    metrics = dict(updated_report.get("metrics") or {})
    decisions = [
        decision
        for scope in final_scopes
        for decision in (scope.get("pairwise_decisions") or [])
        if isinstance(decision, dict)
    ]
    groups = [
        group
        for scope in final_scopes
        for group in (scope.get("groups") or [])
        if isinstance(group, dict)
    ]
    metrics.update(
        scopes=len(final_scopes),
        skills=len(target_worker_ids),
        candidate_pairs=sum(len(scope.get("candidate_pairs") or []) for scope in final_scopes),
        pairwise_equivalent=sum(
            (item.get("effective_verdict") or item.get("verdict")) == "equivalent"
            for item in decisions
        ),
        pairwise_not_equivalent=sum(
            (item.get("effective_verdict") or item.get("verdict")) == "not_equivalent"
            for item in decisions
        ),
        pairwise_insufficient_evidence=sum(
            (item.get("effective_verdict") or item.get("verdict")) == "insufficient_evidence"
            for item in decisions
        ),
        audit_rejected_pairs=sum(bool(item.get("audit_rejected")) for item in decisions),
        singleton_groups=sum(len(group.get("member_skill_ids") or []) == 1 for group in groups),
        multi_member_groups=sum(len(group.get("member_skill_ids") or []) > 1 for group in groups),
    )
    updated_report["metrics"] = metrics
    updated_report["last_operation"] = {
        "mode": "incremental",
        "added_worker_ids": sorted(added_ids),
        "removed_worker_ids": sorted(removed_ids - added_ids),
        "affected_scope_cids": sorted(affected_scope_cids),
        "candidate_pair_count": incremental_pair_count,
        "moved_workers": [
            {"worker_id": worker_id, **moved_workers[worker_id]}
            for worker_id in sorted(moved_workers)
        ],
    }
    from indexing.tree.equivalence import summarize_equivalence_scopes

    updated_report.update(
        summarize_equivalence_scopes(
            final_scopes,
            status="complete",
            expected_input_count=len(target_worker_ids),
        )
    )
    return IncrementalEquivalenceResult(
        nodes=updated_nodes,
        report=updated_report,
        audit_events=audit_events,
        affected_scope_cids=tuple(sorted(affected_scope_cids)),
    )


def replace_equivalence_scope_nodes(
    *,
    nodes: Sequence[object],
    scope_cid: str,
    scope_state: dict | None,
    skills_by_id: Dict[str, dict],
) -> List[Dict[str, object]]:
    """Replace one terminal scope's equivalence layer in a flat tree preset."""

    normalized_scope_cid = str(scope_cid).strip()
    if not normalized_scope_cid:
        raise EquivalenceIncrementalStateError("Equivalence scope CID is empty.")
    prefix = normalized_scope_cid + "."
    kept = [
        dict(node)
        for node in nodes
        if isinstance(node, dict) and not str(node.get("cid") or "").startswith(prefix)
    ]
    if not any(str(node.get("cid") or "") == normalized_scope_cid for node in kept):
        raise EquivalenceIncrementalStateError(
            f"Taxonomy scope {normalized_scope_cid!r} is absent from the flat tree."
        )
    if scope_state is None:
        return kept

    from indexing.tree.preset_writer import TreePresetWriter

    groups = list(scope_state.get("groups") or [])
    used_cids = {str(node.get("cid") or "") for node in kept}
    additions: list[dict[str, object]] = []
    for group in sorted(groups, key=lambda item: str(item.get("group_id") or "")):
        group_id = str(group.get("group_id") or "").strip()
        member_ids = sorted(str(item).strip() for item in group.get("member_skill_ids") or [] if str(item).strip())
        if not group_id or not member_ids:
            raise EquivalenceIncrementalStateError(
                f"Scope {normalized_scope_cid!r} contains an invalid equivalence group."
            )
        group_cid = unique_child_cid(
            parent=normalized_scope_cid,
            segment=TreePresetWriter.cid_term(group_id, fallback="EquivalenceGroup"),
            used=used_cids,
        )
        used_cids.add(group_cid)
        additions.append(
            {
                "cid": group_cid,
                "type": "branch",
                "description": str(group.get("description") or group.get("name") or group_id).strip(),
                "select_when": str(group.get("select_when") or "").strip(),
                "dont_select_when": str(group.get("dont_select_when") or "").strip(),
            }
        )
        for worker_id in member_ids:
            skill = skills_by_id.get(worker_id)
            if skill is None:
                raise EquivalenceIncrementalStateError(
                    f"Equivalence group {group_id!r} references unknown Skill {worker_id!r}."
                )
            leaf_seed = TreePresetWriter.compact_leaf_cid_seed(
                worker_id=worker_id,
                display_name="",
                old_term=worker_id,
            )
            leaf_cid = unique_child_cid(
                parent=group_cid,
                segment=TreePresetWriter.cid_term(leaf_seed, fallback="Skill"),
                used=used_cids,
            )
            used_cids.add(leaf_cid)
            additions.append(
                {
                    "cid": leaf_cid,
                    "type": "leaf",
                    "worker_id": worker_id,
                    "description": str(skill.get("description") or "").strip(),
                    "select_when": str(skill.get("select_when") or "").strip(),
                    "dont_select_when": str(skill.get("dont_select_when") or "").strip(),
                    "source_description": str(skill.get("source_description") or "").strip(),
                }
            )
    return kept + additions


def _validated_equivalence_scopes(report: dict) -> list[dict]:
    if not isinstance(report, dict) or str(report.get("status") or "") != "complete":
        raise EquivalenceIncrementalStateError("Base equivalence report is missing or incomplete.")
    raw_scopes = report.get("scopes")
    if not isinstance(raw_scopes, list) or not raw_scopes:
        raise EquivalenceIncrementalStateError("Base equivalence report has no reusable scopes.")
    scopes: list[dict] = []
    seen_scope_cids: set[str] = set()
    for raw_scope in raw_scopes:
        if not isinstance(raw_scope, dict):
            raise EquivalenceIncrementalStateError("Base equivalence report contains a non-object scope.")
        state = dict(raw_scope)
        scope_cid = str(state.get("scope_cid") or "").strip()
        if not scope_cid or scope_cid in seen_scope_cids:
            raise EquivalenceIncrementalStateError(
                f"Base equivalence report contains an invalid scope CID: {scope_cid!r}."
            )
        seen_scope_cids.add(scope_cid)
        _validate_normalized_scope_state(state, expected_worker_ids=set(_scope_skill_ids(state)))
        scopes.append(state)
    return scopes


def _scope_skill_ids(scope_state: dict) -> list[str]:
    raw_skills = scope_state.get("skills") or []
    if not isinstance(raw_skills, list):
        raise EquivalenceIncrementalStateError("Equivalence scope skills must be a list.")
    skill_ids: list[str] = []
    for item in raw_skills:
        if isinstance(item, dict):
            worker_id = str(item.get("skill_id") or "").strip()
        else:
            worker_id = str(item).strip()
        if not worker_id:
            raise EquivalenceIncrementalStateError("Equivalence scope contains an empty Skill id.")
        skill_ids.append(worker_id)
    if len(skill_ids) != len(set(skill_ids)):
        raise EquivalenceIncrementalStateError("Equivalence scope contains duplicate Skill ids.")
    return skill_ids


def _validate_normalized_scope_state(scope_state: dict, *, expected_worker_ids: set[str]) -> None:
    state_worker_ids = set(_scope_skill_ids(scope_state))
    if state_worker_ids != expected_worker_ids:
        raise EquivalenceIncrementalStateError(
            "Normalized scope Skill coverage mismatch "
            f"(expected={sorted(expected_worker_ids)}, actual={sorted(state_worker_ids)})."
        )
    groups = scope_state.get("groups")
    if not isinstance(groups, list):
        raise EquivalenceIncrementalStateError("Normalized scope equivalence groups must be a list.")
    if not expected_worker_ids:
        if groups:
            raise EquivalenceIncrementalStateError("Empty taxonomy scope must not retain equivalence groups.")
        return
    if not groups:
        raise EquivalenceIncrementalStateError("Normalized non-empty scope has no equivalence groups.")
    grouped_ids: list[str] = []
    seen_group_ids: set[str] = set()
    for group in groups:
        if not isinstance(group, dict):
            raise EquivalenceIncrementalStateError("Equivalence group must be an object.")
        group_id = str(group.get("group_id") or "").strip()
        if not group_id or group_id in seen_group_ids:
            raise EquivalenceIncrementalStateError(f"Invalid or duplicate equivalence group id: {group_id!r}.")
        seen_group_ids.add(group_id)
        members = [str(item).strip() for item in group.get("member_skill_ids") or [] if str(item).strip()]
        if not members or len(members) != len(set(members)):
            raise EquivalenceIncrementalStateError(
                f"Equivalence group {group_id!r} has empty or duplicate membership."
            )
        grouped_ids.extend(members)
    if len(grouped_ids) != len(set(grouped_ids)) or set(grouped_ids) != expected_worker_ids:
        raise EquivalenceIncrementalStateError(
            "Equivalence groups must cover every scope Skill exactly once."
        )


def _empty_equivalence_scope_state(cached_state: dict, *, scope_cid: str) -> dict:
    state = dict(cached_state)
    state.update(
        scope_cid=scope_cid,
        skills=[],
        skill_hashes={},
        candidate_pairs=[],
        pairwise_pair_count=0,
        pairwise_decisions=[],
        audit_rejected_pairs=[],
        groups=[],
        status="complete",
    )
    return state


def _validate_scope_coverage(scopes: Sequence[dict], *, expected_worker_ids: set[str]) -> None:
    worker_ids: list[str] = []
    for scope in scopes:
        worker_ids.extend(_scope_skill_ids(scope))
    if len(worker_ids) != len(set(worker_ids)) or set(worker_ids) != expected_worker_ids:
        raise EquivalenceIncrementalStateError(
            "Incremental equivalence scopes do not cover the target catalog exactly once."
        )


def _validate_flat_worker_coverage(nodes: Sequence[object], *, expected_worker_ids: set[str]) -> None:
    worker_ids = [
        str(node.get("worker_id") or "").strip()
        for node in nodes
        if isinstance(node, dict) and str(node.get("worker_id") or "").strip()
    ]
    if len(worker_ids) != len(set(worker_ids)) or set(worker_ids) != expected_worker_ids:
        raise EquivalenceIncrementalStateError(
            "Incremental flat tree does not cover the target catalog exactly once."
        )


def prune_deleted_skills_from_tree(nodes: Sequence[object], *, removed_worker_ids: set[str]) -> List[Dict[str, object]]:
    normalized = [dict(node) for node in nodes if isinstance(node, dict)]
    normalized = [node for node in normalized if str(node.get("worker_id") or "") not in removed_worker_ids]
    while True:
        child_counts: Dict[str, int] = {}
        for node in normalized:
            cid = str(node.get("cid") or "")
            if not cid:
                continue
            parent = parent_cid(cid)
            if parent:
                child_counts[parent] = child_counts.get(parent, 0) + 1
        pruned = [
            node
            for node in normalized
            if str(node.get("type") or "") != "branch" or child_counts.get(str(node.get("cid") or ""), 0) > 0
        ]
        if len(pruned) == len(normalized):
            return pruned
        normalized = pruned


def align_leaf_nodes_with_catalog(
    nodes: Sequence[object], records_by_worker: Dict[str, CatalogRecord]
) -> List[Dict[str, object]]:
    aligned: List[Dict[str, object]] = []
    for raw_node in nodes:
        if not isinstance(raw_node, dict):
            continue
        node = dict(raw_node)
        worker_id = str(node.get("worker_id") or "").strip()
        if worker_id and worker_id in records_by_worker:
            record = records_by_worker[worker_id]
            node["cid"] = record.cid
            node["description"] = record.description
        aligned.append(node)
    return aligned


def build_catalog_records_from_existing(
    *, nodes: Sequence[object], records_by_worker: Dict[str, CatalogRecord]
) -> List[CatalogRecord]:
    records: List[CatalogRecord] = []
    for raw_node in nodes:
        if not isinstance(raw_node, dict):
            continue
        worker_id = str(raw_node.get("worker_id") or "").strip()
        if not worker_id:
            continue
        record = records_by_worker.get(worker_id)
        if record is None:
            continue
        records.append(
            CatalogRecord(
                worker_id=record.worker_id,
                cid=str(raw_node.get("cid") or record.cid),
                name=record.name,
                description=str(raw_node.get("description") or record.description),
                skill_path=record.skill_path,
                branch_path=tuple(str(raw_node.get("cid") or record.cid).split(".")[:-1]),
                category=record.category,
                retrieval_text=record.retrieval_text,
                metadata=dict(record.metadata),
                tags=tuple(record.tags),
            )
        )
    return sorted(records, key=lambda item: item.cid)


def tree_nodes_to_tree_dict(nodes: Sequence[object], records: Sequence[CatalogRecord]) -> dict:
    nodes_by_cid = {str(node.get("cid") or ""): dict(node) for node in nodes if isinstance(node, dict)}
    children: Dict[str, list[str]] = {}
    for cid in nodes_by_cid:
        parent = parent_cid(cid)
        children.setdefault(parent, []).append(cid)
    records_by_cid = {record.cid: record for record in records}

    def build(cid: str) -> dict:
        node = nodes_by_cid.get(cid, {})
        label = cid.split(".")[-1] if cid else "ROOT"
        payload = {
            "name": str(node.get("worker_id") or label),
            "cid": cid,
            "type": str(node.get("type") or "branch"),
            "description": str(node.get("description") or ""),
            "children": [build(child) for child in sorted(children.get(cid, []))],
        }
        record = records_by_cid.get(cid)
        if record is not None:
            payload["skill_path"] = record.skill_path
            payload["worker_id"] = record.worker_id
        return payload

    root_children = [build(cid) for cid in sorted(children.get("", []))]
    return {"name": "ROOT", "cid": "ROOT", "type": "root", "children": root_children}


def enrich_branch_descriptions(
    nodes: Sequence[object], *, catalog_records: Sequence[CatalogRecord]
) -> List[Dict[str, object]]:
    catalog_by_branch: Dict[str, list[CatalogRecord]] = {}
    for record in catalog_records:
        parts = record.cid.split(".")
        for depth in range(1, len(parts)):
            branch_cid = ".".join(parts[:depth])
            catalog_by_branch.setdefault(branch_cid, []).append(record)

    enriched: List[Dict[str, object]] = []
    for raw_node in nodes:
        if not isinstance(raw_node, dict):
            continue
        node = dict(raw_node)
        if str(node.get("type") or "") == "branch":
            cid = str(node.get("cid") or "")
            node["description"] = build_branch_description(
                cid=cid,
                existing_description=str(node.get("description") or ""),
                descendants=catalog_by_branch.get(cid, ()),
            )
        enriched.append(node)
    return enriched


def build_branch_description(*, cid: str, existing_description: str, descendants: Sequence[CatalogRecord]) -> str:
    base = strip_branch_exposure(existing_description)
    if not descendants:
        return base
    samples = sample_catalog_records(descendants, limit=3)
    parts: List[str] = []
    if base:
        parts.append(base)
    parts.append(f"Covers {len(descendants)} descendant skill{'s' if len(descendants) != 1 else ''}.")
    keywords = collect_branch_keywords(descendants, limit=8)
    if keywords:
        parts.append("Representative keywords: " + ", ".join(keywords))
    parts.append(
        "Representative descendants: " + "; ".join(format_catalog_record_snippet(record) for record in samples)
    )
    return "\n\n".join(part for part in parts if part).strip()


def strip_branch_exposure(description: str) -> str:
    """Remove branch exposure text generated by :func:`build_branch_description`.

    Incremental builds enrich an already-published tree again.  The generated
    suffix therefore has to be removed as one unit; stripping only the final
    ``Representative descendants`` paragraph would duplicate the preceding
    coverage and keyword paragraphs on every update.
    """

    text = str(description or "").strip()
    cut_at = len(text)
    for marker in (
        "Covers ",
        "Representative keywords:",
        "Representative descendants:",
    ):
        if text.startswith(marker):
            cut_at = 0
        paragraph_index = text.find(f"\n\n{marker}")
        if paragraph_index >= 0:
            cut_at = min(cut_at, paragraph_index)
    return text[:cut_at].strip()


def sample_catalog_records(records: Sequence[CatalogRecord], *, limit: int) -> List[CatalogRecord]:
    target = max(0, limit)
    if target <= 0:
        return []
    ordered = sorted(records, key=lambda item: (item.name.lower(), item.worker_id.lower(), item.cid))
    selected: List[CatalogRecord] = []
    seen_worker_ids: set[str] = set()
    seen_tokens: set[str] = set()
    while len(selected) < min(target, len(ordered)):
        best_record: CatalogRecord | None = None
        best_score = -1
        for record in ordered:
            if record.worker_id in seen_worker_ids:
                continue
            tokens = _record_text_tokens(record)
            novelty = len(tokens - seen_tokens)
            coverage = len(tokens)
            score = novelty * 10 + coverage
            if best_record is None or score > best_score:
                best_record = record
                best_score = score
        if best_record is None:
            break
        selected.append(best_record)
        seen_worker_ids.add(best_record.worker_id)
        seen_tokens.update(_record_text_tokens(best_record))
    return selected


def format_catalog_record_snippet(record: CatalogRecord) -> str:
    name = str(record.name or record.worker_id).strip() or record.worker_id
    summary = _compact_summary(record.description, limit=96)
    if summary:
        return f"{name}: {summary}"
    return name


def collect_branch_keywords(records: Sequence[CatalogRecord], *, limit: int) -> List[str]:
    counter: Counter[str] = Counter()
    for record in records:
        counter.update(_record_text_tokens(record))
    if not counter:
        return []
    ranked = sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    return [token for token, _count in ranked[:max(0, limit)]]


def _record_text_tokens(record: CatalogRecord) -> set[str]:
    return text_tokens(record.name, record.description, record.worker_id, record.cid)


def _compact_summary(text: str, *, limit: int) -> str:
    normalized = " ".join(str(text or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[:max(0, limit - 3)].rstrip() + "..."


def text_tokens(*values: str) -> set[str]:
    tokens: set[str] = set()
    for value in values:
        for token in str(value or "").replace("-", " ").replace("_", " ").replace(".", " ").split():
            cleaned = token.strip().lower()
            if cleaned:
                tokens.add(cleaned)
    return tokens


def slug_term(value: str, fallback: str = "node") -> str:
    raw = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(value or ""))
    compact = "-".join(part for part in raw.split("-") if part)
    return compact or fallback


def join_cid(parent: str, child: str) -> str:
    return f"{parent}.{child}" if parent else child


def parent_cid(cid: str) -> str:
    return cid.rsplit(".", 1)[0] if "." in cid else ""


def unique_child_cid(*, parent: str, segment: str, used: set[str]) -> str:
    candidate = join_cid(parent, segment)
    if candidate not in used:
        return candidate
    index = 2
    while True:
        candidate = join_cid(parent, f"{segment}-{index}")
        if candidate not in used:
            return candidate
        index += 1
