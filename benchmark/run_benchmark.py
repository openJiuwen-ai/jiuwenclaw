#!/usr/bin/env python3
"""Benchmark Runner — one-click automated evaluation of the skill self-evolution system.

Usage:
    # 前提: Agent 服务已启动 (Gateway + AgentServer)，telemetry 已开启
    python benchmark/run_benchmark.py

    # 自定义参数
    python benchmark/run_benchmark.py --host 127.0.0.1 --port 18092 --timeout 300

    # 只跑对话生成 trace，不跑演进
    python benchmark/run_benchmark.py --skip-evolve

    # 只跑演进和评分（trace 已生成）
    python benchmark/run_benchmark.py --skip-prompts

    # 重置到演进前状态（清理 traces、evolutions、恢复 skill 原始文件）
    python benchmark/run_benchmark.py --reset

    # 指定工作目录（默认 ~/.jiuwenswarm/）
    python benchmark/run_benchmark.py --workspace ~/.jiuwenswarm/
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import time
import uuid
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("benchmark")

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 18092  # AgentServer direct
DEFAULT_TIMEOUT = 300  # 5 min per prompt
BENCHMARK_DIR = Path(__file__).resolve().parent
REPORT_DIR = BENCHMARK_DIR / "report"
SKILLS_SRC = BENCHMARK_DIR / "skills"
TEST_DATA_SRC = BENCHMARK_DIR / "test_data"


# ---------------------------------------------------------------------------
# 1. Setup — copy skills + test data, configure permissions
# ---------------------------------------------------------------------------

_config_backup: str | None = None


def setup_environment(workspace: Path) -> None:
    """Copy benchmark skills and test data into the agent workspace,
    and temporarily set tool permissions to auto-allow."""
    global _config_backup

    skills_dst = workspace / "agent" / "workspace" / "skills"
    test_data_dst = workspace / "agent" / "workspace" / "benchmark_test_data"

    skills_dst.mkdir(parents=True, exist_ok=True)
    test_data_dst.mkdir(parents=True, exist_ok=True)

    for skill_dir in sorted(SKILLS_SRC.iterdir()):
        if skill_dir.is_dir():
            dst = skills_dst / skill_dir.name
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(skill_dir, dst)
            log.info(f"  ✓ skill: {skill_dir.name}")

    for f in sorted(TEST_DATA_SRC.iterdir()):
        if f.is_file():
            shutil.copy2(f, test_data_dst / f.name)
    log.info(f"  ✓ test_data: {len(list(TEST_DATA_SRC.iterdir()))} files → {test_data_dst}")

    # Set bash/write/edit tools to auto-allow so agent can execute without prompts
    config_path = workspace / "config" / "config.yaml"
    if config_path.exists():
        _config_backup = config_path.read_text()
        modified = _config_backup
        for tool in ("bash", "write", "write_file", "edit_file", "search_replace",
                      "mcp_exec_command", "create_terminal"):
            modified = modified.replace(f"{tool}: ask", f"{tool}: allow")
        if modified != _config_backup:
            config_path.write_text(modified)
            log.info("  ✓ permissions: bash/write/edit → allow (backup saved)")


def teardown_environment(workspace: Path) -> None:
    """Restore original config after benchmark."""
    global _config_backup

    config_path = workspace / "config" / "config.yaml"
    if _config_backup is not None and config_path.exists():
        config_path.write_text(_config_backup)
        _config_backup = None
        log.info("  ✓ permissions: restored original config")


# ---------------------------------------------------------------------------
# 2. Load test cases
# ---------------------------------------------------------------------------

def load_test_cases() -> list[dict]:
    """Load all benchmark_case.json files, sorted by category_id then skill_id."""
    cases = []
    for case_file in sorted(SKILLS_SRC.glob("*/benchmark_case.json")):
        with open(case_file) as f:
            case = json.load(f)
        case["_dir"] = str(case_file.parent)
        cases.append(case)

    cases.sort(key=lambda c: (c.get("category_id", 99), c["skill_id"]))
    return cases


# ---------------------------------------------------------------------------
# 3. WebSocket client — send prompts, collect responses
# ---------------------------------------------------------------------------

async def send_prompt(
    host: str, port: int, prompt: str, session_id: str, timeout: int
) -> str:
    """Send a single prompt to AgentServer and return the response text.

    Note: response capture is best-effort for display purposes.
    The actual trace data (for the evolution pipeline) is stored in traces.db
    by the OTEL instrumentors regardless of what we capture here.
    """
    import websockets

    uri = f"ws://{host}:{port}"
    request_id = f"bench-{uuid.uuid4().hex[:8]}"

    message = {
        "protocol_version": "1.0",
        "request_id": request_id,
        "channel": "web",
        "session_id": session_id,
        "method": "chat.send",
        "params": {
            "query": prompt,
            "mode": "agent.plan",
        },
        "is_stream": True,
    }

    async with websockets.connect(uri, max_size=10 * 1024 * 1024) as ws:
        # Wait for connection.ack
        try:
            ack_raw = await asyncio.wait_for(ws.recv(), timeout=10)
            ack = json.loads(ack_raw)
            if ack.get("event") != "connection.ack":
                log.debug(f"  Unexpected first frame: {ack_raw[:200]}")
        except asyncio.TimeoutError:
            return "[ERROR] Connection ack timeout"

        await ws.send(json.dumps(message))

        full_text: list[str] = []
        deadline = time.time() + timeout

        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                full_text.append("\n[TIMEOUT]")
                break

            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=min(remaining, 60))
            except asyncio.TimeoutError:
                full_text.append("\n[TIMEOUT waiting for next frame]")
                break

            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                full_text.append(raw)
                continue

            inner = data.get("body", {}).get("delta", {})

            # Auto-approve permission interrupts (safety net alongside config change)
            if (isinstance(inner, dict)
                    and inner.get("event_type") == "chat.ask_user_question"
                    and inner.get("source") == "permission_interrupt"):
                approval = {
                    "protocol_version": "1.0",
                    "request_id": request_id,
                    "channel": "web",
                    "session_id": session_id,
                    "method": "chat.user_answer",
                    "params": {
                        "answer": "always_allow",
                        "question_request_id": inner.get("request_id", ""),
                    },
                    "is_stream": False,
                }
                try:
                    await ws.send(json.dumps(approval))
                except Exception:
                    pass
                continue

            # Extract text from chat.text events
            text = _extract_text_from_frame(data)
            if text:
                full_text.append(text)

            # Handle final frame — but only trust it if we already captured
            # some response text.  The gateway may send an intermediate
            # chat.final when the agent finishes one LLM turn (e.g. deciding
            # to call a tool) before the agent loop is truly complete.
            # If we haven't captured any text yet, keep waiting.
            is_final = (
                data.get("is_final", False)
                or data.get("event") == "chat.final"
                or (data.get("body", {}).get("event_type") == "chat.final")
            )
            if is_final:
                # Try to extract final answer from body.result
                result = data.get("body", {}).get("result", {})
                result_text = ""
                if isinstance(result, dict):
                    for key in ("text", "content", "message", "summary"):
                        val = result.get(key, "")
                        if isinstance(val, str) and val:
                            result_text = val
                            break
                elif isinstance(result, str) and result:
                    result_text = result

                if result_text:
                    full_text.append(result_text)

                # Only break if we captured meaningful text
                combined = "".join(full_text).strip()
                if combined:
                    break
                # Otherwise keep waiting for the real final response

    return "".join(full_text)


def _extract_text_from_frame(data: dict) -> str:
    """Extract agent response text from an E2A WebSocket frame.

    Known E2A frame structure:
    - body.delta is a dict with event_type and content
    - chat.text = agent's response text (primary source)
    - chat.reasoning = internal thinking (not captured)
    """
    body = data.get("body", {})
    if not isinstance(body, dict):
        return ""

    inner = body.get("delta", {})
    if isinstance(inner, dict):
        event_type = inner.get("event_type", "")
        if event_type in ("chat.text", "chat.response"):
            content = inner.get("content", "")
            if isinstance(content, str) and content:
                return content

    # Fallback: body.delta_kind == "text" with string delta
    if body.get("delta_kind") == "text":
        delta = body.get("delta", "")
        if isinstance(delta, str) and delta:
            return delta

    return ""


async def run_all_prompts(
    cases: list[dict], host: str, port: int, timeout: int
) -> dict[str, str]:
    """Send prompts for all cases, return {skill_id: response_text}."""
    responses: dict[str, str] = {}

    for i, case in enumerate(cases, 1):
        skill_id = case["skill_id"]
        prompt = case.get("test_task", "")
        session_id = f"bench_{skill_id}_{uuid.uuid4().hex[:6]}"

        log.info(f"  [{i:2d}/{len(cases)}] {skill_id}: sending prompt...")
        try:
            response = await send_prompt(host, port, prompt, session_id, timeout)
            responses[skill_id] = response
            preview = response[:120].replace("\n", " ")
            log.info(f"           ← {preview}...")
        except Exception as e:
            responses[skill_id] = f"[ERROR] {e}"
            log.error(f"           ← ERROR: {e}")

        if i < len(cases):
            await asyncio.sleep(2)

    return responses


# ---------------------------------------------------------------------------
# 4. Evolution pipeline
# ---------------------------------------------------------------------------

def run_evolution(n_traces: int = 20) -> bool:
    """Trigger the evolution pipeline via CLI."""
    cmd = ["jiuwenswarm-evolve", "run", "--latest", str(n_traces)]
    log.info(f"  Running: {' '.join(cmd)}")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.stdout:
            for line in result.stdout.strip().split("\n"):
                log.info(f"    {line}")
        if result.returncode != 0:
            log.error(f"  Evolution failed (exit {result.returncode})")
            if result.stderr:
                log.error(f"    {result.stderr[:500]}")
            return False
        return True
    except FileNotFoundError:
        log.error("  jiuwenswarm-evolve CLI not found. Is the package installed?")
        return False
    except subprocess.TimeoutExpired:
        log.error("  Evolution pipeline timed out (600s)")
        return False


# ---------------------------------------------------------------------------
# 5. Read evolution results
# ---------------------------------------------------------------------------

def read_proposals_from_db(db_path: Path) -> list[dict]:
    """Read all proposals from evolution.db."""
    if not db_path.exists():
        log.warning(f"  evolution.db not found at {db_path}")
        return []

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    try:
        rows = conn.execute(
            "SELECT proposal_id, target_type, target_id, proposal_type, "
            "root_cause, targeted_fix, predicted_impact, state, batch_id, metadata "
            "FROM proposals ORDER BY created_at DESC"
        ).fetchall()
    except sqlite3.OperationalError:
        conn.close()
        return []

    proposals = []
    for row in rows:
        p = dict(row)
        p["targeted_fix"] = json.loads(p["targeted_fix"]) if p["targeted_fix"] else {}
        p["metadata"] = json.loads(p["metadata"]) if p["metadata"] else {}
        proposals.append(p)

    conn.close()
    return proposals


def read_evolutions_for_skill(skills_dir: Path, skill_id: str) -> list[dict]:
    """Read evolutions.json entries for a skill."""
    evo_path = skills_dir / skill_id / "evolutions.json"
    if not evo_path.exists():
        return []
    with open(evo_path) as f:
        data = json.load(f)
    return data.get("entries", [])


# ---------------------------------------------------------------------------
# 6. Scoring
# ---------------------------------------------------------------------------

def check_keywords(text: str, keywords: list[str]) -> bool:
    """Case-insensitive substring match against any keyword."""
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in keywords)


def score_case(case: dict, proposals: list[dict], evolutions: list[dict]) -> dict:
    """Score a single benchmark case."""
    skill_id = case["skill_id"]
    category = case["category"]
    expected = case.get("expected_behavior", {})

    skill_proposals = [
        p for p in proposals
        if p.get("target_id") == skill_id and p.get("target_type") == "skill"
    ]

    result = {
        "skill_id": skill_id,
        "category": category,
        "proposal_count": len(skill_proposals),
        "scores": {},
        "total": 0.0,
        "details": "",
    }

    if category == "fixable_error":
        return _score_fixable(result, skill_proposals, expected)
    elif category == "normal":
        return _score_normal(result, skill_proposals)
    elif category == "bad_experience":
        return _score_bad_experience(result, skill_proposals, expected, case)
    elif category == "unfixable":
        return _score_unfixable(result, skill_proposals)
    return result


def _score_fixable(result: dict, proposals: list[dict], expected: dict) -> dict:
    rc_keywords = expected.get("root_cause_keywords", [])
    fix_keywords = expected.get("fix_keywords", [])

    result["scores"]["proposal"] = 0.30 if len(proposals) >= 1 else 0.0

    if proposals:
        p = proposals[0]
        root_cause = p.get("root_cause", "")
        suggestion = p.get("targeted_fix", {}).get("suggestion", "")
        state = p.get("state", "")

        rc_match = check_keywords(root_cause, rc_keywords)
        fix_match = check_keywords(suggestion, fix_keywords)

        result["scores"]["root_cause"] = 0.25 if rc_match else 0.0
        result["scores"]["fix"] = 0.25 if fix_match else 0.0
        result["scores"]["decision"] = 0.20 if state == "active" else 0.0
        result["details"] = (
            f"state={state}, rc_match={rc_match}, fix_match={fix_match}, "
            f"root_cause='{root_cause[:80]}...'"
        )
    else:
        result["details"] = "No proposals generated"

    result["total"] = sum(result["scores"].values())
    return result


def _score_normal(result: dict, proposals: list[dict]) -> dict:
    passed = len(proposals) == 0
    result["scores"]["proposal"] = 1.0 if passed else 0.0
    result["total"] = 1.0 if passed else 0.0
    result["details"] = (
        "PASS: zero proposals" if passed
        else f"FAIL: {len(proposals)} proposal(s) generated (should be 0)"
    )
    return result


def _score_bad_experience(
    result: dict, proposals: list[dict], expected: dict, case: dict
) -> dict:
    rc_keywords = expected.get("root_cause_keywords", [])
    fix_keywords = expected.get("fix_keywords", [])

    pre_entries = case.get("pre_existing_evolutions", {})
    if isinstance(pre_entries, dict):
        pre_entries = [pre_entries]
    entry_ids = [e.get("entry_id", "") for e in pre_entries]

    governance_found = False
    for p in proposals:
        combined = p.get("root_cause", "") + " " + p.get("targeted_fix", {}).get("suggestion", "")
        if any(eid and eid in combined for eid in entry_ids):
            governance_found = True
        if check_keywords(combined, fix_keywords):
            governance_found = True

    result["scores"]["proposal"] = 0.30 if governance_found else 0.0

    if proposals:
        p = proposals[0]
        root_cause = p.get("root_cause", "")
        suggestion = p.get("targeted_fix", {}).get("suggestion", "")
        state = p.get("state", "")

        result["scores"]["root_cause"] = 0.25 if check_keywords(root_cause, rc_keywords) else 0.0
        result["scores"]["fix"] = 0.25 if check_keywords(suggestion, fix_keywords) else 0.0
        result["scores"]["decision"] = 0.20 if state == "active" else 0.0
        result["details"] = (
            f"state={state}, governance={governance_found}, "
            f"rc_match={check_keywords(root_cause, rc_keywords)}, "
            f"fix_match={check_keywords(suggestion, fix_keywords)}"
        )
    else:
        result["details"] = f"No proposals (governance_found={governance_found})"

    result["total"] = sum(result["scores"].values())
    return result


def _score_unfixable(result: dict, proposals: list[dict]) -> dict:
    if not proposals:
        passed = True
        detail = "PASS: zero proposals"
    else:
        all_rejected = all(p.get("state") == "rejected" for p in proposals)
        passed = all_rejected
        states = [p.get("state") for p in proposals]
        detail = (
            f"PASS: all proposals REJECTED ({states})"
            if passed
            else f"FAIL: proposal states={states} (should be rejected or empty)"
        )

    result["scores"]["proposal"] = 1.0 if passed else 0.0
    result["total"] = 1.0 if passed else 0.0
    result["details"] = detail
    return result


# ---------------------------------------------------------------------------
# 7. Report
# ---------------------------------------------------------------------------

def print_report(results: list[dict]) -> None:
    """Print the benchmark score report."""
    print("\n" + "=" * 80)
    print("  BENCHMARK RESULTS")
    print("=" * 80)

    header = f"{'#':>2}  {'Skill':<26} {'Category':<10} {'Prop':>5} {'RC':>5} {'Fix':>5} {'Dec':>5} {'Score':>6}"
    print(header)
    print("-" * 80)

    totals: list[float] = []
    category_scores: dict[str, list[float]] = {}

    for i, r in enumerate(results, 1):
        cat = r["category"]
        category_scores.setdefault(cat, []).append(r["total"])

        if cat in ("normal", "unfixable"):
            s = r["scores"].get("proposal", 0)
            line = f"{i:>2}  {r['skill_id']:<26} {cat:<10} {s:>5.2f} {'—':>5} {'—':>5} {'—':>5} {r['total']:>6.2f}"
        else:
            s = r["scores"]
            line = (
                f"{i:>2}  {r['skill_id']:<26} {cat:<10} "
                f"{s.get('proposal', 0):>5.2f} {s.get('root_cause', 0):>5.2f} "
                f"{s.get('fix', 0):>5.2f} {s.get('decision', 0):>5.2f} "
                f"{r['total']:>6.2f}"
            )
        print(line)
        totals.append(r["total"])

    print("-" * 80)
    print(f"{'':>2}  {'TOTAL':<26} {'':>10} {'':>5} {'':>5} {'':>5} {'':>5} {sum(totals):>6.2f} / 10.00")

    print("\n" + "=" * 80)
    print("  CAPABILITY SUMMARY")
    print("=" * 80)

    cap_map = {
        "fixable_error": "基本修复能力",
        "normal": "过度优化抑制",
        "bad_experience": "经验污染治理",
        "unfixable": "边界判断能力",
    }

    for cat, name in cap_map.items():
        scores = category_scores.get(cat, [])
        if scores:
            avg = sum(scores) / len(scores)
            grade = "A" if avg >= 0.9 else "B" if avg >= 0.7 else "C" if avg >= 0.5 else "D"
            print(f"  {name:<16} {avg:.2f}  [{grade}]")
        else:
            print(f"  {name:<16}  N/A")

    overall = sum(totals) / len(totals) if totals else 0
    grade = "A" if overall >= 0.9 else "B" if overall >= 0.7 else "C" if overall >= 0.5 else "D"
    print(f"\n  {'Overall':<16} {overall:.2f}  [{grade}]")
    print("=" * 80)

    print("\n  DETAILS:")
    for i, r in enumerate(results, 1):
        print(f"  [{i:>2}] {r['skill_id']}: {r['details']}")
    print()


def generate_detailed_report(
    cases: list[dict],
    responses: dict[str, str] | None,
    results: list[dict],
    skills_dir: Path,
    include_evolutions: bool,
) -> str:
    """Generate a markdown report with prompts, responses, and evolution details."""
    from datetime import datetime

    lines: list[str] = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines.append(f"# Benchmark Report — {now}\n")

    # Summary table
    lines.append("## Summary\n")
    lines.append("| # | Skill | Category | Score | Details |")
    lines.append("|---|-------|----------|-------|---------|")
    for i, r in enumerate(results, 1):
        lines.append(
            f"| {i} | {r['skill_id']} | {r['category']} "
            f"| {r['total']:.2f} | {r['details'][:80]} |"
        )
    total = sum(r["total"] for r in results)
    lines.append(f"\n**Total: {total:.2f} / {len(results) * 1.0:.2f}**\n")

    # Per-case details
    lines.append("---\n")
    lines.append("## Case Details\n")

    for i, case in enumerate(cases, 1):
        skill_id = case["skill_id"]
        category = case["category"]
        result = results[i - 1] if i - 1 < len(results) else {}

        lines.append(f"### {i}. {skill_id} ({category})\n")

        # User prompt
        prompt = case.get("_prompt") or case.get("test_task", "")
        lines.append(f"**User Prompt:**\n> {prompt}\n")

        # Agent response
        if responses and skill_id in responses:
            resp = responses[skill_id]
            lines.append(f"**Agent Response:**\n```\n{resp}\n```\n")
        else:
            lines.append("**Agent Response:** _(no response recorded)_\n")

        # Scoring
        lines.append(f"**Score: {result.get('total', 0):.2f}** — {result.get('details', '')}\n")

        # Evolution experiences
        if include_evolutions:
            evolutions = read_evolutions_for_skill(skills_dir, skill_id)
            if evolutions:
                lines.append(f"**Evolutions ({len(evolutions)} entries):**\n")
                for j, entry in enumerate(evolutions, 1):
                    change = entry.get("change", {})
                    lines.append(
                        f"{j}. `[{change.get('target', '?')}]` "
                        f"**{entry.get('summary', entry.get('id', ''))}** "
                        f"(score={entry.get('score', '?')}, "
                        f"applied={entry.get('applied', False)})\n"
                        f"   > {change.get('content', '')[:200]}\n"
                    )
            else:
                lines.append("**Evolutions:** _(none)_\n")

        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 8. Main
# ---------------------------------------------------------------------------

async def main_async(args: argparse.Namespace) -> None:
    workspace = Path(os.path.expanduser(args.workspace))
    test_data_path = str(workspace / "agent" / "workspace" / "benchmark_test_data")
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    log.info("=== Step 0: Setup ===")
    setup_environment(workspace)

    try:
        # Load test cases
        log.info("\n=== Step 1: Load test cases ===")
        cases = load_test_cases()
        log.info(f"  Loaded {len(cases)} cases")

        # Substitute test_data/ with absolute path in prompts
        for c in cases:
            task = c.get("test_task", "")
            task = task.replace("test_data/", f"{test_data_path}/")
            c["_prompt"] = task
            log.info(f"    [{c['category']}] {c['skill_id']}: {task[:70]}...")

        # Send prompts (generate traces)
        if not args.skip_prompts:
            log.info(f"\n=== Step 2: Send prompts (host={args.host}:{args.port}) ===")

            original_tasks = {c["skill_id"]: c.get("test_task", "") for c in cases}
            for c in cases:
                c["test_task"] = c["_prompt"]

            responses = await run_all_prompts(cases, args.host, args.port, args.timeout)

            for c in cases:
                c["test_task"] = original_tasks[c["skill_id"]]

            # Save responses with timestamp + latest copy
            ts = time.strftime("%Y%m%d_%H%M%S")
            resp_ts_file = REPORT_DIR / f"responses_{ts}.json"
            resp_latest = REPORT_DIR / "last_responses.json"
            for path in (resp_ts_file, resp_latest):
                with open(path, "w") as f:
                    json.dump(responses, f, ensure_ascii=False, indent=2)
            log.info(f"  Responses saved to {resp_ts_file.name}")
        else:
            log.info("\n=== Step 2: Skipped (--skip-prompts) ===")
            responses = None

        # Run evolution
        if not args.skip_evolve:
            log.info("\n=== Step 3: Run evolution pipeline ===")
            ok = run_evolution(n_traces=20)
            if not ok:
                log.warning("  Evolution pipeline had issues, continuing with scoring...")
        else:
            log.info("\n=== Step 3: Skipped (--skip-evolve) ===")

        # Score
        log.info("\n=== Step 4: Score ===")
        db_path = Path(args.evolution_db) if args.evolution_db else workspace / "evolution.db"
        proposals = read_proposals_from_db(db_path)
        log.info(f"  Found {len(proposals)} proposals in {db_path}")

        skills_dir = workspace / "agent" / "workspace" / "skills"
        results: list[dict] = []

        for case in cases:
            evolutions = read_evolutions_for_skill(skills_dir, case["skill_id"])
            result = score_case(case, proposals, evolutions)
            results.append(result)

        print_report(results)

        # Generate reports with timestamp + latest copies
        ts = time.strftime("%Y%m%d_%H%M%S")

        report_json = REPORT_DIR / f"report_{ts}.json"
        report_json_latest = REPORT_DIR / "last_report.json"
        for path in (report_json, report_json_latest):
            with open(path, "w") as f:
                json.dump(results, f, ensure_ascii=False, indent=2)

        detailed_md = generate_detailed_report(
            cases=cases,
            responses=responses,
            results=results,
            skills_dir=skills_dir,
            include_evolutions=not args.skip_evolve,
        )
        report_md = REPORT_DIR / f"report_{ts}.md"
        report_md_latest = REPORT_DIR / "last_report.md"
        for path in (report_md, report_md_latest):
            path.write_text(detailed_md, encoding="utf-8")

        log.info(f"Reports saved: report_{ts}.json, report_{ts}.md")

    finally:
        log.info("\n=== Teardown ===")
        teardown_environment(workspace)


def reset_benchmark_state(workspace: Path) -> None:
    """Reset benchmark state to pre-evolution state.

    Clears traces.db, evolution.db, and evolutions.json / evolution/*.md
    for all benchmark skills EXCEPT currency-converter and json-validator
    (which have pre-existing evolution data).
    """
    import glob as _glob

    workspace = workspace.expanduser().resolve()
    skills_dir = workspace / "agent" / "workspace" / "skills"

    # 1. Clear traces.db (delete so agent recreates with proper schema)
    traces_db = workspace / "traces.db"
    if traces_db.exists():
        traces_db.unlink()
        log.info("  ✓ deleted traces.db (agent will recreate on next request)")

    # 2. Clear evolution.db (truncate tables, keep schema)
    evo_db = workspace / "evolution.db"
    if evo_db.exists():
        conn = sqlite3.connect(str(evo_db))
        for table in ("proposals", "decision_results", "apply_records",
                       "trace_batches", "training_candidates"):
            try:
                conn.execute(f"DELETE FROM {table}")
            except sqlite3.OperationalError:
                pass
        conn.commit()
        conn.close()
        log.info("  ✓ cleared evolution.db (all tables emptied)")

    # 3. Restore benchmark skills from source (covers evolutions.json,
    #    evolution/, and SKILL.md evolution-index blocks in one shot).
    #    Pre-existing evolution skills (currency-converter, json-validator)
    #    are restored to their original benchmark state as well.
    skills_src = BENCHMARK_DIR / "skills"
    restored = []
    for skill_src in sorted(skills_src.iterdir()):
        if not skill_src.is_dir():
            continue
        name = skill_src.name
        skill_dst = skills_dir / name

        # Remove existing skill dir, then copy fresh from benchmark source
        if skill_dst.exists():
            shutil.rmtree(skill_dst)
        shutil.copytree(skill_src, skill_dst)
        restored.append(name)

    if restored:
        log.info(f"  ✓ restored {len(restored)} skills from benchmark source: {', '.join(restored)}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="JiuwenSwarm Skill Self-Evolution Benchmark Runner",
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="AgentServer host")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="AgentServer port")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="Timeout per prompt (seconds)")
    parser.add_argument("--workspace", default="~/.jiuwenswarm/", help="Agent workspace directory")
    parser.add_argument("--evolution-db", default=None,
                        help="Path to evolution.db (default: <workspace>/evolution.db)")
    parser.add_argument("--skip-prompts", action="store_true", help="Skip sending prompts (traces already exist)")
    parser.add_argument("--skip-evolve", action="store_true", help="Skip evolution pipeline")
    parser.add_argument("--reset", action="store_true",
                        help="Reset benchmark state (clear traces, evolutions) and exit")

    args = parser.parse_args()

    if args.reset:
        workspace = Path(os.path.expanduser(args.workspace))
        log.info("=== Resetting benchmark state ===")
        reset_benchmark_state(workspace)
        log.info("Done. Restart agent to pick up fresh traces.db.")
        return

    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
