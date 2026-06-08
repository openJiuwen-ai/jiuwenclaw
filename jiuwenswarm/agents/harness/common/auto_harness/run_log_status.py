# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Structured run-log status helpers for scheduled auto-harness tasks."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

META_EVOLVE_STAGE_ORDER = [
    "assess",
    "plan",
    "implement",
    "verify",
    "commit",
    "publish",
    "learnings",
]

STAGE_DISPLAY_NAMES = {
    "assess": "评估",
    "plan": "规划",
    "implement": "实现",
    "verify": "验证",
    "commit": "提交",
    "publish": "发布 PR",
    "learnings": "经验总结",
    "build_verify": "构建验证",
    "activate": "激活",
}

_LEGACY_SKIP_STAGE_MARKERS = {
    "显式 GitCode issue 修复任务，跳过 assess/plan": ("assess", "plan"),
}


def infer_skipped_stages_from_message(content: str) -> tuple[str, ...]:
    """Normalize legacy skip messages into structured skipped stages."""
    return next(
        (
            stages for marker, stages in _LEGACY_SKIP_STAGE_MARKERS.items()
            if marker in content
        ),
        (),
    )


def classify_failure(error: str, last_message: str = "") -> str:
    """Return a stable failure code suitable for compact UI display."""
    text = f"{error}\n{last_message}".lower()
    if "missing required labels" in text:
        return "missing_required_labels"
    if (
        "no allowed files" in text
        or "no changes" in text
        or "did not create a new commit" in text
    ):
        return "no_effective_diff"
    if "git branch push failed" in text or "push failed" in text:
        return "push_rejected"
    if (
        "gitcode pr creation failed" in text
        or "http error 400" in text
        or "bad request" in text
    ):
        return "pr_api_failed"
    if "lint" in text or "type-check" in text or "ci" in text or "verify" in text:
        return "verify_failed"
    if "file must be read before editing" in text or "tool" in text:
        return "agent_tool_error"
    return "unknown_failure"


def extract_pr_url_from_text(text: str) -> str:
    """Find the first concrete GitCode PR/MR URL in a log message."""
    for url in re.findall(r"https://gitcode\.com/[^\s)>\"]+", text):
        if re.search(r"/(?:pulls|pull_requests|merge_requests)/\d+", url):
            return url
    return ""


def format_progress_summary(progress: dict[str, Any]) -> str:
    failed_stage = progress.get("failed_stage")
    current_stage = progress.get("current_stage")
    completed = progress.get("completed_stages") or []
    total = len(progress.get("stages") or [])
    if failed_stage:
        return f"失败于 {STAGE_DISPLAY_NAMES.get(failed_stage, failed_stage)}"
    if current_stage:
        current_stage_name = STAGE_DISPLAY_NAMES.get(current_stage, current_stage)
        return f"{len(completed)}/{total} 已完成，正在 {current_stage_name}"
    if total and len(completed) >= total:
        return f"{len(completed)}/{total} 已完成"
    if total:
        return f"{len(completed)}/{total} 已完成"
    return "暂无阶段日志"


def summarize_progress_from_logs(logs: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize stage progress from structured harness logs."""
    pipeline = ""
    stage_order: list[str] = []
    stage_status: dict[str, str] = {}
    stage_messages: dict[str, list[str]] = {}
    last_stage_message = ""
    last_message_stage = ""
    last_event_type = ""
    last_error = ""
    pr_url = ""

    for entry in logs:
        event_type = str(entry.get("event_type") or "")
        last_event_type = event_type or last_event_type
        if event_type == "harness.message":
            if entry.get("pipeline") and entry.get("stages"):
                pipeline = str(entry.get("pipeline") or pipeline)
                stage_order = [
                    str(stage.get("slot") or "")
                    for stage in entry.get("stages") or []
                    if stage.get("slot")
                ]
            stage = str(entry.get("stage") or "")
            content = str(entry.get("content") or "")
            for skipped_stage in infer_skipped_stages_from_message(content):
                stage_status.setdefault(skipped_stage, "skipped")
                if skipped_stage not in stage_order:
                    stage_order.append(skipped_stage)
            if content and not pr_url:
                pr_url = extract_pr_url_from_text(content)
            if stage:
                last_message_stage = stage
                if content:
                    last_stage_message = content
                    stage_messages.setdefault(stage, []).append(content)
        elif event_type == "harness.stage_result" and not entry.get("scope"):
            stage = str(entry.get("stage") or "")
            status = str(entry.get("status") or "")
            if stage:
                stage_status[stage] = status
                if entry.get("error"):
                    last_error = str(entry.get("error") or "")
                if stage not in stage_order:
                    stage_order.append(stage)
                messages = entry.get("messages") or []
                if messages:
                    stage_messages.setdefault(stage, []).extend(str(msg) for msg in messages)
                    if not pr_url:
                        pr_url = extract_pr_url_from_text(
                            "\n".join(str(msg) for msg in messages)
                        )
        elif event_type == "harness.session_finished" and entry.get("error"):
            last_error = str(entry.get("error") or "")

    if not stage_order:
        stage_order = list(META_EVOLVE_STAGE_ORDER)
        for stage in stage_status:
            if stage not in stage_order:
                stage_order.append(stage)

    completed = [
        stage for stage in stage_order
        if stage_status.get(stage) in {"success", "skipped"}
    ]
    failed_stage = next(
        (
            stage for stage in stage_order
            if stage_status.get(stage) == "failed"
        ),
        "",
    )
    current_stage = ""
    if not failed_stage:
        if (
            last_message_stage
            and stage_status.get(last_message_stage) not in {"success", "failed", "skipped"}
        ):
            current_stage = last_message_stage
        else:
            current_stage = next(
                (
                    stage for stage in stage_order
                    if stage_status.get(stage) not in {"success", "skipped"}
                ),
                "",
            )

    stages = []
    for stage in stage_order:
        status = stage_status.get(stage)
        if not status:
            status = "running" if stage == current_stage else "pending"
        recent_messages = stage_messages.get(stage) or []
        stages.append({
            "stage": stage,
            "name": STAGE_DISPLAY_NAMES.get(stage, stage),
            "status": status,
            "messages": recent_messages[-3:],
        })

    progress = {
        "pipeline": pipeline,
        "stages": stages,
        "completed_stages": completed,
        "current_stage": current_stage,
        "failed_stage": failed_stage,
        "last_message": last_stage_message,
        "last_event_type": last_event_type,
        "last_error": last_error,
        "failure_code": classify_failure(last_error, last_stage_message) if failed_stage else "",
        "pr_url": pr_url,
    }
    progress["summary"] = format_progress_summary(progress)
    return progress


def determine_pipeline_status_from_log(log_path: Path) -> dict[str, Any]:
    """Parse a JSON Lines log file and determine whether the pipeline succeeded."""
    pipeline_type = ""
    pipeline_stages: list[str] = []
    stage_results: dict[str, str] = {}

    try:
        with log_path.open("r", encoding="utf-8") as lf:
            for line in lf:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (
                    entry.get("event_type") == "harness.message"
                    and entry.get("stages")
                    and entry.get("pipeline")
                ):
                    pipeline_stages = [s.get("slot") for s in entry["stages"]]
                    pipeline_type = entry.get("pipeline", "")
                if entry.get("event_type") == "harness.message":
                    content = str(entry.get("content") or "")
                    for skipped_stage in infer_skipped_stages_from_message(content):
                        stage_results.setdefault(skipped_stage, "skipped")
                if entry.get("event_type") == "harness.stage_result" and not entry.get("scope"):
                    slot = entry.get("stage")
                    status = entry.get("status")
                    if slot:
                        stage_results[slot] = status
    except Exception as exc:
        logger.warning("[AutoHarnessRunLogStatus] Failed to read log %s: %s", log_path, exc)
        return {"failed": False, "error": ""}

    if pipeline_type == "extended_evolve_pipeline":
        if "build_verify" not in stage_results:
            return {"failed": True, "error": "Stage 'build_verify' not appeared"}
        if stage_results.get("activate") not in {"success", "skipped"}:
            return {
                "failed": True,
                "error": f"Stage 'activate' {stage_results.get('activate', 'not completed')}",
            }
        return {"failed": False, "error": ""}

    for slot in pipeline_stages:
        result = stage_results.get(slot)
        if result not in {"success", "skipped"}:
            return {
                "failed": True,
                "error": f"Stage '{slot}' {stage_results.get(slot, 'not completed')}",
            }

    return {"failed": False, "error": ""}


def has_terminal_session_event(log_path: Path) -> bool:
    """Return whether a structured run log contains a terminal session event."""
    try:
        with log_path.open("r", encoding="utf-8") as lf:
            for line in lf:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (
                    entry.get("event_type") == "harness.session_finished"
                    and entry.get("is_terminal") is True
                ):
                    return True
    except Exception as exc:
        logger.warning("[AutoHarnessRunLogStatus] Failed to scan terminal event in %s: %s", log_path, exc)
    return False
