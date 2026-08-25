# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for lightweight session artifact-path provenance."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from openjiuwen.harness.tools.base_tool import ToolOutput

from jiuwenswarm.agents.harness.common.rails.permissions import (
    artifact_path_provenance as provenance_module,
)
from jiuwenswarm.agents.harness.common.rails.permissions.artifact_path_provenance import (
    ArtifactCandidateState,
    SessionArtifactPathProvenance,
    consume_artifact_candidate_state,
    publish_artifact_candidate_state,
    stage_semantic_artifact_paths,
    tool_result_succeeded,
)


def _candidate_state(
    workspace: Path,
    *,
    semantic_paths: tuple[str, ...] = (),
    host_write_paths: tuple[str, ...] = (),
    payload: str = "",
    tool_name: str = "mcp_exec_command",
    include_semantic: bool = True,
    effective_workdir: Path | str | None = None,
) -> tuple[SimpleNamespace, ArtifactCandidateState | None]:
    ctx = SimpleNamespace(extra={})
    stage_semantic_artifact_paths(ctx, semantic_paths)
    publish_artifact_candidate_state(
        ctx,
        session_id="session-1",
        tool_name=tool_name,
        tool_call_id="tool-call-1",
        workspace_root=workspace,
        host_write_paths=host_write_paths,
        untrusted_args={"code": payload},
        include_semantic=include_semantic,
        effective_workdir=effective_workdir,
    )
    return ctx, consume_artifact_candidate_state(ctx, tool_name=tool_name)


def test_semantic_candidate_registers_only_after_grounding_and_existence(
    tmp_path: Path,
) -> None:
    output = tmp_path / "outputs" / "report.csv"
    output.parent.mkdir()
    output.write_text("result", encoding="utf-8")
    _, state = _candidate_state(
        tmp_path,
        semantic_paths=("outputs/report.csv",),
        payload="write_csv('$WORKSPACE/outputs/report.csv')",
    )
    ledger = SessionArtifactPathProvenance("session-1")

    result = ledger.record_verified(state=state)  # type: ignore[arg-type]

    assert result.accepted == 1
    assert result.rejected == 0
    assert ledger.contains(root_session_id="session-1", path=output)


def test_existing_file_can_register_when_reviewer_declares_clear_output(
    tmp_path: Path,
) -> None:
    output = tmp_path / "report.csv"
    output.write_text("already exists", encoding="utf-8")
    _, state = _candidate_state(
        tmp_path,
        semantic_paths=("report.csv",),
        payload="update_and_deliver('report.csv')",
    )
    ledger = SessionArtifactPathProvenance("session-1")

    result = ledger.record_verified(state=state)  # type: ignore[arg-type]

    assert result.accepted == 1
    assert ledger.contains(root_session_id="session-1", path=output)


def test_nonroot_workdir_rejects_same_named_root_file_and_registers_output(
    tmp_path: Path,
) -> None:
    root_output = tmp_path / "report.csv"
    root_output.write_text("unrelated root file", encoding="utf-8")
    workdir = tmp_path / "subdir"
    workdir.mkdir()
    actual_output = workdir / "report.csv"
    actual_output.write_text("task output", encoding="utf-8")
    ledger = SessionArtifactPathProvenance("session-1")

    _, wrong_state = _candidate_state(
        tmp_path,
        semantic_paths=("report.csv",),
        payload="write('report.csv')",
        effective_workdir=workdir,
    )
    wrong_result = ledger.record_verified(state=wrong_state)  # type: ignore[arg-type]

    _, correct_state = _candidate_state(
        tmp_path,
        semantic_paths=("subdir/report.csv",),
        payload="write('report.csv')",
        effective_workdir=workdir,
    )
    correct_result = ledger.record_verified(  # type: ignore[arg-type]
        state=correct_state,
    )

    assert wrong_result.accepted == 0
    assert wrong_result.reason_codes == ("path_not_grounded",)
    assert not ledger.contains(root_session_id="session-1", path=root_output)
    assert correct_result.accepted == 1
    assert ledger.contains(root_session_id="session-1", path=actual_output)
    assert ledger.relevant_paths(
        root_session_id="session-1",
        workspace_root=tmp_path,
        grounding_texts=("read report.csv",),
        effective_workdir=workdir,
    ) == ("subdir/report.csv",)


def test_host_known_write_does_not_require_semantic_grounding(tmp_path: Path) -> None:
    output = tmp_path / "report.csv"
    output.write_text("result", encoding="utf-8")
    _, state = _candidate_state(
        tmp_path,
        host_write_paths=(str(output),),
        payload="payload without a path",
        include_semantic=False,
        tool_name="write_file",
    )
    ledger = SessionArtifactPathProvenance("session-1")

    result = ledger.record_verified(state=state)  # type: ignore[arg-type]

    assert result.accepted == 1
    assert ledger.contains(root_session_id="session-1", path=output)


@pytest.mark.parametrize(
    ("candidate", "payload", "expected_reason"),
    [
        ("missing.csv", "write('missing.csv')", "path_missing_or_unsafe"),
        ("report.csv", "write('report.csv.bak')", "path_not_grounded"),
        ("report.csv", "write(dynamic_name)", "path_not_grounded"),
    ],
)
def test_semantic_candidate_rejects_missing_or_ungrounded_paths(
    tmp_path: Path,
    candidate: str,
    payload: str,
    expected_reason: str,
) -> None:
    if candidate == "report.csv":
        (tmp_path / candidate).write_text("result", encoding="utf-8")
    _, state = _candidate_state(
        tmp_path,
        semantic_paths=(candidate,),
        payload=payload,
    )
    ledger = SessionArtifactPathProvenance("session-1")

    result = ledger.record_verified(state=state)  # type: ignore[arg-type]

    assert result.accepted == 0
    assert result.rejected == 1
    assert expected_reason in result.reason_codes


@pytest.mark.parametrize(
    "payload",
    (
        "write('报告.csv备份')",
        "write('备份报告.csv')",
    ),
)
def test_unicode_path_superstrings_do_not_satisfy_grounding(
    tmp_path: Path,
    payload: str,
) -> None:
    output = tmp_path / "报告.csv"
    output.write_text("existing", encoding="utf-8")
    _, state = _candidate_state(
        tmp_path,
        semantic_paths=(output.name,),
        payload=payload,
    )
    ledger = SessionArtifactPathProvenance("session-1")

    result = ledger.record_verified(state=state)  # type: ignore[arg-type]

    assert result.accepted == 0
    assert result.reason_codes == ("path_not_grounded",)


@pytest.mark.parametrize(
    "payload",
    (
        "write('report.csv backup')",
        "write('backup report.csv')",
        "write('report.csv(backup)')",
        "write('(backup)report.csv')",
        "write('report.csv,backup')",
        "write('backup,report.csv')",
        "write('report.csv:backup')",
        "write('backup:report.csv')",
        "write('report.csv=backup')",
        "write('backup=report.csv')",
    ),
)
def test_valid_filename_delimiter_superstrings_do_not_satisfy_grounding(
    tmp_path: Path,
    payload: str,
) -> None:
    output = tmp_path / "report.csv"
    output.write_text("existing", encoding="utf-8")
    _, state = _candidate_state(
        tmp_path,
        semantic_paths=(output.name,),
        payload=payload,
    )
    ledger = SessionArtifactPathProvenance("session-1")

    result = ledger.record_verified(state=state)  # type: ignore[arg-type]

    assert result.accepted == 0
    assert result.reason_codes == ("path_not_grounded",)


def test_post_gate_rejects_directory_protected_and_symlink_escape(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "directory"
    directory.mkdir()
    protected = tmp_path / "protected" / "result.csv"
    protected.parent.mkdir()
    protected.write_text("result", encoding="utf-8")
    outside = tmp_path.parent / f"{tmp_path.name}-outside.csv"
    outside.write_text("outside", encoding="utf-8")
    symlink = tmp_path / "escape.csv"
    symlink.symlink_to(outside)
    _, state = _candidate_state(
        tmp_path,
        semantic_paths=("directory", "protected/result.csv", "escape.csv"),
        payload=(
            "deliver('directory', 'protected/result.csv', 'escape.csv')"
        ),
    )
    ledger = SessionArtifactPathProvenance("session-1")

    result = ledger.record_verified(
        state=state,  # type: ignore[arg-type]
        excluded_paths=("protected",),
    )

    assert result.accepted == 0
    assert result.rejected == 3
    assert "path_missing_or_unsafe" in result.reason_codes
    assert "path_protected" in result.reason_codes


def test_ambiguous_input_is_not_published_when_reviewer_returns_no_artifact(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "input.csv"
    input_path.write_text("input", encoding="utf-8")
    ctx, state = _candidate_state(
        tmp_path,
        semantic_paths=(),
        payload="python analyze.py input.csv",
    )

    assert state is None
    assert consume_artifact_candidate_state(
        ctx,
        tool_name="mcp_exec_command",
    ) is None


def test_candidate_state_is_one_shot_and_tool_local(tmp_path: Path) -> None:
    output = tmp_path / "report.csv"
    output.write_text("result", encoding="utf-8")
    ctx = SimpleNamespace(extra={})
    stage_semantic_artifact_paths(ctx, ("report.csv",))
    publish_artifact_candidate_state(
        ctx,
        session_id="session-1",
        tool_name="write_file",
        tool_call_id="tool-call-1",
        workspace_root=tmp_path,
        host_write_paths=(),
        untrusted_args={"content": "write report.csv"},
        include_semantic=True,
    )

    assert consume_artifact_candidate_state(ctx, tool_name="read_file") is None
    assert consume_artifact_candidate_state(ctx, tool_name="write_file") is None


async def test_same_tool_concurrent_contexts_keep_candidate_state_isolated(
    tmp_path: Path,
) -> None:
    contexts: list[SimpleNamespace] = []
    for index in range(2):
        ctx = SimpleNamespace(extra={})
        path = f"report-{index}.csv"
        stage_semantic_artifact_paths(ctx, (path,))
        publish_artifact_candidate_state(
            ctx,
            session_id="session-1",
            tool_name="mcp_exec_command",
            tool_call_id=f"tool-call-{index}",
            workspace_root=tmp_path,
            host_write_paths=(),
            untrusted_args={"command": f"write('{path}')"},
            include_semantic=True,
        )
        contexts.append(ctx)

    async def consume(ctx: SimpleNamespace) -> ArtifactCandidateState | None:
        await asyncio.sleep(0)
        return consume_artifact_candidate_state(
            ctx,
            tool_name="mcp_exec_command",
        )

    states = await asyncio.gather(*(consume(ctx) for ctx in contexts))

    assert [state.tool_call_id for state in states if state is not None] == [
        "tool-call-0",
        "tool-call-1",
    ]
    assert [state.candidates[0].path for state in states if state is not None] == [
        "report-0.csv",
        "report-1.csv",
    ]


def test_relevant_paths_support_access_and_payload_without_tool_allowlist(
    tmp_path: Path,
) -> None:
    output = tmp_path / "outputs" / "report.csv"
    output.parent.mkdir()
    output.write_text("first", encoding="utf-8")
    _, state = _candidate_state(
        tmp_path,
        semantic_paths=("outputs/report.csv",),
        payload="write('outputs/report.csv')",
    )
    ledger = SessionArtifactPathProvenance("session-1")
    ledger.record_verified(state=state)  # type: ignore[arg-type]

    by_access = ledger.relevant_paths(
        root_session_id="session-1",
        workspace_root=tmp_path,
        access_paths=(str(output),),
    )
    by_payload = ledger.relevant_paths(
        root_session_id="session-1",
        workspace_root=tmp_path,
        grounding_texts=("delete outputs/report.csv",),
    )
    output.write_text("replaced", encoding="utf-8")
    after_content_change = ledger.relevant_paths(
        root_session_id="session-1",
        workspace_root=tmp_path,
        access_paths=(str(output),),
    )

    assert by_access == ("outputs/report.csv",)
    assert by_payload == ("outputs/report.csv",)
    assert after_content_change == ("outputs/report.csv",)


def test_relevant_paths_drop_deleted_or_cross_session_entries(tmp_path: Path) -> None:
    output = tmp_path / "report.csv"
    output.write_text("result", encoding="utf-8")
    _, state = _candidate_state(
        tmp_path,
        semantic_paths=("report.csv",),
        payload="write('report.csv')",
    )
    ledger = SessionArtifactPathProvenance("session-1")
    ledger.record_verified(state=state)  # type: ignore[arg-type]

    assert ledger.relevant_paths(
        root_session_id="other-session",
        workspace_root=tmp_path,
        access_paths=(str(output),),
    ) == ()
    output.unlink()
    assert ledger.relevant_paths(
        root_session_id="session-1",
        workspace_root=tmp_path,
        access_paths=(str(output),),
    ) == ()
    assert len(ledger) == 0


def test_ledger_capacity_evicts_oldest_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(provenance_module, "MAX_SESSION_ARTIFACT_PATHS", 2)
    ledger = SessionArtifactPathProvenance("session-1")
    outputs = []
    for index in range(3):
        output = tmp_path / f"report-{index}.csv"
        output.write_text(str(index), encoding="utf-8")
        outputs.append(output)
        _, state = _candidate_state(
            tmp_path,
            semantic_paths=(output.name,),
            payload=f"write('{output.name}')",
        )
        ledger.record_verified(state=state)  # type: ignore[arg-type]

    assert len(ledger) == 2
    assert not ledger.contains(root_session_id="session-1", path=outputs[0])
    assert ledger.contains(root_session_id="session-1", path=outputs[2])
    ledger.dispose()
    assert len(ledger) == 0


@pytest.mark.parametrize(
    ("tool_result", "expected"),
    [
        ("written", False),
        ("permission denied", False),
        ("success=True data='written'", True),
        ("[ERROR]: failed", False),
        (json.dumps({"exit_code": 0}), True),
        (json.dumps({"exit_code": 1}), False),
        (json.dumps({"background": True, "status": "started"}), False),
        (ToolOutput(success=True, data={"pid": 123, "status": "started"}), False),
        ({"success": True}, True),
        ({"status": "failed"}, False),
        ({"unstructured": "mapping"}, False),
        (None, False),
    ],
)
def test_tool_result_success_classification(
    tool_result: object,
    expected: bool,
) -> None:
    ctx = SimpleNamespace(
        inputs=SimpleNamespace(tool_result=tool_result),
        exception=None,
    )

    assert tool_result_succeeded(ctx) is expected
