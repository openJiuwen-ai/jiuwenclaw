# DeepResearch Stage Gap Backfill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure every DeepResearch stage between the current and target stage emits synchronized task, reasoning, and foreground events, even when workflow node events are absent.

**Architecture:** Keep stage progression entirely inside the DeepResearch-specific `advance_stage()` router boundary. Replace the single-target transition with a monotonic loop that emits one complete three-event group per missing stage; leave completion and error behavior unchanged.

**Tech Stack:** Python 3.12, pytest, async unit tests, JiuwenClaw DeepResearch stream router.

---

## File structure

- Modify `jiuwenclaw/agentserver/tools/deepresearch/stream_router.py`: generate a complete event group for every missing stage.
- Modify `tests/unit/agentserver/test_deepresearch_stream_router.py`: reproduce a Stage 3 to Stage 6 jump and verify ordering and deduplication.
- Modify `tests/unit/agentserver/test_deepresearch_stream_tool.py`: update successful terminal-path expectations to require continuous stages while retaining error-path behavior.

### Task 1: Reproduce the missing Stage 4 and Stage 5 events

**Files:**
- Test: `tests/unit/agentserver/test_deepresearch_stream_router.py`

- [ ] **Step 1: Add a failing router regression test**

Add:

```python
def test_advance_stage_backfills_every_missing_stage_in_event_order():
    state = RouterState(current_stage=3)

    frames = advance_stage(state, 6)

    assert [frame["event_type"] for frame in frames] == [
        "task.update",
        "chat.reasoning",
        "chat.delta",
        "task.update",
        "chat.reasoning",
        "chat.delta",
        "task.update",
        "chat.reasoning",
        "chat.delta",
    ]
    updates = [
        frame for frame in frames if frame["event_type"] == "task.update"
    ]
    assert [
        next(
            index
            for index, task in enumerate(update["tasks"], start=1)
            if task["status"] == "in_progress"
        )
        for update in updates
    ] == [4, 5, 6]
    assert [
        frame["content"]
        for frame in frames
        if frame["event_type"] == "chat.delta"
    ] == [
        "[DeepResearch 阶段切换] 开始 Stage 4：报告整合\n",
        "[DeepResearch 阶段切换] 开始 Stage 5：引用溯源与校验\n",
        "[DeepResearch 阶段切换] 开始 Stage 6：报告交付\n",
    ]
    assert state.current_stage == 6
```

- [ ] **Step 2: Run the regression test and verify RED**

Run:

```bash
/Users/hualinge/vscodeproject/jiuwenclaw/.venv/bin/python -m pytest \
  tests/unit/agentserver/test_deepresearch_stream_router.py::test_advance_stage_backfills_every_missing_stage_in_event_order \
  -q
```

Expected: FAIL because the current implementation returns only one Stage 6 event group.

### Task 2: Implement monotonic gap filling

**Files:**
- Modify: `jiuwenclaw/agentserver/tools/deepresearch/stream_router.py`
- Test: `tests/unit/agentserver/test_deepresearch_stream_router.py`

- [ ] **Step 1: Extract one-stage event generation**

Add a private helper that assumes `state.current_stage` already identifies the stage being rendered:

```python
def _stage_transition_frames(state: RouterState) -> list[dict]:
    tasks = []
    for index, title in enumerate(DEEPRESEARCH_STAGES, start=1):
        if index < state.current_stage:
            status = "completed"
        elif index == state.current_stage:
            status = "in_progress"
        else:
            status = "pending"
        tasks.append({
            "task_id": f"deepresearch_stage_{index}",
            "task_content": title,
            "status": status,
        })

    task_update = {
        "event_type": "task.update",
        "tasks": tasks,
        "total_tasks": len(DEEPRESEARCH_STAGES),
        "completed_tasks": state.current_stage - 1,
        "in_progress_tasks": 1,
        "pending_tasks": len(DEEPRESEARCH_STAGES) - state.current_stage,
    }
    title = DEEPRESEARCH_STAGES[state.current_stage - 1]
    message = {
        "task_id": f"deepresearch_stage_{state.current_stage}",
        "task_content": title,
        "content": (
            f"[DeepResearch 阶段切换] 开始 Stage "
            f"{state.current_stage}：{title}\n"
        ),
    }
    return [
        task_update,
        {"event_type": "chat.reasoning", **message},
        {"event_type": "chat.delta", **message},
    ]
```

- [ ] **Step 2: Make ordinary advancement emit every missing stage**

Change the non-completion branch of `advance_stage()` to:

```python
if state.stages_completed or stage <= state.current_stage:
    return []
if stage < 1 or stage > len(DEEPRESEARCH_STAGES):
    raise ValueError(f"invalid deepresearch stage: {stage}")

frames: list[dict] = []
for next_stage in range(state.current_stage + 1, stage + 1):
    state.current_stage = next_stage
    frames.extend(_stage_transition_frames(state))
return frames
```

Keep the `complete=True` branch responsible for the existing all-completed snapshot and Stage 6 completion message. Do not route errors or cancellations through the gap-filling branch.

- [ ] **Step 3: Run the router regression test and verify GREEN**

Run:

```bash
/Users/hualinge/vscodeproject/jiuwenclaw/.venv/bin/python -m pytest \
  tests/unit/agentserver/test_deepresearch_stream_router.py::test_advance_stage_backfills_every_missing_stage_in_event_order \
  -q
```

Expected: `1 passed`.

- [ ] **Step 4: Run the complete router test file**

Run:

```bash
/Users/hualinge/vscodeproject/jiuwenclaw/.venv/bin/python -m pytest \
  tests/unit/agentserver/test_deepresearch_stream_router.py -q
```

Expected: all router tests pass.

### Task 3: Align tool-level success-path expectations

**Files:**
- Modify: `tests/unit/agentserver/test_deepresearch_stream_tool.py`

- [ ] **Step 1: Require continuous stages for successful terminal paths**

Update successful-path assertions so that:

```python
assert [_active_stage(update) for update in task_updates] == [
    1, 2, 3, 4, 5, 6, None,
]
```

For a successful path that contains only `started` followed by `completed`, require:

```python
assert [_active_stage(update) for update in _task_updates(payloads)] == [
    1, 2, 3, 4, 5, 6, None,
]
```

For the outline auto-resume success path, require:

```python
assert [_active_stage(update) for update in _task_updates(payloads)] == [
    1, 2, 3, 4, 5, 6, None,
]
```

Update Stage-facing reasoning and delta assertions to include the same missing stages in order. Keep the Stage 2 single-emission count unchanged.

- [ ] **Step 2: Keep error paths bounded by the last observed stage**

Require:

```python
assert [_active_stage(update) for update in _task_updates(payloads)] == [
    1, 2, 3,
]
```

for the research error path. The observed Stage 3 node backfills the missing Stage 2 event, while
the following error marker does not synthesize Stage 4, 5, or 6.

- [ ] **Step 3: Run the tool-level stage tests**

Run:

```bash
/Users/hualinge/vscodeproject/jiuwenclaw/.venv/bin/python -m pytest \
  tests/unit/agentserver/test_deepresearch_stream_tool.py \
  -k 'stage or completed_report or outline_interaction_is_resumed_inside' \
  -q
```

Expected: all selected tests pass.

### Task 4: Verify and commit the implementation

**Files:**
- Modify: `jiuwenclaw/agentserver/tools/deepresearch/stream_router.py`
- Modify: `tests/unit/agentserver/test_deepresearch_stream_router.py`
- Modify: `tests/unit/agentserver/test_deepresearch_stream_tool.py`

- [ ] **Step 1: Run the complete focused DeepResearch suite**

Run:

```bash
/Users/hualinge/vscodeproject/jiuwenclaw/.venv/bin/python -m pytest \
  tests/unit/agentserver/test_deepresearch_stream_router.py \
  tests/unit/agentserver/test_deepresearch_stream_tool.py \
  -q
```

Expected: all tests pass with zero failures.

- [ ] **Step 2: Run syntax and whitespace checks**

Run:

```bash
/Users/hualinge/vscodeproject/jiuwenclaw/.venv/bin/python -m py_compile \
  jiuwenclaw/agentserver/tools/deepresearch/stream_router.py \
  tests/unit/agentserver/test_deepresearch_stream_router.py \
  tests/unit/agentserver/test_deepresearch_stream_tool.py
git diff --check
```

Expected: both commands exit 0 with no output.

- [ ] **Step 3: Review the exact diff against the design**

Run:

```bash
git diff --stat
git diff -- \
  jiuwenclaw/agentserver/tools/deepresearch/stream_router.py \
  tests/unit/agentserver/test_deepresearch_stream_router.py \
  tests/unit/agentserver/test_deepresearch_stream_tool.py
```

Expected: only the DeepResearch router and its two test files contain implementation changes.

- [ ] **Step 4: Commit**

Run:

```bash
git add \
  jiuwenclaw/agentserver/tools/deepresearch/stream_router.py \
  tests/unit/agentserver/test_deepresearch_stream_router.py \
  tests/unit/agentserver/test_deepresearch_stream_tool.py
git commit -m "fix(deepresearch): backfill missing stage transitions"
```

Expected: one implementation commit is created on `codex/deepresearch-stage-gap-backfill`.

### Task 5: Integrate and verify the mainline runtime

**Files:**
- No additional source changes expected.

- [ ] **Step 1: Recheck mainline and worktree state**

Run:

```bash
git status --short --branch
git -C /Users/hualinge/vscodeproject/jiuwenclaw status --short --branch
git merge-base --is-ancestor enterprise_dev HEAD
```

Expected: the feature worktree is clean, the base relationship is valid, and unrelated untracked files in the main checkout remain untouched.

- [ ] **Step 2: Fast-forward `enterprise_dev`**

Run from `/Users/hualinge/vscodeproject/jiuwenclaw`:

```bash
git merge --ff-only codex/deepresearch-stage-gap-backfill
```

Expected: `enterprise_dev` advances to the feature branch commit without a merge commit.

- [ ] **Step 3: Verify tests on the merged mainline**

Run:

```bash
/Users/hualinge/vscodeproject/jiuwenclaw/.venv/bin/python -m pytest \
  tests/unit/agentserver/test_deepresearch_stream_router.py \
  tests/unit/agentserver/test_deepresearch_stream_tool.py \
  -q
```

Expected: all tests pass with zero failures.

- [ ] **Step 4: Restart and smoke-test the existing OfficeClaw service**

Restart with the existing process-scoped Huawei CAS and Green Package frontend settings. Verify:

```text
3003 frontend cwd = packages/green-package/web
3004 /health = HTTP 200
/api/islogin provider = huawei-cas
/api/islogin isskip = false
/login/callback = HTTP 200
agentserver cwd = /Users/hualinge/vscodeproject/jiuwenclaw
```

Do not modify `.env`, runtime configuration files, Redis, or persistent data.
