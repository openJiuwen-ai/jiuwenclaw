# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Compatibility wrapper for issue-fix runner."""

from .issue_fix.issue_runner import asyncio
from .issue_fix.issue_runner import GitCodeIssueRunner, IssueWatchOptions

__all__ = ["GitCodeIssueRunner", "IssueWatchOptions"]
