from jiuwenswarm.server.wire_truncate import _HISTORY_RESTORABLE_ASSISTANT_EVENT_TYPES


def test_subagent_roster_updates_are_restorable_history_events() -> None:
    assert "chat.subtask_update" in _HISTORY_RESTORABLE_ASSISTANT_EVENT_TYPES
