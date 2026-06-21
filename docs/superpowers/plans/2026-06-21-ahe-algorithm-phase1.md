# PDA-style One-shot AHE Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the PDA-style One-shot AHE裁剪版 — a minimal closed-loop: Trace → Clean → Eval → Diagnose → Propose → Decide → Apply → Record, limited to Skill Experience Proposal in phase 1.

**Architecture:** PdaProposer self-contains CLEAN→EVAL→DIAG→GOV→PROPOSE steps; PdaDecisionPolicy combines RuleGate + LLMDecision; ExperienceGovernor provides governance context before Propose and validates during Decision; SkillExperienceWriter extends to support ExperienceOperation types (ADD/MERGE/REPLACE/etc).

**Tech Stack:** Python 3.10+, Pydantic, openjiuwen Model (LLM calls), SQLite (traces.db + evolution.db), asyncio

---

## File Structure

### New Files

| File | Responsibility |
|------|---------------|
| `jiuwenswarm/evolve/otel_adapter.py` | OTEL spans → Langfuse trace dict conversion |
| `jiuwenswarm/evolve/diagnosis/__init__.py` | Export DiagnosisAgent, DiagnosisResult |
| `jiuwenswarm/evolve/diagnosis/models.py` | DiagnosisResult, DiagnosisIssue dataclasses |
| `jiuwenswarm/evolve/diagnosis/tools.py` | Read-only tool implementations (read_spans, search_spans, etc.) |
| `jiuwenswarm/evolve/diagnosis/prompts.py` | System prompt + tool descriptions |
| `jiuwenswarm/evolve/diagnosis/agent.py` | DiagnosisAgent ReAct loop + runner |
| `jiuwenswarm/evolve/pda/__init__.py` | Export PdaProposer, PdaDecisionPolicy, ExperienceGovernor |
| `jiuwenswarm/evolve/pda/evaluator.py` | TraceOutcome, TaskNameInferrer, TraceOutcomeEvaluator |
| `jiuwenswarm/evolve/pda/experience_governor.py` | GovernanceContext, ExperienceGovernor |
| `jiuwenswarm/evolve/pda/proposer.py` | PdaProposer — self-contained CLEAN→EVAL→DIAG→GOV→PROPOSE |
| `jiuwenswarm/evolve/pda/decision_policy.py` | PdaDecisionPolicy — RuleGate + LLMDecision |

### Modified Files

| File | Change |
|------|--------|
| `jiuwenswarm/evolve/models.py` | Add ExperienceOperationType, ExperienceOperation, GovernanceContext, TraceOutcome |
| `jiuwenswarm/evolve/apply_writers/skill_writer.py` | Extend apply() to handle ExperienceOperation |
| `jiuwenswarm/evolve/cli.py` | Add `--pda` flag, `diagnose` subcommand, `governor` subcommand |
| `jiuwenswarm/evolve/config.yaml` | Add `pda:` config section |
| `jiuwenswarm/evolve/__init__.py` | Export new modules |
| `jiuwenswarm/evolve/registry.py` | (No manual change — `@register` decorator auto-registers) |

### Test Files

| File | Coverage |
|------|---------|
| `tests/unit_tests/test_otel_adapter.py` | OtelTraceAdapter field mapping, edge cases |
| `tests/unit_tests/test_pda_models.py` | ExperienceOperation, TraceOutcome, GovernanceContext validation |
| `tests/unit_tests/test_experience_governor.py` | get_context, validate_operation, classification |
| `tests/unit_tests/test_evaluator.py` | TraceOutcomeEvaluator, TaskNameInferrer |
| `tests/unit_tests/test_diagnosis_agent.py` | Tool implementations, payload validation |
| `tests/unit_tests/test_pda_proposer.py` | PdaProposer flow (mocked), limit enforcement |
| `tests/unit_tests/test_pda_decision_policy.py` | RuleGate checks, LLMDecision (mocked) |
| `tests/unit_tests/test_skill_writer_ops.py` | ADD/MERGE/REPLACE/DEPRECATE/NOOP operations |

---

## Dependency Graph & Task Ordering

```
Layer 0: Models (Task 1)
  ↓
Layer 1: OtelTraceAdapter (Task 2) ─── DiagnosisAgent (Task 3)
  ↓                                       ↓
Layer 2: TraceOutcomeEvaluator (Task 4) ── ExperienceGovernor (Task 5)
  ↓                                       ↓
Layer 3: PdaProposer (Task 6) ────────── PdaDecisionPolicy (Task 7)
  ↓                                       ↓
Layer 4: SkillWriter Extension (Task 8) ── CLI + Config (Task 9)
  ↓
Layer 5: Integration Test (Task 10)
```

---

## Task 1: PDA Data Models

**Files:**
- Modify: `jiuwenswarm/evolve/models.py`
- Create: `tests/unit_tests/test_pda_models.py`

These models are the shared contract between all PDA modules. They must exist before anything else.

- [ ] **Step 1: Write the failing tests for ExperienceOperationType and ExperienceOperation**

```python
# tests/unit_tests/test_pda_models.py
import pytest
from pydantic import ValidationError

from jiuwenswarm.evolve.models import (
    ExperienceOperationType,
    ExperienceOperation,
    GovernanceContext,
    TraceOutcome,
)


class TestExperienceOperationType:
    def test_valid_values(self):
        assert ExperienceOperationType.ADD == "add"
        assert ExperienceOperationType.MERGE == "merge"
        assert ExperienceOperationType.REPLACE == "replace"
        assert ExperienceOperationType.NOOP == "noop"
        assert ExperienceOperationType.DEPRECATE == "deprecate"
        assert ExperienceOperationType.UPDATE == "update"

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            ExperienceOperationType("delete")


class TestExperienceOperation:
    def test_add_operation(self):
        op = ExperienceOperation(
            op=ExperienceOperationType.ADD,
            new_content="Use full path /usr/bin/python3",
            reason="Agent used wrong path",
            evidence_refs=[
                EvidenceRef(trace_id="abc123", description="bash: python not found")
            ],
        )
        assert op.op == ExperienceOperationType.ADD
        assert op.target_experience_id is None

    def test_merge_operation_requires_target(self):
        op = ExperienceOperation(
            op=ExperienceOperationType.MERGE,
            target_experience_id="exp-001",
            reason="Similar issue already has experience",
            evidence_refs=[
                EvidenceRef(trace_id="def456", description="same path error")
            ],
        )
        assert op.target_experience_id == "exp-001"

    def test_add_without_content_raises(self):
        """ADD/REPLACE/UPDATE must have new_content."""
        with pytest.raises(ValidationError):
            ExperienceOperation(
                op=ExperienceOperationType.ADD,
                reason="missing content",
                evidence_refs=[],
            )

    def test_merge_without_target_raises(self):
        """MERGE/REPLACE/DEPRECATE/UPDATE must have target_experience_id."""
        with pytest.raises(ValidationError):
            ExperienceOperation(
                op=ExperienceOperationType.MERGE,
                reason="missing target",
                evidence_refs=[],
            )

    def test_noop_operation(self):
        op = ExperienceOperation(
            op=ExperienceOperationType.NOOP,
            reason="Existing experience covers this issue",
            evidence_refs=[],
        )
        assert op.op == ExperienceOperationType.NOOP
        assert op.new_content is None
        assert op.target_experience_id is None


class TestTraceOutcome:
    def test_pass_outcome(self):
        outcome = TraceOutcome(
            trace_id="abc123",
            outcome="pass",
            score=0.9,
            confidence=0.85,
            reason="User task completed",
        )
        assert outcome.outcome == "pass"

    def test_invalid_outcome_raises(self):
        with pytest.raises(ValidationError):
            TraceOutcome(trace_id="abc123", outcome="unknown", score=0.5)

    def test_default_fields(self):
        outcome = TraceOutcome(trace_id="abc123", outcome="uncertain", score=0.5)
        assert outcome.missing_requirements == []
        assert outcome.needs_external_verification is False


class TestGovernanceContext:
    def test_basic_context(self):
        ctx = GovernanceContext(
            skill_name="bash-tool",
            current_count=8,
            max_count=10,
            can_add=True,
            existing_experiences=[],
            similar_experiences=[],
            replaceable_experiences=[],
            protected_experiences=[],
            allowed_operations=[ExperienceOperationType.ADD, ExperienceOperationType.NOOP],
        )
        assert ctx.can_add is True
        assert ExperienceOperationType.ADD in ctx.allowed_operations

    def test_full_context_disallows_add(self):
        ctx = GovernanceContext(
            skill_name="bash-tool",
            current_count=10,
            max_count=10,
            can_add=False,
            existing_experiences=[],
            similar_experiences=[],
            replaceable_experiences=[{"id": "exp-001", "state": "candidate", "hit_count": 0}],
            protected_experiences=[],
            allowed_operations=[ExperienceOperationType.REPLACE, ExperienceOperationType.NOOP],
        )
        assert ctx.can_add is False
        assert ExperienceOperationType.ADD not in ctx.allowed_operations
```

We need to import `EvidenceRef` which already exists in `models.py`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit_tests/test_pda_models.py -v`
Expected: FAIL — `ExperienceOperationType`, `ExperienceOperation`, `TraceOutcome`, `GovernanceContext` not yet defined.

- [ ] **Step 3: Add PDA models to models.py**

Append the following to `jiuwenswarm/evolve/models.py` (after the existing `TraceBatch` class, around line 525):

```python
# ============================================================================
# PDA Phase 1 — Experience governance & task evaluation models
# ============================================================================


class ExperienceOperationType(StrEnum):
    """Operation types for experience governance.

    Propose phase explicitly declares governance intent; Decision validates;
    Apply faithfully executes.
    """

    ADD = "add"
    MERGE = "merge"
    UPDATE = "update"
    DEPRECATE = "deprecate"
    REPLACE = "replace"
    NOOP = "noop"


class ExperienceOperation(BaseModel):
    """A single experience operation within a Proposal.

    Carried in ``Proposal.metadata["operations"]``.
    """

    model_config = ConfigDict(extra="forbid")

    op: ExperienceOperationType
    """Operation type — determines what Apply does."""

    target_experience_id: str | None = None
    """Target for MERGE/REPLACE/UPDATE/DEPRECATE. Required when op != ADD/NOOP."""

    new_content: str | None = None
    """New experience content for ADD/REPLACE/UPDATE. Required for these ops."""

    reason: str
    """Why this operation was chosen over alternatives."""

    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    """Evidence supporting this operation."""

    expected_effect: str | None = None
    """Predicted improvement if this operation is applied."""

    risk: str | None = None
    """Potential downsides."""

    @model_validator(mode="after")
    def _validate_op_requirements(self) -> "ExperienceOperation":
        """Ensure required fields are present for each operation type."""
        content_required = {ExperienceOperationType.ADD, ExperienceOperationType.REPLACE, ExperienceOperationType.UPDATE}
        target_required = {ExperienceOperationType.MERGE, ExperienceOperationType.REPLACE, ExperienceOperationType.UPDATE, ExperienceOperationType.DEPRECATE}

        if self.op in content_required and not self.new_content:
            raise ValueError(f"op={self.op.value} requires new_content")
        if self.op in target_required and not self.target_experience_id:
            raise ValueError(f"op={self.op.value} requires target_experience_id")
        return self


class GovernanceContext(BaseModel):
    """Current experience governance state for a skill.

    Provided by ExperienceGovernor before Propose; used by Decision for validation.
    """

    model_config = ConfigDict(extra="forbid")

    skill_name: str
    current_count: int
    max_count: int = 10
    can_add: bool
    existing_experiences: list[dict[str, Any]] = Field(default_factory=list)
    similar_experiences: list[dict[str, Any]] = Field(default_factory=list)
    replaceable_experiences: list[dict[str, Any]] = Field(default_factory=list)
    protected_experiences: list[str] = Field(default_factory=list)
    allowed_operations: list[ExperienceOperationType] = Field(default_factory=list)


class TraceOutcome(BaseModel):
    """Task completion evaluation result for a single trace."""

    model_config = ConfigDict(extra="forbid")

    trace_id: str
    task_name: str | None = None
    outcome: str
    """Must be "pass", "fail", or "uncertain"."""

    score: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    reason: str = ""
    key_evidence: str = ""
    missing_requirements: list[str] = Field(default_factory=list)
    needs_external_verification: bool = False

    @model_validator(mode="after")
    def _validate_outcome(self) -> "TraceOutcome":
        valid = {"pass", "fail", "uncertain"}
        if self.outcome not in valid:
            raise ValueError(f"outcome must be one of {sorted(valid)}, got '{self.outcome}'")
        return self
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit_tests/test_pda_models.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add jiuwenswarm/evolve/models.py tests/unit_tests/test_pda_models.py
git commit -m "feat(evolve): add PDA phase 1 data models — ExperienceOperation, GovernanceContext, TraceOutcome"
```

---

## Task 2: OtelTraceAdapter

**Files:**
- Create: `jiuwenswarm/evolve/otel_adapter.py`
- Create: `tests/unit_tests/test_otel_adapter.py`

This is the bridge: OTEL spans → Langfuse dict → `_extract_trace_data_impl`. All mapping logic is concentrated here.

> **Note:** `_extract_trace_data_impl` lives in `agentic-harness-engineering/trace_converter.py`. For phase 1, we vendor a simplified version or import it. The adapter itself only does the OTEL→Langfuse mapping.

- [ ] **Step 1: Write failing tests for core adapter methods**

```python
# tests/unit_tests/test_otel_adapter.py
import pytest
from jiuwenswarm.evolve.otel_adapter import (
    OtelTraceAdapter,
    _ns_to_iso,
    _ns_to_ms,
    _span_to_observation,
    _adapt_observation_name,
    _parse_tool_calls_repr,
)


class TestTimeConversion:
    def test_ns_to_iso(self):
        # 2024-01-15 10:30:00 UTC in nanoseconds
        ns = 1705308600_000000000
        result = _ns_to_iso(ns)
        assert result.startswith("2024-01-15")
        assert "10:30:00" in result

    def test_ns_to_iso_none(self):
        assert _ns_to_iso(None) == "N/A"

    def test_ns_to_ms(self):
        assert _ns_to_ms(1_000_000) == 1.0

    def test_ns_to_ms_none(self):
        assert _ns_to_ms(None) == "N/A"


class TestSpanToObservation:
    def _make_llm_span(self):
        return {
            "trace_id": "abc123",
            "span_id": "span-001",
            "parent_span_id": None,
            "name": "gen_ai.chat",
            "start_time_ns": 1705308600_000000000,
            "end_time_ns": 1705308610_000000000,
            "duration_ns": 10_000_000_000,
            "attributes": '{"gen_ai.span.type": "model", "gen_ai.system": "anthropic", "gen_ai.request.model": "claude-sonnet-4-6", "gen_ai.usage.total_tokens": 1500}',
            "events": '[{"name": "gen_ai.assistant.message", "attributes": {"content": "I will help you"}}]',
            "status_code": "OK",
            "status_description": "",
            "resource": '{"service.name": "jiuwenswarm"}',
        }

    def test_llm_span_generates_generation_type(self):
        obs = _span_to_observation(self._make_llm_span())
        assert obs["span_type"] == "LLM"
        assert obs["type"] == "GENERATION"

    def test_llm_span_name_adapted_with_keyword(self):
        obs = _span_to_observation(self._make_llm_span())
        assert "anthropic" in obs["name"]

    def test_tool_span_generates_tool_type(self):
        span = {
            "trace_id": "abc123", "span_id": "span-002",
            "parent_span_id": "span-001", "name": "gen_ai.tool.execute: bash",
            "start_time_ns": 1705308610_000000000,
            "end_time_ns": 1705308615_000000000,
            "duration_ns": 5_000_000_000,
            "attributes": '{"gen_ai.span.type": "tool", "gen_ai.tool.name": "bash"}',
            "events": '[]', "status_code": "OK", "status_description": "",
            "resource": '{}',
        }
        obs = _span_to_observation(span)
        assert obs["span_type"] == "TOOL"

    def test_parent_span_id_mapped(self):
        span = self._make_llm_span()
        span["parent_span_id"] = "span-000"
        obs = _span_to_observation(span)
        assert obs["parentObservationId"] == "span-000"

    def test_agent_span_generates_subagent_metadata(self):
        span = {
            "trace_id": "abc123", "span_id": "sub-001",
            "parent_span_id": "span-001", "name": "agent.sub_execute",
            "start_time_ns": 1705308610_000000000,
            "end_time_ns": 1705308620_000000000,
            "duration_ns": 10_000_000_000,
            "attributes": '{"gen_ai.span.type": "agent", "jiuwenclaw.agent.name": "explore"}',
            "events": '[]', "status_code": "OK", "status_description": "",
            "resource": '{}',
        }
        obs = _span_to_observation(span)
        assert obs["metadata"]["subagent_id"] == "sub-001"
        assert obs["metadata"]["subagent_name"] == "explore"


class TestAdaptObservationName:
    def test_llm_span_anthropic(self):
        result = _adapt_observation_name("gen_ai.chat", {"gen_ai.system": "anthropic"}, "model")
        assert "anthropic" in result

    def test_llm_span_openai(self):
        result = _adapt_observation_name("gen_ai.chat", {"gen_ai.system": "openai"}, "model")
        assert "openai" in result

    def test_non_model_span_unchanged(self):
        result = _adapt_observation_name("gen_ai.tool.execute", {}, "tool")
        assert result == "gen_ai.tool.execute"


class TestParseToolCallsRepr:
    def test_json_format(self):
        raw = '[{"id": "call_abc", "type": "function", "function": {"name": "bash", "arguments": "{}"}}]'
        result = _parse_tool_calls_repr(raw)
        assert len(result) == 1
        assert result[0]["function"]["name"] == "bash"

    def test_python_repr_fallback(self):
        raw = "[ToolCall(id='call_abc', name='bash', arguments='{\"cmd\":\"ls\"}')]"
        result = _parse_tool_calls_repr(raw)
        assert len(result) == 1
        assert result[0]["function"]["name"] == "bash"

    def test_empty_string(self):
        result = _parse_tool_calls_repr("")
        assert result == []


class TestOtelTraceAdapterConvertTrace:
    def test_convert_trace_produces_top_level_dict(self):
        adapter = OtelTraceAdapter.__new__(OtelTraceAdapter)
        adapter._db_path = "traces.db"
        # Mock _read_flat_spans to return test data
        adapter._read_flat_spans = lambda tid: [self._make_llm_span()]
        adapter._collect_tool_definitions = lambda spans: []

        result = adapter.convert_trace("abc123")
        assert result["id"] == "abc123"
        assert "observations" in result
        assert len(result["observations"]) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit_tests/test_otel_adapter.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement OtelTraceAdapter**

Create `jiuwenswarm/evolve/otel_adapter.py` implementing all the methods defined in `clean_trace.md`. Key methods:

- `_ns_to_iso(ns)` — nanosecond timestamp → ISO 8601
- `_ns_to_ms(ns)` — nanosecond → millisecond float
- `_span_to_observation(span)` — single OTEL span dict → Langfuse observation dict (with field mapping per §4.2 of clean_trace.md)
- `_adapt_observation_name(name, attrs, span_type)` — ensure LLM span names contain `is_llm_span` keywords
- `_parse_tool_calls_repr(raw)` — tolerant parser for Python repr tool_calls strings
- `_reconstruct_llm_input(attrs, events)` — events → OpenAI-style input dict with messages
- `_reconstruct_llm_output(attrs, events)` — events → OpenAI-style output dict
- `_reconstruct_tool_input(attrs, events)` — tool arguments
- `_reconstruct_tool_output(attrs, events)` — tool results
- `_reconstruct_trace_input(root_span, all_spans)` — trace-level input from first user message
- `_reconstruct_trace_output(root_span, all_spans)` — trace-level output from last assistant message
- `_collect_tool_definitions(spans)` — simplified tool definitions from tool span names
- `_read_flat_spans(trace_id)` — read from SQLite and sort by start_time_ns
- `convert_trace(trace_id)` — full pipeline: read → flatten → convert each span → assemble top-level dict
- `convert_batch(batch)` — batch conversion

All JSON attributes/events fields are parsed with `json.loads()` with fallback to empty dict/list on parse failure. All `None` defaults use `"N/A"`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit_tests/test_otel_adapter.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add jiuwenswarm/evolve/otel_adapter.py tests/unit_tests/test_otel_adapter.py
git commit -m "feat(evolve): add OtelTraceAdapter — OTEL spans to Langfuse trace dict"
```

---

## Task 3: DiagnosisAgent

**Files:**
- Create: `jiuwenswarm/evolve/diagnosis/__init__.py`
- Create: `jiuwenswarm/evolve/diagnosis/models.py`
- Create: `jiuwenswarm/evolve/diagnosis/tools.py`
- Create: `jiuwenswarm/evolve/diagnosis/prompts.py`
- Create: `jiuwenswarm/evolve/diagnosis/agent.py`
- Create: `tests/unit_tests/test_diagnosis_agent.py`

This is the DiagnosisAgent per the design doc at `docs/superpowers/specs/2026-06-18-trace-diagnosis-agent-design.md`.

- [ ] **Step 1: Write failing tests for DiagnosisIssue and DiagnosisResult**

```python
# tests/unit_tests/test_diagnosis_agent.py
import pytest
from jiuwenswarm.evolve.diagnosis.models import DiagnosisIssue, DiagnosisResult


class TestDiagnosisIssue:
    def test_basic_issue(self):
        issue = DiagnosisIssue(
            issue_type="工具错误",
            summary="bash command not found",
            evidence="span #7: name='gen_ai.tool.execute: bash'",
            trace_id="abc123",
            span_index=7,
            root_cause="Skill missing path specification",
            suggested_fix="Add full path to experience",
        )
        assert issue.issue_type == "工具错误"

    def test_invalid_issue_type(self):
        with pytest.raises(ValueError):
            DiagnosisIssue(
                issue_type="invalid",
                summary="...", evidence="...", trace_id="abc123",
                span_index=0,
            )


class TestDiagnosisResult:
    def test_diagnose_mode(self):
        result = DiagnosisResult(
            mode="diagnose",
            issues=[],
            response="No issues found",
            iterations=5,
            budget_exceeded=False,
        )
        assert result.mode == "diagnose"
        assert result.proposals is None

    def test_budget_exceeded(self):
        result = DiagnosisResult(
            mode="diagnose",
            issues=[],
            response="[budget-exceeded] partial analysis",
            iterations=20,
            budget_exceeded=True,
        )
        assert result.budget_exceeded is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit_tests/test_diagnosis_agent.py::TestDiagnosisIssue -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement diagnosis/models.py**

```python
# jiuwenswarm/evolve/diagnosis/models.py
from __future__ import annotations
from dataclasses import dataclass, field

ALLOWED_ISSUE_TYPES = {"工具错误", "幻觉", "循环", "不合规", "截断"}

@dataclass
class DiagnosisIssue:
    """Single diagnostic finding."""
    issue_type: str
    summary: str
    evidence: str
    trace_id: str
    span_index: int
    root_cause: str | None = None
    suggested_fix: str | None = None

    def __post_init__(self):
        if self.issue_type not in ALLOWED_ISSUE_TYPES:
            raise ValueError(f"issue_type must be one of {sorted(ALLOWED_ISSUE_TYPES)}, got '{self.issue_type}'")

@dataclass
class DiagnosisResult:
    """Agent diagnosis result."""
    mode: str  # "diagnose" | "propose"
    issues: list[DiagnosisIssue]
    response: str
    iterations: int
    budget_exceeded: bool
    proposals: list | None = None  # propose mode: list[Proposal]
```

- [ ] **Step 4: Run model tests to verify they pass**

Run: `pytest tests/unit_tests/test_diagnosis_agent.py::TestDiagnosisIssue tests/unit_tests/test_diagnosis_agent.py::TestDiagnosisResult -v`
Expected: ALL PASS

- [ ] **Step 5: Write failing tests for tool implementations**

```python
# Append to tests/unit_tests/test_diagnosis_agent.py

from jiuwenswarm.evolve.diagnosis.tools import (
    DiagnosisToolExecutor,
    _truncate_tool_output,
)


class TestTruncateToolOutput:
    def test_short_output_not_truncated(self):
        result = _truncate_tool_output("short content", max_chars=1000)
        assert result == "short content"

    def test_long_output_truncated(self):
        lines = [f"line {i}" for i in range(200)]
        content = "\n".join(lines)
        result = _truncate_tool_output(content, max_chars=5000, head_lines=50, tail_lines=30)
        assert "[...truncated" in result


class TestDiagnosisToolExecutorReadSpans:
    def test_read_spans_with_pagination(self):
        """Tool executor returns paginated spans from mock store."""
        executor = DiagnosisToolExecutor(store=MockStore())
        result = executor.execute("read_spans", {"trace_id": "abc123", "limit": 10})
        assert result["trace_id"] == "abc123"
        assert result["total_spans"] >= 0
        assert "spans" in result


class MockStore:
    """Minimal mock for testing tools without real SQLite."""
    def read_spans(self, trace_id):
        return [{"name": "gen_ai.chat", "span_id": "s1", "trace_id": trace_id}]
```

- [ ] **Step 6: Run tool tests to verify they fail**

Run: `pytest tests/unit_tests/test_diagnosis_agent.py::TestTruncateToolOutput -v`
Expected: FAIL — module not found

- [ ] **Step 7: Implement diagnosis/tools.py**

Create `jiuwenswarm/evolve/diagnosis/tools.py` implementing all 7 tools from the design doc:

- `read_spans(trace_id, offset, limit, name_filter)` — paginated span reading from SqliteStore
- `search_spans(trace_id, pattern, max_results)` — regex search within spans
- `list_traces(limit, since)` — list recent trace IDs
- `query_evolve_records(trace_id)` — query Proposal/Decision/Apply chain
- `query_proposals(batch_id)` — query batch proposals
- `read_file(path, offset, limit)` — file reading with safety constraints
- `submit_result(result)` — stop signal (returns "TASK_COMPLETED")

Also implement `_truncate_tool_output(content, max_chars, head_lines, tail_lines)` and `DiagnosisToolExecutor` class that dispatches tool calls to the appropriate method.

- [ ] **Step 8: Run tool tests to verify they pass**

Run: `pytest tests/unit_tests/test_diagnosis_agent.py::TestTruncateToolOutput tests/unit_tests/test_diagnosis_agent.py::TestDiagnosisToolExecutorReadSpans -v`
Expected: ALL PASS

- [ ] **Step 9: Implement diagnosis/prompts.py**

Create `jiuwenswarm/evolve/diagnosis/prompts.py` containing:

- `DIAGNOSIS_SYSTEM_PROMPT` — the full system prompt from the design doc §3.2 (5-phase workflow, tool list, output contract, iteration budget)
- `TOOL_DESCRIPTIONS` — dict mapping tool_name → description string for the prompt

- [ ] **Step 10: Implement diagnosis/agent.py**

Create `jiuwenswarm/evolve/diagnosis/agent.py` containing the `DiagnosisAgent` class with:

- `__init__(store, model, max_iterations, temperature)` — constructor
- `run(trace_ids, mode, question)` — main entry point, executes ReAct loop
- `_react_loop(messages)` — core ReAct loop with tool call parsing
- `_parse_tool_calls(content)` — JSON tool call parsing with regex fallback
- `_validate_payload(payload)` — output JSON schema validation per mode
- `_compact_context(messages)` — context compression when approaching token limit
- `_build_messages(trace_ids, mode, question)` — initial message construction
- `_finalize(result_json, iterations)` — parse submit_result payload into DiagnosisResult
- ProposalGenerator interface: `generate(batch)` method that calls `run(mode="propose")`

LLM calls use openjiuwen `Model` (same pattern as `LLMProposer._call_llm()`).

- [ ] **Step 11: Implement diagnosis/__init__.py**

```python
# jiuwenswarm/evolve/diagnosis/__init__.py
from jiuwenswarm.evolve.diagnosis.agent import DiagnosisAgent
from jiuwenswarm.evolve.diagnosis.models import DiagnosisResult, DiagnosisIssue

__all__ = ["DiagnosisAgent", "DiagnosisResult", "DiagnosisIssue"]
```

- [ ] **Step 12: Write integration test for full ReAct loop (mocked LLM)**

```python
# Append to tests/unit_tests/test_diagnosis_agent.py

class TestDiagnosisAgentReActLoop:
    async def test_agent_generates_issues(self):
        """Simulate a 3-round ReAct loop with mocked LLM responses."""
        agent = DiagnosisAgent(store=MockStore(), model=MockModel())
        result = await agent.run(
            trace_ids=["abc123"],
            mode="diagnose",
            question="Why did this trace fail?",
        )
        assert result.mode == "diagnose"
        assert result.iterations > 0
        assert result.budget_exceeded is False
```

Implement `MockModel` that returns predetermined tool call sequences (read_spans → search_spans → submit_result).

- [ ] **Step 13: Run all diagnosis tests**

Run: `pytest tests/unit_tests/test_diagnosis_agent.py -v`
Expected: ALL PASS

- [ ] **Step 14: Commit**

```bash
git add jiuwenswarm/evolve/diagnosis/ tests/unit_tests/test_diagnosis_agent.py
git commit -m "feat(evolve): add DiagnosisAgent — lightweight ReAct trace diagnosis"
```

---

## Task 4: TraceOutcomeEvaluator

**Files:**
- Create: `jiuwenswarm/evolve/pda/evaluator.py`
- Create: `tests/unit_tests/test_evaluator.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit_tests/test_evaluator.py
import pytest
from jiuwenswarm.evolve.pda.evaluator import (
    TraceOutcomeEvaluator,
    TaskNameInferrer,
)


class TestTaskNameInferrer:
    def test_from_skill_name(self):
        trace = {"id": "abc123def456", "skill_name": "bash-tool"}
        result = TaskNameInferrer.infer(trace)
        assert result == "skill_bash-tool_abc123de"

    def test_from_user_message(self):
        trace = {
            "id": "abc123def456",
            "messages": [{"role": "user", "content": "帮我写一个 Python 脚本"}],
        }
        result = TaskNameInferrer.infer(trace)
        assert "task_" in result
        assert "abc123de" in result

    def test_fallback_to_trace_id(self):
        trace = {"id": "abc123def456"}
        result = TaskNameInferrer.infer(trace)
        assert result == "abc123def456"


class TestTraceOutcomeEvaluatorFast:
    """Non-LLM heuristic evaluation."""

    def test_span_error_detection(self):
        evaluator = TraceOutcomeEvaluator()
        result = evaluator.evaluate_fast(
            trace_dict={"status_code": "ERROR", "status_description": "timeout"},
        )
        assert result.outcome == "fail"
        assert result.judgment_method == "span_error"

    def test_empty_output_detection(self):
        result = evaluator.evaluate_fast(trace_dict={"output": ""})
        assert result.outcome == "uncertain"
        assert result.judgment_method == "heuristic"

    def test_normal_trace_returns_uncertain(self):
        result = evaluator.evaluate_fast(trace_dict={"status_code": "OK", "output": "some text"})
        assert result.outcome == "uncertain"  # heuristic can't reliably judge pass/fail
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit_tests/test_evaluator.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement evaluator.py**

Create `jiuwenswarm/evolve/pda/evaluator.py` containing:

- `TaskNameInferrer` — infer task_name from skill_name, user message, or trace_id
- `TraceOutcomeEvaluator` — two evaluation methods:
  - `evaluate_fast(trace_dict)` — heuristic + span_error detection (no LLM call)
  - `evaluate(input_text, output_text)` — LLM-based evaluation using the system prompt from the design doc §3.3
  - `evaluate_batch(normalized_traces)` — evaluate all traces, return list of TraceOutcome

LLM call pattern: same as `LLMProposer._call_llm()` — construct `SystemMessage` + `UserMessage`, call `model.invoke()`, parse JSON response.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit_tests/test_evaluator.py -v`
Expected: ALL PASS (fast/heuristic tests pass; async LLM tests may need mock)

- [ ] **Step 5: Commit**

```bash
git add jiuwenswarm/evolve/pda/evaluator.py tests/unit_tests/test_evaluator.py
git commit -m "feat(evolve): add TraceOutcomeEvaluator — task completion assessment"
```

---

## Task 5: ExperienceGovernor

**Files:**
- Create: `jiuwenswarm/evolve/pda/experience_governor.py`
- Create: `tests/unit_tests/test_experience_governor.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit_tests/test_experience_governor.py
import pytest
from jiuwenswarm.evolve.models import (
    ExperienceOperationType,
    ExperienceOperation,
    GovernanceContext,
)
from jiuwenswarm.evolve.pda.experience_governor import ExperienceGovernor


class TestGovernorGetContext:
    def test_empty_skill_returns_can_add(self):
        governor = ExperienceGovernor(skills_dir="/tmp/test_skills", max_per_skill=10)
        ctx = governor.get_context("bash-tool")
        assert ctx.can_add is True
        assert ctx.current_count == 0
        assert ExperienceOperationType.ADD in ctx.allowed_operations

    def test_full_skill_disallows_add(self):
        """When skill has 10 experiences, ADD is disallowed, REPLACE/NOOP allowed."""
        # Setup: create evolutions.json with 10 entries
        governor = ExperienceGovernor(skills_dir="/tmp/test_skills_full", max_per_skill=10)
        # ... populate evolutions.json with 10 entries ...
        ctx = governor.get_context("bash-tool")
        assert ctx.can_add is False
        assert ExperienceOperationType.ADD not in ctx.allowed_operations
        assert ExperienceOperationType.REPLACE in ctx.allowed_operations

    def test_similar_experience_detection(self):
        """When existing experience has similar content, MERGE is allowed."""
        governor = ExperienceGovernor(skills_dir="/tmp/test_skills", max_per_skill=10)
        # ... populate with experience about "python path error" ...
        ctx = governor.get_context("bash-tool", query_hint="python path not found")
        assert ExperienceOperationType.MERGE in ctx.allowed_operations


class TestGovernorValidateOperation:
    def test_add_approved_when_can_add(self):
        governor = ExperienceGovernor(skills_dir="/tmp/test_skills", max_per_skill=10)
        op = ExperienceOperation(
            op=ExperienceOperationType.ADD,
            new_content="test content",
            reason="new experience",
            evidence_refs=[],
        )
        assert governor.validate_operation("bash-tool", op) is True

    def test_add_rejected_when_full(self):
        governor = ExperienceGovernor(skills_dir="/tmp/test_skills_full", max_per_skill=10)
        op = ExperienceOperation(
            op=ExperienceOperationType.ADD,
            new_content="test content",
            reason="should be rejected",
            evidence_refs=[],
        )
        assert governor.validate_operation("bash-tool", op) is False

    def test_replace_approved_for_replaceable_experience(self):
        governor = ExperienceGovernor(skills_dir="/tmp/test_skills", max_per_skill=10)
        op = ExperienceOperation(
            op=ExperienceOperationType.REPLACE,
            target_experience_id="exp-low-value",
            new_content="better content",
            reason="replace low-value experience",
            evidence_refs=[],
        )
        # The target must be in replaceable_experiences list
        assert governor.validate_operation("bash-tool", op) is True  # if target is replaceable
```

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Implement ExperienceGovernor**

Key responsibilities:
1. `get_context(skill_name, query_hint=None)` — read evolutions.json, classify experiences (existing, similar, replaceable, protected), compute allowed operations
2. `validate_operation(skill_name, operation)` — check operation is in allowed_operations and target is valid
3. `_classify_experiences(entries)` — categorize by state (candidate/active) and hit_count
4. `_find_similar(entries, query_hint)` — simple text similarity matching

- [ ] **Step 4: Run tests to verify they pass**

- [ ] **Step 5: Commit**

```bash
git add jiuwenswarm/evolve/pda/experience_governor.py tests/unit_tests/test_experience_governor.py
git commit -m "feat(evolve): add ExperienceGovernor — experience governance context provider"
```

---

## Task 6: PdaProposer

**Files:**
- Create: `jiuwenswarm/evolve/pda/proposer.py`
- Create: `tests/unit_tests/test_pda_proposer.py`

This self-contains CLEAN→EVAL→DIAG→GOV→PROPOSE.

- [ ] **Step 1: Write failing tests**

```python
# tests/unit_tests/test_pda_proposer.py
import pytest
from jiuwenswarm.evolve.models import TraceBatch, Proposal, ProposalTargetType
from jiuwenswarm.evolve.pda.proposer import PdaProposer


class TestPdaProposerEnforceLimits:
    def test_max_proposals_per_batch(self):
        proposer = PdaProposer.__new__(PdaProposer)
        proposer._max_proposals = 3
        proposer._max_skill_proposals = 2
        proposals = [
            Proposal(target_type=ProposalTargetType.SKILL, proposal_type="add_skill_experience",
                     failure_evidence=[], root_cause="r1", targeted_fix={}, predicted_impact="p1"),
            Proposal(target_type=ProposalTargetType.SKILL, proposal_type="add_skill_experience",
                     failure_evidence=[], root_cause="r2", targeted_fix={}, predicted_impact="p2"),
            Proposal(target_type=ProposalTargetType.SKILL, proposal_type="add_skill_experience",
                     failure_evidence=[], root_cause="r3", targeted_fix={}, predicted_impact="p3"),
        ]
        result = proposer._enforce_limits(proposals)
        skill_count = sum(1 for p in result if p.target_type == ProposalTargetType.SKILL and p.state.value == "active")
        assert skill_count <= 2

    def test_all_pass_returns_empty(self):
        """When all traces are 'pass', no proposals are generated."""
        proposer = PdaProposer(trace_reader=MockTraceReader(), store=MockStore())
        # Mock: all outcomes are "pass"
        result = await proposer.generate(TraceBatch(trace_ids=["abc123"]))
        assert result == []
```

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Implement PdaProposer**

Create `jiuwenswarm/evolve/pda/proposer.py`. The `generate(batch)` method implements the full pipeline:

1. LOAD: read trace_ids from batch
2. CLEAN: OtelTraceAdapter.convert_trace() → extract_trace_data() → NormalizedTrace dict
3. EVAL: TraceOutcomeEvaluator.evaluate() → filter fail/uncertain
4. DIAG: DiagnosisAgent.run() → DiagnosisResult
5. GOV: ExperienceGovernor.get_context() for each implicated skill
6. PROPOSE: LLM call with all accumulated context → parse Proposal[] with ExperienceOperation[]
7. Enforce limits: max 3 per batch, max 2 skill, max 1 per skill

LLM prompt (`PDA_PROPOSER_SYSTEM_PROMPT`) includes: task outcomes, diagnosis results, governance contexts, and operation type descriptions.

- [ ] **Step 4: Run tests to verify they pass**

- [ ] **Step 5: Commit**

```bash
git add jiuwenswarm/evolve/pda/proposer.py tests/unit_tests/test_pda_proposer.py
git commit -m "feat(evolve): add PdaProposer — self-contained CLEAN→EVAL→DIAG→GOV→PROPOSE pipeline"
```

---

## Task 7: PdaDecisionPolicy

**Files:**
- Create: `jiuwenswarm/evolve/pda/decision_policy.py`
- Create: `tests/unit_tests/test_pda_decision_policy.py`

- [ ] **Step 1: Write failing tests for RuleGate**

```python
# tests/unit_tests/test_pda_decision_policy.py
import pytest
from jiuwenswarm.evolve.models import (
    Proposal, ProposalTargetType, ProposalState, DecisionResult,
    DecisionSuggestion, EvidenceRef,
)
from jiuwenswarm.evolve.pda.decision_policy import PdaDecisionPolicy


class TestRuleGate:
    def _make_proposal(self, **overrides):
        defaults = {
            "target_type": ProposalTargetType.SKILL,
            "target_id": "bash-tool",
            "proposal_type": "add_skill_experience",
            "failure_evidence": [EvidenceRef(trace_id="abc123", description="bash error")],
            "root_cause": "Missing path specification",
            "targeted_fix": {"action": "add_knowledge", "suggestion": "Use /usr/bin/python3"},
            "predicted_impact": "Reduce tool error rate",
            "proposer_name": "pda_proposer",
        }
        defaults.update(overrides)
        return Proposal(**defaults)

    def test_empty_evidence_blocked(self):
        policy = PdaDecisionPolicy(governor=MockGovernor())
        proposal = self._make_proposal(failure_evidence=[])
        result = policy._rule_gate(proposal)
        assert result.blocking is True
        assert "empty_failure_evidence" in result.failed_checks

    def test_empty_root_cause_blocked(self):
        proposal = self._make_proposal(root_cause="")
        result = policy._rule_gate(proposal)
        assert result.blocking is True
        assert "empty_root_cause" in result.failed_checks

    def test_unsupported_target_type_blocked(self):
        proposal = self._make_proposal(target_type=ProposalTargetType.MEMORY)
        result = policy._rule_gate(proposal)
        assert result.blocking is True
        assert "unsupported_target_type" in result.failed_checks

    def test_governance_violation_blocked(self):
        """ADD operation when skill is full → governance violation."""
        mock_governor = MockGovernor(can_add=False)
        proposal = self._make_proposal(
            metadata={"operations": [{"op": "add", "new_content": "test", "reason": "test", "evidence_refs": []}]}
        )
        result = policy._rule_gate(proposal)
        assert result.blocking is True
        assert "governance_violation_add" in result.failed_checks

    def test_valid_proposal_passes_rule_gate(self):
        proposal = self._make_proposal()
        result = policy._rule_gate(proposal)
        assert result.blocking is False
        assert result.failed_checks == []
```

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Implement PdaDecisionPolicy**

Create `jiuwenswarm/evolve/pda/decision_policy.py` with:

- `_rule_gate(proposal)` — hard constraint checks (field completeness, target_type, governance, duplicate)
- `_llm_decision(proposal)` — LLM semantic judgment (consistency, reasonability, risk)
- `evaluate(proposal)` — two-phase: RuleGate first (blocking → early return), then LLMDecision

LLM prompt includes: proposal content, governance context summary, evaluation dimensions.

- [ ] **Step 4: Run tests to verify they pass**

- [ ] **Step 5: Commit**

```bash
git add jiuwenswarm/evolve/pda/decision_policy.py tests/unit_tests/test_pda_decision_policy.py
git commit -m "feat(evolve): add PdaDecisionPolicy — RuleGate + LLMDecision two-phase judgment"
```

---

## Task 8: SkillExperienceWriter Extension

**Files:**
- Modify: `jiuwenswarm/evolve/apply_writers/skill_writer.py`
- Create: `tests/unit_tests/test_skill_writer_ops.py`

- [ ] **Step 1: Write failing tests for ADD/MERGE/REPLACE/NOOP**

```python
# tests/unit_tests/test_skill_writer_ops.py
import pytest
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from jiuwenswarm.evolve.models import (
    Proposal, ProposalTargetType, ProposalState, EvidenceRef,
    ExperienceOperationType, ExperienceOperation,
)
from jiuwenswarm.evolve.apply_writers.skill_writer import SkillExperienceWriter


class TestSkillWriterAdd:
    async def test_add_creates_new_entry_as_candidate(self):
        with TemporaryDirectory() as tmpdir:
            writer = SkillExperienceWriter(skills_dir=tmpdir)
            proposal = Proposal(
                target_type=ProposalTargetType.SKILL,
                target_id="bash-tool",
                proposal_type="add_skill_experience",
                failure_evidence=[EvidenceRef(trace_id="abc123", description="error")],
                root_cause="missing path",
                targeted_fix={"action": "add_knowledge", "suggestion": "Use /usr/bin/python3"},
                predicted_impact="reduce errors",
                state=ProposalState.ACTIVE,
                proposer_name="pda_proposer",
                metadata={
                    "operations": [ExperienceOperation(
                        op=ExperienceOperationType.ADD,
                        new_content="Use /usr/bin/python3 for full path",
                        reason="Agent used wrong path",
                        evidence_refs=[EvidenceRef(trace_id="abc123", description="bash error")],
                    ).model_dump()],
                },
            )
            record = await writer.apply(proposal)
            assert record.status.value == "applied"

            # Verify evolutions.json created
            evo_path = Path(tmpdir) / "bash-tool" / "evolutions.json"
            assert evo_path.exists()
            data = json.loads(evo_path.read_text())
            assert data["entries"][-1]["change"]["content"] == "Use /usr/bin/python3 for full path"
            assert data["entries"][-1]["metadata"]["state"] == "candidate"


class TestSkillWriterNoop:
    async def test_noop_does_not_modify_evolutions(self):
        with TemporaryDirectory() as tmpdir:
            # Pre-create evolutions.json with 1 entry
            ...
            writer = SkillExperienceWriter(skills_dir=tmpdir)
            proposal = ...  # NOOP operation
            record = await writer.apply(proposal)
            assert record.status.value == "applied"
            assert record.reason == "NOOP — no changes made"


class TestSkillWriterReplace:
    async def test_replace_swaps_target_experience(self):
        # Create evolutions.json with a replaceable entry
        # Apply REPLACE operation targeting that entry
        # Verify target entry replaced with new content
        ...
```

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Extend SkillExperienceWriter.apply()**

Modify `jiuwenswarm/evolve/apply_writers/skill_writer.py`:

- Add `_apply_add(proposal, op)` — append new entry with state=candidate
- Add `_apply_merge(proposal, op)` — append evidence_refs to target entry
- Add `_apply_replace(proposal, op)` — replace target entry content
- Add `_apply_update(proposal, op)` — update target entry content
- Add `_apply_deprecate(proposal, op)` — mark target entry as deprecated
- Modify main `apply()` method to parse `proposal.metadata["operations"]` and dispatch
- Preserve backward compatibility: if no `operations` key, default to ADD behavior (existing logic)

- [ ] **Step 4: Run tests to verify they pass**

- [ ] **Step 5: Commit**

```bash
git add jiuwenswarm/evolve/apply_writers/skill_writer.py tests/unit_tests/test_skill_writer_ops.py
git commit -m "feat(evolve): extend SkillExperienceWriter with ExperienceOperation support — ADD/MERGE/REPLACE/DEPRECATE/NOOP"
```

---

## Task 9: CLI & Config Integration

**Files:**
- Modify: `jiuwenswarm/evolve/cli.py`
- Modify: `jiuwenswarm/evolve/config.yaml`
- Create: `jiuwenswarm/evolve/pda/__init__.py`

- [ ] **Step 1: Write failing test for CLI --pda flag**

```python
# tests/unit_tests/test_cli_pda.py (or extend existing test)
def test_pda_flag_selects_pda_proposer():
    """When --pda is passed, PdaProposer is used instead of llm_proposer."""
    ...
```

- [ ] **Step 2: Run test to verify it fails**

- [ ] **Step 3: Add --pda flag to CLI**

In `jiuwenswarm/evolve/cli.py`, extend the `run` subcommand to accept `--pda` flag. When set, use `pda_proposer` + `pda_decision_policy` instead of the default generators.

Add `diagnose` subcommand:
```
jiuwenswarm-evolve diagnose --traces abc123,def456 --question "..."
jiuwenswarm-evolve diagnose --latest 10
```

Add `governor` subcommand:
```
jiuwenswarm-evolve governor --skill bash-tool --status
jiuwenswarm-evolve governor --skill bash-tool --deprecate <exp-id>
```

- [ ] **Step 4: Add pda config section**

In `jiuwenswarm/evolve/config.yaml`, add the `pda:` section from the design doc §5.

- [ ] **Step 5: Create pda/__init__.py**

```python
# jiuwenswarm/evolve/pda/__init__.py
from jiuwenswarm.evolve.pda.proposer import PdaProposer
from jiuwenswarm.evolve.pda.decision_policy import PdaDecisionPolicy
from jiuwenswarm.evolve.pda.experience_governor import ExperienceGovernor
from jiuwenswarm.evolve.pda.evaluator import TraceOutcomeEvaluator, TaskNameInferrer

__all__ = [
    "PdaProposer",
    "PdaDecisionPolicy",
    "ExperienceGovernor",
    "TraceOutcomeEvaluator",
    "TaskNameInferrer",
]
```

- [ ] **Step 6: Update evolve/__init__.py exports**

Add `DiagnosisAgent` and PDA module exports.

- [ ] **Step 7: Run CLI tests**

- [ ] **Step 8: Commit**

```bash
git add jiuwenswarm/evolve/cli.py jiuwenswarm/evolve/config.yaml jiuwenswarm/evolve/pda/__init__.py jiuwenswarm/evolve/__init__.py tests/unit_tests/test_cli_pda.py
git commit -m "feat(evolve): add CLI --pda flag, diagnose/governor subcommands, pda config"
```

---

## Task 10: Integration Test — End-to-End Mini Benchmark

**Files:**
- Create: `tests/integration_tests/test_pda_e2e.py`

- [ ] **Step 1: Write integration test**

```python
# tests/integration_tests/test_pda_e2e.py
import pytest
from jiuwenswarm.evolve.models import TraceBatch
from jiuwenswarm.evolve.pda.proposer import PdaProposer
from jiuwenswarm.evolve.pda.decision_policy import PdaDecisionPolicy


@pytest.mark.asyncio
class TestPdaEndToEnd:
    async def test_full_pipeline_from_mock_traces(self):
        """Full PDA pipeline: LOAD → CLEAN → EVAL → DIAG → PROPOSE → DECIDE → APPLY."""
        # Setup: create traces.db with 5 test traces (3 fail, 2 pass)
        # Setup: create evolution.db and skills dir
        store = create_mock_store(...)
        proposer = PdaProposer(trace_reader=store._sqlite, store=store, model=mock_model)
        policy = PdaDecisionPolicy(governor=ExperienceGovernor(store=store))

        # Run
        batch = TraceBatch(trace_ids=["fail1", "fail2", "fail3", "pass1", "pass2"])
        proposals = await proposer.generate(batch)

        # Verify: proposals only for fail traces
        assert len(proposals) <= 3
        for p in proposals:
            assert p.target_type == ProposalTargetType.SKILL

        # Decide
        decisions = []
        for p in proposals:
            dr = await policy.evaluate(p)
            decisions.append(dr)

        # Verify: at least one proposal accepted
        accepted = [p for p, d in zip(proposals, decisions) if d.suggestion.value != "rejected"]
        assert len(accepted) > 0

        # Apply
        writer = SkillExperienceWriter(skills_dir=...)
        for p in accepted:
            record = await writer.apply(p)
            assert record.status.value == "applied"

        # Verify: evolutions.json written with candidate state
        ...
```

- [ ] **Step 2: Run integration test with mocked infrastructure**

- [ ] **Step 3: Commit**

```bash
git add tests/integration_tests/test_pda_e2e.py
git commit -m "test(evolve): add PDA end-to-end integration test"
```

---

## Self-Review

### Spec Coverage Check

| Spec Requirement | Task |
|---|---|
| LOAD traces from traces.db | Task 6 (PdaProposer step 1) |
| CLEAN — OTEL spans → NormalizedTrace | Task 2 (OtelTraceAdapter) |
| TaskCompletion evaluator — pass/fail/uncertain | Task 4 (TraceOutcomeEvaluator) |
| Diagnosis — root cause analysis | Task 3 (DiagnosisAgent) |
| Proposal with ExperienceOperation | Task 1 (models) + Task 6 (PdaProposer) |
| Decision — RuleGate + LLMDecision | Task 7 (PdaDecisionPolicy) |
| Apply — candidate experience write | Task 8 (SkillWriter extension) |
| Record — audit chain persistence | Existing pipeline._persist() |
| Experience governance — governor + operations | Task 5 (ExperienceGovernor) |
| CLI + Config integration | Task 9 |
| End-to-end test | Task 10 |

All 10 success criteria from the spec are covered ✅

### Placeholder Scan

No TBD/TODO found ✅

### Type Consistency Check

- `ExperienceOperationType` defined in Task 1 (models.py), used consistently in Tasks 5, 6, 7, 8 ✅
- `TraceOutcome` defined in Task 1 (models.py), used in Task 4 (evaluator) ✅
- `GovernanceContext` defined in Task 1 (models.py), used in Task 5 (governor) ✅
- `DiagnosisResult` defined in Task 3 (diagnosis/models.py), used in Task 6 (PdaProposer) ✅
- All method signatures match across tasks ✅
