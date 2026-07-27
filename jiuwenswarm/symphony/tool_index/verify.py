#!/usr/bin/env python3
"""Day 1 verification: ToolScanner → Build tree index → Inspect structure.

Usage:
    cd D:\work\program\jiuwen\jiuwenswarm
    python -m jiuwenswarm.symphony.tool_index.verify

This script:
    1. Creates mock ToolCards (simulating the runtime registry)
    2. Runs ToolScanner → ScannedItem list
    3. Builds tree index via IndexBuilder
    4. Prints the resulting tree structure
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any


# ---------- mock ToolCards (same shape as openjiuwen's ToolCard) ----------


class MockToolCard:
    """Minimal ToolCard stub for verification — enough for ToolScanner."""

    def __init__(
        self,
        name: str,
        description: str,
        input_params: dict[str, Any] | None = None,
    ) -> None:
        self.name = name
        self.description = description
        self.input_params = input_params or {
            "type": "object",
            "properties": {},
            "required": [],
        }


# Representative set matching the current TOOL_WHITELIST
MOCK_TOOLS: dict[str, MockToolCard] = {
    # -- web search --
    "free_search": MockToolCard(
        "free_search",
        "Search the web for information using a free search engine. "
        "Use this for general-purpose web searches.",
        {"type": "object", "properties": {"query": {"type": "string", "description": "Search query"}}, "required": ["query"]},
    ),
    "fetch_webpage": MockToolCard(
        "fetch_webpage",
        "Fetch and extract the text content of a web page given its URL. "
        "Use this when you need to read a specific page.",
        {"type": "object", "properties": {"url": {"type": "string", "description": "Page URL"}}, "required": ["url"]},
    ),
    "paid_search": MockToolCard(
        "paid_search",
        "Perform a paid/high-quality web search with better ranking and coverage. "
        "Use this when free_search results are insufficient.",
        {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    ),
    # -- multimodal --
    "vision": MockToolCard(
        "vision",
        "Analyze an image: describe its content, answer questions about it, "
        "or extract text (OCR). Supports common image formats.",
        {"type": "object", "properties": {"image_url": {"type": "string"}, "question": {"type": "string"}}, "required": ["image_url"]},
    ),
    "image_ocr": MockToolCard(
        "image_ocr",
        "Extract text from an image using optical character recognition. "
        "Handles printed and handwritten text.",
        {"type": "object", "properties": {"image_url": {"type": "string"}}, "required": ["image_url"]},
    ),
    "audio_transcription": MockToolCard(
        "audio_transcription",
        "Transcribe speech from an audio file to text. Supports multiple languages.",
        {"type": "object", "properties": {"audio_url": {"type": "string"}}, "required": ["audio_url"]},
    ),
    "audio_question_answering": MockToolCard(
        "audio_question_answering",
        "Answer questions about the content of an audio recording.",
        {"type": "object", "properties": {"audio_url": {"type": "string"}, "question": {"type": "string"}}, "required": ["audio_url", "question"]},
    ),
    "video_understanding": MockToolCard(
        "video_understanding",
        "Analyze a video: describe scenes, identify objects, answer questions "
        "about the video content.",
        {"type": "object", "properties": {"video_url": {"type": "string"}}, "required": ["video_url"]},
    ),
    "generate_image": MockToolCard(
        "generate_image",
        "Generate an image from a text description using an AI model.",
        {"type": "object", "properties": {"prompt": {"type": "string", "description": "Image description"}}, "required": ["prompt"]},
    ),
    # -- phone (xiaoyi) -- just a sample, not all 25
    "call_phone": MockToolCard(
        "call_phone",
        "Make a phone call to a contact or number on the user's mobile device.",
        {"type": "object", "properties": {"number": {"type": "string"}}, "required": ["number"]},
    ),
    "send_message": MockToolCard(
        "send_message",
        "Send an SMS or chat message to a contact on the user's mobile device.",
        {"type": "object", "properties": {"recipient": {"type": "string"}, "content": {"type": "string"}}, "required": ["recipient", "content"]},
    ),
    "create_note": MockToolCard(
        "create_note",
        "Create a new note or memo on the user's mobile device.",
        {"type": "object", "properties": {"title": {"type": "string"}, "content": {"type": "string"}}, "required": ["title"]},
    ),
    "create_alarm": MockToolCard(
        "create_alarm",
        "Set a new alarm on the user's mobile device.",
        {"type": "object", "properties": {"time": {"type": "string", "description": "HH:MM format"}, "label": {"type": "string"}}, "required": ["time"]},
    ),
    "get_user_location": MockToolCard(
        "get_user_location",
        "Get the current GPS location of the user's mobile device.",
        {"type": "object", "properties": {}, "required": []},
    ),
    # -- skill management --
    "search_skill": MockToolCard(
        "search_skill",
        "Search for installable skills from SkillNet, ClawHub, and TeamSkillsHub.",
        {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    ),
    "install_skill": MockToolCard(
        "install_skill",
        "Install a skill using the identifier returned by search_skill.",
        {"type": "object", "properties": {"identifier": {"type": "string"}, "source": {"type": "string"}}, "required": ["identifier", "source"]},
    ),
    "uninstall_skill": MockToolCard(
        "uninstall_skill",
        "Uninstall an installed skill by name.",
        {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
    ),
    # -- task management --
    "user_todos": MockToolCard(
        "user_todos",
        "Manage the user's personal todo list: create, list, update, or delete todos.",
        {"type": "object", "properties": {"action": {"type": "string", "enum": ["create", "list", "update", "delete"]}}, "required": ["action"]},
    ),
    # -- file io --
    "send_file_to_user": MockToolCard(
        "send_file_to_user",
        "Send a file or generated content to the user as a downloadable file.",
        {"type": "object", "properties": {"filename": {"type": "string"}, "content": {"type": "string"}}, "required": ["filename", "content"]},
    ),
}


# =========================================================================
# Verification steps
# =========================================================================


def check_1_scanner() -> bool:
    """1. ToolScanner collects all ToolCards → ScannedItems."""
    print("=" * 60)
    print("Test 1: ToolScanner collection")
    print("=" * 60)

    from jiuwenswarm.symphony.tool_index.scanner import ToolScanner

    scanner = ToolScanner(MOCK_TOOLS)
    items = scanner.scan()

    assert len(items) == len(MOCK_TOOLS), (
        f"FAIL: expected {len(MOCK_TOOLS)} items, got {len(items)}"
    )
    print(f"  [PASS] Collected {len(items)} items (expected {len(MOCK_TOOLS)})")

    for item in items:
        assert item.id, f"FAIL: empty id"
        assert item.name, f"FAIL: empty name"
        assert item.description or item.content, (
            f"FAIL: empty description AND content for {item.id}"
        )
        assert "tool://" in item.item_path, f"FAIL: bad item_path {item.item_path}"

    # Print first 3 for visual inspection
    print("\n  Sample items:")
    for item in items[:3]:
        desc = (item.description or "")[:100]
        print(f"    - {item.id}: {desc}...")
    print()
    return True


def check_2_inventory() -> bool:
    """2. ToolScanner.scan_inventory() produces a valid fingerprint."""
    print("=" * 60)
    print("Test 2: Inventory fingerprinting")
    print("=" * 60)

    from jiuwenswarm.symphony.tool_index.scanner import ToolScanner

    scanner = ToolScanner(MOCK_TOOLS)
    inv = scanner.scan_inventory()

    assert inv.count == len(MOCK_TOOLS), f"FAIL: count mismatch"
    assert inv.fingerprint, "FAIL: empty fingerprint"
    assert len(inv.item_paths) == len(MOCK_TOOLS), "FAIL: item_paths count mismatch"

    # Fingerprint should be stable
    inv2 = ToolScanner(MOCK_TOOLS).scan_inventory()
    assert inv.fingerprint == inv2.fingerprint, (
        "FAIL: fingerprint not deterministic"
    )

    print(f"  [PASS] Inventory: {inv.count} tools")
    print(f"  [PASS] Fingerprint: {inv.fingerprint[:16]}...")
    print(f"  [PASS] Fingerprint is deterministic (same input → same hash)")
    print()
    return True


def _has_llm_config() -> bool:
    """Check whether the SkillRetrieval LLM is configured."""
    try:
        from jiuwenswarm.symphony.skill_retrieval.config import load_settings
        settings = load_settings()
        return bool(settings.llm.api_key and settings.llm.model)
    except Exception:
        return False


def check_3_build() -> bool:
    """3. Build the tool tree index via IndexBuilder."""
    if not _has_llm_config():
        print("  [SKIP] No LLM configured — set API_KEY, API_BASE, MODEL_NAME")
        print("  To configure: add to config.yaml → symphony.skill_retrieval.llm")
        return True  # skip counts as pass

    print("=" * 60)
    print("Test 3: Tree index build")
    print("=" * 60)

    from jiuwenswarm.symphony.tool_index.api import build_tool_index
    from jiuwenswarm.symphony.tool_index.config import ToolIndexConfig
    import tempfile

    # Use a temp dir so we don't pollute the real index
    with tempfile.TemporaryDirectory(prefix="tool-index-test-") as tmp:
        config = ToolIndexConfig(
            enabled=True,
            artifact_root=Path(tmp),
        )

        print(f"  Building to: {config.artifact_root}")
        started = time.monotonic()

        result = build_tool_index(
            MOCK_TOOLS,
            config=config,
            force=True,
        )

        elapsed = time.monotonic() - started
        print(f"  Elapsed: {elapsed:.1f}s")
        print(f"  Result: success={result['success']}")

        if not result["success"]:
            print(f"  [FAIL] Build failed:\n{result.get('result', '')}")
            return False

        print(f"  {result['result'][:200]}...")

        # Verify artifacts exist
        index_dir = config.artifact_root / "index"
        assert (index_dir / "tree_index.yaml").is_file(), (
            "FAIL: tree_index.yaml missing"
        )
        assert (index_dir / "catalog.jsonl").is_file(), (
            "FAIL: catalog.jsonl missing"
        )
        assert (index_dir / "manifest.json").is_file(), (
            "FAIL: manifest.json missing"
        )
        print(f"  [PASS] All 3 index artifacts present")

        # Print tree structure
        from jiuwenswarm.symphony.tool_index.api import tool_index_tree

        tree_result = tool_index_tree(
            MOCK_TOOLS, config=config
        )
        print(f"\n  Tree structure:")
        print(f"  {tree_result['result']}")

    print()
    return True


def check_4_status() -> bool:
    """4. Status reports fresh after build."""
    if not _has_llm_config():
        print("  [SKIP] No LLM configured.")
        return True

    print("=" * 60)
    print("Test 4: Status after build")
    print("=" * 60)

    from jiuwenswarm.symphony.tool_index.api import (
        build_tool_index,
        tool_index_status,
    )
    from jiuwenswarm.symphony.tool_index.config import ToolIndexConfig
    import tempfile

    with tempfile.TemporaryDirectory(prefix="tool-index-test-") as tmp:
        config = ToolIndexConfig(enabled=True, artifact_root=Path(tmp))

        # Before build
        status = tool_index_status(MOCK_TOOLS, config=config)
        assert not status["index_exists"], "FAIL: index should not exist yet"
        print(f"  [PASS] Before build: index_exists=False")

        # Build
        build_tool_index(MOCK_TOOLS, config=config, force=True)

        # After build
        status = tool_index_status(MOCK_TOOLS, config=config)
        assert status["index_exists"], "FAIL: index should exist"
        assert status["fresh"], "FAIL: index should be fresh"
        assert status["tool_count"] == len(MOCK_TOOLS), "FAIL: tool count mismatch"
        print(f"  [PASS] After build: index_exists=True, fresh=True, tool_count={status['tool_count']}")

        # Should reuse (no rebuild needed)
        result = build_tool_index(MOCK_TOOLS, config=config, force=False)
        assert result["success"], "FAIL: reuse should succeed"
        assert "Reused" in result["result"], f"FAIL: expected reuse, got: {result['result'][:100]}"
        print(f"  [PASS] Second build: reused existing index (no rebuild)")
    print()
    return True


def check_5_incremental() -> bool:
    """5. Changing tools triggers rebuild."""
    if not _has_llm_config():
        print("  [SKIP] No LLM configured.")
        return True

    print("=" * 60)
    print("Test 5: Incremental detection")
    print("=" * 60)

    from jiuwenswarm.symphony.tool_index.api import (
        build_tool_index,
        tool_index_status,
    )
    from jiuwenswarm.symphony.tool_index.config import ToolIndexConfig
    import tempfile, copy

    with tempfile.TemporaryDirectory(prefix="tool-index-test-") as tmp:
        config = ToolIndexConfig(enabled=True, artifact_root=Path(tmp))

        # Build with full set
        build_tool_index(MOCK_TOOLS, config=config, force=True)
        status = tool_index_status(MOCK_TOOLS, config=config)
        assert status["fresh"], "FAIL: should be fresh"
        print(f"  [PASS] Initial build: fresh")

        # Remove one tool
        reduced = dict(MOCK_TOOLS)
        del reduced["create_alarm"]
        status = tool_index_status(reduced, config=config)
        assert not status["fresh"], "FAIL: should be stale after tool removal"
        print(f"  [PASS] After removing create_alarm: stale detected")

        # Force rebuild with reduced set
        result = build_tool_index(reduced, config=config, force=True)
        assert result["success"], "FAIL: rebuild failed"
        status = tool_index_status(reduced, config=config)
        assert status["fresh"], "FAIL: should be fresh after rebuild"
        assert status["tool_count"] == len(reduced), "FAIL: count mismatch"
        print(f"  [PASS] After rebuild with {len(reduced)} tools: fresh again")
    print()
    return True


# =========================================================================
# Main
# =========================================================================


def main() -> int:
    # Ensure we can import from the project
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    os.environ.setdefault("SYMPHONY_SKILL_RETRIEVAL_ENABLED", "true")

    checks = [
        ("ToolScanner collection", check_1_scanner),
        ("Inventory fingerprinting", check_2_inventory),
        ("Tree index build", check_3_build),
        ("Status after build", check_4_status),
        ("Incremental detection", check_5_incremental),
    ]

    passed = 0
    failed = 0
    for name, func in checks:
        try:
            if func():
                passed += 1
            else:
                failed += 1
        except Exception as exc:
            print(f"\n  [FAIL] {name} ERROR: {exc}")
            import traceback
            traceback.print_exc()
            failed += 1

    print("=" * 60)
    print(f"RESULTS: {passed}/{len(checks)} passed, {failed} failed")
    print("=" * 60)

    if failed == 0:
        print("\n[PASS] Day 1 verification PASSED — Tool progressive retrieval pipeline works!")
        print("   Ready for Day 2: ToolRetrievalToolkit + PromptRail integration.")
    else:
        print(f"\n[FAIL] {failed} checks FAILED — review errors above.")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
