# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
BackendProxy
------------

Lightweight Flask-based reverse proxy that provides a **stable inference URL**
for agents while the actual vLLM server addresses change between training steps
(wake / sleep cycles).

Responsibilities:
- Accept OpenAI-compatible ``/v1/<path>`` requests and forward them to one of
  the registered backend vLLM servers (random load-balancing).
- Expose ``/proxy/backends`` (POST) so the training loop can hot-swap the
  backend server list every step.
- Expose ``/health`` for readiness checks.

This is NOT a "microservice" — it is a simple in-process Flask thread that
provides address-level stability to the agent runtime.
"""

import asyncio
import json
import random
import socket
import threading
import time
from typing import List, Optional

import requests
from flask import Flask, Response, abort, request

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger


class BackendProxy:
    """
    Reverse proxy running in a daemon thread.

    Automatically picks a free port on startup -- no manual configuration needed.

    Usage::

        proxy = BackendProxy()
        await proxy.start()            # auto-picks free port
        print(proxy.url)               # e.g. http://127.0.0.1:54321
        proxy.update_backend_servers(["10.0.0.1:8000", "10.0.0.2:8000"])
        await proxy.stop()
    """

    def __init__(
        self,
        llm_timeout_seconds: float = 30_000,
        model_name: str = "agentrl",
    ) -> None:
        self.llm_timeout_seconds = llm_timeout_seconds
        self.model_name = model_name
        self._backend_servers: List[str] = []
        self._host: str = "0.0.0.0"
        self._port: int = 0  # auto-assigned
        self._app: Optional[Flask] = None
        self._thread: Optional[threading.Thread] = None
        self._is_running = False

    # -- configuration ------------------------------------------------------

    @property
    def port(self) -> int:
        """Return the port the proxy is listening on (0 until started)."""
        return self._port

    @property
    def url(self) -> str:
        """Return the base URL of the proxy (e.g. http://127.0.0.1:54321)."""
        return f"http://127.0.0.1:{self._port}"

    @staticmethod
    def _find_free_port() -> int:
        """Find and return a free TCP port on localhost."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("", 0))
            return s.getsockname()[1]

    def update_backend_servers(self, servers) -> None:
        """Replace the active backend server list."""
        if isinstance(servers, str):
            servers = [servers]
        self._backend_servers = list(servers)
        logger.info("Updated backend servers: %s", self._backend_servers)

    # -- lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        """Start the Flask proxy in a daemon thread and wait until healthy.

        Automatically picks a free port if none was explicitly set.
        """
        if self._is_running:
            logger.warning("BackendProxy is already running")
            return
        if self._port == 0:
            self._port = self._find_free_port()
        self._thread = threading.Thread(target=self._run_server, daemon=True)
        self._thread.start()
        await self._wait_for_server_ready()

    async def stop(self) -> None:
        """Stop the proxy server thread and release resources."""
        self._is_running = False

    def start_sync(self) -> None:
        """Blocking wrapper around :meth:`start`."""
        asyncio.run(self.start())

    # -- Flask app ----------------------------------------------------------

    def _create_app(self) -> Flask:
        app = Flask(__name__)

        @app.route("/health", methods=["GET"])
        def health_check():
            return {"status": "healthy", "timestamp": time.time()}

        @app.route("/v1/models", methods=["GET"])
        def models():
            # Forward to a real backend when available so the response reflects
            # the actual served model name and metadata reported by vLLM.
            if self._backend_servers:
                try:
                    target = random.choice(self._backend_servers)
                    resp = requests.get(
                        f"http://{target}/v1/models",
                        timeout=10,
                    )
                    if resp.status_code == 200:
                        return Response(
                            resp.content,
                            status=200,
                            mimetype="application/json",
                        )
                except Exception as exc:
                    logger.warning("Failed to fetch /v1/models from backend: %s", exc)
            # Fallback: no backends registered yet or backend unreachable.
            return {"data": [{"id": self.model_name, "object": "model"}]}

        @app.route(
            "/v1/<path:path>",
            methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
        )
        def proxy(path):
            return self._handle_proxy_request(path)

        @app.route("/proxy/backends", methods=["POST"])
        def update_backends_route():
            """Hot-swap backend server list at runtime."""
            try:
                data = request.get_json(force=True, silent=False)
                servers = data.get("servers")
                if not servers:
                    return {"error": "Field 'servers' is required."}, 400
                self.update_backend_servers(servers)
                return {"status": "ok", "servers": self._backend_servers}
            except Exception as e:
                logger.error("Failed to update backend servers via API: %s", e)
                return {"error": str(e)}, 400

        return app

    # -- request forwarding -------------------------------------------------

    def _handle_proxy_request(self, path: str) -> Response:
        if not self._backend_servers:
            logger.error("No backend servers available, returning 503")
            abort(503, description="No backend LLM servers available.")

        target_server = random.choice(self._backend_servers)
        logger.debug("Proxying request to %s/v1/%s", target_server, path)
        return self._forward_request(target_server, path)

    def _forward_request(self, target_server: str, path: str) -> Response:
        target_url = f"http://{target_server}/v1/{path}"
        headers = {
            key: value for key, value in request.headers if key.lower() != "host"
        }

        raw = request.get_data()
        try:
            req_data = json.loads(raw) if raw else {}
            # Ensure model name is set for vLLM compatibility
            req_data["model"] = req_data.get("model", "agentrl")
            # Force non-streaming to avoid SSE/blocking-proxy protocol conflict
            req_data["stream"] = False
            body = json.dumps(req_data)
        except (json.JSONDecodeError, TypeError):
            body = raw

        try:
            response = requests.request(
                method=request.method,
                url=target_url,
                headers=headers,
                params=request.args,
                data=body,
                cookies=request.cookies,
                allow_redirects=False,
                timeout=self.llm_timeout_seconds,
            )
            return self._create_response(response)
        except requests.exceptions.RequestException as e:
            logger.error("Error proxying request to %s: %s", target_url, e)
            error_body = {
                "error": "proxy_request_failed",
                "detail": str(e),
                "target_url": target_url,
            }
            return Response(
                response=json.dumps(error_body),
                status=500,
                mimetype="application/json",
            )

    @staticmethod
    def _create_response(response: requests.Response) -> Response:
        excluded_headers = {
            "content-encoding",
            "content-length",
            "transfer-encoding",
            "connection",
            "keep-alive",
            "proxy-authenticate",
            "proxy-authorization",
            "te",
            "trailers",
            "upgrade",
        }
        headers = [
            (name, value)
            for name, value in response.raw.headers.items()
            if name.lower() not in excluded_headers
        ]
        return Response(response.content, status=response.status_code, headers=headers)

    # -- server thread ------------------------------------------------------

    def _run_server(self) -> None:
        try:
            self._app = self._create_app()
            self._is_running = True
            logger.info("Starting proxy server on %s:%d", self._host, self._port)
            self._app.run(
                host=self._host,
                port=self._port,
                threaded=True,
                debug=False,
                use_reloader=False,
            )
        except Exception as e:
            logger.error("Proxy server error: %s", e)
            self._is_running = False

    async def _wait_for_server_ready(self, max_attempts: int = 30) -> None:
        for attempt in range(max_attempts):
            try:
                resp = requests.get(
                    f"http://{self._host}:{self._port}/health", timeout=2
                )
                if resp.status_code == 200 and resp.json().get("status") == "healthy":
                    logger.info(
                        "Proxy server started successfully on port %d", self._port
                    )
                    return
            except requests.exceptions.ConnectionError:
                if attempt % 5 == 0:
                    logger.warning(
                        "Waiting for proxy server to start… (attempt %d/%d)",
                        attempt + 1,
                        max_attempts,
                    )
            except Exception as e:
                if attempt % 5 == 0:
                    logger.warning("Health check attempt %d failed: %s", attempt + 1, e)
            await asyncio.sleep(0.5)

        raise build_error(
            StatusCode.AGENT_RL_PROXY_SERVER_START_FAILED,
            host=self._host,
            port=str(self._port),
        )
