# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for WeCom/DingTalk lazy per-tenant file workspace (方案 A)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from jiuwenclaw.channel.dingtalk_file_service import DingTalkFileService
from jiuwenclaw.channel.wecom_file_service import WecomFileService


def test_wecom_file_service_tenant_scope_isolates(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "jiuwenclaw.channel.tenant_paths.get_multi_tenant_user_workspace_dir",
        lambda sid, aid: tmp_path / f"service_{sid}" / f"agent_{aid}",
    )
    svc = WecomFileService(ws_client=SimpleNamespace(), workspace_dir=None)
    assert not (tmp_path / "service_default").exists()

    with svc.tenant_scope("default", "office"):
        office_dir = Path(svc._get_download_dir("images"))
    with svc.tenant_scope("default", "default"):
        default_dir = Path(svc._get_download_dir("images"))

    assert "agent_office" in str(office_dir)
    assert "agent_default" in str(default_dir)
    assert office_dir != default_dir
    assert office_dir.exists()
    assert default_dir.exists()


def test_wecom_file_service_config_override(tmp_path):
    override = tmp_path / "custom_ws"
    svc = WecomFileService(ws_client=SimpleNamespace(), workspace_dir=str(override))
    with svc.tenant_scope("default", "office"):
        path = Path(svc._get_download_dir("files"))
    assert path == override / "wecom_files" / "downloads" / "files"


def test_dingtalk_file_service_tenant_scope_isolates(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "jiuwenclaw.channel.tenant_paths.get_multi_tenant_user_workspace_dir",
        lambda sid, aid: tmp_path / f"service_{sid}" / f"agent_{aid}",
    )

    async def _token():
        return "tok"

    svc = DingTalkFileService(
        client_id="app",
        get_token_func=_token,
        http_client=SimpleNamespace(),
        workspace_dir=None,
    )
    with svc.tenant_scope("default", "office"):
        office_dir = Path(svc._get_download_dir("image"))
    with svc.tenant_scope("default", "default"):
        default_dir = Path(svc._get_download_dir("image"))

    assert "agent_office" in str(office_dir)
    assert office_dir != default_dir
