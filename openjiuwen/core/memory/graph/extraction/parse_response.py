# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Parse Response

Utilities to parse JSON and extract structured content from LLM responses.
"""

import difflib
import json
import re
from typing import Any, Optional

from .custom_types import JSONLike

JSON_DECODER = json.JSONDecoder(strict=False)
REGEX_FIND_JSON_START = re.compile(r"[\[\{]")
REGEX_FIND_CODE_BLOCK = re.compile(r"(?s)```([A-Za-z]*)\s*\n(.*?)```")


def parse_json(resp: str, output_schema: Optional[dict[str, Any]] = None) -> Optional[JSONLike]:
    """Attempt to parse json from LLM response"""
    if output_schema:
        must_contain_key = output_schema.get("json_schema", output_schema).get("required")
    else:
        must_contain_key = None
    for possible_match in REGEX_FIND_CODE_BLOCK.finditer(resp):
        try:
            code_block_type = possible_match.group(1).lower()
            if not code_block_type or code_block_type == "json":
                result = JSON_DECODER.decode(possible_match.group(2))
                if must_contain_key:
                    if isinstance(result, dict):
                        result = dict()
                        for key in must_contain_key:
                            fuzzy_match = try_get_key(key=key, src=result)
                            if fuzzy_match is not None:
                                result[key] = fuzzy_match
                        return result
                    continue
                return result
            continue
        except json.JSONDecodeError:
            pass
    return _raw_decode_json(resp, must_contain_key)


def _raw_decode_json(resp: str, must_contain_key: Optional[list[str]] = None) -> Optional[JSONLike]:
    """Attempt to raw decode json from response"""
    possible_resp = [resp]
    candidate = resp
    last_relation_idx = resp.rfind("},")
    if last_relation_idx > 0:
        possible_resp.append(resp[:last_relation_idx] + "}]")

    for possible_start in REGEX_FIND_JSON_START.finditer(resp):
        for candidate in possible_resp:
            try:
                result = JSON_DECODER.raw_decode(candidate, possible_start.start())[0]
                if must_contain_key:
                    if isinstance(result, dict):
                        result = dict()
                        for key in must_contain_key:
                            fuzzy_match = try_get_key(key=key, src=result)
                            if fuzzy_match is not None:
                                result[key] = fuzzy_match
                        return result
                    continue
                return result
            except json.JSONDecodeError:
                pass
    return None


def try_get_key(key: str, src: dict[str, Any], pattern: re.Pattern = re.compile(r"\w+")) -> Optional[Any]:
    """Try to get specific key in input dictionary"""
    key = "".join(pattern.findall(key.casefold()))
    norm2key = {"".join(pattern.findall(k.casefold())): k for k in src.keys()}
    result = difflib.get_close_matches(key, norm2key, n=1, cutoff=0.85)
    if result:
        return norm2key[result[0]]
    return None


def ensure_list(obj: Any) -> list:
    """Ensure returned object is a list"""
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict) and len(obj) == 1:
        obj_list = next(iter(obj.values()))
        if isinstance(obj_list, list):
            return obj_list
    return [obj]
