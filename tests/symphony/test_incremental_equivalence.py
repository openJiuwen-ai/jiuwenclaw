from __future__ import annotations

from copy import deepcopy

from jiuwenswarm.symphony.skill_retrieval.dispatch_imports import dispatch_import_path


def _scope(scope_cid: str, worker_ids: list[str], group_id: str) -> dict:
    return {
        "protocol_hash": "protocol-v1",
        "scope_path": scope_cid.replace(".", "/"),
        "scope_path_parts": scope_cid.split("."),
        "scope_cid": scope_cid,
        "skills": [
            {"skill_id": worker_id, "content_hash": f"hash-{worker_id}"}
            for worker_id in worker_ids
        ],
        "candidate_pairs": [],
        "pairwise_decisions": [],
        "groups": [
            {
                "group_id": group_id,
                "name": group_id,
                "description": f"Group for {', '.join(worker_ids)}",
                "select_when": "",
                "dont_select_when": "",
                "member_skill_ids": list(worker_ids),
                "audit_passed": True,
            }
        ],
    }


def _base_nodes() -> list[dict]:
    return [
        {"cid": "Docs", "type": "branch", "description": "Documents"},
        {"cid": "Docs.Pdf", "type": "branch", "description": "PDF tools"},
        {"cid": "Docs.Pdf.EquivPdf", "type": "branch", "description": "PDF group"},
        {
            "cid": "Docs.Pdf.EquivPdf.pdf-a",
            "type": "leaf",
            "worker_id": "pdf-a",
            "description": "Edit PDF",
        },
        {
            "cid": "Docs.Pdf.EquivPdf.pdf-c",
            "type": "leaf",
            "worker_id": "pdf-c",
            "description": "Modify PDF",
        },
        {"cid": "Docs.Slides", "type": "branch", "description": "Presentation tools"},
        {"cid": "Docs.Slides.EquivSlides", "type": "branch", "description": "Slides group"},
        {
            "cid": "Docs.Slides.EquivSlides.slides-b",
            "type": "leaf",
            "worker_id": "slides-b",
            "description": "Create slides",
        },
    ]


def _skills() -> dict[str, dict]:
    return {
        "pdf-a": {"id": "pdf-a", "name": "PDF A", "description": "Edit PDF"},
        "pdf-c": {"id": "pdf-c", "name": "PDF C", "description": "Modify PDF"},
        "slides-b": {"id": "slides-b", "name": "Slides B", "description": "Create slides"},
    }


def test_incremental_add_routes_and_rebuilds_only_one_terminal_scope() -> None:
    with dispatch_import_path():
        from indexing.workflows.tree_ops import update_equivalence_scopes

    nodes = _base_nodes()
    report = {
        "status": "complete",
        "protocol_hash": "protocol-v1",
        "scopes": [
            _scope("Docs.Pdf", ["pdf-a", "pdf-c"], "equiv-pdf"),
            _scope("Docs.Slides", ["slides-b"], "equiv-slides"),
        ],
    }
    skills = _skills()
    skills["slides-new"] = {
        "id": "slides-new",
        "name": "Slides New",
        "description": "Generate a presentation",
    }
    calls: list[tuple[str, list[str]]] = []

    def normalize(
        scope_cid: str,
        cached_state: dict,
        scope_skills: list[dict],
        remaining_pair_budget: int | None,
    ) -> dict:
        assert remaining_pair_budget is None
        worker_ids = sorted(str(item["id"]) for item in scope_skills)
        calls.append((scope_cid, worker_ids))
        assert cached_state["scope_cid"] == "Docs.Slides"
        return _scope(scope_cid, worker_ids, "equiv-slides-updated")

    result = update_equivalence_scopes(
        nodes=nodes,
        report=report,
        skills_by_id=skills,
        added_worker_ids={"slides-new"},
        removed_worker_ids=set(),
        normalize_scope=normalize,
        route_skill=lambda _skill, _scopes, _nodes: "Docs.Slides",
    )

    assert calls == [("Docs.Slides", ["slides-b", "slides-new"])]
    assert result.affected_scope_cids == ("Docs.Slides",)
    assert [
        node for node in result.nodes if str(node.get("cid") or "").startswith("Docs.Pdf.")
    ] == [node for node in nodes if str(node.get("cid") or "").startswith("Docs.Pdf.")]
    assert {str(node.get("worker_id") or "") for node in result.nodes if node.get("worker_id")} == set(skills)
    assert any(
        node.get("worker_id") == "slides-new"
        and str(node.get("cid") or "").startswith("Docs.Slides.EquivSlidesUpdated.")
        for node in result.nodes
    )
    assert result.report["last_operation"]["affected_scope_cids"] == ["Docs.Slides"]


def test_incremental_delete_reuses_scope_state_and_never_routes() -> None:
    with dispatch_import_path():
        from indexing.workflows.tree_ops import update_equivalence_scopes

    nodes = _base_nodes()
    report = {
        "status": "complete",
        "protocol_hash": "protocol-v1",
        "scopes": [
            _scope("Docs.Pdf", ["pdf-a", "pdf-c"], "equiv-pdf"),
            _scope("Docs.Slides", ["slides-b"], "equiv-slides"),
        ],
    }
    skills = _skills()
    skills.pop("pdf-a")
    cached_states: list[dict] = []

    def normalize(
        scope_cid: str,
        cached_state: dict,
        scope_skills: list[dict],
        remaining_pair_budget: int | None,
    ) -> dict:
        assert remaining_pair_budget is None
        cached_states.append(cached_state)
        return _scope(scope_cid, [str(item["id"]) for item in scope_skills], "equiv-pdf-c")

    def route(*_args) -> str:
        raise AssertionError("delete must not route a Skill")

    result = update_equivalence_scopes(
        nodes=nodes,
        report=report,
        skills_by_id=skills,
        added_worker_ids=set(),
        removed_worker_ids={"pdf-a"},
        normalize_scope=normalize,
        route_skill=route,
    )

    assert len(cached_states) == 1
    assert cached_states[0]["groups"][0]["member_skill_ids"] == ["pdf-a", "pdf-c"]
    assert result.affected_scope_cids == ("Docs.Pdf",)
    assert {str(node.get("worker_id") or "") for node in result.nodes if node.get("worker_id")} == set(skills)
    assert not any(node.get("worker_id") == "pdf-a" for node in result.nodes)
    assert [
        node for node in result.nodes if str(node.get("cid") or "").startswith("Docs.Slides.")
    ] == [node for node in nodes if str(node.get("cid") or "").startswith("Docs.Slides.")]


def test_scope_failure_does_not_mutate_base_nodes_or_report() -> None:
    with dispatch_import_path():
        from indexing.workflows.tree_ops import EquivalenceIncrementalStateError, update_equivalence_scopes

    nodes = _base_nodes()
    report = {
        "status": "complete",
        "protocol_hash": "protocol-v1",
        "scopes": [
            _scope("Docs.Pdf", ["pdf-a", "pdf-c"], "equiv-pdf"),
            _scope("Docs.Slides", ["slides-b"], "equiv-slides"),
        ],
    }
    original_nodes = deepcopy(nodes)
    original_report = deepcopy(report)
    skills = _skills()
    skills["slides-new"] = {"id": "slides-new", "name": "Slides New", "description": "Slides"}

    def fail(*_args) -> dict:
        raise EquivalenceIncrementalStateError("pairwise protocol failed")

    try:
        update_equivalence_scopes(
            nodes=nodes,
            report=report,
            skills_by_id=skills,
            added_worker_ids={"slides-new"},
            removed_worker_ids=set(),
            normalize_scope=fail,
            route_skill=lambda *_args: "Docs.Slides",
        )
    except EquivalenceIncrementalStateError as exc:
        assert "pairwise protocol failed" in str(exc)
    else:
        raise AssertionError("scope normalization failure must abort the incremental update")

    assert nodes == original_nodes
    assert report == original_report


def test_semantic_update_can_move_between_taxonomy_scopes() -> None:
    with dispatch_import_path():
        from indexing.workflows.tree_ops import update_equivalence_scopes
        from indexing.workflows.index_builder import _rebuild_equivalence_catalog

    nodes = _base_nodes()
    report = {
        "status": "complete",
        "protocol_hash": "protocol-v1",
        "scopes": [
            _scope("Docs.Pdf", ["pdf-a", "pdf-c"], "equiv-pdf"),
            _scope("Docs.Slides", ["slides-b"], "equiv-slides"),
        ],
    }
    skills = _skills()
    skills["pdf-a"] = {
        "id": "pdf-a",
        "name": "Presentation Generator",
        "description": "Create slide decks from an outline",
    }
    calls: list[tuple[str, list[str]]] = []

    def normalize(
        scope_cid: str,
        _cached_state: dict,
        scope_skills: list[dict],
        _remaining_pair_budget: int | None,
    ) -> dict:
        worker_ids = sorted(str(item["id"]) for item in scope_skills)
        calls.append((scope_cid, worker_ids))
        return _scope(scope_cid, worker_ids, f"equiv-{scope_cid.rsplit('.', 1)[-1].lower()}-updated")

    result = update_equivalence_scopes(
        nodes=nodes,
        report=report,
        skills_by_id=skills,
        added_worker_ids={"pdf-a"},
        removed_worker_ids=set(),
        normalize_scope=normalize,
        route_skill=lambda skill, _scopes, _nodes: (
            "Docs.Slides" if skill["id"] == "pdf-a" else "Docs.Pdf"
        ),
    )

    assert calls == [
        ("Docs.Pdf", ["pdf-c"]),
        ("Docs.Slides", ["pdf-a", "slides-b"]),
    ]
    assert result.affected_scope_cids == ("Docs.Pdf", "Docs.Slides")
    pdf_a_leaf = next(node for node in result.nodes if node.get("worker_id") == "pdf-a")
    assert str(pdf_a_leaf["cid"]).startswith("Docs.Slides.")
    assert result.report["last_operation"]["moved_workers"] == [
        {
            "worker_id": "pdf-a",
            "from_scope_cid": "Docs.Pdf",
            "to_scope_cid": "Docs.Slides",
        }
    ]

    rebuilt_nodes, catalog_records = _rebuild_equivalence_catalog(
        nodes=result.nodes,
        skills_by_id=skills,
    )
    moved_leaf = next(node for node in rebuilt_nodes if node.get("worker_id") == "pdf-a")
    moved_record = next(record for record in catalog_records if record.worker_id == "pdf-a")
    moved_cid = str(moved_leaf["cid"])
    moved_parent_cid = ".".join(moved_cid.split(".")[:-1])
    assert moved_record.cid == moved_cid
    assert moved_record.category == moved_parent_cid
    assert moved_record.branch_path == tuple(moved_parent_cid.split("."))
    assert moved_cid in moved_record.retrieval_text
    assert any(node.get("cid") == moved_parent_cid for node in rebuilt_nodes)


def test_pairwise_budget_is_accumulated_across_affected_scopes() -> None:
    with dispatch_import_path():
        from indexing.workflows.tree_ops import EquivalenceIncrementalStateError, update_equivalence_scopes

    nodes = _base_nodes()
    report = {
        "status": "complete",
        "protocol_hash": "protocol-v1",
        "scopes": [
            _scope("Docs.Pdf", ["pdf-a", "pdf-c"], "equiv-pdf"),
            _scope("Docs.Slides", ["slides-b"], "equiv-slides"),
        ],
    }
    skills = _skills()
    remaining_budgets: list[int | None] = []

    def normalize(
        scope_cid: str,
        _cached_state: dict,
        scope_skills: list[dict],
        remaining_pair_budget: int | None,
    ) -> dict:
        remaining_budgets.append(remaining_pair_budget)
        state = _scope(scope_cid, sorted(str(item["id"]) for item in scope_skills), f"equiv-{scope_cid}")
        state["candidate_pairs"] = [{"left": "a", "right": "b"}] * 3
        state["pairwise_pair_count"] = 3
        return state

    try:
        update_equivalence_scopes(
            nodes=nodes,
            report=report,
            skills_by_id=skills,
            added_worker_ids={"pdf-a", "slides-b"},
            removed_worker_ids=set(),
            normalize_scope=normalize,
            route_skill=lambda skill, _scopes, _nodes: (
                "Docs.Pdf" if skill["id"].startswith("pdf") else "Docs.Slides"
            ),
            max_pairwise_pairs=5,
        )
    except EquivalenceIncrementalStateError as exc:
        assert "budget exceeded" in str(exc)
    else:
        raise AssertionError("pairwise budget must apply across the whole incremental build")

    assert remaining_budgets == [5, 2]


def test_deleting_last_skill_keeps_empty_taxonomy_scope_for_future_add() -> None:
    with dispatch_import_path():
        from indexing.workflows.tree_ops import update_equivalence_scopes

    nodes = _base_nodes()
    report = {
        "status": "complete",
        "protocol_hash": "protocol-v1",
        "scopes": [
            _scope("Docs.Pdf", ["pdf-a", "pdf-c"], "equiv-pdf"),
            _scope("Docs.Slides", ["slides-b"], "equiv-slides"),
        ],
    }
    remaining_skills = _skills()
    remaining_skills.pop("slides-b")

    deleted = update_equivalence_scopes(
        nodes=nodes,
        report=report,
        skills_by_id=remaining_skills,
        added_worker_ids=set(),
        removed_worker_ids={"slides-b"},
        normalize_scope=lambda *_args: (_ for _ in ()).throw(
            AssertionError("empty scope delete must not invoke the normalizer")
        ),
        route_skill=lambda *_args: (_ for _ in ()).throw(
            AssertionError("delete must not route a Skill")
        ),
    )

    slides_node = next(node for node in deleted.nodes if node.get("cid") == "Docs.Slides")
    assert slides_node["type"] == "branch"
    assert not any(
        str(node.get("cid") or "").startswith("Docs.Slides.") for node in deleted.nodes
    )
    empty_scope = next(
        scope for scope in deleted.report["scopes"] if scope.get("scope_cid") == "Docs.Slides"
    )
    assert empty_scope["skills"] == []
    assert empty_scope["groups"] == []

    skills_after_add = dict(remaining_skills)
    skills_after_add["slides-new"] = {
        "id": "slides-new",
        "name": "Slides New",
        "description": "Create presentation decks",
    }

    def normalize(
        scope_cid: str,
        _cached_state: dict,
        scope_skills: list[dict],
        _remaining_pair_budget: int | None,
    ) -> dict:
        return _scope(scope_cid, [str(item["id"]) for item in scope_skills], "equiv-slides-new")

    added = update_equivalence_scopes(
        nodes=deleted.nodes,
        report=deleted.report,
        skills_by_id=skills_after_add,
        added_worker_ids={"slides-new"},
        removed_worker_ids=set(),
        normalize_scope=normalize,
        route_skill=lambda *_args: "Docs.Slides",
    )

    slides_leaf = next(node for node in added.nodes if node.get("worker_id") == "slides-new")
    assert str(slides_leaf["cid"]).startswith("Docs.Slides.")


def test_branch_description_enrichment_is_idempotent_and_removes_stale_exposure() -> None:
    with dispatch_import_path():
        from indexing.models import CatalogRecord
        from indexing.workflows.tree_ops import enrich_branch_descriptions

    nodes = [
        {"cid": "Docs", "type": "branch", "description": "Document tools"},
        {
            "cid": "Docs.Pdf.pdf-a",
            "type": "leaf",
            "worker_id": "pdf-a",
            "description": "Edit PDF files",
        },
    ]
    records = [
        CatalogRecord(
            worker_id="pdf-a",
            cid="Docs.Pdf.pdf-a",
            name="PDF A",
            description="Edit PDF files",
            skill_path="/skills/pdf-a",
            branch_path=("Docs", "Pdf"),
            category="Docs.Pdf",
            retrieval_text="PDF A\nEdit PDF files",
            metadata={},
        )
    ]

    enriched_once = enrich_branch_descriptions(nodes, catalog_records=records)
    enriched_twice = enrich_branch_descriptions(enriched_once, catalog_records=records)

    assert enriched_twice == enriched_once
    docs_description = next(node for node in enriched_once if node.get("cid") == "Docs")["description"]
    assert str(docs_description).count("Covers 1 descendant skill.") == 1
    assert str(docs_description).count("Representative keywords:") == 1
    assert str(docs_description).count("Representative descendants:") == 1

    without_descendants = enrich_branch_descriptions(enriched_once, catalog_records=[])
    docs_without_descendants = next(
        node for node in without_descendants if node.get("cid") == "Docs"
    )
    assert docs_without_descendants["description"] == "Document tools"
