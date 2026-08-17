# Decouple Symphony Graph Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent switching JiuwenSwarm's current/default planning model from marking an already-published Symphony graph stale.

**Architecture:** Keep agent-core unchanged and preserve the model identity recorded in each graph artifact. JiuwenSwarm's graph-status path will validate skills and build settings against that published identity, while graph building and online planning continue to resolve the current default model independently.

**Tech Stack:** Python, pytest, JiuwenSwarm Symphony adapter, openJiuwen Symphony runtime

---

### Task 1: Lock the service boundary with a regression test

**Files:**
- Modify: `tests/unit_tests/symphony/test_direct_service.py`

- [x] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_service_graph_status_does_not_bind_freshness_to_current_default_model(
    monkeypatch,
    tmp_path,
):
    config = _config(tmp_path)
    captured = {}

    monkeypatch.setattr(
        "jiuwenswarm.symphony.service.load_symphony_config",
        lambda: config,
    )

    def fake_graph_status(*args, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            to_dict=lambda: {"success": True, "exists": True, "stale": False}
        )

    monkeypatch.setattr(
        "jiuwenswarm.symphony.service.graph_status",
        fake_graph_status,
    )
    monkeypatch.setattr(
        "jiuwenswarm.symphony.service.LLMConfig.from_default_model",
        lambda: (_ for _ in ()).throw(AssertionError("default model must not affect graph status")),
    )

    result = await SwarmSymphonyService().graph_status()

    assert result["stale"] is False
    assert captured.get("llm_config") is None
```

- [x] **Step 2: Run the test and confirm it fails**

Run: `.venv/bin/python -m pytest tests/unit_tests/symphony/test_direct_service.py::test_service_graph_status_does_not_bind_freshness_to_current_default_model -q`

Expected: FAIL because `SwarmSymphonyService.graph_status()` still resolves the current default model.

### Task 2: Decouple graph freshness from the planning model

**Files:**
- Modify: `jiuwenswarm/symphony/service.py:51-69`

- [x] **Step 1: Remove current-model resolution from graph status**

```python
async def graph_status(self) -> dict[str, Any]:
    config = load_symphony_config()
    skills_root = config.paths.skills_root
    graph_dir = config.paths.graph_dir
    await self._repair_interrupted_build_state(graph_dir)

    def status() -> dict[str, Any]:
        payload = graph_status(
            skills_root,
            graph_dir,
            llm_config=None,
            symphony_config=config,
        ).to_dict()
        payload.update(_build_log_payload(graph_dir))
        return payload

    return await asyncio.to_thread(status)
```

Passing `None` intentionally makes the status calculation retain the build-model and fingerprint identities stored in the published artifact. Skill content and Symphony build-setting changes remain freshness inputs.

- [x] **Step 2: Run the regression test**

Run: `.venv/bin/python -m pytest tests/unit_tests/symphony/test_direct_service.py::test_service_graph_status_does_not_bind_freshness_to_current_default_model -q`

Expected: PASS.

- [x] **Step 3: Run focused Symphony tests**

Run: `.venv/bin/python -m pytest tests/unit_tests/symphony/test_direct_service.py tests/unit_tests/symphony/test_llm.py -q`

Expected: PASS. Existing tests must continue to prove that explicit build-LLM changes invalidate direct build caches, while the Swarm service no longer treats the planning model as a freshness input.

- [x] **Step 4: Review scope**

Run: `git diff --check && git status --short`

Expected: only this plan, `jiuwenswarm/symphony/service.py`, and `tests/unit_tests/symphony/test_direct_service.py` are changed. Do not commit or push without an explicit request.
