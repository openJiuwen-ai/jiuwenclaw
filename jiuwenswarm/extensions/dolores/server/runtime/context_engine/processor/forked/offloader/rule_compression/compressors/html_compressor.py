from __future__ import annotations

from dataclasses import dataclass, field
from importlib import import_module
from importlib.util import find_spec
import re
from typing import Any

from bs4 import BeautifulSoup, NavigableString, Tag

from jiuwenswarm.extensions.dolores.server.runtime.context_engine.processor.forked.offloader.rule_compression.common import meets_savings_ratio
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.processor.forked.offloader.rule_compression.types import (
    ContentType,
    RuleCompressionResult,
    RuleContext,
)


@dataclass(frozen=True)
class HTMLExtractorConfig:
    output_format: str = "markdown"
    include_links: bool = True
    include_images: bool = False
    include_tables: bool = True
    include_comments: bool = False
    include_formatting: bool = True
    favor_precision: bool = False
    favor_recall: bool = True
    extract_metadata: bool = True


@dataclass(frozen=True)
class HTMLExtractionResult:
    extracted: str
    original: str
    original_length: int
    extracted_length: int
    compression_ratio: float
    title: str | None = None
    author: str | None = None
    date: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def reduction_percent(self) -> float:
        return (1 - self.compression_ratio) * 100


class HTMLExtractor:
    def __init__(self, config: HTMLExtractorConfig | None = None) -> None:
        self.config = config or HTMLExtractorConfig()
        self._trafilatura_config = self._build_trafilatura_config()

    def extract(self, html: str, url: str | None = None) -> HTMLExtractionResult:
        trafilatura = import_module("trafilatura")
        extracted = trafilatura.extract(
            html,
            url=url,
            include_links=self.config.include_links,
            include_images=self.config.include_images,
            include_tables=self.config.include_tables,
            include_comments=self.config.include_comments,
            include_formatting=self.config.include_formatting,
            output_format=self.config.output_format,
            config=self._trafilatura_config,
        )
        if extracted is None:
            extracted = ""

        metadata: dict[str, Any] = {}
        title = author = date = None
        if self.config.extract_metadata and hasattr(trafilatura, "extract_metadata"):
            meta = trafilatura.extract_metadata(html, default_url=url)
            if meta is not None:
                title = getattr(meta, "title", None)
                author = getattr(meta, "author", None)
                date = getattr(meta, "date", None)
                metadata = {
                    "title": title,
                    "author": author,
                    "date": date,
                    "sitename": getattr(meta, "sitename", None),
                    "description": getattr(meta, "description", None),
                    "categories": getattr(meta, "categories", None),
                    "tags": getattr(meta, "tags", None),
                }

        original_length = len(html)
        extracted_length = len(extracted)
        compression_ratio = extracted_length / max(original_length, 1)
        return HTMLExtractionResult(
            extracted=extracted,
            original=html,
            original_length=original_length,
            extracted_length=extracted_length,
            compression_ratio=compression_ratio,
            title=title,
            author=author,
            date=date,
            metadata=metadata,
        )

    def extract_batch(self, html_contents: list[tuple[str, str | None]]) -> list[HTMLExtractionResult]:
        return [self.extract(html, url) for html, url in html_contents]

    def _build_trafilatura_config(self) -> Any:
        try:
            from trafilatura.settings import use_config

            config = use_config()
        except Exception:
            import configparser

            config = configparser.ConfigParser()
        config.set("DEFAULT", "FAVOR_PRECISION", str(self.config.favor_precision))
        config.set("DEFAULT", "FAVOR_RECALL", str(self.config.favor_recall))
        return config


_MAIN_SELECTORS = (
    "article",
    "main",
    '[role="main"]',
    ".article-body",
    ".post-content",
    ".entry-content",
    ".post-body",
    "#content",
    ".main-content",
)
_NOISE_TAGS = ("script", "style", "nav", "footer", "aside", "form", "noscript", "template", "svg")
_NOISE_NAME_RE = re.compile(
    r"(^|[-_])(ad|ads|advert|banner|breadcrumb|cookie|footer|header|menu|nav|newsletter|"
    r"promo|related|share|sidebar|social|sponsor|toolbar)([-_]|$)",
    re.IGNORECASE,
)
_HIDDEN_STYLE_RE = re.compile(r"(?:display\s*:\s*none|visibility\s*:\s*hidden)", re.IGNORECASE)
_HTML_CONTENT_START_RE = re.compile(
    r"^\s*</?(?:article|main|section|div|table|thead|tbody|tr|th|td|ul|ol|li|p|pre|code|strong|span|h[1-6])\b",
    re.IGNORECASE,
)
_CSS_FRAGMENT_LINE_RE = re.compile(r"^\s*(?:[.#:@\w-][^<>]*\{|[-\w]+\s*:\s*[^;]+;|}|/\*|\*/)")
_BLOCK_TAGS = {
    "article",
    "blockquote",
    "div",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "main",
    "ol",
    "p",
    "pre",
    "section",
    "table",
    "ul",
}


class HtmlCompressor:
    def __init__(self, extractor: HTMLExtractor | None = None) -> None:
        self._extractor = extractor or HTMLExtractor()

    def compress(self, content: str, ctx: RuleContext) -> RuleCompressionResult:
        if _looks_like_report_html(content):
            return self._compress_with_beautifulsoup_fallback(
                content,
                ctx,
                extractor_label="beautifulsoup:report",
            )
        extractor_content = _remove_extractor_noise_html(content)
        try:
            extracted = self._extractor.extract(extractor_content)
        except ModuleNotFoundError:
            return self._compress_with_beautifulsoup_fallback(content, ctx)
        except Exception:
            return _unchanged(content)

        candidate = extracted.extracted.strip()
        original_length = len(content)
        details: dict[str, Any] = {
            "extractor": "trafilatura",
            "original_length": original_length,
            "extracted_length": extracted.extracted_length,
            "compression_ratio": extracted.extracted_length / max(original_length, 1),
            "reduction_percent": (1 - extracted.extracted_length / max(original_length, 1)) * 100,
            "title": extracted.title,
            "author": extracted.author,
            "date": extracted.date,
            "metadata": extracted.metadata,
        }
        if not candidate or len(_plain_text(candidate)) < ctx.html_min_content_chars:
            if _looks_like_report_html(content):
                return self._compress_with_beautifulsoup_fallback(
                    content,
                    ctx,
                    extractor_label="beautifulsoup:report",
                )
            return RuleCompressionResult(
                content=content,
                content_type=ContentType.HTML,
                modified=False,
                lossy=False,
                details=details,
            )
        if candidate != content and meets_savings_ratio(content, candidate, ctx):
            return RuleCompressionResult(
                content=candidate,
                content_type=ContentType.HTML,
                modified=True,
                lossy=True,
                details=details,
            )
        return RuleCompressionResult(
            content=content,
            content_type=ContentType.HTML,
            modified=False,
            lossy=False,
            details=details,
        )

    @staticmethod
    def _compress_with_beautifulsoup_fallback(
        content: str,
        ctx: RuleContext,
        *,
        extractor_label: str = "beautifulsoup",
    ) -> RuleCompressionResult:
        try:
            soup = _parse_html(content)
        except Exception:
            return _unchanged(content)

        title = _extract_title(soup)
        is_report = extractor_label.endswith(":report")
        removed_node_count = _remove_report_noise(soup) if is_report else _remove_noise(soup)
        report_rows_removed = _prune_report_result_rows(soup) if is_report else 0
        if is_report:
            body = soup.find("body")
            main = body if isinstance(body, Tag) else None
            source = "body:report" if main is not None else "none"
        else:
            main, source = _find_main_content(soup, ctx.html_min_content_chars)
        if main is None:
            return _unchanged(content)

        blocks = _render_container(main)
        if title and not _contains_title(blocks, title):
            blocks.insert(0, f"# {title}")
        blocks, duplicate_count = _deduplicate_blocks(blocks)
        candidate = "\n\n".join(blocks).strip()
        if len(_plain_text(candidate)) < ctx.html_min_content_chars:
            return _unchanged(content)

        details: dict[str, Any] = {
            "extractor": extractor_label,
            "main_content_source": source,
            "removed_node_count": removed_node_count,
            "report_rows_removed": report_rows_removed,
            "duplicate_block_count": duplicate_count,
            "link_count": len(main.find_all("a")),
            "table_count": len(main.find_all("table")),
            "code_block_count": len(main.find_all("pre")),
            "output_block_count": len(blocks),
        }
        if candidate != content and meets_savings_ratio(content, candidate, ctx):
            return RuleCompressionResult(
                content=candidate,
                content_type=ContentType.HTML,
                modified=True,
                lossy=True,
                details=details,
            )
        return RuleCompressionResult(
            content=content,
            content_type=ContentType.HTML,
            modified=False,
            lossy=False,
            details=details,
        )


def _parse_html(content: str) -> BeautifulSoup:
    if find_spec("lxml") is not None:
        return BeautifulSoup(content, "lxml")
    return BeautifulSoup(content, "html.parser")


def _looks_like_report_html(content: str) -> bool:
    lowered = content[:20000].lower()
    report_markers = (
        "pytest-html",
        "results-table",
        "environment",
        "test results",
        "tests took",
        "report generated",
    )
    if "pytest-html" in lowered or "results-table" in lowered:
        return True
    return lowered.count("<table") >= 2 and sum(marker in lowered for marker in report_markers) >= 2


def _remove_extractor_noise_html(content: str) -> str:
    content = _strip_leading_stylesheet_fragment(content)
    try:
        soup = _parse_html(content)
    except Exception:
        return content
    removed = False
    for node in list(soup.find_all(_NOISE_TAGS)):
        node.decompose()
        removed = True
    return str(soup) if removed else content


def _strip_leading_stylesheet_fragment(content: str) -> str:
    lines = content.splitlines()
    first_content_index = None
    for index, line in enumerate(lines):
        if _HTML_CONTENT_START_RE.search(line):
            first_content_index = index
            break
    if first_content_index is None or first_content_index == 0:
        return content
    leading = lines[:first_content_index]
    css_like_count = sum(1 for line in leading if _CSS_FRAGMENT_LINE_RE.search(line))
    if css_like_count < 2:
        return content
    return "\n".join(lines[first_content_index:])


def _extract_title(soup: BeautifulSoup) -> str:
    meta = soup.find("meta", property="og:title")
    if isinstance(meta, Tag):
        value = meta.get("content")
        if isinstance(value, str) and value.strip():
            return _normalize_text(value)
    title = soup.find("title")
    return _normalize_text(title.get_text(" ", strip=True)) if isinstance(title, Tag) else ""


def _remove_noise(soup: BeautifulSoup) -> int:
    removed = 0
    for node in list(soup.find_all(_NOISE_TAGS)):
        node.decompose()
        removed += 1
    for node in list(soup.find_all(True)):
        if node.parent is None:
            continue
        classes = " ".join(str(value) for value in node.get("class", []))
        identifier = str(node.get("id", ""))
        style = str(node.get("style", ""))
        is_hidden = (
            node.has_attr("hidden")
            or str(node.get("aria-hidden", "")).lower() == "true"
            or bool(_HIDDEN_STYLE_RE.search(style))
        )
        if is_hidden or _NOISE_NAME_RE.search(classes) or _NOISE_NAME_RE.search(identifier):
            node.decompose()
            removed += 1
    return removed


def _remove_report_noise(soup: BeautifulSoup) -> int:
    removed = 0
    for node in list(soup.find_all(_NOISE_TAGS)):
        node.decompose()
        removed += 1
    return removed


_REPORT_ROW_KEEP_RE = re.compile(
    r"\b(fail(?:ed|ure)?|error|broken|skipped|xfailed|xpassed|rerun|unexpected|assertionerror|traceback)\b",
    re.IGNORECASE,
)
_REPORT_ROW_DROP_RE = re.compile(r"\b(pass(?:ed)?|ok)\b", re.IGNORECASE)


def _prune_report_result_rows(soup: BeautifulSoup) -> int:
    rows = [row for row in soup.find_all("tr") if isinstance(row, Tag)]
    data_rows = [
        row
        for row in rows
        if row.find_all("td", recursive=False)
    ]
    if not any(_REPORT_ROW_KEEP_RE.search(row.get_text(" ", strip=True)) for row in data_rows):
        return 0

    removed = 0
    for row in data_rows:
        text = _normalize_text(row.get_text(" ", strip=True))
        if _REPORT_ROW_KEEP_RE.search(text):
            continue
        if _REPORT_ROW_DROP_RE.search(text) or _looks_like_test_result_row(text):
            row.decompose()
            removed += 1
    return removed


def _looks_like_test_result_row(text: str) -> bool:
    return "::test" in text or text.startswith("tests/")


def _find_main_content(soup: BeautifulSoup, min_chars: int) -> tuple[Tag | None, str]:
    for selector in _MAIN_SELECTORS:
        node = soup.select_one(selector)
        if isinstance(node, Tag) and _text_length(node) >= min_chars:
            return node, selector

    body = soup.find("body")
    if not isinstance(body, Tag):
        return None, "none"
    candidates = [
        node
        for node in body.find_all(("article", "main", "section", "div"))
        if isinstance(node, Tag) and _text_length(node) >= min_chars
    ]
    if not candidates:
        return (body, "body") if _text_length(body) >= min_chars else (None, "none")
    selected = max(candidates, key=_content_score)
    return selected, f"density:{selected.name}"


def _content_score(node: Tag) -> int:
    text_length = _text_length(node)
    link_length = sum(len(link.get_text(" ", strip=True)) for link in node.find_all("a"))
    structural_bonus = 30 * len(node.find_all(("p", "pre", "table", "li"), recursive=True))
    return text_length - 2 * link_length + structural_bonus


def _text_length(node: Tag) -> int:
    return len(_normalize_text(node.get_text(" ", strip=True)))


def _render_container(node: Tag) -> list[str]:
    blocks: list[str] = []
    for child in node.children:
        if isinstance(child, NavigableString):
            text = _normalize_text(str(child))
            if text:
                blocks.append(text)
        elif isinstance(child, Tag):
            blocks.extend(_render_tag(child))
    return [block for block in blocks if block.strip()]


def _render_tag(node: Tag) -> list[str]:
    name = node.name.lower()
    if name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        text = _inline_text(node)
        return [f"{'#' * int(name[1])} {text}"] if text else []
    if name == "p":
        text = _inline_text(node)
        return [text] if text else []
    if name in {"ul", "ol"}:
        ordered = name == "ol"
        items = []
        for index, item in enumerate(node.find_all("li", recursive=False), 1):
            text = _inline_text(item)
            if text:
                items.append(f"{index}. {text}" if ordered else f"- {text}")
        return ["\n".join(items)] if items else []
    if name == "blockquote":
        text = _inline_text(node)
        return ["\n".join(f"> {line}" for line in text.splitlines())] if text else []
    if name == "pre":
        code = node.get_text("\n", strip=False).strip("\n")
        return [f"```\n{code}\n```"] if code.strip() else []
    if name == "table":
        table = _render_table(node)
        return [table] if table else []
    if name == "hr":
        return ["---"]
    if name in {"br"}:
        return []
    if name in {"article", "main", "section", "div", "body"}:
        return _render_container(node)
    if any(isinstance(child, Tag) and child.name.lower() in _BLOCK_TAGS for child in node.children):
        return _render_container(node)
    text = _inline_text(node)
    return [text] if text else []


def _inline_text(node: Tag) -> str:
    parts: list[str] = []
    for child in node.children:
        if isinstance(child, NavigableString):
            parts.append(str(child))
            continue
        if not isinstance(child, Tag):
            continue
        name = child.name.lower()
        if name == "a":
            label = _normalize_text(child.get_text(" ", strip=True))
            href = child.get("href")
            parts.append(f"[{label}]({href})" if label and isinstance(href, str) and href.strip() else label)
        elif name == "code" and node.name.lower() != "pre":
            code = _normalize_text(child.get_text(" ", strip=True))
            parts.append(f"`{code}`" if code else "")
        elif name == "br":
            parts.append("\n")
        else:
            parts.append(_inline_text(child))
    return _normalize_inline("".join(parts))


def _render_table(table: Tag) -> str:
    rows: list[list[str]] = []
    for row in table.find_all("tr"):
        cells = [
            _normalize_text(cell.get_text(" ", strip=True)).replace("|", r"\|")
            for cell in row.find_all(("th", "td"), recursive=False)
        ]
        if cells:
            rows.append(cells)
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    normalized = [row + [""] * (width - len(row)) for row in rows]
    header = normalized[0]
    lines = [
        f"| {' | '.join(header)} |",
        f"| {' | '.join('---' for _ in range(width))} |",
    ]
    lines.extend(f"| {' | '.join(row)} |" for row in normalized[1:])
    return "\n".join(lines)


def _deduplicate_blocks(blocks: list[str]) -> tuple[list[str], int]:
    seen: set[str] = set()
    kept: list[str] = []
    duplicates = 0
    for block in blocks:
        key = _normalize_text(block).lower()
        if not key:
            continue
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        kept.append(block.strip())
    return kept, duplicates


def _contains_title(blocks: list[str], title: str) -> bool:
    normalized = _normalize_text(title).lower()
    return any(_normalize_text(block.lstrip("# ")).lower() == normalized for block in blocks)


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _normalize_inline(text: str) -> str:
    lines = [_normalize_text(line) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _plain_text(markdown: str) -> str:
    return re.sub(r"[#>*_`\[\]()|\-]", " ", markdown)


def _unchanged(content: str) -> RuleCompressionResult:
    return RuleCompressionResult(
        content=content,
        content_type=ContentType.HTML,
        modified=False,
    )
