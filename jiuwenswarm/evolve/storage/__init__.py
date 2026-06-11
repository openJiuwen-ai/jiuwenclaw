# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Evolution storage backends (SQLite + file system)."""

from jiuwenswarm.evolve.storage.base import EvolutionStore
from jiuwenswarm.evolve.storage.file_store import FileStore
from jiuwenswarm.evolve.storage.sqlite_store import SqliteStore

__all__ = ["EvolutionStore", "FileStore", "SqliteStore", "create_evolution_store"]


def create_evolution_store(config: dict | None = None) -> EvolutionStore:
    """Factory: build an EvolutionStore from evolve config.

    Args:
        config: Optional full resolved config dict (for telemetry path).
            If None, loads via :func:`jiuwenswarm.evolve.get_evolve_config`.

    Returns:
        An EvolutionStore wired to both SQLite and file system backends.
    """
    from pathlib import Path

    from jiuwenswarm.common.utils import get_user_workspace_dir
    from jiuwenswarm.evolve import get_evolve_config

    evolve_cfg = get_evolve_config()
    storage_cfg = evolve_cfg.get("storage", {})

    workspace_dir = get_user_workspace_dir()
    sqlite_path = storage_cfg.get("sqlite_path", "evolution.db")
    file_path = storage_cfg.get("file_path", "evolution")

    # Resolve relative paths against workspace data dir
    if not Path(sqlite_path).is_absolute():
        sqlite_path = str(workspace_dir / sqlite_path)
    if not Path(file_path).is_absolute():
        file_path = str(workspace_dir / "agent" / "workspace" / file_path)

    # Resolve traces.db path from telemetry config (or default)
    traces_db_path = "traces.db"
    if config is not None:
        telemetry_cfg = config.get("telemetry", {})
        traces_db_path = telemetry_cfg.get("sqlite_db_path", "traces.db")
    if not Path(traces_db_path).is_absolute():
        traces_db_path = str(workspace_dir / traces_db_path)

    sqlite = SqliteStore(db_path=sqlite_path, traces_db_path=traces_db_path)
    file_store = FileStore(root_dir=file_path)

    return EvolutionStore(sqlite_backend=sqlite, file_backend=file_store)
