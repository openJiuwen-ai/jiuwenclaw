# First Token Performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the approved stage A/B observability and low-risk first-token latency reductions.

**Architecture:** Reuse existing DeepAdapter latency markers and rails. Add a small internal prompt snapshot helper and rail timing wrapper; short-circuit invalid memory setup; build only the active-language deferred-tool navigation. No request protocol, persistence, MCP registration, or provider behavior changes.

**Tech Stack:** Python 3.11+, pytest, OpenJiuwen rails.

**Spec:** `docs/superpowers/specs/2026-08-27-first-token-performance-design.md`

## Global Constraints

- Do not log prompt bodies, user input, credentials, or MCP environment values.
- Preserve tool visibility and invocation semantics.
- Preserve provider-specific KV cache affinity fail-closed behavior.
- Add tests before production changes.

---

### Task 1: Test and implement memory-rail short circuit

**Files:**
- Modify: `tests/unit_tests/perf/test_interface_deep_perf_wiring.py`
- Modify: `jiuwenswarm/server/runtime/agent_adapter/interface_deep.py`

- [ ] Add a failing test asserting the source exposes a missing-embedding short circuit.
- [ ] Run the test and confirm it fails because the guard is absent.
- [ ] Return `None` before constructing `EmbeddingConfig` when one required field is empty.
- [ ] Run the targeted test.

### Task 2: Test and implement active-language navigation

**Files:**
- Create: `tests/unit_tests/agentserver/test_progressive_tool_navigation.py`
- Modify: `jiuwenswarm/agents/harness/common/rails/progressive_tool_rail.py`

- [ ] Add a failing async test that asserts an English agent only builds English navigation entries.
- [ ] Run the test and confirm it fails.
- [ ] Resolve the active language from the prompt builder and build one section content variant.
- [ ] Run the targeted test.

### Task 3: Test and implement observability

**Files:**
- Modify: `tests/unit_tests/perf/test_interface_deep_perf_wiring.py`
- Modify: `jiuwenswarm/server/runtime/agent_adapter/interface_deep.py`
- Modify: `jiuwenswarm/agents/harness/common/rails/progressive_tool_rail.py`

- [ ] Add failing regression checks for prompt/rail/affinity latency logging helpers.
- [ ] Run the tests and confirm they fail.
- [ ] Implement redacted prompt snapshots, rail duration logs, and effective affinity status logging.
- [ ] Run targeted tests and the relevant rail tests.

### Task 4: Verify

**Files:** none.

- [ ] Run all touched test files.
- [ ] Run static compilation for touched Python modules.
- [ ] Inspect the diff for secret-bearing logs and unintentional behavior changes.
