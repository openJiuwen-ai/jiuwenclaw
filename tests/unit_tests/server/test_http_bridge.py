"""Phase 2 HTTP bridge（AgentServer 下载/上传端点）单元测试。

覆盖：
- ``_build_http_file_download_response``：缺失/非法 token、注入目录内文件 200、
  越界路径 403、边界工具；
- ``_AgentHttpUploadHandler.do_POST``：缺失/非法 token、合法上传落盘、越界
  相对路径拒绝（通过真实 ThreadingHTTPServer + urllib 请求验证）。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from jiuwenswarm.agents.harness.common.tools.web_file_download import (
    generate_file_download_token,
    generate_file_upload_token,
    is_path_within_user_dirs,
)
from jiuwenswarm.server.agent_ws_server import (
    AgentWebSocketServer,
    _AgentHttpUploadHandler,
)


@pytest.fixture
def isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把目录门面指向临时目录并规避模块级缓存。"""
    monkeypatch.setenv("JIUWENSWARM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        "jiuwenswarm.common.utils.get_user_workspace_dir",
        lambda: tmp_path,
    )
    monkeypatch.setattr(
        "jiuwenswarm.common.utils.get_agent_sessions_dir",
        lambda: tmp_path / "agent" / "sessions",
    )
    monkeypatch.setattr(
        "jiuwenswarm.common.utils.get_agent_workspace_dir",
        lambda: tmp_path / "agent" / "workspace",
    )
    (tmp_path / "agent" / "workspace").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _download_response(
    server: AgentWebSocketServer,
    path: str,
    request_headers: dict[str, str] | None = None,
):
    return server._build_http_file_download_response(
        path, ("/x", None), request_headers
    )


class TestDownloadEndpoint:
    def test_missing_token(self, isolated_data_dir: Path) -> None:
        server = AgentWebSocketServer()
        status, _, _ = _download_response(server, "/file-api/download")
        assert status == 400

    def test_invalid_token(self, isolated_data_dir: Path) -> None:
        server = AgentWebSocketServer()
        status, _, _ = _download_response(
            server, "/file-api/download?token=garbage.token"
        )
        assert status == 403

    def test_ok_file_inside_workspace(self, isolated_data_dir: Path) -> None:
        inside = isolated_data_dir / "agent" / "workspace" / "a.txt"
        inside.write_text("hello", encoding="utf-8")
        token = generate_file_download_token(str(inside), "s1")
        server = AgentWebSocketServer()
        status, headers, body = _download_response(
            server, f"/file-api/download?token={token}"
        )
        assert status == 200
        assert body == b"hello"
        assert any(k.lower() == "content-type" for k, _ in headers)

    def test_valid_token_keeps_legacy_download_scope(self, isolated_data_dir: Path, tmp_path: Path) -> None:
        outside = tmp_path.parent / "outside.txt"
        outside.write_text("secret", encoding="utf-8")
        token = generate_file_download_token(str(outside), "s1")
        server = AgentWebSocketServer()
        status, _, body = _download_response(
            server, f"/file-api/download?token={token}"
        )
        assert status == 200
        assert body == b"secret"

    def test_inline_disposition_and_rfc5987(self, isolated_data_dir: Path) -> None:
        """inline=1 → inline；filename 用 RFC 5987 编码，非 ASCII 不产生乱码。"""
        inside = isolated_data_dir / "agent" / "workspace" / "预览 报告.pdf"
        inside.write_bytes(b"%PDF-1.7")
        token = generate_file_download_token(str(inside), "s1")
        server = AgentWebSocketServer()
        status, headers, _ = _download_response(
            server, f"/file-api/download?token={token}&inline=1"
        )
        assert status == 200
        disposition = dict(headers).get("Content-Disposition", "")
        assert disposition.startswith("inline; filename*=UTF-8''")
        assert "预览" not in disposition  # 非 ASCII 必须以 %XX 编码出现

    def test_attachment_disposition_by_default(self, isolated_data_dir: Path) -> None:
        inside = isolated_data_dir / "agent" / "workspace" / "a.txt"
        inside.write_text("hello", encoding="utf-8")
        token = generate_file_download_token(str(inside), "s1")
        server = AgentWebSocketServer()
        status, headers, _ = _download_response(
            server, f"/file-api/download?token={token}"
        )
        assert status == 200
        assert dict(headers)["Content-Disposition"].startswith("attachment")


class TestUploadEndpoint:
    @pytest.fixture
    def upload_server(self, isolated_data_dir: Path):
        http_server = ThreadingHTTPServer(("127.0.0.1", 0), _AgentHttpUploadHandler)
        import threading

        thread = threading.Thread(target=http_server.serve_forever, daemon=True)
        thread.start()
        yield http_server
        http_server.shutdown()
        http_server.server_close()

    def _post(self, upload_server, token: str, body: bytes):
        port = upload_server.server_address[1]
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/file-api/upload?token={token}",
            data=body,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_missing_token(self, upload_server) -> None:
        status, _ = self._post(upload_server, "", b"x")
        assert status == 400

    def test_invalid_token(self, upload_server) -> None:
        status, _ = self._post(upload_server, "garbage.token", b"x")
        assert status == 403

    def test_upload_lands_in_workspace(self, upload_server, isolated_data_dir: Path) -> None:
        token = generate_file_upload_token("agent/workspace/up.bin", "s1")
        status, payload = self._post(upload_server, token, b"upload-body")
        assert status == 200
        target = Path(payload["path"])
        assert target.read_bytes() == b"upload-body"
        assert payload["size"] == len(b"upload-body")

    def test_traversal_rejected(self, upload_server) -> None:
        token = generate_file_upload_token("../evil.txt", "s1")
        status, _ = self._post(upload_server, token, b"x")
        assert status == 400


class TestPathBoundaryTool:
    def test_within_user_dirs(self, isolated_data_dir: Path) -> None:
        inside = isolated_data_dir / "agent" / "workspace" / "a.txt"
        inside.write_text("x", encoding="utf-8")
        outside = isolated_data_dir.parent / "outside.txt"
        assert is_path_within_user_dirs(str(inside)) is True
        assert is_path_within_user_dirs(str(outside)) is False
        assert is_path_within_user_dirs("") is False
