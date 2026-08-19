# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests: team.runtime.dissolve — 保上下文解散团队 runtime."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from openjiuwen.agent_teams.runtime.pool import RuntimeState

from jiuwenclaw.agentserver.agent_ws_server import AgentWebSocketServer
from jiuwenclaw.agentserver.team.exceptions import (
    TeamDissolveConflictError,
    TeamDissolveNameMismatchError,
    TeamDissolveUnsupportedError,
)
from jiuwenclaw.agentserver.team.team_manager import TeamManager
from jiuwenclaw.schema.agent import AgentRequest
from jiuwenclaw.schema.message import ReqMethod


def _force_local_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        TeamManager,
        "_is_distributed_mode",
        staticmethod(lambda config_base: False),
    )


async def _no_pool_entry(session_id: str):
    return None


@pytest.mark.asyncio
async def test_dissolve_conflict_with_live_stream_task() -> None:
    mgr = TeamManager()
    mgr._stream_tasks["sess_1"] = object()  # type: ignore[assignment]

    with pytest.raises(TeamDissolveConflictError):
        await mgr.dissolve_session_runtime_keep_context("sess_1")


@pytest.mark.asyncio
async def test_dissolve_conflict_when_pool_entry_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mgr = TeamManager()
    entry = SimpleNamespace(state=RuntimeState.RUNNING, current_session_id="sess_1")

    async def _fake_resolve(session_id: str):
        return ("alpha_sess_1", entry)

    monkeypatch.setattr(mgr, "_resolve_resumable_runner_entry", _fake_resolve)

    with pytest.raises(TeamDissolveConflictError):
        await mgr.dissolve_session_runtime_keep_context("sess_1")


@pytest.mark.asyncio
async def test_dissolve_rejected_in_distributed_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        TeamManager,
        "_is_distributed_mode",
        staticmethod(lambda config_base: True),
    )
    mgr = TeamManager()
    monkeypatch.setattr(mgr, "_resolve_resumable_runner_entry", _no_pool_entry)

    with pytest.raises(TeamDissolveUnsupportedError):
        await mgr.dissolve_session_runtime_keep_context("sess_1")


@pytest.mark.asyncio
async def test_dissolve_team_name_mismatch_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _force_local_mode(monkeypatch)
    mgr = TeamManager()
    monkeypatch.setattr(mgr, "_resolve_resumable_runner_entry", _no_pool_entry)
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.team_manager.get_session_metadata",
        lambda session_id: {"team_name": "alpha_sess_1"},
    )

    with pytest.raises(TeamDissolveNameMismatchError):
        await mgr.dissolve_session_runtime_keep_context("sess_1", team_name="beta")


@pytest.mark.asyncio
async def test_dissolve_accepts_base_team_name_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """base 名（未带 session 后缀）与 scoped 实际名匹配时放行；无 runtime 幂等。"""
    _force_local_mode(monkeypatch)
    mgr = TeamManager()
    monkeypatch.setattr(mgr, "_resolve_resumable_runner_entry", _no_pool_entry)
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.team_manager.get_session_metadata",
        lambda session_id: {"team_name": "alpha_sess_1"},
    )

    async def _fake_stop(session_id: str, reason: str = "", *, stop_runner: bool = True) -> bool:
        return False

    monkeypatch.setattr(mgr, "stop_session_runtime", _fake_stop)

    async def _fake_read(session_id: str) -> list[str]:
        return []

    monkeypatch.setattr(mgr, "_read_checkpoint_team_names", _fake_read)
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.team_manager.get_resolved_project_dir",
        lambda session_id: "",
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.team_manager.resolve_team_sqlite_db_path",
        lambda config_base=None: None,
    )

    result = await mgr.dissolve_session_runtime_keep_context("sess_1", team_name="alpha")

    assert result["session_id"] == "sess_1"
    assert result["team_name"] == "alpha_sess_1"
    assert result["dissolved"] is False
    assert result["had_runtime"] is False
    assert result["had_db_rows"] is False
    assert result["db_state_marked"] is False


@pytest.mark.asyncio
async def test_dissolve_non_team_session_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """非团队 session（无 runtime、无 metadata、无 checkpoint 桶）幂等安全。"""
    _force_local_mode(monkeypatch)
    mgr = TeamManager()
    monkeypatch.setattr(mgr, "_resolve_resumable_runner_entry", _no_pool_entry)
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.team_manager.get_session_metadata",
        lambda session_id: {},
    )

    async def _fake_stop(session_id: str, reason: str = "", *, stop_runner: bool = True) -> bool:
        return False

    monkeypatch.setattr(mgr, "stop_session_runtime", _fake_stop)

    async def _fake_read(session_id: str) -> list[str]:
        return []

    monkeypatch.setattr(mgr, "_read_checkpoint_team_names", _fake_read)

    result = await mgr.dissolve_session_runtime_keep_context("sess_2")

    assert result["dissolved"] is False
    assert result["team_name"] is None


def _create_team_db(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(
            """
            CREATE TABLE team_info (
                team_name TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                leader_member_name TEXT NOT NULL
            );
            CREATE TABLE team_member (
                member_name TEXT NOT NULL,
                team_name TEXT NOT NULL
                    REFERENCES team_info(team_name) ON DELETE CASCADE,
                PRIMARY KEY (member_name, team_name)
            );
            CREATE TABLE team_task_abc (
                task_id TEXT PRIMARY KEY,
                team_name TEXT NOT NULL
                    REFERENCES team_info(team_name) ON DELETE CASCADE
            );
            """
        )
        conn.execute(
            "INSERT INTO team_info VALUES ('alpha_sess_1', 'alpha', 'leader')"
        )
        conn.execute(
            "INSERT INTO team_info VALUES ('other_sess_9', 'other', 'leader')"
        )
        conn.execute("INSERT INTO team_member VALUES ('leader', 'alpha_sess_1')")
        conn.execute("INSERT INTO team_member VALUES ('m1', 'other_sess_9')")
        conn.execute("INSERT INTO team_task_abc VALUES ('t1', 'alpha_sess_1')")
        conn.commit()
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_dissolve_deletes_static_rows_keeps_dynamic(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """删 team_info/team_member 目标行；动态表行与他团队行不受影响。"""
    _force_local_mode(monkeypatch)
    db_path = tmp_path / "team.db"
    _create_team_db(db_path)

    mgr = TeamManager()
    monkeypatch.setattr(mgr, "_resolve_resumable_runner_entry", _no_pool_entry)
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.team_manager.get_session_metadata",
        lambda session_id: {"team_name": "alpha_sess_1"},
    )
    stopped: list[str] = []

    async def _fake_stop(session_id: str, reason: str = "", *, stop_runner: bool = True) -> bool:
        stopped.append(session_id)
        return True

    monkeypatch.setattr(mgr, "stop_session_runtime", _fake_stop)

    async def _fake_read(session_id: str) -> list[str]:
        return ["alpha_sess_1"]

    monkeypatch.setattr(mgr, "_read_checkpoint_team_names", _fake_read)
    marked: list[str] = []
    marked_rosters: list[dict | None] = []

    async def _fake_mark(session_id: str, roster_by_team: dict | None = None) -> bool:
        marked.append(session_id)
        marked_rosters.append(roster_by_team)
        return True

    monkeypatch.setattr(mgr, "_mark_teams_cleaned_in_checkpoint", _fake_mark)
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.team_manager.get_resolved_project_dir",
        lambda session_id: "",
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.team_manager.resolve_team_sqlite_db_path",
        lambda config_base=None: db_path,
    )

    result = await mgr.dissolve_session_runtime_keep_context(
        "sess_1",
        team_name="alpha_sess_1",
    )

    assert result["dissolved"] is True
    assert result["had_runtime"] is True
    assert result["had_db_rows"] is True
    assert result["db_state_marked"] is True
    assert stopped == ["sess_1"]
    assert marked == ["sess_1"]
    # 删除前捕获的旧名单随标记一并传出（该测试库的 team_member 只有 member_name 列）
    assert marked_rosters == [{"alpha_sess_1": [{"member_name": "leader"}]}]

    conn = sqlite3.connect(str(db_path))
    try:
        remaining_info = {
            row[0] for row in conn.execute("SELECT team_name FROM team_info")
        }
        remaining_member = {
            row[0] for row in conn.execute("SELECT team_name FROM team_member")
        }
        remaining_tasks = [
            row[0] for row in conn.execute("SELECT task_id FROM team_task_abc")
        ]
    finally:
        conn.close()
    assert remaining_info == {"other_sess_9"}
    assert remaining_member == {"other_sess_9"}
    # 关键正确性点：per-session 动态表行未被级联删除
    assert remaining_tasks == ["t1"]


@pytest.mark.asyncio
async def test_delete_static_rows_sync_missing_db_tables(tmp_path: Path) -> None:
    """db 文件存在但没有静态表时安全返回 False（不建表、不报错）。"""
    db_path = tmp_path / "team.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE unrelated (id TEXT)")
    conn.commit()
    conn.close()

    had_rows, rosters = TeamManager._delete_static_team_rows_sync(db_path, ["alpha_sess_1"])
    assert had_rows is False
    assert rosters == {}


def test_next_dispatch_lands_on_create_after_dissolve() -> None:
    """dissolve 后的终态（team_in_db=False + db_state=cleaned）应落 CREATE。"""
    dispatch = pytest.importorskip("openjiuwen.agent_teams.runtime.dispatch")

    action = dispatch.decide_run_action(
        team_in_db=False,
        team_in_session=True,
        pool_entry=None,
        target_session_id="sess_1",
        target_team_name="alpha_sess_1",
        team_db_state=dispatch.TEAM_DB_STATE_CLEANED,
    )
    assert action.kind is dispatch.RunActionKind.CREATE

    # 池中残留条目也不阻断重建（recreatable 分支先于 REJECT_INCONSISTENT）。
    action_with_pool = dispatch.decide_run_action(
        team_in_db=False,
        team_in_session=True,
        pool_entry=SimpleNamespace(current_session_id="sess_1"),
        target_session_id="sess_1",
        target_team_name="alpha_sess_1",
        team_db_state=dispatch.TEAM_DB_STATE_CLEANED,
    )
    assert action_with_pool.kind is dispatch.RunActionKind.CREATE


def test_req_method_enum_value() -> None:
    assert ReqMethod.TEAM_RUNTIME_DISSOLVE.value == "team.runtime.dissolve"


class _FakeWs:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, data: str) -> None:
        self.sent.append(data)


def _make_request(**kwargs) -> AgentRequest:
    defaults = {
        "request_id": "req-1",
        "channel_id": "web",
        "session_id": "sess-1",
        "req_method": ReqMethod.TEAM_RUNTIME_DISSOLVE,
        "params": {},
        "is_stream": False,
        "timestamp": 0.0,
    }
    defaults.update(kwargs)
    return AgentRequest(**defaults)


@pytest.fixture
def _plain_wire(monkeypatch: pytest.MonkeyPatch) -> None:
    """绕开 E2A 信封，直接透传 AgentResponse dict 便于断言。"""
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.agent_ws_server.encode_agent_response_for_wire",
        lambda resp, response_id, sequence=0: asdict(resp),
    )


@pytest.mark.asyncio
async def test_ws_dissolve_ok(
    monkeypatch: pytest.MonkeyPatch,
    _plain_wire: None,
) -> None:
    captured: dict[str, object] = {}

    class _FakeMgr:
        async def dissolve_session_runtime_keep_context(
            self,
            session_id: str,
            *,
            team_name: str | None = None,
        ) -> dict[str, object]:
            captured["session_id"] = session_id
            captured["team_name"] = team_name
            return {
                "session_id": session_id,
                "team_name": "alpha_sess_1",
                "dissolved": True,
                "had_runtime": True,
                "had_db_rows": True,
                "db_state_marked": True,
            }

    monkeypatch.setattr(
        "jiuwenclaw.agentserver.agent_ws_server.get_team_manager",
        lambda channel_id=None: _FakeMgr(),
    )
    server = object.__new__(AgentWebSocketServer)
    ws = _FakeWs()
    req = _make_request(params={"session_id": "sess_1", "team_name": "alpha"})

    await server._handle_team_runtime_dissolve(ws, req, asyncio.Lock())

    wire = json.loads(ws.sent[0])
    assert wire["ok"] is True
    assert wire["payload"]["dissolved"] is True
    assert wire["payload"]["db_state_marked"] is True
    assert captured == {"session_id": "sess_1", "team_name": "alpha"}


@pytest.mark.asyncio
async def test_ws_dissolve_missing_session_id(_plain_wire: None) -> None:
    server = object.__new__(AgentWebSocketServer)
    ws = _FakeWs()

    await server._handle_team_runtime_dissolve(ws, _make_request(params={}), asyncio.Lock())

    wire = json.loads(ws.sent[0])
    assert wire["ok"] is False
    assert wire["payload"]["code"] == "BAD_REQUEST"


@pytest.mark.asyncio
async def test_ws_dissolve_invalid_session_id(_plain_wire: None) -> None:
    server = object.__new__(AgentWebSocketServer)
    ws = _FakeWs()

    await server._handle_team_runtime_dissolve(
        ws,
        _make_request(params={"session_id": "../escape"}),
        asyncio.Lock(),
    )

    wire = json.loads(ws.sent[0])
    assert wire["ok"] is False
    assert wire["payload"]["code"] == "BAD_REQUEST"


@pytest.mark.asyncio
async def test_ws_dissolve_conflict(
    monkeypatch: pytest.MonkeyPatch,
    _plain_wire: None,
) -> None:
    class _FakeMgr:
        async def dissolve_session_runtime_keep_context(
            self,
            session_id: str,
            *,
            team_name: str | None = None,
        ) -> dict[str, object]:
            raise TeamDissolveConflictError("team runtime is running")

    monkeypatch.setattr(
        "jiuwenclaw.agentserver.agent_ws_server.get_team_manager",
        lambda channel_id=None: _FakeMgr(),
    )
    server = object.__new__(AgentWebSocketServer)
    ws = _FakeWs()

    await server._handle_team_runtime_dissolve(
        ws,
        _make_request(params={"session_id": "sess_1"}),
        asyncio.Lock(),
    )

    wire = json.loads(ws.sent[0])
    assert wire["ok"] is False
    assert wire["payload"]["code"] == "CONFLICT"


@pytest.mark.asyncio
async def test_handle_unary_routes_team_runtime_dissolve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = object.__new__(AgentWebSocketServer)
    called: list[str] = []

    async def _fake_handler(ws, request, send_lock) -> None:
        called.append(request.request_id)

    monkeypatch.setattr(server, "_handle_team_runtime_dissolve", _fake_handler)

    await server._handle_unary(_FakeWs(), _make_request(), asyncio.Lock())

    assert called == ["req-1"]


@pytest.mark.asyncio
async def test_mark_cleaned_called_even_when_bucket_read_degraded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """评审 Important 1：_read_checkpoint_team_names 降级为 [] 时仍必须标 cleaned。

    否则删行成功但桶未标 cleaned，下轮 dispatch 落 REJECT_ORPHANED。
    """
    _force_local_mode(monkeypatch)
    mgr = TeamManager()
    monkeypatch.setattr(mgr, "_resolve_resumable_runner_entry", _no_pool_entry)
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.team_manager.get_session_metadata",
        lambda session_id: {"team_name": "alpha_sess_1"},
    )

    async def _fake_stop(session_id: str, reason: str = "", *, stop_runner: bool = True) -> bool:
        return True

    monkeypatch.setattr(mgr, "stop_session_runtime", _fake_stop)

    async def _degraded_read(session_id: str) -> list[str]:
        return []  # 瞬时失败被吞成 []

    monkeypatch.setattr(mgr, "_read_checkpoint_team_names", _degraded_read)
    marked: list[str] = []

    async def _fake_mark(session_id: str, roster_by_team: dict | None = None) -> bool:
        marked.append(session_id)
        return True

    monkeypatch.setattr(mgr, "_mark_teams_cleaned_in_checkpoint", _fake_mark)
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.team_manager.get_resolved_project_dir",
        lambda session_id: "",
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.team_manager.resolve_team_sqlite_db_path",
        lambda config_base=None: None,
    )

    result = await mgr.dissolve_session_runtime_keep_context("sess_1")

    assert marked == ["sess_1"]
    assert result["db_state_marked"] is True
    assert result["dissolved"] is True


@pytest.mark.asyncio
async def test_dissolve_retry_after_partial_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """部分失败（标 cleaned 抛错）后重调幂等：第二次调用完成全部步骤。"""
    _force_local_mode(monkeypatch)
    mgr = TeamManager()
    monkeypatch.setattr(mgr, "_resolve_resumable_runner_entry", _no_pool_entry)
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.team_manager.get_session_metadata",
        lambda session_id: {"team_name": "alpha_sess_1"},
    )

    async def _fake_stop(session_id: str, reason: str = "", *, stop_runner: bool = True) -> bool:
        return True

    monkeypatch.setattr(mgr, "stop_session_runtime", _fake_stop)

    async def _fake_read(session_id: str) -> list[str]:
        return []

    monkeypatch.setattr(mgr, "_read_checkpoint_team_names", _fake_read)
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.team_manager.get_resolved_project_dir",
        lambda session_id: "",
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.team_manager.resolve_team_sqlite_db_path",
        lambda config_base=None: None,
    )
    mark_calls: list[str] = []

    async def _flaky_mark(session_id: str, roster_by_team: dict | None = None) -> bool:
        mark_calls.append(session_id)
        if len(mark_calls) == 1:
            raise RuntimeError("checkpoint flush failed")
        return True

    monkeypatch.setattr(mgr, "_mark_teams_cleaned_in_checkpoint", _flaky_mark)

    with pytest.raises(RuntimeError, match="checkpoint flush failed"):
        await mgr.dissolve_session_runtime_keep_context("sess_1")

    result = await mgr.dissolve_session_runtime_keep_context("sess_1")

    assert mark_calls == ["sess_1", "sess_1"]
    assert result["dissolved"] is True
    assert result["db_state_marked"] is True


@pytest.mark.asyncio
async def test_dissolve_pool_entry_fallback_releases_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """评审 Important 2：marker 已清但池中仍有条目的兜底分支。

    stop_session_runtime 返回 False 时，兜底分支必须停池条目、释放 A2X
    预留并停 transport（含远程成员的团队否则会泄漏 A2X 预留）。
    """
    _force_local_mode(monkeypatch)
    mgr = TeamManager()
    team_agent = object()
    entry = SimpleNamespace(
        state=RuntimeState.PAUSED,
        current_session_id="sess_1",
        agent=team_agent,
    )

    async def _fake_resolve(session_id: str):
        return ("alpha_sess_1", entry)

    monkeypatch.setattr(mgr, "_resolve_resumable_runner_entry", _fake_resolve)
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.team_manager.get_session_metadata",
        lambda session_id: {"team_name": "alpha_sess_1"},
    )

    async def _fake_stop(session_id: str, reason: str = "", *, stop_runner: bool = True) -> bool:
        return False  # marker 已清 → stop_session_runtime 无可停资源

    monkeypatch.setattr(mgr, "stop_session_runtime", _fake_stop)
    runner_stopped: list[tuple[str, str]] = []

    async def _fake_stop_agent_team(team_name: str = "", session_id: str = "") -> bool:
        runner_stopped.append((team_name, session_id))
        return True

    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.team_manager.Runner.stop_agent_team",
        _fake_stop_agent_team,
    )
    a2x_released: list[tuple[str, object]] = []

    async def _fake_release(session_id: str, *, team_agent=None) -> None:
        a2x_released.append((session_id, team_agent))

    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.team_manager.release_a2x_reservations_for_session",
        _fake_release,
    )
    transport_stopped: list[str] = []

    async def _fake_transport(session_id: str) -> None:
        transport_stopped.append(session_id)

    monkeypatch.setattr(mgr, "_stop_runner_team_agent_transport", _fake_transport)

    async def _fake_read(session_id: str) -> list[str]:
        return []

    monkeypatch.setattr(mgr, "_read_checkpoint_team_names", _fake_read)

    async def _fake_mark(session_id: str, roster_by_team: dict | None = None) -> bool:
        return False

    monkeypatch.setattr(mgr, "_mark_teams_cleaned_in_checkpoint", _fake_mark)
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.team_manager.get_resolved_project_dir",
        lambda session_id: "",
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.team_manager.resolve_team_sqlite_db_path",
        lambda config_base=None: None,
    )

    result = await mgr.dissolve_session_runtime_keep_context("sess_1")

    assert runner_stopped == [("alpha_sess_1", "sess_1")]
    assert a2x_released == [("sess_1", team_agent)]
    assert transport_stopped == ["sess_1"]
    assert result["had_runtime"] is True
    assert result["dissolved"] is True


@pytest.mark.asyncio
async def test_dissolve_removes_pool_entry_despite_stop_returning_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """回归（2026-08-12 事故）：stop_session_runtime 返回 True 不代表池条目
    已移除——marker 已清时它解析不出 team_name，会跳过 Runner stop 却仍停掉
    池条目的 transport 并返回 True。池条目存在时必须无条件按名 stop，否则
    半停条目被 resume 复用会把流卡死。"""
    _force_local_mode(monkeypatch)
    mgr = TeamManager()
    team_agent = object()
    entry = SimpleNamespace(
        state=RuntimeState.PAUSED,
        current_session_id="sess_1",
        agent=team_agent,
    )

    async def _fake_resolve(session_id: str):
        return ("alpha_sess_1", entry)

    monkeypatch.setattr(mgr, "_resolve_resumable_runner_entry", _fake_resolve)
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.team_manager.get_session_metadata",
        lambda session_id: {},
    )

    async def _fake_stop(session_id: str, reason: str = "", *, stop_runner: bool = True) -> bool:
        return True  # 清了本地资源，但 Runner 池条目未被触碰

    monkeypatch.setattr(mgr, "stop_session_runtime", _fake_stop)
    runner_stopped: list[tuple[str, str]] = []

    async def _fake_stop_agent_team(team_name: str = "", session_id: str = "") -> bool:
        runner_stopped.append((team_name, session_id))
        return True

    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.team_manager.Runner.stop_agent_team",
        _fake_stop_agent_team,
    )

    async def _fake_release(session_id: str, *, team_agent=None) -> None:
        return None

    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.team_manager.release_a2x_reservations_for_session",
        _fake_release,
    )

    async def _fake_transport(session_id: str) -> None:
        return None

    monkeypatch.setattr(mgr, "_stop_runner_team_agent_transport", _fake_transport)

    async def _fake_read(session_id: str) -> list[str]:
        return []

    monkeypatch.setattr(mgr, "_read_checkpoint_team_names", _fake_read)

    async def _fake_mark(session_id: str, roster_by_team: dict | None = None) -> bool:
        return False

    monkeypatch.setattr(mgr, "_mark_teams_cleaned_in_checkpoint", _fake_mark)
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.team_manager.get_resolved_project_dir",
        lambda session_id: "",
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.team_manager.resolve_team_sqlite_db_path",
        lambda config_base=None: None,
    )

    result = await mgr.dissolve_session_runtime_keep_context("sess_1")

    assert runner_stopped == [("alpha_sess_1", "sess_1")]
    assert result["had_runtime"] is True


@pytest.mark.asyncio
async def test_dissolve_deletes_rows_from_project_scoped_db(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """回归（2026-08-12 事故）：团队运行在 agent_teams_home_scope(project_dir)
    内，真实 team.db 在 {project_dir}/.agent_teams/team.db。dissolve 必须删
    project 作用域的库，只查全局 home 会删错文件（had_db_rows=False，
    dispatch 永远落不了 CREATE）。"""
    _force_local_mode(monkeypatch)
    project_dir = tmp_path / "proj"
    db_path = project_dir / ".agent_teams" / "team.db"
    db_path.parent.mkdir(parents=True)
    _create_team_db(db_path)

    mgr = TeamManager()
    monkeypatch.setattr(mgr, "_resolve_resumable_runner_entry", _no_pool_entry)
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.team_manager.get_session_metadata",
        lambda session_id: {"team_name": "alpha_sess_1"},
    )

    async def _fake_stop(session_id: str, reason: str = "", *, stop_runner: bool = True) -> bool:
        return True

    monkeypatch.setattr(mgr, "stop_session_runtime", _fake_stop)

    async def _fake_read(session_id: str) -> list[str]:
        return ["alpha_sess_1"]

    monkeypatch.setattr(mgr, "_read_checkpoint_team_names", _fake_read)

    async def _fake_mark(session_id: str, roster_by_team: dict | None = None) -> bool:
        return True

    monkeypatch.setattr(mgr, "_mark_teams_cleaned_in_checkpoint", _fake_mark)
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.team_manager.get_resolved_project_dir",
        lambda session_id: str(project_dir),
    )
    # 全局 home 的库不存在：只有 project 作用域候选能命中
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.team_manager.resolve_team_sqlite_db_path",
        lambda config_base=None: tmp_path / "nonexistent-global" / "team.db",
    )

    result = await mgr.dissolve_session_runtime_keep_context("sess_1")

    assert result["had_db_rows"] is True
    assert result["dissolved"] is True

    conn = sqlite3.connect(str(db_path))
    try:
        remaining_info = {
            row[0] for row in conn.execute("SELECT team_name FROM team_info")
        }
        remaining_tasks = [
            row[0] for row in conn.execute("SELECT task_id FROM team_task_abc")
        ]
    finally:
        conn.close()
    assert remaining_info == {"other_sess_9"}
    assert remaining_tasks == ["t1"]


@pytest.mark.asyncio
async def test_mark_teams_cleaned_checkpoint_roundtrip() -> None:
    """真实 CheckpointerFactory（默认 in-memory 单例）往返：标 cleaned 后可读回。

    仓里没有独立的 in-memory checkpointer fixture；这里直接用
    ``CheckpointerFactory.get_checkpointer()`` 的默认 in-memory 实现，
    session_id 取唯一值避免与共享单例里其他测试的状态冲突。
    """
    from openjiuwen.agent_teams.runtime.metadata import (
        TEAM_DB_STATE_CLEANED,
        TEAM_DB_STATE_CREATED,
        merge_team_db_state,
        read_team_db_state,
    )
    from openjiuwen.core.session.agent_team import create_agent_team_session
    from openjiuwen.core.session.checkpointer import CheckpointerFactory

    sid = "sess_dissolve_roundtrip"
    team = "alpha_sess_dissolve_roundtrip"

    seed = create_agent_team_session(session_id=sid, source_metadata_enabled=False)
    await seed.pre_run()
    merge_team_db_state(seed, team, TEAM_DB_STATE_CREATED)
    await seed.flush_checkpoint()
    await seed.post_run()

    mgr = TeamManager()
    assert await mgr._read_checkpoint_team_names(sid) == [team]
    roster = [{"member_name": "assistant", "display_name": "逻辑大师", "role": "teammate"}]
    assert await mgr._mark_teams_cleaned_in_checkpoint(sid, {team: roster}) is True

    from openjiuwen.agent_teams.runtime.metadata import read_team_namespace

    from jiuwenclaw.agentserver.team.team_manager import TEAM_ROSTER_CHANGE_KEY

    verify = create_agent_team_session(session_id=sid, source_metadata_enabled=False)
    await verify.pre_run()
    try:
        assert read_team_db_state(verify, team) == TEAM_DB_STATE_CLEANED
        change = read_team_namespace(verify, team).get(TEAM_ROSTER_CHANGE_KEY)
        assert change["old_roster"] == roster
        assert isinstance(change["dissolved_at"], int)
    finally:
        await verify.post_run()

    # 重试 dissolve（行已删、捕获不到名单）时保留旧名单，不覆盖为空。
    assert await mgr._mark_teams_cleaned_in_checkpoint(sid, {}) is True
    verify2 = create_agent_team_session(session_id=sid, source_metadata_enabled=False)
    await verify2.pre_run()
    try:
        change2 = read_team_namespace(verify2, team).get(TEAM_ROSTER_CHANGE_KEY)
        assert change2["old_roster"] == roster
    finally:
        await verify2.post_run()

    # 无 checkpoint 的 session：两个 helper 均安全 no-op。
    assert await mgr._read_checkpoint_team_names("sess_dissolve_never_built") == []
    assert await mgr._mark_teams_cleaned_in_checkpoint("sess_dissolve_never_built") is False


@pytest.mark.asyncio
async def test_ws_dissolve_internal_error(
    monkeypatch: pytest.MonkeyPatch,
    _plain_wire: None,
) -> None:
    """评审 Minor 1：兜底异常错误帧带 code=INTERNAL。"""

    class _FakeMgr:
        async def dissolve_session_runtime_keep_context(
            self,
            session_id: str,
            *,
            team_name: str | None = None,
        ) -> dict[str, object]:
            raise RuntimeError("db locked")

    monkeypatch.setattr(
        "jiuwenclaw.agentserver.agent_ws_server.get_team_manager",
        lambda channel_id=None: _FakeMgr(),
    )
    server = object.__new__(AgentWebSocketServer)
    ws = _FakeWs()

    await server._handle_team_runtime_dissolve(
        ws,
        _make_request(params={"session_id": "sess_1"}),
        asyncio.Lock(),
    )

    wire = json.loads(ws.sent[0])
    assert wire["ok"] is False
    assert wire["payload"]["code"] == "INTERNAL"


def test_delete_static_rows_captures_roster_with_display_names(tmp_path: Path) -> None:
    """删除前捕获旧成员名单（含 display_name/role 列），供换岗简报 diff 使用。"""
    db_path = tmp_path / "team.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(
            """
            CREATE TABLE team_info (
                team_name TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                leader_member_name TEXT NOT NULL
            );
            CREATE TABLE team_member (
                member_name TEXT NOT NULL,
                team_name TEXT NOT NULL,
                display_name TEXT,
                role TEXT,
                PRIMARY KEY (member_name, team_name)
            );
            """
        )
        conn.execute("INSERT INTO team_info VALUES ('t_s1', 'T', 'leader')")
        conn.execute("INSERT INTO team_member VALUES ('leader', 't_s1', 'Leader', 'leader')")
        conn.execute("INSERT INTO team_member VALUES ('assistant', 't_s1', '逻辑大师', 'teammate')")
        conn.commit()
    finally:
        conn.close()

    had_rows, rosters = TeamManager._delete_static_team_rows_sync(db_path, ["t_s1"])
    assert had_rows is True
    assert rosters == {
        "t_s1": [
            {"member_name": "leader", "display_name": "Leader", "role": "leader"},
            {"member_name": "assistant", "display_name": "逻辑大师", "role": "teammate"},
        ]
    }

    conn = sqlite3.connect(str(db_path))
    try:
        assert list(conn.execute("SELECT * FROM team_member")) == []
        assert list(conn.execute("SELECT * FROM team_info")) == []
    finally:
        conn.close()
