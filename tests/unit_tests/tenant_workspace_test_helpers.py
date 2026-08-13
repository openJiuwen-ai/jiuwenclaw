# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Helpers for unit tests that mock service_/agent_ tenant paths."""

from __future__ import annotations

from pathlib import Path


def tenant_workspace_key(
    service_id: str | None = "default",
    agent_id: str | None = "default",
) -> str:
    """Legacy helper name; returns a stable label for assertions (not a disk path)."""
    sid = str(service_id or "default").strip() or "default"
    aid = str(agent_id or "default").strip() or "default"
    if sid == "default" and aid == "default":
        return "default"
    return f"{sid}_{aid}"


def _ids_from_workspace_key(workspace_key: str) -> tuple[str, str]:
    wk = str(workspace_key or "default").strip() or "default"
    if wk == "default":
        return "default", "default"
    if "_" in wk:
        sid, aid = wk.split("_", 1)
        return sid or "default", aid or "default"
    return "default", wk


def tenant_workspace_root(
    tmp_path: Path,
    service_id: str | None = "default",
    agent_id: str | None = None,
    *,
    workspace_key: str | None = None,
) -> Path:
    """``tmp_path/service_{sid}/agent_{aid}`` matching production multi-tenant layout.

    Compatible call styles:
    - ``tenant_workspace_root(tmp, sid, aid)``
    - ``tenant_workspace_root(tmp, workspace_key)`` (legacy)
    - ``tenant_workspace_root(tmp, workspace_key=...)``
    """
    if workspace_key is not None:
        sid, aid = _ids_from_workspace_key(workspace_key)
    elif agent_id is None:
        sid, aid = _ids_from_workspace_key(str(service_id or "default"))
    else:
        sid = str(service_id or "default").strip() or "default"
        aid = str(agent_id or "default").strip() or "default"
    return tmp_path / f"service_{sid}" / f"agent_{aid}"


def patch_multi_tenant_workspace_dirs(monkeypatch, tmp_path: Path) -> None:
    def _mock(service_id: str | None, agent_id: str | None = None) -> Path:
        return tenant_workspace_root(tmp_path, service_id, agent_id)

    monkeypatch.setattr(
        "jiuwenswarm.common.utils.get_multi_tenant_user_workspace_dir",
        _mock,
    )
    monkeypatch.setattr(
        "jiuwenswarm.gateway.tenant_paths.get_multi_tenant_user_workspace_dir",
        _mock,
    )
