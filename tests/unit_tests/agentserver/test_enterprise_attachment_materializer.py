# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

from pathlib import Path

import pytest

from jiuwenclaw.agentserver import enterprise_attachment_materializer as mod


def test_enterprise_files_need_download_true_when_url_without_path():
    files = [{"name": "a.png", "url": "http://minio/default/a.png"}]
    assert mod.enterprise_files_need_download(files) is True


def test_enterprise_files_need_download_false_when_local_path_exists(tmp_path: Path):
    local = tmp_path / "a.png"
    local.write_bytes(b"png")
    files = [{"name": "a.png", "url": "http://minio/default/a.png", "path": str(local)}]
    assert mod.enterprise_files_need_download(files) is False


@pytest.mark.asyncio
async def test_materialize_url_attachments_writes_to_workspace_uploads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    workspace = tmp_path / "session"
    workspace.mkdir()
    captured: dict[str, Path] = {}

    def _fake_download(url: str, dest: Path, *, max_file_size: int, timeout: int) -> None:
        captured["url"] = url
        captured["dest"] = dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"fake-image")

    monkeypatch.setenv("AGENT_RUNTIME", "jiuwen")
    monkeypatch.setattr(mod, "_download_http_to_path", _fake_download)

    files = [{"name": "screenshot.png", "url": "http://minio/default/screenshot.png"}]
    result = await mod.materialize_url_attachments(files, str(workspace), request_id="req_1")

    assert result is not files
    assert result[0]["path"] == str(workspace / "uploads" / "screenshot.png")
    assert Path(result[0]["path"]).is_file()
    assert captured["url"] == "http://minio/default/screenshot.png"


@pytest.mark.asyncio
async def test_materialize_skips_when_not_enterprise_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("AGENT_RUNTIME", raising=False)
    files = [{"name": "a.png", "url": "http://minio/default/a.png"}]
    result = await mod.materialize_url_attachments(files, str(tmp_path))
    assert result is files
    assert "path" not in result[0]
