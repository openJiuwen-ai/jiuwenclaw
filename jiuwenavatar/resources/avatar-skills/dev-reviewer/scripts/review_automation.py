#!/usr/bin/env python3
"""Optional automation helpers for dev-reviewer: lint, security-scan, performance-evidence."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any, Callable

RunFn = Callable[[list[str], Path | None, int], tuple[int, str, str]]

SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("private_key_block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("generic_api_key", re.compile(r"(?i)(api[_-]?key|secret[_-]?key|access[_-]?token)\s*[:=]\s*['\"][^'\"]{8,}['\"]")),
    ("password_assignment", re.compile(r"(?i)(password|passwd|pwd)\s*[:=]\s*['\"][^'\"]{4,}['\"]")),
]

HIGH_RISK_CONFIG_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("debug_enabled", re.compile(r"(?i)\bdebug\s*[:=]\s*true\b")),
    ("cors_wildcard", re.compile(r"(?i)(allowed[_-]?origins?|cors|access-control-allow-origin)\s*[:=]\s*['\"]?\*['\"]?")),
    ("disable_ssl_verify", re.compile(r"(?i)(verify\s*=\s*false|rejectUnauthorized\s*:\s*false|NODE_TLS_REJECT_UNAUTHORIZED\s*=\s*0)")),
    ("default_admin_password", re.compile(r"(?i)(admin|root)\s*[:=]\s*['\"](admin|password|123456)['\"]")),
]

CONFIG_GLOBS = (
    "**/application*.yml",
    "**/application*.yaml",
    "**/application*.properties",
    "**/.env*",
    "**/config*.json",
    "**/docker-compose*.yml",
    "**/docker-compose*.yaml",
)


def _tool_available(name: str) -> bool:
    return shutil.which(name) is not None


def _truncate(text: str, limit: int = 8000) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _is_under_repo(path: Path, repo: Path) -> bool:
    try:
        path.resolve().relative_to(repo.resolve())
        return True
    except ValueError:
        return False


def _resolve_repo_file(repo: Path, rel: str) -> Path | None:
    candidate = (repo / rel).resolve()
    if not _is_under_repo(candidate, repo) or not candidate.is_file():
        return None
    return candidate


def _lint_targets(changed_files: list[str] | None) -> list[str]:
    if changed_files:
        return changed_files
    return ["."]


def _scan_files_for_patterns(
    repo: Path,
    patterns: list[tuple[str, re.Pattern[str]]],
    *,
    paths: list[str] | None = None,
    max_files: int = 200,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    candidates: list[Path] = []
    skipped_outside_repo = 0
    repo = repo.resolve()

    if paths is not None:
        scan_mode = "changed_files"
        for rel in paths:
            resolved = _resolve_repo_file(repo, rel)
            if resolved is not None:
                candidates.append(resolved)
            elif rel.strip():
                skipped_outside_repo += 1
    else:
        scan_mode = "repo_wide_capped"
        for path in repo.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".woff", ".woff2", ".pdf", ".zip"}:
                continue
            if any(part in {".git", "node_modules", "dist", "build", "target", ".venv", "venv"} for part in path.parts):
                continue
            candidates.append(path)
            if len(candidates) >= max_files:
                break

    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = str(path.relative_to(repo)).replace("\\", "/")
        for name, pattern in patterns:
            if pattern.search(text):
                hits.append({"pattern": name, "file": rel})

    meta = {
        "mode": scan_mode,
        "files_scanned": len(candidates),
        "skipped_outside_repo": skipped_outside_repo,
        "max_files_cap": max_files if scan_mode == "repo_wide_capped" else None,
    }
    return hits, meta


def _secret_scan_evidence(meta: dict[str, Any], hit_count: int) -> str:
    mode = meta.get("mode")
    files_scanned = meta.get("files_scanned", 0)
    skipped = meta.get("skipped_outside_repo", 0)
    if mode == "changed_files":
        detail = f"changed_files mode; scanned {files_scanned} file(s)"
        if skipped:
            detail += f"; skipped {skipped} path(s) outside repo or missing"
    else:
        cap = meta.get("max_files_cap")
        detail = f"repo-wide capped scan; scanned {files_scanned} file(s) (cap {cap})"
    return f"{detail}; {hit_count} potential secret pattern hit(s) (manual confirmation required)."


def discover_lint_commands(
    repo: Path,
    stack: dict[str, list[str]],
    *,
    changed_files: list[str] | None = None,
) -> list[dict[str, Any]]:
    commands: list[dict[str, Any]] = []
    targets = _lint_targets(changed_files)

    if stack.get("python"):
        if _tool_available("ruff"):
            commands.append({"tool": "ruff", "command": ["ruff", "check", *targets], "kind": "lint"})
        elif _tool_available("pylint"):
            commands.append({"tool": "pylint", "command": ["pylint", *targets], "kind": "lint"})
        if (repo / "pyproject.toml").exists() and _tool_available("mypy"):
            mypy_targets = targets if changed_files else ["."]
            commands.append({"tool": "mypy", "command": ["mypy", *mypy_targets], "kind": "typecheck"})

    if stack.get("node"):
        pkg = repo / "package.json"
        eslint_config = any(
            (repo / name).exists()
            for name in ("eslint.config.js", "eslint.config.mjs", "eslint.config.cjs", ".eslintrc", ".eslintrc.json", ".eslintrc.js")
        )
        if pkg.exists() and (eslint_config or (repo / "node_modules" / ".bin" / "eslint").exists()):
            if (repo / "node_modules" / ".bin" / "eslint").exists():
                commands.append({"tool": "eslint", "command": ["npx", "--no-install", "eslint", *targets], "kind": "lint"})
            elif _tool_available("eslint"):
                commands.append({"tool": "eslint", "command": ["eslint", *targets], "kind": "lint"})
        if pkg.exists():
            if (repo / "node_modules" / ".bin" / "prettier").exists() or _tool_available("prettier"):
                commands.append(
                    {
                        "tool": "prettier",
                        "command": ["npx", "--no-install", "prettier", "--check", *targets],
                        "kind": "format",
                    }
                )
        if (repo / "tsconfig.json").exists():
            if (repo / "node_modules" / ".bin" / "tsc").exists() or _tool_available("tsc"):
                commands.append(
                    {"tool": "tsc", "command": ["npx", "--no-install", "tsc", "--noEmit"], "kind": "typecheck"}
                )

    if stack.get("java"):
        if (repo / "pom.xml").exists() and _tool_available("mvn"):
            commands.append({"tool": "maven-checkstyle", "command": ["mvn", "-q", "checkstyle:check"], "kind": "lint"})
        elif (repo / "build.gradle").exists() or (repo / "build.gradle.kts").exists():
            if _tool_available("gradle"):
                commands.append({"tool": "gradle-check", "command": ["gradle", "check"], "kind": "lint"})

    if stack.get("go") and _tool_available("golangci-lint"):
        commands.append({"tool": "golangci-lint", "command": ["golangci-lint", "run", "./..."], "kind": "lint"})

    if stack.get("rust") and _tool_available("cargo"):
        commands.append({"tool": "cargo-clippy", "command": ["cargo", "clippy", "--", "-D", "warnings"], "kind": "lint"})

    return commands


def run_lint(
    repo: Path,
    stack: dict[str, list[str]],
    run: RunFn,
    timeout: int,
    *,
    changed_files: list[str] | None = None,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for spec in discover_lint_commands(repo, stack, changed_files=changed_files):
        code, stdout, stderr = run(spec["command"], repo, timeout)
        results.append(
            {
                "tool": spec["tool"],
                "kind": spec.get("kind", "lint"),
                "command": " ".join(spec["command"]),
                "exit_code": code,
                "stdout": _truncate(stdout),
                "stderr": _truncate(stderr),
                "passed": code == 0,
            }
        )
    scope = "changed_files" if changed_files else "repo_root"
    notes = [
        "Lint runs are collect-only; project formatters/linters in CI take precedence.",
    ]
    if changed_files:
        notes.append(f"Scoped to {len(changed_files)} changed file(s) from context.json.")
    else:
        notes.append("No changed_files in context; lint tools ran against repo root (.).")
    payload: dict[str, Any] = {
        "schema_version": 1,
        "mode": "collect-only",
        "scope": scope,
        "tools_attempted": len(results),
        "tools_passed": sum(1 for item in results if item["passed"]),
        "results": results,
        "notes": notes,
    }
    if not results:
        payload["status"] = "no_tools"
    else:
        payload["status"] = "completed"
    return payload


def run_dependency_audit(repo: Path, stack: dict[str, list[str]], run: RunFn, timeout: int) -> dict[str, Any]:
    audits: list[dict[str, Any]] = []

    if stack.get("node") and (repo / "package.json").exists():
        cmd = ["npm", "audit", "--json"] if _tool_available("npm") else []
        if cmd:
            code, stdout, stderr = run(cmd, repo, timeout)
            summary = "npm audit completed"
            try:
                payload = json.loads(stdout) if stdout.strip() else {}
                metadata = payload.get("metadata") or {}
                vulns = metadata.get("vulnerabilities") or {}
                summary = f"npm audit vulnerabilities: {json.dumps(vulns, ensure_ascii=False)}"
            except json.JSONDecodeError:
                summary = _truncate(stdout or stderr)
            audits.append(
                {
                    "tool": "npm-audit",
                    "command": " ".join(cmd),
                    "exit_code": code,
                    "summary": summary,
                    "passed": code == 0,
                }
            )

    if stack.get("python"):
        if _tool_available("pip-audit"):
            code, stdout, stderr = run(["pip-audit"], repo, timeout)
            audits.append(
                {
                    "tool": "pip-audit",
                    "command": "pip-audit",
                    "exit_code": code,
                    "summary": _truncate(stdout or stderr),
                    "passed": code == 0,
                }
            )
        elif (repo / "requirements.txt").exists() or (repo / "pyproject.toml").exists():
            audits.append(
                {
                    "tool": "pip-audit",
                    "command": "pip-audit",
                    "exit_code": None,
                    "summary": "pip-audit not installed; skipped CVE scan (install pip-audit for dependency audit).",
                    "passed": None,
                    "status": "skipped",
                }
            )

    if stack.get("rust") and _tool_available("cargo"):
        code, stdout, stderr = run(["cargo", "audit"], repo, timeout)
        audits.append(
            {
                "tool": "cargo-audit",
                "command": "cargo audit",
                "exit_code": code,
                "summary": _truncate(stdout or stderr),
                "passed": code == 0,
            }
        )

    lock_files = [
        name
        for name in ("package-lock.json", "pnpm-lock.yaml", "yarn.lock", "poetry.lock", "Pipfile.lock", "Cargo.lock")
        if (repo / name).exists()
    ]
    return {
        "schema_version": 1,
        "lock_files_present": lock_files,
        "audits": audits,
    }


def _dependency_audit_pass(audits: list[dict[str, Any]], lock_files: list[str]) -> tuple[bool, str]:
    if audits:
        summaries: list[str] = []
        has_failure = False
        has_success = False
        for audit in audits:
            tool = audit.get("tool")
            passed = audit.get("passed")
            status = audit.get("status") or "completed"
            exit_code = audit.get("exit_code")
            if passed is False:
                has_failure = True
                summaries.append(f"{tool}: FAIL (exit={exit_code})")
            elif passed is True:
                has_success = True
                summaries.append(f"{tool}: PASS (exit={exit_code})")
            else:
                summaries.append(f"{tool}: skipped ({audit.get('summary', status)})")
        if has_failure:
            return False, "; ".join(summaries)
        if has_success:
            return True, "; ".join(summaries)
        if lock_files:
            return False, f"lock_files={lock_files}; no audit tool ran; audit_not_executed"
        return False, "; ".join(summaries) if summaries else "No dependency audit tool available."

    if lock_files:
        return False, f"lock_files={lock_files}; no audit tool ran; audit_not_executed"
    return False, "No dependency audit tool available and no lock files present."


def run_security_scan(
    repo: Path,
    stack: dict[str, list[str]],
    run: RunFn,
    timeout: int,
    *,
    changed_files: list[str] | None = None,
) -> dict[str, Any]:
    secret_paths = changed_files if changed_files else None
    secret_hits, secret_meta = _scan_files_for_patterns(repo, SECRET_PATTERNS, paths=secret_paths)
    config_hits: list[dict[str, Any]] = []
    for glob in CONFIG_GLOBS:
        for path in repo.glob(glob):
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            rel = str(path.relative_to(repo)).replace("\\", "/")
            for name, pattern in HIGH_RISK_CONFIG_PATTERNS:
                if pattern.search(text):
                    config_hits.append({"pattern": name, "file": rel})

    dependency = run_dependency_audit(repo, stack, run, timeout)
    dep_pass, dep_summary = _dependency_audit_pass(dependency.get("audits") or [], dependency.get("lock_files_present") or [])
    return {
        "schema_version": 1,
        "notes": [
            "Secret/config pattern hits require manual confirmation; test fixtures often trigger false positives.",
        ],
        "secret_scan": {
            "hits": secret_hits,
            "passed": not secret_hits,
            "evidence": _secret_scan_evidence(secret_meta, len(secret_hits)),
            "scan_meta": secret_meta,
        },
        "config_scan": {
            "hits": config_hits,
            "passed": not config_hits,
            "evidence": f"Checked common config files; {len(config_hits)} high-risk pattern(s) (manual confirmation required).",
        },
        "dependency_audit": {**dependency, "passed": dep_pass, "summary": dep_summary},
    }


def security_scan_to_review_items(scan: dict[str, Any]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    secret = scan.get("secret_scan") or {}
    items.append(
        {
            "category": "secrets",
            "status": "PASS" if secret.get("passed") else "FAIL",
            "evidence": str(secret.get("evidence") or "secret scan not run"),
        }
    )
    config = scan.get("config_scan") or {}
    items.append(
        {
            "category": "config-hardening",
            "status": "PASS" if config.get("passed") else "FAIL",
            "evidence": str(config.get("evidence") or "config scan not run"),
        }
    )
    dep = scan.get("dependency_audit") or {}
    lock_files = dep.get("lock_files_present") or []
    dep_pass = dep.get("passed")
    if dep_pass is None:
        dep_pass, dep_summary = _dependency_audit_pass(dep.get("audits") or [], lock_files)
    else:
        dep_summary = str(dep.get("summary") or "")
    items.append(
        {
            "category": "dependencies",
            "status": "PASS" if dep_pass else "FAIL",
            "evidence": f"lock_files={lock_files or 'none'}; {dep_summary}",
        }
    )
    return items


def merge_security_scan_into_result(result_path: Path, scan: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not result_path.is_file():
        errors.append(f"Missing {result_path}; run init-review first.")
        return errors
    try:
        review = json.loads(result_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        errors.append(f"Invalid JSON in {result_path}: {exc}")
        return errors
    if not isinstance(review, dict):
        errors.append(f"{result_path} must contain a JSON object.")
        return errors
    items = security_scan_to_review_items(scan)
    security = review.setdefault("security_review", {})
    if not isinstance(security, dict):
        errors.append(f"{result_path} security_review must be an object.")
        return errors
    existing = security.get("items") or []
    if not isinstance(existing, list):
        existing = []
    auto_categories = {item["category"] for item in items}
    merged = [item for item in existing if _norm_category(item) not in auto_categories]
    merged.extend(items)
    security["items"] = merged
    if any(item.get("status") == "FAIL" for item in items):
        security["status"] = "FAIL"
    elif not security.get("status") or security.get("status") == "not_applicable":
        security["status"] = "PASS"
    result_path.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
    return errors


def _norm_category(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("category") or "").strip().lower()
    return ""


def build_performance_evidence(repo: Path, stack: dict[str, list[str]], context: dict[str, Any]) -> dict[str, Any]:
    template_path = Path(__file__).resolve().parent.parent / "assets" / "performance_evidence_template.json"
    try:
        payload = json.loads(template_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        payload = {"schema_version": 1, "measurements": [], "suggested_commands": [], "apm_queries": []}

    changed = context.get("changed_files") or []
    domains: list[str] = []
    if stack.get("node"):
        domains.append("frontend/web")
    if stack.get("python") or stack.get("java") or stack.get("go"):
        domains.append("api/backend")
    if not domains:
        domains.append("general")

    commands: list[dict[str, str]] = []
    if stack.get("node"):
        commands.extend(
            [
                {
                    "name": "bundle-size",
                    "command": "npx source-map-explorer 'dist/**/*.js' || du -sh dist/",
                    "reason": "Detect bundle regressions after frontend changes.",
                },
                {
                    "name": "lighthouse",
                    "command": "npx lighthouse <URL> --output=json --output-path=./lighthouse.json",
                    "reason": "Capture LCP/INP/CLS for user-facing pages.",
                },
            ]
        )
    if stack.get("python") or stack.get("java") or stack.get("go") or stack.get("node"):
        commands.extend(
            [
                {
                    "name": "api-benchmark",
                    "command": "ab -n 200 -c 10 http://localhost:<port>/<endpoint>",
                    "reason": "Simple latency/throughput smoke for changed API routes.",
                },
                {
                    "name": "k6-smoke",
                    "command": "k6 run --vus 5 --duration 30s script.js",
                    "reason": "Repeatable load test with thresholds (p95, error rate).",
                },
            ]
        )

    apm_queries = [
        {"system": "datadog", "query": "avg:trace.servlet.request.duration{service:<service>} by {resource_name}"},
        {"system": "prometheus", "query": "histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket[5m])) by (le))"},
        {"system": "generic", "query": "Compare p95 latency and error rate 24h before vs after deploy for changed endpoints."},
    ]

    payload["symptom_domains"] = domains
    payload["changed_files"] = changed
    payload["suggested_commands"] = commands
    payload["apm_queries"] = apm_queries
    payload["measurements"] = payload.get("measurements") or []
    return payload
