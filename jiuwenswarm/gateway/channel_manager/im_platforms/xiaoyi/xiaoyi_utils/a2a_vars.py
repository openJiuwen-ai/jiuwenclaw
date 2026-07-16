# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Helpers to extract A2A variables from Xiaoyi message parts."""

from __future__ import annotations

from typing import Any


def extract_model_name(parts: list[Any] | None) -> str | None:
    """Extract modelName from A2A data parts (variables.clientVariables.modelName).

    Mirrors OpenClaw xy_channel ``extractModelName``: ignore empty / ``none``.
    """
    for part in parts or []:
        if not isinstance(part, dict) or part.get("kind") != "data":
            continue
        data = part.get("data")
        if not isinstance(data, dict):
            continue
        variables = data.get("variables")
        if not isinstance(variables, dict):
            continue
        client_vars = variables.get("clientVariables")
        if not isinstance(client_vars, dict):
            continue
        model_name = client_vars.get("modelName")
        if not isinstance(model_name, str):
            continue
        model_name = model_name.strip()
        if model_name and model_name.lower() != "none":
            return model_name
    return None
