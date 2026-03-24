# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Extraction Base

Multilingual base model and schema helpers for entity extraction.
"""

from collections import deque
from typing import Any, Optional, get_args, get_origin

from pydantic import BaseModel

# This will be populated in jiuwen/context/memory_engine/generation/graph/prompts/entity_extraction/*.py
MULTILINGUAL_DESCRIPTION: dict[str, dict[str, str]] = dict()


class MultilingualBaseModel(BaseModel):
    """
    Base LLM response model, with multilingual support and helper methods for representing the output model as string.
    """

    @classmethod
    def multilingual_model_json_schema(cls, language: str = "cn", strict: bool = False, **kwargs) -> dict[str, Any]:
        """Get JSON schema"""
        desc_lookup = MULTILINGUAL_DESCRIPTION[language]
        result = super().model_json_schema(**kwargs)
        # Recursively replace multilingual description
        cls._recursive_replace(result, lookup=desc_lookup, from_key="description", to_key="description")
        # Adhere to OpenAI's standard for structured output format
        if strict:
            # Perform BFS to ensure additionalProperties = False is set for all json objects
            to_visit = deque([result])
            while to_visit:
                node = to_visit.popleft()
                if isinstance(node, dict):
                    if node.get("type") == "object":
                        node["additionalProperties"] = False
                        property_field = node.get("properties")
                        if isinstance(property_field, dict):
                            node.setdefault("required", list(property_field.keys()))
                    to_visit.extend(node.values())
                elif isinstance(node, list):
                    to_visit.extend(node)
        return result

    @classmethod
    def readable_schema(cls, language: str = "cn", **kwargs) -> tuple[str, dict]:
        """Generate an LLM-readable schema definition"""
        schema = cls.multilingual_model_json_schema(language, **kwargs)
        cls._recursive_replace(schema, lookup={}, from_key="title")
        cls._recursive_replace(schema, lookup={}, from_key="required")

        if "$defs" in schema:
            refs = schema["$defs"]
            map_ref_to_name = {("#/$defs/" + key): key for key in refs}
            cls._recursive_replace(schema, lookup=map_ref_to_name, from_key="$ref", to_key="type")
            del schema["$defs"]
        else:
            refs = dict()

        output_format = ""
        multilingual_lookup = MULTILINGUAL_DESCRIPTION[language]
        for output_name, output_val in cls.model_fields.items():
            output_format += f"{output_name}: {cls._to_json_types(output_val.annotation)}"
            if output_val.description:
                output_format += f"  # {multilingual_lookup[output_val.description]}\n"

        return output_format.removesuffix("\n"), {key: val["properties"] for key, val in refs.items()}

    @classmethod
    def response_format(cls, language: str = "cn") -> dict[str, Any]:
        """Convert to LLM response format"""
        return {
            "type": "json_schema",
            "json_schema": {
                "schema": cls.multilingual_model_json_schema(language, strict=True),
                "name": cls.__name__,
                "strict": True,
            },
        }

    @classmethod
    def _recursive_replace(
        cls, to_search: Any, lookup: dict[str, str], from_key: str, to_key: Optional[str] = None
    ) -> bool:
        """Recursively replace dict keys"""
        has_replaced = False
        to_replace = deque([to_search])
        while to_replace:
            current = to_replace.popleft()
            if isinstance(current, list):
                to_replace.extend(current)
            elif isinstance(current, dict):
                desc_key = current.get(from_key)
                if desc_key is not None:
                    del current[from_key]
                    if to_key:
                        current[to_key] = lookup.get(desc_key, desc_key)
                    has_replaced = True
                to_replace.extend(current.values())
        return has_replaced

    @classmethod
    def _to_json_types(cls, annotation: type) -> str:
        origin = get_origin(annotation)
        if origin is not None:
            origin_name = origin.__name__
            args = get_args(annotation)
            if args:
                return f"{origin_name}[{','.join(arg.__name__ for arg in args)}]"
            return origin_name
        return annotation.__name__
