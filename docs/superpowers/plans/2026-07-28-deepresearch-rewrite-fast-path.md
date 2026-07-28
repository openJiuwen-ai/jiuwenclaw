# DeepResearch Rewrite Fast Path Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route strict DeepResearch rewrite envelopes through prepare, exactly one direct model invocation, and commit without entering the generic Agent runner.

**Architecture:** Add a focused `deepresearch_rewrite_fast_path.py` module that owns strict envelope parsing, minimal prompt construction, one-call orchestration, safe errors, and timing. Add a small branch in `JiuWenClawDeepAdapter.process_message_stream_impl` after request context/runtime configuration is ready; recognized requests emit the existing final response and skip `Runner.run_agent_streaming`, while all other requests keep the current path.

**Tech Stack:** Python 3.12, asyncio, OpenJiuwen `Model.invoke`, pytest, pytest-asyncio.

---

## File Map

- Create `jiuwenclaw/agentserver/deep_agent/deepresearch_rewrite_fast_path.py`: strict parser, prompt builder, safe JSON handling, single-call orchestration, timing result.
- Create `tests/unit/agentserver/test_deepresearch_rewrite_fast_path.py`: parser, orchestration, prompt privacy, and short-circuit tests.
- Modify `jiuwenclaw/agentserver/deep_agent/interface_deep.py`: invoke fast path after runtime context setup and skip Runner for recognized requests.
- Create `tests/unit/agentserver/test_deepresearch_rewrite_fast_path_adapter.py`: adapter routing and stream terminal-state regression tests.

### Task 1: Strict Envelope Parser

**Files:**
- Create: `tests/unit/agentserver/test_deepresearch_rewrite_fast_path.py`
- Create: `jiuwenclaw/agentserver/deep_agent/deepresearch_rewrite_fast_path.py`

- [ ] **Step 1: Write parser tests before the module exists**

Add tests that define the desired public API:

```python
from jiuwenclaw.agentserver.deep_agent.deepresearch_rewrite_fast_path import (
    RewriteFastPathError,
    parse_rewrite_envelope,
)

def test_parse_rewrite_envelope_accepts_exact_request():
    request = parse_rewrite_envelope(_query(action="polish"))
    assert request is not None
    assert request.action == "polish"
    assert request.instruction == ""

def test_parse_rewrite_envelope_ignores_non_exact_wrapper():
    assert parse_rewrite_envelope("please " + _query()) is None

@pytest.mark.parametrize("payload", ["not json", '{"action":"delete"}'])
def test_parse_rewrite_envelope_rejects_recognized_invalid_request(payload):
    with pytest.raises(RewriteFastPathError) as exc:
        parse_rewrite_envelope(
            f"<deepresearch_rewrite_request>{payload}</deepresearch_rewrite_request>"
        )
    assert exc.value.code == "BAD_REQUEST"
```

- [ ] **Step 2: Run the parser tests and verify RED**

Run:

```bash
/Users/hualinge/vscodeproject/jiuwenclaw/.venv/bin/python -m pytest \
  -o addopts='' tests/unit/agentserver/test_deepresearch_rewrite_fast_path.py -q
```

Expected: collection fails with `ModuleNotFoundError` for
`deepresearch_rewrite_fast_path`.

- [ ] **Step 3: Implement the minimum strict parser**

Implement immutable `RewriteRequest`, `RewriteFastPathError`, and
`parse_rewrite_envelope(query)`. The parser must:

```python
_ENVELOPE_RE = re.compile(
    r"\A\s*<deepresearch_rewrite_request>(?P<body>.*?)"
    r"</deepresearch_rewrite_request>\s*\Z",
    re.DOTALL,
)
_REQUEST_KEYS = {"report_path", "action", "selection", "instruction"}
_ACTIONS = {"polish", "expand", "shorten"}
```

Return `None` only when the exact wrapper does not match. Once matched, reject
non-object JSON, missing/extra keys, non-string `report_path`/`instruction`,
non-dict `selection`, and unsupported actions with safe `BAD_REQUEST`.

- [ ] **Step 4: Run parser tests and verify GREEN**

Run the Step 2 command. Expected: all parser tests pass.

- [ ] **Step 5: Commit parser behavior**

```bash
git add \
  jiuwenclaw/agentserver/deep_agent/deepresearch_rewrite_fast_path.py \
  tests/unit/agentserver/test_deepresearch_rewrite_fast_path.py
git commit -m "feat(deepresearch): parse strict rewrite requests"
```

### Task 2: One-Call Rewrite Orchestrator

**Files:**
- Modify: `tests/unit/agentserver/test_deepresearch_rewrite_fast_path.py`
- Modify: `jiuwenclaw/agentserver/deep_agent/deepresearch_rewrite_fast_path.py`

- [ ] **Step 1: Add failing success and short-circuit tests**

Use `AsyncMock` dependencies and assert behavior, not internal implementation:

```python
@pytest.mark.asyncio
async def test_run_fast_path_calls_prepare_model_commit_once_in_order():
    calls = []
    prepare = AsyncMock(side_effect=lambda **_: _record(calls, "prepare", PREPARED))
    model = AsyncMock(side_effect=lambda _: _record(calls, "model", MODEL_RESPONSE))
    commit = AsyncMock(side_effect=lambda **_: _record(calls, "commit", COMPLETED))

    result = await run_rewrite_fast_path(
        _query(), model_invoke=model, prepare_invoke=prepare, commit_invoke=commit
    )

    assert result.status == "completed"
    assert calls == ["prepare", "model", "commit"]
    prepare.assert_awaited_once()
    model.assert_awaited_once()
    commit.assert_awaited_once()

@pytest.mark.asyncio
async def test_run_fast_path_stops_before_model_when_prepare_fails():
    result = await run_rewrite_fast_path(
        _query(),
        prepare_invoke=AsyncMock(return_value=_error("REVISION_CONFLICT")),
        model_invoke=AsyncMock(),
        commit_invoke=AsyncMock(),
    )
    assert result.error_code == "REVISION_CONFLICT"
    assert result.model_calls == 0
```

Also cover model exception, missing content, code fence, trailing JSON text,
invalid structured result, commit error, unrecognized query, and ensure prompt
contains only the allowlisted prepared fields and not `context_token`.

- [ ] **Step 2: Run the orchestrator tests and verify RED**

Run the Task 1 test command. Expected: failures because
`run_rewrite_fast_path` and result types are absent.

- [ ] **Step 3: Implement minimum one-call orchestration**

Add:

```python
@dataclass(frozen=True)
class RewriteFastPathResult:
    recognized: bool
    status: str
    action: str | None
    error_code: str | None
    message: str
    usage_metadata: object | None
    prepare_ms: float
    model_ms: float
    commit_ms: float
    total_ms: float
    model_calls: int
```

`run_rewrite_fast_path` must parse first, invoke prepare/commit with keyword
arguments, decode their JSON strings, call `model_invoke(messages)` exactly
once, read `response.content`, require a single raw JSON object, and never
include `context_token` in the prompt. Keep prompt constants in this module.

Map unexpected dependency exceptions to safe codes:

- prepare JSON/transport failure: `INTERNAL_ERROR`
- model exception: `MODEL_CALL_FAILED`
- model output parse failure: `MODEL_OUTPUT_INVALID`
- commit JSON/transport failure: `WRITE_FAILED`

Return existing prepare/commit `error_code` unchanged.

- [ ] **Step 4: Run orchestrator tests and verify GREEN**

Run the Task 1 test command. Expected: all fast-path module tests pass.

- [ ] **Step 5: Commit orchestration**

```bash
git add \
  jiuwenclaw/agentserver/deep_agent/deepresearch_rewrite_fast_path.py \
  tests/unit/agentserver/test_deepresearch_rewrite_fast_path.py
git commit -m "feat(deepresearch): add single-call rewrite flow"
```

### Task 3: Adapter Routing

**Files:**
- Create: `tests/unit/agentserver/test_deepresearch_rewrite_fast_path_adapter.py`
- Modify: `jiuwenclaw/agentserver/deep_agent/interface_deep.py`

- [ ] **Step 1: Add failing adapter routing tests**

Extract one small adapter helper with a wished-for API:

```python
async def _try_deepresearch_rewrite_fast_path(
    self, query: str
) -> RewriteFastPathResult | None:
    from jiuwenclaw.agentserver.tools.deepresearch import rewrite_tools

    return await run_rewrite_fast_path(
        query,
        model_invoke=self._model.invoke,
        prepare_invoke=rewrite_tools.deepresearch_prepare_rewrite._func,
        commit_invoke=rewrite_tools.deepresearch_commit_rewrite._func,
    )
```

Tests construct the adapter with `object.__new__(JiuWenClawDeepAdapter)`, set
`_model`, and patch the two existing tool `_func` callables. Assert:

- plain query returns `None` and does not call model/prepare/commit;
- valid query passes `self._model.invoke` and calls each dependency once;
- model usage metadata is preserved in the result.

Add a lightweight source-path test around a new helper
`_fast_path_chunks(result, request_id, channel_id)` asserting success produces
one `chat.final` with:

```text
本轮改写已完成。若报告已是最终版本，请回复‘生成 HTML’；如需继续改写，可直接选择下一处内容。
```

and errors produce one safe `chat.final` without selection/model text.

- [ ] **Step 2: Run adapter tests and verify RED**

Run:

```bash
/Users/hualinge/vscodeproject/jiuwenclaw/.venv/bin/python -m pytest \
  -o addopts='' \
  tests/unit/agentserver/test_deepresearch_rewrite_fast_path_adapter.py -q
```

Expected: failure because adapter helpers do not exist.

- [ ] **Step 3: Add minimal adapter helpers and branch**

In `interface_deep.py`:

1. import fast-path result and runner;
2. add `_try_deepresearch_rewrite_fast_path`;
3. add `_fast_path_chunks`;
4. immediately after `_update_runtime_config`, call the helper;
5. if result is recognized, accumulate its `usage_metadata`, log action/status,
   safe error code and phase timings, yield fast-path chunks, and skip the
   `ask_user_question_request_scope`/Runner block;
6. otherwise execute the current block byte-for-byte.

Do not add fallback from recognized failures to Runner. Leave the existing
`finally`, usage summary, and final `is_complete=True` chunk in control of
cleanup and the sole terminal event.

- [ ] **Step 4: Run adapter tests and verify GREEN**

Run the Step 2 command. Expected: all adapter tests pass.

- [ ] **Step 5: Run focused rewrite regressions**

Run:

```bash
/Users/hualinge/vscodeproject/jiuwenclaw/.venv/bin/python -m pytest \
  -o addopts='' \
  tests/unit/agentserver/test_deepresearch_rewrite_fast_path.py \
  tests/unit/agentserver/test_deepresearch_rewrite_fast_path_adapter.py \
  tests/unit/agentserver/test_deepresearch_rewrite_tools.py \
  tests/unit/agentserver/test_deepresearch_document_rewrite.py -q
```

Expected: zero failures.

- [ ] **Step 6: Commit adapter integration**

```bash
git add \
  jiuwenclaw/agentserver/deep_agent/interface_deep.py \
  tests/unit/agentserver/test_deepresearch_rewrite_fast_path_adapter.py
git commit -m "feat(deepresearch): bypass agent loop for report rewrites"
```

### Task 4: Static Verification and Full Focused Gate

**Files:**
- Modify only files required to fix failures introduced by Tasks 1–3.

- [ ] **Step 1: Run syntax and import verification**

```bash
/Users/hualinge/vscodeproject/jiuwenclaw/.venv/bin/python -m compileall -q \
  jiuwenclaw/agentserver/deep_agent/deepresearch_rewrite_fast_path.py \
  jiuwenclaw/agentserver/deep_agent/interface_deep.py
```

Expected: exit 0 and no output.

- [ ] **Step 2: Run the complete focused gate**

Run the Task 3 Step 5 command. Expected: zero failures.

- [ ] **Step 3: Verify diff scope and whitespace**

```bash
git diff enterprise_dev...HEAD --check
git status --short
git diff --stat enterprise_dev...HEAD
```

Expected: only the design, plan, fast-path module, interface integration, and
their tests are changed; no runtime config or persistent data files appear.

### Task 5: Controlled Local Runtime Measurement

**Files:**
- No tracked file changes.

- [ ] **Step 1: Resolve the active runtime before mutation**

Record the OfficeClaw API/web and JiuwenClaw PIDs, process cwd, source HEAD,
ports, auth provider, output directory, and current `.env` hashes. Do not modify
`.env`, Redis, SQLite, startup config, or the existing source worktree.

- [ ] **Step 2: Start or switch only a process-scoped JiuwenClaw instance**

Launch from
`/Users/hualinge/vscodeproject/jiuwenclaw/.worktrees/deepresearch-rewrite-fast-path`
using the same request-scoped Huawei MaaS/CAS-compatible environment as the
active instance. If the existing process cannot be switched without touching
its startup config or parent process, use a separate service-owned test port
and a process-scoped OfficeClaw endpoint override.

- [ ] **Step 3: Measure polish, expand, and shorten**

For one valid Protocol v2 selection per action, capture:

- end-to-end wall time;
- `prepare_ms`, `model_ms`, `commit_ms`, `total_ms`;
- `model_calls`;
- resulting report path/revision;
- delivery status;
- structural/provenance regression result.

Expected: each successful action records `model_calls=1`; no generic
Agent iteration/tool-search/todo events appear for the request.

- [ ] **Step 4: Compare to the previously measured baseline**

Report each action separately. Do not claim a fixed latency SLA from three
samples; state the observed wall-time reduction and confirm whether the model
call count dropped from approximately 10–12 to exactly 1.
