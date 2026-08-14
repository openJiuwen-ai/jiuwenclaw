"""Installation-time setup for OpenClaw-compatible Celia state directories."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

def _touch_missing(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)


def initialize_celia_compatibility(
    _package_root: Path,
    data_dir: Path,
    agent_workspace: Path,
) -> None:
    """创建缺失的 Celia 兼容数据和目录，但不携带或复制 Celia 二进制。"""
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
    logger.info(
        "Celia compatibility state prepared at %s; MCP binary must be supplied "
        "by the extension package or legacy runtime path",
        runtime_root,
    )
