from __future__ import annotations

from jiuwenswarm.extensions.dolores.server.runtime.context_engine.processor.forked.offloader.rule_compression.types import (
    ContentType,
    RuleCompressionResult,
    RuleContext,
)
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.processor.forked.offloader.rule_compression.common import meets_savings_ratio


class PlainTextCompressor:
    @staticmethod
    def compress(content: str, ctx: RuleContext) -> RuleCompressionResult:
        seen: set[str] = set()
        lines: list[str] = []
        for line in content.splitlines():
            normalized = " ".join(line.split())
            if normalized and normalized not in seen:
                seen.add(normalized)
                lines.append(normalized)
            elif not normalized and (not lines or lines[-1] != ""):
                lines.append("")
        compressed = "\n".join(lines).strip()
        if compressed == content or not meets_savings_ratio(content, compressed, ctx):
            return RuleCompressionResult(
                content=content,
                content_type=ContentType.PLAIN_TEXT,
                modified=False,
            )
        return RuleCompressionResult(
            content=compressed,
            content_type=ContentType.PLAIN_TEXT,
            modified=True,
        )
