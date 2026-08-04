"""mock_llm_server loadtest Agent 阶段机与场景路由单元测试。"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_module():
    script = (
        Path(__file__).resolve().parents[2]
        / "packages/jiuwenclaw-ee/claw_manager/scripts/mock_llm_server.py"
    )
    spec = importlib.util.spec_from_file_location("mock_llm_server", script)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


_TRAVEL = (
    "帮我写一篇十万字的小说，主题是旅行的意义，写完后保存到txt发给我。"
    "直接开始写，不要问我其他问题"
)
_SKILL = (
    "先使用skillnet安装这个旅游攻略技能"
    "https://github.com/Asif2BD/openclaw.tours/tree/main，"
    "然后再给我制作一个北京3日游的旅游攻略"
)
_ECHO = (
    "完成！我已经为你创作了小说《旅行的意义》的完整开篇部分，"
    "并保存为 旅行的意义_开篇完整版.txt 发送给你。"
)
_FILE = "帮我把这个文件里的作文扩写到6000字，然后发回给我"
_CRON_CREATE = "创建一个定时任务，1分钟后提醒我喝水"
_CRON_WAKE = "🥤 喝水时间到啦！记得喝杯水，保持水分摄入～"


def _wrap(content: str) -> str:
    return "你收到一条消息：\n" + json.dumps(
        {
            "source": "web",
            "preferred_response_language": "zh",
            "content": content,
            "files_updated_by_user": "{}",
            "type": "user input",
        },
        ensure_ascii=False,
    )


def _user(content: str) -> dict:
    return {"role": "user", "content": _wrap(content)}


def _asst(text: str) -> dict:
    return {"role": "assistant", "content": text}


def _with_session_path(payload: dict, sess: str) -> dict:
    messages = list(payload["messages"])
    first = dict(messages[0])
    first["content"] = first["content"] + f" path={sess}"
    messages[0] = first
    out = dict(payload)
    out["messages"] = messages
    return out


def test_travel_plan_stages_follow_tool_chain():
    mod = _load_module()
    payload = {"messages": [_user(_TRAVEL), _asst("x")], "tools": [{}] * 5}
    plan0 = mod._plan_travel_flow_response(
        payload, stage=0, novel_chars=800, excerpt_chars=200
    )
    assert plan0.kind == "intro_and_tool_call" and plan0.tool_name == "todo_create"

    plan1 = mod._plan_travel_flow_response(
        payload, stage=1, novel_chars=800, excerpt_chars=200
    )
    assert plan1.kind == "stream_text" and plan1.text

    plan2 = mod._plan_travel_flow_response(
        payload, stage=2, novel_chars=800, excerpt_chars=200
    )
    assert plan2.tool_name == "bash"

    plan3 = mod._plan_travel_flow_response(
        payload, stage=3, novel_chars=800, excerpt_chars=200
    )
    assert plan3.tool_name == "write_file"

    plan4 = mod._plan_travel_flow_response(
        payload, stage=4, novel_chars=800, excerpt_chars=200
    )
    assert plan4.tool_name == "read_file"

    for stage in (5, 6, 7, 8):
        plan = mod._plan_travel_flow_response(
            payload, stage=stage, novel_chars=800, excerpt_chars=200
        )
        assert plan.tool_name == "todo_modify", stage

    plan9 = mod._plan_travel_flow_response(
        payload, stage=9, novel_chars=800, excerpt_chars=200
    )
    assert plan9.tool_name == "send_file_to_user"

    plan10 = mod._plan_travel_flow_response(
        payload, stage=10, novel_chars=800, excerpt_chars=200
    )
    assert plan10.tool_name == "todo_modify"

    plan11 = mod._plan_travel_flow_response(
        payload, stage=11, novel_chars=800, excerpt_chars=200
    )
    assert plan11.kind == "stream_text" and plan11.text == mod._NOVEL_FINAL_MESSAGE


def test_travel_session_state_advances_until_done():
    mod = _load_module()
    mod._loadtest_states.clear()
    sess = "sess_ut_travel_stages"
    payload = _with_session_path(
        {"messages": [_user(_TRAVEL), _asst("x"), _user(_TRAVEL)], "tools": [{}] * 5},
        sess,
    )
    scenario = mod._detect_loadtest_scenario(payload)
    key, stage, effective = mod._prepare_loadtest_state(payload, scenario)
    assert key and stage == 0 and effective == mod.MockScenario.TRAVEL
    for used in range(0, 11):
        mod._advance_loadtest_state(key, effective, used, payload)
        state = mod._loadtest_states[key]
        assert state.stage == used + 1 and not state.done
    mod._advance_loadtest_state(key, effective, 11, payload)
    state = mod._loadtest_states[key]
    assert state.done and state.stage == 11


def test_detect_prefers_skill_over_travel_reappend():
    mod = _load_module()
    messages = [
        _user(_TRAVEL),
        _asst("working..."),
        _user(_TRAVEL),
        _asst(_ECHO),
        _user(_SKILL),
        _user(_TRAVEL),
    ]
    assert mod._detect_loadtest_scenario({"messages": messages, "tools": [{}] * 5}) == (
        mod.MockScenario.SKILL
    )


def test_completion_echo_as_user_does_not_block_skill():
    mod = _load_module()
    messages = [
        _user(_TRAVEL),
        _asst("working..."),
        _user(_ECHO),
        _user(_SKILL),
    ]
    assert mod._detect_loadtest_scenario({"messages": messages, "tools": [{}] * 5}) == (
        mod.MockScenario.SKILL
    )


def test_travel_done_then_skill_routes_to_skill_stage0():
    mod = _load_module()
    mod._loadtest_states.clear()
    sess = "sess_ut_travel_skill"
    base = [_user(_TRAVEL), _asst("x")] * 5
    payload_travel = _with_session_path({"messages": base, "tools": [{}] * 5}, sess)
    s1 = mod._detect_loadtest_scenario(payload_travel)
    k1, _, sc1 = mod._prepare_loadtest_state(payload_travel, s1)
    mod._advance_loadtest_state(k1, sc1, 11, payload_travel)

    deduped = base[:3] + [_asst(_ECHO), _user(_SKILL), _user(_TRAVEL)]
    payload_skill = _with_session_path({"messages": deduped, "tools": [{}] * 5}, sess)
    s2 = mod._detect_loadtest_scenario(payload_skill)
    _, st2, sc2 = mod._prepare_loadtest_state(payload_skill, s2)
    assert s2 == mod.MockScenario.SKILL
    assert sc2 == mod.MockScenario.SKILL and st2 == 0


def test_post_done_echo_only_falls_back_to_skill_sequence():
    mod = _load_module()
    mod._loadtest_states.clear()
    sess = "sess_ut_echo_skill"
    base = [_user(_TRAVEL), _asst("x")] * 5
    payload_done = _with_session_path({"messages": base, "tools": [{}] * 5}, sess)
    s_done = mod._detect_loadtest_scenario(payload_done)
    k_done, _, sc_done = mod._prepare_loadtest_state(payload_done, s_done)
    mod._advance_loadtest_state(k_done, sc_done, 11, payload_done)

    echo_only = base + [_asst(_ECHO), _user(_ECHO)]
    payload_echo = _with_session_path({"messages": echo_only, "tools": [{}] * 5}, sess)
    _, st_echo, sc_echo = mod._prepare_loadtest_state(
        payload_echo, mod._detect_loadtest_scenario(payload_echo)
    )
    assert sc_echo == mod.MockScenario.SKILL and st_echo == 0


def test_todo_modify_uses_latest_skill_todo_create_ids():
    mod = _load_module()
    travel_create = {
        "role": "assistant",
        "tool_calls": [
            {
                "id": "call_travel_create",
                "function": {"name": "todo_create", "arguments": '{"tasks": "travel"}'},
            }
        ],
    }
    travel_result = {
        "role": "tool",
        "tool_call_id": "call_travel_create",
        "content": (
            "Created 5 todo tasks.\n"
            "task_id: old-travel-1, content: 《旅行的意义》开篇\n"
            "task_id: old-travel-2, content: 人物详细介绍\n"
        ),
    }
    skill_create = {
        "role": "assistant",
        "tool_calls": [
            {
                "id": "call_skill_create",
                "function": {
                    "name": "todo_create",
                    "arguments": '{"tasks": "安装旅游规划技能;...", "force": true}',
                },
            }
        ],
    }
    skill_result = {
        "role": "tool",
        "tool_call_id": "call_skill_create",
        "content": (
            "Created 4 todo tasks.\n"
            "task_id: skill-new-1, content: 安装旅游规划技能\n"
            "task_id: skill-new-2, content: 收集北京旅游信息\n"
            "task_id: skill-new-3, content: 生成北京3日游攻略\n"
            "task_id: skill-new-4, content: 交付攻略文档\n"
        ),
    }
    msgs = [travel_create, travel_result, skill_create, skill_result]
    parsed = mod._parse_todo_items(msgs)
    assert parsed[0][0] == "skill-new-1"
    args = mod._todo_modify_complete_args(msgs, 0, fallback_contents=mod._SKILL_TODO_CONTENTS)
    assert args["todos"][0]["id"] == "skill-new-1"


def test_skill_done_todo_echo_falls_back_to_file():
    mod = _load_module()
    mod._loadtest_states.clear()
    sess = "sess_ut_skill_file"
    base = [_user(_TRAVEL), _asst("x")] * 5
    skill_msgs = base + [_asst(_ECHO), _user(_SKILL), _asst("skill working...")] * 8
    payload_skill_done = _with_session_path({"messages": skill_msgs, "tools": [{}] * 5}, sess)
    s_skill = mod._detect_loadtest_scenario(payload_skill_done)
    k_skill, _, sc_skill = mod._prepare_loadtest_state(payload_skill_done, s_skill)
    mod._advance_loadtest_state(k_skill, sc_skill, 17, payload_skill_done)
    state_skill = mod._loadtest_states.get(k_skill)

    todo_echo_msgs = skill_msgs + [_user("故事冲突与悬念设置")]
    payload_todo_echo = _with_session_path({"messages": todo_echo_msgs, "tools": [{}] * 5}, sess)
    next_sc = mod._detect_loadtest_scenario_after_done(payload_todo_echo, state_skill)
    assert next_sc == mod.MockScenario.FILE

    _, st_file, sc_file = mod._prepare_loadtest_state(payload_todo_echo, mod.MockScenario.TRAVEL)
    assert sc_file == mod.MockScenario.FILE and st_file == 0


def test_skill_done_file_upload_intent_routes_to_file():
    mod = _load_module()
    mod._loadtest_states.clear()
    sess = "sess_ut_file_upload"
    base = [_user(_TRAVEL), _asst("x")] * 5
    skill_msgs = base + [_asst(_ECHO), _user(_SKILL), _asst("skill working...")] * 8
    payload_skill_done = _with_session_path(
        {"messages": skill_msgs, "tools": [{}] * 5}, f"{sess}_file"
    )
    s2 = mod._detect_loadtest_scenario(payload_skill_done)
    k2, _, sc2 = mod._prepare_loadtest_state(payload_skill_done, s2)
    mod._advance_loadtest_state(k2, sc2, 17, payload_skill_done)
    state2 = mod._loadtest_states.get(k2)

    file_user = _user(_FILE)
    file_user["content"] = _wrap(_FILE).replace(
        '"files_updated_by_user": "{}"',
        '"files_updated_by_user": "{\\"童趣的春天.md\\": \\"uploaded\\"}"',
    )
    file_msgs = skill_msgs + [file_user]
    payload_file = _with_session_path({"messages": file_msgs, "tools": [{}] * 5}, f"{sess}_file")
    next_file = mod._detect_loadtest_scenario_after_done(payload_file, state2)
    assert next_file == mod.MockScenario.FILE


def test_deduped_history_with_travel_user_does_not_reroute_to_travel():
    mod = _load_module()
    mod._loadtest_states.clear()
    sess = "sess_ut_dedup"
    dedup_with_travel = (
        [_user(_TRAVEL), _asst("x"), _user(_TRAVEL), _asst(_ECHO), _user(_SKILL)]
        + [_asst("s")] * 10
    )
    payload_dedup = _with_session_path(
        {"messages": dedup_with_travel, "tools": [{}] * 5}, f"{sess}_dedup"
    )
    sd = mod._detect_loadtest_scenario(payload_dedup)
    kd, _, scd = mod._prepare_loadtest_state(payload_dedup, sd)
    mod._advance_loadtest_state(kd, scd, 17, payload_dedup)
    st_dedup = mod._loadtest_states.get(kd)

    short_msgs = dedup_with_travel[:3] + [_user("故事冲突与悬念设置")]
    payload_short = _with_session_path(
        {"messages": short_msgs, "tools": [{}] * 5}, f"{sess}_dedup"
    )
    next_dedup = mod._detect_loadtest_scenario_after_done(payload_short, st_dedup)
    assert next_dedup == mod.MockScenario.FILE


def test_cron_tool_defs_do_not_hijack_travel_to_skill_sequence():
    mod = _load_module()
    mod._loadtest_states.clear()
    sess = "sess_ut_cron_tools"
    base = [_user(_TRAVEL), _asst("x")] * 5
    payload_travel_only = _with_session_path(
        {"messages": base, "tools": [{"function": {"name": "cron_create_job"}}] * 5},
        f"{sess}_cron",
    )
    st = mod._detect_loadtest_scenario(payload_travel_only)
    kt, _, sct = mod._prepare_loadtest_state(payload_travel_only, st)
    mod._advance_loadtest_state(kt, sct, 11, payload_travel_only)
    state_travel = mod._loadtest_states.get(kt)

    echo_only2 = base + [_asst(_ECHO), _user(_ECHO)]
    payload_cron_tools = _with_session_path(
        {
            "messages": echo_only2,
            "tools": [{"function": {"name": "cron_create_job"}}] * 5,
        },
        f"{sess}_cron",
    )
    next_cron = mod._detect_loadtest_scenario_after_done(payload_cron_tools, state_travel)
    assert next_cron == mod.MockScenario.SKILL


def test_file_bash_fallback_writes_mock_essay_via_heredoc():
    mod = _load_module()
    upload_empty = {"name": mod._SPRING_ESSAY_SOURCE, "url": "", "path": ""}
    bash_cmd = mod._build_file_download_bash(upload_empty)
    assert "MOCK_ESSAY_EOF" in bash_cmd and "童趣的春天" in bash_cmd
    assert "\n&& ls" not in bash_cmd


def test_file_upload_extracted_from_earlier_user_message_before_reappend():
    mod = _load_module()
    file_with_url = _user(_FILE)
    file_with_url["content"] = _wrap(_FILE).replace(
        '"files_updated_by_user": "{}"',
        (
            '"files_updated_by_user": "{\\"童趣的春天.md\\": '
            '{\\"url\\": \\"http://127.0.0.1:8321/download?sandbox_path=test\\", '
            '\\"name\\": \\"童趣的春天.md\\"}}"'
        ),
    )
    reappend = _user(_FILE)
    files = mod._extract_uploaded_files([file_with_url, reappend])
    assert files and files[0].get("url")


def test_scheduled_task_creates_cron_job_via_cron_create_job():
    mod = _load_module()
    cron_payload = {
        "messages": [_user(_CRON_CREATE)],
        "tools": [{"function": {"name": "cron_create_job"}}] * 5,
    }
    assert mod._detect_loadtest_scenario(cron_payload) == mod.MockScenario.SCHEDULED_TASK
    plan, scenario, stage = mod._plan_loadtest_response(
        cron_payload, novel_chars=100, excerpt_chars=50
    )
    assert scenario == mod.MockScenario.SCHEDULED_TASK and stage == 0
    assert plan.tool_name == "cron_create_job"


def test_cron_delivery_returns_drink_reminder():
    mod = _load_module()
    wake_payload = {
        "messages": [_user(_CRON_WAKE)],
        "tools": [{"function": {"name": "bash"}}],
    }
    assert mod._should_use_novel_scenario("loadtest", wake_payload)
    plan, scenario, _ = mod._plan_loadtest_response(
        wake_payload, novel_chars=100, excerpt_chars=50
    )
    assert scenario == mod.MockScenario.CRON_DELIVERY
    assert "喝水时间到啦" in (plan.text or "")


def test_cron_delivery_distinct_from_scheduled_task_creation():
    mod = _load_module()
    cron_payload = {
        "messages": [_user(_CRON_CREATE)],
        "tools": [{"function": {"name": "cron_create_job"}}] * 5,
    }
    cron_ctx_payload = {
        "messages": [_user(_CRON_WAKE)],
        "tools": [{"function": {"name": "bash"}}] * 5,
    }
    cron_ctx_payload["messages"][0]["content"] = (
        cron_ctx_payload["messages"][0]["content"]
        + " path=/context/cron_19f84e04cd2_d0229c213a754bc1813bdb2cd87a3740_context"
    )
    assert mod._is_cron_delivery_request(cron_ctx_payload)
    assert not mod._is_cron_delivery_request(cron_payload)
