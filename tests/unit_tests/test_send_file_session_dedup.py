import asyncio
from unittest.mock import patch

import pytest

from jiuwenswarm.agents.harness.common.tools import send_file_to_user as sfu


@pytest.fixture(autouse=True)
def _clear_dedup_registry():
    sfu._SENT_FILE_PATHS_BY_SESSION.clear()
    yield
    sfu._SENT_FILE_PATHS_BY_SESSION.clear()


def test_partition_and_mark_sent_files():
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


def test_send_file_skips_duplicate_after_success(tmp_path):
    file_path = tmp_path / "handoff.md"
    file_path.write_text("hello", encoding="utf-8")

    toolkit = sfu.SendFileToolkit(
        request_id="r1",
        session_id="sess-1",
        channel_id="web",
    )
    pushed: list[dict] = []

    async def _push(message: dict) -> bool:
        pushed.append(message)
        return True

    with patch.object(sfu, "send_runtime_push", _push), patch(
        "jiuwenswarm.server.runtime.session.session_history.append_history_record",
    ):
        first = asyncio.run(toolkit.send_file(str(file_path)))
        second = asyncio.run(toolkit.send_file(str(file_path)))

    assert "成功发送" in first
    assert "跳过重复投递" in second
    assert len(pushed) == 1


def test_history_failure_after_push_does_not_duplicate_delivery(tmp_path):
    file_path = tmp_path / "delivered.md"
    file_path.write_text("hello", encoding="utf-8")
    toolkit = sfu.SendFileToolkit(
        request_id="r-history",
        session_id="sess-history",
        channel_id="web",
    )
    pushed: list[dict] = []

    async def _push(message: dict) -> bool:
        pushed.append(message)
        return True

    with patch.object(sfu, "send_runtime_push", _push), patch(
        "jiuwenswarm.server.runtime.session.session_history.append_history_record",
        side_effect=OSError("history unavailable"),
    ):
        first = asyncio.run(toolkit.send_file(str(file_path)))
        second = asyncio.run(toolkit.send_file(str(file_path)))

    assert "成功发送" in first
    assert "跳过重复投递" in second
    assert len(pushed) == 1
