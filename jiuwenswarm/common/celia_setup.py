"""Installation-time setup for the OpenClaw-compatible Celia runtime."""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

CELIA_BINARY_NAME = "gspd_memory_mcp_server"
CELIA_BINARY_SHA256 = "bf92adf63af75f5d1ee8a86f9ddd388ae4586f89952228341485aa8733b2c367"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _touch_missing(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)


def initialize_celia_compatibility(
    package_root: Path,
    data_dir: Path,
    agent_workspace: Path,
) -> None:
    """Create only missing user data and install the checked Celia binary."""
    runtime_root = Path.home() / ".openclaw"
    logs_dir = runtime_root / "logs"
    celia_data = agent_workspace / "memory" / "celia_memory"
    for directory in (runtime_root, logs_dir, celia_data, data_dir / "celia" / "bin"):
        directory.mkdir(parents=True, exist_ok=True)

    _touch_missing(agent_workspace / "USER.md")
    _touch_missing(agent_workspace / "MEMORY.md")
    _touch_missing(celia_data / "celia_memory.db")
    _touch_missing(runtime_root / ".memory.log")
    _touch_missing(logs_dir / "Celia_memory.log")

    from jiuwenswarm.agents.harness.common.memory.celia.runtime_state import ensure_runtime_state

    ensure_runtime_state()

    source = package_root / "resources" / "celia" / "linux-arm64" / CELIA_BINARY_NAME
    destination = data_dir / "celia" / "bin" / CELIA_BINARY_NAME
    if not source.is_file():
        logger.warning("Celia package binary is missing: %s", source)
        return
    source_hash = _sha256(source)
    if source_hash != CELIA_BINARY_SHA256:
        raise RuntimeError(
            f"Celia package binary checksum mismatch: expected {CELIA_BINARY_SHA256}, got {source_hash}"
        )
    if not destination.is_file() or _sha256(destination) != source_hash:
        shutil.copy2(source, destination)
    if os.name != "nt":
        destination.chmod(0o755)
    logger.info("Celia compatibility runtime prepared at %s", runtime_root)
