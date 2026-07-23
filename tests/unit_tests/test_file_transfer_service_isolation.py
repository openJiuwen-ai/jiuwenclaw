# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for file transfer received_dir service_id isolation (方案 A)."""

from __future__ import annotations

import asyncio
import base64
import hashlib

from jiuwenclaw.agentserver.file_transfer_manager import FileTransferManager
from jiuwenclaw.config import FileTransferConfig
from jiuwenclaw.utils import (
    FileTransferStartParams,
    get_service_root_dir,
    resolve_file_transfer_received_dir,
)


def test_resolve_file_transfer_received_dir_by_service(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "jiuwenclaw.utils.get_user_workspace_dir",
        lambda: tmp_path,
    )
    office = resolve_file_transfer_received_dir("received_files", "svc_a")
    default = resolve_file_transfer_received_dir("received_files", None)
    assert office == tmp_path / "service_svc_a" / "received_files"
    assert default == tmp_path / "service_default" / "received_files"
    assert office != default


def test_manager_complete_isolates_by_service_id(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "jiuwenclaw.utils.get_user_workspace_dir",
        lambda: tmp_path,
    )
    config = FileTransferConfig(
        enabled=True,
        chunk_size=100,
        max_file_size=10_000,
        received_files_dir="received_files",
    )
    manager = FileTransferManager(config)
    content = b"hello-tenant"
    sha = hashlib.sha256(content).hexdigest()

    async def _run():
        params = FileTransferStartParams(
            transfer_id="t-office",
            filename="a.txt",
            file_size=len(content),
            sha256=sha,
            total_chunks=1,
            chunk_size=100,
            session_id="sess1",
            service_id="tenant_x",
        )
        assert (await manager.handle_transfer_start(params))["accepted"]
        await manager.handle_transfer_chunk(
            "t-office",
            0,
            base64.b64encode(content).decode(),
        )
        result = await manager.handle_transfer_complete("t-office", sha)
        assert result["success"] is True
        path = result["file_path"]
        assert "service_tenant_x" in path
        assert path.endswith("sess1/a.txt") or path.replace("\\", "/").endswith(
            "sess1/a.txt"
        )
        assert (tmp_path / "service_tenant_x" / "received_files" / "sess1" / "a.txt").exists()
        assert not (
            get_service_root_dir("default") / "received_files" / "sess1" / "a.txt"
        ).exists()

    asyncio.run(_run())
