# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""HeartbeatSchedulerService 单元测试:due 扫描 / 并发 / 停止条件 / session / source / preview.

对应方案 §12:
  - schedule 计算: interval 基于 now 重算不补跑; cron 复用 helper; once completed 保留。
  - 并发策略: skip 上一轮运行中跳过并记录 skipped。
  - 停止条件: max_runs 达上限 completed; delete_after_run completed。
  - ghost task: job 删除后当前 run 被清理。
  - 会话生命周期: session 不可达按 session_deleted_policy 处理。
  - source 审计: scheduler 缺失/非法 source 记 warning 兜底 schedule_recovery。
  - 不传 params.mode(关键约束)。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jiuwenswarm.gateway.heartbeat.models import (
    HeartbeatJob,
    HeartbeatSchedule,
    SCHEDULE_CRON,
    SCHEDULE_INTERVAL,
    SCHEDULE_ONCE,
    SOURCE_AGENT_TOOL,
    SOURCE_SCHEDULE_RECOVERY,
    STATUS_COMPLETED,
    STATUS_DISABLED,
    STATUS_SCHEDULED,
)
from jiuwenswarm.gateway.heartbeat.scheduler import HeartbeatSchedulerService
from jiuwenswarm.gateway.heartbeat.session_resolver import SessionSummary
from jiuwenswarm.gateway.heartbeat.store import HeartbeatJobStore


class _FakeMH:
    """记录所有 publish_user_messages 投递的消息。"""

    def __init__(self) -> None:
        self.messages: list = []

    async def publish_user_messages(self, msg) -> None:  # noqa: ANN001
        self.messages.append(msg)


class _FakeResolver:
    """可控的 session resolver:resolve 总返回非 None(模拟 session 存在)。"""

    def set_scheduler(self, scheduler) -> None:  # noqa: ANN001
        pass

    def resolve(self, channel_id: str, session_id: str) -> SessionSummary | None:
        return SessionSummary(session_id=session_id, channel_id=channel_id)

    async def on_session_deleted(self, session_id: str) -> None:
        pass


class _MissingResolver:
    """resolve 返回 None(模拟 session 不存在)。"""

    def set_scheduler(self, scheduler) -> None:  # noqa: ANN001
        pass

    def resolve(self, channel_id: str, session_id: str) -> SessionSummary | None:
        return None

    async def on_session_deleted(self, session_id: str) -> None:
        pass


@pytest.fixture
def setup(tmp_path: Path):
    store = HeartbeatJobStore(path=tmp_path / "hb.json")
    mh = _FakeMH()
    sched = HeartbeatSchedulerService(store=store, message_handler=mh)
    # 注入可控 resolver(默认会尝试读真实 session 目录,测试里都返回 None)
    sched._session_resolver = _FakeResolver()
    return store, mh, sched


# ---------------------------------------------------------------------------
# _compute_next_run
# ---------------------------------------------------------------------------


def test_compute_next_run_interval_based_on_base_ts(setup) -> None:
    store, _, sched = setup
    import asyncio

    async def run() -> None:
        job = HeartbeatJob(
            id="x", name="n", enabled=True, channel_id="web", session_id="s1", prompt="p",
            schedule=HeartbeatSchedule.from_dict({"type": "interval", "interval_seconds": 300}),
        )
        n = sched._compute_next_run(job, 1000.0)
        assert n == 1000.0 + 300

    asyncio.run(run())


def test_compute_next_run_once_returns_none(setup) -> None:
    store, _, sched = setup
    job = HeartbeatJob(
        id="x", name="n", enabled=True, channel_id="web", session_id="s1", prompt="p",
        schedule=HeartbeatSchedule.from_dict({"type": "once", "run_at": 9999.0}),
    )
    assert sched._compute_next_run(job, 1000.0) is None


def test_compute_next_run_cron_uses_cron_helper(setup) -> None:
    store, _, sched = setup
    job = HeartbeatJob(
        id="x", name="n", enabled=True, channel_id="web", session_id="s1", prompt="p",
        schedule=HeartbeatSchedule.from_dict({"type": "cron", "cron_expr": "0 9 * * *"}),
    )
    import time

    base = time.time()
    nxt = sched._compute_next_run(job, base)
    assert nxt is not None
    assert nxt > base  # 下一次触发在 now 之后


def test_compute_next_run_unsupported_type_raises(setup) -> None:
    store, _, sched = setup
    job = HeartbeatJob(
        id="x", name="n", enabled=True, channel_id="web", session_id="s1", prompt="p",
        schedule=HeartbeatSchedule.from_dict({"type": "interval", "interval_seconds": 120}),
    )
    # 篡改 type 触发 ValueError
    job.schedule.type = "bogus"
    with pytest.raises(ValueError, match="unsupported schedule type"):
        sched._compute_next_run(job, 1000.0)


# ---------------------------------------------------------------------------
# dispatch + 不传 mode(关键约束)
# ---------------------------------------------------------------------------


async def test_dispatch_does_not_set_mode_param(setup) -> None:
    store, mh, sched = setup
    job = await store.create_job(
        name="n", channel_id="web", session_id="s1", prompt="p",
        schedule=HeartbeatSchedule.from_dict({"type": "interval", "interval_seconds": 120}),
        source="agent_tool",
    )
    msg = sched._build_message(job, "run1", 1000.0)
    assert "mode" not in msg.params  # 关键:不传 mode
    assert msg.params["query"] == "p"
    assert msg.channel_id == "web"
    assert msg.session_id == "s1"
    assert msg.metadata["automation"]["kind"] == "heartbeat"
    assert msg.metadata["automation"]["job_id"] == job.id
    assert msg.metadata["automation"]["run_id"] == "run1"


async def test_dispatch_full_flow_marks_succeeded_and_reschedules(setup) -> None:
    store, mh, sched = setup
    job = await store.create_job(
        name="n", channel_id="web", session_id="s1", prompt="p",
        schedule=HeartbeatSchedule.from_dict({"type": "interval", "interval_seconds": 120}),
        source="agent_tool",
    )
    await store.update_job(job.id, {"next_run_at": 1.0})  # due
    await sched._tick_once()
    assert len(mh.messages) == 1
    j = await store.get_job(job.id)
    assert j.run_count == 1
    assert j.run_state.last_run_status == "succeeded"
    assert j.next_run_at is not None
    assert j.next_run_at > 1.0  # 基于 now 重算


# ---------------------------------------------------------------------------
# interval 不补跑历史积压
# ---------------------------------------------------------------------------


async def test_interval_does_not_backfill(setup) -> None:
    store, mh, sched = setup
    import time

    now = time.time()
    job = await store.create_job(
        name="n", channel_id="web", session_id="s1", prompt="p",
        schedule=HeartbeatSchedule.from_dict({"type": "interval", "interval_seconds": 120}),
        source="agent_tool",
    )
    # next_run_at 远早于 now(模拟服务离线很久)
    await store.update_job(job.id, {"next_run_at": now - 100000})
    await sched._tick_once()
    j = await store.get_job(job.id)
    # 执行一次后基于 now 重算,不补跑历史
    assert j.run_count == 1
    assert j.next_run_at >= now  # next 在 now 之后,不是 now - 100000 + 120


# ---------------------------------------------------------------------------
# once → completed 保留记录
# ---------------------------------------------------------------------------


async def test_once_schedule_marks_completed(setup) -> None:
    store, mh, sched = setup
    job = await store.create_job(
        name="once", channel_id="web", session_id="s1", prompt="p",
        schedule=HeartbeatSchedule.from_dict({"type": "once", "run_at": 9999.0}),
        source="agent_tool",
    )
    await store.update_job(job.id, {"next_run_at": 1.0})
    await sched._tick_once()
    j = await store.get_job(job.id)
    assert j.status == STATUS_COMPLETED
    assert j.next_run_at is None
    assert j.run_count == 1
    # 记录保留
    assert await store.get_job(job.id) is not None


# ---------------------------------------------------------------------------
# max_runs 停止条件
# ---------------------------------------------------------------------------


async def test_max_runs_reached_marks_completed(setup) -> None:
    store, mh, sched = setup
    job = await store.create_job(
        name="n", channel_id="web", session_id="s1", prompt="p",
        schedule=HeartbeatSchedule.from_dict({"type": "interval", "interval_seconds": 120}),
        source="agent_tool", max_runs=2,
    )
    for _ in range(2):
        await store.update_job(job.id, {"next_run_at": 1.0})
        await sched._tick_once()
    j = await store.get_job(job.id)
    assert j.run_count == 2
    assert j.status == STATUS_COMPLETED
    assert j.enabled is False


async def test_delete_after_run_marks_completed(setup) -> None:
    store, mh, sched = setup
    job = await store.create_job(
        name="n", channel_id="web", session_id="s1", prompt="p",
        schedule=HeartbeatSchedule.from_dict({"type": "interval", "interval_seconds": 120}),
        source="agent_tool", delete_after_run=True,
    )
    await store.update_job(job.id, {"next_run_at": 1.0})
    await sched._tick_once()
    j = await store.get_job(job.id)
    assert j.status == STATUS_COMPLETED
    assert j.run_count == 1


# ---------------------------------------------------------------------------
# 并发策略 skip
# ---------------------------------------------------------------------------


async def test_concurrency_skip_skips_when_run_active(setup, monkeypatch) -> None:
    store, mh, sched = setup
    job = await store.create_job(
        name="n", channel_id="web", session_id="s1", prompt="p",
        schedule=HeartbeatSchedule.from_dict({"type": "interval", "interval_seconds": 120}),
        source="agent_tool", concurrency_policy="skip",
    )
    await store.update_job(job.id, {"next_run_at": 1.0})  # due
    # 模拟上一轮运行中:get_active_run 返回活跃 run_id,但保持 status=scheduled。
    # (不能直接 mark_running,它会把 status 改成 running 导致 _handle_job_tick 跳过。)
    async def _fake_active_run(_jid: str) -> str | None:
        return "active_run"

    monkeypatch.setattr(store, "get_active_run", _fake_active_run)
    await sched._tick_once()
    # 应 skip:不投递新消息,记录 skipped
    j = await store.get_job(job.id)
    assert j.run_state.skipped_count == 1
    assert j.run_state.last_run_status == "skipped"
    assert len(mh.messages) == 0


async def test_concurrency_replace_downgrades_to_skip(setup, monkeypatch) -> None:
    store, mh, sched = setup
    job = await store.create_job(
        name="n", channel_id="web", session_id="s1", prompt="p",
        schedule=HeartbeatSchedule.from_dict({"type": "interval", "interval_seconds": 120}),
        source="agent_tool", concurrency_policy="replace",
    )
    await store.update_job(job.id, {"next_run_at": 1.0})

    async def _fake_active_run(_jid: str) -> str | None:
        return "active_run"

    monkeypatch.setattr(store, "get_active_run", _fake_active_run)
    await sched._tick_once()
    j = await store.get_job(job.id)
    # replace 第一版降级为 skip
    assert j.run_state.skipped_count == 1


# ---------------------------------------------------------------------------
# 状态机: enabled=true 但 status!=scheduled 的不一致 job 被跳过
# ---------------------------------------------------------------------------


async def test_inconsistent_job_skipped(setup) -> None:
    store, mh, sched = setup
    job = await store.create_job(
        name="n", channel_id="web", session_id="s1", prompt="p",
        schedule=HeartbeatSchedule.from_dict({"type": "interval", "interval_seconds": 120}),
        source="agent_tool",
    )
    # 手改成 completed 但 enabled=true(不一致)
    await store.update_job(job.id, {"next_run_at": 1.0})
    # 直接改 store:模拟手改文件造成 enabled=true status=completed
    # 通过 update_job 不能达成(它维护不变量),直接读改文件
    import json

    data = json.loads(store.path.read_text(encoding="utf-8"))
    for item in data["jobs"]:
        if item["id"] == job.id:
            item["status"] = "completed"
            item["enabled"] = True  # 不一致
    store.path.write_text(json.dumps(data), encoding="utf-8")
    await sched._tick_once()
    # scheduler 跳过(status != scheduled),不投递
    assert len(mh.messages) == 0


# ---------------------------------------------------------------------------
# session 不可达 → session_deleted_policy
# ---------------------------------------------------------------------------


@pytest.fixture
def missing_setup(tmp_path: Path):
    store = HeartbeatJobStore(path=tmp_path / "hb.json")
    mh = _FakeMH()
    sched = HeartbeatSchedulerService(store=store, message_handler=mh)
    sched._session_resolver = _MissingResolver()
    return store, mh, sched


async def test_missing_session_default_disable(missing_setup) -> None:
    store, _, sched = missing_setup
    job = await store.create_job(
        name="n", channel_id="web", session_id="gone", prompt="p",
        schedule=HeartbeatSchedule.from_dict({"type": "interval", "interval_seconds": 120}),
        source="agent_tool",  # 默认 session_deleted_policy=disable
    )
    await store.update_job(job.id, {"next_run_at": 1.0})
    await sched._tick_once()
    j = await store.get_job(job.id)
    assert j.status == STATUS_DISABLED
    assert j.enabled is False


async def test_missing_session_completed_policy(missing_setup) -> None:
    store, _, sched = missing_setup
    job = await store.create_job(
        name="n", channel_id="web", session_id="gone", prompt="p",
        schedule=HeartbeatSchedule.from_dict({"type": "interval", "interval_seconds": 120}),
        source="agent_tool", session_deleted_policy="completed",
    )
    await store.update_job(job.id, {"next_run_at": 1.0})
    await sched._tick_once()
    j = await store.get_job(job.id)
    assert j.status == STATUS_COMPLETED


async def test_on_session_deleted_disables_bound_jobs(setup) -> None:
    store, _, sched = setup
    j1 = await store.create_job(
        name="a", channel_id="web", session_id="sd1", prompt="p",
        schedule=HeartbeatSchedule.from_dict({"type": "interval", "interval_seconds": 120}),
        source="agent_tool",
    )
    j2 = await store.create_job(
        name="b", channel_id="web", session_id="sd1", prompt="p",
        schedule=HeartbeatSchedule.from_dict({"type": "interval", "interval_seconds": 120}),
        source="agent_tool", session_deleted_policy="completed",
    )
    j_other = await store.create_job(
        name="c", channel_id="web", session_id="sd2", prompt="p",
        schedule=HeartbeatSchedule.from_dict({"type": "interval", "interval_seconds": 120}),
        source="agent_tool",
    )
    await sched.on_session_deleted("sd1")
    assert (await store.get_job(j1.id)).status == STATUS_DISABLED
    assert (await store.get_job(j2.id)).status == STATUS_COMPLETED
    # 其他 session 的 job 不受影响
    other = await store.get_job(j_other.id)
    assert other.status == STATUS_SCHEDULED


# ---------------------------------------------------------------------------
# source 兜底
# ---------------------------------------------------------------------------


async def test_build_message_source_fallback(setup) -> None:
    store, _, sched = setup
    job = HeartbeatJob(
        id="x", name="n", enabled=True, channel_id="web", session_id="s1", prompt="p",
        schedule=HeartbeatSchedule.from_dict({"type": "interval", "interval_seconds": 120}),
        metadata={"source": "bad_value"},
    )
    msg = sched._build_message(job, "run1", 1.0)
    assert msg.metadata["automation"]["source"] == SOURCE_SCHEDULE_RECOVERY


# ---------------------------------------------------------------------------
# ghost task 清理
# ---------------------------------------------------------------------------


async def test_reload_clears_ghost_runs(setup) -> None:
    store, mh, sched = setup
    job = await store.create_job(
        name="n", channel_id="web", session_id="s1", prompt="p",
        schedule=HeartbeatSchedule.from_dict({"type": "interval", "interval_seconds": 120}),
        source="agent_tool",
    )
    # 模拟一个活跃 run 在内存中
    sched._active_runs["ghost_run"] = (job.id, 1000.0)
    # 删除 job → reload 应清理 ghost run
    await store.delete_job(job.id)
    await sched.reload()
    assert "ghost_run" not in sched._active_runs


# ---------------------------------------------------------------------------
# preview
# ---------------------------------------------------------------------------


def test_preview_interval(setup) -> None:
    store, _, sched = setup
    job = HeartbeatJob(
        id="x", name="n", enabled=True, channel_id="web", session_id="s1", prompt="p",
        schedule=HeartbeatSchedule.from_dict({"type": "interval", "interval_seconds": 600}),
    )
    out = sched.preview_next_runs(job, count=3)
    assert len(out) == 3
    assert all("run_at" in x and "iso" in x for x in out)


def test_preview_cron(setup) -> None:
    store, _, sched = setup
    job = HeartbeatJob(
        id="x", name="n", enabled=True, channel_id="web", session_id="s1", prompt="p",
        schedule=HeartbeatSchedule.from_dict({"type": "cron", "cron_expr": "0 9 * * *"}),
    )
    out = sched.preview_next_runs(job, count=2)
    assert len(out) == 2


def test_preview_once_returns_one_or_empty(setup) -> None:
    store, _, sched = setup
    import time

    future = time.time() + 3600
    job = HeartbeatJob(
        id="x", name="n", enabled=True, channel_id="web", session_id="s1", prompt="p",
        schedule=HeartbeatSchedule.from_dict({"type": "once", "run_at": future}),
    )
    out = sched.preview_next_runs(job, count=5)
    assert len(out) == 1  # once 只返回 1 条
    # 过去的 once 返回空
    past_job = HeartbeatJob(
        id="y", name="n", enabled=True, channel_id="web", session_id="s1", prompt="p",
        schedule=HeartbeatSchedule.from_dict({"type": "once", "run_at": 1.0}),
    )
    assert sched.preview_next_runs(past_job, count=5) == []


# ---------------------------------------------------------------------------
# cancel_run
# ---------------------------------------------------------------------------


async def test_cancel_run_pause_schedule(setup) -> None:
    store, _, sched = setup
    job = await store.create_job(
        name="n", channel_id="web", session_id="s1", prompt="p",
        schedule=HeartbeatSchedule.from_dict({"type": "interval", "interval_seconds": 120}),
        source="agent_tool",
    )
    result = await sched.cancel_run(job.id, pause_schedule=True)
    assert result["paused"] is True
    j = await store.get_job(job.id)
    assert j.status == STATUS_DISABLED


async def test_trigger_run_now_missing_session_disabled(missing_setup) -> None:
    store, _, sched = missing_setup
    job = await store.create_job(
        name="n", channel_id="web", session_id="gone", prompt="p",
        schedule=HeartbeatSchedule.from_dict({"type": "interval", "interval_seconds": 120}),
        source="agent_tool",
    )
    await sched.trigger_run_now(job.id)
    j = await store.get_job(job.id)
    # session 不存在 → missing → disable
    assert j.status == STATUS_DISABLED
