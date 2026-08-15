# coding: utf-8
"""Coerce stringified complex params back to list/dict for MCP tool calls.

Why this exists: LLM tool-call arguments frequently arrive with array/object
fields serialized as a JSON *string* instead of the native list/dict — e.g.
``mcp_memory_create_entities`` gets ``entities='[{"name": ...}]'`` (a str)
when its schema declares ``entities: {type: array}``. openjiuwen's
``MCPTool.invoke`` -> ``SchemaUtils.format_with_schema`` -> ``validate_with_schema``
then raises ``Input should be a valid list [input_type=str]`` and the whole
call fails — the LLM, seeing the tool error, falls back to bash/builtins.

This is a *generic* root cause affecting every MCP tool whose schema has
array/object parameters (memory entities, filesystem paths, github labels, …),
not a memory-specific bug. The retrieval layer already surfaces these tools
(name-boost + LLM naming them in the query); the break is purely at the
invocation layer.

Fix: patch ``MCPTool.invoke`` so that, before delegating to the original
invoke (which runs schema validation), we walk ``inputs`` and for any field
whose JSON-Schema type is ``array`` or ``object`` but whose value is a ``str``,
attempt ``json.loads`` to restore the native list/dict. On parse failure we
leave the value untouched so the stock validation error still surfaces (we
never mask a genuinely broken argument — only rescue the common
LLM-serialized-the-collection case).

Scope: MCP tools only. ``SchemaUtils.format_with_schema`` itself is *not*
patched because it has 6 call sites across function tools / RESTful API tools
/ LLM-output formatting / workflow — touching the shared util would risk
regressing non-MCP paths where a str value is legitimately expected. We wrap
``MCPTool.invoke`` exclusively, mirroring ``mcp_call_timeout_patch``'s pattern.

Idempotent via a module-level ``_PATCHED`` guard; applied once at process
startup from ``JiuWenSwarmDeepAdapter.__init__`` (next to
``apply_mcp_call_timeout_patch``).
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("jiuwenswarm.common.mcp_param_coerce_patch")

_PATCHED = False

__all__ = ["apply_mcp_param_coerce_patch"]


def _expected_field_types(input_params: Any) -> dict:
    """Map top-level field name -> JSON-Schema ``type`` string.

    MCP tool schemas (``McpToolCard.input_params``) are standard JSON Schema
    dicts shaped like ``{"type": "object", "properties": {field: {"type": ...}}}``.
    Returns ``{}`` for anything that isn't a schema dict with a ``properties``
    mapping — callers then treat unknown fields as "leave untouched".
    """
    if not isinstance(input_params, dict):
        return {}
    props = input_params.get("properties")
    if not isinstance(props, dict):
        return {}
    out: dict = {}
    for name, spec in props.items():
        if isinstance(spec, dict) and "type" in spec:
            out[name] = spec["type"]
    return out


def _coerce_stringified_complex(inputs: Any, input_params: Any) -> Any:
    """Return ``inputs`` with str-valued array/object fields parsed back.

    Only fields whose declared schema type is ``array``/``object`` and whose
    runtime value is a ``str`` are touched. A non-JSON string (or one that
    parses to the wrong scalar type) is left as-is so the original validation
    error still fires — this is a rescue for the common LLM serialization
    case, not a blanket type coercion.
    """
    if not isinstance(inputs, dict):
        return inputs
    types = _expected_field_types(input_params)
    if not types:
        return inputs
    coerced = dict(inputs)
    for field, value in coerced.items():
        expected = types.get(field)
        if expected not in ("array", "object"):
            continue
        if not isinstance(value, str):
            continue
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            # Not valid JSON — leave untouched, let stock validation report it.
            continue
        # Only accept a parse result that matches the declared type. This guards
        # against e.g. an ``object`` field whose string parses to a list, or a
        # genuine string field that happened to look JSON-ish — though the
        # type-table gate already keeps us off string-typed fields.
        if expected == "array" and isinstance(parsed, list):
            coerced[field] = parsed
            logger.info(
                "[mcp-coerce] field %r: str -> list (len=%d)", field, len(parsed)
            )
        elif expected == "object" and isinstance(parsed, dict):
            coerced[field] = parsed
            logger.info(
                "[mcp-coerce] field %r: str -> dict (keys=%d)", field, len(parsed)
            )
    return coerced


def apply_mcp_param_coerce_patch() -> None:
    """Patch ``MCPTool.invoke`` to rescue stringified array/object params.

    Idempotent per process.
    """
    global _PATCHED
    if _PATCHED:
        return
    _PATCHED = True

    from openjiuwen.core.foundation.tool.mcp.base import MCPTool

    orig_invoke = MCPTool.invoke

    async def invoke(self, inputs, **kwargs):
        try:
            inputs = _coerce_stringified_complex(
                inputs, getattr(self._card, "input_params", None)
            )
        except Exception as exc:  # noqa: BLE001 — never let the patch break a call
            logger.warning("[mcp-coerce] pre-coerce failed (%r); calling orig invoke", exc)
        return await orig_invoke(self, inputs, **kwargs)

    setattr(MCPTool, "invoke", invoke)

    logger.info(
        "[mcp-coerce] patch applied (target=MCPTool.invoke, coerce str->array/object)"
    )
