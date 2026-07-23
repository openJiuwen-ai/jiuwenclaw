import importlib
import json
from pathlib import Path

import pytest


MODULE = "jiuwenclaw.agentserver.tools.deepresearch_plugin.artifact_naming"


def _api():
    return importlib.import_module(MODULE)


def _provenance(
    document_id: str = "document-a",
    *,
    version_number: int | None = 1,
    version_base_stem: str | None = "report",
    rewrite_history: object = None,
) -> dict:
    provenance = {"document_id": document_id, "rewrite_history": [] if rewrite_history is None else rewrite_history}
    if version_number is not None:
        provenance["version_number"] = version_number
    if version_base_stem is not None:
        provenance["version_base_stem"] = version_base_stem
    return provenance


def _write_sidecar(root: Path, name: str, provenance: dict, markdown: str = "# Report\n") -> Path:
    report = root / f"{name}.md"
    report.write_text(markdown, encoding="utf-8")
    report.with_suffix(".provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False), encoding="utf-8"
    )
    return report


def test_public_dataclasses_are_frozen_slots_and_initial_name_normalizes_terminal_version():
    api = _api()

    version = api.initial_version("Quarterly / report-v17.md")

    assert version == api.ArtifactVersion("Quarterly_report", 1)
    assert getattr(api.ArtifactVersion, "__slots__", None) == ("base_stem", "version_number")
    with pytest.raises(AttributeError):
        version.base_stem = "other"


@pytest.mark.parametrize(
    ("provenance", "code"),
    [
        (_provenance(version_number=None), "ARTIFACT_NAMING_INVALID"),
        (_provenance(version_base_stem=None), "ARTIFACT_NAMING_INVALID"),
        (_provenance(version_number=True), "ARTIFACT_NAMING_INVALID"),
        (_provenance(version_number=1.0), "ARTIFACT_NAMING_INVALID"),
        (_provenance(version_number=0), "ARTIFACT_NAMING_INVALID"),
        (_provenance(version_base_stem="unsafe/name"), "ARTIFACT_NAMING_INVALID"),
        (_provenance(version_base_stem=""), "ARTIFACT_NAMING_INVALID"),
        (_provenance(version_base_stem="report-v2"), "ARTIFACT_NAMING_INVALID"),
    ],
)
def test_explicit_provenance_rejects_partial_or_invalid_version_metadata(tmp_path, provenance, code):
    api = _api()

    with pytest.raises(api.ArtifactNamingError) as caught:
        api.resolve_artifact_version(provenance, tmp_path / "report.md", "# Report\n")

    assert caught.value.code == code


def test_explicit_provenance_is_authoritative_over_filename_and_markdown(tmp_path):
    api = _api()

    version = api.resolve_artifact_version(
        _provenance(version_number=7, version_base_stem="official"),
        tmp_path / "legacy-name-v2.md",
        "# Different heading\n",
    )

    assert version == api.ArtifactVersion("official", 7)


@pytest.mark.parametrize(
    ("markdown", "report_name", "expected_base"),
    [
        ("# First heading!\n# Later heading\n", "report.md", "First_heading"),
        ("## Not an H1\n", "legacy report-v9.md", "legacy_report"),
        ("# !!!\n", "report.md", "深度研究报告"),
        ("\n", "-v9.md", "深度研究报告"),
    ],
)
def test_legacy_resolution_uses_first_h1_then_report_stem_then_default(
    tmp_path, markdown, report_name, expected_base
):
    api = _api()

    version = api.resolve_artifact_version(
        {"rewrite_history": []}, tmp_path / report_name, markdown
    )

    assert version == api.ArtifactVersion(expected_base, 1)


@pytest.mark.parametrize("history", [None, "not-a-list", ["not-a-mapping"]])
def test_legacy_resolution_rejects_malformed_rewrite_history(tmp_path, history):
    api = _api()

    with pytest.raises(api.ArtifactNamingError) as caught:
        api.resolve_artifact_version(
            {"rewrite_history": history}, tmp_path / "report.md", "# Report\n"
        )

    assert caught.value.code == "ARTIFACT_NAMING_INVALID"


def test_legacy_resolution_derives_logical_version_from_rewrite_history(tmp_path):
    api = _api()

    version = api.resolve_artifact_version(
        {"rewrite_history": [{"revision_id": "1"}, {"revision_id": "2"}]},
        tmp_path / "old.md",
        "# Legacy title\n",
    )

    assert version == api.ArtifactVersion("Legacy_title", 3)


def test_allocate_initial_paths_adds_same_title_suffix_and_checks_all_sidecars(tmp_path):
    api = _api()

    first = api.allocate_initial_paths(tmp_path, "report.md")
    first.provenance_path.write_text("{}", encoding="utf-8")
    second = api.allocate_initial_paths(tmp_path, "report.md")
    second.final_result_path.write_text("{}", encoding="utf-8")
    third = api.allocate_initial_paths(tmp_path, "report.md")

    assert first.markdown_path.name == "report-v1.md"
    assert second.markdown_path.name == "report-2-v1.md"
    assert third.markdown_path.name == "report-3-v1.md"
    assert second.provenance_path.name == "report-2-v1.provenance.json"
    assert second.final_result_path.name == "report-2-v1.final-result.json"
    assert getattr(api.ArtifactPaths, "__slots__", None) == (
        "version", "markdown_path", "provenance_path", "final_result_path"
    )


def test_allocate_next_paths_uses_global_same_document_max_across_branches(tmp_path):
    api = _api()
    parent = _write_sidecar(tmp_path, "report-v1", _provenance(version_number=1))
    _write_sidecar(tmp_path, "report-v2", _provenance(version_number=2))

    allocated = api.allocate_next_paths(parent, _provenance(version_number=1), "# Report\n")

    assert allocated.version == api.ArtifactVersion("report", 3)
    assert allocated.markdown_path.name == "report-v3.md"


def test_allocate_next_paths_ignores_unrelated_document_sidecars(tmp_path):
    api = _api()
    parent = _write_sidecar(tmp_path, "report-v1", _provenance(version_number=1))
    _write_sidecar(tmp_path, "other-v99", _provenance("document-b", version_number=99))

    allocated = api.allocate_next_paths(parent, _provenance(version_number=1), "# Report\n")

    assert allocated.version == api.ArtifactVersion("report", 2)


def test_allocate_next_paths_fails_closed_for_invalid_same_document_sibling(tmp_path):
    api = _api()
    parent = _write_sidecar(tmp_path, "report-v1", _provenance(version_number=1))
    _write_sidecar(tmp_path, "report-v2", _provenance(version_number=True))

    with pytest.raises(api.ArtifactNamingError) as caught:
        api.allocate_next_paths(parent, _provenance(version_number=1), "# Report\n")

    assert caught.value.code == "ARTIFACT_NAMING_INVALID"


def test_allocate_next_paths_counts_legacy_sibling_and_advances_past_existing_targets(tmp_path):
    api = _api()
    parent = _write_sidecar(tmp_path, "report-v1", _provenance(version_number=1))
    _write_sidecar(
        tmp_path,
        "legacy-v5",
        {"document_id": "document-a", "rewrite_history": [{}, {}, {}]},
        "# Old report\n",
    )
    (tmp_path / "report-v5.md").write_text("reserved", encoding="utf-8")

    allocated = api.allocate_next_paths(parent, _provenance(version_number=1), "# Report\n")

    assert allocated.version == api.ArtifactVersion("report", 6)
    assert allocated.markdown_path.name == "report-v6.md"


def test_allocate_next_paths_advances_when_candidate_sidecar_already_exists(tmp_path):
    api = _api()
    parent = _write_sidecar(tmp_path, "report-v1", _provenance(version_number=1))
    (tmp_path / "report-v2.provenance.json").write_text("{}", encoding="utf-8")

    allocated = api.allocate_next_paths(parent, _provenance(version_number=1), "# Report\n")

    assert allocated.version == api.ArtifactVersion("report", 3)
