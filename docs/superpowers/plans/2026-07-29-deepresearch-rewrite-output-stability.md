# DeepResearch Rewrite Output Stability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the DeepResearch rewrite fast path recover safe model-output framing mistakes and one intermittent invalid generation without weakening commit validation.

**Architecture:** Keep all model-boundary behavior in `deepresearch_rewrite_fast_path.py`: project prepared units to the exact output schema, canonicalize a complete JSON fence and known ignored slot metadata, validate identifiers before commit, and retry one invalid generation. Extend the immutable result object with safe observability fields and append them to the existing adapter log; leave prepare, commit, Markdown, citation, provenance, and revision code unchanged.

**Tech Stack:** Python 3.12, asyncio, dataclasses, JSON, regular expressions, pytest, pytest-asyncio, unittest.mock.

---

## File Map

- Modify `jiuwenclaw/agentserver/tools/deepresearch/deepresearch_rewrite_fast_path.py`
  for prompt projection, canonical decoding, bounded retry, usage aggregation, and
  result observability fields.
- Modify `tests/unit/agentserver/test_deepresearch_rewrite_fast_path.py` for
  model-boundary and retry regression coverage.
- Modify `jiuwenclaw/agentserver/deep_agent/interface_deep.py` only to append
  the new safe fields to the existing fast-path summary log.
- Modify `tests/unit/agentserver/test_deepresearch_rewrite_fast_path_adapter.py`
  for result-construction and adapter-log coverage.

### Task 1: Project input and canonicalize the observed failure

**Files:**
- Modify: `tests/unit/agentserver/test_deepresearch_rewrite_fast_path.py`
- Modify: `jiuwenclaw/agentserver/tools/deepresearch/deepresearch_rewrite_fast_path.py`

- [ ] **Step 1: Add a failing prompt-projection test**

Add a prepared payload containing every input-only metadata field and assert
that the model-visible `units` value is the exact output schema:

```python
@pytest.mark.asyncio
async def test_run_rewrite_fast_path_projects_model_units_to_output_schema():
    prepared = {
        **_PREPARED,
        "units": [
            {
                "unit_id": "unit_1",
                "type": "paragraph",
                "level": None,
                "list_depth": None,
                "list_marker": None,
                "slots": [
                    {
                        "slot_id": "slot_1",
                        "text": "原句。",
                        "format": ["strong"],
                        "link_id": "link_1",
                    }
                ],
            }
        ],
    }
    model = AsyncMock(
        return_value=SimpleNamespace(content=_json_result(_STRUCTURED_RESULT))
    )

    await run_rewrite_fast_path(
        _query(),
        prepare_invoke=AsyncMock(return_value=_json_result(prepared)),
        model_invoke=model,
        commit_invoke=AsyncMock(return_value=_json_result(_COMPLETED)),
    )

    payload = json.loads(model.await_args.args[0][1]["content"])
    assert payload["units"] == [
        {
            "unit_id": "unit_1",
            "slots": [{"slot_id": "slot_1", "text": "原句。"}],
        }
    ]
```

- [ ] **Step 2: Add a failing regression test for the captured output shape**

```python
@pytest.mark.asyncio
async def test_run_rewrite_fast_path_canonicalizes_fence_and_known_slot_metadata():
    observed = {
        "units": [
            {
                "unit_id": "unit_1",
                "slots": [
                    {
                        "slot_id": "slot_1",
                        "text": "改写后的句子。",
                        "format": [],
                    }
                ],
            }
        ],
        "facts_added": False,
    }
    commit = AsyncMock(return_value=_json_result(_COMPLETED))

    result = await run_rewrite_fast_path(
        _query(),
        prepare_invoke=AsyncMock(return_value=_json_result(_PREPARED)),
        model_invoke=AsyncMock(
            return_value=SimpleNamespace(
                content=f"```json\n{_json_result(observed)}\n```"
            )
        ),
        commit_invoke=commit,
    )

    assert result is not None
    assert result.status == "completed"
    assert result.model_calls == 1
    assert result.model_output_adjustments == ("json_fence", "slot_metadata")
    assert result.model_output_error_reason is None
    commit.assert_awaited_once_with(
        context_token="secret-context-token",
        structured_result=_STRUCTURED_RESULT,
    )
```

- [ ] **Step 3: Run the two tests and verify RED**

Run:

```bash
/Users/hualinge/vscodeproject/jiuwenclaw/.venv/bin/python -m pytest \
  tests/unit/agentserver/test_deepresearch_rewrite_fast_path.py::test_run_rewrite_fast_path_projects_model_units_to_output_schema \
  tests/unit/agentserver/test_deepresearch_rewrite_fast_path.py::test_run_rewrite_fast_path_canonicalizes_fence_and_known_slot_metadata -q
```

Expected: the projection assertion exposes input-only metadata and the captured
output returns `MODEL_OUTPUT_INVALID`.

- [ ] **Step 4: Implement projection and canonical decoding**

Add a typed internal error, exact-fence pattern, projection helper, and decoder:

```python
_JSON_FENCE_RE = re.compile(
    r"\A\s*```(?:json)?\s*\n(?P<body>\{.*\})\n```\s*\Z",
    re.DOTALL | re.IGNORECASE,
)
_IGNORED_SLOT_OUTPUT_KEYS = {"format", "link_id"}


class ModelOutputError(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _project_model_units(raw_units: object) -> list[dict[str, Any]]:
    if not isinstance(raw_units, list) or not raw_units:
        raise ValueError("prepared units are unavailable")
    projected = []
    for unit in raw_units:
        if not isinstance(unit, dict) or not isinstance(unit.get("unit_id"), str):
            raise ValueError("prepared units are invalid")
        raw_slots = unit.get("slots")
        if not isinstance(raw_slots, list) or not raw_slots:
            raise ValueError("prepared units are invalid")
        slots = []
        for slot in raw_slots:
            if (
                not isinstance(slot, dict)
                or not isinstance(slot.get("slot_id"), str)
                or not isinstance(slot.get("text"), str)
            ):
                raise ValueError("prepared units are invalid")
            slots.append({"slot_id": slot["slot_id"], "text": slot["text"]})
        projected.append({"unit_id": unit["unit_id"], "slots": slots})
    return projected


def _decode_model_result(
    content: object,
    expected_units: list[dict[str, Any]],
) -> tuple[dict[str, Any], tuple[str, ...]]:
    if not isinstance(content, str) or not content.strip():
        raise ModelOutputError("content_unavailable")
    adjustments = []
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as bare_error:
        fence = _JSON_FENCE_RE.fullmatch(content)
        if fence is None:
            raise ModelOutputError("json_invalid") from bare_error
        try:
            payload = json.loads(fence.group("body"))
        except json.JSONDecodeError as fence_error:
            raise ModelOutputError("json_invalid") from fence_error
        adjustments.append("json_fence")
    if (
        not isinstance(payload, dict)
        or set(payload) != {"units", "facts_added"}
        or payload.get("facts_added") is not False
    ):
        raise ModelOutputError("top_level_shape")
    units = payload.get("units")
    if not isinstance(units, list) or len(units) != len(expected_units):
        raise ModelOutputError("unit_shape")
    canonical_units = []
    for unit, expected_unit in zip(units, expected_units):
        if (
            not isinstance(unit, dict)
            or set(unit) != {"unit_id", "slots"}
            or unit.get("unit_id") != expected_unit["unit_id"]
        ):
            raise ModelOutputError("unit_shape")
        slots = unit.get("slots")
        expected_slots = expected_unit["slots"]
        if not isinstance(slots, list) or len(slots) != len(expected_slots):
            raise ModelOutputError("slot_shape")
        canonical_slots = []
        for slot, expected_slot in zip(slots, expected_slots):
            if not isinstance(slot, dict):
                raise ModelOutputError("slot_shape")
            keys = set(slot)
            if (
                not {"slot_id", "text"} <= keys
                or not keys <= {"slot_id", "text"} | _IGNORED_SLOT_OUTPUT_KEYS
                or slot.get("slot_id") != expected_slot["slot_id"]
                or not isinstance(slot.get("text"), str)
            ):
                raise ModelOutputError("slot_shape")
            if keys & _IGNORED_SLOT_OUTPUT_KEYS:
                adjustments.append("slot_metadata")
            canonical_slots.append(
                {"slot_id": slot["slot_id"], "text": slot["text"]}
            )
        canonical_units.append(
            {"unit_id": unit["unit_id"], "slots": canonical_slots}
        )
    return (
        {"units": canonical_units, "facts_added": False},
        tuple(dict.fromkeys(adjustments)),
    )
```

Project `prepared["units"]` before message construction, pass the projection to
the decoder, add `model_output_adjustments` and
`model_output_error_reason` defaults to `RewriteFastPathResult` and `_result`,
and propagate the successful adjustment tuple. Remove the complete JSON-fence
case from the existing rejection parameter list because it is now accepted;
leave all other invalid cases unchanged until Task 2 adds retry expectations.

- [ ] **Step 5: Run the two tests and the original focused file**

Run:

```bash
/Users/hualinge/vscodeproject/jiuwenclaw/.venv/bin/python -m pytest \
  tests/unit/agentserver/test_deepresearch_rewrite_fast_path.py -q
```

Expected: the new tests pass. The old test that expected a fenced valid result
to fail has been updated, and all other existing cases pass.

- [ ] **Step 6: Commit Task 1**

```bash
git add \
  jiuwenclaw/agentserver/tools/deepresearch/deepresearch_rewrite_fast_path.py \
  tests/unit/agentserver/test_deepresearch_rewrite_fast_path.py
git commit -m "fix(deepresearch): canonicalize rewrite model output"
```

### Task 2: Add one pre-commit retry and aggregate usage

**Files:**
- Modify: `tests/unit/agentserver/test_deepresearch_rewrite_fast_path.py`
- Modify: `jiuwenclaw/agentserver/tools/deepresearch/deepresearch_rewrite_fast_path.py`

- [ ] **Step 1: Add failing retry and usage tests**

```python
@pytest.mark.asyncio
async def test_run_rewrite_fast_path_retries_invalid_output_once_and_sums_usage():
    model = AsyncMock(
        side_effect=[
            SimpleNamespace(
                content="not json",
                usage_metadata={
                    "input_tokens": 100,
                    "output_tokens": 10,
                    "total_tokens": 110,
                    "total_cost": 0.1,
                },
            ),
            SimpleNamespace(
                content=_json_result(_STRUCTURED_RESULT),
                usage_metadata={
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "total_tokens": 120,
                    "total_cost": 0.2,
                },
            ),
        ]
    )
    commit = AsyncMock(return_value=_json_result(_COMPLETED))

    result = await run_rewrite_fast_path(
        _query(),
        prepare_invoke=AsyncMock(return_value=_json_result(_PREPARED)),
        model_invoke=model,
        commit_invoke=commit,
    )

    assert result is not None
    assert result.status == "completed"
    assert result.model_calls == 2
    assert result.usage_metadata is not None
    assert result.usage_metadata["input_tokens"] == 200
    assert result.usage_metadata["output_tokens"] == 30
    assert result.usage_metadata["total_tokens"] == 230
    assert result.usage_metadata["total_cost"] == pytest.approx(0.3)
    assert result.model_output_error_reason is None
    assert "Strict retry:" in model.await_args_list[1].args[0][0]["content"]
    commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_rewrite_fast_path_returns_final_reason_after_two_invalid_outputs():
    model = AsyncMock(
        side_effect=[
            SimpleNamespace(content="not json"),
            SimpleNamespace(content='{"units": [], "facts_added": false}'),
        ]
    )
    commit = AsyncMock()

    result = await run_rewrite_fast_path(
        _query(),
        prepare_invoke=AsyncMock(return_value=_json_result(_PREPARED)),
        model_invoke=model,
        commit_invoke=commit,
    )

    assert result is not None
    assert result.status == "error"
    assert result.error_code == "MODEL_OUTPUT_INVALID"
    assert result.model_calls == 2
    assert result.model_output_error_reason == "unit_shape"
    commit.assert_not_awaited()
```

Add a third test where the first response is invalid and the second model call
raises `RuntimeError`; expect `MODEL_CALL_FAILED`, `model_calls == 2`, and no
commit.

- [ ] **Step 2: Run the retry tests and verify RED**

Run:

```bash
/Users/hualinge/vscodeproject/jiuwenclaw/.venv/bin/python -m pytest \
  tests/unit/agentserver/test_deepresearch_rewrite_fast_path.py::test_run_rewrite_fast_path_retries_invalid_output_once_and_sums_usage \
  tests/unit/agentserver/test_deepresearch_rewrite_fast_path.py::test_run_rewrite_fast_path_returns_final_reason_after_two_invalid_outputs \
  tests/unit/agentserver/test_deepresearch_rewrite_fast_path.py::test_run_rewrite_fast_path_maps_retry_exception_without_committing -q
```

Expected: all three fail because the current path makes one model call.

- [ ] **Step 3: Implement usage aggregation and a two-attempt loop**

```python
_RETRY_SYSTEM_SUFFIX = (
    "\n\nStrict retry: the previous response was structurally invalid. "
    "Return only the required JSON object. Do not use Markdown fences and do "
    "not copy input metadata fields."
)
_USAGE_SUM_KEYS = {
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cache_tokens",
    "input_cost",
    "output_cost",
    "total_cost",
}


def _merge_usage_metadata(
    total: dict[str, Any] | None,
    current: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if current is None:
        return total
    merged = dict(total or {})
    for key, value in current.items():
        if key in _USAGE_SUM_KEYS and isinstance(value, (int, float)):
            merged[key] = merged.get(key, 0) + value
        elif key not in merged:
            merged[key] = value
    return merged
```

Replace the single call/decode block with a two-attempt loop. Increment
`model_calls` before each invocation, accumulate usage after each successful
provider response, use `_SYSTEM_PROMPT + _RETRY_SYSTEM_SUFFIX` on attempt two,
and stop before commit until decoding succeeds. Return the last
`ModelOutputError.reason` only after the second invalid result.

```python
model_started = time.perf_counter()
model_kwargs = {"temperature": 0.2} if request.action == "polish" else {}
usage_metadata = None
model_calls = 0
structured_result = None
model_output_adjustments = ()
model_output_error_reason = None
for attempt in range(2):
    attempt_messages = messages
    if attempt:
        attempt_messages = [
            {
                "role": "system",
                "content": _SYSTEM_PROMPT + _RETRY_SYSTEM_SUFFIX,
            },
            messages[1],
        ]
    try:
        model_calls += 1
        response = await model_invoke(attempt_messages, **model_kwargs)
    except Exception:  # pylint: disable=broad-exception-caught
        return _result(
            started_at=started_at,
            status="error",
            action=request.action,
            error_code="MODEL_CALL_FAILED",
            message="rewrite model call failed",
            usage_metadata=usage_metadata,
            prepare_ms=prepare_ms,
            model_ms=_milliseconds(model_started),
            model_calls=model_calls,
        )
    usage_metadata = _merge_usage_metadata(
        usage_metadata,
        _normalize_usage_metadata(getattr(response, "usage_metadata", None)),
    )
    try:
        structured_result, model_output_adjustments = _decode_model_result(
            getattr(response, "content", None),
            projected_units,
        )
    except ModelOutputError as exc:
        model_output_error_reason = exc.reason
        continue
    model_output_error_reason = None
    break
model_ms = _milliseconds(model_started)
if structured_result is None:
    return _result(
        started_at=started_at,
        status="error",
        action=request.action,
        error_code="MODEL_OUTPUT_INVALID",
        message="invalid structured rewrite result",
        usage_metadata=usage_metadata,
        prepare_ms=prepare_ms,
        model_ms=model_ms,
        model_calls=model_calls,
        model_output_error_reason=model_output_error_reason,
    )
```

- [ ] **Step 4: Update invalid-output regression expectations**

Keep the complete JSON-fence case out of the rejection parameter list because
Task 1 now accepts it. For every remaining invalid value, configure the mock to
return the same value twice and assert `model_calls == 2`, no commit, and a
non-empty `model_output_error_reason`. Add explicit wrong-unit-ID,
wrong-slot-ID, slot-order, unknown-slot-key, and trailing-text cases.

- [ ] **Step 5: Run the complete fast-path test file**

Run:

```bash
/Users/hualinge/vscodeproject/jiuwenclaw/.venv/bin/python -m pytest \
  tests/unit/agentserver/test_deepresearch_rewrite_fast_path.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit Task 2**

```bash
git add \
  jiuwenclaw/agentserver/tools/deepresearch/deepresearch_rewrite_fast_path.py \
  tests/unit/agentserver/test_deepresearch_rewrite_fast_path.py
git commit -m "fix(deepresearch): retry invalid rewrite output once"
```

### Task 3: Expose safe adapter observability

**Files:**
- Modify: `tests/unit/agentserver/test_deepresearch_rewrite_fast_path_adapter.py`
- Modify: `jiuwenclaw/agentserver/deep_agent/interface_deep.py`

- [ ] **Step 1: Extend the adapter test result helper**

Add keyword arguments with safe defaults and pass them to
`RewriteFastPathResult`:

```python
def _result(
    *,
    status: str = "completed",
    error_code: str | None = None,
    message: str = (
        "本轮改写已完成。若报告已是最终版本，请回复‘生成 HTML’；"
        "如需继续改写，可直接选择下一处内容。"
    ),
    usage_metadata: object | None = None,
    model_calls: int = 1,
    model_output_adjustments: tuple[str, ...] = (),
    model_output_error_reason: str | None = None,
    commit_result: dict | None = None,
) -> RewriteFastPathResult:
    if usage_metadata is None and model_calls:
        usage_metadata = {
            "input_tokens": 100,
            "output_tokens": 20,
            "total_tokens": 120,
        }
    if commit_result is None and status == "completed":
        commit_result = {
            "status": "completed",
            "report_delivered": True,
            "report_path": "/workspace/report.rewrite.md",
            "revision_id": "rev_child",
        }
    return RewriteFastPathResult(
        recognized=True,
        status=status,
        action="polish",
        error_code=error_code,
        message=message,
        usage_metadata=usage_metadata,
        prepare_ms=1.0,
        model_ms=20.0,
        commit_ms=2.0,
        total_ms=23.0,
        model_calls=model_calls,
        model_output_adjustments=model_output_adjustments,
        model_output_error_reason=model_output_error_reason,
        commit_result=commit_result,
    )
```

- [ ] **Step 2: Add a failing adapter-log test**

Patch `interface_module.logger.info`, run a recognized fast-path stream with:

```python
@pytest.mark.asyncio
async def test_process_stream_logs_fast_path_output_diagnostics(monkeypatch):
    result = _result(
        model_calls=2,
        model_output_adjustments=("json_fence", "slot_metadata"),
        model_output_error_reason=None,
    )
    adapter = _stream_adapter(result)

    async def empty_runner(*_args, **_kwargs):
        if False:
            yield None

    monkeypatch.setattr(
        interface_module.Runner,
        "run_agent_streaming",
        empty_runner,
    )
    monkeypatch.setattr(
        interface_module,
        "ask_user_question_request_scope",
        _request_scope,
    )
    monkeypatch.setattr(interface_module, "setup_permission_context", Mock())
    monkeypatch.setattr(interface_module, "cleanup_permission_context", Mock())
    monkeypatch.setattr(interface_module, "set_perf_summary_context", Mock())
    monkeypatch.setattr(interface_module, "finalize_perf_summary_request", Mock())
    monkeypatch.setattr(interface_module, "clear_perf_summary_context", Mock())
    monkeypatch.setattr(interface_module, "mark_request_first_byte", Mock())

    with patch.object(interface_module.logger, "info") as log_info:
        await _collect_stream(adapter, _query())

    summary_call = next(
        call
        for call in log_info.call_args_list
        if call.args
        and str(call.args[0]).startswith("[DeepResearchRewriteFastPath]")
    )
    assert summary_call.args[-2:] == ("json_fence,slot_metadata", None)
```

- [ ] **Step 3: Run the adapter-log test and verify RED**

Run:

```bash
/Users/hualinge/vscodeproject/jiuwenclaw/.venv/bin/python -m pytest \
  tests/unit/agentserver/test_deepresearch_rewrite_fast_path_adapter.py::test_process_stream_logs_fast_path_output_diagnostics -q
```

Expected: FAIL because the existing log has no output diagnostics.

- [ ] **Step 4: Append diagnostics to the existing summary log**

Change only the existing format string and argument list:

```python
logger.info(
    "[DeepResearchRewriteFastPath] request_id=%s session_id=%s "
    "action=%s status=%s error_code=%s prepare_ms=%.3f "
    "model_ms=%.3f commit_ms=%.3f total_ms=%.3f model_calls=%d "
    "output_adjustments=%s model_output_error_reason=%s",
    rid,
    session_id,
    fast_path_result.action,
    fast_path_result.status,
    fast_path_result.error_code,
    fast_path_result.prepare_ms,
    fast_path_result.model_ms,
    fast_path_result.commit_ms,
    fast_path_result.total_ms,
    fast_path_result.model_calls,
    ",".join(fast_path_result.model_output_adjustments) or "none",
    fast_path_result.model_output_error_reason,
    extra={"user_visible": "critical"},
)
```

- [ ] **Step 5: Run both focused test files**

Run:

```bash
/Users/hualinge/vscodeproject/jiuwenclaw/.venv/bin/python -m pytest \
  tests/unit/agentserver/test_deepresearch_rewrite_fast_path.py \
  tests/unit/agentserver/test_deepresearch_rewrite_fast_path_adapter.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit Task 3**

```bash
git add \
  jiuwenclaw/agentserver/deep_agent/interface_deep.py \
  tests/unit/agentserver/test_deepresearch_rewrite_fast_path_adapter.py
git commit -m "chore(deepresearch): log rewrite output recovery"
```

### Task 4: Verify the complete local change

**Files:**
- Verify: `jiuwenclaw/agentserver/tools/deepresearch/deepresearch_rewrite_fast_path.py`
- Verify: `jiuwenclaw/agentserver/deep_agent/interface_deep.py`
- Verify: `tests/unit/agentserver/test_deepresearch_rewrite_fast_path.py`
- Verify: `tests/unit/agentserver/test_deepresearch_rewrite_fast_path_adapter.py`

- [ ] **Step 1: Run all directly affected unit suites**

```bash
/Users/hualinge/vscodeproject/jiuwenclaw/.venv/bin/python -m pytest \
  tests/unit/agentserver/test_deepresearch_rewrite_fast_path.py \
  tests/unit/agentserver/test_deepresearch_rewrite_fast_path_adapter.py \
  tests/unit/agentserver/test_deepresearch_rewrite_tools.py \
  tests/unit/agentserver/test_deepresearch_document_rewrite.py -q
```

Expected: all collected tests pass.

- [ ] **Step 2: Compile changed production modules**

```bash
/Users/hualinge/vscodeproject/jiuwenclaw/.venv/bin/python -m compileall -q \
  jiuwenclaw/agentserver/tools/deepresearch/deepresearch_rewrite_fast_path.py \
  jiuwenclaw/agentserver/deep_agent/interface_deep.py
```

Expected: exit code 0 with no output.

- [ ] **Step 3: Run repository whitespace validation**

```bash
git diff --check 125fa16f88d7469127b2fc53a22687b8ff1ad162...HEAD
git status --short
```

Expected: `git diff --check` exits 0 and status is clean.

- [ ] **Step 4: Review the final diff against the design**

Confirm every production change maps to one of: model-input projection,
canonical decoding, one pre-commit retry, usage aggregation, or safe logging.
Confirm no runtime configuration, persistent data, prepare/commit validator,
OfficeClaw, citation, provenance, or report file changed.
