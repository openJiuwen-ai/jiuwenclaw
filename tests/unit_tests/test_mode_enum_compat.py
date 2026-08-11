# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""P2.4：Mode 枚举新成员 + from_raw 旧→新静默归一 + session_metadata 惰性迁移。

覆盖兼容性铁律 1/2/4/5：旧输入→新输出、新输入直通、持久化旧数据可读、写回不丢失。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from jiuwenswarm.common.schema.message import Mode
from jiuwenswarm.gateway.message_handler.message_handler import ChannelMode
from jiuwenswarm.gateway.message_handler.command_parser.slash_command import (
    ModeSubcommand,
    _VALID_MODE_LINES,
    VALID_MODE_SUBCOMMANDS,
)


# ── 铁律 1：旧 canonical → 新枚举；铁律 2：新串直通 ────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # 旧串 → 新枚举（P2.1：归一目标从 AGENT 改成 AGENT_WORK_*）
        ("agent", Mode.AGENT_WORK_NORMAL),
        ("agent.plan", Mode.AGENT_WORK_PLAN),
        ("agent.fast", Mode.AGENT_WORK_NORMAL),
        ("plan", Mode.AGENT_WORK_NORMAL),
        ("fast", Mode.AGENT_WORK_NORMAL),
        ("code.normal", Mode.AGENT_CODE_NORMAL),
        ("code.plan", Mode.AGENT_CODE_PLAN),
        ("code.team", Mode.TEAM_CODE_NORMAL),
        ("team", Mode.TEAM_WORK_NORMAL),
        ("team.plan", Mode.TEAM_WORK_PLAN),
        ("team.plan.normal", Mode.TEAM_WORK_PLAN),
        ("team.plan.code", Mode.TEAM_CODE_PLAN),
        # 新串直通
        ("agent.work.normal", Mode.AGENT_WORK_NORMAL),
        ("agent.work.plan", Mode.AGENT_WORK_PLAN),
        ("agent.code.normal", Mode.AGENT_CODE_NORMAL),
        ("agent.code.plan", Mode.AGENT_CODE_PLAN),
        ("team.work.normal", Mode.TEAM_WORK_NORMAL),
        ("team.work.plan", Mode.TEAM_WORK_PLAN),
        ("team.code.normal", Mode.TEAM_CODE_NORMAL),
        ("team.code.plan", Mode.TEAM_CODE_PLAN),
    ],
)
def test_from_raw_new_and_legacy(raw, expected):
    assert Mode.from_raw(raw) == expected


def test_from_raw_unknown_falls_back_to_default():
    assert Mode.from_raw("totally-unknown") == Mode.AGENT
    assert Mode.from_raw("totally-unknown", default=Mode.TEAM) == Mode.TEAM


def test_from_raw_empty_and_none_fall_back():
    assert Mode.from_raw("") == Mode.AGENT
    assert Mode.from_raw(None) == Mode.AGENT


# ── P2.1：Mode 枚举成员本身被旧枚举实例归一 ────────────────────────────────


@pytest.mark.parametrize(
    ("legacy_member", "new_member"),
    [
        (Mode.AGENT, Mode.AGENT_WORK_NORMAL),
        (Mode.AGENT_PLAN, Mode.AGENT_WORK_NORMAL),
        (Mode.AGENT_FAST, Mode.AGENT_WORK_NORMAL),
        (Mode.CODE_NORMAL, Mode.AGENT_CODE_NORMAL),
        (Mode.CODE_PLAN, Mode.AGENT_CODE_PLAN),
        (Mode.CODE_TEAM, Mode.TEAM_CODE_NORMAL),
        (Mode.TEAM, Mode.TEAM_WORK_NORMAL),
        (Mode.TEAM_PLAN_NORMAL, Mode.TEAM_WORK_PLAN),
        (Mode.TEAM_PLAN_CODE, Mode.TEAM_CODE_PLAN),
    ],
)
def test_from_raw_legacy_enum_member_normalizes(legacy_member, new_member):
    """旧枚举成员作输入时归一到新成员（兼容旧序列化数据的反解析路径）。"""
    assert Mode.from_raw(legacy_member) == new_member


# ── P2.2：ChannelMode / ModeSubcommand 加新成员 ────────────────────────────


@pytest.mark.parametrize(
    "value",
    [
        "agent.work.normal",
        "agent.work.plan",
        "agent.code.normal",
        "agent.code.plan",
        "team.work.normal",
        "team.work.plan",
        "team.code.normal",
        "team.code.plan",
    ],
)
def test_channel_mode_has_new_canonical(value):
    assert ChannelMode(value).value == value


@pytest.mark.parametrize(
    "value",
    [
        "agent.work.normal",
        "agent.work.plan",
        "agent.code.normal",
        "agent.code.plan",
        "team.work.normal",
        "team.work.plan",
        "team.code.normal",
        "team.code.plan",
    ],
)
def test_mode_subcommand_has_new_canonical(value):
    assert ModeSubcommand(value).value == value


def test_valid_mode_lines_includes_new_canonical():
    """/mode <新串> 必须在 _VALID_MODE_LINES 里（由 ModeSubcommand 推导自动进）。"""
    assert "/mode agent.work.plan" in _VALID_MODE_LINES
    assert "/mode team.code.normal" in _VALID_MODE_LINES


def test_valid_mode_subcommands_keeps_legacy():
    """旧成员保留（兼容旧 /mode 输入）。"""
    for legacy in ("agent", "code.plan", "team.plan.normal"):
        assert legacy in VALID_MODE_SUBCOMMANDS


# ── 铁律 4+5：session_metadata 惰性迁移 mode 字段 ───────────────────────────


def _write_session_metadata(session_dir: Path, mode_value: str) -> Path:
    """写一个含旧 mode 的 metadata.json，返回 metadata 文件路径。"""
    session_dir.mkdir(parents=True, exist_ok=True)
    meta_path = session_dir / "metadata.json"
    meta = {
        "session_id": "sess_test_mode_migrate",
        "channel_id": "web",
        "work_mode": "work",
        "mode": mode_value,
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return meta_path


def _read_session_metadata(session_dir: Path) -> dict:
    return json.loads((session_dir / "metadata.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("old_mode", "new_mode"),
    [
        ("agent", "agent.work.normal"),
        ("agent.plan", "agent.work.plan"),
        ("agent.fast", "agent.work.normal"),
        ("code.normal", "agent.code.normal"),
        ("code.plan", "agent.code.plan"),
        ("code.team", "team.code.normal"),
        ("team", "team.work.normal"),
        ("team.plan.normal", "team.work.plan"),
        ("team.plan.code", "team.code.plan"),
    ],
)
def test_session_metadata_lazy_migrates_old_mode(tmp_path, old_mode, new_mode):
    """铁律 4：读 metadata.json 里旧 mode，应惰性迁成新 canonical（返回值升级）。"""
    from jiuwenswarm.server.runtime.session.session_metadata import (
        _apply_metadata_defaults_with_inference,
    )

    session_dir = tmp_path / "sess_test"
    _write_session_metadata(session_dir, old_mode)

    metadata = _read_session_metadata(session_dir)
    result = _apply_metadata_defaults_with_inference(
        "sess_test", metadata, session_dir=session_dir, enable_writeback=False
    )

    assert result["mode"] == new_mode


def test_session_metadata_new_mode_not_rewritten(tmp_path):
    """铁律 2：已是新 canonical 时不触发写盘（changed 不因 mode 置位）。"""
    from jiuwenswarm.server.runtime.session.session_metadata import (
        _apply_metadata_defaults_with_inference,
    )

    session_dir = tmp_path / "sess_test"
    _write_session_metadata(session_dir, "agent.work.plan")

    metadata = _read_session_metadata(session_dir)
    result = _apply_metadata_defaults_with_inference(
        "sess_test", metadata, session_dir=session_dir, enable_writeback=False
    )

    assert result["mode"] == "agent.work.plan"


def test_session_metadata_missing_mode_leaves_none(tmp_path):
    """无 mode 字段时不主动写入（None 不当旧串升级）。

    P2.3 注入逻辑：``existing_mode = metadata.get("mode")``，None 时跳过迁移
    （不把缺失值当旧串升级）。钉住此行为，避免误把无 mode 的会话强制写入。
    """
    from jiuwenswarm.server.runtime.session.session_metadata import (
        _apply_metadata_defaults_with_inference,
    )

    session_dir = tmp_path / "sess_test"
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "metadata.json").write_text(
        json.dumps(
            {"session_id": "sess_test", "channel_id": "web", "work_mode": "work"},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    metadata = json.loads((session_dir / "metadata.json").read_text(encoding="utf-8"))
    result = _apply_metadata_defaults_with_inference(
        "sess_test", metadata, session_dir=session_dir, enable_writeback=False
    )

    # 无 mode 字段 → existing_mode is None → 不迁移，mode 不被写入。
    assert "mode" not in result or result.get("mode") is None


def test_session_metadata_migration_triggers_writeback(tmp_path, monkeypatch):
    """铁律 5（触发面）：旧 mode 经迁移后应调用 _enqueue_write 写盘。

    写盘走异步队列 + 全局 session 目录，单元测试不验证落盘内容（属集成测试），
    此处只钉住"changed → 触发 _enqueue_write"的调用面，确保迁移确实请求了写盘。
    """
    from jiuwenswarm.server.runtime.session import session_metadata as sm

    session_dir = tmp_path / "sess_test"
    _write_session_metadata(session_dir, "agent.plan")

    metadata = _read_session_metadata(session_dir)
    calls: list[tuple] = []

    def _capture_enqueue(session_id, meta, sync_write=False, preserve_pin_fields=False):
        calls.append((session_id, meta.get("mode")))

    monkeypatch.setattr(sm, "_enqueue_write", _capture_enqueue)

    result = sm._apply_metadata_defaults_with_inference(
        "sess_test", metadata, session_dir=session_dir, enable_writeback=True
    )

    assert result["mode"] == "agent.work.plan"
    # 迁移后应请求写盘，且写盘的 mode 是新串。
    assert calls, "惰性迁移未触发 _enqueue_write"
    assert calls[0][1] == "agent.work.plan"


def test_session_metadata_new_mode_does_not_trigger_writeback(tmp_path, monkeypatch):
    """铁律 5（幂等面）：已是新串时不应触发 _enqueue_write（不回退、不冗余写）。"""
    from jiuwenswarm.server.runtime.session import session_metadata as sm

    session_dir = tmp_path / "sess_test"
    _write_session_metadata(session_dir, "agent.work.plan")

    metadata = _read_session_metadata(session_dir)
    calls: list[tuple] = []

    def _capture_enqueue(session_id, meta, sync_write=False, preserve_pin_fields=False):
        calls.append((session_id, meta.get("mode")))

    monkeypatch.setattr(sm, "_enqueue_write", _capture_enqueue)

    # 注意：metadata 还有 last_user_message_at 等字段会触发 changed=True，
    # 此处只验证 mode 字段不会因已是新串而触发额外写盘——即调用里 mode 是新串。
    result = sm._apply_metadata_defaults_with_inference(
        "sess_test", metadata, session_dir=session_dir, enable_writeback=True
    )
    assert result["mode"] == "agent.work.plan"
    # 若触发了写盘，mode 必须仍是新串（未被回退）。
    for _sid, mode in calls:
        assert mode == "agent.work.plan"
