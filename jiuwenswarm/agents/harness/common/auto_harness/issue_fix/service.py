# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Issue-fix facade that keeps GitCode issue handling out of core service."""

from __future__ import annotations

import os
from typing import Any, Callable, Optional

from openjiuwen.auto_harness.pipelines import META_EVOLVE_PIPELINE
from openjiuwen.core.foundation.llm import Model

from .gitcode_issue_client import GitCodeIssueClient
from .issue_runner import GitCodeIssueRunner, IssueWatchOptions
from .issue_state_store import IssueStateStore


class IssueFixService:
    """Coordinates GitCode issue ingestion as an auto-harness capability."""

    def __init__(
        self,
        *,
        task_store: Any,
        issue_state_store: IssueStateStore,
        harness_service: Any,
        base_config_getter: Callable[[], Any],
        default_repo_url: str,
    ) -> None:
        self._task_store = task_store
        self._issue_state_store = issue_state_store
        self._harness_service = harness_service
        self._base_config_getter = base_config_getter
        self._default_repo_url = default_repo_url

    async def handle(
        self,
        action: str,
        params: dict[str, Any],
        model: Optional[Model] = None,
    ) -> dict[str, Any]:
        """Dispatch issue-fix capability actions."""
        if action in {"process_once", "watch_once"}:
            return await self.process_gitcode_issues_once(params, model)
        if action in {"state_list", "list_states"}:
            return await self.list_gitcode_issue_states()
        return {"error": f"未知 issue-fix 操作: {action}"}

    @staticmethod
    def _parse_repo_identifier(repo: str) -> tuple[str, str]:
        """Parse owner/repo from a GitCode URL or owner/repo string."""
        raw = str(repo or "").strip()
        if not raw:
            return ("", "")
        cleaned = raw.rstrip("/")
        if cleaned.endswith(".git"):
            cleaned = cleaned[:-4]
        parts = [part for part in cleaned.split("/") if part]
        if len(parts) >= 2:
            return (parts[-2], parts[-1])
        return ("", "")

    def _resolve_target_repo(self, params: dict[str, Any]) -> tuple[str, str]:
        owner = str(params.get("owner") or "").strip()
        repo = str(params.get("repo_name") or "").strip()
        if owner and repo:
            return (owner, repo)
        repo_param = str(params.get("repo") or "").strip()
        if repo_param:
            parsed_owner, parsed_repo = self._parse_repo_identifier(repo_param)
            if parsed_owner and parsed_repo:
                return (parsed_owner, parsed_repo)
        base_config = self._base_config_getter()
        repo_url = (
            base_config.repo_url
            if base_config is not None and base_config.repo_url
            else self._default_repo_url
        )
        return self._parse_repo_identifier(repo_url)

    def _resolve_access_token(self, params: dict[str, Any]) -> str:
        token = str(params.get("access_token") or "").strip()
        if token:
            return token
        env_token = os.getenv("GITCODE_ACCESS_TOKEN")
        if env_token:
            return env_token.strip()
        base_config = self._base_config_getter()
        if base_config is not None:
            try:
                return str(base_config.resolve_gitcode_token() or "").strip()
            except Exception:
                pass
        return ""

    @staticmethod
    def _parse_string_tuple(value: Any, default: tuple[str, ...]) -> tuple[str, ...]:
        if value is None:
            return default
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        if isinstance(value, list | tuple):
            return tuple(str(part).strip() for part in value if str(part).strip())
        return default

    @staticmethod
    def _parse_issue_numbers(value: Any) -> tuple[int, ...]:
        if value is None:
            return ()
        raw_parts: list[Any]
        if isinstance(value, str):
            raw_parts = [part.strip() for part in value.replace("，", ",").split(",")]
        elif isinstance(value, int):
            raw_parts = [value]
        elif isinstance(value, list | tuple):
            raw_parts = list(value)
        else:
            return ()

        numbers: list[int] = []
        for part in raw_parts:
            try:
                number = int(part)
            except (TypeError, ValueError):
                continue
            if number > 0 and number not in numbers:
                numbers.append(number)
        return tuple(numbers)

    async def process_gitcode_issues_once(
        self,
        params: dict[str, Any],
        model: Optional[Model] = None,
    ) -> dict[str, Any]:
        """Process GitCode issues once and create auto-harness tasks."""
        del model
        token = self._resolve_access_token(params)
        if not token:
            return {"error": "缺少 GitCode Access Token，请配置 gitcode.access_token 或 GITCODE_ACCESS_TOKEN"}

        owner, repo = self._resolve_target_repo(params)
        if not owner or not repo:
            return {"error": "无法解析 GitCode 仓库，请传入 repo=openJiuwen/jiuwenswarm"}

        try:
            max_issues = int(params.get("max_issues", 1))
        except (TypeError, ValueError):
            max_issues = 1
        max_issues = max(1, min(max_issues, 5))

        try:
            per_page = int(params.get("per_page", 20))
        except (TypeError, ValueError):
            per_page = 20
        per_page = max(1, min(per_page, 100))

        pipeline = str(params.get("pipeline") or META_EVOLVE_PIPELINE)
        issue_numbers = self._parse_issue_numbers(
            params.get("issue_numbers")
            or params.get("issues")
            or params.get("issue")
            or params.get("numbers")
        )
        if issue_numbers:
            max_issues = len(issue_numbers)
        try:
            start_interval_seconds = float(params.get("start_interval_seconds", 0) or 0)
        except (TypeError, ValueError):
            start_interval_seconds = 0.0
        if start_interval_seconds <= 0 and max_issues > 1:
            # openjiuwen auto_harness currently uses second-level timestamps
            # for some readonly worktree paths. Stagger immediate issue tasks
            # to avoid "worktree already exists" collisions.
            start_interval_seconds = 1.2
        options = IssueWatchOptions(
            owner=owner,
            repo=repo,
            issue_numbers=issue_numbers,
            labels=self._parse_string_tuple(params.get("labels"), ("auto-harness",)),
            exclude_labels=self._parse_string_tuple(
                params.get("exclude_labels"),
                ("blocked", "wontfix", "needs-discussion"),
            ),
            max_issues=max_issues,
            per_page=per_page,
            pipeline=pipeline,
            comment_on_start=bool(params.get("comment_on_start", False)),
            dry_run=bool(params.get("dry_run", False)),
            start_interval_seconds=start_interval_seconds,
            max_auto_difficulty=str(params.get("max_auto_difficulty") or "medium"),
        )
        client = GitCodeIssueClient(token=token)
        runner = GitCodeIssueRunner(
            client=client,
            state_store=self._issue_state_store,
            harness_service=self._harness_service,
        )
        return await runner.process_issues_once(options)

    async def list_gitcode_issue_states(self) -> dict[str, Any]:
        issues = []
        for issue in self._issue_state_store.list():
            enriched = dict(issue)
            task_id = str(enriched.get("task_id") or "")
            if task_id:
                task = self._task_store.get_task(task_id)
                if task is not None:
                    enriched["task_status"] = task.get("status")
                    enriched["progress"] = await self._task_store.summarize_task_progress(task)
            issues.append(enriched)
        return {"issues": issues}
