#!/usr/bin/env python3
"""Neo4j-HTTP-API-faithful Basic-auth-enforcing upstream for E2E.

Real Neo4j could not be pulled on 205 (Docker Hub TLS blocked by the netentsec
appliance, tarball 403, all configured mirrors broken). This stand-in emulates
the Neo4j HTTP transaction endpoint shape and Basic auth semantics so the
JiuwenBox Proxy Basic-injection code path is exercised identically.

Endpoint (matches Neo4j 5.x):
    POST /db/{database}/query/v2
    body: {"statement": "RETURN 1 AS value"}
    -> 200 {"keys":["value"],"records":[[1]]} when Basic creds match
    -> 401 WWW-Authenticate: Basic realm="Neo4j" otherwise

The password is read from the E2E_UPSTREAM_PASSWORD env var (never argv).
"""
from __future__ import annotations

import base64
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

USERNAME = os.environ.get("E2E_UPSTREAM_USERNAME", "neo4j")
PASSWORD = os.environ.get("E2E_UPSTREAM_PASSWORD", "")
LISTEN = ("127.0.0.1", int(os.environ.get("E2E_UPSTREAM_PORT", "17474")))
EXPECTED = base64.b64encode(f"{USERNAME}:{PASSWORD}".encode()).decode()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code: int, body: bytes, headers: dict[str, str] | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _check_auth(self) -> bool:
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Basic "):
            return False
        return auth[len("Basic "):].strip() == EXPECTED

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b""
        if not self._check_auth():
            body = json.dumps({"errors": [{"code": "Neo.ClientError.Security.Unauthorized"}]}).encode()
            self._send(401, body, {"WWW-Authenticate": 'Basic realm="Neo4j"'})
            return
        try:
            payload = json.loads(raw.decode() or "{}")
        except Exception:
            payload = {}
        stmt = payload.get("statement", "")
        if "RETURN 1 AS value" in stmt:
            out = {"keys": ["value"], "records": [[1]]}
        else:
            out = {"keys": [], "records": []}
        self._send(200, json.dumps(out).encode())

    def do_GET(self) -> None:  # noqa: N802
        if not self._check_auth():
            self._send(401, b'{"errors":[{"code":"Neo.ClientError.Security.Unauthorized"}]}',
                       {"WWW-Authenticate": 'Basic realm="Neo4j"'})
            return
        self._send(200, b'{"neo4j_version":"e2e-standin","bolt_direct":"bolt://disabled"}')

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        sys.stderr.write("[upstream] %s - %s\n" % (self.address_string(), fmt % args))


if __name__ == "__main__":
    if not PASSWORD:
        print("E2E_UPSTREAM_PASSWORD env var required", file=sys.stderr)
        sys.exit(2)
    srv = ThreadingHTTPServer(LISTEN, Handler)
    print(f"[upstream] listening on {LISTEN[0]}:{LISTEN[1]} (Basic user={USERNAME})", file=sys.stderr)
    srv.serve_forever()
