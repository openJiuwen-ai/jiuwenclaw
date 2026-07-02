# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Generate a markdown report showing before/after compression for all P0+P1 rules.

Uses synthetic long-form test data to demonstrate compression ratios,
and DeepSeek LLM to analyze whether key information is preserved.

Usage:
    python tests/unit_tests/common/print_rule_comparison.py [--output path/to/report.md] [--no-llm]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

# Ensure tokenjuice module is importable
_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "jiuwenswarm" / "common"))

from tokenjuice import reduce_execution, load_rules
from tokenjuice.types import ToolExecutionInput, ReduceOptions


# ── API configuration ────────────────────────────────────────────────

def _load_env_config() -> dict:
    """Load API config from ~/.jiuwenswarm/config/.env"""
    env_path = Path.home() / ".jiuwenswarm" / "config" / ".env"
    config = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            value = value.strip().strip('"').strip("'")
            if value:
                config[key.strip()] = value
    return config


def _call_llm(prompt: str, config: dict) -> dict | None:
    """Call DeepSeek API for analysis."""
    try:
        import openai
        client = openai.OpenAI(
            api_key=config.get("API_KEY", ""),
            base_url=config.get("API_BASE", "https://api.deepseek.com"),
        )
        response = client.chat.completions.create(
            model=config.get("MODEL_NAME", "deepseek-v4-pro"),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=2000,
        )
        text = response.choices[0].message.content.strip()
        # Extract JSON from response — try multiple strategies
        # Strategy 1: code block
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()
        # Strategy 2: find first { to last }
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start:end + 1]
        return json.loads(text)
    except Exception as e:
        print(f"  LLM call failed: {e}")
        return None


# ── Synthetic data generators ────────────────────────────────────────

def gen_generic_fallback() -> str:
    lines = [
        "Starting process...",
        "Loading configuration from /etc/app/config.yaml",
        "INFO: Connected to database at localhost:5432",
        "WARNING: Deprecated API endpoint /v1/users will be removed in v3.0",
        "Processing batch 1 of 50...",
    ]
    for i in range(150):
        lines.append(f"  [{i:04d}] Processing item {i}: status=ok, duration={i*3}ms")
    lines.extend([
        "ERROR: Connection timeout to redis://cache:6379 after 30s",
        "WARNING: Retry attempt 1/3 for cache connection",
        "ERROR: Failed to write metrics to /var/log/app/metrics.json: Permission denied",
        "Process completed with 2 errors, 2 warnings in 45.3s",
    ])
    return "\n".join(lines) + "\n"


def gen_filesystem_ls() -> str:
    lines = ["total 4832"]
    entries = [
        ("drwxr-xr-x", ".git"),
        ("drwxr-xr-x", ".github"),
        ("drwxr-xr-x", ".venv"),
        ("-rw-r--r--", ".gitignore"),
        ("-rw-r--r--", "LICENSE"),
        ("-rw-r--r--", "README.md"),
        ("-rw-r--r--", "pyproject.toml"),
        ("drwxr-xr-x", "src"),
        ("drwxr-xr-x", "tests"),
        ("drwxr-xr-x", "docs"),
    ]
    for perm, name in entries:
        lines.append(f"{perm} 1 user group {len(name)*100} Jun 18 10:00 {name}")
    for i in range(50):
        size = (i + 1) * 1024
        lines.append(f"-rw-r--r-- 1 user group {size:>8} Jun 18 10:{i:02d} module_{i:03d}.py")
    return "\n".join(lines) + "\n"


def gen_search_grep() -> str:
    lines = []
    modules = ["auth", "api", "core", "utils", "models", "views", "serializers", "middleware"]
    for i in range(80):
        mod = modules[i % len(modules)]
        lines.append(f"src/{mod}/handler_{i:02d}.py:{i*5+10}: def process_request_{i}(self, request):  # TODO: refactor")
    lines.append("grep: src/secret/credentials.py: Permission denied")
    lines.append("grep: src/.env: Permission denied")
    lines.append("80 matches found across 45 files")
    return "\n".join(lines) + "\n"


def gen_text_wc() -> str:
    lines = []
    for i in range(40):
        mod = ["auth", "api", "core", "utils", "models"][i % 5]
        lines.append(f"  {100+i*7}  {300+i*15} {2000+i*100} src/{mod}/module_{i:02d}.py")
    lines.append(f" {sum(100+i*7 for i in range(40))} {sum(300+i*15 for i in range(40))} {sum(2000+i*100 for i in range(40))} total")
    return "\n".join(lines) + "\n"


def gen_git_status_modified() -> str:
    return (
        "On branch feature/user-auth\n"
        "Your branch is ahead of 'origin/feature/user-auth' by 3 commits.\n"
        "\n"
        "Changes not staged for commit:\n"
        '  (use "git add <file>..." to update what will be committed)\n'
        '  (use "git restore <file>..." to discard changes in working directory)\n'
        "\tmodified:   src/auth/handler.py\n"
        "\tmodified:   src/auth/models.py\n"
        "\tmodified:   src/auth/views.py\n"
        "\tmodified:   src/core/config.py\n"
        "\tmodified:   tests/test_auth.py\n"
        "\n"
        "Untracked files:\n"
        '  (use "git add <file>..." to include in what will be committed)\n'
        "\tsrc/auth/middleware.py\n"
        "\tsrc/auth/serializers.py\n"
        "\ttests/test_middleware.py\n"
        "\t.env.local\n"
    )


def gen_git_status_clean() -> str:
    return "On branch main\nYour branch is up to date with 'origin/main'.\n\nnothing to commit, working tree clean\n"


def gen_git_status_staged() -> str:
    lines = [
        "On branch develop",
        "Your branch is behind 'origin/develop' by 5 commits.",
        "",
        "Changes to be committed:",
        '  (use "git restore --staged <file>..." to unstage)',
    ]
    for i in range(3):
        lines.append(f"\tmodified:   src/api/endpoint_{i}.py")
    lines.append(f"\tnew file:   src/api/new_feature.py")
    lines.append(f"\tdeleted:    src/api/old_handler.py")
    lines.extend([
        "",
        "Changes not staged for commit:",
        '  (use "git add <file>..." to update what will be committed)',
    ])
    for i in range(5):
        lines.append(f"\tmodified:   src/core/module_{i}.py")
    lines.extend([
        "",
        "Untracked files:",
        '  (use "git add <file>..." to include in what will be committed)',
    ])
    for i in range(4):
        lines.append(f"\ttemp/debug_{i}.log")
    return "\n".join(lines) + "\n"


def gen_git_diff() -> str:
    lines = []
    files = [
        ("src/auth/handler.py", "abc1234", "def5678"),
        ("src/auth/models.py", "111aaaa", "222bbbb"),
        ("src/core/config.py", "333cccc", "444dddd"),
        ("src/api/views.py", "555eeee", "666ffff"),
        ("tests/test_auth.py", "777gggg", "888hhhh"),
    ]
    for filepath, old_hash, new_hash in files:
        lines.extend([
            f"diff --git a/{filepath} b/{filepath}",
            f"index {old_hash}..{new_hash} 100644",
            f"--- a/{filepath}",
            f"+++ b/{filepath}",
        ])
        for hunk_idx in range(3):
            base_line = 10 + hunk_idx * 30
            lines.append(f"@@ -{base_line},8 +{base_line},10 @@ class Handler:")
            lines.append(f"     # Context line {hunk_idx}")
            lines.append(f"     existing_var = 'old_value_{hunk_idx}'")
            for j in range(8):
                lines.append(f"+    new_line_{hunk_idx}_{j} = compute_something({j})")
            for j in range(3):
                lines.append(f"-    removed_line_{hunk_idx}_{j} = deprecated_call()")
            lines.append(f"     # More context")
            lines.append(f"     return result")
    return "\n".join(lines) + "\n"


def gen_task_node() -> str:
    lines = [
        "Starting application server...",
        "Loading configuration from /app/config.json",
        "Connected to database at postgres://localhost:5432/app",
        "Initializing middleware stack...",
        "Route /api/v1/users registered",
        "Route /api/v1/products registered",
        "Route /api/v1/orders registered",
        "Server listening on port 3000",
        "Processing incoming request: GET /api/v1/users",
    ]
    for i in range(15):
        lines.append(f"  [debug] cache hit for key user:{i}:true")
    lines.extend([
        "TypeError: Cannot read properties of undefined (reading 'map')",
        "    at processUsers (/app/src/handlers/user.js:42:18)",
        "    at Object.handleRequest (/app/src/routes/api.js:15:12)",
        "    at Layer.handle [as handle_request] (/app/node_modules/express/lib/router/layer.js:95:5)",
        "    at next (/app/node_modules/express/lib/router/route.js:149:13)",
        "    at Route.dispatch (/app/node_modules/express/lib/router/route.js:119:3)",
    ])
    for i in range(15):
        lines.append(f"    at frame{i} (/app/lib/internal/module_{i}.js:{i*5}:1)")
    lines.append("Node.js v20.11.0")
    return "\n".join(lines) + "\n"


def gen_task_python() -> str:
    lines = []
    for i in range(30):
        lines.append(f"processing step {i}/{29}: completed successfully")
    lines.extend([
        "",
        "Traceback (most recent call last):",
        '  File "/app/main.py", line 15, in <module>',
        "    result = process_data(raw_input)",
        '  File "/app/processors/transform.py", line 42, in process_data',
        "    validated = validate_schema(data)",
        '  File "/app/utils/validation.py", line 88, in validate_schema',
        "    raise ValueError(f'Invalid field: {field_name}')",
        "ValueError: Invalid field: user_email",
    ])
    return "\n".join(lines) + "\n"


def gen_git_log() -> str:
    commits = []
    messages = [
        "feat(auth): add JWT token refresh endpoint",
        "fix(api): resolve race condition in concurrent requests",
        "refactor(core): extract validation logic into separate module",
        "docs: update API documentation for v2 endpoints",
        "test: add integration tests for user authentication flow",
        "chore: update dependencies to latest versions",
        "feat(ui): implement dark mode toggle",
        "fix(db): optimize slow query in user search",
        "perf: add caching layer for frequently accessed data",
        "feat: implement webhook notification system",
        "fix: handle edge case in pagination logic",
        "refactor: simplify error handling middleware",
        "feat(api): add rate limiting to public endpoints",
        "test: mock external API calls in unit tests",
        "fix(auth): prevent session fixation vulnerability",
        "feat: add CSV export for analytics dashboard",
        "chore: configure CI pipeline for automated testing",
        "fix(ui): correct responsive layout on mobile devices",
        "feat: implement real-time notifications via WebSocket",
        "perf: reduce database connection pool size",
        "docs: add contributing guidelines",
        "feat(api): support GraphQL query language",
        "fix: resolve memory leak in event listener",
        "refactor: migrate from callbacks to async/await",
        "test: achieve 90% code coverage",
        "feat: add multi-language support (i18n)",
        "fix: correct timezone handling in date picker",
        "perf: implement lazy loading for large datasets",
        "feat: add file upload with drag-and-drop",
        "chore: set up code quality checks with SonarQube",
        "fix(api): sanitize user input to prevent XSS",
        "feat: implement two-factor authentication",
        "refactor: extract common utilities into shared library",
        "test: add end-to-end tests with Cypress",
        "fix: handle network timeout gracefully",
        "feat: add audit logging for admin actions",
        "perf: optimize image loading with lazy load",
        "docs: write deployment guide for Kubernetes",
        "feat: implement role-based access control (RBAC)",
        "fix: correct calculation in analytics aggregation",
    ]
    for i in range(40):
        hash_val = f"{0xe19618db + i:08x}"
        commits.append(f"{hash_val} {messages[i]}")
    return "\n".join(commits) + "\n"


def gen_filesystem_find() -> str:
    lines = []
    dirs = ["src", "tests", "docs", "scripts", "config"]
    for i in range(60):
        d = dirs[i % len(dirs)]
        lines.append(f"/project/{d}/module_{i:03d}/handler.py")
    lines.append("find: '/project/src/secret': Permission denied")
    lines.append("find: '/project/config/.env': Permission denied")
    return "\n".join(lines) + "\n"


def gen_task_env() -> str:
    lines = [
        "PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME=/home/user",
        "SHELL=/bin/bash",
        "USER=user",
        "LANG=en_US.UTF-8",
        "TERM=xterm-256color",
        "DISPLAY=:0",
        "XDG_SESSION_TYPE=wayland",
    ]
    for i in range(72):
        lines.append(f"APP_VAR_{i:03d}=value_{i:03d}_{'x'*20}")
    return "\n".join(lines) + "\n"


def gen_generic_help() -> str:
    lines = [
        "Usage: myapp [OPTIONS] COMMAND [ARGS]",
        "",
        "  A powerful application for managing distributed systems.",
        "",
        "Options:",
        "  -v, --verbose         Enable verbose output",
        "  -c, --config PATH     Path to configuration file",
        "  -l, --log-level TEXT  Set logging level (DEBUG/INFO/WARNING/ERROR)",
        "  --color / --no-color  Enable/disable colored output",
        "  --timeout INT         Request timeout in seconds [default: 30]",
        "  --retry INT           Number of retry attempts [default: 3]",
        "  -h, --help            Show this message and exit",
        "  --version             Show version and exit",
        "",
        "Commands:",
        "  init        Initialize a new project",
        "  start       Start the application server",
        "  stop        Stop the application server",
        "  status      Show current server status",
        "  deploy      Deploy to production environment",
        "  rollback    Rollback to previous version",
    ]
    for i in range(50):
        lines.append(f"  cmd-{i:02d}     Execute command {i} with advanced options and sub-commands")
    lines.extend([
        "",
        "Environment Variables:",
        "  MYAPP_HOME       Application root directory",
        "  MYAPP_CONFIG     Alternative config file path",
        "  MYAPP_LOG_DIR    Log output directory",
        "",
        "Examples:",
        "  myapp init --template=django",
        "  myapp start --verbose --log-level=DEBUG",
        "  myapp deploy --env=staging --timeout=120",
    ])
    return "\n".join(lines) + "\n"


def gen_git_branch() -> str:
    lines = ["* Dolores"]
    branches = [
        "develop", "main", "release/v1.0", "release/v2.0", "release/v2.1",
        "feature/auth-module", "feature/api-refactor", "feature/ui-redesign",
        "feature/perf-optimization", "feature/search-index", "feature/i18n-support",
        "feature/payment-gateway", "feature/notification-system", "feature/file-upload",
        "feature/cache-layer", "feature/websocket-support", "feature/graphql-api",
        "feature/rate-limiting", "feature/audit-log", "feature/data-export",
        "bugfix/login-error", "bugfix/memory-leak", "bugfix/timezone-issue",
        "bugfix/race-condition", "bugfix/null-pointer", "bugfix/css-overflow",
        "bugfix/api-timeout", "bugfix/db-deadlock", "bugfix/cache-invalidation",
        "hotfix/critical-security", "hotfix/db-migration", "hotfix/ssl-cert-expiry",
        "experiment/ml-integration", "experiment/new-architecture", "experiment/microservices",
        "experiment/event-sourcing", "experiment/cqrs-pattern",
    ]
    for b in branches:
        lines.append(f"  {b}")
    for b in ["develop", "main", "feature/auth-module", "feature/api-refactor",
              "release/v2.0", "release/v2.1", "bugfix/login-error"]:
        lines.append(f"  remotes/origin/{b}")
    return "\n".join(lines) + "\n"


def gen_git_remote() -> str:
    return (
        "origin\tgit@gitcode.com:user/jiuwenswarm.git (fetch)\n"
        "origin\tgit@gitcode.com:user/jiuwenswarm.git (push)\n"
        "upstream\thttps://github.com/openjiuwen/jiuwenswarm.git (fetch)\n"
        "upstream\thttps://github.com/openjiuwen/jiuwenswarm.git (push)\n"
        "backup\tgit@backup.internal:repos/jiuwenswarm.git (fetch)\n"
        "backup\tgit@backup.internal:repos/jiuwenswarm.git (push)\n"
        "team-fork\tgit@gitcode.com:team-dev/jiuwenswarm.git (fetch)\n"
        "team-fork\tgit@gitcode.com:team-dev/jiuwenswarm.git (push)\n"
        "ci-mirror\thttps://ci.internal/mirror/jiuwenswarm.git (fetch)\n"
        "ci-mirror\thttps://ci.internal/mirror/jiuwenswarm.git (push)\n"
        "staging\tgit@staging.internal:deploy/jiuwenswarm.git (fetch)\n"
        "staging\tgit@staging.internal:deploy/jiuwenswarm.git (push)\n"
    )


# ── Test case definitions ────────────────────────────────────────────

CASES = [
    ("P0-1", "generic/fallback", "unknown_tool --process data", gen_generic_fallback, "exec"),
    ("P0-2", "filesystem/ls", "ls -la /project", gen_filesystem_ls, "exec"),
    ("P0-3", "search/grep", "grep -rn 'TODO' src/", gen_search_grep, "grep"),
    ("P0-4", "text/wc", "wc -l src/**/*.py", gen_text_wc, "exec"),
    ("P0-5a", "git/status", "git status", gen_git_status_modified, "exec"),
    ("P0-5b", "git/status", "git status", gen_git_status_clean, "exec"),
    ("P0-5c", "git/status", "git status", gen_git_status_staged, "exec"),
    ("P0-6", "git/diff", "git diff HEAD", gen_git_diff, "exec"),
    ("P0-7", "task/node", "node /app/src/index.js", gen_task_node, "exec"),
    ("P0-8", "task/python", "python3 /app/main.py", gen_task_python, "exec"),
    ("P1-1", "git/log-oneline", "git log --oneline -40", gen_git_log, "exec"),
    ("P1-2", "filesystem/find", "find /project -name '*.py'", gen_filesystem_find, "exec"),
    ("P1-3", "task/env", "env", gen_task_env, "exec"),
    ("P1-4", "generic/help", "myapp --help", gen_generic_help, "exec"),
    ("P1-5", "git/branch", "git branch -a", gen_git_branch, "exec"),
    ("P1-6", "git/remote-v", "git remote -v", gen_git_remote, "exec"),
]


# ── Compression & Analysis ───────────────────────────────────────────

def _reduce(command: str, stdout: str, rules, tool_name: str = "exec"):
    return reduce_execution(
        ToolExecutionInput(tool_name=tool_name, command=command, stdout=stdout, exit_code=0),
        rules=rules,
        opts=ReduceOptions(max_inline_chars=2000),
    )


def _load_rule_json(rule_id: str) -> str:
    parts = rule_id.split("/")
    if len(parts) == 2:
        path = _REPO_ROOT / "jiuwenswarm" / "common" / "tokenjuice" / "rules" / parts[0] / f"{parts[1]}.json"
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    return "(not found)"


def _build_llm_prompt(label: str, command: str, raw: str, compressed: str, stats: dict) -> str:
    raw_chars = stats.get("raw_chars", 0)
    red_chars = stats.get("reduced_chars", 0)
    return f"""你是一个代码输出压缩质量评估专家。

以下是命令 `{command}` 的原始输出和压缩后输出（规则: {label}）。
请分析压缩是否保留了所有关键信息，并给出评分。

## 原始输出（{raw_chars} 字符）
```
{raw[:4000]}
```

## 压缩后输出（{red_chars} 字符）
```
{compressed}
```

## 评估要求
1. 列出原始输出中的关键信息点（错误信息、文件路径、统计数据、状态信息、分支名等）
2. 逐一检查每个关键信息点是否在压缩后保留
3. 评估压缩策略是否合理（不该丢的有没有丢，该丢的有没有丢）
4. 给出 1-10 分评分和总体判定

请严格以 JSON 格式输出（不要其他文字）：
{{
  "key_info_points": ["信息点1", "信息点2", ...],
  "preserved": ["保留的信息点1", ...],
  "lost": ["丢失的信息点1", ...],
  "score": 8,
  "verdict": "优秀",
  "analysis": "简要分析压缩质量"
}}"""


def _truncate(text: str, max_lines: int = 30) -> str:
    lines = text.split("\n")
    if len(lines) <= max_lines:
        return text.rstrip()
    half = max_lines // 2
    return "\n".join(lines[:half]) + f"\n... ({len(lines) - max_lines} lines omitted) ...\n" + "\n".join(lines[-half:])


# ── Report generation ────────────────────────────────────────────────

def generate_report(rules, use_llm: bool = True) -> str:
    config = _load_env_config() if use_llm else {}
    sections = []

    sections.append("# TokenJuice 规则压缩效果对比报告\n")
    sections.append("> 自动生成，使用合成长文本测试数据" + (" + DeepSeek LLM 分析" if use_llm else "") + "\n")
    sections.append(f"> 规则数: 14 条 (P0: 8 + P1: 6), 测试用例: {len(CASES)} 个\n")

    # Run all cases
    results = []
    for label, rule_id, command, gen_fn, tool_name in CASES:
        raw = gen_fn()
        result = _reduce(command, raw, rules, tool_name=tool_name)
        results.append((label, rule_id, command, raw, result))

    # Summary table
    sections.append("## 汇总\n")
    sections.append("| # | 规则 | 原始字符 | 压缩后 | 压缩率 |" + (" LLM 评分 | 判定 |" if use_llm else ""))
    sections.append("|---|---|---|---|---|" + ("---|---|" if use_llm else ""))

    for label, rule_id, command, raw, result in results:
        raw_chars = result.stats.get("raw_chars", 0)
        red_chars = result.stats.get("reduced_chars", 0)
        ratio = result.stats.get("ratio", 1)
        matched = result.classification.matched_reducer if result.classification else "?"
        save_pct = (1 - ratio) * 100 if raw_chars > 0 else 0
        row = f"| {label} | `{matched}` | {raw_chars:,} | {red_chars:,} | {save_pct:.1f}% |"
        sections.append(row)

    sections.append("")

    # Detailed sections
    sections.append("## 详细对比\n")

    for label, rule_id, command, raw, result in results:
        raw_chars = result.stats.get("raw_chars", 0)
        red_chars = result.stats.get("reduced_chars", 0)
        ratio = result.stats.get("ratio", 1)
        matched = result.classification.matched_reducer if result.classification else "?"
        facts = result.facts or {}

        sections.append(f"### {label}: {rule_id}\n")
        sections.append(f"- **匹配规则**: `{matched}`")
        sections.append(f"- **命令**: `{command}`")
        sections.append(f"- **压缩**: {raw_chars:,} → {red_chars:,} ({(1-ratio)*100:.1f}% 节省)")
        if facts:
            sections.append(f"- **事实计数**: {facts}")
        sections.append("")

        # Rule JSON
        sections.append("<details>")
        sections.append("<summary>规则定义 (JSON)</summary>\n")
        sections.append("```json")
        sections.append(_load_rule_json(rule_id))
        sections.append("```\n</details>\n")

        # Before
        sections.append("<details>")
        sections.append(f"<summary>压缩前 ({raw_chars:,} chars, {len(raw.splitlines())} lines)</summary>\n")
        sections.append("```")
        sections.append(_truncate(raw, 35))
        sections.append("```\n</details>\n")

        # After
        sections.append("<details>")
        sections.append(f"<summary>压缩后 ({red_chars:,} chars)</summary>\n")
        sections.append("```")
        sections.append(result.inline_text)
        sections.append("```\n</details>\n")

        # LLM analysis
        if use_llm:
            print(f"  Analyzing {label}: {rule_id}...", end=" ", flush=True)
            prompt = _build_llm_prompt(f"{label}: {rule_id}", command, raw, result.inline_text, result.stats)
            analysis = _call_llm(prompt, config)
            if analysis:
                score = analysis.get("score", "?")
                verdict = analysis.get("verdict", "?")
                key_points = analysis.get("key_info_points", [])
                preserved = analysis.get("preserved", [])
                lost = analysis.get("lost", [])
                detail = analysis.get("analysis", "")

                sections.append(f"**LLM 分析**: 评分 {score}/10 — {verdict}\n")
                if detail:
                    sections.append(f"> {detail}\n")
                if key_points:
                    sections.append("关键信息点:")
                    for pt in key_points:
                        status = "✅" if pt in preserved else "❌"
                        sections.append(f"  - {status} {pt}")
                if lost:
                    sections.append(f"\n⚠️ 丢失信息: {', '.join(lost)}")
                sections.append("")
                print(f"{score}/10 {verdict}")
            else:
                sections.append("**LLM 分析**: 调用失败\n")
                print("FAILED")

        sections.append("---\n")

    return "\n".join(sections)


# ── Main ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Generate tokenjuice compression report")
    parser.add_argument("--output", "-o",
                        default=str(_REPO_ROOT / "docs" / "tokenjuice-compression-report.md"),
                        help="Output markdown file path")
    parser.add_argument("--no-llm", action="store_true",
                        help="Skip LLM analysis (faster)")
    args = parser.parse_args()

    print("Loading rules...")
    rules = load_rules(cwd=str(_REPO_ROOT), include_user=False, include_project=False)
    print(f"Loaded {len(rules)} rules\n")

    print(f"Running {len(CASES)} test cases" + ("" if args.no_llm else " with LLM analysis") + "...")
    report = generate_report(rules, use_llm=not args.no_llm)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")
    print(f"\nReport written to: {output_path}")
    print(f"Size: {len(report):,} chars")


if __name__ == "__main__":
    main()
