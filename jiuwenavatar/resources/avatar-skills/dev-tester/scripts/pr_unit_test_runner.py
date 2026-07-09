#!/usr/bin/env python3
"""PR unit-test validation helper.

This single-file runner supports the whole skill workflow:

1. collect       - collect PR, issue, diff, stack, and changed-file context
2. init-plan     - create an editable unit test plan
3. execute       - run planned unit test commands one by one
4. report        - generate markdown and JSON reports
5. execute-report - execute and then report

The runner does not generate assertions by itself. The agent using the skill
must inspect the PR and issue, write focused unit tests, and update the plan.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def run(cmd: list[str], cwd: Path | None = None, timeout: int = 30) -> tuple[int, str, str]:
    """Run a command and return exit code, stdout, and stderr."""
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            text=True,
            capture_output=True,
            timeout=timeout,
            errors="replace",
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        cmd_str = " ".join(exc.cmd) if exc.cmd else " ".join(cmd)
        return 124, "", f"TimeoutExpired after {timeout}s: {cmd_str}"
    except Exception as exc:
        return 1, "", f"{type(exc).__name__}: {exc}"


def read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def is_url(value: str) -> bool:
    return value.startswith("http://") or value.startswith("https://")


def is_gitcode_url(value: str) -> bool:
    return "gitcode." in value.lower() or "gitcode.com" in value.lower()


def looks_like_login_required(text: str) -> bool:
    sample = text[:5000].lower()
    login_markers = ("login", "sign in", "signin", "登录", "登陆")
    return "<html" in sample and any(marker in sample for marker in login_markers)


def gitcode_token_help() -> str:
    return "GitCode requires an access token. Rerun collect with --gitcode-token <TOKEN> or set GITCODE_TOKEN before starting."


def fetch_text(url: str, timeout: int = 20, token: str = "") -> tuple[bool, str, str]:
    """Fetch URL text with a browser-like user agent."""
    headers = {
        "User-Agent": "Mozilla/5.0 dev-tester-pr-gate/1.0",
        "Accept": "text/plain,text/html,*/*",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
        headers["PRIVATE-TOKEN"] = token
    req = urllib.request.Request(
        url,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            return True, resp.read().decode(charset, errors="replace"), ""
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403} and is_gitcode_url(url):
            return False, "", f"HTTP {exc.code} while fetching GitCode URL. {gitcode_token_help()}"
        return False, "", f"HTTP {exc.code} while fetching {url}"
    except Exception as exc:
        return False, "", f"{type(exc).__name__}: {exc}"


def detect_stack(repo: Path) -> dict[str, list[str]]:
    """Detect common unit-test stacks from marker files."""
    markers = {
        "python": ["pyproject.toml", "requirements.txt", "setup.py", "pytest.ini", "tox.ini"],
        "node": ["package.json", "pnpm-lock.yaml", "yarn.lock", "tsconfig.json"],
        "go": ["go.mod"],
        "rust": ["Cargo.toml"],
        "java": ["pom.xml", "build.gradle", "build.gradle.kts"],
        "make": ["Makefile"],
    }
    found: dict[str, list[str]] = {}
    for stack, names in markers.items():
        hits = [name for name in names if (repo / name).exists()]
        if hits:
            found[stack] = hits
    return found


def changed_files_from_diff(diff_text: str) -> list[str]:
    """Extract changed file paths from a unified diff."""
    files: set[str] = set()
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4:
                path = parts[3][2:] if parts[3].startswith("b/") else parts[3]
                if path != "/dev/null":
                    files.add(path)
        elif line.startswith("+++ b/"):
            files.add(line[6:])
    return sorted(files)


def local_git_diff(
    repo: Path,
    base: str,
    head: str,
    *,
    include_worktree_fallback: bool = False,
) -> tuple[str, list[str], list[str]]:
    """Collect local diff from explicit refs or optional worktree fallbacks."""
    warnings: list[str] = []
    code, inside, err = run(["git", "rev-parse", "--is-inside-work-tree"], cwd=repo)
    if code != 0 or inside.strip() != "true":
        return "", [], [err.strip() or "Local repo is not a git worktree."]

    ranges: list[list[str]] = []
    if base and head:
        ranges.extend([[f"{base}...{head}"], [base, head]])
    elif include_worktree_fallback:
        ranges.extend([["--cached"], [], ["HEAD~1..HEAD"]])

    for diff_range in ranges:
        code, stdout, stderr = run(["git", "diff", "--no-ext-diff", *diff_range], cwd=repo, timeout=60)
        if code == 0 and stdout.strip():
            files = changed_files_from_diff(stdout)
            if files:
                return stdout, files, warnings
        if stderr.strip():
            warnings.append(f"git diff {' '.join(diff_range) or 'working tree'}: {stderr.strip()}")

    code, stdout, stderr = run(["git", "status", "--short"], cwd=repo)
    if code == 0 and stdout.strip():
        files = [line[3:].strip().replace("\\", "/") for line in stdout.splitlines() if len(line) > 3]
        warnings.append("No diff text collected, but git status found changed files.")
        return "", sorted(set(files)), warnings
    if stderr.strip():
        warnings.append(f"git status: {stderr.strip()}")
    return "", [], warnings


def fetch_pr_diff(pr: str, token: str = "") -> tuple[str, list[str]]:
    """Try common GitCode/GitHub diff and patch endpoints."""
    warnings: list[str] = []
    if not is_url(pr):
        return "", ["PR input is not a URL; remote diff fetch skipped."]
    if is_gitcode_url(pr) and not token:
        warnings.append("No GitCode access token was provided. Public PRs may still work; private/login-required PRs require --gitcode-token or GITCODE_TOKEN.")
    base = pr.rstrip("/")
    candidates = [base + ".diff", base + ".patch", base + "?format=diff", base + "?format=patch"]
    for url in candidates:
        ok, text, err = fetch_text(url, token=token)
        if ok and ("diff --git " in text or text.startswith("From ")):
            return text, warnings
        if ok and is_gitcode_url(url) and looks_like_login_required(text):
            warnings.append(gitcode_token_help())
            continue
        warnings.append(err or f"No diff found at {url}")
    return "", warnings


def read_issue(issue: str, token: str = "") -> tuple[str, str, list[str]]:
    """Read issue from file, URL, or inline text."""
    if not issue:
        return "", "", ["No issue input was provided."]
    path = Path(issue)
    if path.exists():
        return path.read_text(encoding="utf-8-sig", errors="replace"), str(path), []
    if is_url(issue):
        ok, text, err = fetch_text(issue, token=token)
        if ok and is_gitcode_url(issue) and looks_like_login_required(text):
            return "", issue, [gitcode_token_help()]
        return (text, issue, []) if ok else ("", issue, [err])
    return issue, "inline", []


def select_pr_diff(
    *,
    remote_diff: str,
    local_diff: str,
    local_files: list[str],
    base: str,
    head: str,
    prefer_local: bool,
) -> tuple[str, list[str], str, list[str]]:
    """Choose PR diff text and changed files; pr-gate defaults to remote PR diff."""
    warnings: list[str] = []
    remote_files = changed_files_from_diff(remote_diff) if remote_diff else []
    use_local_first = prefer_local or bool(base and head)

    if use_local_first:
        diff_text = local_diff or remote_diff
        changed_files = local_files or remote_files
        if local_diff:
            fetch_method = "local-git-diff"
        elif remote_diff:
            fetch_method = "pr-url-diff"
        else:
            fetch_method = "none"
    else:
        diff_text = remote_diff or local_diff
        changed_files = remote_files or local_files
        if remote_diff:
            fetch_method = "pr-url-diff"
        elif local_diff:
            fetch_method = "local-git-diff"
            warnings.append(
                "PR URL diff was unavailable; fell back to local git diff. "
                "The worktree may not match the remote PR."
            )
        else:
            fetch_method = "none"

    if local_diff.strip() and remote_diff.strip() and local_diff.strip() != remote_diff.strip():
        if fetch_method == "pr-url-diff":
            warnings.append(
                "Local git diff differs from PR URL diff; PR URL diff was preferred. "
                "Use --prefer-local or --base/--head to force local refs."
            )
        elif prefer_local:
            warnings.append("Local git diff differs from PR URL diff; --prefer-local selected local diff.")

    return diff_text, changed_files, fetch_method, warnings


def command_collect(ns: argparse.Namespace) -> int:
    repo = Path(ns.repo).resolve()
    out_dir = Path(ns.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    gitcode_token = ns.gitcode_token or os.environ.get("GITCODE_TOKEN") or os.environ.get("GITCODE_ACCESS_TOKEN") or ""

    warnings: list[str] = []
    remote_diff, remote_warnings = fetch_pr_diff(ns.pr, token=gitcode_token)
    warnings.extend(remote_warnings)
    include_worktree = bool(ns.prefer_local) or bool(ns.base and ns.head)
    local_diff, local_files, local_warnings = local_git_diff(
        repo,
        ns.base,
        ns.head,
        include_worktree_fallback=include_worktree,
    )
    warnings.extend(local_warnings)

    diff_text, changed_files, fetch_method, diff_warnings = select_pr_diff(
        remote_diff=remote_diff,
        local_diff=local_diff,
        local_files=local_files,
        base=ns.base,
        head=ns.head,
        prefer_local=bool(ns.prefer_local),
    )
    warnings.extend(diff_warnings)
    issue_text, issue_source, issue_warnings = read_issue(ns.issue, token=gitcode_token)
    warnings.extend(issue_warnings)

    (out_dir / "pr.diff").write_text(diff_text, encoding="utf-8")
    (out_dir / "issue.txt").write_text(issue_text, encoding="utf-8")
    context = {
        "schema_version": 1,
        "pr": ns.pr,
        "issue": ns.issue,
        "issue_source": issue_source,
        "repo": str(repo),
        "base": ns.base,
        "head": ns.head,
        "fetch_method": fetch_method,
        "gitcode_token_provided": bool(gitcode_token),
        "detected_stack": detect_stack(repo),
        "changed_files": changed_files,
        "diff_path": str(out_dir / "pr.diff"),
        "issue_text_path": str(out_dir / "issue.txt"),
        "warnings": warnings,
        "limitations": [],
    }
    if not diff_text.strip():
        context["limitations"].append("No PR diff text was collected; inspect the local repo manually before writing tests.")
    if not changed_files:
        context["limitations"].append("No changed files were collected.")
    if not issue_text.strip():
        context["limitations"].append("No issue text was collected.")
    write_json(out_dir / "context.json", context)
    print(out_dir / "context.json")
    return 0


def first_requirement(issue_text: str) -> str:
    """Extract a compact requirement hint from issue text."""
    for line in issue_text.splitlines():
        cleaned = re.sub(r"^[#>*\\-\\d.\\s]+", "", line).strip()
        if len(cleaned) >= 12:
            return cleaned[:220]
    return "Validate the functional behavior requested by the issue."


def is_test_file(path: str) -> bool:
    lowered = path.lower().replace("\\", "/")
    name = lowered.rsplit("/", 1)[-1]
    return (
        "/test" in lowered
        or name.startswith("test_")
        or name.endswith("_test.py")
        or name.endswith(".test.ts")
        or name.endswith(".spec.ts")
        or name.endswith("_test.go")
        or name.endswith("_test.rs")
        or name.endswith("test.java")
    )


def suggest_artifact(source: str, stacks: dict[str, Any]) -> str:
    """Suggest a likely unit test path for a changed source file."""
    path = Path(source)
    stem = path.stem
    if "python" in stacks:
        return f"tests/test_{stem}.py"
    if "node" in stacks:
        suffix = ".test.ts" if path.suffix in {".ts", ".tsx"} else ".test.js"
        return str(Path("tests") / f"{stem}{suffix}").replace("\\", "/")
    if "go" in stacks:
        return str(path.with_name(f"{stem}_test.go")).replace("\\", "/")
    if "rust" in stacks:
        return str(path).replace("\\", "/")
    if "java" in stacks:
        return str(Path("src/test/java") / f"{stem}Test.java").replace("\\", "/")
    return str(Path("tests") / f"{stem}_unit_test").replace("\\", "/")


def suggest_command(artifact: str, stacks: dict[str, Any], repo: Path) -> str:
    """Suggest a narrow unit-test command. The agent must edit if needed."""
    if "python" in stacks:
        return f"python -m pytest {artifact} -q"
    if "node" in stacks:
        return f"npm test -- {artifact}"
    if "go" in stacks:
        package = str(Path(artifact).parent).replace("\\", "/")
        return f"go test ./{package}" if package and package != "." else "go test ./..."
    if "rust" in stacks:
        return "cargo test"
    if "java" in stacks:
        if (repo / "pom.xml").exists():
            return "mvn test"
        if (repo / "build.gradle").exists() or (repo / "build.gradle.kts").exists():
            return "gradle test"
        return ""
    return ""


def command_init_plan(ns: argparse.Namespace) -> int:
    out_dir = Path(ns.out_dir).resolve()
    context = read_json(out_dir / "context.json")
    repo = Path(context.get("repo") or ".").resolve()
    issue_path = Path(context.get("issue_text_path") or out_dir / "issue.txt")
    issue_text = issue_path.read_text(encoding="utf-8-sig", errors="replace") if issue_path.exists() else ""
    requirement = first_requirement(issue_text)
    stacks = context.get("detected_stack") or {}
    changed = context.get("changed_files") or []
    sources = [p for p in changed if not is_test_file(p)] or changed

    cases = []
    for index, source in enumerate(sources[:8], start=1):
        artifact = source if is_test_file(source) else suggest_artifact(source, stacks)
        cases.append(
            {
                "id": f"UT-{index:03d}",
                "title": f"Validate functional behavior changed in {source}",
                "issue_requirement": requirement,
                "target_changed_files": [source],
                "test_artifacts": [artifact],
                "command": suggest_command(artifact, stacks, repo),
                "expected_behavior": "The unit test demonstrates the PR behavior required by the issue.",
                "notes": "Edit after inspecting code. Keep this unit-test scoped.",
            }
        )
    if not cases:
        cases.append(
            {
                "id": "UT-001",
                "title": "Validate PR behavior with a focused unit test",
                "issue_requirement": requirement,
                "target_changed_files": [],
                "test_artifacts": [],
                "command": "",
                "expected_behavior": "The unit test demonstrates the PR behavior required by the issue.",
                "notes": "Fill this manually after inspecting the PR.",
            }
        )

    plan = {
        "schema_version": 1,
        "pr": context.get("pr"),
        "issue": context.get("issue"),
        "repo": context.get("repo"),
        "cases": cases,
        "warnings": [
            "This is an editable draft. Inspect the PR and issue, write or select real unit tests, and update commands before execution."
        ],
    }
    for case in plan["cases"]:
        case["test_artifacts"] = absolute_artifact_paths(case.get("test_artifacts") or [], repo)
    write_json(out_dir / "unit_test_plan.json", plan)
    print(out_dir / "unit_test_plan.json")
    return 0


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "case"


_UNSAFE_SHELL_PATTERN = re.compile(
    r"(?<!\\)(?:&&|\|\||[;&|`]|\$\(|\$\{|<|>)"
)

_WINDOWS_SCRIPT_STEMS = frozenset({"npm", "npx", "pnpm", "yarn", "yarnpkg"})


def resolve_argv_executable(argv: list[str]) -> list[str]:
    """On Windows, resolve npm-style shims to an executable path (e.g. npm.cmd)."""
    if os.name != "nt" or not argv:
        return argv
    token = argv[0]
    if os.path.isabs(token) and Path(token).exists():
        return argv
    stem = Path(token).stem.lower()
    if stem not in _WINDOWS_SCRIPT_STEMS:
        return argv
    resolved = shutil.which(token) or shutil.which(f"{stem}.cmd") or shutil.which(f"{stem}.bat")
    if resolved:
        return [resolved, *argv[1:]]
    return argv


def parse_plan_command(command: str) -> list[str]:
    """Parse a plan command into argv without invoking a shell."""
    command_clean = (command or "").strip()
    if not command_clean:
        raise ValueError("No executable unit test command was provided.")
    if _UNSAFE_SHELL_PATTERN.search(command_clean):
        raise ValueError(
            "Plan command contains shell operators (; | & ` < > etc.). "
            "Use a single executable with arguments only, e.g. "
            "'python -m pytest tests/test_foo.py -q'."
        )
    argv = shlex.split(command_clean, posix=(os.name != "nt"))
    if not argv:
        raise ValueError("No executable unit test command was provided.")
    return argv


def run_test_command(command: str, cwd: Path, timeout: int) -> tuple[str, int | None, float, str, str]:
    """Run one unit-test command as argv (no shell)."""
    started = time.monotonic()
    try:
        argv = parse_plan_command(command)
    except ValueError as exc:
        duration = time.monotonic() - started
        return "fail", None, duration, "", str(exc)

    try:
        proc = subprocess.run(
            resolve_argv_executable(argv),
            cwd=str(cwd),
            shell=False,
            text=True,
            capture_output=True,
            timeout=timeout,
            errors="replace",
        )
        duration = time.monotonic() - started
        return ("pass" if proc.returncode == 0 else "fail"), proc.returncode, duration, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        duration = time.monotonic() - started
        stdout = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode(errors="replace")
        stderr = exc.stderr if isinstance(exc.stderr, str) else (exc.stderr or b"").decode(errors="replace")
        return "fail", None, duration, stdout, stderr + f"\nTIMEOUT after {timeout}s"
    except Exception as exc:
        duration = time.monotonic() - started
        return "fail", None, duration, "", f"{type(exc).__name__}: {exc}"


def command_execute(ns: argparse.Namespace) -> int:
    out_dir = Path(ns.out_dir).resolve()
    plan = read_json(out_dir / "unit_test_plan.json")
    repo = Path(ns.repo or plan.get("repo") or ".").resolve()
    plan = normalize_plan_test_artifacts(out_dir, repo)
    logs_dir = out_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    for case in plan.get("cases") or []:
        case_id = str(case.get("id") or f"UT-{len(results) + 1:03d}")
        command = str(case.get("command") or "").strip()
        stdout_path = logs_dir / f"{safe_name(case_id)}.stdout.txt"
        stderr_path = logs_dir / f"{safe_name(case_id)}.stderr.txt"
        if command:
            status, exit_code, duration, stdout, stderr = run_test_command(command, repo, ns.timeout)
        else:
            status, exit_code, duration, stdout, stderr = "fail", None, 0.0, "", "No executable unit test command was provided."
        stdout_path.write_text(stdout or "", encoding="utf-8", errors="replace")
        stderr_path.write_text(stderr or "", encoding="utf-8", errors="replace")
        results.append(
            {
                "id": case_id,
                "title": case.get("title") or case_id,
                "test_artifacts": case.get("test_artifacts") or [],
                "test_artifacts_absolute": absolute_artifact_paths(case.get("test_artifacts") or [], repo),
                "target_changed_files": case.get("target_changed_files") or [],
                "command": command,
                "status": status,
                "exit_code": exit_code,
                "duration_seconds": round(duration, 3),
                "stdout_log": str(stdout_path),
                "stderr_log": str(stderr_path),
            }
        )

    verdict = "PASS" if results and all(item["status"] == "pass" for item in results) else "FAIL"
    write_json(
        out_dir / "unit_test_results.json",
        {
            "schema_version": 1,
            "verdict": verdict,
            "repo": str(repo),
            "results": results,
            "warnings": [] if results else ["No unit test cases were executed."],
        },
    )
    print(out_dir / "unit_test_results.json")
    return 0 if verdict == "PASS" else 1


def bullet(items: list[str]) -> list[str]:
    return [f"- {item}" for item in items] if items else ["- None recorded."]


def absolute_artifact_paths(artifacts: Any, repo: Path) -> list[str]:
    paths: list[str] = []
    for artifact in artifacts or []:
        value = str(artifact).strip()
        if not value:
            continue
        path = Path(value)
        absolute_path = path if path.is_absolute() else repo / path
        paths.append(str(absolute_path.resolve()))
    return paths


def command_with_absolute_artifacts(command: str, artifacts: Any, absolute_paths: list[str]) -> str:
    updated = str(command or "")
    placeholders: dict[str, str] = {}
    for index, (artifact, absolute_path) in enumerate(zip(artifacts or [], absolute_paths)):
        value = str(artifact).strip()
        if not value:
            continue
        placeholder = f"__PR_UNIT_TEST_ARTIFACT_{index}__"
        placeholders[placeholder] = absolute_path
        variants = {
            value,
            value.replace("/", "\\"),
            value.replace("\\", "/"),
        }
        for variant in sorted(variants, key=len, reverse=True):
            updated = updated.replace(variant, placeholder)
    for placeholder, absolute_path in placeholders.items():
        updated = updated.replace(placeholder, absolute_path)
    return updated


def normalize_plan_test_artifacts(out_dir: Path, repo: Path) -> dict[str, Any]:
    plan_path = out_dir / "unit_test_plan.json"
    plan = read_json(plan_path)
    cases = plan.get("cases") or []
    changed = False
    for case in cases:
        original_artifacts = case.get("test_artifacts") or []
        absolute_paths = absolute_artifact_paths(original_artifacts, repo)
        if absolute_paths and case.get("test_artifacts") != absolute_paths:
            case["test_artifacts"] = absolute_paths
            changed = True
        if absolute_paths and case.get("test_artifacts_absolute") != absolute_paths:
            case["test_artifacts_absolute"] = absolute_paths
            changed = True
        command = str(case.get("command") or "")
        absolute_command = command_with_absolute_artifacts(command, original_artifacts, absolute_paths)
        if absolute_command != command:
            case["command"] = absolute_command
            changed = True
    if changed:
        write_json(plan_path, plan)
    return plan


def preserved_test_scripts(cases: list[dict[str, Any]], repo: Path) -> list[dict[str, Any]]:
    scripts: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for case in cases:
        case_id = str(case.get("id") or "")
        for artifact in case.get("test_artifacts") or []:
            repo_path = str(artifact).strip()
            if not repo_path:
                continue
            artifact_path = Path(repo_path)
            absolute_path = artifact_path if artifact_path.is_absolute() else repo / artifact_path
            absolute_path = absolute_path.resolve()
            key = (case_id, str(absolute_path))
            if key in seen:
                continue
            seen.add(key)
            scripts.append(
                {
                    "case_id": case_id,
                    "repo_path": str(absolute_path),
                    "absolute_path": str(absolute_path),
                    "exists": absolute_path.exists(),
                }
            )
    return scripts


def script_language(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".py": "python",
        ".js": "javascript",
        ".jsx": "jsx",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".go": "go",
        ".rs": "rust",
        ".java": "java",
        ".kt": "kotlin",
        ".kts": "kotlin",
        ".rb": "ruby",
        ".php": "php",
        ".cs": "csharp",
        ".cpp": "cpp",
        ".cc": "cpp",
        ".cxx": "cpp",
        ".c": "c",
        ".h": "c",
        ".hpp": "cpp",
        ".sh": "bash",
        ".ps1": "powershell",
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".xml": "xml",
    }.get(suffix, "")


def markdown_fence(content: str) -> str:
    longest = max((len(match.group(0)) for match in re.finditer(r"`+", content)), default=0)
    return "`" * max(3, longest + 1)


def load_test_script_sources(scripts: list[dict[str, Any]], out_dir: Path) -> list[dict[str, Any]]:
    snapshot_dir = out_dir / "test_script_sources"
    sources: list[dict[str, Any]] = []
    for script in scripts:
        absolute_path = Path(str(script.get("absolute_path") or ""))
        item = dict(script)
        item["language"] = script_language(absolute_path)
        item["source"] = ""
        item["snapshot_path"] = ""
        item["read_error"] = ""
        if not absolute_path.exists():
            item["read_error"] = "file does not exist"
            sources.append(item)
            continue
        try:
            source = absolute_path.read_text(encoding="utf-8-sig", errors="replace")
            snapshot_dir.mkdir(parents=True, exist_ok=True)
            snapshot_name = f"{safe_name(str(item.get('case_id') or 'case'))}__{safe_name(absolute_path.name)}"
            snapshot_path = snapshot_dir / snapshot_name
            snapshot_path.write_text(source, encoding="utf-8", errors="replace")
            item["source"] = source
            item["snapshot_path"] = str(snapshot_path)
        except Exception as exc:
            item["read_error"] = f"{type(exc).__name__}: {exc}"
        sources.append(item)
    return sources


def test_case_script_map(cases: list[dict[str, Any]], repo: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for case in cases:
        case_id = str(case.get("id") or "")
        title = str(case.get("title") or "")
        for absolute_path in absolute_artifact_paths(case.get("test_artifacts") or [], repo):
            key = (case_id, absolute_path)
            if key in seen:
                continue
            seen.add(key)
            path = Path(absolute_path)
            rows.append(
                {
                    "test_case_id": case_id,
                    "title": title,
                    "test_script_absolute_path": absolute_path,
                    "exists": path.exists(),
                }
            )
    return rows


def command_report(ns: argparse.Namespace) -> int:
    out_dir = Path(ns.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    context = read_json(out_dir / "context.json")
    plan = read_json(out_dir / "unit_test_plan.json")
    results = read_json(out_dir / "unit_test_results.json")
    case_results = results.get("results") or []
    verdict = "PASS" if case_results and all(item.get("status") == "pass" for item in case_results) else "FAIL"
    repo_text = context.get("repo") or plan.get("repo") or results.get("repo") or ""
    repo = Path(repo_text).resolve() if repo_text else Path(".").resolve()
    plan = normalize_plan_test_artifacts(out_dir, repo)
    normalized_case_results: list[dict[str, Any]] = []
    for item in case_results:
        enriched = dict(item)
        enriched["test_artifacts_absolute"] = absolute_artifact_paths(item.get("test_artifacts") or [], repo)
        if enriched["test_artifacts_absolute"]:
            enriched["command"] = command_with_absolute_artifacts(
                str(enriched.get("command") or ""),
                item.get("test_artifacts") or [],
                enriched["test_artifacts_absolute"],
            )
            enriched["test_artifacts"] = enriched["test_artifacts_absolute"]
        normalized_case_results.append(enriched)
    case_results = normalized_case_results
    plan_cases = plan.get("cases") or []
    report_test_cases: list[dict[str, Any]] = []
    for case in plan_cases:
        enriched = dict(case)
        enriched["test_artifacts_absolute"] = absolute_artifact_paths(case.get("test_artifacts") or [], repo)
        report_test_cases.append(enriched)
    saved_scripts = preserved_test_scripts(plan_cases, repo)
    case_script_map = test_case_script_map(report_test_cases, repo)
    script_sources = load_test_script_sources(saved_scripts, out_dir)

    warnings = []
    warnings.extend(context.get("warnings") or [])
    warnings.extend(plan.get("warnings") or [])
    warnings.extend(results.get("warnings") or [])
    limitations = list(context.get("limitations") or [])
    if not case_results:
        limitations.append("No executable unit test evidence was produced.")
    if any(not item.get("command") for item in case_results):
        limitations.append("At least one test case had no executable command.")
    if any(not item.get("exists") for item in saved_scripts):
        limitations.append("At least one planned test artifact path does not currently exist in the repository.")
    if any(item.get("read_error") for item in script_sources):
        limitations.append("At least one preserved unit test script could not be read into the report.")

    evidence_files = [str(out_dir / name) for name in ("context.json", "pr.diff", "issue.txt", "unit_test_plan.json", "unit_test_results.json")]
    evidence_files.extend(str(item.get("snapshot_path")) for item in script_sources if item.get("snapshot_path"))
    for item in case_results:
        evidence_files.extend(path for path in [item.get("stdout_log"), item.get("stderr_log")] if path)

    lines: list[str] = [
        "# PR Unit Test Validation Report",
        "",
        "## Target",
        "",
        f"- PR: {context.get('pr') or plan.get('pr') or 'unknown'}",
        f"- Issue: {context.get('issue') or plan.get('issue') or 'unknown'}",
        f"- Repository: {context.get('repo') or plan.get('repo') or results.get('repo') or 'unknown'}",
        f"- Diff source: {context.get('fetch_method') or 'unknown'}",
        f"- Changed files: {', '.join(context.get('changed_files') or []) or 'none collected'}",
        "",
        "## Overall Verdict",
        "",
        verdict,
        "",
        "## Test Case ID to Test Script Mapping",
        "",
        "| Test Case ID | Test Script Absolute Path | Exists |",
        "| --- | --- | --- |",
    ]
    if case_script_map:
        for item in case_script_map:
            exists = "yes" if item.get("exists") else "no"
            lines.append(f"| {item.get('test_case_id', '')} | {item.get('test_script_absolute_path', '')} | {exists} |")
    else:
        lines.append("| none | n/a | no |")

    lines.extend([
        "",
        "## Preserved Unit Test Scripts",
        "",
        "These unit test scripts are kept in the target repository. The runner does not delete them after execution.",
        "",
        "| Case ID | Absolute Path | Exists |",
        "| --- | --- | --- |",
    ])
    if saved_scripts:
        for script in saved_scripts:
            exists = "yes" if script.get("exists") else "no"
            lines.append(f"| {script.get('case_id', '')} | {script.get('absolute_path', '')} | {exists} |")
    else:
        lines.append("| none | n/a | no |")

    lines.extend(["", "## Unit Test Script Source Code", ""])
    if script_sources:
        for source_item in script_sources:
            lines.extend(
                [
                    f"### {source_item.get('case_id', '')}: {source_item.get('absolute_path', '')}",
                    "",
                    f"- Absolute path: {source_item.get('absolute_path', '')}",
                    f"- Snapshot path: {source_item.get('snapshot_path') or 'not created'}",
                    f"- Exists: {'yes' if source_item.get('exists') else 'no'}",
                    "",
                ]
            )
            if source_item.get("source"):
                fence = markdown_fence(str(source_item.get("source") or ""))
                language = str(source_item.get("language") or "")
                lines.extend([f"{fence}{language}", str(source_item.get("source") or ""), fence, ""])
            else:
                lines.extend([f"Source code unavailable: {source_item.get('read_error') or 'empty file'}", ""])
    else:
        lines.append("No unit test script source code was recorded because no test_artifacts were listed.")

    lines.extend([
        "",
        "## Unit Test Case List",
        "",
        "| ID | Title | Requirement | Test Artifacts | Command |",
        "| --- | --- | --- | --- | --- |",
    ])
    for case in report_test_cases:
        command = str(case.get("command") or "")
        artifacts = ", ".join(str(path) for path in case.get("test_artifacts_absolute") or [])
        lines.append(f"| {case.get('id', '')} | {case.get('title', '')} | {case.get('issue_requirement', '')} | {artifacts} | `{command}` |")

    lines.extend(["", "## Execution Results", "", "| ID | Status | Exit Code | Duration | Test Artifacts | Logs |", "| --- | --- | --- | --- | --- | --- |"])
    for item in case_results:
        artifacts = ", ".join(str(path) for path in item.get("test_artifacts_absolute") or absolute_artifact_paths(item.get("test_artifacts") or [], repo))
        logs = ", ".join(path for path in [item.get("stdout_log"), item.get("stderr_log")] if path)
        lines.append(f"| {item.get('id', '')} | {item.get('status', '')} | {item.get('exit_code')} | {item.get('duration_seconds')}s | {artifacts} | {logs} |")
    if not case_results:
        lines.append("| none | fail | n/a | n/a | n/a | No test cases executed. |")

    lines.extend(["", "## Functional Correctness Conclusion", ""])
    if verdict == "PASS":
        lines.append("PASS: all listed unit test cases executed successfully and directly validate the planned PR behavior.")
    else:
        lines.append("FAIL: unit-test evidence is missing or at least one listed unit test case failed or could not be executed.")
    lines.extend(["", "## Evidence Files", *bullet(evidence_files), "", "## Risks and Limitations", *bullet(limitations + warnings), "", "## Recommended Actions"])
    lines.append("- Proceed with normal code review using this unit-test evidence." if verdict == "PASS" else "- Fix failing tests or add executable focused unit tests that directly cover the PR behavior.")

    (out_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_json(
        out_dir / "report.json",
        {
            "schema_version": 1,
            "verdict": verdict,
            "target": {
                "pr": context.get("pr") or plan.get("pr"),
                "issue": context.get("issue") or plan.get("issue"),
                "repo": context.get("repo") or plan.get("repo") or results.get("repo"),
            },
            "changed_files": context.get("changed_files") or [],
            "test_cases": report_test_cases,
            "execution_results": case_results,
            "test_case_script_map": case_script_map,
            "preserved_test_scripts": saved_scripts,
            "preserved_test_script_sources": script_sources,
            "evidence_files": evidence_files,
            "warnings": warnings,
            "limitations": limitations,
        },
    )
    print(out_dir / "report.md")
    print(out_dir / "report.json")
    return 0 if verdict == "PASS" else 1


def command_execute_report(ns: argparse.Namespace) -> int:
    execute_code = command_execute(ns)
    if execute_code != 0 and execute_code != 1:
        return execute_code
    return command_report(ns)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate PR functionality with focused unit tests.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("collect", help="Collect PR, issue, diff, stack, and changed-file context.")
    p.add_argument("--pr", required=True)
    p.add_argument("--issue", required=True)
    p.add_argument("--repo", default=".")
    p.add_argument("--base", default="")
    p.add_argument("--head", default="")
    p.add_argument("--out-dir", default=".unit-test-report")
    p.add_argument("--gitcode-token", default="", help="GitCode access token for private/login-required PR or issue URLs. Prefer GITCODE_TOKEN env var for repeated use.")
    p.add_argument(
        "--prefer-local",
        action="store_true",
        help="Prefer local git diff over PR URL diff (default: PR URL diff when available).",
    )
    p.set_defaults(func=command_collect, prefer_local=False)

    p = sub.add_parser("init-plan", help="Create an editable unit test plan JSON.")
    p.add_argument("--out-dir", default=".unit-test-report")
    p.set_defaults(func=command_init_plan)

    p = sub.add_parser("execute", help="Execute planned unit tests sequentially.")
    p.add_argument("--out-dir", default=".unit-test-report")
    p.add_argument("--repo", default="")
    p.add_argument("--timeout", type=int, default=120)
    p.set_defaults(func=command_execute)

    p = sub.add_parser("report", help="Generate markdown and JSON reports.")
    p.add_argument("--out-dir", default=".unit-test-report")
    p.set_defaults(func=command_report)

    p = sub.add_parser("execute-report", help="Execute planned tests and generate reports.")
    p.add_argument("--out-dir", default=".unit-test-report")
    p.add_argument("--repo", default="")
    p.add_argument("--timeout", type=int, default=120)
    p.set_defaults(func=command_execute_report)
    return parser


def main() -> int:
    ns = build_parser().parse_args()
    return ns.func(ns)


if __name__ == "__main__":
    raise SystemExit(main())
