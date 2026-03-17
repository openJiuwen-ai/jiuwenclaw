# -*- coding: UTF-8 -*-

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Literal

OperationType = Literal["ADD", "UPDATE", "TAG", "REMOVE"]


@dataclass
class DeltaOperation:
    """Single mutation to apply to the playbook."""

    type: OperationType
    section: str
    content: Optional[str] = None
    bullet_id: Optional[str] = None
    metadata: Dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_json(cls, payload: Dict[str, object]) -> "DeltaOperation":
        return cls(
            type=str(payload["type"]),
            section=str(payload.get("section", "")),
            content=payload.get("content") and str(payload["content"]),
            bullet_id=payload.get("bullet_id")
            and str(payload.get("bullet_id")),  # type: ignore[arg-type]
            metadata={
                str(k): int(v) for k, v in (payload.get("metadata") or {}).items()
            },
        )

    def to_json(self) -> Dict[str, object]:
        data: Dict[str, object] = {"type": self.type, "section": self.section}
        if self.content is not None:
            data["content"] = self.content
        if self.bullet_id is not None:
            data["bullet_id"] = self.bullet_id
        if self.metadata:
            data["metadata"] = self.metadata
        return data


@dataclass
class DeltaBatch:
    """Bundle of curator reasoning and operations."""

    reasoning: str
    operations: List[DeltaOperation] = field(default_factory=list)

    @classmethod
    def from_json(cls, payload: Dict[str, object]) -> "DeltaBatch":
        ops_payload = payload.get("operations")
        operations = []
        if isinstance(ops_payload, Iterable):
            for item in ops_payload:
                if isinstance(item, dict):
                    operations.append(DeltaOperation.from_json(item))
        return cls(reasoning=str(payload.get("reasoning", "")), operations=operations)

    def to_json(self) -> Dict[str, object]:
        return {
            "reasoning": self.reasoning,
            "operations": [op.to_json() for op in self.operations],
        }


@dataclass
class Bullet:
    """Single playbook entry."""

    id: str
    section: str
    content: str
    helpful: int = 0
    harmful: int = 0
    neutral: int = 0
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def apply_metadata(self, metadata: Dict[str, int]) -> None:
        for key, value in metadata.items():
            if hasattr(self, key):
                setattr(self, key, int(value))

    def tag(self, tag: str, increment: int = 1) -> None:
        if tag not in ("helpful", "harmful", "neutral"):
            raise ValueError(f"Unsupported tag: {tag}")
        current = getattr(self, tag)
        setattr(self, tag, current + increment)
        self.updated_at = datetime.now(timezone.utc).isoformat()


@dataclass
class BulletTag:
    id: str
    tag: str


class Playbook:
    """Structured context store as defined by ACE."""

    def __init__(self) -> None:
        self._bullets: Dict[str, Bullet] = {}
        self._sections: Dict[str, List[str]] = {}
        self._next_id = 0

    # ------------------------------------------------------------------ #
    # CRUD utils
    # ------------------------------------------------------------------ #
    def add_bullet(
        self,
        section: str,
        content: str,
        bullet_id: Optional[str] = None,
        metadata: Optional[Dict[str, int]] = None,
    ) -> Bullet:
        bullet_id = bullet_id or self._generate_id(section)
        metadata = metadata or {}
        bullet = Bullet(id=bullet_id, section=section, content=content)
        bullet.apply_metadata(metadata)
        self._bullets[bullet_id] = bullet
        self._sections.setdefault(section, []).append(bullet_id)
        return bullet

    def update_bullet(
        self,
        bullet_id: str,
        *,
        content: Optional[str] = None,
        metadata: Optional[Dict[str, int]] = None,
    ) -> Optional[Bullet]:
        bullet = self._bullets.get(bullet_id)
        if bullet is None:
            return None
        if content is not None:
            bullet.content = content
        if metadata:
            bullet.apply_metadata(metadata)
        bullet.updated_at = datetime.now(timezone.utc).isoformat()
        return bullet

    def tag_bullet(self, bullet_id: str, tag: str, increment: int = 1) -> Optional[Bullet]:
        bullet = self._bullets.get(bullet_id)
        if bullet is None:
            return None
        bullet.tag(tag, increment=increment)
        return bullet

    def remove_bullet(self, bullet_id: str) -> None:
        bullet = self._bullets.pop(bullet_id, None)
        if bullet is None:
            return
        section_list = self._sections.get(bullet.section)
        if section_list:
            self._sections[bullet.section] = [
                bid for bid in section_list if bid != bullet_id
            ]
            if not self._sections[bullet.section]:
                del self._sections[bullet.section]

    def get_bullet(self, bullet_id: str) -> Optional[Bullet]:
        return self._bullets.get(bullet_id)

    def bullets(self) -> List[Bullet]:
        return list(self._bullets.values())

    def bullet_ids(self) -> List[str]:
        """Return all bullet IDs."""
        return list(self._bullets.keys())

    def load_bullet(self, bullet: Bullet) -> None:
        """Load a bullet directly (for deserialization)."""
        self._bullets[bullet.id] = bullet
        self._sections.setdefault(bullet.section, []).append(bullet.id)

    def set_next_id(self, next_id: int) -> None:
        """Set the next ID counter (for deserialization)."""
        self._next_id = next_id

    # ------------------------------------------------------------------ #
    # Serialization
    # ------------------------------------------------------------------ #
    def to_dict(self) -> Dict[str, object]:
        return {
            "bullets": {bullet_id: asdict(bullet) for bullet_id, bullet in self._bullets.items()},
            "sections": self._sections,
            "next_id": self._next_id,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, object]) -> "Playbook":
        instance = cls()
        bullets_payload = payload.get("bullets", {})
        if isinstance(bullets_payload, dict):
            for bullet_id, bullet_value in bullets_payload.items():
                if isinstance(bullet_value, dict):
                    instance._bullets[bullet_id] = Bullet(**bullet_value)
        sections_payload = payload.get("sections", {})
        if isinstance(sections_payload, dict):
            instance._sections = {
                section: list(ids) if isinstance(ids, Iterable) else []
                for section, ids in sections_payload.items()
            }
        instance._next_id = int(payload.get("next_id", 0))
        return instance

    def dumps(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def loads(cls, data: str) -> "Playbook":
        payload = json.loads(data)
        if not isinstance(payload, dict):
            raise ValueError("Playbook serialization must be a JSON object.")
        return cls.from_dict(payload)

    # ------------------------------------------------------------------ #
    # Delta application
    # ------------------------------------------------------------------ #
    def apply_delta(self, delta: DeltaBatch) -> None:
        for operation in delta.operations:
            self._apply_operation(operation)

    def _apply_operation(self, operation: DeltaOperation) -> None:
        op_type = operation.type.upper()
        if op_type == "ADD":
            self.add_bullet(
                section=operation.section,
                content=operation.content or "",
                bullet_id=operation.bullet_id,
                metadata=operation.metadata,
            )
        elif op_type == "UPDATE":
            if operation.bullet_id is None:
                return
            self.update_bullet(
                operation.bullet_id,
                content=operation.content,
                metadata=operation.metadata,
            )
        elif op_type == "TAG":
            if operation.bullet_id is None:
                return
            for tag, increment in operation.metadata.items():
                self.tag_bullet(operation.bullet_id, tag, increment)
        elif op_type == "REMOVE":
            if operation.bullet_id is None:
                return
            self.remove_bullet(operation.bullet_id)

    # ------------------------------------------------------------------ #
    # Presentation helpers
    # ------------------------------------------------------------------ #
    def as_prompt(self) -> str:
        """Return a human-readable playbook string for prompting LLMs."""
        parts: List[str] = []
        for section, bullet_ids in sorted(self._sections.items()):
            parts.append(f"## {section}")
            for bullet_id in bullet_ids:
                bullet = self._bullets[bullet_id]
                counters = (
                    f"(helpful={bullet.helpful}, harmful={bullet.harmful}, neutral={bullet.neutral})"
                )
                parts.append(f"- [{bullet.id}] {bullet.content} {counters}")
        return "\n".join(parts)

    def stats(self) -> Dict[str, object]:
        return {
            "sections": len(self._sections),
            "bullets": len(self._bullets),
            "tags": {
                "helpful": sum(b.helpful for b in self._bullets.values()),
                "harmful": sum(b.harmful for b in self._bullets.values()),
                "neutral": sum(b.neutral for b in self._bullets.values()),
            },
        }
    
    def make_playbook_excerpt(self, bullet_ids: List[str]) -> str:
        lines: List[str] = []
        seen = set()
        for bullet_id in bullet_ids:
            if bullet_id in seen:
                continue
            bullet = self.get_bullet(bullet_id)
            if bullet:
                seen.add(bullet_id)
                lines.append(f"[{bullet.id}] {bullet.content}")
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    def _generate_id(self, section: str) -> str:
        self._next_id += 1
        if section and section.strip():
            section_prefix = section.split()[0].lower()
        else:
            section_prefix = "general"
        return f"{section_prefix}-{self._next_id:05d}"