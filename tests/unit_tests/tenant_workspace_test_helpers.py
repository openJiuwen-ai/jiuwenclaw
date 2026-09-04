# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Helpers for unit tests that mock ``workspace_{key}/`` tenant paths."""

from __future__ import annotations

from pathlib import Path


def tenant_workspace_key(
    service_id: str | None = "default",
    agent_id: str | None = "default",
) -> str:
    """Legacy helper name; derive a workspace_key label from routing ids."""
    sid = str(service_id or "default").strip() or "default"
    aid = str(agent_id or "default").strip() or "default"
    if sid == "default" and aid == "default":
        return "default"
    return f"{sid}_{aid}"


def tenant_workspace_root(
    tmp_path: Path,
    service_id: str | None = "default",
    agent_id: str | None = None,
    *,
    workspace_key: str | None = None,
) -> Path:
    """``tmp_path/workspace_{key}`` matching production multi-tenant layout.

    Compatible call styles:
    - ``tenant_workspace_root(tmp, sid, aid)`` → key ``{sid}_{aid}`` (or ``default``)
    - ``tenant_workspace_root(tmp, workspace_key)`` (positional as key)
    - ``tenant_workspace_root(tmp, workspace_key=...)``
    """
    if workspace_key is not None:
        wk = str(workspace_key or "default").strip() or "default"
    elif agent_id is None and service_id is not None and "/" not in str(service_id):
        # positional single-arg style: tenant_workspace_root(tmp, "mykey")
        # vs tenant_workspace_root(tmp, "default", "office")
        # When agent_id is None, treat first id as workspace_key if it doesn't look
        # like a pure service id pair call — keep legacy pair helper via both args.
        wk = str(service_id or "default").strip() or "default"
    else:
        wk = tenant_workspace_key(service_id, agent_id)
    return tmp_path / f"workspace_{wk}"


def patch_multi_tenant_workspace_dirs(monkeypatch, tmp_path: Path) -> None:
    """让多租户工作区按 key 分桶并落在 ``tmp_path`` 下（企业版行为）。

    直接 patch ``get_multi_tenant_user_workspace_dir`` 会因被测模块的
    ``from ... import get_multi_tenant_user_workspace_dir`` 引用而失效；
    改为 patch ``is_enterprise``（走企业版按 key 分桶分支）与
    ``get_user_workspace_dir``（落在 tmp_path），对任意调用方都生效。
    """
    monkeypatch.setattr(
        "jiuwenswarm.common.utils.is_enterprise",
        lambda: True,
    )
    monkeypatch.setattr(
        "jiuwenswarm.common.utils.get_user_workspace_dir",
        lambda: tmp_path,
    )
