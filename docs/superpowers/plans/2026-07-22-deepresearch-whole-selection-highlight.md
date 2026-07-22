# DeepResearch Whole-Selection Highlight Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist highlight ranges for every visible editable span in a rewritten selection whenever any selected slot changes.

**Architecture:** Keep the provenance schema and OfficeClaw renderer unchanged. JiuwenClaw will first detect whether the complete selected slot set produced any change, then map every non-empty replacement in that selection back to UTF-8 byte ranges through the existing Markdown rewrite map.

**Tech Stack:** Python 3.12, pytest, JiuwenClaw Markdown rewrite map

---

### Task 1: Lock the new selection-level semantics with failing tests

**Files:**
- Modify: `tests/unit/agentserver/test_deepresearch_document_rewrite.py:1431-1467`

- [ ] **Step 1: Change the multi-unit expectation to the whole selection**

Rename `test_commit_highlights_changed_slot_but_not_noop_slot` to `test_commit_highlights_whole_multi_unit_selection_when_one_slot_changes`. Keep the setup, then assert two ranges in document order: the unchanged `unchanged` paragraph and the rewritten `new text` paragraph.

```python
    expected = []
    for text in (b"unchanged", b"new text"):
        start = child_bytes.index(text)
        expected.append({
            "start_byte": start,
            "end_byte": start + len(text),
            "unit_type": "paragraph",
        })
    assert child_provenance["rewrite_highlights"]["ranges"] == expected
```

- [ ] **Step 2: Add a protected-content and formatting regression for every supported action**

Add a test parametrized over `shorten`, `expand`, and `polish`. Select `old **2024** [[1]](https://example.com/source) tail` from a larger paragraph, change only the `old ` slot, then concatenate the child bytes referenced by the persisted ranges.

```python
@pytest.mark.parametrize("action", ["shorten", "expand", "polish"])
def test_commit_highlights_whole_visible_selection_for_every_action(tmp_path, action):
    raw = "old **2024** [[1]](https://example.com/source) tail"
    body = f"prefix {raw} suffix\n"
    report, _ = _write_document(tmp_path, body)
    prepared = _prepare(
        tmp_path,
        report,
        raw,
        visible="old 2024 [1] tail",
        action=action,
    )
    changed_slot = next(
        slot["slot_id"]
        for unit in prepared["units"]
        for slot in unit["slots"]
        if slot["text"] == "old "
    )

    result = commit_rewrite(
        context_token=prepared["context_token"],
        session_id="S1",
        structured_result=_structured_payload(
            prepared, {changed_slot: "expanded "}
        ),
    )

    child_bytes = Path(result["report_path"]).read_bytes()
    provenance = json.loads(
        Path(result["provenance_path"]).read_text(encoding="utf-8")
    )
    highlighted = b"".join(
        child_bytes[item["start_byte"]:item["end_byte"]]
        for item in provenance["rewrite_highlights"]["ranges"]
    ).decode("utf-8")
    assert "expanded " in highlighted
    assert "2024" in highlighted
    assert " tail" in highlighted
    assert "prefix" not in highlighted
    assert "suffix" not in highlighted
    assert "**" not in highlighted
    assert "[1]" not in highlighted
    assert "https://example.com/source" not in highlighted
```

- [ ] **Step 3: Run the two tests and verify RED**

Run:

```bash
/Users/hualinge/vscodeproject/jiuwenclaw/.venv/bin/python -m pytest \
  tests/unit/agentserver/test_deepresearch_document_rewrite.py::test_commit_highlights_whole_multi_unit_selection_when_one_slot_changes \
  tests/unit/agentserver/test_deepresearch_document_rewrite.py::test_commit_highlights_whole_visible_selection_for_every_action -q
```

Expected: failures show that unchanged selected slots (`unchanged`, `2024`, and `tail`) are absent from the current highlight ranges.

### Task 2: Generate ranges for the entire visible selection

**Files:**
- Modify: `jiuwenclaw/agentserver/tools/deepresearch_plugin/document_rewrite.py:929-1003`
- Test: `tests/unit/agentserver/test_deepresearch_document_rewrite.py`

- [ ] **Step 1: Detect a rewrite result across the complete selection**

At the start of `_current_highlight_ranges`, after `selected_by_id`, scan selected slots against their original visible slices. Return an empty list if every replacement is identical.

```python
    has_rewrite_result = False
    for original_unit in original_map.units:
        selected_unit = selected_by_id.get(original_unit.unit_id)
        if selected_unit is None:
            continue
        selected_slots = {slot.slot_id: slot for slot in selected_unit.slots}
        for original_slot in original_unit.slots:
            selected_slot = selected_slots.get(original_slot.slot_id)
            if selected_slot is None:
                continue
            visible_start = original_slot.visible_boundary_to_byte.index(
                selected_slot.start_byte
            )
            visible_end = original_slot.visible_boundary_to_byte.index(
                selected_slot.end_byte
            )
            if (
                slot_texts[selected_slot.slot_id]
                != original_slot.text[visible_start:visible_end]
            ):
                has_rewrite_result = True
                break
        if has_rewrite_result:
            break
    if not has_rewrite_result:
        return []
```

- [ ] **Step 2: Map every non-empty selected replacement**

In the existing range-building pass, stop filtering by per-slot text equality. A non-empty replacement is visible selected content and must contribute a span once the global change gate is true.

```python
            if replacement:
                highlight_spans.append(
                    (highlight_start, highlight_start + len(replacement))
                )
```

Keep `visible_slot_byte_ranges`, range merging, `MAX_HIGHLIGHT_RANGES`, and protected-anchor handling unchanged.

- [ ] **Step 3: Run focused tests and verify GREEN**

Run the two-node command from Task 1. Expected: `4 passed` because the second test has three action parameters.

- [ ] **Step 4: Run the complete rewrite test file**

Run:

```bash
/Users/hualinge/vscodeproject/jiuwenclaw/.venv/bin/python -m pytest \
  tests/unit/agentserver/test_deepresearch_document_rewrite.py -q
```

Expected: all 165 collected cases pass (the 162-case baseline plus three parametrized cases, with one existing test renamed rather than added).

- [ ] **Step 5: Commit the behavior change**

```bash
git add \
  jiuwenclaw/agentserver/tools/deepresearch_plugin/document_rewrite.py \
  tests/unit/agentserver/test_deepresearch_document_rewrite.py
git commit -m "fix(deepresearch): highlight whole rewritten selection"
```

### Task 3: Verify the unchanged OfficeClaw rendering boundary

**Files:**
- Verify only: `/Users/hualinge/tmp/relay-claw-tmp/relay-claw_2766/.worktrees/main-runtime/packages/web/src/components/document-preview/__tests__/deepresearch-rewrite-render-plugin.test.ts`

- [ ] **Step 1: Run the existing renderer regression**

```bash
pnpm test -- \
  src/components/document-preview/__tests__/deepresearch-rewrite-render-plugin.test.ts
```

Run from `/Users/hualinge/tmp/relay-claw-tmp/relay-claw_2766/.worktrees/main-runtime/packages/web`.

Expected: the renderer suite passes, including the case that highlights visible bold/link text while excluding citation text and Markdown syntax.

- [ ] **Step 2: Perform repository hygiene checks**

Run from the JiuwenClaw feature worktree:

```bash
git diff --check enterprise_dev...HEAD
git status --short
git log -3 --oneline --decorate
```

Expected: no whitespace errors; only ignored test artifacts may exist; the branch contains the design, plan, and behavior commits.
