# DeepResearch Friendly Progress Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render DeepResearch outline, section plans, and retrieved sources as readable Markdown without changing its existing frontend event protocol.

**Architecture:** Add a pure, node-aware display formatter inside the DeepResearch stream router. Apply it only when creating `chat.reasoning` frames, leaving raw chunks, task metadata, interruption state, and recovery behavior unchanged.

**Tech Stack:** Python 3.12, pytest, existing RelayClaw `chat.reasoning` events.

---

### Task 1: Specify friendly display behavior

**Files:**
- Modify: `tests/unit/agentserver/test_deepresearch_stream_router.py`

- [x] Add a failing test asserting that an `outline` JSON string becomes Markdown containing the report title, ordered section titles and descriptions, and does not expose `"sections"` or `"thought"` JSON keys.
- [x] Add a failing test asserting that a `plan_reasoning` JSON string becomes Markdown containing the plan title, thought, research status, and ordered step titles and descriptions.
- [x] Add a failing test asserting that a `collector_info_retrieval` document JSON string becomes a Markdown link plus its query.
- [x] Add a compatibility test asserting that ordinary text and an unknown JSON object remain unchanged.
- [x] Run the four focused tests and confirm they fail only because raw JSON is still emitted.

### Task 2: Implement the node-aware formatter

**Files:**
- Modify: `jiuwenclaw/agentserver/tools/deepresearch/stream_router.py`

- [x] Add a JSON-object parser that accepts dictionaries and JSON object strings, but returns no parsed value for malformed JSON, arrays, and scalar values.
- [x] Add focused Markdown renderers for outline, plan, and retrieved-source shapes.
- [x] Add a dispatcher keyed by `agent`, returning the original `_as_text` value when a shape is unrecognized.
- [x] Call the dispatcher from `_raw_process_parts` while keeping reasoning content and the `sub_reporter` body suppression contract intact.
- [x] Run the focused tests and confirm they pass.

### Task 3: Verify compatibility

**Files:**
- Test: `tests/unit/agentserver/test_deepresearch_stream_router.py`
- Test: `tests/unit/agentserver/test_deepresearch_stream_tool.py`

- [x] Run the complete router test module.
- [x] Run the DeepResearch stream tool test module to verify the unchanged event and interruption contract.
- [x] Run `git diff --check`.
- [x] Inspect the final diff and confirm no files outside the design, plan, router, and router tests changed.
