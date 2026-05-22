from __future__ import annotations

import asyncio
import json
import logging
import socket
from typing import Any
from urllib.parse import parse_qs, urlparse

from jiuwenclaw.sandbox.sandbox_registry import fetch_sandbox_records_async

logger = logging.getLogger(__name__)

SANDBOX_INFO_HTTP_PORT = 19004


class SandboxInfoHttpServer:
    """对外暴露沙箱信息查询 HTTP 接口。"""

    def __init__(self) -> None:
        self.listen_host = self._get_local_ip()
        self._server: asyncio.Server | None = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._server = await asyncio.start_server(
            self._handle_connection,
            self.listen_host,
            SANDBOX_INFO_HTTP_PORT,
        )
        self._running = True
        logger.info(
            "[SandboxInfoHttp] server started: http://%s:%d/api/v1/sandboxes",
            self.listen_host,
            SANDBOX_INFO_HTTP_PORT,
        )

    async def stop(self) -> None:
        self._running = False
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None

    async def handle_request(
        self,
        method: str,
        path: str,
        headers: dict[str, str],
        body: bytes,
    ) -> tuple[int, dict[str, str], bytes]:
        _ = headers, body
        method_u = (method or "").strip().upper()
        parsed = urlparse(str(path or "").strip())
        request_path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)

        if method_u != "GET":
            return self._json_response(405, {"error": "method not allowed"})

        sandbox_id = ""
        if request_path.startswith("/api/v1/sandboxes/"):
            sandbox_id = request_path[len("/api/v1/sandboxes/") :].strip("/")
        elif request_path == "/api/v1/sandboxes":
            sandbox_id = str((query.get("sandbox_id") or query.get("sandboxId") or [""])[0]).strip()
        else:
            return self._json_response(404, {"error": "not found"})

        try:
            records = await fetch_sandbox_records_async(sandbox_id or None)
        except Exception as exc:  # noqa: BLE001
            logger.exception("[SandboxInfoHttp] query failed")
            return self._json_response(500, {"error": str(exc)})

        if sandbox_id and not records:
            return self._json_response(404, {"error": "sandbox not found", "sandbox_id": sandbox_id})

        payload: dict[str, Any] = {"items": records}
        if sandbox_id:
            payload["sandbox_id"] = sandbox_id
            payload["item"] = records[0] if records else None
        return self._json_response(200, payload)

    @staticmethod
    def _json_response(status: int, payload: dict[str, Any]) -> tuple[int, dict[str, str], bytes]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return status, {"Content-Type": "application/json"}, body

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            request_line = await reader.readline()
            if not request_line:
                return
            parts = request_line.decode("utf-8", errors="replace").strip().split(" ")
            if len(parts) < 2:
                return
            method = parts[0]
            raw_path = parts[1]

            headers: dict[str, str] = {}
            content_length = 0
            while True:
                line = await reader.readline()
                if not line or line == b"\r\n":
                    break
                line_str = line.decode("utf-8", errors="replace").strip()
                if ":" not in line_str:
                    continue
                key, value = line_str.split(":", 1)
                key_l = key.strip().lower()
                headers[key_l] = value.strip()
                if key_l == "content-length":
                    try:
                        content_length = int(value.strip())
                    except ValueError:
                        content_length = 0

            body = b""
            if content_length > 0:
                body = await reader.readexactly(content_length)

            status, resp_headers, resp_body = await self.handle_request(
                method,
                raw_path,
                headers,
                body,
            )
            header_lines = "\r\n".join(f"{k}: {v}" for k, v in resp_headers.items())
            writer.write(f"HTTP/1.1 {status}\r\n{header_lines}\r\n\r\n".encode("utf-8"))
            writer.write(resp_body)
            await writer.drain()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[SandboxInfoHttp] connection error: %s", exc)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    @staticmethod
    def _get_local_ip() -> str:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect(("8.8.8.8", 80))
                return str(sock.getsockname()[0] or "127.0.0.1")
        except OSError:
            return "127.0.0.1"
