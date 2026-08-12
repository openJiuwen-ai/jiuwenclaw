from __future__ import annotations

import json
import re
from typing import Protocol

from json_repair import loads as repair_json_loads

from jiuwenswarm.extensions.dolores.server.runtime.context_engine.processor.forked.offloader.rule_compression.common import (
    strip_display_line_prefixes,
    strip_display_line_prefixes_preserving_body_whitespace,
)
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.processor.forked.offloader.rule_compression.types import (
    ContentType,
    RuleCompressionResult,
    RuleContext,
)

from .compressors.diff_compressor import DiffCompressor
from .compressors.html_compressor import HtmlCompressor
from .compressors.json_array_compressor import JsonArrayCompressor
from .compressors.log_compressor import LogCompressor
from .compressors.plain_text_compressor import PlainTextCompressor
from .compressors.search_results_compressor import SearchResultsCompressor


class RuleCompressor(Protocol):
    def compress(self, content: str, ctx: RuleContext) -> RuleCompressionResult:
        pass


class RuleContentRouter:
    """Detect content type and dispatch to one focused deterministic compressor."""

    _HTML_STRUCTURAL_TAG_RE = re.compile(
        r"</?(?:html|head|body|article|main|section|div|script|style|nav|footer|aside|"
        r"table|thead|tbody|tr|th|td|ul|ol|li|p|pre|code|strong|span|h[1-6])\b",
        re.IGNORECASE,
    )
    _SEARCH_LINE_RE = re.compile(r"^.+?:\d+[:\-].+$")
    _TIMESTAMP_LOG_LINE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}\b")
    _LOG_RE = re.compile(
        r"(?im)("
        r"^=+ (?:test session starts|failures|errors|short test summary info) =+$|"
        r"^collected \d+ items\b|"
        r"^Planning next action for user request:|"
        r"^Tool call requested:|"
        r"^Tool call completed:|"
        r"^Tool result received:|"
        r"^(?:FAILED|ERROR) .+::.+(?: - |$)|"
        r"^\d+\s+(?:failed|passed|skipped|errors?|warnings?)\b|"
        r"\b\d+\s+failed,\s+\d+\s+passed\b|"
        r"^npm (?:ERR!|WARN|info)\b|"
        r"^(?:PASS|FAIL) \S+|"
        r"^Test Suites:|"
        r"^(?:Compiling|Finished|Running) \S+|"
        r"^(?:error|warning)(?:\[[A-Z]\d+\])?:|"
        r"^(?:\[[A-Z]+\]\s*)?(?:ERROR|WARN|WARNING|INFO|DEBUG|TRACE|FATAL|CRITICAL)\b|"
        r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}.*\b(?:ERROR|WARN|WARNING|INFO|DEBUG|TRACE|FATAL|CRITICAL)\b|"
        r"^.+?:\d+(?::\d+)?:\s*(?:error|warning):|"
        r"^make(?:\[\d+\])?:|"
        r"^Traceback \(most recent call last\)|"
        r"^\s*File \".+\", line \d+|"
        r"^\s*at .+\(.+:\d+:\d+\)|"
        r"^-->\s+.+:\d+:\d+"
        r")"
    )

    def __init__(self) -> None:
        self._compressors: dict[ContentType, RuleCompressor] = {
            ContentType.JSON_ARRAY: JsonArrayCompressor(),
            ContentType.GIT_DIFF: DiffCompressor(),
            ContentType.HTML: HtmlCompressor(),
            ContentType.SEARCH_RESULTS: SearchResultsCompressor(),
            ContentType.LOG: LogCompressor(),
            ContentType.PLAIN_TEXT: PlainTextCompressor(),
        }

    def detect(self, content: str, ctx: RuleContext | None = None) -> ContentType:
        text = (content or "").strip()
        if not text:
            return ContentType.PLAIN_TEXT
        display_text = strip_display_line_prefixes(text)
        json_text = self._json_detection_text(text)
        try:
            parsed = json.loads(json_text)
        except (TypeError, ValueError):
            parsed = self._repair_json_array(json_text)
        if isinstance(parsed, list):
            return ContentType.JSON_ARRAY
        html_detection_text = self._html_detection_text(text)
        lowered = html_detection_text[:20000].lower()
        if self._looks_like_html(lowered):
            return ContentType.HTML
        if self._LOG_RE.search(display_text):
            return ContentType.LOG
        if re.search(r"(?m)^diff --git ", display_text) or re.search(r"(?m)^@@ .+ @@", display_text):
            return ContentType.GIT_DIFF
        lines = [line for line in display_text.splitlines() if line.strip()]
        if lines:
            matches = sum(
                1 for line in lines if self._SEARCH_LINE_RE.match(line) and not self._TIMESTAMP_LOG_LINE_RE.match(line)
            )
            if matches / len(lines) >= 0.3:
                return ContentType.SEARCH_RESULTS
        return ContentType.PLAIN_TEXT

    def compress(self, content: str, ctx: RuleContext) -> RuleCompressionResult:
        content_type = self.detect(content, ctx)
        if content_type == ContentType.GIT_DIFF:
            routed_content = strip_display_line_prefixes_preserving_body_whitespace(content)
        else:
            routed_content = (
                strip_display_line_prefixes(content)
                if content_type
                in {
                    ContentType.HTML,
                    ContentType.JSON_ARRAY,
                    ContentType.SEARCH_RESULTS,
                    ContentType.LOG,
                }
                else content
            )
        return self._compressors[content_type].compress(routed_content, ctx)

    @staticmethod
    def _json_detection_text(content: str) -> str:
        stripped = strip_display_line_prefixes(content)
        return stripped if stripped != content else content

    @staticmethod
    def _repair_json_array(content: str) -> object | None:
        if not content.lstrip().startswith("["):
            return None
        try:
            return repair_json_loads(content)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _html_detection_text(content: str) -> str:
        stripped = strip_display_line_prefixes(content)
        return stripped if stripped != content else content

    def _looks_like_html(self, content: str) -> bool:
        html_prefixes = ("<!doctype html", "<html", "<head", "<body")
        return content.startswith(html_prefixes) or len(self._HTML_STRUCTURAL_TAG_RE.findall(content)) >= 2


ContentRouter = RuleContentRouter
