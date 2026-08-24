from __future__ import annotations

from dataclasses import replace
import json
import threading
from pathlib import Path
from types import SimpleNamespace

from jiuwenswarm.symphony.skill_retrieval.build_coordinator import (
    cancel_skill_index_build,
    start_skill_index_build,
)
from jiuwenswarm.symphony.skill_retrieval import api as skill_retrieval_api
from jiuwenswarm.symphony.skill_retrieval.api import build_skill_index
from jiuwenswarm.symphony.skill_retrieval.config import (
    BuildSettings,
    LLMSettings,
    RetrieveSettings,
    SkillRetrievalSettings,
)
from jiuwenswarm.symphony.skill_retrieval.dispatch_imports import dispatch_import_path
from jiuwenswarm.symphony.skill_retrieval.index_service import (
    SkillIndexService,
    _changed_inventory_paths,
    _write_index_build_metadata,
    _is_complete_index,
    expected_index_fingerprint,
)
from jiuwenswarm.symphony.skill_retrieval.inventory import scan_skill_inventory


def _write_skill(root: Path, dirname: str, *, name: str | None = None, description: str = "desc") -> None:
    skill_dir = root / dirname
    skill_dir.mkdir(parents=True)
    skill_name = name or dirname
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {skill_name}\ndescription: {description}\n---\n\nBody\n",
        encoding="utf-8",
    )


def _write_complete_equivalence_sidecars(
    index_dir: Path,
    settings: SkillRetrievalSettings,
    *,
    worker_id: str,
) -> tuple[str, str]:
    with dispatch_import_path():
        from indexing.tree.equivalence import (
            EQUIVALENCE_PROTOCOL_HASH,
            equivalence_build_complete_event,
            summarize_equivalence_scopes,
        )
        from indexing.workflows.artifacts import resolve_build_config
        from indexing.workflows.index_builder import _equivalence_incremental_signature

        resolved = resolve_build_config(config=SkillIndexService._make_build_config(settings))
        signature = _equivalence_incremental_signature(
            resolved,
            protocol_hash=EQUIVALENCE_PROTOCOL_HASH,
        )
    scope = {
        "protocol_hash": EQUIVALENCE_PROTOCOL_HASH,
        "model": settings.llm.model,
        "scope_path": "root/existing",
        "scope_path_parts": ["root", "existing"],
        "scope_cid": "Existing",
        "skill_hashes": {worker_id: "content-hash"},
        "skills": [{"skill_id": worker_id, "content_hash": "content-hash"}],
        "candidate_pairs": [],
        "pairwise_pair_count": 0,
        "pairwise_decisions": [],
        "audit_rejected_pairs": [],
        "groups": [
            {
                "group_id": "equiv-0000000000000001",
                "name": "Existing capability",
                "description": "Existing capability.",
                "select_when": "Use for the existing capability.",
                "dont_select_when": "Do not use for unrelated requests.",
                "member_skill_ids": [worker_id],
                "audit_passed": True,
            }
        ],
    }
    report = {
        "status": "complete",
        "protocol_version": "terminal-skill-equivalence-v1",
        "protocol_hash": EQUIVALENCE_PROTOCOL_HASH,
        "incremental_signature": signature,
        "model": settings.llm.model,
        "scopes": [scope],
        "metrics": {},
    }
    report.update(
        summarize_equivalence_scopes(
            [scope],
            status="complete",
            expected_input_count=1,
        )
    )
    report_text = json.dumps(report, ensure_ascii=False, sort_keys=True) + "\n"
    audit_text = "".join(
        json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
        for event in (
            {"event": "skill_aliases", "protocol_hash": EQUIVALENCE_PROTOCOL_HASH},
            equivalence_build_complete_event(report),
        )
    )
    (index_dir / "equivalence_report.json").write_text(report_text, encoding="utf-8")
    (index_dir / "equivalence_audit.jsonl").write_text(audit_text, encoding="utf-8")
    return report_text, audit_text


class _InventoryManager:
    def __init__(self, skills_dir: Path) -> None:
        self._skills_dir = skills_dir

    @staticmethod
    def get_local_skills() -> list[dict]:
        return []

    @staticmethod
    def get_installed_plugins() -> list[dict]:
        return [
            {"name": "disabled-plugin", "enabled": False, "skills": ["disabled-plugin"]},
            {"name": "enabled-plugin", "enabled": True, "skills": ["enabled-plugin"]},
        ]

    @staticmethod
    def get_skill_enabled(name: str) -> bool:
        return name != "disabled-skill"


def test_scan_skill_inventory_includes_all_installed_skills(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    _write_skill(skills_dir, "disabled-plugin")
    _write_skill(skills_dir, "disabled-skill")
    _write_skill(skills_dir, "enabled-plugin")

    inventory = scan_skill_inventory(_InventoryManager(skills_dir))

    assert [item.name for item in inventory.items] == [
        "disabled-plugin",
        "disabled-skill",
        "enabled-plugin",
    ]


def test_index_fingerprint_tracks_semantic_build_inputs_without_secrets(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    _write_skill(skills_dir, "enabled-skill")
    inventory = scan_skill_inventory(SimpleNamespace(_skills_dir=skills_dir))
    settings = SkillRetrievalSettings(
        enabled=True,
        artifact_root=tmp_path / "artifact",
        llm=LLMSettings(model="model-a", api_key="key-a", base_url="https://api-a.example"),
        build=BuildSettings(max_depth=4),
        retrieve=RetrieveSettings(),
    )

    changed_llm = replace(
        settings,
        llm=LLMSettings(model="model-b", api_key="key-b", base_url="https://api-b.example", seed=123),
    )
    changed_build = replace(settings, build=BuildSettings(max_depth=5))
    changed_secret = replace(
        settings,
        llm=LLMSettings(model="model-a", api_key="rotated-key", base_url="https://api-a.example"),
    )
    changed_timeout = replace(settings, build=BuildSettings(max_depth=4, request_timeout_seconds=999))
    _write_skill(skills_dir, "another-skill")
    changed_inventory = scan_skill_inventory(SimpleNamespace(_skills_dir=skills_dir))

    assert expected_index_fingerprint(inventory, changed_llm) != expected_index_fingerprint(inventory, settings)
    assert expected_index_fingerprint(inventory, changed_build) != expected_index_fingerprint(inventory, settings)
    assert expected_index_fingerprint(inventory, changed_secret) == expected_index_fingerprint(inventory, settings)
    assert expected_index_fingerprint(inventory, changed_timeout) == expected_index_fingerprint(inventory, settings)
    assert expected_index_fingerprint(changed_inventory, settings) != expected_index_fingerprint(inventory, settings)


def test_index_fingerprint_tracks_root_taxonomy_file_content(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    _write_skill(skills_dir, "enabled-skill")
    inventory = scan_skill_inventory(SimpleNamespace(_skills_dir=skills_dir))
    taxonomy_path = tmp_path / "taxonomy.yaml"
    taxonomy_path.write_text("tree_root_categories:\n  - id: docs\n    name: Docs\n", encoding="utf-8")
    settings = SkillRetrievalSettings(
        enabled=True,
        artifact_root=tmp_path / "artifact",
        llm=LLMSettings(model="model", api_key="key", base_url="https://api.example"),
        build=BuildSettings(root_categories=str(taxonomy_path)),
        retrieve=RetrieveSettings(),
    )

    before = expected_index_fingerprint(inventory, settings)
    taxonomy_path.write_text("tree_root_categories:\n  - id: search\n    name: Search\n", encoding="utf-8")

    assert expected_index_fingerprint(inventory, settings) != before


def test_complete_index_requires_equivalence_sidecars_when_enabled(tmp_path: Path) -> None:
    for filename in ("tree_index.yaml", "catalog.jsonl", "manifest.json"):
        (tmp_path / filename).write_text("{}\n", encoding="utf-8")

    assert _is_complete_index(tmp_path)
    assert not _is_complete_index(tmp_path, equivalence_enabled=True)

    (tmp_path / "equivalence_audit.jsonl").write_text("{}\n", encoding="utf-8")
    (tmp_path / "equivalence_report.json").write_text(
        '{"status": "complete", "protocol_hash": "test-protocol"}\n',
        encoding="utf-8",
    )
    assert _is_complete_index(tmp_path, equivalence_enabled=True)


def test_complete_index_rejects_incompatible_report_and_corrupt_audit(tmp_path: Path) -> None:
    settings = SkillRetrievalSettings(
        enabled=True,
        artifact_root=tmp_path.parent / "artifact",
        llm=LLMSettings(model="model", api_key="key", base_url="https://api.example"),
        build=BuildSettings(equivalence_enabled=True),
        retrieve=RetrieveSettings(),
    )
    for filename in ("tree_index.yaml", "catalog.jsonl", "manifest.json"):
        (tmp_path / filename).write_text("{}\n", encoding="utf-8")
    report_text, _ = _write_complete_equivalence_sidecars(
        tmp_path,
        settings,
        worker_id="existing-skill",
    )

    assert _is_complete_index(tmp_path, settings=settings)

    report = json.loads(report_text)
    report["protocol_hash"] = "stale-protocol"
    (tmp_path / "equivalence_report.json").write_text(json.dumps(report), encoding="utf-8")
    assert not _is_complete_index(tmp_path, settings=settings)

    (tmp_path / "equivalence_report.json").write_text(report_text, encoding="utf-8")
    (tmp_path / "equivalence_audit.jsonl").write_text("not-json\n", encoding="utf-8")
    assert not _is_complete_index(tmp_path, settings=settings)


def test_changed_inventory_paths_detects_in_place_skill_update(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    _write_skill(skills_dir, "updated-skill", description="before")
    manager = SimpleNamespace(_skills_dir=skills_dir)
    before = scan_skill_inventory(manager)
    previous_state = {"inventory": before.to_state_payload()}

    skill_file = skills_dir / "updated-skill" / "SKILL.md"
    skill_file.write_text(
        "---\nname: updated-skill\ndescription: after\n---\n\nChanged body\n",
        encoding="utf-8",
    )
    after = scan_skill_inventory(manager)

    assert _changed_inventory_paths(previous_state, before) == set()
    assert _changed_inventory_paths(previous_state, after) == {
        str((skills_dir / "updated-skill").resolve())
    }


def test_recovery_rejects_same_path_backup_built_from_old_skill_content(
    tmp_path: Path,
) -> None:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    _write_skill(skills_dir, "updated-skill", description="before")
    manager = SimpleNamespace(_skills_dir=skills_dir)
    before = scan_skill_inventory(manager)
    artifact_root = tmp_path / "artifact"
    backup_dir = artifact_root / "index.backup-1"
    backup_dir.mkdir(parents=True)
    settings = SkillRetrievalSettings(
        enabled=True,
        artifact_root=artifact_root,
        llm=LLMSettings(model="model", api_key="key", base_url="https://api.example"),
        build=BuildSettings(),
        retrieve=RetrieveSettings(),
    )
    (backup_dir / "tree_index.yaml").write_text("nodes: []\n", encoding="utf-8")
    (backup_dir / "catalog.jsonl").write_text("", encoding="utf-8")
    (backup_dir / "manifest.json").write_text(
        json.dumps({"item_paths": before.item_paths}),
        encoding="utf-8",
    )
    _write_index_build_metadata(
        backup_dir,
        fingerprint=expected_index_fingerprint(before, settings),
        inventory=before,
    )

    (skills_dir / "updated-skill" / "SKILL.md").write_text(
        "---\nname: updated-skill\ndescription: after\n---\n\nChanged body\n",
        encoding="utf-8",
    )
    after = scan_skill_inventory(manager)

    recovered = SkillIndexService(manager)._recover_index(
        settings=settings,
        inventory=after,
        expected_fingerprint=expected_index_fingerprint(after, settings),
    )

    assert recovered is False
    assert not (artifact_root / "index").exists()
    assert backup_dir.exists()


def test_failed_equivalence_incremental_build_preserves_last_complete_index(
    monkeypatch,
    tmp_path: Path,
) -> None:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    _write_skill(skills_dir, "existing-skill")
    manager = SimpleNamespace(_skills_dir=skills_dir)
    previous_inventory = scan_skill_inventory(manager)
    artifact_root = tmp_path / "artifact"
    index_dir = artifact_root / "index"
    index_dir.mkdir(parents=True)
    settings = SkillRetrievalSettings(
        enabled=True,
        artifact_root=artifact_root,
        llm=LLMSettings(model="model", api_key="key", base_url="https://api.example"),
        build=BuildSettings(equivalence_enabled=True),
        retrieve=RetrieveSettings(),
    )
    original_tree = "nodes:\n  - cid: Existing\n    type: branch\n"
    (index_dir / "tree_index.yaml").write_text(original_tree, encoding="utf-8")
    (index_dir / "catalog.jsonl").write_text("", encoding="utf-8")
    (index_dir / "manifest.json").write_text(
        json.dumps({"item_paths": previous_inventory.item_paths}),
        encoding="utf-8",
    )
    original_report, original_audit = _write_complete_equivalence_sidecars(
        index_dir,
        settings,
        worker_id="existing-skill",
    )
    (artifact_root / "state.json").write_text(
        json.dumps(
            {
                "fingerprint": expected_index_fingerprint(previous_inventory, settings),
                "inventory": previous_inventory.to_state_payload(),
                "indexed_count": previous_inventory.count,
            }
        ),
        encoding="utf-8",
    )
    skill_file = skills_dir / "existing-skill" / "SKILL.md"
    skill_file.write_text(
        "---\nname: existing-skill\ndescription: updated\n---\n\nChanged body\n",
        encoding="utf-8",
    )
    current_inventory = scan_skill_inventory(manager)

    monkeypatch.setattr(
        "jiuwenswarm.symphony.skill_retrieval.index_service.load_settings",
        lambda: settings,
    )
    monkeypatch.setattr(
        SkillIndexService,
        "_check_build_llm_access",
        staticmethod(lambda settings: None),
    )

    def fail_incremental_build(self, **kwargs):
        output_dir = Path(kwargs["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "equivalence_audit.jsonl").write_text(
            '{"event":"llm_exchange","validation_error":"bad pairwise payload"}\n',
            encoding="utf-8",
        )
        (output_dir / "equivalence_report.json").write_text(
            '{"status":"failed","error":"pairwise protocol failed"}\n',
            encoding="utf-8",
        )
        raise RuntimeError("pairwise protocol failed")

    monkeypatch.setattr(SkillIndexService, "incremental_build", fail_incremental_build)

    result = SkillIndexService(manager).build_index(force=False)

    assert result["success"] is False
    assert "pairwise protocol failed" in result["result"]
    assert (index_dir / "tree_index.yaml").read_text(encoding="utf-8") == original_tree
    assert (index_dir / "equivalence_audit.jsonl").read_text(encoding="utf-8") == original_audit
    assert (index_dir / "equivalence_report.json").read_text(encoding="utf-8") == original_report
    failed_state = json.loads((artifact_root / "state.json").read_text(encoding="utf-8"))
    assert failed_state["inventory"] == previous_inventory.to_state_payload()
    assert _changed_inventory_paths(failed_state, current_inventory) == {
        str((skills_dir / "existing-skill").resolve())
    }
    diagnostics_dir = Path(failed_state["build"]["diagnostics_dir"])
    assert diagnostics_dir.is_dir()
    assert "bad pairwise payload" in (
        diagnostics_dir / "index" / "equivalence_audit.jsonl"
    ).read_text(encoding="utf-8")
    assert SkillIndexService(manager).status()["build_diagnostics_dir"] == str(diagnostics_dir)


def test_status_and_tree_require_rebuild_when_build_llm_changes(monkeypatch, tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    _write_skill(skills_dir, "enabled-skill")
    manager = SimpleNamespace(_skills_dir=skills_dir)
    inventory = scan_skill_inventory(manager)
    artifact_root = tmp_path / "artifact"
    index_dir = artifact_root / "index"
    index_dir.mkdir(parents=True)
    settings = SkillRetrievalSettings(
        enabled=True,
        artifact_root=artifact_root,
        llm=LLMSettings(model="model-a", api_key="key-a", base_url="https://api-a.example"),
        build=BuildSettings(max_depth=4),
        retrieve=RetrieveSettings(),
    )
    changed_llm = replace(
        settings,
        llm=LLMSettings(model="model-b", api_key="key-b", base_url="https://api-b.example"),
    )
    (index_dir / "tree_index.yaml").write_text("nodes: []\n", encoding="utf-8")
    (index_dir / "catalog.jsonl").write_text("", encoding="utf-8")
    (index_dir / "manifest.json").write_text(
        json.dumps({"item_paths": inventory.item_paths}),
        encoding="utf-8",
    )
    (artifact_root / "state.json").write_text(
        json.dumps(
            {
                "fingerprint": expected_index_fingerprint(inventory, settings),
                "indexed_count": inventory.count,
                "build": {"status": "success", "stage": "success", "progress": 1.0},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "jiuwenswarm.symphony.skill_retrieval.index_service.load_settings",
        lambda: changed_llm,
    )

    status = SkillIndexService(manager).status()
    tree = SkillIndexService(manager).tree(language="zh")

    assert status["index_exists"] is True
    assert status["fresh"] is False
    assert status["build_status"] == "idle"
    assert tree["success"] is False
    assert tree["index_dir"] == str(index_dir)


def test_tree_disabled_message_uses_language_without_markdown_heading(monkeypatch, tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    settings = SkillRetrievalSettings(
        enabled=False,
        artifact_root=tmp_path / "artifact",
        llm=LLMSettings(model="", api_key="", base_url=""),
        build=BuildSettings(),
        retrieve=RetrieveSettings(),
    )
    monkeypatch.setattr(
        "jiuwenswarm.symphony.skill_retrieval.index_service.load_settings",
        lambda: settings,
    )

    zh = SkillIndexService(SimpleNamespace(_skills_dir=skills_dir)).tree(language="zh")
    en = SkillIndexService(SimpleNamespace(_skills_dir=skills_dir)).tree(language="en")

    assert zh["success"] is False
    assert "技能检索当前已关闭" in zh["result"]
    assert not zh["result"].lstrip().startswith("#")
    assert "Skill retrieval is currently disabled" in en["result"]
    assert not en["result"].lstrip().startswith("#")


def test_build_index_with_no_skills_clears_stale_index_and_records_failure(monkeypatch, tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    artifact_root = tmp_path / "artifact"
    index_dir = artifact_root / "index"
    index_dir.mkdir(parents=True)
    (index_dir / "tree_index.yaml").write_text("nodes: []\n", encoding="utf-8")
    (index_dir / "catalog.jsonl").write_text("", encoding="utf-8")
    (index_dir / "manifest.json").write_text(json.dumps({"item_paths": ["/old/skill"]}), encoding="utf-8")
    (artifact_root / "state.json").write_text(
        json.dumps({"fingerprint": "old", "indexed_count": 1}),
        encoding="utf-8",
    )
    settings = SkillRetrievalSettings(
        enabled=True,
        artifact_root=artifact_root,
        llm=LLMSettings(model="", api_key="", base_url=""),
        build=BuildSettings(),
        retrieve=RetrieveSettings(),
    )
    monkeypatch.setattr(
        "jiuwenswarm.symphony.skill_retrieval.index_service.load_settings",
        lambda: settings,
    )

    result = SkillIndexService(SimpleNamespace(_skills_dir=skills_dir)).build_index(force=True)

    assert result["success"] is False
    assert not index_dir.exists()
    state = json.loads((artifact_root / "state.json").read_text(encoding="utf-8"))
    assert state["build"]["status"] == "failed"
    assert "No installed skills" in state["build"]["error"]


def test_cancel_without_running_build_does_not_write_cancel_state(monkeypatch, tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifact"
    settings = SkillRetrievalSettings(
        enabled=True,
        artifact_root=artifact_root,
        llm=LLMSettings(model="model", api_key="key", base_url=""),
        build=BuildSettings(),
        retrieve=RetrieveSettings(),
    )
    monkeypatch.setattr(
        "jiuwenswarm.symphony.skill_retrieval.build_coordinator.load_settings",
        lambda: settings,
    )

    result = cancel_skill_index_build(SimpleNamespace(_skills_dir=tmp_path / "skills"))

    assert result["success"] is False
    assert result["build_status"] == "idle"
    assert not (artifact_root / "state.json").exists()


def test_background_build_marks_shared_state(monkeypatch, tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifact"
    settings = SkillRetrievalSettings(
        enabled=True,
        artifact_root=artifact_root,
        llm=LLMSettings(model="model", api_key="key", base_url=""),
        build=BuildSettings(),
        retrieve=RetrieveSettings(),
    )
    monkeypatch.setattr(
        "jiuwenswarm.symphony.skill_retrieval.build_coordinator.load_settings",
        lambda: settings,
    )
    monkeypatch.setattr(
        "jiuwenswarm.symphony.skill_retrieval.index_service.load_settings",
        lambda: settings,
    )
    release = threading.Event()
    started = threading.Event()

    def fake_build_index(self, *, force=False, cancel_check=None, source="manual"):
        started.set()
        release.wait(timeout=1)
        return {"success": True, "result": "# ok"}

    monkeypatch.setattr(SkillIndexService, "build_index", fake_build_index)
    manager = SimpleNamespace(_skills_dir=tmp_path / "skills")

    result = start_skill_index_build(manager, force=True, source="web")
    assert started.wait(timeout=1)

    assert result["success"] is True
    assert result["background"] is True
    state = json.loads((artifact_root / "state.json").read_text(encoding="utf-8"))
    assert state["build"]["status"] == "running"
    assert state["build"]["stage"] == "queued"

    cancel_result = cancel_skill_index_build(manager)
    release.set()
    assert cancel_result["success"] is True
    assert cancel_result["build_status"] == "cancelled"
    state = json.loads((artifact_root / "state.json").read_text(encoding="utf-8"))
    assert state["build"]["status"] == "cancelled"
    assert state["build"]["stage"] == "cancelled"
    assert state["build"]["cancel_requested"] is False


def test_force_build_bypasses_fresh_index_reuse(monkeypatch, tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    _write_skill(skills_dir, "enabled-skill")
    manager = SimpleNamespace(_skills_dir=skills_dir)
    artifact_root = tmp_path / "artifact"
    settings = SkillRetrievalSettings(
        enabled=True,
        artifact_root=artifact_root,
        llm=LLMSettings(model="model", api_key="key", base_url=""),
        build=BuildSettings(),
        retrieve=RetrieveSettings(),
    )
    inventory = scan_skill_inventory(manager)
    expected = expected_index_fingerprint(inventory, settings)
    index_dir = artifact_root / "index"
    index_dir.mkdir(parents=True)
    (index_dir / "tree_index.yaml").write_text("nodes: []\n", encoding="utf-8")
    (index_dir / "catalog.jsonl").write_text("", encoding="utf-8")
    (index_dir / "manifest.json").write_text(
        json.dumps({"item_paths": inventory.item_paths}),
        encoding="utf-8",
    )
    (artifact_root / "state.json").write_text(
        json.dumps({"fingerprint": expected, "indexed_count": inventory.count}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "jiuwenswarm.symphony.skill_retrieval.index_service.load_settings",
        lambda: settings,
    )
    monkeypatch.setattr(SkillIndexService, "_check_build_llm_access", staticmethod(lambda settings: None))
    calls: list[str] = []

    def fake_run_dispatch_build(*, settings, inventory, output_dir):
        calls.append("build")
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "tree_index.yaml").write_text("nodes: []\n", encoding="utf-8")
        (output_dir / "catalog.jsonl").write_text("", encoding="utf-8")
        (output_dir / "manifest.json").write_text(
            json.dumps({"item_paths": inventory.item_paths}),
            encoding="utf-8",
        )

    monkeypatch.setattr(SkillIndexService, "_run_dispatch_build", staticmethod(fake_run_dispatch_build))

    result = SkillIndexService(manager).build_index(force=True)

    assert result["success"] is True
    assert calls == ["build"]
    state = json.loads((artifact_root / "state.json").read_text(encoding="utf-8"))
    assert state["build"]["status"] == "success"


def test_missing_llm_config_records_failure(monkeypatch, tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    _write_skill(skills_dir, "enabled-skill")
    artifact_root = tmp_path / "artifact"
    settings = SkillRetrievalSettings(
        enabled=True,
        artifact_root=artifact_root,
        llm=LLMSettings(model="", api_key="", base_url=""),
        build=BuildSettings(),
        retrieve=RetrieveSettings(),
    )
    monkeypatch.setattr(
        "jiuwenswarm.symphony.skill_retrieval.index_service.load_settings",
        lambda: settings,
    )

    result = SkillIndexService(SimpleNamespace(_skills_dir=skills_dir)).build_index(force=True)

    assert result["success"] is False
    assert "requires a model and API key" in result["result"]
    state = json.loads((artifact_root / "state.json").read_text(encoding="utf-8"))
    assert state["build"]["status"] == "failed"
    assert state["build"]["stage"] == "llm_config"


def test_build_fails_when_llm_access_check_fails(monkeypatch, tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    _write_skill(skills_dir, "enabled-skill")
    artifact_root = tmp_path / "artifact"
    settings = SkillRetrievalSettings(
        enabled=True,
        artifact_root=artifact_root,
        llm=LLMSettings(model="model", api_key="bad-key", base_url="https://example.invalid"),
        build=BuildSettings(),
        retrieve=RetrieveSettings(),
    )
    monkeypatch.setattr(
        "jiuwenswarm.symphony.skill_retrieval.index_service.load_settings",
        lambda: settings,
    )

    def fail_llm_access(settings):
        raise RuntimeError("Skill index build model is not reachable or rejected the request: unauthorized")

    monkeypatch.setattr(SkillIndexService, "_check_build_llm_access", staticmethod(fail_llm_access))

    result = SkillIndexService(SimpleNamespace(_skills_dir=skills_dir)).build_index(force=True)

    assert result["success"] is False
    assert "not reachable" in result["result"]
    assert not (artifact_root / "index").exists()
    state = json.loads((artifact_root / "state.json").read_text(encoding="utf-8"))
    assert state["build"]["status"] == "failed"
    assert state["build"]["stage"] == "llm_check"
    assert "not reachable" in state["build"]["error"]


def test_llm_access_check_uses_tree_builder_runtime_imports(monkeypatch, tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    _write_skill(skills_dir, "enabled-skill")
    manager = SimpleNamespace(_skills_dir=skills_dir)
    settings = SkillRetrievalSettings(
        enabled=True,
        artifact_root=tmp_path / "artifact",
        llm=LLMSettings(model="model", api_key="key", base_url="https://example.invalid"),
        build=BuildSettings(),
        retrieve=RetrieveSettings(),
    )
    monkeypatch.setattr(
        "jiuwenswarm.symphony.skill_retrieval.index_service.load_settings",
        lambda: settings,
    )

    with dispatch_import_path():
        from indexing.tree.llm_runtime import TreeLLMRuntime
        from indexing.workflows.index_builder import IndexBuilder

        def fake_build(*, item_paths, output_dir, item_type, config):
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "tree_index.yaml").write_text("nodes: []\n", encoding="utf-8")
            (output_dir / "catalog.jsonl").write_text("", encoding="utf-8")
            (output_dir / "manifest.json").write_text(
                json.dumps({"item_paths": list(item_paths)}),
                encoding="utf-8",
            )

        monkeypatch.setattr(TreeLLMRuntime, "call_llm_json", lambda self, prompt, max_retries=3: {"ok": True})
        monkeypatch.setattr(IndexBuilder, "build", staticmethod(fake_build))

    result = SkillIndexService(manager).build_index(force=True)

    assert result["success"] is True


def test_status_ignores_success_state_when_index_artifacts_are_deleted(monkeypatch, tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    _write_skill(skills_dir, "enabled-skill")
    artifact_root = tmp_path / "artifact"
    settings = SkillRetrievalSettings(
        enabled=True,
        artifact_root=artifact_root,
        llm=LLMSettings(model="model", api_key="key", base_url=""),
        build=BuildSettings(),
        retrieve=RetrieveSettings(),
    )
    inventory = scan_skill_inventory(SimpleNamespace(_skills_dir=skills_dir))
    artifact_root.mkdir(parents=True)
    (artifact_root / "state.json").write_text(
        json.dumps(
            {
                "fingerprint": expected_index_fingerprint(inventory, settings),
                "indexed_count": inventory.count,
                "build": {"status": "success", "stage": "success", "progress": 1.0},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "jiuwenswarm.symphony.skill_retrieval.index_service.load_settings",
        lambda: settings,
    )

    status = SkillIndexService(SimpleNamespace(_skills_dir=skills_dir)).status()
    tree = SkillIndexService(SimpleNamespace(_skills_dir=skills_dir)).tree(language="zh")

    assert status["index_exists"] is False
    assert status["fresh"] is False
    assert status["build_status"] == "idle"
    assert status["build_logs"] == []
    assert "No usable skill index" in status["build_message"]
    assert tree["success"] is False
    assert tree["nodes"] == []


def test_api_status_repairs_interrupted_running_state(monkeypatch, tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    artifact_root = tmp_path / "artifact"
    artifact_root.mkdir()
    (artifact_root / "state.json").write_text(
        json.dumps({"build": {"status": "running", "stage": "build", "progress": 0.5}}),
        encoding="utf-8",
    )
    settings = SkillRetrievalSettings(
        enabled=True,
        artifact_root=artifact_root,
        llm=LLMSettings(model="model", api_key="key", base_url=""),
        build=BuildSettings(),
        retrieve=RetrieveSettings(),
    )
    monkeypatch.setattr(skill_retrieval_api, "_STARTUP_REPAIR_DONE", False)
    monkeypatch.setattr(
        "jiuwenswarm.symphony.skill_retrieval.build_coordinator.load_settings",
        lambda: settings,
    )
    monkeypatch.setattr(
        "jiuwenswarm.symphony.skill_retrieval.index_service.load_settings",
        lambda: settings,
    )

    status = skill_retrieval_api.get_skill_retrieval_status(SimpleNamespace(_skills_dir=skills_dir))

    assert status["build_status"] == "failed"
    assert status["build_stage"] == "interrupted"
    assert "interrupted" in status["build_error"]


def test_api_status_keeps_active_build_running(monkeypatch, tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifact"
    settings = SkillRetrievalSettings(
        enabled=True,
        artifact_root=artifact_root,
        llm=LLMSettings(model="model", api_key="key", base_url=""),
        build=BuildSettings(),
        retrieve=RetrieveSettings(),
    )
    monkeypatch.setattr(skill_retrieval_api, "_STARTUP_REPAIR_DONE", False)
    monkeypatch.setattr(
        "jiuwenswarm.symphony.skill_retrieval.build_coordinator.load_settings",
        lambda: settings,
    )
    monkeypatch.setattr(
        "jiuwenswarm.symphony.skill_retrieval.index_service.load_settings",
        lambda: settings,
    )
    release = threading.Event()
    started = threading.Event()
    manager = SimpleNamespace(_skills_dir=tmp_path / "skills")

    def fake_build_index(self, *, force=False, cancel_check=None, source="manual"):
        started.set()
        release.wait(timeout=1)
        return {"success": True, "result": "# ok"}

    monkeypatch.setattr(SkillIndexService, "build_index", fake_build_index)

    start_skill_index_build(manager, force=True, source="web")
    assert started.wait(timeout=1)
    status = skill_retrieval_api.get_skill_retrieval_status(manager)
    release.set()

    assert status["build_status"] == "running"
    assert status["build_stage"] == "queued"


def test_build_skill_index_api_waits_for_shared_background_build(monkeypatch, tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifact"
    settings = SkillRetrievalSettings(
        enabled=True,
        artifact_root=artifact_root,
        llm=LLMSettings(model="model", api_key="key", base_url=""),
        build=BuildSettings(),
        retrieve=RetrieveSettings(),
    )
    monkeypatch.setattr(
        "jiuwenswarm.symphony.skill_retrieval.build_coordinator.load_settings",
        lambda: settings,
    )
    monkeypatch.setattr(
        "jiuwenswarm.symphony.skill_retrieval.index_service.load_settings",
        lambda: settings,
    )
    calls: list[tuple[bool, str]] = []
    started = threading.Event()
    release = threading.Event()
    manager = SimpleNamespace(_skills_dir=tmp_path / "skills")

    def fake_build_index(self, *, force=False, cancel_check=None, source="tool"):
        calls.append((force, source))
        started.set()
        release.wait(timeout=1)
        return {"success": True, "result": "# Skill Retrieval Index\n\nDone."}

    monkeypatch.setattr(SkillIndexService, "build_index", fake_build_index)
    monkeypatch.setattr(
        SkillIndexService,
        "status",
        lambda self: {"build_status": "success", "index_exists": True, "fresh": True},
    )

    web_result = start_skill_index_build(manager, force=True, source="web")
    assert started.wait(timeout=1)
    state = json.loads((artifact_root / "state.json").read_text(encoding="utf-8"))
    assert web_result["build_status"] == "running"
    assert state["build"]["status"] == "running"

    result_box: list[dict] = []
    tool_thread = threading.Thread(
        target=lambda: result_box.append(build_skill_index(manager, force=True, source="tool"))
    )
    tool_thread.start()
    release.set()
    tool_thread.join(timeout=1)

    assert result_box == [
        {
            "success": True,
            "result": (
                "# Skill Index Build\n\n"
                "Skill index build completed. You can now call `skill_branch_explore` "
                "or `skill_branch_peek` to inspect installed skills."
            ),
        }
    ]
    assert calls == [(True, "web")]


def test_tree_rejects_stale_manifest_and_uses_requested_language(monkeypatch, tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    _write_skill(skills_dir, "current-skill")
    artifact_root = tmp_path / "artifact"
    index_dir = artifact_root / "index"
    index_dir.mkdir(parents=True)
    (index_dir / "tree_index.yaml").write_text(
        "nodes:\n"
        "  - cid: old\n"
        "    type: leaf\n"
        "    worker_id: old-skill\n",
        encoding="utf-8",
    )
    (index_dir / "catalog.jsonl").write_text("", encoding="utf-8")
    (index_dir / "manifest.json").write_text(json.dumps({"item_paths": ["/old/skill"]}), encoding="utf-8")
    (artifact_root / "state.json").write_text(json.dumps({"fingerprint": "old"}), encoding="utf-8")
    settings = SkillRetrievalSettings(
        enabled=True,
        artifact_root=artifact_root,
        llm=LLMSettings(model="model", api_key="key", base_url=""),
        build=BuildSettings(),
        retrieve=RetrieveSettings(),
    )
    monkeypatch.setattr(
        "jiuwenswarm.symphony.skill_retrieval.index_service.load_settings",
        lambda: settings,
    )

    zh = SkillIndexService(SimpleNamespace(_skills_dir=skills_dir)).tree(language="zh")
    en = SkillIndexService(SimpleNamespace(_skills_dir=skills_dir)).tree(language="en")

    assert zh["success"] is False
    assert zh["nodes"] == []
    assert "# 技能索引树" not in zh["result"]
    assert "当前没有可用" in zh["result"]
    assert en["success"] is False
    assert en["nodes"] == []
    assert "# Skill Index Tree" not in en["result"]
    assert "No usable" in en["result"]


def test_build_error_normalizes_non_streaming_remote_model_error(monkeypatch, tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    _write_skill(skills_dir, "enabled-skill")
    artifact_root = tmp_path / "artifact"
    settings = SkillRetrievalSettings(
        enabled=True,
        artifact_root=artifact_root,
        llm=LLMSettings(model="model", api_key="key", base_url=""),
        build=BuildSettings(),
        retrieve=RetrieveSettings(),
    )
    monkeypatch.setattr(
        "jiuwenswarm.symphony.skill_retrieval.index_service.load_settings",
        lambda: settings,
    )
    monkeypatch.setattr(SkillIndexService, "_check_build_llm_access", staticmethod(lambda settings: None))

    def raise_remote_error(*, settings, inventory, output_dir):
        raise RuntimeError("set to false for non-streaming calls")

    monkeypatch.setattr(SkillIndexService, "_run_dispatch_build", staticmethod(raise_remote_error))

    result = SkillIndexService(SimpleNamespace(_skills_dir=skills_dir)).build_index(force=True)

    assert result["success"] is False
    assert "non-streaming LLM calls" in result["result"]
    state = json.loads((artifact_root / "state.json").read_text(encoding="utf-8"))
    assert "non-streaming LLM calls" in state["build"]["error"]
