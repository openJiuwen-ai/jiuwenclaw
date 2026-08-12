from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from jiuwenswarm.extensions.dolores.server.runtime.context_engine.processor.forked.offloader.rule_compression.common import (
    meets_savings_ratio,
)
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.processor.forked.offloader.rule_compression.types import (
    ContentType,
    RuleCompressionResult,
    RuleContext,
)


@dataclass(frozen=True)
class DiffHunk:
    header: str
    lines: tuple[str, ...]
    position: int

    @property
    def additions(self) -> int:
        return sum(1 for line in self.lines if _is_addition_line(line))

    @property
    def deletions(self) -> int:
        return sum(1 for line in self.lines if _is_deletion_line(line))

    @property
    def change_count(self) -> int:
        return self.additions + self.deletions


@dataclass(frozen=True)
class DiffFile:
    header: tuple[str, ...]
    hunks: tuple[DiffHunk, ...]
    position: int

    @property
    def label(self) -> str:
        return self.header[0] if self.header else f"diff-file-{self.position}"

    @property
    def additions(self) -> int:
        return sum(hunk.additions for hunk in self.hunks)

    @property
    def deletions(self) -> int:
        return sum(hunk.deletions for hunk in self.hunks)

    @property
    def change_count(self) -> int:
        return sum(hunk.change_count for hunk in self.hunks)


_PRIORITY_RE = re.compile(
    r"\b("
    r"error|exception|fail|failed|failure|fatal|critical|crash|panic|"
    r"important|note|todo|fixme|hack|xxx|bug|fix|"
    r"security|auth|password|secret|token"
    r")\b",
    re.IGNORECASE,
)
_CCR_RATIO_THRESHOLD = 0.8


class DiffCompressor:
    def compress(self, content: str, ctx: RuleContext) -> RuleCompressionResult:
        original_line_count = len(content.splitlines())
        if original_line_count < ctx.diff_min_lines:
            return _unchanged(content)
        preamble, files = _parse_diff(content)
        if not files:
            return _unchanged(content)

        selected_files = self._select_files(files, ctx)
        output = list(preamble)
        hunks_retained = 0
        hunks_omitted = 0
        context_lines_retained = 0
        context_lines_omitted = 0
        changed_lines_retained = 0
        changed_lines_omitted = 0
        changed_lines_remaining = max(ctx.diff_max_changed_lines_total, 0)
        additions = sum(file.additions for file in files)
        deletions = sum(file.deletions for file in files)

        for file in sorted(selected_files, key=lambda item: item.position):
            output.extend(file.header)
            selected_hunks = self._select_hunks(file.hunks, ctx)
            selected_positions = {hunk.position for hunk in selected_hunks}
            omitted_for_file = len(file.hunks) - len(selected_hunks)
            for hunk in file.hunks:
                if hunk.position not in selected_positions:
                    continue
                changed_limit = min(
                    max(ctx.diff_max_changed_lines_per_hunk, 0),
                    changed_lines_remaining,
                )
                (
                    reduced,
                    retained,
                    omitted,
                    changed_retained,
                    changed_omitted,
                ) = _reduce_hunk_lines(
                    hunk.lines,
                    ctx.diff_max_context_lines,
                    changed_limit,
                )
                changed_lines_remaining = max(changed_lines_remaining - changed_retained, 0)
                output.append(hunk.header)
                output.extend(reduced)
                if omitted:
                    output.append(
                        f"[{omitted} unchanged/context diff lines omitted]"
                    )
                if changed_omitted:
                    output.append(f"[{changed_omitted} changed diff lines omitted]")
                hunks_retained += 1
                context_lines_retained += retained
                context_lines_omitted += omitted
                changed_lines_retained += changed_retained
                changed_lines_omitted += changed_omitted
            if omitted_for_file:
                hunks_omitted += omitted_for_file

        files_omitted = len(files) - len(selected_files)
        output.append(
            f"[{len(files)} files changed, +{additions} -{deletions} lines, "
            f"{hunks_omitted} hunks omitted, {files_omitted} files omitted]"
        )

        candidate = "\n".join(output)
        compressed_line_count = len(candidate.splitlines())
        details: dict[str, Any] = {
            "files_affected": len(files),
            "files_retained": len(selected_files),
            "files_omitted": files_omitted,
            "hunks_affected": sum(len(file.hunks) for file in files),
            "hunks_retained": hunks_retained,
            "hunks_omitted": hunks_omitted,
            "additions": additions,
            "deletions": deletions,
            "context_lines_retained": context_lines_retained,
            "context_lines_omitted": context_lines_omitted,
            "changed_lines_retained": changed_lines_retained,
            "changed_lines_omitted": changed_lines_omitted,
            "original_line_count": original_line_count,
            "compressed_line_count": compressed_line_count,
            "should_offload_original": compressed_line_count < original_line_count * _CCR_RATIO_THRESHOLD,
        }
        lossy = (
            files_omitted > 0
            or hunks_omitted > 0
            or context_lines_omitted > 0
            or changed_lines_omitted > 0
        )
        if candidate != content and lossy and meets_savings_ratio(content, candidate, ctx):
            return RuleCompressionResult(
                content=candidate,
                content_type=ContentType.GIT_DIFF,
                modified=True,
                lossy=True,
                details=details,
            )
        return RuleCompressionResult(
            content=content,
            content_type=ContentType.GIT_DIFF,
            modified=False,
            lossy=False,
            details=details,
        )

    @staticmethod
    def _select_files(files: list[DiffFile], ctx: RuleContext) -> list[DiffFile]:
        if len(files) <= ctx.diff_max_files:
            return files
        return sorted(
            files,
            key=lambda file: (
                -file.change_count,
                file.position,
            ),
        )[: ctx.diff_max_files]

    @staticmethod
    def _select_hunks(hunks: tuple[DiffHunk, ...], ctx: RuleContext) -> list[DiffHunk]:
        limit = ctx.diff_max_hunks_per_file
        if len(hunks) <= limit:
            return list(hunks)

        selected: dict[int, DiffHunk] = {}
        for hunk in (hunks[0], hunks[-1]):
            if len(selected) < limit:
                selected[hunk.position] = hunk
        for hunk in sorted(
            hunks,
            key=lambda item: (
                -_score_text(
                    "\n".join((item.header, *item.lines)),
                    item.change_count,
                    ctx.query_terms,
                ),
                item.position,
            ),
        ):
            if len(selected) >= limit:
                break
            selected.setdefault(hunk.position, hunk)
        return sorted(selected.values(), key=lambda item: item.position)


def _parse_diff(content: str) -> tuple[tuple[str, ...], list[DiffFile]]:
    preamble: list[str] = []
    files: list[DiffFile] = []
    file_header: list[str] | None = None
    hunks: list[DiffHunk] = []
    hunk_header: str | None = None
    hunk_lines: list[str] = []

    def finish_hunk() -> None:
        nonlocal hunk_header, hunk_lines
        if hunk_header is not None:
            hunks.append(DiffHunk(hunk_header, tuple(hunk_lines), len(hunks)))
        hunk_header = None
        hunk_lines = []

    def finish_file() -> None:
        nonlocal file_header, hunks
        if file_header is not None:
            finish_hunk()
            files.append(DiffFile(tuple(file_header), tuple(hunks), len(files)))
        file_header = None
        hunks = []

    for line in content.splitlines():
        if _is_file_header(line):
            finish_file()
            file_header = [line]
        elif file_header is None:
            preamble.append(line)
        elif _is_hunk_header(line):
            finish_hunk()
            hunk_header = line
        elif hunk_header is None:
            file_header.append(line)
        else:
            hunk_lines.append(line)
    finish_file()
    return tuple(preamble), files


def _reduce_hunk_lines(
    lines: tuple[str, ...],
    max_context: int,
    max_changed: int,
) -> tuple[list[str], int, int, int, int]:
    change_positions = [index for index, line in enumerate(lines) if _is_change_line(line)]
    priority_changes = [
        index
        for index in change_positions
        if _PRIORITY_RE.search(lines[index])
    ]
    selected_changes: set[int] = set(priority_changes[:max_changed])
    for index in change_positions:
        if len(selected_changes) >= max_changed:
            break
        selected_changes.add(index)

    kept: list[str] = []
    context_retained = 0
    context_omitted = 0
    changed_retained = 0
    changed_omitted = 0
    for index, line in enumerate(lines):
        if _is_context_line(line):
            if any(abs(index - change) <= max_context for change in selected_changes):
                kept.append(line)
                context_retained += 1
            else:
                context_omitted += 1
        elif _is_change_line(line):
            if index in selected_changes:
                kept.append(line)
                changed_retained += 1
            else:
                changed_omitted += 1
        else:
            kept.append(line)
    return kept, context_retained, context_omitted, changed_retained, changed_omitted


def _score_text(text: str, change_count: int, query_terms: frozenset[str]) -> int:
    lowered = text.lower()
    score = min(0.3, change_count * 0.03)
    score += 0.2 * sum(1 for term in query_terms if term.lower() in lowered)
    score += 0.3 if _PRIORITY_RE.search(text) else 0
    return int(min(score, 1.0) * 1000)


def _file_text(file: DiffFile) -> str:
    lines = list(file.header)
    for hunk in file.hunks:
        lines.extend((hunk.header, *hunk.lines))
    return "\n".join(lines)


def _is_file_header(line: str) -> bool:
    return line.startswith(("diff --git ", "diff --cc ", "diff --combined "))


def _is_hunk_header(line: str) -> bool:
    return line.startswith(("@@ ", "@@@ "))


def _is_change_line(line: str) -> bool:
    return _is_addition_line(line) or _is_deletion_line(line)


def _is_addition_line(line: str) -> bool:
    return line.startswith("+") and not line.startswith("+++ ")


def _is_deletion_line(line: str) -> bool:
    return line.startswith("-") and not line.startswith("--- ")


def _is_context_line(line: str) -> bool:
    return (line.startswith(" ") or line == "") and not _is_change_line(line)


def _unchanged(content: str) -> RuleCompressionResult:
    return RuleCompressionResult(
        content=content,
        content_type=ContentType.GIT_DIFF,
        modified=False,
    )
