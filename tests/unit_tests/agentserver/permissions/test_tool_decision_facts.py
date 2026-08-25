"""Current contracts for the thin tool decision facts carrier."""

from __future__ import annotations

from pathlib import Path

import pytest

import jiuwenswarm.agents.harness.common.rails.permissions as permissions_package
import jiuwenswarm.agents.harness.common.rails.permissions.tool_decision_facts as facts_module
from jiuwenswarm.agents.harness.common.rails.permissions.tool_capabilities import (
    ToolCapability,
    install_permission_file_semantics,
)
from jiuwenswarm.agents.harness.common.rails.permissions.tool_decision_facts import (
    ToolDecisionFacts,
    build_tool_decision_facts,
)


def _facts(
    tool_name: str,
    args: dict[str, object],
    root: Path | None,
    **kwargs: object,
):
    return build_tool_decision_facts(
        tool_name,
        args,
        workspace_root=root,
        original_args_were_valid_object=True,
        **kwargs,
    )


def test_package_exports_fact_contracts_without_eager_owner_imports() -> None:
    assert permissions_package.ToolCapability is ToolCapability
    assert permissions_package.ToolDecisionFacts is ToolDecisionFacts
    assert permissions_package.build_tool_decision_facts is build_tool_decision_facts


def test_core_access_extraction_owns_read_and_write_paths(tmp_path: Path) -> None:
    read = _facts("read_file", {"path": "README.md"}, tmp_path)
    write = _facts("write_file", {"path": "output.txt", "content": "ok"}, tmp_path)

    assert read.read_paths == ((tmp_path / "README.md").as_posix(),)
    assert read.write_paths == ()
    assert write.write_paths == ((tmp_path / "output.txt").as_posix(),)
    assert read.accesses_known is True
    assert write.accesses_known is True


def test_read_pdf_uses_core_file_access_extraction(tmp_path: Path) -> None:
    install_permission_file_semantics()
    pdf_path = tmp_path / "report.pdf"
    facts = _facts("read_pdf", {"pdf_path": str(pdf_path)}, tmp_path)

    assert facts.accesses_known is True
    assert facts.read_paths == (pdf_path.as_posix(),)
    assert facts.write_paths == ()

    missing = _facts("read_pdf", {"pdf_path": ""}, tmp_path)
    assert missing.accesses_known is False
    assert missing.read_paths == ()


def test_lsp_uses_core_file_access_extraction(tmp_path: Path) -> None:
    install_permission_file_semantics()
    source_path = tmp_path / "src" / "main.py"
    facts = _facts(
        "lsp",
        {
            "operation": "goToDefinition",
            "file_path": str(source_path),
            "line": 1,
            "character": 1,
        },
        tmp_path,
    )

    assert facts.accesses_known is True
    assert facts.read_paths == (source_path.as_posix(),)
    assert facts.write_paths == ()

    workspace_symbol = _facts(
        "lsp",
        {"operation": "workspaceSymbol", "file_path": "", "query": "main"},
        tmp_path,
    )
    assert workspace_symbol.accesses_known is False
    assert workspace_symbol.read_paths == ()


def test_engine_external_paths_are_consumed_not_recomputed(tmp_path: Path) -> None:
    external = (tmp_path.parent / "outside.txt").as_posix()
    facts = _facts(
        "read_file",
        {"path": "README.md"},
        tmp_path,
        external_paths=(external,),
    )

    assert facts.external_paths == (external,)
    assert external not in facts.paths


def test_platform_root_is_host_fact_not_an_action_descriptor(tmp_path: Path) -> None:
    platform = tmp_path / "agent-workspace"
    facts = _facts(
        "read_file",
        {"path": str(platform / "skills" / "SKILL.md")},
        tmp_path / "project",
        platform_trusted_root=platform,
    )

    assert facts.platform_trusted_root == platform.as_posix()
    assert not hasattr(facts, "trusted_path_rules")


def test_missing_workspace_or_unsupported_path_never_looks_empty_and_safe() -> None:
    missing_root = _facts("read_file", {"path": "README.md"}, None)
    unsupported = _facts("apply_patch", {"patch": "*** Begin Patch"}, Path("."))

    assert missing_root.accesses_known is False
    assert unsupported.accesses_known is False


def test_shell_command_accesses_are_observations_not_complete_facts(
    tmp_path: Path,
) -> None:
    raw_command = "\n\u2003printf ok | tee out\n\u2003"
    facts = _facts(
        "mcp_exec_command",
        {
            "command": raw_command,
            "workdir": str(tmp_path / "subdir"),
        },
        tmp_path,
    )

    assert facts.command == "printf ok | tee out"
    assert facts.raw_command == raw_command
    assert facts.effective_workdir == "subdir"
    assert facts.accesses_known is False
    assert not hasattr(facts, "command_operator_kinds")
    assert not hasattr(facts, "deterministic_findings")


def test_uv_install_does_not_claim_authoritative_empty_accesses(
    tmp_path: Path,
) -> None:
    facts = _facts(
        "mcp_exec_command",
        {"command": "uv pip install -e ."},
        tmp_path,
    )

    assert facts.accesses_known is False
    assert facts.read_paths == ()
    assert facts.write_paths == ()


def test_bash_effective_workdir_requires_host_frozen_workdir(tmp_path: Path) -> None:
    nested = tmp_path / "nested"

    frozen = _facts(
        "bash",
        {"command": "pwd", "workdir": str(nested)},
        tmp_path,
    )
    default_unfrozen = _facts("bash", {"command": "pwd"}, tmp_path)
    legacy_unfrozen = _facts(
        "bash",
        {"command": "pwd", "cwd": str(nested)},
        tmp_path,
    )

    assert frozen.effective_workdir == "nested"
    assert default_unfrozen.effective_workdir == ""
    assert legacy_unfrozen.effective_workdir == ""


def test_exec_access_remains_policy_write_but_not_artifact_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = tmp_path / "analyze.py"
    monkeypatch.setattr(
        facts_module,
        "extract_accesses_native",
        lambda *_args, **_kwargs: [(script, "exec", "command")],
    )

    facts = _facts(
        "mcp_exec_command",
        {"command": "python analyze.py"},
        tmp_path,
    )

    assert facts.write_paths == (script.as_posix(),)
    assert facts.artifact_write_paths == ()


def test_send_paths_come_only_from_send_file_guard(tmp_path: Path) -> None:
    path = (tmp_path / "report.md").as_posix()
    facts = _facts(
        "send_file_to_user",
        {"abs_file_path_list": [path]},
        tmp_path,
        send_paths=(path,),
    )

    assert facts.read_paths == (path,)
    assert facts.accesses_known is True


@pytest.mark.parametrize(
    ("tool_name", "tool_args"),
    [
        ("todo_modify", {"future": {"nested": True}}),
        ("memory_search", {"query": object(), "maxResults": -999}),
        ("skill_tool", {"skill_name": "daily-report", "future": {"nested": True}}),
    ],
)
def test_runtime_owned_arguments_remain_opaque(
    tmp_path: Path,
    tool_name: str,
    tool_args: dict[str, object],
) -> None:
    facts = _facts(tool_name, tool_args, tmp_path)

    assert dict(facts.untrusted_args) == tool_args


def test_arguments_are_immutable_and_no_descriptor_fields_remain(tmp_path: Path) -> None:
    facts = _facts("todo_get", {"id": "todo-1"}, tmp_path)

    with pytest.raises(TypeError):
        facts.untrusted_args["id"] = "other"  # type: ignore[index]
    for retired in ("schema_valid", "risk_tier", "domain_operation", "absolute_uris"):
        assert not hasattr(facts, retired)
