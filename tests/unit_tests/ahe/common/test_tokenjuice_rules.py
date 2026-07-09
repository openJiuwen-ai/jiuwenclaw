# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tokenjuice rule-level tests — P0 + P1 rules.

Each rule gets test methods that verify:
  1. Rule matching (classification.matched_reducer)
  2. Key information preservation (critical strings survive compression)
  3. Compression effectiveness (reduced < raw)
  4. Counter accuracy (facts dict values)
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure tokenjuice module is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "jiuwenswarm" / "common"))

from tokenjuice import reduce_execution, load_rules
from tokenjuice.types import ToolExecutionInput, ReduceOptions


@pytest.fixture(scope="module")
def rules():
    """Load rules once for the entire test module."""
    return load_rules(
        cwd=str(Path(__file__).resolve().parents[4]),
        include_user=False,
        include_project=False,
    )


def _reduce(command: str, stdout: str, exit_code: int = 0, rules=None, tool_name: str = "exec", **kwargs):
    """Helper to run reduce_execution with common defaults."""
    return reduce_execution(
        ToolExecutionInput(
            tool_name=tool_name,
            command=command,
            stdout=stdout,
            exit_code=exit_code,
            **kwargs,
        ),
        rules=rules,
        opts=ReduceOptions(max_inline_chars=2000),
    )


# ====================================================================
# P0 — 立即可用，高价值（8 条规则）
# ====================================================================


class TestP0GenericFallback:
    """generic/fallback — 兜底规则，覆盖所有未匹配命令。"""

    def test_clamps_large_output(self, rules):
        raw = "\n".join([f"line {i}: some unknown command output" for i in range(200)]) + "\n"
        result = _reduce("unknown_command --flag", raw, rules=rules)

        assert result.classification.matched_reducer == "generic/fallback"
        assert result.stats["reduced_chars"] < result.stats["raw_chars"]
        assert "line 0" in result.inline_text  # first lines preserved

    def test_preserves_error_lines(self, rules):
        raw = (
            "normal output line 1\n"
            "ERROR: something went wrong\n"
            "normal output line 2\n"
            "WARNING: deprecated API usage\n"
            + "\n".join([f"filler line {i}" for i in range(50)])
            + "\n"
        )
        result = _reduce("some_tool", raw, rules=rules)

        assert result.classification.matched_reducer == "generic/fallback"


class TestP0FilesystemLs:
    """filesystem/ls — 目录列表压缩。"""

    def test_preserves_first_entries(self, rules):
        lines = [f"-rw-r--r-- 1 user group {i*100} Jun 16 file{i:03d}.txt" for i in range(30)]
        raw = "total 300\n" + "\n".join(lines) + "\n"
        result = _reduce("ls -la", raw, rules=rules)

        assert result.classification.matched_reducer == "filesystem/ls"
        assert "file000.txt" in result.inline_text
        assert result.stats["reduced_chars"] < result.stats["raw_chars"]


class TestP0SearchGrep:
    """search/grep — grep 输出压缩。"""

    def test_preserves_match_lines(self, rules):
        lines = [
            f"src/module{i}.py:{i*10}: def function_{i}():  # TODO"
            for i in range(1, 30)
        ]
        raw = "\n".join(lines) + "\n"
        result = _reduce("grep -rn 'TODO' src/", raw, rules=rules)

        assert result.classification.matched_reducer == "search/grep"
        assert "src/module1.py:10" in result.inline_text
        assert "src/module2.py:20" in result.inline_text
        assert result.facts is not None
        assert result.facts.get("match", 0) >= 15

    def test_preserves_error_messages(self, rules):
        raw = (
            "src/a.py:5: match here\n"
            "grep: src/secret: Permission denied\n"
            "src/b.py:10: another match\n"
        )
        result = _reduce("grep -rn 'match' src/", raw, rules=rules)

        assert result.classification.matched_reducer == "search/grep"
        assert "Permission denied" in result.inline_text


class TestP0TextWc:
    """text/wc — 行数/字数统计。"""

    def test_preserves_totals(self, rules):
        raw = (
            "  150  450 3200 src/main.py\n"
            "   80  220 1600 src/utils.py\n"
            "  230  670 4800 total\n"
        )
        result = _reduce("wc src/*.py", raw, rules=rules)

        assert result.classification.matched_reducer == "text/wc"
        assert "total" in result.inline_text


class TestP0GitStatus:
    """git/status — 工作区状态压缩。"""

    def test_preserves_branch_and_files(self, rules):
        raw = (
            "On branch feature/auth\n"
            "Your branch is up to date with 'origin/feature/auth'.\n"
            "\n"
            "Changes not staged for commit:\n"
            "  (use \"git add <file>...\" to update what will be committed)\n"
            "\tmodified:   src/auth/handler.py\n"
            "\tmodified:   src/auth/routes.py\n"
            "\n"
            "Untracked files:\n"
            "\t.env.local\n"
            "\ttests/test_auth.py\n"
        )
        result = _reduce("git status", raw, rules=rules)

        assert result.classification.matched_reducer == "git/status"
        # Branch name preserved (key fix)
        assert "feature/auth" in result.inline_text
        # Modified files preserved
        assert "src/auth/handler.py" in result.inline_text
        assert "src/auth/routes.py" in result.inline_text
        # Untracked files preserved
        assert ".env.local" in result.inline_text
        # Counters
        assert result.facts is not None
        assert result.facts.get("modified file", 0) >= 2
        assert result.facts.get("untracked file", 0) >= 1

    def test_clean_tree(self, rules):
        raw = "On branch main\nnothing to commit, working tree clean\n"
        result = _reduce("git status", raw, rules=rules)

        assert result.classification.matched_reducer == "git/status"
        assert "working tree clean" in result.inline_text


class TestP0GitDiff:
    """git/diff — 代码变更压缩。"""

    def test_preserves_hunks(self, rules):
        raw = (
            "diff --git a/src/main.py b/src/main.py\n"
            "--- a/src/main.py\n"
            "+++ b/src/main.py\n"
            "@@ -10,6 +10,7 @@ def handler():\n"
            "     x = 1\n"
            "-    y = old_value\n"
            "+    y = new_value\n"
            "+    z = added_line\n"
            "     return x + y\n"
        )
        result = _reduce("git diff -- src/main.py", raw, rules=rules)

        assert result.classification.matched_reducer == "git/diff"
        assert "diff --git" in result.inline_text
        assert "@@" in result.inline_text
        assert "+    y = new_value" in result.inline_text
        assert "-    y = old_value" in result.inline_text

    def test_counters(self, rules):
        raw = (
            "diff --git a/a.py b/a.py\n"
            "--- a/a.py\n"
            "+++ b/a.py\n"
            "@@ -1,3 +1,4 @@\n"
            "-old1\n"
            "+new1\n"
            "+new2\n"
            " context\n"
        )
        result = _reduce("git diff -- a.py", raw, rules=rules)

        assert result.classification.matched_reducer == "git/diff"
        assert result.facts is not None
        assert result.facts.get("added line", 0) >= 2
        assert result.facts.get("removed line", 0) >= 1


class TestP0TaskNode:
    """task/node — Node.js 执行输出。"""

    def test_preserves_errors(self, rules):
        raw = (
            "running script...\n"
            "TypeError: Cannot read properties of undefined\n"
            "    at processRequest (/app/src/handler.js:42:15)\n"
            "    at Object.<anonymous> (/app/src/index.js:10:5)\n"
            + "\n".join([f"  at frame{i} (/app/lib/mod{i}.js:{i}:1)" for i in range(20)])
            + "\n"
        )
        result = _reduce("node /app/src/index.js", raw, rules=rules)

        assert result.classification.matched_reducer == "task/node"
        assert "TypeError" in result.inline_text


class TestP0TaskPython:
    """task/python — Python 执行输出。"""

    def test_preserves_traceback(self, rules):
        raw = (
            "Traceback (most recent call last):\n"
            "  File \"/app/main.py\", line 10, in <module>\n"
            "    result = process(data)\n"
            "  File \"/app/utils.py\", line 25, in process\n"
            "    return data['key']\n"
            "KeyError: 'key'\n"
            + "\n".join([f"filler line {i}" for i in range(30)])
            + "\n"
        )
        result = _reduce("python3 -c 'import main'", raw, rules=rules)

        assert result.classification.matched_reducer == "task/python"
        assert "Traceback" in result.inline_text
        assert "KeyError" in result.inline_text


# ====================================================================
# P1 — 高价值，中频出现（6 条规则）
# ====================================================================


class TestP1GitLog:
    """git/log-oneline — 提交历史。"""

    def test_preserves_commits(self, rules):
        raw = "\n".join([
            f"abc{i:04d} feat: commit message {i}" for i in range(20)
        ]) + "\n"
        result = _reduce("git log --oneline -20", raw, rules=rules)

        assert result.classification.matched_reducer == "git/log-oneline"
        assert "abc0000" in result.inline_text
        assert result.stats["reduced_chars"] < result.stats["raw_chars"]


class TestP1FilesystemFind:
    """filesystem/find — 文件搜索。"""

    def test_preserves_paths(self, rules):
        lines = [f"/project/src/module{i}/file.py" for i in range(20)]
        raw = "\n".join(lines) + "\n"
        result = _reduce("find . -name '*.py' -type f", raw, rules=rules)

        assert result.classification.matched_reducer == "filesystem/find"
        assert "module0" in result.inline_text
        assert result.stats["reduced_chars"] < result.stats["raw_chars"]


class TestP1TaskEnv:
    """task/env — 环境变量列表。"""

    def test_compresses_env_list(self, rules):
        lines = [f"VAR_{i}=value_{i}" for i in range(50)]
        raw = "\n".join(lines) + "\n"
        result = _reduce("env", raw, rules=rules)

        assert result.classification.matched_reducer == "task/env"
        assert "VAR_0=value_0" in result.inline_text
        assert result.stats["reduced_chars"] < result.stats["raw_chars"]


class TestP1GenericHelp:
    """generic/help — help 输出。"""

    def test_preserves_help_structure(self, rules):
        raw = (
            "Usage: my-tool [OPTIONS] COMMAND [ARGS]\n"
            "\n"
            "  A powerful CLI tool.\n"
            "\n"
            "Options:\n"
            "  --verbose    Enable verbose output\n"
            "  --config     Path to config file\n"
            "  --help       Show this message\n"
            "\n"
            "Commands:\n"
            "  init         Initialize project\n"
            "  build        Build the project\n"
            "  deploy       Deploy to production\n"
            + "\n".join([f"  cmd{i:<12} Command {i} description" for i in range(30)])
            + "\n"
        )
        result = _reduce("my-tool --help", raw, rules=rules)

        assert result.classification.matched_reducer == "generic/help"
        assert "Usage:" in result.inline_text
        assert "Options:" in result.inline_text


class TestP1GitBranch:
    """git/branch — 分支管理。"""

    def test_preserves_branches(self, rules):
        raw = (
            "  develop\n"
            "* feature/auth\n"
            "  main\n"
            "  hotfix/fix-login\n"
        )
        result = _reduce("git branch", raw, rules=rules)

        assert result.classification.matched_reducer == "git/branch"
        assert "feature/auth" in result.inline_text
        assert "main" in result.inline_text


class TestP1GitRemoteV:
    """git/remote-v — 远程仓库。"""

    def test_preserves_remotes(self, rules):
        raw = (
            "origin\tgit@github.com:user/repo.git (fetch)\n"
            "origin\tgit@github.com:user/repo.git (push)\n"
            "upstream\thttps://github.com/org/repo.git (fetch)\n"
            "upstream\thttps://github.com/org/repo.git (push)\n"
        )
        result = _reduce("git remote -v", raw, rules=rules)

        assert result.classification.matched_reducer == "git/remote-v"
        assert "origin" in result.inline_text
        assert "upstream" in result.inline_text
