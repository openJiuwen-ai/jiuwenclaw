# DeepResearch HTML Follow-up Restart Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make an explicit DeepResearch rewrite HTML follow-up resolve the trusted committed revision after a JiuwenClaw restart.

**Architecture:** Persist a small, versioned HTML export target in the existing tenant/session checkpoint when the rewrite fast path commits. Add a deterministic pre-LLM follow-up path that explicitly restores that target through the adapter's persistent checkpointer and invokes the existing HTML tool once.

**Tech Stack:** Python 3.12, asyncio, OpenJiuwen session/checkpointer APIs, pytest

---

### Task 1: Define the trusted follow-up contract

**Files:**
- Create: `jiuwenclaw/agentserver/tools/deepresearch/deepresearch_rewrite_html_followup.py`
- Test: `tests/unit/agentserver/test_deepresearch_rewrite_html_followup.py`

- [ ] **Step 1: Write failing intent and target tests**

```python
def test_parse_html_followup_accepts_documented_phrases():
    assert is_html_followup_request("生成 HTML")
    assert is_html_followup_request("请生成html。")


def test_target_from_commit_requires_completed_trusted_fields():
    target = target_from_commit_result({
        "status": "completed",
        "report_path": "/workspace/report-v2.md",
        "revision_id": "rev_child",
    })
    assert target.to_state() == {
        "schema_version": 1,
        "report_path": "/workspace/report-v2.md",
        "revision_id": "rev_child",
    }
    assert target_from_commit_result({"status": "error"}) is None
```

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```bash
/Users/hualinge/vscodeproject/jiuwenclaw/.venv/bin/python -m pytest \
  tests/unit/agentserver/test_deepresearch_rewrite_html_followup.py -q
```

Expected: collection fails because the new module does not exist.

- [ ] **Step 3: Implement the pure contract helpers**

Define:

```python
PENDING_HTML_EXPORT_STATE_KEY = "deepresearch_pending_html_export"

@dataclass(frozen=True)
class RewriteHtmlTarget:
    report_path: str
    revision_id: str

def is_html_followup_request(query: object) -> bool: ...
def target_from_commit_result(payload: object) -> RewriteHtmlTarget | None: ...
def target_from_state(payload: object) -> RewriteHtmlTarget | None: ...
def decode_html_tool_result(payload: object) -> RewriteHtmlFollowupResult: ...
```

Validation must require exact state keys, schema version `1`, non-empty strings,
and a `rev_` identifier matching the HTML tool schema.

- [ ] **Step 4: Run the tests and verify they pass**

Run the command from Step 2.

Expected: all follow-up contract tests pass.

### Task 2: Persist and restore the target

**Files:**
- Modify: `jiuwenclaw/agentserver/deep_agent/interface_deep.py:7738-7817`
- Test: `tests/unit/agentserver/test_deepresearch_rewrite_fast_path_adapter.py`

- [ ] **Step 1: Write failing adapter persistence tests**

Extend the existing fast-path persistence test to assert:

```python
session.update_state.assert_called_once_with({
    PENDING_HTML_EXPORT_STATE_KEY: {
        "schema_version": 1,
        "report_path": "/workspace/report.rewrite.md",
        "revision_id": "rev_child",
    }
})
```

Add a test that an invalid completed result returns `False` and is not flushed.

- [ ] **Step 2: Run the focused test and verify it fails**

```bash
/Users/hualinge/vscodeproject/jiuwenclaw/.venv/bin/python -m pytest \
  tests/unit/agentserver/test_deepresearch_rewrite_fast_path_adapter.py \
  -k "persist_fast_path" -q
```

Expected: target state was not written.

- [ ] **Step 3: Store the validated target before checkpoint flush**

In `_persist_deepresearch_rewrite_fast_path_turn`, derive the target only from
`result.commit_result`. Return `False` if validation fails. Call
`session.update_state(...)` before `save_contexts` and
`post_agent_execute_for_session`.

- [ ] **Step 4: Add explicit persistent restore**

Add an adapter helper that:

```python
session = create_agent_session(session_id=session_id, card=self._instance.card)
inner = getattr(session, "_inner", None)
await self._checkpointer.pre_agent_execute(inner, None)
return target_from_state(session.get_state(PENDING_HTML_EXPORT_STATE_KEY))
```

It must not use `Session.pre_run()`, because that reads the global in-memory
checkpointer.

- [ ] **Step 5: Run the focused tests**

Run the command from Step 2.

Expected: persistence and restore tests pass.

### Task 3: Handle HTML before the Agent loop

**Files:**
- Modify: `jiuwenclaw/agentserver/deep_agent/interface_deep.py:7840-7992`
- Modify: `jiuwenclaw/agentserver/deep_agent/interface_deep.py:7994-8740`
- Test: `tests/unit/agentserver/test_deepresearch_rewrite_fast_path_adapter.py`

- [ ] **Step 1: Write failing stream tests**

Add tests proving that:

```python
chunks = await _collect_stream(adapter, "生成 HTML")
html_tool.assert_awaited_once_with(
    report_path="/workspace/report.rewrite.md",
    revision_id="rev_child",
)
assert runner_calls == []
```

Also cover missing target, tool failure, different session, and ensure the
request never falls through to `Runner.run_agent_streaming`.

- [ ] **Step 2: Run the stream tests and verify they fail**

```bash
/Users/hualinge/vscodeproject/jiuwenclaw/.venv/bin/python -m pytest \
  tests/unit/agentserver/test_deepresearch_rewrite_fast_path_adapter.py \
  -k "html_followup" -q
```

Expected: the plain request enters Runner.

- [ ] **Step 3: Implement deterministic handling**

Add `_try_deepresearch_rewrite_html_followup` and response formatting. Invoke it
after request runtime configuration is bound and before
`_try_deepresearch_rewrite_fast_path`. A recognized HTML request always marks the
fast path handled, including missing-target and tool-error cases.

Mirror the behavior in the non-streaming entry so both adapter interfaces have
the same contract.

- [ ] **Step 4: Run the stream tests**

Run the command from Step 2.

Expected: all HTML follow-up tests pass.

### Task 4: Prove restart persistence

**Files:**
- Modify: `tests/unit_tests/agentserver/test_tenant_checkpoint.py`

- [ ] **Step 1: Write the restart regression**

Create two `PersistenceCheckpointer` instances against the same temporary SQLite
path. Persist `PENDING_HTML_EXPORT_STATE_KEY` with the first instance, discard
all session/adapter objects, and restore with the second instance. Assert the
same session returns the target and a different session returns `None`.

- [ ] **Step 2: Run the regression and verify it fails before implementation**

```bash
/Users/hualinge/vscodeproject/jiuwenclaw/.venv/bin/python -m pytest \
  tests/unit_tests/agentserver/test_tenant_checkpoint.py \
  -k "rewrite_html_restart" -q
```

Expected: restore support is absent before the adapter changes.

- [ ] **Step 3: Make the minimum adapter changes required by the regression**

Use the adapter's tenant-scoped `self._checkpointer` directly for both save and
restore. Do not call `CheckpointerFactory.set_default_checkpointer`.

- [ ] **Step 4: Run all focused tests**

```bash
/Users/hualinge/vscodeproject/jiuwenclaw/.venv/bin/python -m pytest \
  tests/unit/agentserver/test_deepresearch_rewrite_html_followup.py \
  tests/unit/agentserver/test_deepresearch_rewrite_fast_path_adapter.py \
  tests/unit_tests/agentserver/test_tenant_checkpoint.py -q
```

Expected: all focused tests pass.

### Task 5: Regression verification

**Files:**
- Verify only; no additional files

- [ ] **Step 1: Run adjacent DeepResearch rewrite tests**

```bash
/Users/hualinge/vscodeproject/jiuwenclaw/.venv/bin/python -m pytest \
  tests/unit/agentserver/test_deepresearch_rewrite_fast_path.py \
  tests/unit/agentserver/test_deepresearch_rewrite_fast_path_adapter.py \
  tests/unit/agentserver/test_deepresearch_rewrite_html_followup.py \
  tests/unit/agentserver/test_deepresearch_rewrite_tools.py \
  tests/unit_tests/agentserver/test_tenant_checkpoint.py -q
```

Expected: all tests pass.

- [ ] **Step 2: Inspect the final diff**

```bash
git status --short
git diff --check
git diff --stat
```

Expected: only the design, plan, follow-up module, adapter, and focused tests are
changed; `git diff --check` has no output.
