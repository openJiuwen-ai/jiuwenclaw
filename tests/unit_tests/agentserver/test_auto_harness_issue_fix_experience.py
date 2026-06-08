import asyncio
from pathlib import Path

from jiuwenswarm.agents.harness.common.auto_harness.gitcode_issue_client import GitCodeIssue
from jiuwenswarm.agents.harness.common.auto_harness.issue_runner import (
    GitCodeIssueRunner,
    IssueWatchOptions,
)
from jiuwenswarm.agents.harness.common.auto_harness.issue_state_store import IssueStateStore
from jiuwenswarm.agents.harness.common.auto_harness.task_store import TaskStore


def _issue(number: int, title: str, body: str, labels: tuple[str, ...] = ("bug",)) -> GitCodeIssue:
    return GitCodeIssue(
        number=number,
        title=title,
        body=body,
        html_url=f"https://gitcode.com/openJiuwen/jiuwenswarm/issues/{number}",
        labels=labels,
        raw={},
    )


class _FakeClient:
    def __init__(self, issues):
        self._issues = {issue.number: issue for issue in issues}

    def get_issue(self, *, owner, repo, number):
        return self._issues[number]

    def list_issues(self, **_kwargs):
        return list(self._issues.values())

    def list_issue_pull_requests(self, **_kwargs):
        return []

    def list_pull_requests(self, **_kwargs):
        return []


class _FakeHarnessService:
    def __init__(self):
        self.queries = []

    async def run_task(self, query: str, model=None, pipeline=None):
        self.queries.append(query)
        return {"task_id": "sch_fake", "message": "started"}

    async def get_scheduled_task_status(self, task_id: str):
        return None


def test_assess_issue_difficulty_marks_unclear_or_high_for_human():
    unclear = _issue(1, "偶现问题", "待补充，暂时不清楚如何复现", ("bug",))
    result = GitCodeIssueRunner.assess_issue_difficulty(unclear)
    assert result["level"] == "unclear"
    assert result["needs_human"] is True

    high = _issue(
        2,
        "MCP 支持前端配置",
        "新增 MCP 前端配置、后端 API、协议兼容和多模块集成，需要设计思路。",
        ("feature", "sig/jiuwenclaw"),
    )
    result = GitCodeIssueRunner.assess_issue_difficulty(high)
    assert result["level"] in {"high", "unclear"}


def test_watch_once_skips_hard_issue_as_needs_human(tmp_path: Path):
    hard_issue = _issue(
        494,
        "MCP 支持前端配置",
        "新增 MCP 前端配置、后端 API、协议兼容和多模块集成，需要设计思路。",
        ("feature", "sig/jiuwenclaw"),
    )
    service = _FakeHarnessService()
    runner = GitCodeIssueRunner(
        client=_FakeClient([hard_issue]),
        state_store=IssueStateStore(tmp_path),
        harness_service=service,
    )

    result = asyncio.run(
        runner.watch_once(
            IssueWatchOptions(
                owner="openJiuwen",
                repo="jiuwenswarm",
                issue_numbers=(494,),
                labels=(),
                max_auto_difficulty="medium",
            )
        )
    )

    assert result["started"] == []
    assert result["skipped"][0]["status"] == "needs_human"
    assert result["skipped"][0]["human_label"] == "needs-human"
    assert service.queries == []


def test_watch_once_starts_medium_or_lower_issue(tmp_path: Path):
    easy_issue = _issue(
        1266,
        "InstanceLock.release Windows NameError",
        "在 Windows 下调用 InstanceLock.release 会出现 NameError。复现步骤明确，只需修复异常变量名并补充单测。",
        ("bug", "sig/jiuwenclaw"),
    )
    service = _FakeHarnessService()
    runner = GitCodeIssueRunner(
        client=_FakeClient([easy_issue]),
        state_store=IssueStateStore(tmp_path),
        harness_service=service,
    )

    result = asyncio.run(
        runner.watch_once(
            IssueWatchOptions(
                owner="openJiuwen",
                repo="jiuwenswarm",
                issue_numbers=(1266,),
                labels=(),
                max_auto_difficulty="medium",
            )
        )
    )

    assert result["started"][0]["task_id"] == "sch_fake"
    assert "GitCode Issue #1266" in service.queries[0]


def test_task_progress_extracts_pr_and_failure_code():
    logs = [
        {
            "event_type": "harness.message",
            "pipeline": "meta_evolve_pipeline",
            "stages": [{"slot": "implement"}, {"slot": "verify"}, {"slot": "publish"}],
        },
        {"event_type": "harness.stage_result", "stage": "implement", "status": "success"},
        {
            "event_type": "harness.stage_result",
            "stage": "publish",
            "status": "failed",
            "error": "GitCode PR creation failed: HTTP 400 Bad Request",
            "messages": [
                "PR 发布诊断: http_status=400",
                "PR 已创建: https://gitcode.com/openJiuwen/jiuwenswarm/merge_requests/2379",
            ],
        },
    ]

    progress = TaskStore.summarize_progress_from_logs(logs)

    assert progress["failed_stage"] == "publish"
    assert progress["failure_code"] == "pr_api_failed"
    assert progress["pr_url"].endswith("/merge_requests/2379")
