# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""DingTalk _detect_workspace_files: per-session project_dir + tenant workspace fallback."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from jiuwenswarm.gateway.channel_manager.base import RobotMessageRouter
from jiuwenswarm.gateway.channel_manager.im_platforms.dingtalk.dingtalk_connect import (
    DingTalkChannel,
    DingTalkConfig,
)


def _make_channel() -> DingTalkChannel:
    config = DingTalkConfig(
        enabled=True,
        client_id="test_client_id",
        client_secret="test_client_secret",
        send_file_allowed=True,
    )
    return DingTalkChannel(config, MagicMock(spec=RobotMessageRouter))


def _patch_tenant_roots(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "jiuwenswarm.gateway.tenant_paths.get_multi_tenant_user_workspace_dir",
        lambda sid, aid: tmp_path / f"service_{sid}" / f"agent_{aid}",
    )
    monkeypatch.setattr(
        "jiuwenswarm.common.utils.get_multi_tenant_user_workspace_dir",
        lambda sid, aid: tmp_path / f"service_{sid}" / f"agent_{aid}",
    )
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.session.session_metadata.get_agent_sessions_dir",
        lambda: tmp_path / "global_sessions",
    )


def _write_session_project_dir(
    sessions_root: Path, session_id: str, project_dir: Path
) -> None:
    meta_dir = sessions_root / session_id
    meta_dir.mkdir(parents=True)
    (meta_dir / "metadata.json").write_text(
        json.dumps({"session_id": session_id, "project_dir": str(project_dir)}),
        encoding="utf-8",
    )


def test_detect_abs_path_under_tenant_workspace(monkeypatch, tmp_path: Path):
    ch = _make_channel()
    _patch_tenant_roots(monkeypatch, tmp_path)
    ws = tmp_path / "service_office" / "agent_bot" / "agent" / "workspace"
    ws.mkdir(parents=True)
    target = ws / "report.docx"
    target.write_bytes(b"docx")

    abs_fwd = str(target).replace("\\", "/")
    found = ch._detect_workspace_files(
        f"已生成文件 {abs_fwd} ，请查收。",
        "sess_1",
        service_id="office",
        agent_id="bot",
    )
    assert len(found) == 1
    assert Path(found[0]).resolve() == target.resolve()


def test_detect_quoted_filename_uses_session_project_dir(monkeypatch, tmp_path: Path):
    ch = _make_channel()
    _patch_tenant_roots(monkeypatch, tmp_path)
    project = tmp_path / "my_project"
    project.mkdir()
    target = project / "notes.pdf"
    target.write_bytes(b"%PDF")
    sessions = tmp_path / "service_default" / "agent_office" / "agent" / "sessions"
    _write_session_project_dir(sessions, "sess_proj", project)

    ws = tmp_path / "service_default" / "agent_office" / "agent" / "workspace"
    ws.mkdir(parents=True)

    found = ch._detect_workspace_files(
        "请下载 'notes.pdf' 查看。",
        "sess_proj",
        service_id="default",
        agent_id="office",
    )
    assert len(found) == 1
    assert Path(found[0]).resolve() == target.resolve()


def test_detect_falls_back_to_tenant_workspace_when_no_project_dir(
    monkeypatch, tmp_path: Path
):
    ch = _make_channel()
    _patch_tenant_roots(monkeypatch, tmp_path)
    ws = tmp_path / "service_default" / "agent_office" / "agent" / "workspace"
    ws.mkdir(parents=True)
    target = ws / "notes.pdf"
    target.write_bytes(b"%PDF")

    found = ch._detect_workspace_files(
        "请下载 'notes.pdf' 查看。",
        "sess_plain",
        service_id="default",
        agent_id="office",
    )
    assert len(found) == 1
    assert Path(found[0]).resolve() == target.resolve()


def test_detect_does_not_use_other_tenant_workspace(monkeypatch, tmp_path: Path):
    ch = _make_channel()
    _patch_tenant_roots(monkeypatch, tmp_path)
    other = tmp_path / "service_default" / "agent_other" / "agent" / "workspace"
    other.mkdir(parents=True)
    (other / "secret.docx").write_bytes(b"x")
    mine = tmp_path / "service_default" / "agent_office" / "agent" / "workspace"
    mine.mkdir(parents=True)

    found = ch._detect_workspace_files(
        "请下载 'secret.docx' 查看。",
        "sess_1",
        service_id="default",
        agent_id="office",
    )
    assert found == []
