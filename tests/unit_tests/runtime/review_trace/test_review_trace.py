from __future__ import annotations

import json
from pathlib import Path

from jiuwenavatar.server.runtime.review_trace.integration import (
    committer_review_trace_base_dir,
    should_collect_committer_review_trace,
)
from jiuwenavatar.server.runtime.review_trace.redaction import redact_sensitive_values
from jiuwenavatar.server.runtime.review_trace.store import CommitterReviewTraceStore


def _executed_review() -> dict:
    return {
        "execution_id": "review-1",
        "messages": ["https://gitcode.com/openJiuwen/agent-core/pull/1910"],
        "steps": [{
            "kind": "tool",
            "detail": {
                "tool_name": "bash",
                "call_args": {"command": "python scripts/code_review_runner.py collect --pr https://gitcode.com/openJiuwen/agent-core/pull/1910"},
                "call_result": {"success": True},
            },
        }],
    }


def test_collection_scope_requires_committer_and_enabled(monkeypatch) -> None:
    monkeypatch.setenv("COMMITTER_REVIEW_TRACE_ENABLED", "true")
    assert should_collect_committer_review_trace("committer") is True
    assert should_collect_committer_review_trace("developer") is False
    assert should_collect_committer_review_trace("") is False
    monkeypatch.setenv("COMMITTER_REVIEW_TRACE_ENABLED", "false")
    assert should_collect_committer_review_trace("committer") is False


def test_store_writes_only_review_trace_by_default(tmp_path: Path) -> None:
    store = CommitterReviewTraceStore(
        tmp_path,
        review_traces_dir=tmp_path / "default" / "review_traces",
        write_review_trace=True,
        save_raw=False,
        require_pr_review=True,
    )
    store.save(_executed_review())
    traces = list((tmp_path / "default" / "review_traces").glob("*.json"))
    assert len(traces) == 1
    assert not (tmp_path / "default" / "raw").exists()
    assert json.loads(traces[0].read_text(encoding="utf-8"))["skill"] == "dev-reviewer"


def test_store_ignores_pr_mentions_without_review_execution(tmp_path: Path) -> None:
    store = CommitterReviewTraceStore(
        tmp_path,
        review_traces_dir=tmp_path / "default" / "review_traces",
        write_review_trace=True,
        save_raw=False,
        require_pr_review=True,
    )
    store.save({"execution_id": "mention", "messages": ["https://gitcode.com/a/b/pull/1"]})
    assert list((tmp_path / "default" / "review_traces").glob("*.json")) == []


def test_collect_merge_request_wins_over_stale_pull_url(tmp_path: Path) -> None:
    trajectory = {
        "execution_id": "review-1325",
        "messages": [
            "old context https://gitcode.com/openJiuwen/agent-core/pull/1910",
            "please review https://gitcode.com/openJiuwen/agent-core/issues/1325",
        ],
        "steps": [{
            "kind": "tool",
            "detail": {
                "tool_name": "bash",
                "call_args": {
                    "command": (
                        "python scripts/code_review_runner.py collect --pr "
                        "https://gitcode.com/openJiuwen/agent-core/merge_requests/1325"
                    )
                },
                "call_result": {"success": True},
            },
        }],
    }
    store = CommitterReviewTraceStore(
        tmp_path,
        review_traces_dir=tmp_path / "default" / "review_traces",
        write_review_trace=True,
        save_raw=False,
        require_pr_review=True,
    )
    store.save(trajectory)
    traces = list((tmp_path / "default" / "review_traces").glob("*.json"))
    assert len(traces) == 1
    trace = json.loads(traces[0].read_text(encoding="utf-8"))
    assert trace["case_id"] == "real_pr_openJiuwen_agent-core_1325"
    assert trace["task"]["pr_url"] == (
        "https://gitcode.com/openJiuwen/agent-core/merge_requests/1325"
    )
    assert "1910" not in traces[0].name


def test_stale_history_is_not_used_when_current_request_has_no_pr(tmp_path: Path) -> None:
    trajectory = {
        "execution_id": "stale-only",
        "steps": [{
            "kind": "llm",
            "detail": {"messages": [
                {"role": "user", "content": "review https://gitcode.com/a/b/pull/1910"},
                {"role": "assistant", "content": "done"},
                {"role": "user", "content": "now inspect https://gitcode.com/a/b/issues/1325"},
            ]},
        }],
    }
    store = CommitterReviewTraceStore(
        tmp_path,
        review_traces_dir=tmp_path / "default" / "review_traces",
        write_review_trace=True,
        save_raw=False,
        require_pr_review=True,
    )
    store.save(trajectory)
    assert list((tmp_path / "default" / "review_traces").glob("*.json")) == []


def test_reading_previous_review_is_not_a_new_review(tmp_path: Path) -> None:
    trajectory = {
        "execution_id": "read-old-review",
        "messages": ["review https://gitcode.com/openJiuwen/agent-core/merge_requests/1325"],
        "steps": [
            {
                "kind": "tool",
                "detail": {
                    "tool_name": "skill_tool",
                    "call_args": {"skill_name": "dev-reviewer"},
                    "call_result": {"success": True},
                },
            },
            {
                "kind": "tool",
                "detail": {
                    "tool_name": "read_file",
                    "call_args": {"file_path": "D:/repo/doc/pr1325/review/result.json"},
                    "call_result": {
                        "success": True,
                        "data": {"content": '{"findings":{"must_fix":[{"id":"MF-001","location":"a.py:1"}]}}'},
                    },
                },
            },
        ],
    }
    store = CommitterReviewTraceStore(
        tmp_path,
        review_traces_dir=tmp_path / "default" / "review_traces",
        write_review_trace=True,
        save_raw=False,
        require_pr_review=True,
    )
    store.save(trajectory)
    assert list((tmp_path / "default" / "review_traces").glob("*.json")) == []


def test_location_alone_does_not_prove_resolve_positions() -> None:
    from jiuwenavatar.server.runtime.review_trace.adapter import trajectory_to_review_trace

    trajectory = {
        "messages": ["https://gitcode.com/a/b/pull/1"],
        "steps": [
            {
                "kind": "tool",
                "detail": {
                    "tool_name": "bash",
                    "call_args": {"command": "python code_review_runner.py collect --pr https://gitcode.com/a/b/pull/1"},
                    "call_result": {"success": True},
                },
            },
            {
                "kind": "tool",
                "detail": {
                    "tool_name": "write_file",
                    "call_args": {
                        "file_path": "D:/repo/result.json",
                        "content": '{"findings":{"must_fix":[{"id":"MF-001","location":"a.py:1"}]}}',
                    },
                    "call_result": {"success": True},
                },
            },
        ],
    }
    trace = trajectory_to_review_trace(trajectory)
    assert trace["findings"][0]["position"] is None
    assert trace["findings"][0]["position_resolved"] is False
    assert trace["runner_steps"]["resolve_positions"]["status"] == ""


def test_null_position_stays_unresolved_after_resolve_command() -> None:
    from jiuwenavatar.server.runtime.review_trace.adapter import trajectory_to_review_trace

    result = {
        "findings": {
            "must_fix": [],
            "should_fix": [
                {"id": "SF-1", "location": "a.py:10", "position": None}
            ],
            "nice_to_have": [],
        }
    }
    trajectory = {
        "messages": ["https://gitcode.com/a/b/pull/1"],
        "steps": [
            {
                "kind": "tool",
                "detail": {
                    "tool_name": "bash",
                    "call_args": {
                        "command": (
                            "python code_review_runner.py collect --pr "
                            "https://gitcode.com/a/b/pull/1"
                        )
                    },
                    "call_result": {"success": True},
                },
            },
            {
                "kind": "tool",
                "detail": {
                    "tool_name": "write_file",
                    "call_args": {
                        "file_path": "D:/repo/result.json",
                        "content": json.dumps(result),
                    },
                    "call_result": {"success": True},
                },
            },
            {
                "kind": "tool",
                "detail": {
                    "tool_name": "bash",
                    "call_args": {
                        "command": "python code_review_runner.py resolve-positions --number 1"
                    },
                    "call_result": {"success": True},
                },
            },
        ],
    }

    trace = trajectory_to_review_trace(trajectory)

    assert trace["findings"][0]["position"] is None
    assert trace["findings"][0]["position_resolved"] is False
    assert trace["runner_steps"]["resolve_positions"]["status"] == ""


def test_runner_post_comments_defaults_to_dry_run() -> None:
    from jiuwenavatar.server.runtime.review_trace.adapter import trajectory_to_review_trace

    trajectory = {
        "messages": ["https://gitcode.com/a/b/pull/1"],
        "steps": [
            {
                "kind": "tool",
                "detail": {
                    "tool_name": "bash",
                    "call_args": {"command": "python code_review_runner.py collect --pr https://gitcode.com/a/b/pull/1"},
                    "call_result": {"success": True},
                },
            },
            {
                "kind": "tool",
                "detail": {
                    "tool_name": "bash",
                    "call_args": {"command": "python code_review_runner.py post-comments --number 1"},
                    "call_result": {
                        "success": True,
                        "data": {
                            "content": json.dumps({
                                "ok": True,
                                "dry_run": True,
                                "results": [{"id": "MF-001", "status": "dry_run"}],
                            })
                        },
                    },
                },
            },
        ],
    }
    trace = trajectory_to_review_trace(trajectory)
    assert trace["runner_steps"]["post_comments"]["status"] == "dry_run_success"
    assert trace["gitcode_api"]["mode"] == "dry_run"
    assert trace["gitcode_api"]["execute_used"] is False


def test_single_digit_finding_id_is_linked_to_direct_comment() -> None:
    from jiuwenavatar.server.runtime.review_trace.adapter import trajectory_to_review_trace

    result = {"findings": {"must_fix": [], "should_fix": [
        {"id": "SF-1", "location": "a.py:1", "position": 1}
    ], "nice_to_have": []}}
    trajectory = {
        "messages": ["https://gitcode.com/a/b/pull/1"],
        "steps": [
            {"kind": "tool", "detail": {
                "tool_name": "bash",
                "call_args": {"command": "python code_review_runner.py collect --pr https://gitcode.com/a/b/pull/1"},
                "call_result": {"success": True},
            }},
            {"kind": "tool", "detail": {
                "tool_name": "write_file",
                "call_args": {"file_path": "D:/result.json", "content": json.dumps(result)},
                "call_result": {"success": True},
            }},
            {"kind": "tool", "detail": {
                "tool_name": "bash",
                "call_args": {"command": "python pr_commenter.py --number 1 --comment-file D:/SF-1.md --path a.py --position 1"},
                "call_result": {"success": True, "data": {"content": '{"success":true,"comment_id":"abc"}'}},
            }},
        ],
    }
    trace = trajectory_to_review_trace(trajectory)
    assert trace["findings"][0]["comment_posted"] is True
    assert trace["gitcode_api"]["mode"] == "execute"


def test_default_runtime_location_is_user_data_not_repository(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert committer_review_trace_base_dir(avatar_id="avatar/1") == (
        tmp_path / ".jiuwenavatar" / "review_traces" / "avatar_1"
    )


def test_redaction_masks_credentials_and_home() -> None:
    value = {"token": "secret-value", "path": str(Path.home() / "private"), "text": "Bearer abc.def"}
    redacted = redact_sensitive_values(value)
    assert redacted["token"] == "******"
    assert "<USER_HOME>" in redacted["path"]
    assert redacted["text"] == "Bearer ******"
