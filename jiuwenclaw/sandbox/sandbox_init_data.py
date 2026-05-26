from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from jiuwenclaw.utils import logger

if TYPE_CHECKING:
    from jiuwenclaw.sandbox.sandbox_client import SandboxClient

SANDBOX_INIT_DATA_PATH_ENV = "SANDBOX_INIT_DATA_PATH"
DEFAULT_SANDBOX_INIT_DATA_PATH = "/home/sandbox/init_data.json"
SANDBOX_REMOTE_PATH_PREFIX = "/home/sandbox/"


def get_sandbox_init_data_path() -> str:
    raw = os.environ.get(SANDBOX_INIT_DATA_PATH_ENV, "").strip()
    if raw:
        return raw
    return DEFAULT_SANDBOX_INIT_DATA_PATH


def strip_sandbox_remote_path_prefix(path: str) -> str:
    """Strip the ``/home/sandbox/`` prefix from a sandbox-side path.

    ``SandboxClient.upload_file`` expects ``remote_path`` to be relative to the
    sandbox's home directory, while ``SANDBOX_INIT_DATA_PATH`` is configured as
    an absolute path (so AgentServer inside the sandbox can read it directly).
    Strip the well-known prefix before invoking ``upload_file``.
    """

    if path.startswith(SANDBOX_REMOTE_PATH_PREFIX):
        return path[len(SANDBOX_REMOTE_PATH_PREFIX):]
    return path


def build_sandbox_init_data_payload(*, api_key: str, sandbox_id: str) -> dict[str, str]:
    return {
        "apiKey": str(api_key).strip(),
        "sandboxId": str(sandbox_id).strip(),
    }


def serialize_sandbox_init_data(*, api_key: str, sandbox_id: str) -> str:
    payload = build_sandbox_init_data_payload(api_key=api_key, sandbox_id=sandbox_id)
    return json.dumps(payload, ensure_ascii=False)


async def upload_sandbox_init_data(
    sandbox_client: SandboxClient,
    *,
    sandbox_id: str,
    api_key: str,
    remote_path: str | None = None,
) -> None:
    target_path = (remote_path or get_sandbox_init_data_path()).strip()
    if not target_path:
        raise ValueError("sandbox init data remote path is empty")
    upload_path = strip_sandbox_remote_path_prefix(target_path)
    if not upload_path:
        raise ValueError("sandbox init data upload path is empty after prefix strip")
    content = serialize_sandbox_init_data(api_key=api_key, sandbox_id=sandbox_id)
    local_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".json",
            delete=False,
        ) as handle:
            handle.write(content)
            local_path = handle.name
        result = await sandbox_client.upload_file(
            local_path,
            upload_path,
            sandbox_id,
        )
        if not result.success:
            raise RuntimeError(result.error or "upload_file failed for sandbox init data")
        logger.info(
            "Uploaded sandbox init data for sandbox_id=%s remote_path=%s upload_path=%s",
            sandbox_id,
            target_path,
            upload_path,
        )
    finally:
        if local_path:
            try:
                Path(local_path).unlink(missing_ok=True)
            except OSError:
                logger.exception("Failed to remove temporary sandbox init data file: %s", local_path)
