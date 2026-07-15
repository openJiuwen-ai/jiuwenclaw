"""压测脚本完成判定单元测试。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_module():
    script = (
        Path(__file__).resolve().parents[2]
        / "packages/jiuwenclaw-ee/claw_manager/scripts/enterprise_runtime_concurrent_test.py"
    )
    spec = importlib.util.spec_from_file_location("enterprise_runtime_concurrent_test", script)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_build_route_plan_shards2_buckets_user_id():
    mod = _load_module()
    plans = mod._build_route_plan(
        concurrency=6,
        shards=3,
        shards2=2,
        group_prefix="loadtest",
        user_id_prefix="loadtest_user",
    )
    assert len(plans) == 6
    assert plans == [
        mod.RoutePlan(shard=0, shard2=0, group_id="loadtest_s0", bot_id="bot_main", user_id="loadtest_user_s0_a0"),
        mod.RoutePlan(shard=1, shard2=0, group_id="loadtest_s1", bot_id="bot_main", user_id="loadtest_user_s1_a0"),
        mod.RoutePlan(shard=2, shard2=0, group_id="loadtest_s2", bot_id="bot_main", user_id="loadtest_user_s2_a0"),
        mod.RoutePlan(shard=0, shard2=1, group_id="loadtest_s0", bot_id="bot_main", user_id="loadtest_user_s0_a1"),
        mod.RoutePlan(shard=1, shard2=1, group_id="loadtest_s1", bot_id="bot_main", user_id="loadtest_user_s1_a1"),
        mod.RoutePlan(shard=2, shard2=1, group_id="loadtest_s2", bot_id="bot_main", user_id="loadtest_user_s2_a1"),
    ]


def test_build_route_plan_shards2_reuses_user_id_per_agent_bucket():
    mod = _load_module()
    plans = mod._build_route_plan(
        concurrency=12,
        shards=3,
        shards2=2,
        group_prefix="loadtest",
        user_id_prefix="loadtest_user",
    )
    assert len(plans) == 12
    assert plans[0] == mod.RoutePlan(
        shard=0, shard2=0, group_id="loadtest_s0", bot_id="bot_main", user_id="loadtest_user_s0_a0"
    )
    assert plans[1] == mod.RoutePlan(
        shard=1, shard2=0, group_id="loadtest_s1", bot_id="bot_main", user_id="loadtest_user_s1_a0"
    )
    assert plans[2] == mod.RoutePlan(
        shard=2, shard2=0, group_id="loadtest_s2", bot_id="bot_main", user_id="loadtest_user_s2_a0"
    )
    assert plans[3] == mod.RoutePlan(
        shard=0, shard2=1, group_id="loadtest_s0", bot_id="bot_main", user_id="loadtest_user_s0_a1"
    )
    assert plans[4] == mod.RoutePlan(
        shard=1, shard2=1, group_id="loadtest_s1", bot_id="bot_main", user_id="loadtest_user_s1_a1"
    )
    assert plans[5] == mod.RoutePlan(
        shard=2, shard2=1, group_id="loadtest_s2", bot_id="bot_main", user_id="loadtest_user_s2_a1"
    )
    assert plans[6] == mod.RoutePlan(
        shard=0, shard2=0, group_id="loadtest_s0", bot_id="bot_main", user_id="loadtest_user_s0_a0"
    )
    assert plans[7] == mod.RoutePlan(
        shard=1, shard2=0, group_id="loadtest_s1", bot_id="bot_main", user_id="loadtest_user_s1_a0"
    )


def test_build_route_plan_allows_uneven_shards_and_shards2():
    mod = _load_module()
    plans = mod._build_route_plan(
        concurrency=7,
        shards=3,
        shards2=2,
        group_prefix="loadtest",
        user_id_prefix="loadtest_user",
    )
    assert len(plans) == 7
    assert [p.shard for p in plans] == [0, 1, 2, 0, 1, 2, 0]
    shard_counts = {0: 0, 1: 0, 2: 0}
    agent_counts: dict[tuple[int, int], int] = {}
    for p in plans:
        shard_counts[p.shard] += 1
        agent_counts[(p.shard, p.shard2)] = agent_counts.get((p.shard, p.shard2), 0) + 1
    assert shard_counts == {0: 3, 1: 2, 2: 2}
    assert agent_counts[(0, 0)] == 2
    assert agent_counts[(0, 1)] == 1
    assert agent_counts[(2, 1)] == 1


def test_build_route_plan_shards2_one_hits_same_agent_per_shard():
    mod = _load_module()
    plans = mod._build_route_plan(
        concurrency=6,
        shards=3,
        shards2=1,
        group_prefix="loadtest",
        user_id_prefix="loadtest_user",
    )
    assert len(plans) == 6
    assert plans[0].user_id == "loadtest_user_s0_a0"
    assert plans[3].user_id == "loadtest_user_s0_a0"
    assert plans[1].user_id == "loadtest_user_s1_a0"
    assert plans[4].user_id == "loadtest_user_s1_a0"


def test_build_route_plan_service_shard_key_bot_id():
    mod = _load_module()
    plans = mod._build_route_plan(
        concurrency=3,
        shards=3,
        shards2=0,
        group_prefix="loadtest",
        user_id_prefix="loadtest_user",
        bot_id="bot_main",
        service_shard_key="bot_id",
    )
    assert plans == [
        mod.RoutePlan(shard=0, shard2=0, group_id="loadtest", bot_id="bot_main_s0", user_id="loadtest_user_00"),
        mod.RoutePlan(shard=1, shard2=0, group_id="loadtest", bot_id="bot_main_s1", user_id="loadtest_user_01"),
        mod.RoutePlan(shard=2, shard2=0, group_id="loadtest", bot_id="bot_main_s2", user_id="loadtest_user_02"),
    ]


def test_build_route_plan_default_unique_user_per_idx():
    mod = _load_module()
    plans = mod._build_route_plan(
        concurrency=3,
        shards=3,
        shards2=0,
        group_prefix="loadtest",
        user_id_prefix="loadtest_user",
    )
    assert [p.user_id for p in plans] == [
        "loadtest_user_00",
        "loadtest_user_01",
        "loadtest_user_02",
    ]


def test_loadtest_terminal_ready_requires_file_only():
    mod = _load_module()
    assert not mod._loadtest_terminal_ready(
        saw_deliverable_file=False,
        saw_post_deliverable_text=True,
    )
    assert mod._loadtest_terminal_ready(
        saw_deliverable_file=True,
        saw_post_deliverable_text=False,
    )
    assert mod._loadtest_terminal_ready(
        saw_deliverable_file=True,
        saw_post_deliverable_text=True,
    )


def test_hitl_resume_clear_includes_chat_file():
    mod = _load_module()
    assert "chat.delta" in mod._HITL_RESUME_CLEAR_EVENTS
    assert "chat.file" in mod._HITL_RESUME_CLEAR_EVENTS


def test_usage_summary_blocked_before_deliverable_milestone():
    mod = _load_module()
    assert not mod._should_complete_invoke(
        accepted=True,
        saw_agent_output=True,
        hitl_paused=False,
        hitl_await_agent_resume=False,
        saw_deliverable_file=False,
        saw_post_deliverable_text=False,
        event="chat.usage_summary",
        payload={},
    )


def test_usage_summary_completes_after_deliverable_milestone():
    mod = _load_module()
    assert mod._should_complete_invoke(
        accepted=True,
        saw_agent_output=True,
        hitl_paused=False,
        hitl_await_agent_resume=False,
        saw_deliverable_file=True,
        saw_post_deliverable_text=True,
        event="chat.usage_summary",
        payload={},
    )


def test_usage_summary_completes_after_file_without_post_text():
    """短 allow 子流：file 后可能几乎没有 stage8 delta，仍应完成。"""
    mod = _load_module()
    assert mod._should_complete_invoke(
        accepted=True,
        saw_agent_output=True,
        hitl_paused=False,
        hitl_await_agent_resume=False,
        saw_deliverable_file=True,
        saw_post_deliverable_text=False,
        event="chat.usage_summary",
        payload={},
    )


def test_processing_idle_blocked_at_stage3_without_file():
    mod = _load_module()
    assert not mod._should_complete_on_processing_idle(
        accepted=True,
        saw_agent_output=True,
        hitl_paused=False,
        hitl_suppress_next_idle=False,
        hitl_await_agent_resume=False,
        saw_deliverable_file=False,
        saw_post_deliverable_text=False,
        payload={"is_processing": False},
    )


def test_processing_idle_completes_after_file_without_stage8_text():
    mod = _load_module()
    assert mod._should_complete_on_processing_idle(
        accepted=True,
        saw_agent_output=True,
        hitl_paused=False,
        hitl_suppress_next_idle=False,
        hitl_await_agent_resume=False,
        saw_deliverable_file=True,
        saw_post_deliverable_text=False,
        payload={"is_processing": False},
    )


def test_usage_summary_blocked_during_hitl_resume_wait():
    mod = _load_module()
    assert not mod._should_complete_invoke(
        accepted=True,
        saw_agent_output=True,
        hitl_paused=False,
        hitl_await_agent_resume=True,
        saw_deliverable_file=True,
        saw_post_deliverable_text=True,
        event="chat.usage_summary",
        payload={},
    )


def test_extract_runtime_failure_chat_error():
    mod = _load_module()
    err = mod._extract_runtime_failure(
        "chat.error",
        {"error": "boom", "event_type": "chat.error"},
    )
    assert err is not None
    assert "boom" in err
    assert err.startswith("runtime_error:")


def test_extract_runtime_failure_capacity_code_100001():
    mod = _load_module()
    err = mod._extract_runtime_failure(
        "chat.error",
        {"code": 100001, "error": "服务并发度超过上限，消息请求失败"},
    )
    assert err is not None
    assert err.startswith("capacity_error:")
    assert "100001" in err or "服务并发度超过上限" in err


def test_extract_runtime_failure_capacity_code_100002_without_chat_error_event():
    mod = _load_module()
    err = mod._extract_runtime_failure(
        "chat.delta",
        {"code": "100002", "message": "无足够并发为新 session 预留"},
    )
    assert err is not None
    assert err.startswith("capacity_error:")
    assert "无足够并发" in err


def test_extract_runtime_failure_ignores_normal_payload():
    mod = _load_module()
    assert mod._extract_runtime_failure("chat.delta", {"content": "hello"}) is None
    assert mod._extract_runtime_failure("chat.file", {"path": "/tmp/a.md"}) is None


def test_premature_idle_fails_when_accepted_without_deliverable():
    """资源拒绝后 Gateway 只补 is_processing=false 时应立即失败。"""
    mod = _load_module()
    assert mod._should_fail_on_premature_idle(
        accepted=True,
        hitl_paused=False,
        hitl_suppress_next_idle=False,
        hitl_await_agent_resume=False,
        saw_deliverable_file=False,
        saw_post_deliverable_text=False,
        payload={"is_processing": False},
    )


def test_premature_idle_fails_after_allow_when_agent_never_resumes():
    mod = _load_module()
    assert mod._should_fail_on_premature_idle(
        accepted=True,
        hitl_paused=False,
        hitl_suppress_next_idle=False,
        hitl_await_agent_resume=True,
        saw_deliverable_file=False,
        payload={"is_processing": False},
    )


def test_premature_idle_not_while_waiting_user_answer():
    mod = _load_module()
    assert not mod._should_fail_on_premature_idle(
        accepted=True,
        hitl_paused=True,
        hitl_suppress_next_idle=False,
        hitl_await_agent_resume=False,
        saw_deliverable_file=False,
        payload={"is_processing": False},
    )


def test_premature_idle_not_when_suppressing_post_allow_idle():
    mod = _load_module()
    assert not mod._should_fail_on_premature_idle(
        accepted=True,
        hitl_paused=False,
        hitl_suppress_next_idle=True,
        hitl_await_agent_resume=True,
        saw_deliverable_file=False,
        payload={"is_processing": False},
    )


def test_premature_idle_not_after_deliverable():
    mod = _load_module()
    assert not mod._should_fail_on_premature_idle(
        accepted=True,
        hitl_paused=False,
        hitl_suppress_next_idle=False,
        hitl_await_agent_resume=False,
        saw_deliverable_file=True,
        payload={"is_processing": False},
    )
