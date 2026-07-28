# DeepResearch Outline Stage Dedup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure one DeepResearch start-to-outline-auto-resume call chain emits Stage 2 once and names it “大纲生成” everywhere.

**Architecture:** Keep the public `deepresearch_stream` tool signature unchanged. Move its existing execution body behind a private implementation that accepts an optional `RouterState`; the public wrapper starts with a new state, while the internal `outline_interaction` auto-resume passes the existing state so `advance_stage()` performs its existing monotonic deduplication. Update only the DeepResearch runtime constant, its focused tests, and the OfficeClaw DeepResearch Skill text.

**Tech Stack:** Python 3.11+, pytest/pytest-asyncio, OpenJiuwen `@tool`, Markdown Skill specification.

---

### Task 1: Reproduce Stage 2 duplication and title mismatch

**Files:**
- Modify: `tests/unit/agentserver/test_deepresearch_stream_tool.py:2672`
- Modify: `tests/unit/agentserver/test_deepresearch_stream_router.py:14`

- [ ] **Step 1: Add the automatic-resume dedup assertion**

Extend `test_outline_interaction_is_resumed_inside_the_tool_without_returning_control_to_model`
after its existing process assertions:

```python
    payloads = [call.args[0]["payload"] for call in push.send_push.await_args_list]
    stage_2_updates = [
        payload for payload in _task_updates(payloads)
        if _active_stage(payload) == 2
    ]
    stage_2_messages = [
        payload for payload in payloads
        if payload.get("task_id") == "deepresearch_stage_2"
        and payload.get("event_type") in {"chat.reasoning", "chat.delta"}
        and payload.get("content", "").startswith("[DeepResearch 阶段切换]")
    ]
    assert len(stage_2_updates) == 1
    assert [payload["event_type"] for payload in stage_2_messages] == [
        "chat.reasoning",
        "chat.delta",
    ]
```

- [ ] **Step 2: Add the Stage 2 title contract**

Change the Stage 2 entry in `STAGE_TITLES` to `"大纲生成"` and add:

```python
def test_stage_2_uses_outline_generation_title_on_all_surfaces():
    frames = advance_stage(RouterState(), 2)

    assert frames[0]["tasks"][1]["task_content"] == "大纲生成"
    assert frames[1]["task_content"] == "大纲生成"
    assert frames[1]["content"] == "[DeepResearch 阶段切换] 开始 Stage 2：大纲生成\n"
    assert frames[2]["task_content"] == "大纲生成"
    assert frames[2]["content"] == "[DeepResearch 阶段切换] 开始 Stage 2：大纲生成\n"
```

- [ ] **Step 3: Run the two tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest \
  tests/unit/agentserver/test_deepresearch_stream_tool.py::test_outline_interaction_is_resumed_inside_the_tool_without_returning_control_to_model \
  tests/unit/agentserver/test_deepresearch_stream_router.py::test_stage_2_uses_outline_generation_title_on_all_surfaces \
  -q
```

Expected: two assertion failures. The tool test sees two Stage 2 updates/message pairs, and the router test sees the old title `大纲生成与确认`.

### Task 2: Reuse routing state during internal outline resume

**Files:**
- Modify: `jiuwenclaw/agentserver/tools/deepresearch/tools.py:1551-1938`
- Test: `tests/unit/agentserver/test_deepresearch_stream_tool.py`

- [ ] **Step 1: Separate the public tool wrapper from the private implementation**

Keep the decorated public function’s current parameters exactly as they are. Move the existing execution body to:

```python
async def _deepresearch_stream_impl(
    action: str,
    query: str = "",
    conversation_id: str = "",
    feedback: str = "",
    node: str = "",
    file_name: str = "",
    interaction_result: str = "",
    _router_state=None,
) -> str:
```

Make the existing internal call helper target this private function:

```python
async def _call_deepresearch_stream_impl(**kwargs) -> str:
    return await _deepresearch_stream_impl(**kwargs)
```

Define the decorated public wrapper with the unchanged public signature:

```python
@tool(
    name="deepresearch_stream",
    description=(
        "deepresearch skill 的首选且唯一入口。流式执行 DeepResearch 深度研究,"
        "进度经 chat 通道(chat.reasoning/task.start/task.complete/"
        "processing_status)实时推送到前端。执行到人机交互节点时返回 interrupted outcome,"
        "由 agent 调 ask_user_question 处理后,再以 action=resume 调本工具恢复。"
        "outline_interaction 由工具内部以 accepted 自动恢复，不返回给 agent；"
        "feedback_handler 恢复时须把 ask_user_question 完整返回值作为"
        " interaction_result 传入；工具会把 skipped 或 answered+空答案"
        ' 归一化为 feedback={"feedback":"","interaction_status":"skipped"}，'
        "不得默认选择任何选项或改写为自然语言反馈。"
        "不返回中间 chunk,只返回 outcome,避免污染 agent context。"
        "⚠不适用场景:PPT制作辅助研究、单点数据查询、快速搜索"
    ),
)
async def deepresearch_stream(
    action: str,
    query: str = "",
    conversation_id: str = "",
    feedback: str = "",
    node: str = "",
    file_name: str = "",
    interaction_result: str = "",
) -> str:
    return await _call_deepresearch_stream_impl(
        action=action,
        query=query,
        conversation_id=conversation_id,
        feedback=feedback,
        node=node,
        file_name=file_name,
        interaction_result=interaction_result,
    )
```

- [ ] **Step 2: Reuse the supplied state**

Replace unconditional state creation with:

```python
    state = _router_state or RouterState(section_titles=dict(cached_titles))
```

Pass the current state only through the internal automatic-resume call:

```python
        return await _call_deepresearch_stream_impl(
            action="resume",
            conversation_id=str(outcome.get("conversation_id", outcome_cid)),
            feedback='{"interrupt_feedback":"accepted","feedback":""}',
            node="outline_interaction",
            file_name=file_name,
            _router_state=state,
        )
```

- [ ] **Step 3: Run the automatic-resume test and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest \
  tests/unit/agentserver/test_deepresearch_stream_tool.py::test_outline_interaction_is_resumed_inside_the_tool_without_returning_control_to_model \
  -q
```

Expected: `1 passed`.

### Task 3: Rename the runtime and Skill Stage 2 title

**Files:**
- Modify: `jiuwenclaw/agentserver/tools/deepresearch/stream_router.py:57-65`
- Modify: `/Users/hualinge/tmp/relay-claw-tmp/relay-claw_2766/office-claw-skills/deepresearch/SKILL.md:93`
- Modify: `/Users/hualinge/tmp/relay-claw-tmp/relay-claw_2766/office-claw-skills/deepresearch/SKILL.md:361`
- Test: `tests/unit/agentserver/test_deepresearch_stream_router.py`

- [ ] **Step 1: Rename the runtime Stage 2 constant**

Change only this tuple item:

```python
DEEPRESEARCH_STAGES: tuple[str, ...] = (
    "研究主题澄清",
    "大纲生成",
    "并行调研与章节撰写",
    "报告整合",
    "引用溯源与校验",
    "报告交付",
)
```

- [ ] **Step 2: Rename the Skill chapter and summary**

Apply these two exact text changes:

```markdown
## Stage 2: 大纲生成
```

```markdown
> Pipeline 全流程完成：Stage 1 研究主题澄清 → Stage 2 大纲生成 → Stage 3 并行调研与章节撰写 → Stage 4 报告整合 → Stage 5 引用溯源与校验 → Stage 6 Markdown 报告交付。Main Agent 全程主控交互与恢复，研究、写作和溯源仍由 DeepResearch  workflow 执行；chat 通道下行确保前端实时可见进度，中间 chunk 不污染 Agent context。
```

- [ ] **Step 3: Run the title test and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest \
  tests/unit/agentserver/test_deepresearch_stream_router.py::test_stage_2_uses_outline_generation_title_on_all_surfaces \
  -q
```

Expected: `1 passed`.

### Task 4: Verify and commit both main branches

**Files:**
- Verify: `jiuwenclaw/agentserver/tools/deepresearch/tools.py`
- Verify: `jiuwenclaw/agentserver/tools/deepresearch/stream_router.py`
- Verify: `tests/unit/agentserver/test_deepresearch_stream_tool.py`
- Verify: `tests/unit/agentserver/test_deepresearch_stream_router.py`
- Verify: `/Users/hualinge/tmp/relay-claw-tmp/relay-claw_2766/office-claw-skills/deepresearch/SKILL.md`

- [ ] **Step 1: Run the focused JiuwenClaw suite**

Run:

```bash
.venv/bin/python -m pytest \
  tests/unit/agentserver/test_deepresearch_stream_router.py \
  tests/unit/agentserver/test_deepresearch_stream_tool.py \
  -q
```

Expected: all tests pass with zero failures.

- [ ] **Step 2: Run syntax and whitespace checks**

Run:

```bash
.venv/bin/python -m py_compile \
  jiuwenclaw/agentserver/tools/deepresearch/tools.py \
  jiuwenclaw/agentserver/tools/deepresearch/stream_router.py
git diff --check
```

In OfficeClaw run:

```bash
git diff --check -- office-claw-skills/deepresearch/SKILL.md
```

Expected: all commands exit `0` without output.

- [ ] **Step 3: Commit JiuwenClaw `enterprise_dev`**

Stage only these files:

```bash
git add \
  jiuwenclaw/agentserver/tools/deepresearch/tools.py \
  jiuwenclaw/agentserver/tools/deepresearch/stream_router.py \
  tests/unit/agentserver/test_deepresearch_stream_tool.py \
  tests/unit/agentserver/test_deepresearch_stream_router.py
git commit -m "fix(deepresearch): deduplicate outline stage transition"
```

- [ ] **Step 4: Commit OfficeClaw `main`**

Stage only the two Stage 2 wording hunks from
`office-claw-skills/deepresearch/SKILL.md`, preserving any unrelated working-tree edits, then run:

```bash
git commit -m "docs(deepresearch): rename outline stage"
```

- [ ] **Step 5: Restart and validate runtime identity**

Restart only the existing OfficeClaw API, Green Package web, and JiuwenClaw sidecar processes using
their current process-scoped Huawei CAS environment. Do not modify `.env`, Redis, SQLite, or other
persistent data. Verify:

```text
API source revision == OfficeClaw main HEAD
sidecar source revision == JiuwenClaw enterprise_dev HEAD
/api/islogin provider == huawei-cas
/api/islogin isskip == false
```

Run a DeepResearch outline flow and confirm the persisted event stream contains exactly one
Stage 2 `task.update`, one Stage 2 `chat.reasoning`, and one Stage 2 `chat.delta`, each using
“大纲生成”.
