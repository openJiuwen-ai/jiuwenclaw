# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""按租户路径幂等安装/切换 openjiuwen 持久化 checkpointer."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from jiuwenclaw.utils import get_checkpoint_dir

logger = logging.getLogger(__name__)

_checkpointers: dict[str, Any] = {}
_lock = asyncio.Lock()


async def ensure_persistent_checkpointer(
    service_id: str | None = None,
    agent_id: str | None = None,
) -> None:
    """Install or switch to the persistence checkpointer for the given tenant."""
    try:
        from openjiuwen.core.session.checkpointer import CheckpointerFactory
        from openjiuwen.core.session.checkpointer.checkpointer import CheckpointerConfig
        from openjiuwen.core.session.checkpointer.persistence import PersistenceCheckpointerProvider

        checkpoint_dir = get_checkpoint_dir(service_id, agent_id)
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        key = str(checkpoint_dir.resolve())

        async with _lock:
            if key not in _checkpointers:
                PersistenceCheckpointerProvider()
                checkpointer = await CheckpointerFactory.create(
                    CheckpointerConfig(
                        type="persistence",
                        conf={
                            "db_type": "sqlite",
                            "db_path": f"{checkpoint_dir}/checkpoint",
                        },
                    )
                )
                _checkpointers[key] = checkpointer
                logger.info(
                    "[checkpoint_setup] installed persistence checkpointer: %s",
                    checkpoint_dir,
                )
            CheckpointerFactory.set_default_checkpointer(_checkpointers[key])
    except Exception as exc:
        logger.error(
            "[checkpoint_setup] fail to setup checkpoint (service_id=%s, agent_id=%s): %s",
            service_id,
            agent_id,
            exc,
        )
