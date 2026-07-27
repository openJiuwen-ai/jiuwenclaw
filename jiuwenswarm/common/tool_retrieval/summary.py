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
from typing import Any, Dict

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
