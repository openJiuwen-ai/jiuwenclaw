"""压测脚本完成判定单元测试。"""

from __future__ import annotations

import importlib.util
import sys
import time
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


def test_cron_creation_text_does_not_mark_step_done():
    mod = _load_module()
    creation = (
        "✅ 喝水提醒已创建！\n\n⏰ 执行时间：1 分钟后\n\n"
        "📝 提醒内容：🥤 喝水时间到啦！记得喝杯水，保持水分摄入～"
    )
    assert mod._is_cron_creation_text(creation)
    assert not mod._is_cron_delivery_text(creation)
    assert not mod._content_marks_step_done(
        expect_file=False,
        expect_delayed_text=True,
        content=creation,
    )


def test_cron_delivery_text_marks_step_done():
    mod = _load_module()
    delivery = "🥤 喝水时间到啦！记得喝杯水，保持水分摄入～"
    assert mod._is_cron_delivery_text(delivery)
    assert mod._content_marks_step_done(
        expect_file=False,
        expect_delayed_text=True,
        content=delivery,
    )


def test_event_session_id_prefers_payload():
    mod = _load_module()
    assert (
        mod._event_session_id(
            {"session_id": "sess_a"},
            {"session_id": "sess_b"},
        )
        == "sess_b"
    )
    assert mod._event_session_id({"session_id": "sess_a"}, {}) == "sess_a"
    assert mod._event_session_id({}, {}) == ""


def test_premature_idle_not_after_cron_creation_subflow():
    mod = _load_module()
    assert not mod._should_fail_on_premature_idle(
        expect_file=False,
        expect_delayed_text=True,
        accepted=True,
        hitl_paused=False,
        hitl_suppress_next_idle=False,
        hitl_await_agent_resume=False,
        saw_deliverable_file=False,
        saw_step_text=False,
        payload={"is_processing": False},
    )


def test_premature_idle_still_fails_for_non_delayed_text_step():
    mod = _load_module()
    assert mod._should_fail_on_premature_idle(
        expect_file=False,
        expect_delayed_text=False,
        accepted=True,
        hitl_paused=False,
        hitl_suppress_next_idle=False,
        hitl_await_agent_resume=False,
        saw_deliverable_file=False,
        saw_step_text=False,
        payload={"is_processing": False},
    )


def test_build_loadtest_cron_step_waits_for_delivery():
    mod = _load_module()
    steps = mod._build_default_loadtest_steps(mod._DEFAULT_SPRING_ESSAY)
    cron = steps[-1]
    assert cron.name == "cron"
    assert "1分钟后" in cron.content
    assert cron.expect_delayed_text is True
    assert cron.expect_file is False


def test_build_loadtest_file_step_downloads_deliverable():
    mod = _load_module()
    steps = mod._build_default_loadtest_steps(mod._DEFAULT_SPRING_ESSAY)
    file_step = next(s for s in steps if s.name == "file")
    assert file_step.download_deliverable is True


def test_make_run_download_dir_appends_timestamp():
    mod = _load_module()
    when = time.strptime("2026-07-21 21:15:25", "%Y-%m-%d %H:%M:%S")
    path = mod._make_run_download_dir(when=when)
    assert path.name == "download_20260721_211525"
    assert path.parent == mod._SCRIPT_DIR


def test_indexed_download_filename_appends_request_index():
    mod = _load_module()
    assert mod._indexed_download_filename("童趣的春天_扩写版.md", 0) == "童趣的春天_扩写版_00.md"
    assert mod._indexed_download_filename("report.txt", 12) == "report_12.txt"


def test_absolute_download_url_resolves_relative_path():
    mod = _load_module()
    assert (
        mod._absolute_download_url("ws://10.0.0.1:30105/ws", "/file-api/download?token=abc")
        == "http://10.0.0.1:30105/file-api/download?token=abc"
    )
    assert (
        mod._absolute_download_url("ws://10.0.0.1:30105/ws", "https://cdn.example/a.md")
        == "https://cdn.example/a.md"
    )


def test_extract_downloadable_files_requires_download_url():
    mod = _load_module()
    assert mod._extract_downloadable_files({"files": [{"name": "a.md", "path": "/tmp/a.md"}]}) == []
    assert mod._extract_downloadable_files(
        {"files": [{"name": "a.md", "download_url": "/file-api/download?token=t"}]}
    ) == [{"name": "a.md", "download_url": "/file-api/download?token=t"}]


def test_hitl_resume_clear_includes_chat_file():
    mod = _load_module()
    assert "chat.delta" in mod._HITL_RESUME_CLEAR_EVENTS
    assert "chat.file" in mod._HITL_RESUME_CLEAR_EVENTS
    assert "chat.tool_result" in mod._HITL_RESUME_CLEAR_EVENTS


def test_usage_summary_blocked_during_hitl_resume_wait_without_file():
    mod = _load_module()
    assert not mod._should_complete_invoke(
        accepted=True,
        saw_agent_output=True,
        hitl_paused=False,
        hitl_await_agent_resume=True,
        saw_deliverable_file=False,
        saw_post_deliverable_text=True,
        event="chat.usage_summary",
        payload={},
    )


def test_usage_summary_completes_during_hitl_resume_wait_after_file():
    """skill_complete 放行后可能直接 usage_summary，无中间 delta。"""
    mod = _load_module()
    assert mod._should_complete_invoke(
        accepted=True,
        saw_agent_output=True,
        hitl_paused=False,
        hitl_await_agent_resume=True,
        saw_deliverable_file=True,
        saw_post_deliverable_text=False,
        event="chat.usage_summary",
        payload={},
    )


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
