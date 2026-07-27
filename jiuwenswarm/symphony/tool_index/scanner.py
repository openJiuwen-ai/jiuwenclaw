"""ToolScanner — collect registered ToolCards and convert to ScannedItem for tree indexing.

Because ToolCards are already structured (name + description + JSON Schema),
the "fingerprint" stage is deterministic — no LLM extraction needed.

.. note::
    This module deliberately does NOT import from ``symphony.indexing`` at the
    module level because the indexing package is vendored and requires
    ``dispatch_import_path`` sys.path injection before it can be resolved.
    The ``ScannedItem`` class is defined locally with the same shape.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# =========================================================================
# Local ScannedItem — same shape as indexing.scanners.base.ScannedItem
# but without the vendored import dependency.
# =========================================================================


@dataclass
class ScannedItem:
    """Normalized scanned item used by tree/catalog builders."""

    id: str
    name: str
    description: str
    item_path: str
    content: str = ""
    github_url: str = ""
    stars: int = 0
    is_official: bool = False
    author: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "skill_path": self.item_path,
            "path": self.item_path,
            "content": self.content,
            "github_url": self.github_url,
            "stars": self.stars,
            "is_official": self.is_official,
            "author": self.author,
        }


# =========================================================================
# ToolInventory
# =========================================================================


@dataclass
class ToolInventory:
    """Snapshot of currently registered tools for incremental build detection."""

    items: list[dict[str, Any]] = field(default_factory=list)
    count: int = 0
    fingerprint: str = ""
    item_paths: list[str] = field(default_factory=list)

    def to_state_payload(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "fingerprint": self.fingerprint,
            "item_paths": self.item_paths,
        }


# =========================================================================
# ToolScanner
# =========================================================================


class ToolScanner:
    """Collect ToolCards from a runtime dict and produce ScannedItem records.

    Each ToolCard already carries ``name``, ``description``, and
    ``input_params`` (JSON Schema), so the scanner only needs to serialise them
    into ``ScannedItem.content`` — no LLM extraction required.

    The output matches the contract expected by the indexing pipeline's
    ``IndexBuilder.build()`` and ``TreeBuilder``.
    """

    def __init__(
        self,
        tool_cards: dict[str, Any] | None = None,
        *,
        enabled_tool_names: set[str] | frozenset[str] | None = None,
    ) -> None:
        self._tool_cards: dict[str, Any] = dict(tool_cards or {})
        self._enabled_tool_names: frozenset[str] = frozenset(
            enabled_tool_names or ()
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scan(self) -> list[ScannedItem]:
        """Return every registered ToolCard as a ScannedItem.

        Filters to *enabled_tool_names* when the set is non-empty.
        """
        items: list[ScannedItem] = []
        for name, card in sorted(
            self._tool_cards.items(), key=lambda kv: kv[0].lower()
        ):
            if self._enabled_tool_names and name not in self._enabled_tool_names:
                continue
            item = self._tool_card_to_scanned_item(name, card)
            if item is not None:
                items.append(item)
        return items

    def scan_inventory(self) -> ToolInventory:
        """Build a fingerprintable inventory for incremental build decisions."""
        items = self.scan()
        item_paths = sorted(item.item_path for item in items)
        fp_payload = {
            "items": [
                {
                    "id": item.id,
                    "name": item.name,
                    "description": item.description,
                }
                for item in items
            ]
        }
        fingerprint = hashlib.sha256(
            json.dumps(
                fp_payload, ensure_ascii=False, sort_keys=True
            ).encode("utf-8")
        ).hexdigest()
        return ToolInventory(
            items=[item.to_dict() for item in items],
            count=len(items),
            fingerprint=fingerprint,
            item_paths=item_paths,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _tool_card_to_scanned_item(
        name: str, card: Any
    ) -> ScannedItem | None:
        """Convert one ToolCard into a ScannedItem."""
        try:
            description = str(getattr(card, "description", "") or "").strip()
            input_params = getattr(card, "input_params", None) or {}
            params_json = json.dumps(
                input_params, ensure_ascii=False, indent=2
            )
        except Exception:
            return None

        if not name.strip():
            return None

        return ScannedItem(
            id=name,
            name=name,
            description=description or name,
            item_path=f"tool://{name}",
            content=(
                f"Tool: {name}\n"
                f"Description: {description or '(no description)'}\n"
                f"Parameters:\n{params_json}"
            ),
        )
