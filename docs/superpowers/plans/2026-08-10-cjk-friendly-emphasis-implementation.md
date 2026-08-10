# CJK-Friendly Emphasis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make JiuwenClaw map CJK-adjacent Markdown emphasis exactly like the OfficeClaw preview, including `超**20℃**昼夜温差。`, without weakening selection integrity checks.

**Architecture:** Replace only the `emphasis` tokenizer on the `MarkdownIt` instance created inside `build_rewrite_map()`. Reuse markdown-it's delimiter scan and post-processing, overriding only `*` opener/closer flags at the two CJK-friendly punctuation/symbol boundaries; keep `_`, byte ranges, hashes, protected anchors, and all global parser state unchanged.

**Tech Stack:** Python 3.12, markdown-it-py 4.x, pytest, `unicodedata` from the standard library.

---

### Task 1: Lock the exact mapping contract with failing tests

**Files:**
- Modify: `docs/superpowers/specs/2026-08-10-cjk-bold-quote-selection-design.md`
- Modify: `tests/unit/agentserver/test_markdown_rewrite_map.py`

- [ ] **Step 1: Add the production sample to the written test contract**

Add `超**20℃**昼夜温差。` to the mapping test examples so the design explicitly covers the incident that prompted the fix.

- [ ] **Step 2: Add the failing mapper tests**

```python
@pytest.mark.parametrize(
    ("markdown", "strong_text"),
    [
        ("这是“**重点**”内容", "重点"),
        ("这是**“重点**”内容", "“重点"),
        ("这是“**重点”**内容", "重点”"),
        ("这是**“重点”**内容", "“重点”"),
        ("每周**≤2次**。", "≤2次"),
        ("增长**+6.9%**左右", "+6.9%"),
        ("超**20℃**昼夜温差。", "20℃"),
    ],
)
def test_cjk_adjacent_strong_uses_preview_parser_boundaries(markdown, strong_text):
    rewrite_map = rewrite_map_module.build_rewrite_map(markdown)

    assert rewrite_map.unsupported_regions == ()
    strong_slots = [
        slot
        for unit in rewrite_map.units
        for slot in unit.slots
        if slot.formats == ("strong",)
    ]
    assert [slot.text for slot in strong_slots] == [strong_text]
    assert [anchor.source for anchor in rewrite_map.units[0].protected] == ["**", "**"]


def test_non_cjk_symbol_boundary_remains_literal():
    rewrite_map = rewrite_map_module.build_rewrite_map("x**≤**y")

    assert rewrite_map.unsupported_regions == ()
    assert [slot.text for slot in rewrite_map.units[0].slots] == ["x**≤**y"]
    assert rewrite_map.units[0].protected == ()
```

Update the existing balanced-marker parameter for `每周**≤2次**。` from literal to formatted so the old behavior is explicitly retired, while keeping `x**≤**y` literal.

- [ ] **Step 3: Run the mapper tests and verify RED**

Run:

```bash
/Users/hualinge/vscodeproject/jiuwenclaw/.venv/bin/python -m pytest -o addopts='' \
  tests/unit/agentserver/test_markdown_rewrite_map.py \
  -k 'cjk_adjacent_strong or balanced_literal_format_markers' -q
```

Expected: FAIL because current markdown-it emits literal `**` instead of a `strong` slot for at least the CJK punctuation/symbol cases.

### Task 2: Lock the prepare-chain contract with a failing test

**Files:**
- Modify: `tests/unit/agentserver/test_deepresearch_document_rewrite.py`

- [ ] **Step 1: Add the real prepare request regression**

```python
def test_prepare_accepts_cjk_adjacent_temperature_strong_selection(tmp_path):
    body = "应对超**20℃**昼夜温差。\n"
    report, _ = _write_document(tmp_path, body)

    prepared = _prepare(
        tmp_path,
        report,
        body.rstrip("\n"),
        visible="应对超20℃昼夜温差。",
        action="polish",
    )

    slots = prepared["units"][0]["slots"]
    assert "".join(slot["text"] for slot in slots) == "应对超20℃昼夜温差。"
    assert [slot["text"] for slot in slots if slot["format"] == ["strong"]] == ["20℃"]
    assert all("**" not in slot["text"] for slot in slots)
```

- [ ] **Step 2: Run the prepare test and verify RED**

Run:

```bash
/Users/hualinge/vscodeproject/jiuwenclaw/.venv/bin/python -m pytest -o addopts='' \
  tests/unit/agentserver/test_deepresearch_document_rewrite.py \
  -k 'prepare_accepts_cjk_adjacent_temperature' -q
```

Expected: FAIL with `SELECTION_MAPPING_CONFLICT: selected text does not match normalized Markdown visibility`.

### Task 3: Add a local CJK-friendly emphasis tokenizer

**Files:**
- Modify: `jiuwenclaw/agentserver/tools/deepresearch_plugin/markdown_rewrite_map.py`

- [ ] **Step 1: Import the standard-library classifier and markdown-it delimiter types**

```python
import unicodedata

from markdown_it.rules_inline.state_inline import Delimiter, StateInline
```

- [ ] **Step 2: Implement the minimal tokenizer override**

```python
def _is_unicode_punctuation_or_symbol(character: str) -> bool:
    return bool(character) and unicodedata.category(character)[0] in {"P", "S"}


def _is_cjk_character(character: str) -> bool:
    return (
        bool(character)
        and unicodedata.east_asian_width(character) in {"F", "H", "W"}
        and not _is_unicode_punctuation_or_symbol(character)
    )


def _tokenize_cjk_friendly_emphasis(state: StateInline, silent: bool) -> bool:
    start = state.pos
    marker = state.src[start]
    if silent or marker not in {"_", "*"}:
        return False

    scanned = state.scanDelims(start, marker == "*")
    before = state.src[start - 1] if start > 0 else ""
    after_index = start + scanned.length
    after = state.src[after_index] if after_index < state.posMax else ""
    can_open = scanned.can_open
    can_close = scanned.can_close
    if marker == "*":
        can_open = can_open or (
            _is_cjk_character(before)
            and _is_unicode_punctuation_or_symbol(after)
        )
        can_close = can_close or (
            _is_unicode_punctuation_or_symbol(before)
            and _is_cjk_character(after)
        )

    for _ in range(scanned.length):
        token = state.push("text", "", 0)
        token.content = marker
        state.delimiters.append(
            Delimiter(
                marker=ord(marker),
                length=scanned.length,
                token=len(state.tokens) - 1,
                end=-1,
                open=can_open,
                close=can_close,
            )
        )
    state.pos += scanned.length
    return True
```

- [ ] **Step 3: Register it only on the rewrite-map parser**

Immediately after constructing the parser in `build_rewrite_map()`:

```python
parser.inline.ruler.at("emphasis", _tokenize_cjk_friendly_emphasis)
```

Do not modify `build_document_anchor_index()` or global markdown-it state.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run both focused commands from Tasks 1 and 2. Expected: all selected tests pass.

### Task 4: Verify safety and regression scope

**Files:**
- Verify only; no expected production edits.

- [ ] **Step 1: Run both related test modules**

```bash
/Users/hualinge/vscodeproject/jiuwenclaw/.venv/bin/python -m pytest -o addopts='' \
  tests/unit/agentserver/test_markdown_rewrite_map.py \
  tests/unit/agentserver/test_deepresearch_document_rewrite.py -q
```

Expected: baseline `272` plus the new test cases pass with zero failures.

- [ ] **Step 2: Compile the modified Python modules**

```bash
/Users/hualinge/vscodeproject/jiuwenclaw/.venv/bin/python -m compileall -q \
  jiuwenclaw/agentserver/tools/deepresearch_plugin/markdown_rewrite_map.py \
  tests/unit/agentserver/test_markdown_rewrite_map.py \
  tests/unit/agentserver/test_deepresearch_document_rewrite.py
```

Expected: exit code 0 and no output.

- [ ] **Step 3: Inspect the final diff and scope**

Confirm that production changes are limited to `markdown_rewrite_map.py`, no configuration or persistent data changed, and the design/tests/plan are the only other files.

- [ ] **Step 4: Commit the implementation**

```bash
git add docs/superpowers/specs/2026-08-10-cjk-bold-quote-selection-design.md \
  docs/superpowers/plans/2026-08-10-cjk-friendly-emphasis-implementation.md \
  jiuwenclaw/agentserver/tools/deepresearch_plugin/markdown_rewrite_map.py \
  tests/unit/agentserver/test_markdown_rewrite_map.py \
  tests/unit/agentserver/test_deepresearch_document_rewrite.py
git commit -m "fix(deepresearch): align CJK emphasis rewrite mapping"
```

Do not merge into `enterprise_dev`, push, or restart the running service without separate authorization.
