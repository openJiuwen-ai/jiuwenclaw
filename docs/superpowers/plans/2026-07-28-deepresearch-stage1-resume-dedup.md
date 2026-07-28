# DeepResearch Stage 1 Resume Dedup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure a `feedback_handler` resume does not emit the Stage 1 transition a second time, while Stage 2 is still emitted only after the outline node is observed.

**Architecture:** Keep the existing `stream_router` monotonic stage contract unchanged. Seed a newly created DeepResearch router state with Stage 1 as the already-reached checkpoint only for `action="resume", node="feedback_handler"`; the existing `advance_stage` guard then suppresses the duplicate resume marker and advances normally on the first outline chunk.

**Tech Stack:** Python 3.12, pytest, `unittest.mock.AsyncMock`, JiuwenClaw DeepResearch stream routing.

---

### Task 1: Reproduce the start-to-feedback-resume duplicate

**Files:**
- Modify: `tests/unit/agentserver/test_deepresearch_stream_tool.py`

- [ ] **Step 1: Add a failing two-invocation regression test**

Add this test near the existing feedback resume tests:

```python
@pytest.mark.asyncio
async def test_feedback_resume_does_not_repeat_stage_1_transition():
    start_lines = [
        json.dumps({"__deepsearch_status__": "started", "conversation_id": "C1"}),
        json.dumps({
            "__deepsearch_status__": "interrupted",
            "agent": "feedback_handler",
            "conversation_id": "C1",
        }),
    ]
    resume_lines = [
        json.dumps({"__deepsearch_status__": "resuming", "conversation_id": "C1"}),
        json.dumps({"agent": "outline", "content": "# 第一章"}),
        json.dumps({
            "__deepsearch_status__": "error",
            "conversation_id": "C1",
            "error": "stop after outline",
        }),
    ]
    push = AsyncMock()
    spawn = AsyncMock(side_effect=[_Proc(start_lines), _Proc(resume_lines)])

    with patch.object(dt, "_resolve_jiuwenclaw_python", return_value="/p"), \
         patch.object(dt, "_resolve_run_script", return_value="/s"), \
         patch.object(dt, "_get_route", return_value={
             "request_id": "R1", "channel_id": "CH1", "session_id": "S1",
         }), \
         patch("asyncio.create_subprocess_exec", new=spawn), \
         patch(
             "jiuwenclaw.agentserver.gateway_push.transport.WebSocketGatewayPushTransport",
             return_value=push,
         ):
        interrupted = await dt.deepresearch_stream._func(
            action="start", query="X", file_name="r",
        )
        resumed = await dt.deepresearch_stream._func(
            action="resume",
            conversation_id="C1",
            node="feedback_handler",
            feedback='{"feedback":"补充范围"}',
            file_name="r",
        )

    assert json.loads(interrupted)["status"] == "interrupted"
    assert json.loads(resumed)["status"] == "error"
    payloads = [call.args[0]["payload"] for call in push.send_push.await_args_list]
    transitions = [
        (payload["event_type"], payload["task_id"])
        for payload in payloads
        if payload.get("event_type") in {"chat.reasoning", "chat.delta"}
        and payload.get("content", "").startswith("[DeepResearch 阶段切换]")
    ]
    assert transitions == [
        ("chat.reasoning", "deepresearch_stage_1"),
        ("chat.delta", "deepresearch_stage_1"),
        ("chat.reasoning", "deepresearch_stage_2"),
        ("chat.delta", "deepresearch_stage_2"),
    ]
    assert [_active_stage(update) for update in _task_updates(payloads)] == [1, 2]
```

- [ ] **Step 2: Run the regression test and verify RED**

Run:

```bash
/Users/hualinge/vscodeproject/jiuwenclaw/.venv/bin/python -m pytest \
  tests/unit/agentserver/test_deepresearch_stream_tool.py::test_feedback_resume_does_not_repeat_stage_1_transition -q
```

Expected: FAIL because the current implementation produces a second Stage 1 reasoning/delta pair and active-stage sequence `[1, 1, 2]`.

### Task 2: Seed the feedback resume stage baseline

**Files:**
- Modify: `jiuwenclaw/agentserver/tools/deepresearch/tools.py`
- Test: `tests/unit/agentserver/test_deepresearch_stream_tool.py`

- [ ] **Step 1: Implement the minimum state initialization change**

Replace the new-state construction with:

```python
    resume_stage = (
        1
        if action == "resume" and node == "feedback_handler"
        else 0
    )
    state = (
        existing_state
        if existing_state is not None
        else RouterState(
            section_titles=dict(cached_titles),
            current_stage=resume_stage,
        )
    )
```

Do not change `advance_stage`, the `resuming` marker handling, or other resume-node mappings.

- [ ] **Step 2: Run the regression test and verify GREEN**

Run:

```bash
/Users/hualinge/vscodeproject/jiuwenclaw/.venv/bin/python -m pytest \
  tests/unit/agentserver/test_deepresearch_stream_tool.py::test_feedback_resume_does_not_repeat_stage_1_transition -q
```

Expected: PASS.

- [ ] **Step 3: Run the existing outline auto-resume regression**

Run:

```bash
/Users/hualinge/vscodeproject/jiuwenclaw/.venv/bin/python -m pytest \
  tests/unit/agentserver/test_deepresearch_stream_tool.py::test_outline_interaction_is_resumed_inside_the_tool_without_returning_control_to_model -q
```

Expected: PASS, confirming Stage 2 remains deduplicated.

### Task 3: Verify and commit the focused fix

**Files:**
- Modify: `jiuwenclaw/agentserver/tools/deepresearch/tools.py`
- Modify: `tests/unit/agentserver/test_deepresearch_stream_tool.py`

- [ ] **Step 1: Run all DeepResearch router and stream-tool tests**

Run:

```bash
/Users/hualinge/vscodeproject/jiuwenclaw/.venv/bin/python -m pytest \
  tests/unit/agentserver/test_deepresearch_stream_router.py \
  tests/unit/agentserver/test_deepresearch_stream_tool.py -q
```

Expected: all tests pass.

- [ ] **Step 2: Run syntax and whitespace checks**

Run:

```bash
/Users/hualinge/vscodeproject/jiuwenclaw/.venv/bin/python -m py_compile \
  jiuwenclaw/agentserver/tools/deepresearch/tools.py \
  tests/unit/agentserver/test_deepresearch_stream_tool.py
git diff --check
```

Expected: both commands exit successfully without output.

- [ ] **Step 3: Review the final diff**

Run:

```bash
git diff --stat
git diff -- \
  jiuwenclaw/agentserver/tools/deepresearch/tools.py \
  tests/unit/agentserver/test_deepresearch_stream_tool.py
```

Expected: only the feedback-resume state baseline and its focused regression test are present.

- [ ] **Step 4: Commit the implementation**

Run:

```bash
git add \
  jiuwenclaw/agentserver/tools/deepresearch/tools.py \
  tests/unit/agentserver/test_deepresearch_stream_tool.py
git commit -m "fix(deepresearch): avoid repeated stage 1 on resume"
```

Expected: one implementation commit containing exactly the two scoped files.
