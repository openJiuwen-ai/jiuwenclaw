from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from typing import Any


_TERM_RE = re.compile(r"[A-Za-z0-9_]{3,}")
_PATH_ARGUMENT_KEYS = frozenset(
    {
        "file",
        "file_path",
        "filepath",
        "filename",
        "path",
        "paths",
    }
)
_NON_USER_INPUT_ARGUMENT_KEYS = frozenset(
    {
        "trusted_dirs",
        "preferred_response_language",
        "files_updated_by_user",
        "timezone",
        "timestamp",
        "encoding",
        "offset",
        "limit",
        "start",
        "end",
        "channel",
        "channels",
        "frontend",
        "session_id",
        "context_id",
        "request_id",
    }
)


def extract_query_terms(
    user_content: str,
    tool_name: str | None = None,
    tool_arguments: object | None = None,
) -> frozenset[str]:
    """Extract stable lowercase terms for rule-compression relevance scoring."""

    terms: set[str] = set()
    _add_terms(terms, user_content)
    if tool_name:
        _add_terms(terms, tool_name)
    _add_terms_from_value(terms, _parse_json_if_possible(tool_arguments))
    return frozenset(terms)


def _add_terms(terms: set[str], value: str) -> None:
    for match in _TERM_RE.finditer(value):
        start = match.start()
        if start > 0 and value[start - 1] in "\\/":
            continue
        terms.add(match.group(0).lower())


def _add_terms_from_value(terms: set[str], value: Any) -> None:
    if value is None:
        return
    if isinstance(value, str):
        _add_terms(terms, value)
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            _add_terms_from_value(terms, key)
            if isinstance(key, str) and key.lower() in _PATH_ARGUMENT_KEYS:
                continue
            if isinstance(key, str) and key.lower() in _NON_USER_INPUT_ARGUMENT_KEYS:
                continue
            _add_terms_from_value(terms, item)
        return
    if isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray)):
        for item in value:
            _add_terms_from_value(terms, item)
        return
    _add_terms(terms, str(value))


def _parse_json_if_possible(value: object | None) -> object | None:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value
