from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from jiuwenswarm.extensions.dolores.server.runtime.context_engine.processor.forked.offloader.rule_compression.common import (
    meets_savings_ratio,
    strip_display_line_prefixes
)
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.processor.forked.offloader.rule_compression.types import (
    ContentType,
    RuleCompressionResult,
    RuleContext,
)


@dataclass(frozen=True)
class SearchMatch:
    file_path: str
    line_number: int
    content: str
    original: str
    position: int
    score: float = 0.0


ERROR_SIGNAL_RE = re.compile(
    r"\b(error|failed|failure|traceback|exception|fatal|panic)\b",
    re.IGNORECASE,
)
WARNING_SIGNAL_RE = re.compile(r"\b(warn|warning|deprecated)\b", re.IGNORECASE)
IMPORTANCE_SIGNAL_RE = re.compile(r"\b(todo|fixme|important|critical)\b", re.IGNORECASE)

QUERY_CONTENT_HIT_SCORE = 0.25
QUERY_CONTENT_SCORE_CAP = 0.60
QUERY_PATH_HIT_SCORE = 0.15
QUERY_PATH_SCORE_CAP = 0.30
ERROR_SIGNAL_SCORE = 0.50
WARNING_SIGNAL_SCORE = 0.35
IMPORTANCE_SIGNAL_SCORE = 0.25
MAX_SEARCH_MATCH_SCORE = 1.0
HIGH_SIGNAL_SCORE_THRESHOLD = 0.5


class SearchResultsCompressor:
    def compress(self, content: str, ctx: RuleContext) -> RuleCompressionResult:
        compressed, lossy, details = self._compress(content, ctx)
        return RuleCompressionResult(
            content=compressed,
            content_type=ContentType.SEARCH_RESULTS,
            modified=compressed != content,
            lossy=lossy,
            details=details,
        )

    def _compress(
        self,
        content: str,
        ctx: RuleContext,
    ) -> tuple[str, bool, dict[str, Any]]:
        grouped: dict[str, list[SearchMatch]] = {}
        order: list[str] = []
        unparsed_lines: list[str] = []
        normalized_content = strip_display_line_prefixes(content)
        for position, line in enumerate(normalized_content.splitlines()):
            match = parse_search_match(line, position)
            if not match:
                if line.strip():
                    unparsed_lines.append(line)
                continue
            if match.file_path not in grouped:
                grouped[match.file_path] = []
                order.append(match.file_path)
            grouped[match.file_path].append(match)
        if not grouped:
            return content, False, {}

        scored_groups = {
            file_path: [self._score_match(match, ctx.query_terms) for match in matches]
            for file_path, matches in grouped.items()
        }
        file_positions = {file_path: index for index, file_path in enumerate(order)}
        ranked_files = sorted(
            order,
            key=lambda file_path: (
                -sum(match.score for match in scored_groups[file_path]),
                file_positions[file_path],
            ),
        )
        selected_files = ranked_files[:ctx.search_max_files]
        original_match_count = sum(len(matches) for matches in scored_groups.values())
        adaptive_limit = self._adaptive_limit(scored_groups, ctx.search_max_total_matches)

        lines = ["[SEARCH_RESULTS compressed]", *unparsed_lines]
        selected_count = 0
        processed_files = 0
        omitted_any = len(selected_files) < len(order)
        for file_path in selected_files:
            remaining = adaptive_limit - selected_count
            if remaining <= 0:
                omitted_any = True
                break
            processed_files += 1
            matches = scored_groups[file_path]
            per_file_limit = min(ctx.search_max_matches_per_file, remaining)
            kept = self._select_matches(matches, per_file_limit)
            lines.extend(match.original for match in kept)
            selected_count += len(kept)
            omitted = len(matches) - len(kept)
            if omitted > 0:
                omitted_any = True
                lines.append(f"[... and {omitted} more matches in {file_path}]")

        omitted_files = len(order) - processed_files
        if omitted_files > 0:
            omitted_any = True
            lines.append(f"[... and {omitted_files} more files omitted]")

        details = {
            "original_match_count": original_match_count,
            "retained_match_count": selected_count,
            "omitted_match_count": max(original_match_count - selected_count, 0),
            "files_affected": len(order),
            "files_retained": processed_files,
            "unparsed_line_count": len(unparsed_lines),
            "adaptive_match_limit": adaptive_limit,
        }
        candidate = "\n".join(lines)
        if omitted_any and meets_savings_ratio(content, candidate, ctx):
            return candidate, True, details
        return content, False, details

    def _adaptive_limit(
        self,
        grouped: dict[str, list[SearchMatch]],
        hard_limit: int,
    ) -> int:
        matches = [match for file_matches in grouped.values() for match in file_matches]
        total = len(matches)
        if total <= 0:
            return 0
        normalized_contents = {self._normalize_content(match.content) for match in matches}
        diversity_ratio = len(normalized_contents) / total
        base_limit = min(5, total)
        diversity_slots = round((min(total, hard_limit) - base_limit) * diversity_ratio)
        high_signal_count = sum(1 for match in matches if match.score >= HIGH_SIGNAL_SCORE_THRESHOLD)
        signal_floor = min(base_limit + high_signal_count, hard_limit, total)
        return min(max(base_limit + diversity_slots, signal_floor), hard_limit, total)

    @staticmethod
    def _normalize_content(content: str) -> str:
        lowered = re.sub(r"\b\d+\b", "<n>", content.lower())
        return " ".join(lowered.split())

    @staticmethod
    def _score_match(match: SearchMatch, query_terms: frozenset[str]) -> SearchMatch:
        lowered_content = match.content.lower()
        lowered_path = match.file_path.lower()
        content_hits = sum(1 for term in query_terms if term in lowered_content)
        path_hits = sum(1 for term in query_terms if term in lowered_path)

        score = 0.0
        score += min(content_hits * QUERY_CONTENT_HIT_SCORE, QUERY_CONTENT_SCORE_CAP)
        score += min(path_hits * QUERY_PATH_HIT_SCORE, QUERY_PATH_SCORE_CAP)

        if ERROR_SIGNAL_RE.search(match.content):
            score += ERROR_SIGNAL_SCORE
        elif WARNING_SIGNAL_RE.search(match.content):
            score += WARNING_SIGNAL_SCORE
        elif IMPORTANCE_SIGNAL_RE.search(match.content):
            score += IMPORTANCE_SIGNAL_SCORE
        score = min(score, MAX_SEARCH_MATCH_SCORE)
        return SearchMatch(
            file_path=match.file_path,
            line_number=match.line_number,
            content=match.content,
            original=match.original,
            position=match.position,
            score=score,
        )

    @staticmethod
    def _select_matches(matches: list[SearchMatch], limit: int) -> list[SearchMatch]:
        if limit <= 0:
            return []
        if len(matches) <= limit:
            return sorted(matches, key=lambda match: match.position)

        selected: dict[int, SearchMatch] = {}
        for match in (matches[0], matches[-1]):
            if len(selected) < limit:
                selected[match.position] = match
        for match in sorted(matches, key=lambda item: (-item.score, item.position)):
            if len(selected) >= limit:
                break
            selected.setdefault(match.position, match)
        return sorted(selected.values(), key=lambda match: match.position)


def parse_search_match(line: str, position: int = 0) -> SearchMatch | None:
    normalized_line = strip_display_line_prefixes(line)
    drive_prefix_end = 2 if re.match(r"^[A-Za-z]:[\\/]", normalized_line) else 0
    for marker in re.finditer(r"([:-])(\d+)([:-])", normalized_line[drive_prefix_end:]):
        start = drive_prefix_end + marker.start()
        end = drive_prefix_end + marker.end()
        file_path = normalized_line[:start]
        if not file_path:
            continue
        return SearchMatch(
            file_path=file_path,
            line_number=int(marker.group(2)),
            content=normalized_line[end:],
            original=normalized_line,
            position=position,
        )
    return None
