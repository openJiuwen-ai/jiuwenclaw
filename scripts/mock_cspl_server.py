#!/usr/bin/env python3
"""Mock CSPL API server — always returns REJECT for testing."""

import argparse
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse


class MockCsplHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        path = urlparse(self.path).path

        if path != "/celia-claw/v1/rest-api/skill/execute":
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'{"error": "not found"}')
            return

        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len) if content_len else b""
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = body.decode("utf-8", errors="replace")

        print("\n=== CSPL REQUEST ===")
        print(f"Headers: {dict(self.headers)}")
        print(
            f"Body: {json.dumps(payload, indent=2, ensure_ascii=False) if isinstance(payload, dict) else payload}"
        )
        print()

        response = {
            "data": {"securityResult": "REJECT"},
            "retCode": "0",
        }
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(response).encode())

    def log_message(self, format, *args):
        print(f"[MOCK-CSPL] {args[0]}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Mock CSPL API server")
    parser.add_argument("--port", type=int, default=8899, help="Listen port (default: 8899)")
    args = parser.parse_args()

    server = HTTPServer(("0.0.0.0", args.port), MockCsplHandler)
    print(f"Mock CSPL server listening on http://0.0.0.0:{args.port}")
    print("Endpoint: POST /celia-claw/v1/rest-api/skill/execute")
    print("Always returns: REJECT")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()
