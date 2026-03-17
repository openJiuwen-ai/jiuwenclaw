# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import inspect
import re
from enum import Enum
from typing import Any, Callable, Dict, Type, get_type_hints
from pydantic import BaseModel

from openjiuwen.core.foundation.tool.utils.type_schema_extractor import TypeSchemaExtractorRegistry


class CallableSchemaExtractor:
    """Extractor for callable object input schemas (stateless)"""

    # Singleton handler registry
    __type_schema_extractor_registry = TypeSchemaExtractorRegistry()

    @classmethod
    def generate_schema(cls, callable_obj: Callable) -> Dict:
        """Generate complete callable_obj schema"""
        signature = inspect.signature(callable_obj)
        type_hints = get_type_hints(callable_obj)
        return cls._generate_input_schema(callable_obj, signature, type_hints)


    @classmethod
    def get_type_schema(cls, type_hint: Any) -> Dict:
        """Convert Python type to JSON Schema using registered handlers"""
        for extractor in cls.__type_schema_extractor_registry.get_extractors():
            if extractor.can_extract(type_hint):
                return extractor.extract(type_hint, cls)

        # Default handling for unknown types
        return {"type": "object", "description": "Object"}

    @staticmethod
    def _humanize_name(name: str) -> str:
        """Convert variable name to human readable text"""
        if not name:
            return ''

        # Handle snake_case
        if '_' in name:
            words = name.split('_')
            return ' '.join(word.capitalize() for word in words if word).lower()

        # Handle camelCase and PascalCase
        result = []
        for i, char in enumerate(name):
            if i == 0:
                result.append(char.upper())
            elif char.isupper():
                is_lower = i > 0 and (name[i - 1].islower() or (i < len(name) - 1 and name[i + 1].islower()))
                if is_lower:
                    result.append(' ')
                result.append(char)
            else:
                result.append(char)

        humanized = ''.join(result)

        # Handle common abbreviations
        abbreviations = ['id', 'url', 'uri', 'api', 'sql', 'html', 'xml', 'json', 'csv']
        for abbr in abbreviations:
            pattern = rf'\b{abbr.upper()}\b'
            humanized = re.sub(pattern, abbr.upper(), humanized, flags=re.IGNORECASE)

        return humanized.lower()

    @staticmethod
    def extract_function_description(callable_obj: Callable) -> str:
        """Extract function description from docstring"""
        doc = inspect.getdoc(callable_obj)

        if not doc:
            return CallableSchemaExtractor._humanize_name(callable_obj.__name__)

        lines = doc.strip().split('\n')
        description_lines = []

        for line in lines:
            line = line.strip()
            if not line:
                break
            if line.lower().startswith(
                    ('args:', 'parameters:', 'returns:', 'raises:', 'yields:', 'examples:', 'notes:')):
                break
            if re.match(r'^\s*\w+\s*:', line):
                break
            description_lines.append(line)

        result = ' '.join(description_lines).strip()
        return result if result else CallableSchemaExtractor._humanize_name(callable_obj.__name__)

    @staticmethod
    def _extract_docstring_descriptions(callable_obj: Callable) -> Dict[str, str]:
        """Extract parameter descriptions from docstring"""
        descriptions = {}
        doc = inspect.getdoc(callable_obj)

        if not doc:
            return descriptions

        patterns = [
            r'^\s*(?P<name>\w+)\s*:\s*(?P<desc>.*?)(?=\n\s*\w+\s*:|$)',
            r'^\s*(?P<name>\w+)\s*:.*?\n\s*(?P<desc>.*?)(?=\n\s*\w+\s*:|$)',
            r':param\s+(?P<name>\w+)\s*:\s*(?P<desc>.*?)(?=\n\s*:|\Z)',
            r'^\s*(?P<name>\w+)\s*:\s*(?P<desc>.*?)$',
        ]

        args_section_pattern = r'(?:Args|Parameters|Arguments)\s*:?\s*\n(.*?)(?:\n\s*\n|\Z)'
        args_match = re.search(args_section_pattern, doc, re.DOTALL | re.IGNORECASE)

        if args_match:
            args_text = args_match.group(1)
            for pattern in patterns:
                for match in re.finditer(pattern, args_text, re.MULTILINE | re.DOTALL):
                    name = match.group('name')
                    desc = match.group('desc').strip()
                    if desc:
                        descriptions[name] = desc

        return descriptions

    @staticmethod
    def get_base_model_schema(model: Type[BaseModel]) -> Dict:
        """Generate JSON Schema from Pydantic BaseModel with all $defs expanded inline"""
        schema = model.model_json_schema()

        def expand_all_refs(schema_part: Any, definitions: Dict) -> Any:
            if isinstance(schema_part, dict):
                if '$ref' in schema_part:
                    ref_path = schema_part['$ref']
                    if ref_path.startswith('#/$defs/') or ref_path.startswith('#/definitions/'):
                        ref_key = ref_path.split('/')[-1]
                        if ref_key in definitions:
                            ref_schema = definitions[ref_key].copy()
                            return expand_all_refs(ref_schema, definitions)
                    return schema_part

                for key in ['allOf', 'anyOf', 'oneOf']:
                    if key in schema_part:
                        schema_part[key] = [expand_all_refs(item, definitions) for item in schema_part[key]]

                if 'items' in schema_part:
                    schema_part['items'] = expand_all_refs(schema_part['items'], definitions)

                if 'properties' in schema_part:
                    new_properties = {}
                    for prop_name, prop_schema in schema_part['properties'].items():
                        expanded_prop = expand_all_refs(prop_schema, definitions)
                        if isinstance(expanded_prop, dict) and 'description' not in expanded_prop:
                            expanded_prop['description'] = CallableSchemaExtractor._humanize_name(prop_name)
                        new_properties[prop_name] = expanded_prop
                    schema_part['properties'] = new_properties

                if 'additionalProperties' in schema_part and isinstance(schema_part['additionalProperties'], dict):
                    schema_part['additionalProperties'] = expand_all_refs(schema_part['additionalProperties'],
                                                                          definitions)

                if 'patternProperties' in schema_part:
                    schema_part['patternProperties'] = {
                        k: expand_all_refs(v, definitions)
                        for k, v in schema_part['patternProperties'].items()
                    }

                result = {}
                for k, v in schema_part.items():
                    result[k] = expand_all_refs(v, definitions)
                return result

            elif isinstance(schema_part, list):
                return [expand_all_refs(item, definitions) for item in schema_part]
            else:
                return schema_part

        definitions = {}
        if '$defs' in schema:
            definitions.update(schema['$defs'])
        if 'definitions' in schema:
            definitions.update(schema['definitions'])

        expanded_schema = expand_all_refs(schema, definitions)

        expanded_schema.pop('$defs', None)
        expanded_schema.pop('definitions', None)

        return expanded_schema

    @staticmethod
    def get_enum_schema(enum_type: Type[Enum]) -> Dict:
        """Handle enum types"""
        return {
            "type": "string",
            "enum": [e.value for e in enum_type],
            "description": f"Enum values: {', '.join([str(e.value) for e in enum_type])}"
        }

    @classmethod
    def _generate_input_schema(cls, callable_obj: Callable, signature, type_hints) -> Dict:
        """Generate input JSON Schema with all definitions expanded inline"""
        properties = {}
        required = []

        param_descriptions = cls._extract_docstring_descriptions(callable_obj)
        has_var_keyword = False
        for param_name, param in signature.parameters.items():
            if param_name in ['self', 'cls']:
                continue
            if param.kind == inspect.Parameter.VAR_POSITIONAL:
                has_var_keyword = True
                continue

            elif param.kind == inspect.Parameter.VAR_KEYWORD:
                has_var_keyword = True
                continue
            param_type = type_hints.get(param_name, Any)
            param_schema = cls.get_type_schema(param_type)

            description = param_descriptions.get(param_name, "")
            if not description:
                description = cls._humanize_name(param_name)

            if description and 'description' not in param_schema:
                param_schema["description"] = description

            if param.default != inspect.Parameter.empty:
                if param.default is None:
                    param_schema["default"] = None
                else:
                    param_schema["default"] = param.default
            else:
                required.append(param_name)

            properties[param_name] = param_schema

        schema = {
            "type": "object",
            "properties": properties,
            "additionalProperties": has_var_keyword,
            "title": cls._humanize_name(callable_obj.__name__)
        }

        if required:
            schema["required"] = required

        return schema
