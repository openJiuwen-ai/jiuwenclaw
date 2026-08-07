# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.

"""Enterprise Web file push helpers (Gateway -> app_web /file-api/push)."""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_K8S_WEB_SERVICE_URL = "http://jiuwenclaw-web-nodeport:5173"


def resolve_web_server_push_url() -> str:
    """Resolve Web Server base URL for enterprise file push."""
    if not os.getenv("AGENT_RUNTIME", "").strip():
        return ""

    explicit = os.getenv("JIUWENCLAW_WEB_SERVER_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")

    k8s_host = os.getenv("JIUWENCLAW_WEB_NODEPORT_SERVICE_HOST", "").strip()
    k8s_port = os.getenv("JIUWENCLAW_WEB_NODEPORT_SERVICE_PORT", "").strip()
    if k8s_host and k8s_port:
        return f"http://{k8s_host}:{k8s_port}"

    logger.warning(
        "[WebFilePush] 使用默认 K8s Service URL: %s",
        _DEFAULT_K8S_WEB_SERVICE_URL,
    )
    return _DEFAULT_K8S_WEB_SERVICE_URL


async def push_file_to_web_and_get_token(
    file_path: str,
    filename: str,
    session_id: str,
) -> dict[str, Any] | None:
    """Push a local file to Web Server and return download metadata."""
    if not os.getenv("AGENT_RUNTIME", "").strip():
        return None

    web_server_url = resolve_web_server_push_url()
    if not web_server_url:
        return None

    import aiohttp

    push_endpoint = f"{web_server_url}/file-api/push"

    try:
        file_size = os.path.getsize(file_path)
        with open(file_path, "rb") as f:
            file_content = f.read()

        form_data = aiohttp.FormData()
        form_data.add_field(
            "file",
            file_content,
            filename=filename,
            content_type="application/octet-stream",
        )
        form_data.add_field("session_id", session_id)
        form_data.add_field("filename", filename)
        form_data.add_field("file_size", str(file_size))

        timeout = aiohttp.ClientTimeout(total=300)
        async with aiohttp.ClientSession(timeout=timeout) as http_session:
            async with http_session.post(push_endpoint, data=form_data) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(
                        "[WebFilePush] 推送文件到 Web Server 失败: %s, status=%d, error=%s",
                        filename,
                        response.status,
                        error_text,
                    )
                    return None

                result = await response.json()
                if not result.get("success"):
                    logger.error(
                        "[WebFilePush] Web Server 返回错误: %s",
                        result.get("error"),
                    )
                    return None

                logger.info(
                    "[WebFilePush] 成功推送文件到 Web Server: %s, download_url=%s",
                    filename,
                    result.get("download_url"),
                )
                return {
                    "name": filename,
                    "size": file_size,
                    "mime_type": "application/octet-stream",
                    "download_url": result.get("download_url"),
                    "download_token": result.get("download_token"),
                    "expires_at": result.get("expires_at"),
                }
    except Exception as exc:
        logger.error(
            "[WebFilePush] 推送文件到 Web Server 异常: %s, error: %s",
            filename,
            exc,
            exc_info=True,
        )
        return None
