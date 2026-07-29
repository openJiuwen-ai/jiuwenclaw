# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""HeartbeatController 单元测试:Web/RPC + Agent Tool + source 审计 + 资源限制 + 禁止字段.

对应方案 §12:
  - source 审计: controller 创建/更新时强制写入并校验 metadata.source 枚举。
  - Agent Tool: heartbeat_create_job 自动继承当前 channel_id/session_id。
  - Agent Tool: 独立周期任务意图应拒绝或提示 cron_create_job(通过 schema/forbidden 字段拦截)。
  - 停止义务: schema 含 heartbeat_update_job(enabled=false) / heartbeat_cancel_run 要求。
  - 资源限制: max_active_jobs_per_session / global 超限拦截。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jiuwenswarm.gateway.heartbeat.controller import HeartbeatController
from jiuwenswarm.gateway.heartbeat.models import HeartbeatSchedule
from jiuwenswarm.gateway.heartbeat.scheduler import HeartbeatSchedulerService
from jiuwenswarm.gateway.heartbeat.store import HeartbeatJobStore


class _FakeMH:
    async def publish_user_messages(self, msg) -> None:  # noqa: ANN001
        pass


@pytest.fixture
def ctrl(tmp_path: Path):
    store = HeartbeatJobStore(path=tmp_path / "hb.json")
    sched = HeartbeatSchedulerService(store=store, message_handler=_FakeMH())
    controller = HeartbeatController(store=store, scheduler=sched)
    HeartbeatController.reset_instance()
    return controller


def _interval_schedule(seconds: int = 120) -> HeartbeatSchedule:
    return HeartbeatSchedule.from_dict({"type": "interval", "interval_seconds": seconds})


# ---------------------------------------------------------------------------
# Agent Tool: 自动继承 channel_id/session_id
# ---------------------------------------------------------------------------


async def test_agent_tool_create_inherits_session(ctrl: HeartbeatController) -> None:
    HeartbeatController.set_session_ctx(channel_id="web", session_id="sess-A")
    job = await ctrl._tool_create_job(
        name="继续", prompt="继续检查", source="agent_tool",
        schedule={"type": "interval", "interval_seconds": 120},
    )
    assert job["channel_id"] == "web"
    assert job["session_id"] == "sess-A"
    assert job["metadata"]["source"] == "agent_tool"
    assert job["next_run_at"] is not None


async def test_agent_tool_create_no_session_raises(ctrl: HeartbeatController) -> None:
    HeartbeatController.set_session_ctx(channel_id="", session_id="")
    with pytest.raises(ValueError, match="no current session"):
        await ctrl._tool_create_job(
            name="x", prompt="p", source="agent_tool",
            schedule={"type": "interval", "interval_seconds": 120},
        )


async def test_web_rpc_create_requires_channel_and_session(ctrl: HeartbeatController) -> None:
    with pytest.raises(ValueError, match="channel_id is required"):
        await ctrl.create_job({"name": "x", "session_id": "s", "prompt": "p", "source": "web_rpc",
                               "schedule": {"type": "interval", "interval_seconds": 120}})
    with pytest.raises(ValueError, match="session_id is required"):
        await ctrl.create_job({"name": "x", "channel_id": "web", "prompt": "p", "source": "web_rpc",
                               "schedule": {"type": "interval", "interval_seconds": 120}})


# ---------------------------------------------------------------------------
# 禁止字段拦截(mode/model/approval/sandbox/worktree)
# ---------------------------------------------------------------------------


async def test_create_rejects_mode(ctrl: HeartbeatController) -> None:
    with pytest.raises(ValueError, match="forbidden"):
        await ctrl.create_job({
            "name": "x", "channel_id": "web", "session_id": "s", "prompt": "p", "source": "web_rpc",
            "schedule": {"type": "interval", "interval_seconds": 120}, "mode": "agent",
        })


async def test_create_rejects_model(ctrl: HeartbeatController) -> None:
    with pytest.raises(ValueError, match="forbidden"):
        await ctrl.create_job({
            "name": "x", "channel_id": "web", "session_id": "s", "prompt": "p", "source": "web_rpc",
            "schedule": {"type": "interval", "interval_seconds": 120}, "model": "claude-x",
        })


async def test_update_rejects_sandbox(ctrl: HeartbeatController) -> None:
    HeartbeatController.set_session_ctx(channel_id="web", session_id="s1")
    job = await ctrl._tool_create_job(
        name="x", prompt="p", source="agent_tool",
        schedule={"type": "interval", "interval_seconds": 120},
    )
    with pytest.raises(ValueError, match="forbidden"):
        await ctrl.update_job(job["id"], {"sandbox": "dangerous"})


# ---------------------------------------------------------------------------
# source 审计
# ---------------------------------------------------------------------------


async def test_create_rejects_bad_source(ctrl: HeartbeatController) -> None:
    with pytest.raises(ValueError, match="invalid metadata.source"):
        await ctrl.create_job({
            "name": "x", "channel_id": "web", "session_id": "s", "prompt": "p", "source": "bad",
            "schedule": {"type": "interval", "interval_seconds": 120},
        })


async def test_create_default_source_web_rpc(ctrl: HeartbeatController) -> None:
    job = await ctrl.create_job({
        "name": "x", "channel_id": "web", "session_id": "s", "prompt": "p",
        "schedule": {"type": "interval", "interval_seconds": 120},
    })
    assert job["metadata"]["source"] == "web_rpc"


# ---------------------------------------------------------------------------
# 资源限制
# ---------------------------------------------------------------------------


async def test_min_interval_enforced(ctrl: HeartbeatController) -> None:
    with pytest.raises(ValueError, match="at least"):
        await ctrl.create_job({
            "name": "x", "channel_id": "web", "session_id": "s", "prompt": "p", "source": "web_rpc",
            "schedule": {"type": "interval", "interval_seconds": 30},  # < 60
        })


async def test_max_active_jobs_per_session_enforced(ctrl: HeartbeatController, monkeypatch) -> None:
    ctrl.set_limits({"max_active_jobs_per_session": 2, "max_active_jobs_global": 100,
                     "min_interval_seconds": 60})

    async def _count_session(_sid: str) -> int:
        # 模拟已有 2 个活跃 job(达到上限)
        return 2

    async def _count_global() -> int:
        return 2

    monkeypatch.setattr(ctrl._store, "count_active_jobs_for_session", _count_session)
    monkeypatch.setattr(ctrl._store, "count_active_jobs_global", _count_global)
    with pytest.raises(ValueError, match="max_active_jobs_per_session"):
        await ctrl.create_job({
            "name": "x", "channel_id": "web", "session_id": "s", "prompt": "p", "source": "web_rpc",
            "schedule": {"type": "interval", "interval_seconds": 120},
        })


# ---------------------------------------------------------------------------
# toggle / 重新激活
# ---------------------------------------------------------------------------


async def test_toggle_reactivate_completed(ctrl: HeartbeatController) -> None:
    HeartbeatController.set_session_ctx(channel_id="web", session_id="s1")
    job = await ctrl._tool_create_job(
        name="x", prompt="p", source="agent_tool",
        schedule={"type": "interval", "interval_seconds": 120}, max_runs=1,
    )
    # 手动 completed
    await ctrl._store.mark_completed(job["id"], "r1", 1000.0)
    assert (await ctrl.get_job(job["id"]))["status"] == "completed"
    # 重新激活
    reactivated = await ctrl.toggle_job(job["id"], True)
    assert reactivated["status"] == "scheduled"
    assert reactivated["enabled"] is True
    assert reactivated["next_run_at"] is not None


# ---------------------------------------------------------------------------
# delete / preview / list / meta
# ---------------------------------------------------------------------------


async def test_delete_job(ctrl: HeartbeatController) -> None:
    HeartbeatController.set_session_ctx(channel_id="web", session_id="s1")
    job = await ctrl._tool_create_job(
        name="x", prompt="p", source="agent_tool",
        schedule={"type": "interval", "interval_seconds": 120},
    )
    result = await ctrl.delete_job(job["id"])
    assert result["deleted"] is True
    assert await ctrl.get_job(job["id"]) is None


async def test_preview_job(ctrl: HeartbeatController) -> None:
    HeartbeatController.set_session_ctx(channel_id="web", session_id="s1")
    job = await ctrl._tool_create_job(
        name="x", prompt="p", source="agent_tool",
        schedule={"type": "interval", "interval_seconds": 600},
    )
    result = await ctrl.preview_job(job["id"], count=3)
    assert len(result["next"]) == 3


async def test_list_jobs_filtered_by_session(ctrl: HeartbeatController) -> None:
    HeartbeatController.set_session_ctx(channel_id="web", session_id="s1")
    await ctrl._tool_create_job(
        name="a", prompt="p", source="agent_tool",
        schedule={"type": "interval", "interval_seconds": 120},
    )
    HeartbeatController.set_session_ctx(channel_id="web", session_id="s2")
    await ctrl._tool_create_job(
        name="b", prompt="p", source="agent_tool",
        schedule={"type": "interval", "interval_seconds": 120},
    )
    result = await ctrl.list_jobs({"session_id": "s1"})
    assert len(result["jobs"]) == 1
    assert result["jobs"][0]["session_id"] == "s1"


async def test_get_meta(ctrl: HeartbeatController) -> None:
    meta = ctrl.get_meta()
    assert "limits" in meta
    assert "scheduled" in meta["statuses"]
    assert "interval" in meta["schedule_types"]
    assert "skip" in meta["concurrency_policies"]


# ---------------------------------------------------------------------------
# get_tools:9 个工具 + 停止义务描述
# ---------------------------------------------------------------------------


def test_get_tools_returns_9_tools(ctrl: HeartbeatController) -> None:
    tools = ctrl.get_tools()
    names = {t.card.name for t in tools}
    expected = {
        "heartbeat_list_jobs", "heartbeat_get_job", "heartbeat_create_job",
        "heartbeat_update_job", "heartbeat_delete_job", "heartbeat_toggle_job",
        "heartbeat_preview_job", "heartbeat_run_now", "heartbeat_cancel_run",
    }
    assert names == expected
    assert len(tools) == 9


def test_create_job_description_contains_decision_tree_and_stop_obligation(ctrl: HeartbeatController) -> None:
    tools = ctrl.get_tools()
    create_tool = next(t for t in tools if t.card.name == "heartbeat_create_job")
    desc = create_tool.card.description
    # 决策树:何时用 heartbeat vs cron
    assert "cron_create_job" in desc
    # 停止义务:必须实际调用工具停止
    assert "heartbeat_update_job(enabled=false)" in desc
    assert "heartbeat_cancel_run(pause_schedule=true)" in desc


def test_cancel_run_description_mentions_pause_schedule(ctrl: HeartbeatController) -> None:
    tools = ctrl.get_tools()
    cancel_tool = next(t for t in tools if t.card.name == "heartbeat_cancel_run")
    assert "pause_schedule" in cancel_tool.card.description
