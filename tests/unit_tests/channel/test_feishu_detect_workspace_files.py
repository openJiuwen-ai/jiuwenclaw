"""Feishu _detect_workspace_files F1: abs path uses current workspace_dir."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from jiuwenclaw.channel.base import RobotMessageRouter
from jiuwenclaw.channel.feishu import FeishuChannel, FeishuConfig


def _make_channel() -> FeishuChannel:
    config = FeishuConfig(
        enabled=True,
        app_id="test_app_id",
        app_secret="test_app_secret",
        enable_file_upload=True,
    )
    return FeishuChannel(config, MagicMock(spec=RobotMessageRouter))


def test_detect_abs_path_under_new_tenant_workspace(monkeypatch, tmp_path: Path):
    ch = _make_channel()
    ws = tmp_path / "service_office" / "agent_bot" / "agent" / "jiuwenclaw_workspace"
    ws.mkdir(parents=True)
    target = ws / "report.docx"
    target.write_bytes(b"docx")

    monkeypatch.setattr(
        "jiuwenclaw.agentserver.session_metadata.get_resolved_project_dir",
        lambda *_a, **_k: str(ws),
    )
    monkeypatch.setattr(
        "jiuwenclaw.utils.resolve_tenant_sessions_dir",
        lambda *_a, **_k: tmp_path / "sessions",
    )

    # New-layout absolute path (forward slashes, as LLMs often emit)
    abs_fwd = str(target).replace("\\", "/")
    text = f"已生成文件 {abs_fwd} ，请查收。"
    found = ch._detect_workspace_files(
        text,
        "sess_1",
        service_id="office",
        agent_id="bot",
    )
    assert len(found) == 1
    assert Path(found[0]).resolve() == target.resolve()


def test_detect_quoted_filename_still_works(monkeypatch, tmp_path: Path):
    ch = _make_channel()
    ws = tmp_path / "workspace"
    ws.mkdir()
    target = ws / "notes.pdf"
    target.write_bytes(b"%PDF")

    monkeypatch.setattr(
        "jiuwenclaw.agentserver.session_metadata.get_resolved_project_dir",
        lambda *_a, **_k: str(ws),
    )
    monkeypatch.setattr(
        "jiuwenclaw.utils.resolve_tenant_sessions_dir",
        lambda *_a, **_k: tmp_path / "sessions",
    )

    found = ch._detect_workspace_files(
        "请下载 'notes.pdf' 查看。",
        "sess_1",
        service_id="default",
        agent_id="default",
    )
    assert len(found) == 1
    assert Path(found[0]).resolve() == target.resolve()


def test_detect_old_home_agent_workspace_pattern_no_longer_matches(
    monkeypatch, tmp_path: Path
):
    """Legacy hardcoded /home/.../agent/workspace/ must not drive detection."""
    ch = _make_channel()
    ws = tmp_path / "jiuwenclaw_workspace"
    ws.mkdir()
    (ws / "keep.txt").write_text("x", encoding="utf-8")

    monkeypatch.setattr(
        "jiuwenclaw.agentserver.session_metadata.get_resolved_project_dir",
        lambda *_a, **_k: str(ws),
    )
    monkeypatch.setattr(
        "jiuwenclaw.utils.resolve_tenant_sessions_dir",
        lambda *_a, **_k: tmp_path / "sessions",
    )

    legacy = "/home/ubuntu/.jiuwenclaw/agent/workspace/missing.docx"
    found = ch._detect_workspace_files(
        f"文件在 {legacy}",
        "sess_1",
        service_id="default",
        agent_id="default",
    )
    assert found == []
