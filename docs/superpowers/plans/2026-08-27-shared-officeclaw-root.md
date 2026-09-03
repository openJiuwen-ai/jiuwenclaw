# Shared OfficeClaw Root Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reuse an unbound OfficeClaw root agent while preserving a distinct project-bound child adapter for every session.

**Architecture:** `AgentManager` selects a project-free cache key only behind an OfficeClaw feature flag. `JiuWenSwarmDeepAdapter` creates a session child with a deep-copied root config plus the request project overlay, and rejects attempts to rebind an existing session child to a different project.

**Tech Stack:** Python 3.11+, asyncio, pytest, OpenJiuwen DeepAgent.

**Spec:** `docs/superpowers/specs/2026-08-27-first-token-performance-design.md`

## Global Constraints

- Default behavior and every non-OfficeClaw channel retain project-keyed root caching.
- Never place MCP config, callback tokens, request metadata, session IDs, checkpoints, or user content on the shared root.
- Do not mutate root config while constructing a session child.
- Reject, rather than overwrite, an existing session's non-empty project binding.
- Add tests before production changes.

---

### Task 1: Lock down root-cache selection

**Files:**
- Modify: `tests/unit_tests/agentserver/test_agent_manager_session_cleanup.py`
- Modify: `jiuwenswarm/server/runtime/agent_manager.py`

**Interfaces:**
- Produces: `_shared_officeclaw_root_enabled()` and project-free OfficeClaw cache-key selection.

- [ ] Write failing tests that two OfficeClaw project directories create one root only when the feature flag is enabled, while a disabled flag retains two roots.
- [ ] Run the focused tests and verify failure because project-free key selection is absent.
- [ ] Add a narrowly-scoped environment flag and select an empty project component only for OfficeClaw agent mode.
- [ ] Run the focused tests and verify they pass.

### Task 2: Bind project state only to a new session child

**Files:**
- Modify: `tests/unit_tests/agentserver/test_agent_manager_session_cleanup.py`
- Modify: `jiuwenswarm/server/runtime/agent_adapter/interface_deep.py`

**Interfaces:**
- Changes: `_get_or_create_session_adapter(session_id, *, project_dir=None)`.
- Produces: child config copied from root and overlayed with the supplied project directory.

- [ ] Write failing tests proving two child adapters receive distinct project overlays and the root config remains unchanged.
- [ ] Run the focused tests and verify failure because the method has no project overlay argument.
- [ ] Deep-copy root session config, apply the project overlay only to the copy, and reject a later conflicting project binding for an existing session.
- [ ] Pass project_dir from `prepare_session`, unary chat, and streaming chat root dispatches.
- [ ] Run the focused tests and verify they pass.

### Task 3: Add safe observability and regression checks

**Files:**
- Modify: `tests/unit_tests/agentserver/test_agent_manager_session_cleanup.py`
- Modify: `jiuwenswarm/server/runtime/agent_manager.py`

**Interfaces:**
- Produces: `[latency.shared_root]` hit/miss/bypass log line without paths, query text, metadata or credentials.

- [ ] Write a failing source/behavior test for flag-bypass behavior and shared-root hit selection.
- [ ] Run the focused tests and verify failure.
- [ ] Add minimal redacted logging at cache selection and run focused tests.
- [ ] Run the stage A/B regression tests, the agent manager tests, and Python compilation for changed modules.
