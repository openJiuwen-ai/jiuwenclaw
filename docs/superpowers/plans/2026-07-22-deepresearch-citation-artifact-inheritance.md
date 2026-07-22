# DeepResearch Citation Artifact Inheritance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve hidden citation preview associations for both initial DeepResearch reports and immutable rewritten child Markdown files.

**Architecture:** Normalize the two allowed hidden paths once in `deepresearch_tools.py`, persist them in initial provenance, and construct `chat.file.metadata.artifactBundle` from the normalized data. Child provenance already inherits parent keys; `deepresearch_rewrite_tools.py` will bounded-read the generated child sidecar and attach the inherited bundle without blocking Markdown delivery when metadata is absent or invalid.

**Tech Stack:** Python 3.11+, asyncio, JSON provenance sidecars, pytest/pytest-asyncio, JiuwenClaw gateway push events.

---

### Task 1: Initial report association and provenance

**Files:**
- Modify: `jiuwenclaw/agentserver/tools/deepresearch_tools.py:77-167,709-744`
- Test: `tests/unit/agentserver/test_deepresearch_stream_tool.py:320-445`

- [ ] **Step 1: Keep the existing failing initial-delivery tests**

The tests must assert that only `raw_report_path` and `citations_preview_path` survive, `citations_path` stays hidden, blank values yield no bundle, and the completed `chat.file` contains:

```python
{
    "metadata": {
        "artifactBundle": {
            "schemaVersion": "1.0",
            "relatedArtifacts": [
                {
                    "type": "raw_report",
                    "path": "/skill/data/C1.raw_report.md",
                    "contentType": "text/markdown",
                    "relatedToPathIndex": 0,
                },
                {
                    "type": "citations_preview",
                    "path": "/skill/data/C1.citations.preview.json",
                    "contentType": "application/json",
                    "schemaVersion": "1.1",
                    "relatedToPathIndex": 0,
                },
            ],
        }
    }
}
```

- [ ] **Step 2: Verify RED**

Run:

```bash
/Users/hualinge/vscodeproject/jiuwenclaw/.venv/bin/python -m pytest -o addopts='' \
  tests/unit/agentserver/test_deepresearch_stream_tool.py -q \
  -k 'related_artifact_bundle or completed_report_is_delivered'
```

Expected: three failures because `_build_related_artifact_bundle` is missing and `chat.file` has no metadata.

- [ ] **Step 3: Implement path normalization and bundle creation**

Add focused helpers:

```python
def _citation_artifacts(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        key: item.strip()
        for key in ("raw_report_path", "citations_preview_path")
        if isinstance((item := value.get(key)), str) and item.strip()
    }


def _build_related_artifact_bundle(value: object, markdown_index: int) -> dict | None:
    artifacts = _citation_artifacts(value)
    rows = []
    # append raw_report and citations_preview rows in stable order
    return {"schemaVersion": "1.0", "relatedArtifacts": rows} if rows else None
```

Extend `_write_report_markdown` and `_write_report_artifacts_stream` with an optional normalized `citation_artifacts` argument. Add it to initial provenance only when non-empty:

```python
if citation_artifacts:
    provenance["citation_artifacts"] = citation_artifacts
```

In the completed-marker path, normalize once, pass the result into report generation, determine the MD index from the ordered artifact keys, and add metadata only when the bundle is non-empty. Do not change visible MD+HTML files, task stage updates, outcome fields, or `citations.json` handling.

- [ ] **Step 4: Add provenance and MD+HTML assertions**

Extend report-writer tests to assert:

```python
assert provenance["citation_artifacts"] == {
    "raw_report_path": "/skill/data/C1.raw_report.md",
    "citations_preview_path": "/skill/data/C1.citations.preview.json",
}
assert "citations_path" not in provenance["citation_artifacts"]
```

Patch `_write_report_artifacts_stream` in the delivery test to return both MD and HTML and assert `relatedToPathIndex` points to the MD entry while both visible files remain present.

- [ ] **Step 5: Verify GREEN**

Run the Step 2 command, then:

```bash
/Users/hualinge/vscodeproject/jiuwenclaw/.venv/bin/python -m pytest -o addopts='' \
  tests/unit/agentserver/test_deepresearch_stream_tool.py -q
```

Expected: focused and complete stream-tool suites pass.

### Task 2: Rewritten child association inheritance

**Files:**
- Modify: `jiuwenclaw/agentserver/tools/deepresearch_rewrite_tools.py:1-20,286-304,351-353`
- Test: `tests/unit/agentserver/test_deepresearch_rewrite_tools.py:16-49,362-399`
- Test: `tests/unit/agentserver/test_deepresearch_document_rewrite.py`

- [ ] **Step 1: Write failing child-delivery tests**

Extend `_document()` with optional citation artifacts and assert a completed rewrite sends:

```python
payload = push.send_push.await_args.args[0]["payload"]
assert payload["metadata"]["artifactBundle"]["relatedArtifacts"][1] == {
    "type": "citations_preview",
    "path": "/skill/data/C1.citations.preview.json",
    "contentType": "application/json",
    "schemaVersion": "1.1",
    "relatedToPathIndex": 0,
}
```

Add parameterized `_deliver_report` cases for missing sidecar, oversized sidecar, invalid JSON, non-object JSON, and invalid `citation_artifacts`; each must still send the Markdown without metadata.

- [ ] **Step 2: Verify RED**

Run:

```bash
/Users/hualinge/vscodeproject/jiuwenclaw/.venv/bin/python -m pytest -o addopts='' \
  tests/unit/agentserver/test_deepresearch_rewrite_tools.py -q \
  -k 'citation_artifact or prepare_and_commit_tools_return_short_outcomes'
```

Expected: failures because `_deliver_report` neither reads provenance nor sends metadata.

- [ ] **Step 3: Implement bounded provenance loading and child delivery**

Import `_build_related_artifact_bundle` from `deepresearch_tools`, define a small maximum sidecar size, and add a fail-closed loader:

```python
def _load_citation_artifacts(provenance_path: str) -> dict[str, str]:
    path = Path(provenance_path)
    with path.open("rb") as stream:
        payload = stream.read(PROVENANCE_METADATA_MAX_BYTES + 1)
    if len(payload) > PROVENANCE_METADATA_MAX_BYTES:
        return {}
    value = json.loads(payload)
    if not isinstance(value, dict):
        return {}
    artifacts = value.get("citation_artifacts")
    return artifacts if isinstance(artifacts, dict) else {}
```

Catch read/decode/JSON errors and return `{}`. Change delivery to accept `provenance_path`, build the optional bundle, and preserve the legacy payload when no valid rows survive:

```python
async def _deliver_report(report_path: str, provenance_path: str, route: dict[str, object]) -> bool:
    payload = {
        "event_type": "chat.file",
        "files": [{"path": report_path, "name": os.path.basename(report_path)}],
    }
    bundle = _build_related_artifact_bundle(
        _load_citation_artifacts(provenance_path), 0
    )
    if bundle:
        payload["metadata"] = {"artifactBundle": bundle}
    # send existing gateway envelope
```

Call it with `result["provenance_path"]`. Do not expose hidden paths in the tool outcome.

- [ ] **Step 4: Prove provenance inheritance**

Add a core rewrite test whose parent provenance contains `citation_artifacts`; after commit, assert the child provenance contains exactly the same object. No production change should be needed because child provenance already starts from `dict(provenance)`.

- [ ] **Step 5: Verify GREEN**

Run:

```bash
/Users/hualinge/vscodeproject/jiuwenclaw/.venv/bin/python -m pytest -o addopts='' \
  tests/unit/agentserver/test_deepresearch_rewrite_tools.py \
  tests/unit/agentserver/test_deepresearch_document_rewrite.py -q
```

Expected: all tests pass.

### Task 3: Regression verification, commit, integration, and push

**Files:**
- Modify only the two production files, their focused tests, this plan, and the approved design document.

- [ ] **Step 1: Run feature regression suites**

```bash
/Users/hualinge/vscodeproject/jiuwenclaw/.venv/bin/python -m pytest -o addopts='' \
  tests/unit/agentserver/test_deepresearch_stream_tool.py \
  tests/unit/agentserver/test_deepresearch_rewrite_tools.py \
  tests/unit/agentserver/test_deepresearch_document_rewrite.py \
  tests/unit/agentserver/test_markdown_rewrite_map.py -q
```

Expected: all tests pass with zero failures.

- [ ] **Step 2: Check the patch**

```bash
git diff --check
git status --short
git diff --stat
```

Expected: no whitespace errors and only in-scope files changed.

- [ ] **Step 3: Commit the implementation**

```bash
git add \
  jiuwenclaw/agentserver/tools/deepresearch_tools.py \
  jiuwenclaw/agentserver/tools/deepresearch_rewrite_tools.py \
  tests/unit/agentserver/test_deepresearch_stream_tool.py \
  tests/unit/agentserver/test_deepresearch_rewrite_tools.py \
  tests/unit/agentserver/test_deepresearch_document_rewrite.py \
  docs/superpowers/plans/2026-07-22-deepresearch-citation-artifact-inheritance.md
git commit -m "fix(deepresearch): preserve citation preview associations"
```

- [ ] **Step 4: Push the feature branch**

```bash
git push origin codex/deepresearch-document-rewrite
```

Expected: non-force push succeeds.

- [ ] **Step 5: Fast-forward `enterprise_dev` and verify**

Because the feature branch was fast-forwarded from `enterprise_dev` before implementation:

```bash
git -C /Users/hualinge/vscodeproject/jiuwenclaw merge --ff-only codex/deepresearch-document-rewrite
```

Then rerun the feature regression command from the main JiuwenClaw worktree.

- [ ] **Step 6: Push `enterprise_dev`**

```bash
git -C /Users/hualinge/vscodeproject/jiuwenclaw push origin enterprise_dev
```

Expected: non-force push succeeds and local/remote commit hashes match.
