import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jiuwenswarm.agents.harness.common.tools import send_file_to_user as sfu


@pytest.fixture(autouse=True)
def _clear_dedup_registry():
    sfu._SENT_FILE_PATHS_BY_SESSION.clear()
    yield
    sfu._SENT_FILE_PATHS_BY_SESSION.clear()


def test_partition_and_mark_sent_files():
    with patch.object(sfu, "_load_sent_file_paths_from_history", return_value=set()):
        new_paths, skipped = sfu._partition_sent_files("s1", [r"C:\tmp\a.md", r"C:\tmp\b.md"])
        assert new_paths == [r"C:\tmp\a.md", r"C:\tmp\b.md"]
        assert skipped == []

        sfu._mark_files_sent("s1", [r"C:\tmp\a.md"])
        new_paths, skipped = sfu._partition_sent_files("s1", [r"C:\tmp\a.md", r"C:\tmp\b.md"])
        assert new_paths == [r"C:\tmp\b.md"]
        assert skipped == [r"C:\tmp\a.md"]

        sfu.clear_sent_files_for_session("s1")
        new_paths, skipped = sfu._partition_sent_files("s1", [r"C:\tmp\a.md"])
        assert new_paths == [r"C:\tmp\a.md"]
        assert skipped == []


def test_partition_uses_durable_history_after_memory_cleanup():
    file_path = r"C:\tmp\handoff.md"
    history = [
        {
            "event_type": "chat.file",
            "files": [{"path": file_path, "name": "handoff.md"}],
        }
    ]

    sfu._mark_files_sent("sess-1", [file_path])
    sfu.clear_sent_files_for_session("sess-1")

    with patch(
        "jiuwenswarm.server.runtime.session.session_history.load_history_records",
        return_value=history,
    ):
        new_paths, skipped = sfu._partition_sent_files("sess-1", [file_path])

    assert new_paths == []
    assert skipped == [file_path]


def test_send_file_skips_duplicate_after_success(tmp_path):
    file_path = tmp_path / "handoff.md"
    file_path.write_text("hello", encoding="utf-8")

    toolkit = sfu.SendFileToolkit(
        request_id="r1",
        session_id="sess-1",
        channel_id="web",
    )
    mock_server = MagicMock()
    mock_server.send_push = AsyncMock()

    with patch(
        "jiuwenswarm.server.agent_ws_server.AgentWebSocketServer.get_instance",
        return_value=mock_server,
    ), patch(
        "jiuwenswarm.server.runtime.session.session_history.append_history_record",
    ), patch.object(
        sfu,
        "_load_sent_file_paths_from_history",
        return_value=set(),
    ):
        first = asyncio.run(toolkit.send_file(str(file_path)))
        second = asyncio.run(toolkit.send_file(str(file_path)))

    assert "成功发送" in first
    assert "跳过重复投递" in second
    assert mock_server.send_push.await_count == 1
