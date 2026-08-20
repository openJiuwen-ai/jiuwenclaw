# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for DiffService turn-diff reading / aggregation."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from jiuwenclaw.agentserver.diff_service import (
    MAX_LINES_PER_FILE,
    DiffService,
    get_diff_service,
)


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _write_file_ops(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _patch_tenant_roots(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.diff_service.get_multi_tenant_user_workspace_dir",
        lambda sid, aid: tmp_path / f"service_{sid}" / f"agent_{aid}",
    )


def _session_dir(tmp_path: Path, session_id: str, *, sid="default", aid="default") -> Path:
    session_dir = (
        tmp_path / f"service_{sid}" / f"agent_{aid}" / "agent" / "sessions" / session_id
    )
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def _workspace_hist(tmp_path: Path, *, sid="default", aid="default") -> Path:
    return (
        tmp_path
        / f"service_{sid}"
        / f"agent_{aid}"
        / "agent"
        / "jiuwenclaw_workspace"
        / ".agent_history"
    )


def test_is_valid_file_ops_file_matches_session_suffix():
    assert DiffService._is_valid_file_ops_file(
        "file_ops_jiuwenclaw_sess1_sess1.json", "sess1", require_session=True
    )
    assert DiffService._is_valid_file_ops_file(
        "file_ops_jiuwenclaw_sess1.json", "sess1", require_session=True
    )
    assert not DiffService._is_valid_file_ops_file(
        "file_ops_jiuwenclaw_other.json", "sess1", require_session=True
    )
    assert not DiffService._is_valid_file_ops_file(
        "file_ops_jiuwenclaw.json", "sess1", require_session=True
    )
    # short session_id must not substring-match unrelated files
    assert not DiffService._is_valid_file_ops_file(
        "file_ops_jiuwenclaw_sess10.json", "sess1", require_session=True
    )


def test_is_valid_file_ops_file_accepts_subagent_suffix():
    # parent session sees child agent ops: file_ops_{agent}_{session}_sub_{type}_{suffix}.json
    assert DiffService._is_valid_file_ops_file(
        "file_ops_code_sess1_sub_explore_ab12.json",
        "sess1",
        require_session=True,
    )
    # agent_id portion must not contain underscores (avoids ambiguous parses)
    assert not DiffService._is_valid_file_ops_file(
        "file_ops_jiuwen_claw_sess1_sub_explore_ab12.json",
        "sess1",
        require_session=True,
    )


def test_get_diff_service_singleton():
    a = get_diff_service()
    b = get_diff_service()
    assert a is b
    assert isinstance(a, DiffService)


def test_read_agent_history_merges_session_and_project_dir(tmp_path, monkeypatch):
    _patch_tenant_roots(monkeypatch, tmp_path)
    session_id = "sess_abc"
    workspace_hist = _workspace_hist(tmp_path)
    project_dir = tmp_path / "my_project"
    project_hist = project_dir / ".agent_history"

    # enterprise card_id style: jiuwenclaw_{session_id}
    _write_file_ops(
        workspace_hist / f"file_ops_jiuwenclaw_{session_id}_{session_id}.json",
        {
            str(tmp_path / "a.py"): [
                {
                    "timestamp": _iso(100.0),
                    "action": "write",
                    "old_content": None,
                    "new_content": "print(1)\n",
                }
            ]
        },
    )
    # legacy global file still readable
    _write_file_ops(
        workspace_hist / "file_ops_jiuwenclaw.json",
        {
            str(tmp_path / "legacy.py"): [
                {
                    "timestamp": _iso(90.0),
                    "action": "write",
                    "old_content": None,
                    "new_content": "legacy\n",
                }
            ]
        },
    )
    # project_dir session file (higher priority source when duplicates exist)
    _write_file_ops(
        project_hist / f"file_ops_jiuwenclaw_{session_id}_{session_id}.json",
        {
            str(project_dir / "b.py"): [
                {
                    "timestamp": _iso(110.0),
                    "action": "edit",
                    "old_content": "old\n",
                    "new_content": "new\n",
                }
            ]
        },
    )

    service = DiffService()
    loaded = service._read_agent_history(
        session_id,
        str(project_dir),
        service_id="default",
        agent_id="default",
    )
    paths = {Path(p).name for p in loaded}
    assert "a.py" in paths
    assert "b.py" in paths
    assert "legacy.py" in paths


def test_duplicate_ops_prefer_project_dir_source(tmp_path, monkeypatch):
    _patch_tenant_roots(monkeypatch, tmp_path)
    session_id = "sess_dup"
    shared = str(tmp_path / "shared.py")
    # Identical op in workspace (low priority) and project_dir (high priority);
    # merge should keep a single entry and prefer the project source marker.
    identical = {
        "timestamp": _iso(50.0),
        "action": "write",
        "old_content": None,
        "new_content": "same\n",
        "source": "workspace",
    }
    _write_file_ops(
        _workspace_hist(tmp_path) / f"file_ops_jiuwenclaw_{session_id}.json",
        {shared: [identical]},
    )
    _write_file_ops(
        (tmp_path / "proj" / ".agent_history")
        / f"file_ops_jiuwenclaw_{session_id}.json",
        {shared: [{**identical, "source": "project"}]},
    )

    service = DiffService()
    loaded = service._read_agent_history(
        session_id,
        str(tmp_path / "proj"),
        service_id="default",
        agent_id="default",
    )
    key = str(Path(shared).resolve())
    assert len(loaded[key]) == 1
    assert loaded[key][0].get("source") == "project"


def test_get_turn_diffs_aggregates_by_user_window(tmp_path, monkeypatch):
    _patch_tenant_roots(monkeypatch, tmp_path)
    session_id = "sess_turn"
    session_dir = _session_dir(tmp_path, session_id)

    history = [
        {"role": "user", "content": "create a.py", "timestamp": 100.0, "id": "u1"},
        {
            "role": "assistant",
            "event_type": "chat.final",
            "content": "done",
            "timestamp": 105.0,
            "id": "a1",
        },
        {"role": "user", "content": "edit a.py", "timestamp": 200.0, "id": "u2"},
        {
            "role": "assistant",
            "event_type": "chat.final",
            "content": "done",
            "timestamp": 205.0,
            "id": "a2",
        },
    ]
    (session_dir / "history.json").write_text(json.dumps(history), encoding="utf-8")
    (session_dir / "metadata.json").write_text(
        json.dumps({"project_dir": str(tmp_path / "proj")}),
        encoding="utf-8",
    )

    target = tmp_path / "proj" / "a.py"
    _write_file_ops(
        _workspace_hist(tmp_path) / f"file_ops_jiuwenclaw_{session_id}_{session_id}.json",
        {
            str(target): [
                {
                    "timestamp": _iso(102.0),
                    "action": "write",
                    "old_content": None,
                    "new_content": "hello\n",
                },
                {
                    "timestamp": _iso(202.0),
                    "action": "edit",
                    "old_content": "hello\n",
                    "new_content": "hello\nworld\n",
                },
            ]
        },
    )

    service = DiffService()
    turns = service.get_turn_diffs(
        session_id,
        service_id="default",
        agent_id="default",
    )
    assert len(turns) == 2
    # most recent first
    assert turns[0]["turnIndex"] == 2
    assert turns[0]["stats"]["filesChanged"] == 1
    assert turns[0]["stats"]["linesAdded"] >= 1
    assert turns[1]["turnIndex"] == 1
    file_info = next(iter(turns[1]["files"].values()))
    assert file_info["isNewFile"] is True
    assert file_info["hunks"]


def test_edits_after_chat_final_still_belong_to_turn(tmp_path, monkeypatch):
    """Turn end is next user message, not chat.final — late edits stay in the turn."""
    _patch_tenant_roots(monkeypatch, tmp_path)
    session_id = "sess_late"
    session_dir = _session_dir(tmp_path, session_id)
    history = [
        {"role": "user", "content": "write", "timestamp": 100.0, "id": "u1"},
        {
            "role": "assistant",
            "event_type": "chat.final",
            "content": "done",
            "timestamp": 105.0,
            "id": "a1",
        },
        {"role": "user", "content": "next", "timestamp": 200.0, "id": "u2"},
    ]
    (session_dir / "history.json").write_text(json.dumps(history), encoding="utf-8")
    target = tmp_path / "late.py"
    _write_file_ops(
        _workspace_hist(tmp_path) / f"file_ops_jiuwenclaw_{session_id}.json",
        {
            str(target): [
                {
                    # after chat.final(105) but before next user(200)
                    "timestamp": _iso(150.0),
                    "action": "write",
                    "old_content": None,
                    "new_content": "late\n",
                }
            ]
        },
    )

    turns = DiffService().get_turn_diffs(
        session_id, service_id="default", agent_id="default"
    )
    assert len(turns) == 1
    assert turns[0]["turnIndex"] == 1
    assert turns[0]["stats"]["filesChanged"] == 1


def test_turns_without_files_are_filtered(tmp_path, monkeypatch):
    _patch_tenant_roots(monkeypatch, tmp_path)
    session_id = "sess_empty_turns"
    session_dir = _session_dir(tmp_path, session_id)
    history = [
        {"role": "user", "content": "no edits", "timestamp": 100.0, "id": "u1"},
        {
            "role": "assistant",
            "event_type": "chat.final",
            "content": "ok",
            "timestamp": 110.0,
            "id": "a1",
        },
        {"role": "user", "content": "edit now", "timestamp": 200.0, "id": "u2"},
    ]
    (session_dir / "history.json").write_text(json.dumps(history), encoding="utf-8")
    _write_file_ops(
        _workspace_hist(tmp_path) / f"file_ops_jiuwenclaw_{session_id}.json",
        {
            str(tmp_path / "only.py"): [
                {
                    "timestamp": _iso(210.0),
                    "action": "write",
                    "old_content": None,
                    "new_content": "x\n",
                }
            ]
        },
    )
    turns = DiffService().get_turn_diffs(
        session_id, service_id="default", agent_id="default"
    )
    assert len(turns) == 1
    # original user-message index kept (not renumbered to 1)
    assert turns[0]["turnIndex"] == 2


def test_get_turn_diff_and_summaries(tmp_path, monkeypatch):
    _patch_tenant_roots(monkeypatch, tmp_path)
    session_id = "sess_api"
    session_dir = _session_dir(tmp_path, session_id)
    history = [
        {"role": "user", "content": "t1", "timestamp": 100.0, "id": "u1", "request_id": "r1"},
        {
            "role": "assistant",
            "event_type": "chat.final",
            "content": "ok",
            "timestamp": 110.0,
            "id": "a1",
            "request_id": "r1",
        },
    ]
    (session_dir / "history.json").write_text(json.dumps(history), encoding="utf-8")
    _write_file_ops(
        _workspace_hist(tmp_path) / f"file_ops_jiuwenclaw_{session_id}.json",
        {
            str(tmp_path / "f.py"): [
                {
                    "timestamp": _iso(105.0),
                    "action": "write",
                    "old_content": None,
                    "new_content": "abc\n",
                }
            ]
        },
    )
    service = DiffService()
    one = service.get_turn_diff(
        session_id, turn_index=1, service_id="default", agent_id="default"
    )
    assert one is not None
    assert one["turnIndex"] == 1
    assert one["request_id"] == "r1"
    assert one["assistant_message_id"] == "a1"
    assert any("hunks" in info for info in one["files"].values())

    assert (
        service.get_turn_diff(
            session_id, turn_index=99, service_id="default", agent_id="default"
        )
        is None
    )

    summaries = service.get_turn_diff_summaries(
        session_id, service_id="default", agent_id="default"
    )
    assert len(summaries) == 1
    file_summary = next(iter(summaries[0]["files"].values()))
    assert "hunks" not in file_summary
    assert file_summary["isNewFile"] is True


def test_project_dir_from_metadata_and_cwd_fallback(tmp_path, monkeypatch):
    _patch_tenant_roots(monkeypatch, tmp_path)
    session_id = "sess_meta"
    session_dir = _session_dir(tmp_path, session_id)
    sessions_root = session_dir.parent

    (session_dir / "metadata.json").write_text(
        json.dumps({"project_dir": str(tmp_path / "from_top")}),
        encoding="utf-8",
    )
    assert DiffService._get_project_dir_from_metadata(
        session_id, sessions_root=sessions_root
    ) == str(tmp_path / "from_top")

    (session_dir / "metadata.json").write_text(
        json.dumps({"channel_metadata": {"cwd": str(tmp_path / "from_cwd")}}),
        encoding="utf-8",
    )
    assert DiffService._get_project_dir_from_metadata(
        session_id, sessions_root=sessions_root
    ) == str(tmp_path / "from_cwd")

    (session_dir / "metadata.json").write_text("{}", encoding="utf-8")
    assert (
        DiffService._get_project_dir_from_metadata(
            session_id, sessions_root=sessions_root
        )
        is None
    )


def test_explicit_project_dir_overrides_metadata(tmp_path, monkeypatch):
    """内部 API 仍可显式传入 project_dir（供可信调用方 / 单测）；WS 层须先 resolve_trusted。"""
    _patch_tenant_roots(monkeypatch, tmp_path)
    session_id = "sess_override"
    session_dir = _session_dir(tmp_path, session_id)
    (session_dir / "history.json").write_text(
        json.dumps(
            [{"role": "user", "content": "x", "timestamp": 100.0, "id": "u1"}]
        ),
        encoding="utf-8",
    )
    (session_dir / "metadata.json").write_text(
        json.dumps({"project_dir": str(tmp_path / "meta_proj")}),
        encoding="utf-8",
    )
    # only the explicit project_dir has the ops
    explicit = tmp_path / "explicit_proj"
    _write_file_ops(
        explicit / ".agent_history" / f"file_ops_jiuwenclaw_{session_id}.json",
        {
            str(explicit / "only.py"): [
                {
                    "timestamp": _iso(101.0),
                    "action": "write",
                    "old_content": None,
                    "new_content": "e\n",
                }
            ]
        },
    )
    turns = DiffService().get_turn_diffs(
        session_id,
        service_id="default",
        agent_id="default",
        project_dir=str(explicit),
    )
    assert len(turns) == 1
    assert any(Path(p).name == "only.py" for p in turns[0]["files"])


def test_resolve_trusted_project_dir_rejects_unbound_and_mismatch(tmp_path, monkeypatch):
    _patch_tenant_roots(monkeypatch, tmp_path)
    session_id = "sess_trust"
    session_dir = _session_dir(tmp_path, session_id)
    sessions_root = session_dir.parent
    trusted = (tmp_path / "bound_proj").resolve()
    trusted.mkdir()
    evil = (tmp_path / "evil_proj").resolve()
    evil.mkdir()

    # 无 metadata：拒绝任意 params.project_dir
    assert (
        DiffService.resolve_trusted_project_dir(
            session_id, str(evil), sessions_root=sessions_root
        )
        is None
    )

    (session_dir / "metadata.json").write_text(
        json.dumps({"project_dir": str(trusted)}),
        encoding="utf-8",
    )
    # 匹配绑定路径 → 接受
    assert Path(
        DiffService.resolve_trusted_project_dir(
            session_id, str(trusted), sessions_root=sessions_root
        )
    ) == trusted
    # 不匹配 → 回退 metadata，不采用 evil
    assert Path(
        DiffService.resolve_trusted_project_dir(
            session_id, str(evil), sessions_root=sessions_root
        )
    ) == trusted
    # 未传 → metadata
    assert Path(
        DiffService.resolve_trusted_project_dir(
            session_id, None, sessions_root=sessions_root
        )
    ) == trusted


def test_read_agent_history_dedup_scales_with_many_entries(tmp_path, monkeypatch):
    """去重应为近似 O(n)，千级同文件条目不应退化为明显卡顿。"""
    import time

    _patch_tenant_roots(monkeypatch, tmp_path)
    session_id = "sess_dedup_scale"
    hist = _workspace_hist(tmp_path)
    target = str(tmp_path / "hot.py")
    entries = []
    for i in range(1000):
        entries.append(
            {
                "timestamp": _iso(1000.0 + i * 3),  # >2s apart → 不去重
                "action": "edit",
                "old_content": f"v{i}\n",
                "new_content": f"v{i + 1}\n",
            }
        )
    # 再塞一批与前半段重复（同内容、时间差 <2s）——应被去重掉
    for i in range(0, 500):
        entries.append(
            {
                "timestamp": _iso(1000.0 + i * 3 + 0.5),
                "action": "edit",
                "old_content": f"v{i}\n",
                "new_content": f"v{i + 1}\n",
            }
        )
    _write_file_ops(
        hist / f"file_ops_jiuwenclaw_{session_id}.json",
        {target: entries},
    )
    service = DiffService()
    started = time.perf_counter()
    loaded = service._read_agent_history(
        session_id, service_id="default", agent_id="default"
    )
    elapsed = time.perf_counter() - started
    assert len(loaded[str(Path(target).resolve())]) == 1000
    assert elapsed < 1.0  # 宽松上限；主要防 O(n^2) 回归


def test_deleted_file_flag_and_truncation():
    hunks, truncated = DiffService._compute_hunks("a\nb\n", None)
    assert truncated is False
    assert hunks[0]["newLines"] == 0
    assert all(line.startswith("-") for line in hunks[0]["lines"])

    big = "\n".join(f"line{i}" for i in range(MAX_LINES_PER_FILE + 50)) + "\n"
    hunks, truncated = DiffService._compute_hunks(None, big)
    assert truncated is True
    assert len(hunks[0]["lines"]) == MAX_LINES_PER_FILE


def test_compute_hunks_marks_new_and_deleted_and_context():
    hunks, truncated = DiffService._compute_hunks(None, "a\nb\n")
    assert truncated is False
    assert hunks[0]["lines"] == ["+a", "+b"]

    hunks, truncated = DiffService._compute_hunks("a\nb\n", None)
    assert truncated is False
    assert hunks[0]["lines"] == ["-a", "-b"]

    old = "keep\nline1\nline2\nline3\nkeep2\n"
    new = "keep\nline1\nCHANGED\nline3\nkeep2\n"
    hunks, truncated = DiffService._compute_hunks(old, new)
    assert truncated is False
    assert hunks
    # context lines should start with a space
    assert any(line.startswith(" ") for line in hunks[0]["lines"])
    assert any(line.startswith("-line2") for line in hunks[0]["lines"])
    assert any(line.startswith("+CHANGED") for line in hunks[0]["lines"])


def test_iso_to_timestamp_accepts_float_and_z_suffix():
    assert DiffService._iso_to_timestamp(123.5) == 123.5
    assert DiffService._iso_to_timestamp(10) == 10.0
    ts = DiffService._iso_to_timestamp("1970-01-01T00:00:01Z")
    assert abs(ts - 1.0) < 1e-6


def test_rewound_and_discarded_entries_hidden_by_default(tmp_path, monkeypatch):
    _patch_tenant_roots(monkeypatch, tmp_path)
    hist_dir = _workspace_hist(tmp_path)
    _write_file_ops(
        hist_dir / "file_ops_jiuwenclaw.json",
        {
            str(tmp_path / "x.py"): [
                {
                    "timestamp": _iso(1.0),
                    "action": "write",
                    "old_content": None,
                    "new_content": "x\n",
                    "rewound_out": True,
                },
                {
                    "timestamp": _iso(1.5),
                    "action": "write",
                    "old_content": None,
                    "new_content": "d\n",
                    "discarded_out": True,
                },
                {
                    "timestamp": _iso(2.0),
                    "action": "write",
                    "old_content": None,
                    "new_content": "y\n",
                },
            ]
        },
    )
    service = DiffService()
    key = str((tmp_path / "x.py").resolve())
    hidden = service._read_agent_history(service_id="default", agent_id="default")
    assert key in hidden
    assert len(hidden[key]) == 1
    shown = service._read_agent_history(
        service_id="default",
        agent_id="default",
        include_rewound=True,
    )
    assert len(shown[key]) == 3


def test_tenant_isolation_between_agents(tmp_path, monkeypatch):
    _patch_tenant_roots(monkeypatch, tmp_path)
    _write_file_ops(
        _workspace_hist(tmp_path, aid="office") / "file_ops_jiuwenclaw.json",
        {
            str(tmp_path / "office.py"): [
                {
                    "timestamp": _iso(1.0),
                    "action": "write",
                    "old_content": None,
                    "new_content": "o\n",
                }
            ]
        },
    )
    _write_file_ops(
        _workspace_hist(tmp_path, aid="other") / "file_ops_jiuwenclaw.json",
        {
            str(tmp_path / "other.py"): [
                {
                    "timestamp": _iso(1.0),
                    "action": "write",
                    "old_content": None,
                    "new_content": "x\n",
                }
            ]
        },
    )
    service = DiffService()
    office = service._read_agent_history(service_id="default", agent_id="office")
    names = {Path(p).name for p in office}
    assert "office.py" in names
    assert "other.py" not in names


def test_unsafe_session_id_returns_empty_history(tmp_path, monkeypatch):
    _patch_tenant_roots(monkeypatch, tmp_path)
    # path traversal / unsafe id must not read anything
    assert DiffService()._read_history("../evil", service_id="default", agent_id="default") == []
    assert DiffService()._read_history("a/b", service_id="default", agent_id="default") == []


def test_empty_history_or_no_ops_returns_empty(tmp_path, monkeypatch):
    _patch_tenant_roots(monkeypatch, tmp_path)
    session_id = "sess_blank"
    session_dir = _session_dir(tmp_path, session_id)
    (session_dir / "history.json").write_text("[]", encoding="utf-8")
    assert (
        DiffService().get_turn_diffs(
            session_id, service_id="default", agent_id="default"
        )
        == []
    )

    (session_dir / "history.json").write_text(
        json.dumps(
            [{"role": "user", "content": "hi", "timestamp": 1.0, "id": "u"}]
        ),
        encoding="utf-8",
    )
    assert (
        DiffService().get_turn_diffs(
            session_id, service_id="default", agent_id="default"
        )
        == []
    )


def test_is_deleted_file_in_turn_diff(tmp_path, monkeypatch):
    _patch_tenant_roots(monkeypatch, tmp_path)
    session_id = "sess_del"
    session_dir = _session_dir(tmp_path, session_id)
    (session_dir / "history.json").write_text(
        json.dumps(
            [{"role": "user", "content": "rm", "timestamp": 100.0, "id": "u1"}]
        ),
        encoding="utf-8",
    )
    _write_file_ops(
        _workspace_hist(tmp_path) / f"file_ops_jiuwenclaw_{session_id}.json",
        {
            str(tmp_path / "gone.py"): [
                {
                    "timestamp": _iso(101.0),
                    "action": "write",
                    "old_content": "bye\n",
                    "new_content": None,
                }
            ]
        },
    )
    turns = DiffService().get_turn_diffs(
        session_id, service_id="default", agent_id="default"
    )
    assert len(turns) == 1
    info = next(iter(turns[0]["files"].values()))
    assert info["isDeletedFile"] is True
    assert info["linesRemoved"] >= 1


def test_web_forward_whitelist_includes_command_diff():
    """Gateway must forward command.diff for Web clients (no local ACK).

    Avoid importing app_web_handlers (heavy deps); assert source constants instead.
    """
    src = (
        Path(__file__).resolve().parents[3]
        / "jiuwenclaw"
        / "app_web_handlers.py"
    ).read_text(encoding="utf-8")
    # both frozensets should list command.diff
    assert src.count('"command.diff"') >= 2
    assert "_FORWARD_REQ_METHODS" in src
    assert "_FORWARD_NO_LOCAL_HANDLER_METHODS" in src
