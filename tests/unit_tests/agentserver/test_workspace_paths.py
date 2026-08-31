# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from pathlib import Path

from jiuwenswarm.agents.harness.common.workspace_paths import (
    STALE_SANDBOX_ARTIFACT_PATH,
    display_workspace_path,
    resolve_workspace_path,
    sanitize_visible_value,
)


def test_resolve_workspace_uri_stays_inside_current_workspace(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True)

    resolved = resolve_workspace_path(
        "workspace://current/reports/out.pptx", workspace_root
    )

    assert resolved == workspace_root / "reports" / "out.pptx"


def test_resolve_workspace_uri_rejects_encoded_escape(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True)

    assert (
        resolve_workspace_path("workspace://current/%2e%2e/old.txt", workspace_root)
        is None
    )


def test_resolve_workspace_path_rejects_legacy_sandbox_artifact_path(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    legacy_path = tmp_path / ".sandbox-artifacts" / "sess_old" / "old.txt"
    workspace_root.mkdir(parents=True)

    assert resolve_workspace_path(legacy_path, workspace_root) is None


def test_display_current_workspace_path_as_canonical_path(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    path = workspace_root / "reports" / "out.pptx"

    assert display_workspace_path(path, workspace_root) == path.as_posix()


def test_sanitize_visible_value_preserves_current_paths_and_redacts_stale_paths(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    current_path = workspace_root / "reports" / "out.pptx"
    stale_path = tmp_path / ".sandbox-artifacts" / "sess_old" / "reports" / "old.pptx"
    value = {
        "stdout": f"created {current_path}",
        "stderr": f"stale {stale_path}",
        "items": [str(current_path)],
    }

    sanitized = sanitize_visible_value(value, workspace_root)

    assert sanitized["stdout"] == f"created {current_path}"
    assert sanitized["stderr"] == f"stale {STALE_SANDBOX_ARTIFACT_PATH}"
    assert sanitized["items"] == [str(current_path)]


def test_sanitize_visible_value_sanitizes_mapping_keys(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    current_path = workspace_root / "reports" / "out.pptx"
    stale_path = tmp_path / ".sandbox-artifacts" / "sess_old" / "old.pptx"

    sanitized = sanitize_visible_value(
        {
            str(current_path): "current",
            str(stale_path): "stale",
        },
        workspace_root,
    )

    assert sanitized == {
        str(current_path): "current",
        STALE_SANDBOX_ARTIFACT_PATH: "stale",
    }


def test_sanitize_visible_text_redacts_stale_sandbox_path_with_spaces(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    stale_path = tmp_path / ".sandbox-artifacts" / "sess_old" / "secret report.pdf"

    sanitized = sanitize_visible_value(
        {"stderr": f"stale={stale_path}"}, workspace_root
    )

    assert sanitized["stderr"] == f"stale={STALE_SANDBOX_ARTIFACT_PATH}"
    assert "secret report.pdf" not in sanitized["stderr"]


def test_sanitize_visible_text_preserves_current_path_when_stale_path_follows(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    current_path = workspace_root / "outputs" / "report.pptx"
    stale_path = tmp_path / ".sandbox-artifacts" / "sess_old" / "old.pptx"

    sanitized = sanitize_visible_value(
        {"command": f"--out {current_path} --old {stale_path}"},
        workspace_root,
    )

    assert str(current_path) in sanitized["command"]
    assert STALE_SANDBOX_ARTIFACT_PATH in sanitized["command"]
