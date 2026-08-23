from __future__ import annotations

import io
import threading
import sys
import types
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote
from unittest.mock import patch

import pytest

from jiuwenswarm.agents.harness.common.tools.verified_download_assets import (
    VerifiedDownloadAssetOwner,
)
from jiuwenswarm.agents.harness.common.tools.web_file_download import (
    WebFileDownloadManager,
)

bootstrap_module = types.ModuleType("jiuwenswarm.agents.harness.team.bootstrap")
bootstrap_module.configure_agent_teams_home = lambda: None
sys.modules.setdefault(bootstrap_module.__name__, bootstrap_module)

from jiuwenswarm.channels.web.app_web import _SpaStaticHandler


class _DownloadHandlerStub:
    def __init__(self, *, headers: dict[str, str] | None = None) -> None:
        self.command = "GET"
        self.headers = headers or {}
        self.wfile = io.BytesIO()
        self.status: int | None = None
        self.response_headers: dict[str, str] = {}

    def send_response(self, status: int) -> None:
        self.status = status

    def send_header(self, name: str, value: str) -> None:
        self.response_headers[name] = value

    def end_headers(self) -> None:
        return None

    def log_error(self, message: str, *args: object) -> None:
        raise AssertionError(message % args)


def test_raw_file_serves_persisted_session_image(tmp_path):
    agent_root = tmp_path / "agent"
    image_path = agent_root / "sessions" / "session-1" / "uploads" / "image.png"
    image_path.parent.mkdir(parents=True)
    image_bytes = b"\x89PNG\r\n\x1a\nimage-data"
    image_path.write_bytes(image_bytes)

    class Handler(_SpaStaticHandler):
        project_root = tmp_path
        workspace_root = agent_root
        agent_teams_root = tmp_path / "agent-teams"
        logs_root = tmp_path / "logs"
        auto_harness_root = tmp_path / "auto-harness"
        api_target = ""
        ws_target = ""

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port)
        connection.request("GET", f"/file-api/raw-file?path={quote(str(image_path))}")
        response = connection.getresponse()
        try:
            assert response.status == 200
            assert response.headers["Content-Type"] == "image/png"
            assert response.read() == image_bytes
        finally:
            connection.close()
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_agentos_download_is_proxied_to_web_channel(tmp_path):
    received_paths: list[str] = []

    class WebChannelHandler(BaseHTTPRequestHandler):
        def do_HEAD(self):  # noqa: N802
            received_paths.append(self.path)
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format, *args):  # noqa: A002
            del format, args

    web_channel = ThreadingHTTPServer(("127.0.0.1", 0), WebChannelHandler)
    web_channel_thread = threading.Thread(target=web_channel.serve_forever)
    web_channel_thread.start()

    class Handler(_SpaStaticHandler):
        project_root = tmp_path
        workspace_root = tmp_path / "agent"
        agent_teams_root = tmp_path / "agent-teams"
        logs_root = tmp_path / "logs"
        auto_harness_root = tmp_path / "auto-harness"
        api_target = f"http://127.0.0.1:{web_channel.server_port}"
        ws_target = ""

    frontend = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    frontend_thread = threading.Thread(target=frontend.serve_forever)
    frontend_thread.start()
    try:
        with patch("jiuwenswarm.channels.web.app_web._uses_agentos_routing", return_value=True):
            connection = HTTPConnection("127.0.0.1", frontend.server_port)
            connection.request("HEAD", "/file-api/download?token=signed-token&user_id=user-1")
            response = connection.getresponse()
            try:
                assert response.status == 200
            finally:
                response.read()
                connection.close()
        assert received_paths == ["/file-api/download?token=signed-token&user_id=user-1"]
    finally:
        frontend.shutdown()
        frontend_thread.join()
        frontend.server_close()
        web_channel.shutdown()
        web_channel_thread.join()
        web_channel.server_close()


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_verified_download_get_and_head_use_conventional_staged_file_handler(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
) -> None:
    source = tmp_path / "approved report.md"
    content = b"approved-content"
    source.write_bytes(content)
    owner = VerifiedDownloadAssetOwner(
        root=tmp_path / "assets",
        now_fn=lambda: 100.0,
        start_sweeper=False,
    )
    asset = owner.stage(
        source,
        file_name=source.name,
        expires_at=160.0,
    )
    manager = WebFileDownloadManager("s" * 32, asset_owner=owner)
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.common.tools.web_file_download.time.time",
        lambda: 100.0,
    )
    monkeypatch.setattr(WebFileDownloadManager, "_instance", manager)
    token = manager.generate_verified_asset_token(
        asset,
        file_name=source.name,
        session_id="session-a",
    )

    class Handler(_SpaStaticHandler):
        project_root = tmp_path
        workspace_root = tmp_path
        agent_teams_root = tmp_path / "agent-teams"
        logs_root = tmp_path / "logs"
        auto_harness_root = tmp_path / "auto-harness"
        api_target = ""
        ws_target = ""

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        with patch(
            "jiuwenswarm.channels.web.app_web._uses_agentos_routing",
            return_value=False,
        ):
            connection = HTTPConnection("127.0.0.1", server.server_port)
            connection.request(method, f"/file-api/download?token={quote(token)}")
            response = connection.getresponse()
            try:
                assert response.status == 200
                assert response.headers["Content-Length"] == str(len(content))
                assert response.headers["Cache-Control"] == "no-store"
                assert response.read() == (content if method == "GET" else b"")
            finally:
                connection.close()
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_verified_asset_retry_range_and_concurrent_get_use_same_sealed_object(
    tmp_path: Path,
) -> None:
    source = tmp_path / "media.bin"
    source.write_bytes(b"0123456789")
    owner = VerifiedDownloadAssetOwner(
        root=tmp_path / "assets",
        start_sweeper=False,
    )
    asset = owner.stage(
        source,
        file_name=source.name,
        expires_at=10**10,
    )
    source.write_bytes(b"changed-after-capture")

    def fetch(headers: dict[str, str] | None = None) -> _DownloadHandlerStub:
        handler = _DownloadHandlerStub(headers=headers)
        _SpaStaticHandler._serve_verified_local_download(
            handler,
            asset.sealed_path.as_posix(),
            inline=False,
            file_name=source.name,
        )
        return handler

    first = fetch()
    retry = fetch()
    partial = fetch({"Range": "bytes=2-5"})
    with ThreadPoolExecutor(max_workers=4) as executor:
        concurrent_bodies = tuple(
            executor.map(lambda _index: fetch().wfile.getvalue(), range(4))
        )

    assert first.status == retry.status == 200
    assert first.wfile.getvalue() == retry.wfile.getvalue() == b"0123456789"
    assert partial.status == 206
    assert partial.wfile.getvalue() == b"2345"
    assert concurrent_bodies == (b"0123456789",) * 4
