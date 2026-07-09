# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""CSPL Sentinel rail provider for swarm team member assembly."""

from __future__ import annotations

import logging
from typing import Any

from openjiuwen.agent_teams.harness.manifest import (
    ElementKind,
    harness_element,
)

logger = logging.getLogger(__name__)

CSPL_SENTINEL = "swarm.cspl_sentinel"


@harness_element(
    kind=ElementKind.RAIL,
    name=CSPL_SENTINEL,
    description="CSPL tool input/output security scanning (skipped when cspl.enabled is false).",
)
def build_cspl_sentinel_rail(params: dict[str, Any], context: Any) -> Any:
    """Build CsplSentinelRail when CSPL is enabled in config."""
    try:
        from jiuwenswarm.agents.harness.common.rails.cspl import CsplConfig, CsplSentinelRail

        cfg = CsplConfig.load()
        if not cfg.enabled:
            return None
        return CsplSentinelRail(cfg)
    except Exception as exc:
        logger.warning("[swarm.cspl_sentinel] create failed: %s", exc)
        return None


__all__ = ["CSPL_SENTINEL", "build_cspl_sentinel_rail"]
