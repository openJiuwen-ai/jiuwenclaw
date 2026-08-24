from __future__ import annotations

from types import SimpleNamespace

from jiuwenswarm.symphony.skill_retrieval.dispatch_imports import dispatch_import_path


def test_dynamic_grouping_prompts_do_not_use_fixed_group_count() -> None:
    with dispatch_import_path():
        from indexing.tree.grouping import TreeGroupingEngine

    class FakeBuilder:
        def __init__(self) -> None:
            self.config = SimpleNamespace(branching_factor=999)
            self.prompts: list[str] = []

        def _call_llm_json(self, prompt: str) -> dict:
            self.prompts.append(prompt)
            if "Canonicalization pass" in prompt:
                return {"canonical_groups": {"alpha": {"name": "Alpha", "description": "Alpha group"}}}
            return {"groups": {"alpha": {"name": "Alpha", "description": "Alpha group"}}}

    builder = FakeBuilder()
    engine = TreeGroupingEngine(builder)

    groups = engine.discover_groups(
        [{"id": "skill-a", "name": "Skill A", "description": "A skill."}],
        parent_context={"name": "Parent", "description": "Parent scope."},
    )
    merged = engine.merge_group_definitions([groups, groups])

    expected_group = {
        "alpha": {
            "name": "Alpha",
            "description": "Alpha group",
            "select_when": "",
            "dont_select_when": "",
        }
    }
    assert groups == expected_group
    assert merged == expected_group
    assert len(builder.prompts) == 2
    assert "996" not in builder.prompts[0]
    assert "1001" not in builder.prompts[0]
    assert "996" not in builder.prompts[1]
    assert "1001" not in builder.prompts[1]
    assert "fixed configured count" in builder.prompts[0]
    assert "fixed configured count" in builder.prompts[1]


def test_taxonomy_only_prompts_are_selected_only_when_strict_equivalence_is_enabled() -> None:
    with dispatch_import_path():
        from indexing.tree.grouping import TreeGroupingEngine

    class FakeBuilder:
        def __init__(self, equivalence_enabled: bool) -> None:
            self._equiv_grouping_enabled = equivalence_enabled
            self.prompts: list[str] = []

        def _call_llm_json(self, prompt: str, is_retry: bool = False) -> dict:
            del is_retry
            self.prompts.append(prompt)
            if "Skills awaiting placement" in prompt:
                return {"assignments": {"skill-a": "alpha"}}
            return {"groups": {"alpha": {"name": "Alpha", "description": "Alpha group"}}}

    skills = [{"id": "skill-a", "name": "Skill A", "description": "A skill."}]
    groups = {"alpha": {"name": "Alpha", "description": "Alpha group"}}

    legacy_builder = FakeBuilder(False)
    legacy_engine = TreeGroupingEngine(legacy_builder)
    legacy_engine.discover_groups(skills, parent_context=None)
    legacy_engine.classify_skills_single(skills, groups)
    assert "near substitutes" in legacy_builder.prompts[0]
    assert "near substitute for the other members" in legacy_builder.prompts[1]
    assert "broadest correct home" not in legacy_builder.prompts[1]

    strict_builder = FakeBuilder(True)
    strict_engine = TreeGroupingEngine(strict_builder)
    strict_engine.discover_groups(skills, parent_context=None)
    strict_engine.classify_skills_single(skills, groups)
    assert "smallest set of groups" in strict_builder.prompts[0]
    assert "near substitutes" not in strict_builder.prompts[0]
    assert "broadest correct home" in strict_builder.prompts[1]
    assert "near substitute for the other members" not in strict_builder.prompts[1]
