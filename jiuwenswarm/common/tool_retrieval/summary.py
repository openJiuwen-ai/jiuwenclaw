# -*- coding: utf-8 -*-
"""Tool-summary helpers — verbatim port of agent-core's ProgressiveToolRail
base methods, so the retrieval lib can build haystacks and output dicts without
injecting agent-core callables (keeps the lib self-contained / standalone).

Ports (logic identical to ProgressiveToolRail._parameters_summary /
_safe_serialize_parameters / _parameters_to_text / _build_tool_summary):
  parameters_summary / safe_serialize_parameters / parameters_to_text / build_tool_summary
"""
from __future__ import annotations

import inspect
import logging
import re
from typing import Any, Dict, List

from pydantic import BaseModel

logger = logging.getLogger("jiuwenswarm.common.tool_retrieval.summary")


def safe_serialize_parameters(parameters: Any) -> Any:
    try:
        if inspect.isclass(parameters) and issubclass(parameters, BaseModel):
            try:
                return parameters.model_json_schema()
            except Exception:
                return str(parameters)
        if isinstance(parameters, dict):
            return parameters
        return str(parameters)
    except Exception as exc:
        logger.warning("[tool_retrieval] failed to serialize parameters: %s", exc)
        return str(parameters)


def parameters_summary(parameters: Any) -> str:
    try:
        if inspect.isclass(parameters) and issubclass(parameters, BaseModel):
            fields = getattr(parameters, "model_fields", None)
            if isinstance(fields, dict):
                names = list(fields.keys())
                return f"fields: {', '.join(names)}" if names else "no declared fields"

        if isinstance(parameters, dict):
            props = parameters.get("properties")
            if isinstance(props, dict) and props:
                return f"fields: {', '.join(props.keys())}"
            if parameters:
                return f"schema keys: {', '.join(parameters.keys())}"
            return "empty schema"

        if parameters is None:
            return "no parameters"

        return str(parameters)
    except Exception as exc:
        logger.warning("[tool_retrieval] failed to summarize parameters: %s", exc)
        return "parameter summary unavailable"


def parameters_to_text(parameters: Any) -> str:
    summary = parameters_summary(parameters)
    raw = safe_serialize_parameters(parameters)
    return f"{summary} {raw}"


def build_tool_summary(tool: Any, *, detail_level: int = 1) -> Dict[str, Any]:
    name = str(getattr(tool, "name", "") or "")
    description = str(getattr(tool, "description", "") or "")
    parameters = getattr(tool, "parameters", None)

    payload: Dict[str, Any] = {
        "name": name,
        "description": description,
    }

    if detail_level >= 2:
        payload["parameter_summary"] = parameters_summary(parameters)

    if detail_level >= 3:
        payload["parameters"] = safe_serialize_parameters(parameters)

    return payload


# ---------------------------------------------------------------------------
# v2 phase 2: haystack cleaning helpers
# ---------------------------------------------------------------------------

# JSON-Schema structural keywords that carry no search signal — they appear
# in every parameter dict and would drown out the useful tokens (field names,
# descriptions, enum values). Excluded from the haystack.
_JSON_SCHEMA_NOISE_KEYS = frozenset({
    "type", "properties", "required", "default", "items",
    "description", "$schema", "additionalProperties",
    "minItems", "maxItems", "minLength", "maxLength",
    "minimum", "maximum", "pattern", "format",
    "anyOf", "allOf", "oneOf", "$ref", "title",
})

# CamelCase splitter: insert a space at a lowercase→uppercase boundary.
_CAMEL_BOUNDARY = re.compile(r"([a-z0-9])([A-Z])")


def _split_identifier(name: str) -> str:
    """Split a tool/field identifier into space-separated tokens.

    ``read_file`` → ``read file``
    ``sendEmail`` → ``send Email``
    ``memory_search`` → ``memory search``

    Dense embedders (and BM25) match far better on split tokens than on the
    raw composite identifier — ``memory_search`` as one token is opaque to a
    bag-of-words ranker. ratel ``indexing.rs::searchable_text`` does the same.
    """
    if not name:
        return ""
    s = str(name).replace("_", " ")
    s = _CAMEL_BOUNDARY.sub(r"\1 \2", s)
    return s.lower().strip()


def _flatten_schema(parameters: Any, out: List[str], *, depth: int = 0) -> None:
    """Recursively extract search-useful tokens from a JSON-Schema-ish param
    structure: field names (split), field descriptions, and enum values.

    Excludes structural noise (``type``/``properties``/``required``/…). The
    haystack built from these tokens is what BM25 and dense rank over — a
    clean haystack is the precondition for both rankers to score well
    (the noisy raw-dict haystack was the root cause of false positives like
    ``write_file`` ranking for "send file").

    Depth-guarded to avoid pathological schemas. ``build_tool_summary`` (the
    full schema returned to the LLM) is NOT touched — only the search
    haystack uses this.
    """
    if depth > 5 or parameters is None:
        return
    if isinstance(parameters, dict):
        for k, v in parameters.items():
            if k in _JSON_SCHEMA_NOISE_KEYS:
                # but still recurse into the value if it's a container —
                # e.g. "properties" key itself is noise, but its value holds
                # the field definitions we want.
                if isinstance(v, (dict, list)):
                    _flatten_schema(v, out, depth=depth + 1)
                continue
            if k:
                out.append(_split_identifier(str(k)))
            if isinstance(v, dict):
                # enum values are high-signal (create/list/delete) — surface them.
                enum = v.get("enum")
                if isinstance(enum, list):
                    out.extend(_split_identifier(str(e)) for e in enum if e)
                _flatten_schema(v, out, depth=depth + 1)
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, dict):
                        _flatten_schema(item, out, depth=depth + 1)
            elif isinstance(v, str) and depth <= 3 and len(v) < 200:
                # short string values at shallow depth can be useful labels
                # (e.g. an enum-like "type": "string" — but type is in noise
                # set so skipped above; this catches stray label strings).
                out.append(_split_identifier(v))
    elif isinstance(parameters, list):
        for item in parameters:
            if isinstance(item, (dict, list)):
                _flatten_schema(item, out, depth=depth + 1)

