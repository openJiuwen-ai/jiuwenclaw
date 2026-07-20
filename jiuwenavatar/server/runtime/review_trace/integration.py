"""Runtime wiring for Committer PR-review trace collection."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from openjiuwen.harness.rails import TrajectoryRail

from .store import CommitterReviewTraceStore


def _env(name: str, legacy_name: str, default: str) -> str:
    return os.getenv(name, os.getenv(legacy_name, default)).strip()


def is_committer_review_trace_enabled() -> bool:
    value = _env(
        "COMMITTER_REVIEW_TRACE_ENABLED",
        "COMMITTER_EVOLUTION_TRAJECTORY_ENABLED",
        "true",
    ).lower()
    return value not in {"0", "false", "no", "off"}


def should_collect_committer_review_trace(persona_id: str) -> bool:
    """Collection is scoped to the Committer persona and the feature flag."""

    return persona_id == "committer" and is_committer_review_trace_enabled()


def committer_review_trace_base_dir(*, avatar_id: str = "") -> Path:
    """Return the runtime-data directory; it is intentionally outside Git."""

    override = _env(
        "COMMITTER_REVIEW_TRACE_DIR",
        "COMMITTER_EVOLUTION_TRAJECTORY_DIR",
        "",
    )
    root = Path(override) if override else Path.home() / ".jiuwenavatar" / "review_traces"
    safe_avatar = _safe_segment(avatar_id)
    return root / safe_avatar if safe_avatar else root


def is_committer_review_trace_raw_enabled() -> bool:
    value = _env(
        "COMMITTER_REVIEW_TRACE_KEEP_RAW",
        "COMMITTER_EVOLUTION_KEEP_RAW_TRAJECTORY",
        "false",
    ).lower()
    return value not in {"0", "false", "no", "off"}


def build_committer_review_trace_rail(*, avatar_id: str = "") -> Any:
    base_dir = committer_review_trace_base_dir(avatar_id=avatar_id)
    store = CommitterReviewTraceStore(
        base_dir,
        review_traces_dir=base_dir / "default" / "review_traces",
        write_review_trace=True,
        save_raw=is_committer_review_trace_raw_enabled(),
        require_pr_review=True,
        redact_secrets=True,
    )
    return TrajectoryRail(trajectory_store=store)


def _safe_segment(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", (value or "").strip())
    return cleaned.strip("._")[:80]
