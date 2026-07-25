#!/usr/bin/env python3
"""Neo4j-HTTP-API-faithful Basic-auth-enforcing upstream for offline E2E.

Real Neo4j 2026.06.0 could not be pulled on 205 (Docker Hub TLS blocked by the
netentsec appliance, dist.neo4j.org tarball 403, all configured mirrors broken)
and the host lacks Java 21. This stand-in emulates the real Neo4j HTTP
transactional endpoint so the JiuwenBox Proxy Basic-injection code path is
exercised identically to the real-Neo4j E2E (run_e2e.py with E2E_UPSTREAM=real).

Interface emulated (verified against real Neo4j 2026.06.0,
POST /db/{database}/tx/commit):

    request : {"statements":[{"statement":"RETURN 1 AS value"}]}
    200 ok  : {"results":[{"columns":["value"],
                            "data":[{"row":[1],"meta":[null]}]}],
               "notifications":[],"errors":[]}
    401     : {"errors":[{"code":"Neo.ClientError.Security.Unauthorized",
                         "message":"Invalid credential."}]}
               (or "...No authentication header supplied." when absent)

This matches the real tx/commit response shape (results[].columns +
results[].data[].row), NOT the deprecated Query API v2 shape
({"data":{"fields":[...],"values":[[1]]}}). The password is read from the
E2E_UPSTREAM_PASSWORD env var (never argv).
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

USERNAME = os.environ.get("E2E_UPSTREAM_USERNAME", "neo4j")
PASSWORD = os.environ.get("E2E_UPSTREAM_PASSWORD", "")
LISTEN = ("127.0.0.1", int(os.environ.get("E2E_UPSTREAM_PORT", "17474")))
EXPECTED = base64.b64encode(f"{USERNAME}:{PASSWORD}".encode()).decode()

# POST /db/{database}/tx/commit  (also accept the legacy /db/data/transaction/commit)
TX_COMMIT_RE = re.compile(r"^/db/[^/]+/tx/commit$|^/db/data/transaction/commit$")


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

    def _auth_error(self) -> tuple[str, str]:
        auth = self.headers.get("Authorization", "")
        if not auth:
            return "No authentication header supplied.", ""
        return "Invalid credential.", 'Basic realm="Neo4j"'

    def _check_auth(self) -> bool:
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Basic "):
            return False
        return auth[len("Basic "):].strip() == EXPECTED

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b""
        if not self._check_auth():
            msg, www = self._auth_error()
            body = json.dumps({"errors": [
                {"code": "Neo.ClientError.Security.Unauthorized", "message": msg}
            ]}, separators=(",", ":")).encode()
            hdrs = {"WWW-Authenticate": www} if www else None
            self._send(401, body, hdrs)
            return
        try:
            payload = json.loads(raw.decode() or "{}")
        except Exception:
            payload = {}
        # tx/commit body: {"statements":[{"statement":"..."}]}
        stmts = payload.get("statements", [])
        stmt = stmts[0].get("statement", "") if stmts else payload.get("statement", "")
        if "RETURN 1 AS value" in stmt:
            out = {"results": [{"columns": ["value"],
                                "data": [{"row": [1], "meta": [None]}]}],
                   "notifications": [], "errors": []}
        else:
            out = {"results": [{"columns": [], "data": []}],
                   "notifications": [], "errors": []}
        self._send(200, json.dumps(out, separators=(",",":")).encode())

    def do_GET(self) -> None:  # noqa: N802
        if not self._check_auth():
            msg, www = self._auth_error()
            body = json.dumps({"errors": [
                {"code": "Neo.ClientError.Security.Unauthorized", "message": msg}
            ]}, separators=(",", ":")).encode()
            hdrs = {"WWW-Authenticate": www} if www else None
            self._send(401, body, hdrs)
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
