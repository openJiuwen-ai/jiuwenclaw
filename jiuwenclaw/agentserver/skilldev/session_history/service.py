from __future__ import annotations

from pathlib import Path
from typing import Any

from jiuwenclaw.agentserver.skilldev.schema import SkillDevState
from jiuwenclaw.agentserver.skilldev.session_history.assembler import (
    build_restore_payload,
    build_session_summary,
)
from jiuwenclaw.agentserver.skilldev.session_history.store import SkillDevSessionHistoryStore
from jiuwenclaw.agentserver.skilldev.store import StateStore


class SkillDevSessionHistoryService:
    def __init__(self, *, base_dir: Path, state_store: StateStore) -> None:
        self._store = SkillDevSessionHistoryStore(base_dir)
        self._state_store = state_store

    def append_user_start(self, *, task_id: str, payload: dict[str, Any]) -> None:
        self._store.append_event(
            task_id=task_id,
            source="user",
            event_type="skilldev.user_start",
            payload=payload,
        )

    def append_user_respond(self, *, task_id: str, payload: dict[str, Any]) -> None:
        self._store.append_event(
            task_id=task_id,
            source="user",
            event_type="skilldev.user_respond",
            payload=payload,
        )

    def append_agent_event(self, *, task_id: str, event_type: str, payload: dict[str, Any]) -> None:
        self._store.append_event(
            task_id=task_id,
            source="assistant",
            event_type=event_type,
            payload=payload,
        )

    def save_state_snapshot(self, *, task_id: str, state: SkillDevState) -> None:
        self._store.save_snapshot(task_id, state.to_checkpoint_dict())

    def list_sessions(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for task_id in self._state_store.list_tasks():
            state = self._state_store.load_state_sync(task_id)
            if state is None:
                continue
            summary = build_session_summary(task_id, state.to_checkpoint_dict())
            out.append(summary.to_dict())
        out.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        return out

    def restore_session(self, task_id: str) -> dict[str, Any] | None:
        state = self._state_store.load_state_sync(task_id)
        if state is None:
            return None
        snapshot = self._store.load_snapshot(task_id)
        state_dict = snapshot if isinstance(snapshot, dict) else state.to_checkpoint_dict()
        events = self._store.list_events(task_id)
        return build_restore_payload(task_id=task_id, state_dict=state_dict, events=events)
