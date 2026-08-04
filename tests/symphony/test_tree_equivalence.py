from __future__ import annotations

import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from jiuwenswarm.symphony.skill_retrieval.dispatch_imports import dispatch_import_path


def _dimensions(value: str = "same") -> dict[str, str]:
    return {
        "primary_action": value,
        "target_object": "same",
        "input_precondition": "same",
        "result_or_side_effect": "same",
        "specialized_scope": "same",
        "user_visible_platform": "same",
        "bundle_breadth": "same",
    }


def _decision(left: str, right: str, verdict: str) -> dict:
    if verdict == "equivalent":
        return {
            "left": left,
            "right": right,
            "verdict": verdict,
            "left_replaces_right": True,
            "right_replaces_left": True,
            "dimensions": _dimensions(),
            "common_request": "Create a standard presentation deck from an outline.",
            "distinguishing_request": "",
            "reason_code": "mutual_substitute",
            "reason": "Both produce the same user-visible deck from the same input.",
        }
    return {
        "left": left,
        "right": right,
        "verdict": verdict,
        "left_replaces_right": False,
        "right_replaces_left": False,
        "dimensions": _dimensions("different"),
        "common_request": "",
        "distinguishing_request": "Create an academic poster rather than a standard slide deck.",
        "reason_code": "action_mismatch",
        "reason": "The primary deliverable differs.",
    }


def _audit(result: str = "pass", conflicts: list[dict] | None = None) -> dict:
    passed = result == "pass"
    return {
        "result": result,
        "capability": {
            "name": "Presentation Deck Generation" if passed else "",
            "description": "Generate a standard presentation deck from a user outline." if passed else "",
            "select_when": "The user asks to create a presentation deck." if passed else "",
            "dont_select_when": "The user asks for a poster or a non-presentation document." if passed else "",
        },
        "conflicts": list(conflicts or []),
    }


def _insufficient_decision(left: str, right: str) -> dict:
    dimensions = _dimensions()
    dimensions["input_precondition"] = "unknown"
    return {
        "left": left,
        "right": right,
        "verdict": "insufficient_evidence",
        "left_replaces_right": None,
        "right_replaces_left": None,
        "dimensions": dimensions,
        "common_request": "",
        "distinguishing_request": "",
        "reason_code": "insufficient_description",
        "reason": "The input contract is not documented.",
    }


class _ScriptedBuilder:
    def __init__(self, output_path: Path, responses: list[dict], *, all_pairs_limit: int = 12) -> None:
        self.output_path = output_path
        self.model = "fake-model"
        self._manager_config = SimpleNamespace(
            build=SimpleNamespace(
                equivalence_all_pairs_scope_limit=all_pairs_limit,
                equivalence_candidate_neighbors=8,
                equivalence_max_pairwise_pairs=10000,
            )
        )
        self._thread_local = threading.local()
        self._llm_calls = 0
        self.responses = list(responses)
        self.prompts: list[str] = []

    def _call_llm(self, prompt: str, is_retry: bool = False) -> str:
        self._llm_calls += 1
        self._thread_local.truncated = False
        self.prompts.append(prompt)
        if not self.responses:
            raise AssertionError("unexpected model call")
        return json.dumps(self.responses.pop(0))


def _skill(Skill, skill_id: str, name: str):
    return Skill(
        item_id=skill_id,
        name=name,
        description=f"{name} capability",
        content=f"# {name}\nUse this Skill to handle {name} requests.",
    )


def test_direct_tree_builder_defaults_equivalence_off_and_writes_yaml(tmp_path: Path) -> None:
    with dispatch_import_path():
        from indexing.tree.builder import TreeBuilder
        from indexing.tree.schema import TreeBuildConfig, TreeManagerConfig

        build_config = TreeBuildConfig(postprocess_enabled=False)
        assert build_config.equiv_grouping_enabled is False
        output_path = tmp_path / "tree_index.yaml"
        builder = TreeBuilder(
            skills_dir=tmp_path,
            output_path=output_path,
            manager_config=TreeManagerConfig(build=build_config),
            client=SimpleNamespace(),
            model="fake-model",
            skill_entries=[{"id": "skill-a", "name": "Skill A", "description": "Capability A"}],
        )

        builder._write_yaml({"tree_sketch": "", "nodes": []})

        assert output_path.exists()
        assert builder._skill_profiles_enabled is False
        assert builder._cache_observability is False
        assert not (tmp_path / "equivalence_report.json").exists()


def test_individual_skills_form_cliques_and_preserve_taxonomy_scope(tmp_path: Path) -> None:
    with dispatch_import_path():
        from indexing.tree.equivalence import EquivalenceNormalizer
        from indexing.tree.schema import Skill, TreeNode

        skills = [
            _skill(Skill, "skill-a", "Deck Generator A"),
            _skill(Skill, "skill-b", "Deck Generator B"),
            _skill(Skill, "skill-c", "Poster Generator"),
        ]
        scope = TreeNode(
            node_id="presentations",
            name="Presentations",
            description="Presentation creation taxonomy branch.",
            children=[
                TreeNode(node_id="leaf-c", name="C", skills=[skills[2]], depth=3),
                TreeNode(node_id="leaf-a", name="A", skills=[skills[0]], depth=3),
                TreeNode(node_id="leaf-b", name="B", skills=[skills[1]], depth=3),
            ],
            depth=2,
            parent_id="office",
        )
        office = TreeNode(node_id="office", name="Office", children=[scope], depth=1, parent_id="root")
        root = TreeNode(node_id="root", name="Root", children=[office])
        pairwise = {
            "decisions": [
                _decision("s000001", "s000002", "equivalent"),
                _decision("s000001", "s000003", "not_equivalent"),
                _decision("s000002", "s000003", "equivalent"),
            ]
        }
        builder = _ScriptedBuilder(
            tmp_path / "tree_index.yaml",
            [pairwise, _audit()],
        )

        report = EquivalenceNormalizer(builder).normalize(root)

        assert root.children == [office]
        assert office.children == [scope]
        assert scope.skills == []
        assert all(child.id.startswith("equiv-") for child in scope.children)
        assert all(child.is_leaf and child.skills for child in scope.children)
        member_sets = {frozenset(skill.id for skill in child.skills) for child in scope.children}
        assert member_sets == {frozenset({"skill-a", "skill-b"}), frozenset({"skill-c"})}
        assert report["status"] == "complete"
        assert report["metrics"]["multi_member_groups"] == 1
        assert (tmp_path / "equivalence_audit.jsonl").exists()
        assert (tmp_path / "equivalence_report.json").exists()


@pytest.mark.parametrize("category_count", [1, 2])
def test_synthetic_root_never_becomes_an_equivalence_scope(
    tmp_path: Path,
    category_count: int,
) -> None:
    with dispatch_import_path():
        from indexing.tree.equivalence import EquivalenceNormalizer
        from indexing.tree.schema import Skill, TreeNode

        categories = [
            TreeNode(
                node_id=f"category-{index}",
                name=f"Category {index}",
                skills=[_skill(Skill, f"skill-{index}", f"Skill {index}")],
                depth=1,
                parent_id="root",
            )
            for index in range(1, category_count + 1)
        ]
        root = TreeNode(node_id="root", name="Root", children=list(categories))
        builder = _ScriptedBuilder(tmp_path / "tree_index.yaml", [])

        report = EquivalenceNormalizer(builder).normalize(root)

        assert root.children == categories
        assert report["metrics"]["scopes"] == category_count
        assert builder._llm_calls == 0
        for index, category in enumerate(categories, start=1):
            assert category.id == f"category-{index}"
            assert len(category.children) == 1
            assert category.children[0].id.startswith("equiv-")
            assert [skill.id for skill in category.children[0].skills] == [f"skill-{index}"]


def test_scope_unions_individual_skills_across_terminal_taxonomy_leaves(tmp_path: Path) -> None:
    with dispatch_import_path():
        from indexing.tree.equivalence import EquivalenceNormalizer
        from indexing.tree.schema import Skill, TreeNode

        presentation_scope = TreeNode(
            node_id="presentations",
            name="Presentations",
            description="Presentation creation taxonomy branch.",
            children=[
                TreeNode(
                    node_id="provider-a",
                    name="Provider A",
                    skills=[_skill(Skill, "skill-a", "Deck Generator A")],
                    depth=3,
                    parent_id="presentations",
                ),
                TreeNode(
                    node_id="provider-b",
                    name="Provider B",
                    skills=[_skill(Skill, "skill-b", "Deck Generator B")],
                    depth=3,
                    parent_id="presentations",
                ),
            ],
            depth=2,
            parent_id="office",
        )
        office = TreeNode(
            node_id="office",
            name="Office",
            children=[presentation_scope],
            depth=1,
            parent_id="root",
        )
        root = TreeNode(node_id="root", name="Root", children=[office])
        builder = _ScriptedBuilder(
            tmp_path / "tree_index.yaml",
            [
                {"decisions": [_decision("s000001", "s000002", "equivalent")]},
                _audit(),
            ],
        )

        EquivalenceNormalizer(builder).normalize(root)

        assert root.children == [office]
        assert office.children == [presentation_scope]
        assert len(presentation_scope.children) == 1
        group = presentation_scope.children[0]
        assert group.id.startswith("equiv-")
        assert {skill.id for skill in group.skills} == {"skill-a", "skill-b"}
        assert group.name == "Presentation Deck Generation"
        assert all(child.id not in {"provider-a", "provider-b"} for child in presentation_scope.children)


def test_one_terminal_taxonomy_leaf_uses_its_parent_as_scope(tmp_path: Path) -> None:
    with dispatch_import_path():
        from indexing.tree.equivalence import EquivalenceNormalizer
        from indexing.tree.schema import Skill, TreeNode

        terminal_leaf = TreeNode(
            node_id="provider-variants",
            name="Provider Variants",
            skills=[
                _skill(Skill, "skill-a", "Deck Generator A"),
                _skill(Skill, "skill-b", "Deck Generator B"),
            ],
            depth=3,
            parent_id="presentations",
        )
        presentation_scope = TreeNode(
            node_id="presentations",
            name="Presentations",
            children=[terminal_leaf],
            depth=2,
            parent_id="office",
        )
        office = TreeNode(
            node_id="office",
            name="Office",
            children=[presentation_scope],
            depth=1,
            parent_id="root",
        )
        root = TreeNode(node_id="root", name="Root", children=[office])
        builder = _ScriptedBuilder(
            tmp_path / "tree_index.yaml",
            [
                {"decisions": [_decision("s000001", "s000002", "equivalent")]},
                _audit(),
            ],
        )

        report = EquivalenceNormalizer(builder).normalize(root)

        assert report["scopes"][0]["scope_path_parts"] == ["root", "office", "presentations"]
        assert office.children == [presentation_scope]
        assert len(presentation_scope.children) == 1
        assert presentation_scope.children[0].id.startswith("equiv-")
        assert {skill.id for skill in presentation_scope.children[0].skills} == {"skill-a", "skill-b"}


def test_group_audit_removes_conflict_edge_and_reclusters(tmp_path: Path) -> None:
    with dispatch_import_path():
        from indexing.tree.equivalence import EquivalenceNormalizer
        from indexing.tree.schema import Skill, TreeNode

        scope = TreeNode(
            node_id="presentations",
            name="Presentations",
            children=[
                TreeNode(node_id="leaf-a", name="A", skills=[_skill(Skill, "skill-a", "A")]),
                TreeNode(node_id="leaf-b", name="B", skills=[_skill(Skill, "skill-b", "B")]),
                TreeNode(node_id="leaf-c", name="C", skills=[_skill(Skill, "skill-c", "C")]),
            ],
        )
        root = TreeNode(node_id="root", name="Root", children=[scope])
        pairwise = {
            "decisions": [
                _decision("s000001", "s000002", "equivalent"),
                _decision("s000001", "s000003", "equivalent"),
                _decision("s000002", "s000003", "equivalent"),
            ]
        }
        conflict = _audit(
            "conflict",
            [
                {"left": "s000001", "right": "s000003", "reason": "Different specialized scope."}
            ],
        )
        builder = _ScriptedBuilder(
            tmp_path / "tree_index.yaml",
            [pairwise, conflict, _audit()],
        )

        report = EquivalenceNormalizer(builder).normalize(root)

        member_sets = {frozenset(skill.id for skill in child.skills) for child in scope.children}
        assert member_sets == {frozenset({"skill-a", "skill-b"}), frozenset({"skill-c"})}
        assert report["metrics"]["audit_conflicts"] == 1
        assert report["metrics"]["audit_reclusters"] == 1
        rejected = [
            row for row in report["scopes"][0]["pairwise_decisions"] if row["audit_rejected"]
        ]
        assert [(row["left_skill_id"], row["right_skill_id"]) for row in rejected] == [
            ("skill-a", "skill-c")
        ]

        cached_scope = TreeNode(
            node_id="presentations",
            name="Presentations",
            skills=[
                _skill(Skill, "skill-a", "A"),
                _skill(Skill, "skill-b", "B"),
                _skill(Skill, "skill-c", "C"),
            ],
        )
        cache_builder = _ScriptedBuilder(tmp_path / "cached" / "tree_index.yaml", [])
        EquivalenceNormalizer(cache_builder).normalize_scope(
            cached_scope,
            ("root", "presentations"),
            cached_state=report["scopes"][0],
        )
        cached_member_sets = {
            frozenset(skill.id for skill in child.skills) for child in cached_scope.children
        }
        assert cache_builder._llm_calls == 0
        assert cached_member_sets == member_sets


def test_invalid_protocol_fails_after_one_correction_without_mutating_tree(tmp_path: Path) -> None:
    with dispatch_import_path():
        from indexing.tree.equivalence import EquivalenceNormalizer, EquivalenceProtocolError
        from indexing.tree.schema import Skill, TreeNode

        original_skills = [_skill(Skill, "skill-a", "A"), _skill(Skill, "skill-b", "B")]
        scope = TreeNode(node_id="presentations", name="Presentations", skills=list(original_skills))
        root = TreeNode(node_id="root", name="Root", children=[scope])
        builder = _ScriptedBuilder(tmp_path / "tree_index.yaml", [{}, {}])

        with pytest.raises(EquivalenceProtocolError, match="after one correction"):
            EquivalenceNormalizer(builder).normalize(root)

        assert scope.skills == original_skills
        assert scope.children == []
        assert builder._llm_calls == 2
        report = json.loads((tmp_path / "equivalence_report.json").read_text(encoding="utf-8"))
        assert report["status"] == "failed"
        assert report["metrics"]["correction_attempts"] == 1
        assert report["metrics"]["protocol_validation_errors"] == 2
        audit_rows = [
            json.loads(line)
            for line in (tmp_path / "equivalence_audit.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        exchanges = [row for row in audit_rows if row.get("event") == "llm_exchange"]
        assert len(exchanges) == 2
        assert "Validation error" in exchanges[1]["prompt"]


def test_unhashable_dimension_value_uses_correction_retry(tmp_path: Path) -> None:
    with dispatch_import_path():
        from indexing.tree.equivalence import EquivalenceNormalizer
        from indexing.tree.schema import Skill, TreeNode

        scope = TreeNode(
            node_id="scope",
            name="Scope",
            skills=[_skill(Skill, "skill-a", "A"), _skill(Skill, "skill-b", "B")],
        )
        root = TreeNode(node_id="root", name="Root", children=[scope])
        invalid = _decision("s000001", "s000002", "not_equivalent")
        invalid["dimensions"]["primary_action"] = ["different"]
        valid = _decision("s000001", "s000002", "not_equivalent")
        builder = _ScriptedBuilder(
            tmp_path / "tree_index.yaml",
            [{"decisions": [invalid]}, {"decisions": [valid]}],
        )

        report = EquivalenceNormalizer(builder).normalize(root)

        assert report["status"] == "complete"
        assert report["metrics"]["correction_attempts"] == 1
        assert report["metrics"]["protocol_validation_errors"] == 1
        assert builder._llm_calls == 2


@pytest.mark.parametrize(
    ("field", "malformed_value"),
    [
        ("common_request", {"text": "shared request"}),
        ("distinguishing_request", ["different request"]),
        ("reason", {"text": "different deliverable"}),
    ],
)
def test_pairwise_text_fields_require_json_strings_and_use_correction_retry(
    tmp_path: Path,
    field: str,
    malformed_value: object,
) -> None:
    with dispatch_import_path():
        from indexing.tree.equivalence import EquivalenceNormalizer
        from indexing.tree.schema import Skill, TreeNode

        scope = TreeNode(
            node_id="scope",
            name="Scope",
            skills=[_skill(Skill, "skill-a", "A"), _skill(Skill, "skill-b", "B")],
        )
        invalid = _decision("s000001", "s000002", "not_equivalent")
        invalid[field] = malformed_value
        valid = _decision("s000001", "s000002", "not_equivalent")
        builder = _ScriptedBuilder(
            tmp_path / field / "tree_index.yaml",
            [{"decisions": [invalid]}, {"decisions": [valid]}],
        )

        report = EquivalenceNormalizer(builder).normalize_scope(scope, ("root", "scope"))

        assert report["run_metrics"]["correction_attempts"] == 1
        assert report["run_metrics"]["protocol_validation_errors"] == 1
        assert builder._llm_calls == 2


@pytest.mark.parametrize(
    ("field", "malformed_value"),
    [
        ("name", ["PDF editing"]),
        ("description", {"text": "Edit PDF files"}),
        ("select_when", True),
        ("dont_select_when", 42),
    ],
)
def test_group_audit_capability_fields_require_json_strings_and_use_correction_retry(
    tmp_path: Path,
    field: str,
    malformed_value: object,
) -> None:
    with dispatch_import_path():
        from indexing.tree.equivalence import EquivalenceNormalizer
        from indexing.tree.schema import Skill, TreeNode

        scope = TreeNode(
            node_id="scope",
            name="Scope",
            skills=[_skill(Skill, "skill-a", "A"), _skill(Skill, "skill-b", "B")],
        )
        invalid_audit = _audit()
        invalid_audit["capability"][field] = malformed_value
        builder = _ScriptedBuilder(
            tmp_path / field / "tree_index.yaml",
            [
                {"decisions": [_decision("s000001", "s000002", "equivalent")]},
                invalid_audit,
                _audit(),
            ],
        )

        report = EquivalenceNormalizer(builder).normalize_scope(scope, ("root", "scope"))

        assert report["run_metrics"]["correction_attempts"] == 1
        assert report["run_metrics"]["protocol_validation_errors"] == 1
        assert builder._llm_calls == 3


def test_prompt_fields_are_length_bounded_and_control_characters_removed(tmp_path: Path) -> None:
    with dispatch_import_path():
        from indexing.tree.equivalence import EquivalenceNormalizer
        from indexing.tree.schema import Skill, TreeNode

        dirty_name = "X" * 5000 + "\x00IGNORE-INSTRUCTIONS"
        scope = TreeNode(
            node_id="scope\x00" + "Y" * 2000,
            name=dirty_name,
            description=dirty_name,
            skills=[
                _skill(Skill, "skill-a", dirty_name),
                _skill(Skill, "skill-b", "Normal Skill"),
            ],
        )
        root = TreeNode(node_id="root", name="Root", children=[scope])
        builder = _ScriptedBuilder(
            tmp_path / "tree_index.yaml",
            [{"decisions": [_decision("s000001", "s000002", "not_equivalent")]}],
        )

        EquivalenceNormalizer(builder).normalize(root)

        assert len(builder.prompts) == 1
        assert "\x00" not in builder.prompts[0]
        assert dirty_name not in builder.prompts[0]
        assert len(builder.prompts[0]) < 12_000


def test_insufficient_evidence_is_a_valid_conservative_singleton_result(tmp_path: Path) -> None:
    with dispatch_import_path():
        from indexing.tree.equivalence import EquivalenceNormalizer
        from indexing.tree.schema import Skill, TreeNode

        scope = TreeNode(
            node_id="scope",
            name="Scope",
            skills=[_skill(Skill, "skill-a", "A"), _skill(Skill, "skill-b", "B")],
        )
        builder = _ScriptedBuilder(
            tmp_path / "tree_index.yaml",
            [{"decisions": [_insufficient_decision("s000001", "s000002")]}],
        )

        report = EquivalenceNormalizer(builder).normalize_scope(scope, ("root", "scope"))

        assert len(scope.children) == 2
        assert all(len(group.skills) == 1 for group in scope.children)
        assert report["pairwise_decisions"][0]["verdict"] == "insufficient_evidence"
        assert builder._llm_calls == 1


def test_large_scope_uses_overlapping_candidate_protocol(tmp_path: Path) -> None:
    with dispatch_import_path():
        from indexing.tree.equivalence import EquivalenceNormalizer
        from indexing.tree.schema import Skill, TreeNode

        scope = TreeNode(
            node_id="presentations",
            name="Presentations",
            skills=[
                _skill(Skill, "skill-a", "A"),
                _skill(Skill, "skill-b", "B"),
                _skill(Skill, "skill-c", "C"),
            ],
        )
        root = TreeNode(node_id="root", name="Root", children=[scope])
        candidates = {
            "candidates": [
                {"anchor": "s000001", "neighbors": ["s000002"]},
                {"anchor": "s000002", "neighbors": ["s000001"]},
                {"anchor": "s000003", "neighbors": []},
            ]
        }
        builder = _ScriptedBuilder(
            tmp_path / "tree_index.yaml",
            [
                candidates,
                {"decisions": [_decision("s000001", "s000002", "equivalent")]},
                _audit(),
            ],
            all_pairs_limit=2,
        )

        report = EquivalenceNormalizer(builder).normalize(root)

        assert report["metrics"]["candidate_pairs"] == 1
        assert "Candidate recall" in builder.prompts[0]
        assert "skill-a" not in "\n".join(builder.prompts)


def test_large_scope_pure_delete_reuses_candidates_pairs_and_audit_without_llm(tmp_path: Path) -> None:
    with dispatch_import_path():
        from indexing.tree.equivalence import EquivalenceNormalizer
        from indexing.tree.schema import Skill, TreeNode

        initial_skills = [
            _skill(Skill, f"skill-{index:02d}", f"Capability {index:02d}")
            for index in range(1, 14)
        ]
        candidate_rows = []
        for index in range(1, 14):
            neighbors = ["s000002"] if index == 1 else ["s000001"] if index == 2 else []
            candidate_rows.append({"anchor": f"s{index:06d}", "neighbors": neighbors})
        initial_builder = _ScriptedBuilder(
            tmp_path / "first" / "tree_index.yaml",
            [
                {"candidates": candidate_rows},
                {"decisions": [_decision("s000001", "s000002", "equivalent")]},
                _audit(),
            ],
            all_pairs_limit=12,
        )
        initial_scope = TreeNode(node_id="scope", name="Scope", skills=initial_skills)
        cached_state = EquivalenceNormalizer(initial_builder).normalize_scope(initial_scope, ("root", "scope"))

        retained_skills = [
            _skill(Skill, f"skill-{index:02d}", f"Capability {index:02d}")
            for index in range(1, 13)
        ]
        delete_builder = _ScriptedBuilder(
            tmp_path / "second" / "tree_index.yaml",
            [],
            all_pairs_limit=12,
        )
        retained_scope = TreeNode(node_id="scope", name="Scope", skills=retained_skills)

        state = EquivalenceNormalizer(delete_builder).normalize_scope(
            retained_scope,
            ("root", "scope"),
            cached_state=cached_state,
        )

        assert delete_builder._llm_calls == 0
        assert len(state["skills"]) == 12
        assert any(
            set(group["member_skill_ids"]) == {"skill-01", "skill-02"}
            for group in state["groups"]
        )


def test_pure_delete_preserves_cached_final_group_boundaries(tmp_path: Path) -> None:
    with dispatch_import_path():
        from indexing.tree.equivalence import EquivalenceNormalizer
        from indexing.tree.schema import Skill, TreeNode

        initial_scope = TreeNode(
            node_id="scope",
            name="Scope",
            skills=[
                _skill(Skill, "skill-a", "A"),
                _skill(Skill, "skill-b", "B"),
                _skill(Skill, "skill-c", "C"),
            ],
        )
        initial_builder = _ScriptedBuilder(
            tmp_path / "initial" / "tree_index.yaml",
            [
                {
                    "decisions": [
                        _decision("s000001", "s000002", "equivalent"),
                        _decision("s000001", "s000003", "equivalent"),
                        _decision("s000002", "s000003", "not_equivalent"),
                    ]
                },
                _audit(),
            ],
        )
        cached_state = EquivalenceNormalizer(initial_builder).normalize_scope(
            initial_scope,
            ("root", "scope"),
        )
        assert {
            frozenset(group["member_skill_ids"]) for group in cached_state["groups"]
        } == {frozenset({"skill-a", "skill-b"}), frozenset({"skill-c"})}

        retained_scope = TreeNode(
            node_id="scope",
            name="Scope",
            skills=[_skill(Skill, "skill-a", "A"), _skill(Skill, "skill-c", "C")],
        )
        delete_builder = _ScriptedBuilder(tmp_path / "delete" / "tree_index.yaml", [])
        state = EquivalenceNormalizer(delete_builder).normalize_scope(
            retained_scope,
            ("root", "scope"),
            cached_state=cached_state,
        )

        assert delete_builder._llm_calls == 0
        assert {
            frozenset(group["member_skill_ids"]) for group in state["groups"]
        } == {frozenset({"skill-a"}), frozenset({"skill-c"})}
        surviving_pair = state["pairwise_decisions"][0]
        assert surviving_pair["verdict"] == "equivalent"
        assert surviving_pair["effective_verdict"] == "not_equivalent"
        assert surviving_pair["effective_rejection_reason"] == "cached_group_boundary"

        repeated_scope = TreeNode(
            node_id="scope",
            name="Scope",
            skills=[_skill(Skill, "skill-a", "A"), _skill(Skill, "skill-c", "C")],
        )
        repeated_builder = _ScriptedBuilder(tmp_path / "repeated" / "tree_index.yaml", [])
        repeated_state = EquivalenceNormalizer(repeated_builder).normalize_scope(
            repeated_scope,
            ("root", "scope"),
            cached_state=state,
        )
        assert repeated_builder._llm_calls == 0
        assert all(len(group["member_skill_ids"]) == 1 for group in repeated_state["groups"])
