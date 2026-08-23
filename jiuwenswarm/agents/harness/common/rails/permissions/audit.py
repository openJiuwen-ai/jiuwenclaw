# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Observe-only audit logging for auto permissions."""

from __future__ import annotations

import json
import logging
from typing import Any

from jiuwenswarm.agents.harness.common.rails.permissions.tool_decision_facts import (
    ToolDecisionFacts,
)

logger = logging.getLogger(__name__)


def emit_permission_audit(
    facts: ToolDecisionFacts,
    *,
    decision: str,
    reason: str,
    degraded: bool,
    grant_id: str = "",
    grant_reason: str = "",
    extra: dict[str, object] | None = None,
    persistent_writer: Any | None = None,
) -> Any | None:
    """Emit an auto-permission audit event without raw tool arguments."""
    event = {
        "decision": decision,
        "degraded": degraded,
        "accesses_known": facts.accesses_known,
        "path_counts": {
            "external": len(facts.external_paths),
            "read": len(facts.read_paths),
            "write": len(facts.write_paths),
        },
        "reason": reason,
        "risk_tier": facts.capability.risk_tier,
        "side_effects": sorted(facts.capability.static_side_effects),
        "tool_category": facts.tool_category,
        "tool_name": facts.tool_name,
    }
    if grant_id:
        event["grant_id"] = grant_id
    if grant_reason:
        event["grant_reason"] = grant_reason
    if extra:
        event.update(extra)
    logger.info(
        "permission_audit %s", json.dumps(event, sort_keys=True, separators=(",", ":"))
    )
    if persistent_writer is None:
        return None
    return persistent_writer.write(
        facts,
        decision=decision,
        reason=reason,
        degraded=degraded,
        grant_id=grant_id,
        grant_reason=grant_reason,
        extra=extra,
    )
