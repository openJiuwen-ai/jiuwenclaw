"""Base detector with lazy DB reload via MAX(updated_at) check (async)."""

from __future__ import annotations

import re
import threading
from typing import Any

from jiuwenswarm.telemetry.audit.rule_loader import get_last_updated, get_rules_for_detector


class BaseDetector:
    """Base class for audit detectors — handles lazy reload from DB.

    Subclasses implement async evaluate/scan/check methods that call
    ``await self._maybe_reload()`` first, then iterate compiled patterns.
    ``_detector_type`` specifies which detector column to filter on.
    """

    _detector_type: str = ""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._compiled: list[tuple[dict[str, Any], re.Pattern]] = []
        self._last_updated: str | None = None
        # Rules loaded lazily on first evaluate call

    async def _maybe_reload(self) -> None:
        """Check if rules changed; reload if so. Called on each evaluate."""
        ts = await get_last_updated()
        if ts != self._last_updated:
            await self.reload()

    async def reload(self) -> None:
        """Load rules from DB and compile regexes."""
        rules = await get_rules_for_detector(self._detector_type)
        with self._lock:
            self._compiled = []
            for r in rules:
                try:
                    pattern = re.compile(r["pattern"], re.IGNORECASE)
                    self._compiled.append((r, pattern))
                except re.error:
                    pass
            self._last_updated = await get_last_updated()
