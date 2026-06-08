# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Task construction helpers for explicit GitCode issue-fix queries."""

from __future__ import annotations

import re

from openjiuwen.auto_harness.schema import OptimizationTask


def extract_gitcode_issue_number(query: str) -> str:
    """Extract an issue number from an explicit GitCode issue-fix query."""
    match = re.search(
        r"GitCode\s+Issue\s*#?\s*(\d+)|\bIssue\s*#\s*(\d+)",
        query,
        flags=re.IGNORECASE,
    )
    if not match:
        return ""
    return next((group for group in match.groups() if group), "")


def build_issue_fix_task_from_query(query: str) -> OptimizationTask | None:
    """Build a structured auto-harness task when the query targets a GitCode issue."""
    issue_number = extract_gitcode_issue_number(query)
    if not issue_number:
        return None
    return OptimizationTask(
        topic=f"fix-issue-{issue_number}",
        description=query,
        issue_ref=f"#{issue_number}",
        status="pending",
    )
