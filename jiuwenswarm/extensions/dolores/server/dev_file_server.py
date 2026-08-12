"""Dolores dev file-download HTTP server (fork-only, zero mainline change).

Stock jiuwenswarm dev mode (vite:5173 + WS-only gateway/agentserver) has no HTTP
endpoint serving `/file-api/download` file bytes: stock vite.config.ts has no
such middleware (the request falls to the vite SPA fallback -> the browser
saves index.html as the artifact -> corruption), and stock `/file-api/raw-file`
gates on isPathUnderAllowedRoot which rejects the agent's artifact dirs. In prod
app_web.py serves /file-api/download directly; dev has no equivalent, so this
module is it. Runs inside the AgentServer process so it shares
WebFileDownloadManager's secret with send_file_to_user.

Serves GET/HEAD/OPTIONS /file-api/download?token=... via a stdlib
ThreadingHTTPServer, reusing stock validate_file_download_token (HMAC; stock
does not enforce exp, so stale links still resolve) + streaming with
Content-Type/Length/Disposition + CORS (for the 5173 page's cross-origin HEAD
probe). extension.py rewrites download_url to point here. Only active when
JIUWENSWARM_AGENT_KIND=dolores.
"""
from __future__ import annotations

import json
import logging
import mimetypes
import os
import os.path
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

logger = logging.getLogger(__name__)

DEFAULT_PORT = 18098

_server_thread: threading.Thread | None = None
_running_port: int | None = None


class _DoloresFileHandler(BaseHTTPRequestHandler):
    """Serve `/file-api/download?token=...` reusing stock token validation."""

    server_version = "DoloresDevFile/1.0"

    def log_message(self, *args, **kwargs):  # noqa: D401, ARG002
        return

    def _set_cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Max-Age", "86400")

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._set_cors()
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(body)
            except Exception:
                logger.debug("json write interrupted", exc_info=True)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._set_cors()
        self.end_headers()

    def do_HEAD(self) -> None:  # noqa: N802
        self._handle(send_body=False)

    def do_GET(self) -> None:  # noqa: N802
        self._handle(send_body=True)

    def _handle(self, *, send_body: bool) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/file-api/download":
            self._send_json(404, {"error": "not_found"})
            return

        token = (parse_qs(parsed.query).get("token", [""]) or [""])[0]
        if not token:
            self._send_json(400, {"error": "missing_token"})
            return

        try:
            from jiuwenswarm.agents.harness.common.tools.web_file_download import (
                validate_file_download_token,
            )
        except Exception:
            self._send_json(500, {"error": "download_module_unavailable"})
            return

        payload = validate_file_download_token(token)
        if payload is None:
            self._send_json(403, {"error": "invalid_or_expired_token"})
            return

        file_path = (payload or {}).get("path", "")
        if not file_path or not os.path.isfile(file_path):
            self._send_json(404, {"error": "file_not_found"})
            return

        try:
            file_size = os.path.getsize(file_path)
        except OSError:
            self._send_json(404, {"error": "file_not_found"})
            return

        name = os.path.basename(file_path)
        guessed, _ = mimetypes.guess_type(name)
        mime = guessed or "application/octet-stream"

        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(file_size))
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Content-Disposition", f"attachment; filename*=UTF-8''{quote(name)}"
        )
        self._set_cors()
        self.end_headers()

        if not send_body:
            return
        try:
            with open(file_path, "rb") as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except Exception:
            logger.debug("file stream interrupted: %s", file_path, exc_info=True)


def start(port: int | None = None) -> int:
    """Start the dev file server (idempotent). Returns the bound port, or 0 on failure."""
    global _server_thread, _running_port
    if _server_thread and _server_thread.is_alive() and _running_port:
        return _running_port

    desired = int(port) if port else int(os.getenv("DOLORES_FILE_PORT", str(DEFAULT_PORT)))
    candidates = [desired, 0] if desired else [0]
    httpd = None
    for cand in candidates:
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", cand), _DoloresFileHandler)
            break
        except OSError as exc:
            logger.debug("bind 127.0.0.1:%s failed: %s", cand, exc)
            continue
    if httpd is None:
        logger.error("[DoloresFile] cannot bind any port; dev download will fall back to stock")
        return 0

    httpd.daemon_threads = True
    actual_port = httpd.server_address[1]
    _server_thread = threading.Thread(
        target=httpd.serve_forever, name="dolores-dev-file", daemon=True
    )
    _server_thread.start()
    _running_port = actual_port
    logger.info(
        "[DoloresAgent] dev file server listening on http://127.0.0.1:%s/file-api/download",
        actual_port,
    )
    return actual_port
