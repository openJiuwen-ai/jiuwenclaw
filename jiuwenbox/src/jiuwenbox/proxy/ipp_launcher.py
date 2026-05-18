"""Standalone inference privacy proxy launcher.

This script is executed inside a network namespace to start the HTTP proxy.
The proxy listens on 127.0.0.1 inside that namespace, making it only
accessible to processes within the same namespace.

Usage:
    python3 -m jiuwenbox.proxy.ipp_launcher --config <json_config>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal

from jiuwenbox.logging_config import configure_logging
from jiuwenbox.proxy.inference_privacy_proxy import InferencePrivacyProxyConfig, ProxyRoute, InferencePrivacyProxy

configure_logging()
logger = logging.getLogger(__name__)

_proxy: InferencePrivacyProxy | None = None


def _signal_handler(sig: int, frame: object) -> None:
    """Handle shutdown signals."""
    logger.info("Received signal %d, shutting down...", sig)
    if _proxy:
        asyncio.create_task(_proxy.stop())


def main() -> None:
    parser = argparse.ArgumentParser(description="Launch HTTP inference privacy proxy")
    parser.add_argument("--config", required=True, help="JSON configuration for the proxy")
    parser.add_argument("--namespace", default=None, help="Network namespace name (for logging)")
    args = parser.parse_args()

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    config_json = json.loads(args.config)

    routes = [
        ProxyRoute(
            path_prefix=r["path_prefix"],
            target_endpoint=r["target_endpoint"],
            api_key=r.get("api_key", ""),
            skip_cert_verify=r.get("skip_cert_verify", False),
        )
        for r in config_json.get("routes", [])
    ]

    config = InferencePrivacyProxyConfig(
        listen_port=config_json.get("listen_port", 8080),
        routes=routes,
    )

    global _proxy
    _proxy = InferencePrivacyProxy(config)

    async def run():
        await _proxy.start()
        logger.info(
            "HTTP IPP running in namespace '%s': 127.0.0.1:%d with %d routes",
            args.namespace or "default",
            config.listen_port,
            len(routes),
        )
        while _proxy.is_running:
            await asyncio.sleep(1)

    asyncio.run(run())


if __name__ == "__main__":
    main()
