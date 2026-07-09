from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path


RUNNER_PATH = (
    Path(__file__).resolve().parents[2]
    / "jiuwenavatar"
    / "resources"
    / "avatar-skills"
    / "dev-reviewer"
    / "scripts"
    / "code_review_runner.py"
)
SCRIPTS_DIR = RUNNER_PATH.parent


def load_script(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_runner():
    return load_script("dev_review_runner", RUNNER_PATH)


def make_review_workspace(tmp_path: Path, review: dict) -> Path:
    repo = tmp_path / "repo"
    review_dir = repo / "doc" / "demo" / "review"
    review_dir.mkdir(parents=True)
    (review_dir / "pr.diff").write_text(
        "\n".join(
            [
                "diff --git a/pkg/a.py b/pkg/a.py",
                "--- a/pkg/a.py",
                "+++ b/pkg/a.py",
                "@@ -1,2 +1,4 @@",
                " def f():",
                '+    err = payload.get("error")',
                "+    if err:",
                "     return False",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (review_dir / "result.json").write_text(
        json.dumps(review, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return repo


def base_review(findings: dict) -> dict:
    return {
        "schema_version": 1,
        "findings": findings,
    }


def namespace(repo: Path, **kwargs):
    values = {
        "module": "demo",
        "repo_root": str(repo),
        "out_dir": "",
    }
    values.update(kwargs)
    return argparse.Namespace(**values)


def test_render_review_comment_from_full_comment_object():
    runner = load_runner()
    body = runner.render_review_comment(
        {
            "id": "CR-001",
            "dimension": "Code",
            "issue": "falsy error 被误判为异常",
            "comment": {
                "title": "`error` 字段对 falsy 值存在误判",
                "scenario": "`error=0`、`error=[]` 或 `error=False` 时会进入错误分支。",
                "examples": ["`error=0` -> 误判为有错误", "`error=[]` -> 误判为有错误"],
                "impact": "会错误中断正常执行路径。",
                "fix": "仅对非空字符串或真实异常对象判定为错误。",
                "verification": "补充 falsy error 参数化测试。",
                "code": "if err is not None:\n    ...",
            },
        },
        "must_fix",
    )

    assert "**[严重][Must Fix][Code]** `error` 字段对 falsy 值存在误判" in body
    assert "例如：" in body
    assert "- `error=0` -> 误判为有错误" in body
    assert "```" in body
    assert "<!-- dev-reviewer:CR-001 -->" in body


def test_render_review_comment_falls_back_to_legacy_fields():
    runner = load_runner()
    body = runner.render_review_comment(
        {
            "id": "CR-010",
            "dimension": "Spec",
            "issue": "缺少边界测试",
            "risk": "回归时可能漏掉空输入路径。",
            "recommendation": "补充空输入测试。",
            "minimal_patch_example": "+def test_empty_input(): ...",
        },
        "should_fix",
    )

    assert "**[建议][Should Fix][Spec]** 缺少边界测试" in body
    assert "**影响：** 回归时可能漏掉空输入路径。" in body
    assert "**建议修复：** 补充空输入测试。" in body
    assert "```diff" in body
    assert "<!-- dev-reviewer:CR-010 -->" in body


def test_validate_comments_rejects_missing_position_and_duplicate_ids(tmp_path: Path):
    runner = load_runner()
    review = base_review(
        {
            "must_fix": [
                {"id": "CR-001", "dimension": "Code", "location": "pkg/a.py:99", "issue": "bad"},
                {"id": "CR-001", "dimension": "Code", "location": "pkg/a.py:2", "issue": "dup"},
            ],
            "should_fix": [],
            "nice_to_have": [],
        }
    )
    repo = make_review_workspace(tmp_path, review)

    code = runner.command_validate_comments(namespace(repo))
    payload = json.loads((repo / "doc" / "demo" / "review" / "result.json").read_text(encoding="utf-8"))
    errors, _ = runner.validate_review_comments(payload)

    assert code == 1
    assert any("duplicate finding id" in error for error in errors)
    assert any("missing valid position" in error for error in errors)


def test_render_comments_writes_files_and_manifest(tmp_path: Path):
    runner = load_runner()
    review = base_review(
        {
            "must_fix": [
                {
                    "id": "CR-001",
                    "dimension": "Code",
                    "location": "pkg/a.py:2",
                    "issue": "error 判定错误",
                    "risk": "误中断",
                    "recommendation": "区分 None 与 falsy 值。",
                }
            ],
            "should_fix": [],
            "nice_to_have": [],
        }
    )
    repo = make_review_workspace(tmp_path, review)

    code = runner.command_render_comments(namespace(repo, dry_run=True))
    manifest_path = repo / "doc" / "demo" / "review" / "comments" / "manifest.json"
    comment_path = repo / "doc" / "demo" / "review" / "comments" / "CR-001.md"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert code == 0
    assert comment_path.is_file()
    assert manifest["items"][0]["id"] == "CR-001"
    assert manifest["items"][0]["mode"] == "inline"
    assert manifest["items"][0]["position"] == 2


def test_post_comments_dry_run_outputs_commands_without_api(tmp_path: Path, capsys):
    runner = load_runner()
    review = base_review(
        {
            "must_fix": [
                {
                    "id": "CR-001",
                    "dimension": "Code",
                    "location": "pkg/a.py:2",
                    "issue": "bad",
                    "risk": "may fail",
                    "recommendation": "fix it",
                }
            ],
            "should_fix": [],
            "nice_to_have": [],
        }
    )
    repo = make_review_workspace(tmp_path, review)
    assert runner.command_render_comments(namespace(repo, dry_run=True)) == 0
    capsys.readouterr()

    code = runner.command_post_comments(
        namespace(
            repo,
            number=42,
            config="gitcode-repo.json",
            workspace="demo",
            target_project="upstream",
            gitcode_repo_root=str(RUNNER_PATH.parents[2] / "gitcode-repo"),
            execute=False,
        )
    )
    output = json.loads(capsys.readouterr().out)

    assert code == 0
    assert output["dry_run"] is True
    assert output["results"][0]["status"] == "dry_run"
    assert "--comment-file" in output["results"][0]["command"]
    assert "--dry-run" in output["results"][0]["command"]


def test_post_comments_dry_run_allows_discussion_comments(tmp_path: Path, capsys):
    runner = load_runner()
    review = base_review(
        {
            "must_fix": [],
            "should_fix": [
                {
                    "id": "CR-ARCH",
                    "dimension": "Code",
                    "location": "(architecture)",
                    "issue": "整体缓存策略缺失，无法定位到具体代码行。",
                }
            ],
            "nice_to_have": [],
        }
    )
    repo = make_review_workspace(tmp_path, review)
    assert runner.command_render_comments(namespace(repo, dry_run=True)) == 0
    capsys.readouterr()

    code = runner.command_post_comments(
        namespace(
            repo,
            number=42,
            config="gitcode-repo.json",
            workspace="demo",
            target_project="upstream",
            gitcode_repo_root=str(RUNNER_PATH.parents[2] / "gitcode-repo"),
            execute=False,
        )
    )
    output = json.loads(capsys.readouterr().out)
    command = output["results"][0]["command"]

    assert code == 0
    assert "--allow-review-discussion-comment" in command
    assert "--path" not in command
    assert "--position" not in command


def test_extract_dev_reviewer_signatures():
    runner = load_runner()

    signatures = runner.extract_dev_reviewer_signatures(
        [
            {"body": "已提交\n<!-- dev-reviewer:CR-001 -->"},
            {"body": "普通评论"},
            {"body": "<!-- dev-reviewer:CR-ABC_02 -->"},
        ]
    )

    assert signatures == {"CR-001", "CR-ABC_02"}


def test_config_hardening_security_item_is_schema_valid():
    validator = load_script("review_schema_validator", SCRIPTS_DIR / "review_schema_validator.py")
    review = {
        "schema_version": 1,
        "verdict": "PASS",
        "gate_verdict": "PASS",
        "verdict_reason": "Reviewed and no blocking findings remain.",
        "layer_alignment": "PASS",
        "patch_risk": "none",
        "risk_rating": "Low",
        "summary": {"change_intent": "test", "scope": "unit"},
        "pass_fail_reasons": ["No blocking findings."],
        "findings": {"must_fix": [], "should_fix": [], "nice_to_have": []},
        "security_review": {
            "status": "PASS",
            "items": [
                {
                    "category": "config-hardening",
                    "status": "PASS",
                    "evidence": "config scan completed",
                }
            ],
        },
        "reviewer": "dev-reviewer",
    }

    assert validator.validate_review_result(review) == []


def test_dependency_audit_lock_only_is_not_pass():
    automation = load_script("review_automation", SCRIPTS_DIR / "review_automation.py")

    passed, summary = automation._dependency_audit_pass([], ["package-lock.json"])

    assert passed is False
    assert "audit_not_executed" in summary
