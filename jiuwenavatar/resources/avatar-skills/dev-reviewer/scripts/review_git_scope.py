#!/usr/bin/env python3
"""Git diff scope for review collect."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

RunFn = Callable[[list[str], Path | None, int], tuple[int, str, str]]
ChangedFromDiffFn = Callable[[str], list[str]]
ChangedFromStatusFn = Callable[[str], list[str]]

# Exit-code mapping in code_review_runner.command_collect
COLLECT_ERROR_NOT_REPO = "not_repo"
COLLECT_ERROR_MISSING_BASE = "missing_base"
COLLECT_ERROR_MISSING_HEAD = "missing_head"
COLLECT_ERROR_REQUIRES_REFS = "requires_refs"
COLLECT_ERROR_NO_DIFF = "no_diff"

COLLECT_FATAL_ERRORS = frozenset(
    {
        COLLECT_ERROR_NOT_REPO,
        COLLECT_ERROR_MISSING_BASE,
        COLLECT_ERROR_MISSING_HEAD,
        COLLECT_ERROR_REQUIRES_REFS,
        COLLECT_ERROR_NO_DIFF,
    }
)


@dataclass
class LocalDiffResult:
    diff_text: str = ""
    changed_files: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    scope: dict[str, Any] = field(default_factory=dict)
    error_code: str = ""

    @property
    def ok(self) -> bool:
        return not self.error_code or bool(self.diff_text or self.changed_files)


def _short(sha: str) -> str:
    return sha.strip()[:12] if sha else ""


def rev_parse(repo: Path, ref: str, run: RunFn, timeout: int = 30) -> str | None:
    code, out, _ = run(["git", "rev-parse", "--verify", ref], repo, timeout)
    return out.strip() if code == 0 else None


def collect_git_snapshot(repo: Path, run: RunFn, timeout: int = 30) -> dict[str, Any]:
    snap: dict[str, Any] = {
        "branch": "",
        "head_ref": "HEAD",
        "head_sha": "",
        "dirty": False,
    }
    code, branch, _ = run(["git", "branch", "--show-current"], repo, timeout)
    if code == 0:
        snap["branch"] = branch.strip()
    code, sha, _ = run(["git", "rev-parse", "HEAD"], repo, timeout)
    if code == 0:
        snap["head_sha"] = _short(sha)
    code, status, _ = run(["git", "status", "--porcelain"], repo, timeout)
    if code == 0:
        snap["dirty"] = bool(status.strip())
    return snap


def porcelain_status(repo: Path, run: RunFn, timeout: int = 30) -> tuple[str, str]:
    code, stdout, stderr = run(["git", "status", "--porcelain"], repo, timeout)
    if code != 0:
        return "", stderr.strip()
    return stdout, ""


def diff_untracked_file(repo: Path, path: str, run: RunFn, timeout: int = 30) -> str:
    file_path = repo / path
    if not file_path.is_file():
        return ""
    null_device = "NUL" if os.name == "nt" else "/dev/null"
    code, stdout, _ = run(["git", "diff", "--no-index", "--", null_device, path], repo, timeout)
    if stdout.strip():
        return stdout if stdout.endswith("\n") else stdout + "\n"
    if code not in (0, 1):
        return ""
    return ""


def _file_paths(repo: Path, paths: list[str]) -> list[str]:
    """Keep files only; skip directory-only status entries (e.g. ``?? doc/``)."""
    kept: list[str] = []
    for path in paths:
        target = repo / path
        if target.is_dir():
            continue
        kept.append(path)
    return kept


def supplement_untracked(
    repo: Path,
    run: RunFn,
    diff_text: str,
    files: list[str],
    changed_files_from_status: ChangedFromStatusFn,
    timeout: int = 30,
) -> tuple[str, list[str], list[str]]:
    warnings: list[str] = []
    porcelain, err = porcelain_status(repo, run, timeout)
    if err:
        warnings.append(err)
        return diff_text, files, warnings
    if not porcelain.strip():
        return diff_text, files, warnings

    status_files = _file_paths(repo, changed_files_from_status(porcelain))
    merged = sorted(set(files) | set(status_files))
    untracked = [p for p in status_files if p not in set(files)]
    if not untracked:
        return diff_text, merged, warnings

    parts = [diff_text] if diff_text.strip() else []
    for path in untracked:
        chunk = diff_untracked_file(repo, path, run, timeout)
        if chunk:
            parts.append(chunk)
        else:
            warnings.append(f"untracked file listed but no diff text: {path}")

    combined = "".join(parts)
    if untracked and not combined.strip():
        warnings.append("untracked files present; diff text may be incomplete")
    return combined, merged, warnings


def _collect_working_tree(
    repo: Path,
    run: RunFn,
    *,
    changed_files_from_diff: ChangedFromDiffFn,
    changed_files_from_status: ChangedFromStatusFn,
    scope: dict[str, Any],
    timeout: int,
) -> LocalDiffResult:
    warnings: list[str] = []

    for spec_args, label in (
        (["--cached"], "staged"),
        ([], "working-tree"),
        (["HEAD~1..HEAD"], "HEAD~1..HEAD"),
    ):
        code, stdout, stderr = run(["git", "diff", "--no-ext-diff", *spec_args], repo, timeout)
        if code == 0 and stdout.strip():
            files = changed_files_from_diff(stdout)
            if files:
                scope = {**scope, "diff_spec": label}
                diff_text, merged, extra = supplement_untracked(
                    repo,
                    run,
                    stdout,
                    files,
                    changed_files_from_status,
                    timeout=30,
                )
                warnings.extend(extra)
                return LocalDiffResult(diff_text, merged, warnings, scope)
        if stderr.strip():
            warnings.append(stderr.strip())

    code, stdout, stderr = run(["git", "status", "--short"], repo, 30)
    if code == 0 and stdout.strip():
        warnings.append("no diff text; using git status file list")
        scope = {**scope, "diff_spec": "status-only"}
        files = sorted(set(_file_paths(repo, changed_files_from_status(stdout))))
        diff_text, merged, extra = supplement_untracked(
            repo,
            run,
            "",
            files,
            changed_files_from_status,
            timeout=30,
        )
        warnings.extend(extra)
        return LocalDiffResult(diff_text, merged, warnings, scope)

    if stderr.strip():
        warnings.append(stderr.strip())
    return LocalDiffResult("", [], warnings, scope)


def local_git_diff(
    repo: Path,
    base: str,
    head: str,
    run: RunFn,
    *,
    allow_working_tree: bool,
    changed_files_from_diff: ChangedFromDiffFn,
    changed_files_from_status: ChangedFromStatusFn,
    timeout: int = 90,
) -> LocalDiffResult:
    warnings: list[str] = []
    scope: dict[str, Any] = {
        "diff_spec": "",
        "base_ref": base.strip(),
        "head_ref": head.strip(),
        "base_sha": "",
        "head_sha": "",
        "merge_base": "",
    }

    code, inside, err = run(["git", "rev-parse", "--is-inside-work-tree"], repo, 30)
    if code != 0 or inside.strip() != "true":
        return LocalDiffResult(
            "",
            [],
            [err.strip() or "not a git worktree"],
            scope,
            error_code=COLLECT_ERROR_NOT_REPO,
        )

    base_ref = base.strip()
    head_ref = head.strip()

    if base_ref and head_ref:
        base_sha = rev_parse(repo, base_ref, run)
        head_sha = rev_parse(repo, head_ref, run)
        if not base_sha:
            return LocalDiffResult(
                "",
                [],
                [f"--base not found: {base_ref}"],
                scope,
                error_code=COLLECT_ERROR_MISSING_BASE,
            )
        if not head_sha:
            return LocalDiffResult(
                "",
                [],
                [f"--head not found: {head_ref}"],
                scope,
                error_code=COLLECT_ERROR_MISSING_HEAD,
            )
        scope["base_sha"] = _short(base_sha)
        scope["head_sha"] = _short(head_sha)
        mb_code, mb_out, _ = run(["git", "merge-base", base_ref, head_ref], repo, 30)
        if mb_code == 0:
            scope["merge_base"] = _short(mb_out)

        for spec in (f"{base_ref}...{head_ref}", f"{base_ref}..{head_ref}"):
            code, stdout, stderr = run(["git", "diff", "--no-ext-diff", spec], repo, timeout)
            if code == 0 and stdout.strip():
                scope["diff_spec"] = spec
                diff_text, merged, extra = supplement_untracked(
                    repo,
                    run,
                    stdout,
                    changed_files_from_diff(stdout),
                    changed_files_from_status,
                    timeout=30,
                )
                warnings.extend(extra)
                return LocalDiffResult(diff_text, merged, warnings, scope)

            if stderr.strip():
                warnings.append(stderr.strip())

        porcelain, perr = porcelain_status(repo, run, 30)
        if perr:
            warnings.append(perr)
        status_files = _file_paths(repo, changed_files_from_status(porcelain)) if porcelain.strip() else []

        if base_sha == head_sha and porcelain.strip():
            warnings.append(
                "no diff between --base and --head (same commit); falling back to working tree"
            )
            wt = _collect_working_tree(
                repo,
                run,
                changed_files_from_diff=changed_files_from_diff,
                changed_files_from_status=changed_files_from_status,
                scope=scope,
                timeout=timeout,
            )
            if wt.diff_text or wt.changed_files:
                wt.warnings = warnings + wt.warnings
                return wt

        if base_sha == head_sha and status_files:
            scope["diff_spec"] = "status-only"
            diff_text, merged, extra = supplement_untracked(
                repo,
                run,
                "",
                status_files,
                changed_files_from_status,
                timeout=30,
            )
            warnings.extend(extra)
            if merged:
                warnings.append("no diff between --base and --head; using status file list")
                return LocalDiffResult(diff_text, merged, warnings, scope)

        return LocalDiffResult(
            "",
            [],
            warnings + ["no diff for --base/--head"],
            scope,
            error_code=COLLECT_ERROR_NO_DIFF,
        )

    if not allow_working_tree:
        return LocalDiffResult(
            "",
            [],
            ["local collect requires --base and --head"],
            scope,
            error_code=COLLECT_ERROR_REQUIRES_REFS,
        )

    wt = _collect_working_tree(
        repo,
        run,
        changed_files_from_diff=changed_files_from_diff,
        changed_files_from_status=changed_files_from_status,
        scope=scope,
        timeout=timeout,
    )
    wt.warnings = warnings + wt.warnings
    return wt


def file_set_differs(a: list[str], b: list[str]) -> list[str]:
    return sorted(set(a) ^ set(b))


def resolve_fetch_method(diff_text: str, scope: dict[str, Any]) -> str:
    if diff_text.strip():
        return "local-git-diff"
    spec = scope.get("diff_spec") or ""
    if spec == "status-only":
        return "status-only"
    return "none"
