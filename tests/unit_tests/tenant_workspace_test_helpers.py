# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Helpers for unit tests that mock workspace_key-based tenant paths."""

from __future__ import annotations

from pathlib import Path


def tenant_workspace_key(
    service_id: str | None = "default",
    agent_id: str | None = "default",
) -> str:
    sid = str(service_id or "default").strip() or "default"
    aid = str(agent_id or "default").strip() or "default"
    if sid == "default" and aid == "default":
        return "default"
    return f"{sid}_{aid}"


def tenant_workspace_root(tmp_path: Path, workspace_key: str) -> Path:
    wk = str(workspace_key or "default").strip() or "default"
    return tmp_path / f"workspace_{wk}"


def patch_multi_tenant_workspace_dirs(monkeypatch, tmp_path: Path) -> None:
    def _mock(workspace_key: str) -> Path:
        return tenant_workspace_root(tmp_path, workspace_key)

    monkeypatch.setattr(
        "jiuwenswarm.common.utils.get_multi_tenant_user_workspace_dir",
        _mock,
    )
    monkeypatch.setattr(
        "jiuwenswarm.gateway.tenant_paths.get_multi_tenant_user_workspace_dir",
        _mock,
    )
