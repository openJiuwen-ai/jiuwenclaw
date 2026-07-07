# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""User profile data model and persistence for the proactive recommendation engine.

The profile captures what the LLM has learned about the user from their
conversations across all channels.  It is updated incrementally by the
proactive engine on each tick.

Storage: ``~/.jiuwenswarm/agent/workspace/user_profile.json``
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ── UserProfile ──────────────────────────────────────────────────


@dataclass
class UserProfile:
    """Persistent user profile maintained by the proactive engine.

    Fields are grouped by who manages them:

    * **LLM-managed** (updated via ``merge()`` from LLM output):
      ``preferences``, ``goals``, ``interests``, ``commitments``

    * **Engine-managed** (updated by the engine itself, never by LLM):
      ``recommendation_history``
    """

    # ── LLM-managed fields ──────────────────────────────────────

    preferences: list[str] = field(default_factory=list)
    """Long-term preferences: tech stack, work habits, communication style."""

    goals: list[str] = field(default_factory=list)
    """Short-term goals: tasks the user is actively working on (days-scale)."""

    interests: list[str] = field(default_factory=list)
    """Interest boundaries: areas the user might want to explore but hasn't yet."""

    commitments: list[str] = field(default_factory=list)
    """Pending tasks: things the user said they'd do but haven't done yet."""

    # ── Engine-managed fields ───────────────────────────────────

    recommendation_history: list[dict[str, Any]] = field(default_factory=list)
    """Past recommendations with type, target, reason, timestamp (max 20)."""

    cooldown_records: dict[str, float] = field(default_factory=dict)
    """Cooldown records: target -> last recommended timestamp. Persisted to survive restarts."""

    last_updated: str = ""

    # ── Merge ───────────────────────────────────────────────────

    def merge(self, delta: dict[str, Any]) -> None:
        """Apply an incremental update from the LLM.

        The LLM outputs the *complete* list for each field it wants to change.
        Fields absent from ``delta`` are left untouched.  Engine-managed fields
        (``recommendation_history``) are never modified here.
        """
        replace_fields = ("preferences", "goals", "interests", "commitments")
        for key in replace_fields:
            new_val = delta.get(key)
            if isinstance(new_val, list):
                setattr(self, key, [v for v in new_val if v])

        self.last_updated = datetime.now(timezone.utc).isoformat()

    def add_recommendation(self, rec: dict[str, Any]) -> None:
        """Append a recommendation record and cap at 20 entries."""
        self.recommendation_history.append(rec)
        if len(self.recommendation_history) > 20:
            self.recommendation_history = self.recommendation_history[-20:]


# ── File helpers ──────────────────────────────────────────────────


def _default_profile_path() -> Path:
    from jiuwenswarm.common.utils import get_agent_workspace_dir
    return get_agent_workspace_dir() / "user_profile.json"


def load_user_profile(path: Path | None = None) -> UserProfile:
    """Load profile from disk, returning an empty profile on missing/corrupt file."""
    profile_path = path or _default_profile_path()
    if not profile_path.exists() or profile_path.stat().st_size == 0:
        # Missing or empty file = fresh state, not an error.
        return UserProfile()
    try:
        data = json.loads(profile_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return UserProfile()

        return UserProfile(
            preferences=_safe_list(data, "preferences"),
            goals=_safe_list(data, "goals"),
            interests=_safe_list(data, "interests"),
            commitments=_safe_list(data, "commitments"),
            recommendation_history=_safe_list(data, "recommendation_history"),
            cooldown_records=_safe_dict(data, "cooldown_records"),
            last_updated=data.get("last_updated", ""),
        )
    except Exception as exc:
        logger.warning("[UserProfile] load failed: %s", exc)
        return UserProfile()


def _safe_list(data: dict, key: str) -> list:
    """Extract a list from JSON data, returning [] on missing/wrong type."""
    val = data.get(key, [])
    return val if isinstance(val, list) else []


def _safe_dict(data: dict, key: str) -> dict:
    """Extract a dict from JSON data, returning {} on missing/wrong type."""
    val = data.get(key, {})
    return val if isinstance(val, dict) else {}


def save_user_profile(profile: UserProfile, path: Path | None = None) -> None:
    """Persist profile to disk."""
    profile_path = path or _default_profile_path()
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        profile_path.write_text(
            json.dumps(asdict(profile), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("[UserProfile] save failed: %s", exc)
