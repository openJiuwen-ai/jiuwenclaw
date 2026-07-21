# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""逐轮 Diff 历史查询测试。"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from jiuwenswarm.server.runtime.session.git_diff_status import (
    DiffFileEntry,
    DiffStatusService,
)
from jiuwenswarm.server.runtime.session.project_git import GitError, GitOperationError
from jiuwenswarm.server.utils.diff_service import DiffHistoryExpiredError, DiffService


@pytest.fixture(autouse=True)
def _avoid_snapshot_disk_io(monkeypatch):
    monkeypatch.setattr(DiffService, "_load_turn_snapshot", lambda self, session_id, change_set_id: None)
    monkeypatch.setattr(DiffService, "_save_turn_snapshot", lambda self, session_id, turn: None)


@pytest.fixture(autouse=True)
def _default_git_service(monkeypatch):
    service = SimpleNamespace(
        status=lambda project: SimpleNamespace(
            error=None,
            is_git=True,
            repo_root="/proj",
            branch="main",
            head="abc123",
            transient=False,
            is_dirty=False,
        ),
    )
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.session.project_git.get_project_git_service",
        lambda: service,
    )


def _ts(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


_HISTORY: list[dict] = [
    {"role": "user", "timestamp": 1784542845.0, "content": "first prompt",
     "request_id": "req-001", "id": "req-001:user"},
    {"role": "assistant", "timestamp": 1784542850.0, "content": "response1",
     "request_id": "req-001", "id": "req-001:assistant"},
    {"role": "user", "timestamp": 1784542900.0, "content": "second prompt",
     "request_id": "req-002", "id": "req-002:user"},
    {"role": "assistant", "timestamp": 1784542905.0, "content": "response2",
     "request_id": "req-002", "id": "req-002:assistant"},
]

_FILE_OPS: dict[str, list[dict]] = {
    "/proj/file_a.py": [
        {
            "action": "write",
            "timestamp": _ts(1784542850.0),
            "old_content": "line1\nline2\n",
            "new_content": "line1\nline2\nline3\n",
        },
    ],
    "/proj/file_b.py": [
        {
            "action": "write",
            "timestamp": _ts(1784542905.0),
            "old_content": "old\n",
            "new_content": "new\n",
        },
    ],
}

_PROJECT = SimpleNamespace(
    project_id="proj-1",
    project_dir="/proj",
    work_mode="code",
    name="test-project",
)


def _patch_diff_service():
    ph = patch.object(DiffService, "_read_history", return_value=_HISTORY)
    pa = patch.object(DiffService, "_read_agent_history", return_value=_FILE_OPS)
    pl = patch.object(DiffService, "_load_change_sets", return_value=[])
    ps = patch.object(DiffService, "_save_change_sets", return_value=None)
    return ph, pa, pl, ps


def _patch_diff_service_with_persistence():
    saved: list[dict] = []

    def _load(session_id):
        return list(saved)

    def _save(session_id, change_sets):
        saved.clear()
        saved.extend(change_sets)

    ph = patch.object(DiffService, "_read_history", return_value=_HISTORY)
    pa = patch.object(DiffService, "_read_agent_history", return_value=_FILE_OPS)
    pl = patch.object(DiffService, "_load_change_sets", side_effect=_load)
    ps = patch.object(DiffService, "_save_change_sets", side_effect=_save)
    return ph, pa, pl, ps


def test_get_turn_diff_returns_matching_turn():
    ph, pa, pl, ps = _patch_diff_service()
    with ph, pa, pl, ps:
        service = DiffService()
        turn = service.get_turn_diff("sess-1", turn_index=1, project_dir="/proj")
    assert turn is not None
    assert turn["turnIndex"] == 1
    assert "/proj/file_a.py" in turn["files"]
    assert "change_set_id" in turn
    assert turn["request_id"] == "req-001"
    assert turn["user_message_id"] == "req-001:user"
    assert turn["assistant_message_id"] == "req-001:assistant"
    assert turn["status"] == "completed"


def test_get_turn_diff_returns_none_for_missing_turn():
    ph, pa, pl, ps = _patch_diff_service()
    with ph, pa, pl, ps:
        service = DiffService()
        turn = service.get_turn_diff("sess-1", turn_index=99, project_dir="/proj")
    assert turn is None


def test_get_turn_diff_finds_second_turn():
    ph, pa, pl, ps = _patch_diff_service()
    with ph, pa, pl, ps:
        service = DiffService()
        turn = service.get_turn_diff("sess-1", turn_index=2, project_dir="/proj")
    assert turn is not None
    assert turn["turnIndex"] == 2
    assert "/proj/file_b.py" in turn["files"]
    assert turn["request_id"] == "req-002"


def test_get_turn_diff_by_change_set_id():
    ph, pa, pl, ps = _patch_diff_service_with_persistence()
    with ph, pa, pl, ps:
        service = DiffService()
        turns = service.get_turn_diffs("sess-1", "/proj")
        cs_id = turns[-1]["change_set_id"]
        turn = service.get_turn_diff(
            "sess-1", change_set_id=cs_id, project_dir="/proj",
        )
    assert turn is not None
    assert turn["turnIndex"] == 1
    assert turn["change_set_id"] == cs_id


def test_get_turn_diff_change_set_id_not_found():
    ph, pa, pl, ps = _patch_diff_service()
    with ph, pa, pl, ps:
        service = DiffService()
        turn = service.get_turn_diff(
            "sess-1", change_set_id="cs_nonexistent", project_dir="/proj",
        )
    assert turn is None


def test_get_turn_diff_neither_specified():
    ph, pa, pl, ps = _patch_diff_service()
    with ph, pa, pl, ps:
        service = DiffService()
        turn = service.get_turn_diff("sess-1", project_dir="/proj")
    assert turn is None


def test_change_set_id_format():
    ph, pa, pl, ps = _patch_diff_service()
    with ph, pa, pl, ps:
        service = DiffService()
        turn = service.get_turn_diff("sess-1", turn_index=1, project_dir="/proj")
    assert turn is not None
    cs_id = turn["change_set_id"]
    assert cs_id.startswith("cs_sess-1_1_")
    suffix = cs_id.split("_", 3)[-1]
    assert len(suffix) == 8
    int(suffix, 16)


def test_change_set_id_is_stable():
    ph, pa, pl, ps = _patch_diff_service_with_persistence()
    with ph, pa, pl, ps:
        service = DiffService()
        turn1 = service.get_turn_diff("sess-1", turn_index=1, project_dir="/proj")
        assert turn1 is not None
        cs_id_1 = turn1["change_set_id"]
        turn2 = service.get_turn_diff("sess-1", turn_index=1, project_dir="/proj")
        assert turn2 is not None
        assert turn2["change_set_id"] == cs_id_1


def test_change_set_id_is_stable_without_request_id():
    legacy_history = [
        {"role": "user", "timestamp": 1784542845.0, "content": "legacy prompt"},
        {"role": "assistant", "timestamp": 1784542850.0, "content": "response"},
    ]
    saved: list[dict] = []

    def _load(session_id):
        return list(saved)

    def _save(session_id, change_sets):
        saved.clear()
        saved.extend(change_sets)

    ph = patch.object(DiffService, "_read_history", return_value=legacy_history)
    pa = patch.object(DiffService, "_read_agent_history", return_value=_FILE_OPS)
    pl = patch.object(DiffService, "_load_change_sets", side_effect=_load)
    ps = patch.object(DiffService, "_save_change_sets", side_effect=_save)
    with ph, pa, pl, ps:
        service = DiffService()
        turn1 = service.get_turn_diff("legacy", turn_index=1, project_dir="/proj")
        assert turn1 is not None
        cs_id = turn1["change_set_id"]
        turn2 = service.get_turn_diff("legacy", turn_index=1, project_dir="/proj")
    assert turn2 is not None
    assert turn2["change_set_id"] == cs_id


def test_change_set_id_new_after_rewind():
    saved: list[dict] = []
    history_holder: list[list[dict]] = [list(_HISTORY)]

    def _load(session_id):
        return list(saved)

    def _save(session_id, change_sets):
        saved.clear()
        saved.extend(change_sets)

    def _read_history(session_id):
        return history_holder[0]

    ph = patch.object(DiffService, "_read_history", side_effect=_read_history)
    pa = patch.object(DiffService, "_read_agent_history", return_value=_FILE_OPS)
    pl = patch.object(DiffService, "_load_change_sets", side_effect=_load)
    ps = patch.object(DiffService, "_save_change_sets", side_effect=_save)
    with ph, pa, pl, ps:
        service = DiffService()
        turn1 = service.get_turn_diff("sess-1", turn_index=1, project_dir="/proj")
        assert turn1 is not None
        old_cs_id = turn1["change_set_id"]
        assert turn1["request_id"] == "req-001"

        history_holder[0] = [
            {"role": "user", "timestamp": 1784542846.0, "content": "rewritten prompt",
             "request_id": "req-new", "id": "req-new:user"},
            {"role": "assistant", "timestamp": 1784542850.0, "content": "response",
             "request_id": "req-new", "id": "req-new:assistant"},
            {"role": "user", "timestamp": 1784542900.0, "content": "second prompt",
             "request_id": "req-002", "id": "req-002:user"},
            {"role": "assistant", "timestamp": 1784542905.0, "content": "response2",
             "request_id": "req-002", "id": "req-002:assistant"},
        ]

        turn2 = service.get_turn_diff("sess-1", turn_index=1, project_dir="/proj")
        assert turn2 is not None
        new_cs_id = turn2["change_set_id"]

    assert old_cs_id != new_cs_id
    assert turn2["request_id"] == "req-new"
    assert turn2["user_message_id"] == "req-new:user"


def test_mark_turn_discarded_preserves_snapshot():
    saved: list[dict] = []
    snapshots: dict[str, dict] = {}

    def _load(session_id):
        return list(saved)

    def _save(session_id, change_sets):
        saved.clear()
        saved.extend(change_sets)

    def _load_snapshot(self, session_id, change_set_id):
        return snapshots.get(change_set_id)

    def _save_snapshot(self, session_id, turn):
        snapshots[turn["change_set_id"]] = dict(turn)

    ph = patch.object(DiffService, "_read_history", return_value=_HISTORY)
    pa = patch.object(DiffService, "_read_agent_history", return_value=_FILE_OPS)
    pl = patch.object(DiffService, "_load_change_sets", side_effect=_load)
    ps = patch.object(DiffService, "_save_change_sets", side_effect=_save)
    pls = patch.object(DiffService, "_load_turn_snapshot", _load_snapshot)
    pss = patch.object(DiffService, "_save_turn_snapshot", _save_snapshot)
    with ph, pa, pl, ps, pls, pss:
        service = DiffService()
        cs_id = service.mark_turn_discarded("sess-1", 1, project_dir="/proj")
        assert cs_id is not None
        turn = service.get_turn_diff("sess-1", change_set_id=cs_id, project_dir="/proj")
    assert turn is not None
    assert turn["status"] == "discarded"
    assert "/proj/file_a.py" in turn["files"]


def test_turn_diff_list_returns_summaries_with_files_without_hunks():
    ph, pa, pl, ps = _patch_diff_service()
    with ph, pa, pl, ps:
        result = DiffStatusService.get_turn_diff_list(
            project=_PROJECT, session_id="sess-1", limit=50,
        )
    assert result["project_id"] == "proj-1"
    assert result["session_id"] == "sess-1"
    assert result["repo_root"] == "/proj"
    assert result["branch"] == "main"
    assert result["base_head"] == "abc123"
    assert result["total"] == 2
    assert result["limit"] == 50
    assert result["cursor"] == 0
    assert result["next_cursor"] == 2
    assert result["has_more"] is False
    assert result["turns"][0]["turn_index"] == 2
    assert result["turns"][1]["turn_index"] == 1
    for summary in result["turns"]:
        assert "files" in summary
        assert "kind" in summary
        assert "timestamp" in summary
        assert "user_prompt_preview" in summary
        assert "stats" in summary
        for file_entry in summary["files"].values():
            assert file_entry["hunks"] == []
            assert "change_type" in file_entry
            assert "is_deleted_file" in file_entry
    assert "file_b.py" in result["turns"][0]["files"]
    assert "file_a.py" in result["turns"][1]["files"]


def test_turn_diff_list_includes_change_set_metadata():
    ph, pa, pl, ps = _patch_diff_service()
    with ph, pa, pl, ps:
        result = DiffStatusService.get_turn_diff_list(
            project=_PROJECT, session_id="sess-1", limit=50,
        )
    for summary in result["turns"]:
        assert "change_set_id" in summary
        assert summary["change_set_id"].startswith("cs_sess-1_")
        assert "request_id" in summary
        assert "assistant_message_id" in summary
        assert "user_message_id" in summary
        assert summary["status"] == "completed"
    assert result["turns"][0]["request_id"] == "req-002"
    assert result["turns"][0]["user_message_id"] == "req-002:user"
    assert result["turns"][1]["request_id"] == "req-001"
    assert result["turns"][1]["assistant_message_id"] == "req-001:assistant"


def test_turn_diff_list_respects_limit():
    ph, pa, pl, ps = _patch_diff_service()
    with ph, pa, pl, ps:
        result = DiffStatusService.get_turn_diff_list(
            project=_PROJECT, session_id="sess-1", limit=1,
        )
    assert result["total"] == 2
    assert len(result["turns"]) == 1
    assert result["turns"][0]["turn_index"] == 2
    assert result["next_cursor"] == 1
    assert result["has_more"] is True


def test_turn_diff_list_respects_cursor():
    ph, pa, pl, ps = _patch_diff_service()
    with ph, pa, pl, ps:
        result = DiffStatusService.get_turn_diff_list(
            project=_PROJECT, session_id="sess-1", limit=1, cursor=1,
        )
    assert result["total"] == 2
    assert result["cursor"] == 1
    assert result["next_cursor"] == 2
    assert result["has_more"] is False
    assert len(result["turns"]) == 1
    assert result["turns"][0]["turn_index"] == 1


def test_turn_diff_list_empty_session():
    ph = patch.object(DiffService, "_read_history", return_value=[])
    pa = patch.object(DiffService, "_read_agent_history", return_value={})
    pl = patch.object(DiffService, "_load_change_sets", return_value=[])
    ps = patch.object(DiffService, "_save_change_sets", return_value=None)
    with ph, pa, pl, ps:
        result = DiffStatusService.get_turn_diff_list(
            project=_PROJECT, session_id="empty", limit=50,
        )
    assert result["total"] == 0
    assert result["turns"] == []


def test_turn_diff_list_limit_zero_returns_all():
    ph, pa, pl, ps = _patch_diff_service()
    with ph, pa, pl, ps:
        result = DiffStatusService.get_turn_diff_list(
            project=_PROJECT, session_id="sess-1", limit=0,
        )
    assert result["limit"] == 0
    assert result["total"] == 2
    assert len(result["turns"]) == 2


def _fake_git_service(repo_root="/proj", error=None):
    return SimpleNamespace(
        status=lambda project: SimpleNamespace(
            error=error,
            is_git=error is None,
            repo_root=repo_root,
            branch="main",
            head="abc123",
            transient=False,
            is_dirty=False,
        ),
    )


def test_turn_diff_detail_returns_files_and_hunks():
    ph, pa, pl, ps = _patch_diff_service()
    with ph, pa, pl, ps, patch(
        "jiuwenswarm.server.runtime.session.project_git.get_project_git_service",
        return_value=_fake_git_service(),
    ):
        result = DiffStatusService.get_turn_diff_detail(
            project=_PROJECT, session_id="sess-1", turn_index=1,
        )
    assert result is not None
    assert result["turn_index"] == 1
    assert result["project_id"] == "proj-1"
    assert result["session_id"] == "sess-1"
    assert result["repo_root"] == "/proj"
    assert result["branch"] == "main"
    assert result["base_head"] == "abc123"
    assert "files" in result
    assert "file_a.py" in result["files"]
    file_entry = result["files"]["file_a.py"]
    assert file_entry["status"] == "modified"
    assert file_entry["change_type"] == "modified"
    assert file_entry["is_deleted_file"] is False
    assert file_entry["lines_added"] == 1
    assert file_entry["lines_removed"] == 0
    assert len(file_entry["hunks"]) > 0
    assert "change_set_id" in result
    assert result["request_id"] == "req-001"


def test_turn_diff_detail_returns_none_for_missing_turn():
    ph, pa, pl, ps = _patch_diff_service()
    with ph, pa, pl, ps, patch(
        "jiuwenswarm.server.runtime.session.project_git.get_project_git_service",
        return_value=_fake_git_service(),
    ):
        result = DiffStatusService.get_turn_diff_detail(
            project=_PROJECT, session_id="sess-1", turn_index=99,
        )
    assert result is None


def test_turn_diff_detail_by_change_set_id():
    ph, pa, pl, ps = _patch_diff_service_with_persistence()
    with ph, pa, pl, ps, patch(
        "jiuwenswarm.server.runtime.session.project_git.get_project_git_service",
        return_value=_fake_git_service(),
    ):
        list_result = DiffStatusService.get_turn_diff_list(
            project=_PROJECT, session_id="sess-1", limit=50,
        )
        cs_id = list_result["turns"][-1]["change_set_id"]
        result = DiffStatusService.get_turn_diff_detail(
            project=_PROJECT, session_id="sess-1", change_set_id=cs_id,
        )
    assert result is not None
    assert result["turn_index"] == 1
    assert result["change_set_id"] == cs_id
    assert "file_a.py" in result["files"]


def test_turn_diff_detail_falls_back_when_not_git_repository():
    ph, pa, pl, ps = _patch_diff_service()
    git_error = GitError("NOT_GIT_REPOSITORY", "not a git repository")
    with ph, pa, pl, ps, patch(
        "jiuwenswarm.server.runtime.session.project_git.get_project_git_service",
        return_value=_fake_git_service(repo_root=None, error=git_error),
    ):
        result = DiffStatusService.get_turn_diff_detail(
            project=_PROJECT, session_id="sess-1", turn_index=1,
        )
    assert result is not None
    assert result["repo_root"] == "/proj"
    assert result["branch"] is None
    assert result["base_head"] is None
    assert "file_a.py" in result["files"]
    assert result["files"]["file_a.py"]["hunks"]


def test_turn_diff_detail_falls_back_when_git_not_found():
    ph, pa, pl, ps = _patch_diff_service()
    git_error = GitError("GIT_NOT_FOUND", "git executable not found")
    with ph, pa, pl, ps, patch(
        "jiuwenswarm.server.runtime.session.project_git.get_project_git_service",
        return_value=_fake_git_service(repo_root=None, error=git_error),
    ):
        result = DiffStatusService.get_turn_diff_detail(
            project=_PROJECT, session_id="sess-1", turn_index=1,
        )
    assert result is not None
    assert result["repo_root"] == "/proj"
    assert result["branch"] is None
    assert result["base_head"] is None
    assert "file_a.py" in result["files"]


def test_diff_status_falls_back_to_last_turn_when_not_git_repository():
    ph, pa, pl, ps = _patch_diff_service()
    git_error = GitError("NOT_GIT_REPOSITORY", "not a git repository")
    with ph, pa, pl, ps, patch(
        "jiuwenswarm.server.runtime.session.project_git.get_project_git_service",
        return_value=_fake_git_service(repo_root=None, error=git_error),
    ):
        result = DiffStatusService.get_project_diff_status(
            project=_PROJECT,
            session_id="sess-1",
            include_files=True,
            include_hunks=True,
        ).to_dict(include_hunks=True)
    assert result["repo"]["is_git"] is False
    assert result["repo"]["repo_root"] == "/proj"
    assert result["repo"]["branch"] is None
    assert result["current"] is None
    assert result["last_turn"] is not None
    assert "file_b.py" in result["last_turn"]["files"]


def test_diff_status_falls_back_to_last_turn_when_git_not_found():
    ph, pa, pl, ps = _patch_diff_service()
    git_error = GitError("GIT_NOT_FOUND", "git executable not found")
    with ph, pa, pl, ps, patch(
        "jiuwenswarm.server.runtime.session.project_git.get_project_git_service",
        return_value=_fake_git_service(repo_root=None, error=git_error),
    ):
        result = DiffStatusService.get_project_diff_status(
            project=_PROJECT,
            session_id="sess-1",
            include_files=True,
            include_hunks=True,
        ).to_dict(include_hunks=True)
    assert result["repo"]["is_git"] is False
    assert result["repo"]["repo_root"] == "/proj"
    assert result["repo"]["branch"] is None
    assert result["repo"]["head"] is None
    assert result["current"] is None
    assert result["last_turn"] is not None
    assert "file_b.py" in result["last_turn"]["files"]


def test_turn_diff_list_falls_back_when_not_git_repository():
    ph, pa, pl, ps = _patch_diff_service()
    git_error = GitError("NOT_GIT_REPOSITORY", "not a git repository")
    with ph, pa, pl, ps, patch(
        "jiuwenswarm.server.runtime.session.project_git.get_project_git_service",
        return_value=_fake_git_service(repo_root=None, error=git_error),
    ):
        result = DiffStatusService.get_turn_diff_list(
            project=_PROJECT, session_id="sess-1", limit=50,
        )
    assert result["project_id"] == "proj-1"
    assert result["session_id"] == "sess-1"
    assert result["repo_root"] == "/proj"
    assert result["branch"] is None
    assert result["base_head"] is None
    assert result["total"] == 2
    assert len(result["turns"]) == 2
    assert "file_b.py" in result["turns"][0]["files"]
    assert "file_a.py" in result["turns"][1]["files"]


def test_turn_diff_detail_respects_include_flags():
    ph, pa, pl, ps = _patch_diff_service()
    with ph, pa, pl, ps:
        result = DiffStatusService.get_turn_diff_detail(
            project=_PROJECT,
            session_id="sess-1",
            turn_index=1,
            include_files=True,
            include_hunks=False,
        )
    assert result is not None
    assert "file_a.py" in result["files"]
    assert result["files"]["file_a.py"]["hunks"] == []


def test_turn_diff_detail_can_omit_files():
    ph, pa, pl, ps = _patch_diff_service()
    with ph, pa, pl, ps:
        result = DiffStatusService.get_turn_diff_detail(
            project=_PROJECT,
            session_id="sess-1",
            turn_index=1,
            include_files=False,
            include_hunks=False,
        )
    assert result is not None
    assert result["files"] == {}


def test_turn_diff_detail_rejects_transient_git_state(monkeypatch):
    service = SimpleNamespace(
        status=lambda project: SimpleNamespace(
            error=None,
            repo_root="/proj",
            branch="main",
            head="abc123",
            transient=True,
        ),
    )
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.session.project_git.get_project_git_service",
        lambda: service,
    )
    ph, pa, pl, ps = _patch_diff_service()
    with ph, pa, pl, ps:
        with pytest.raises(GitOperationError) as excinfo:
            DiffStatusService.get_turn_diff_detail(
                project=_PROJECT, session_id="sess-1", turn_index=1,
            )
    assert excinfo.value.git_error.code == "GIT_TRANSIENT_STATE"


def test_get_turn_diff_change_set_orphan_snapshot_is_expired(monkeypatch):
    snapshots = {
        "cs_old": {
            "turnIndex": 1,
            "change_set_id": "cs_old",
            "request_id": "req-old",
            "files": {},
            "stats": {"filesChanged": 0, "linesAdded": 0, "linesRemoved": 0},
        }
    }
    monkeypatch.setattr(
        DiffService,
        "_load_turn_snapshot",
        lambda self, session_id, change_set_id: snapshots.get(change_set_id),
    )
    ph, pa = (
        patch.object(DiffService, "_read_history", return_value=_HISTORY),
        patch.object(DiffService, "_read_agent_history", return_value=_FILE_OPS),
    )
    pl = patch.object(DiffService, "_load_change_sets", return_value=[])
    ps = patch.object(DiffService, "_save_change_sets", return_value=None)
    with ph, pa, pl, ps:
        service = DiffService()
        with pytest.raises(DiffHistoryExpiredError):
            service.get_turn_diff("sess-1", change_set_id="cs_old", project_dir="/proj")


def test_diff_file_entry_serializes_is_untracked():
    entry = DiffFileEntry(file_path="new.txt", is_untracked=True, is_new_file=True)
    assert entry.to_dict()["is_untracked"] is True
    assert entry.to_dict()["change_type"] == "modified"


def test_diff_file_entry_serializes_deleted_contract_fields():
    entry = DiffFileEntry(
        file_path="old.txt", status="deleted", is_deleted_file=True,
    )
    data = entry.to_dict()
    assert data["status"] == "deleted"
    assert data["change_type"] == "deleted"
    assert data["is_deleted_file"] is True


def test_turn_diff_persists_historical_repo_context():
    saved: list[dict] = []

    def _load(session_id):
        return list(saved)

    def _save(session_id, change_sets):
        saved.clear()
        saved.extend(change_sets)

    ph = patch.object(DiffService, "_read_history", return_value=_HISTORY)
    pa = patch.object(DiffService, "_read_agent_history", return_value=_FILE_OPS)
    pl = patch.object(DiffService, "_load_change_sets", side_effect=_load)
    ps = patch.object(DiffService, "_save_change_sets", side_effect=_save)
    with ph, pa, pl, ps:
        service = DiffService()
        turn = service.get_turn_diff(
            "sess-1",
            turn_index=1,
            project_dir="/proj",
            repo_context={
                "repo_root": "/proj",
                "branch": "feature/a",
                "base_head": "old-head",
            },
        )
        assert turn is not None
        cs_id = turn["change_set_id"]

        turn_after_branch_switch = service.get_turn_diff(
            "sess-1",
            change_set_id=cs_id,
            project_dir="/proj",
            repo_context={
                "repo_root": "/proj",
                "branch": "main",
                "base_head": "new-head",
            },
        )

    assert turn_after_branch_switch is not None
    assert turn_after_branch_switch["branch"] == "feature/a"
    assert turn_after_branch_switch["base_head"] == "old-head"


def test_parse_git_porcelain_status_maps_file_states():
    output = "\n".join(
        [
            " M modified.py",
            "A  added.py",
            "D  deleted.py",
            " D missing.py",
            "R  old.py -> renamed.py",
            "?? untracked.py",
        ]
    )

    assert DiffService._parse_git_porcelain_status(output) == {
        "modified.py": "modified",
        "added.py": "added",
        "deleted.py": "deleted",
        "missing.py": "missing",
        "renamed.py": "renamed",
        "untracked.py": "added",
    }
