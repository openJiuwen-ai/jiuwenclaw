# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import inspect
from abc import ABC, abstractmethod
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, get_origin, get_args, Union, TypeVar, ForwardRef, Literal
from types import UnionType
from uuid import UUID
from pydantic import BaseModel



class TypeSchemaExtractor(ABC):
    """Type handler abstract base class"""

    @abstractmethod
    def can_extract(self, type_hint: Any) -> bool:
        """Check if this handler can handle the given type"""
        pass

    @abstractmethod
    def extract(self, type_hint: Any, extractor: 'CallableSchemaExtractor') -> Dict:
        """Handle the type and return JSON Schema"""
        pass


class SimpleTypeSchemaExtractor(TypeSchemaExtractor):
    """Handler for simple built-in types"""

    def __init__(self):
        self.type_mapping = {
            str: {"type": "string"},
            int: {"type": "integer"},
            float: {"type": "number"},
            bool: {"type": "boolean"},
            list: {"type": "array"},
            dict: {"type": "object"},
            type(None): {"type": "null"},
            datetime: {"type": "string", "format": "date-time"},
            date: {"type": "string", "format": "date"},
            time: {"type": "string", "format": "time"},
            Decimal: {"type": "number"},
            UUID: {"type": "string", "format": "uuid"},
            Path: {"type": "string", "format": "path"},
            bytes: {"type": "string", "format": "binary"},
            Any: {},
        }

    def can_extract(self, type_hint: Any) -> bool:
        return type_hint in self.type_mapping

    def extract(self, type_hint: Any, extractor: 'CallableSchemaExtractor') -> Dict:
        return self.type_mapping[type_hint].copy()


class OptionalSchemaExtractor(TypeSchemaExtractor):
    """Handler for Optional[T] types"""

    def can_extract(self, type_hint: Any) -> bool:
        origin = get_origin(type_hint)
        if origin in (Union, UnionType):
            args = get_args(type_hint)
            return type(None) in args
        return False

    def extract(self, type_hint: Any, extractor: 'CallableSchemaExtractor') -> Dict:
        args = get_args(type_hint)
        non_none_types = [arg for arg in args if arg is not type(None)]
        if len(non_none_types) == 1:
            schema = extractor.get_type_schema(non_none_types[0])
            schema["nullable"] = True
            return schema
        return {"type": "object"}


class UnionSchemaExtractor(TypeSchemaExtractor):
    """Handler for Union[T1, T2, ...] types"""

    def can_extract(self, type_hint: Any) -> bool:
        origin = get_origin(type_hint)
        if origin in (Union, UnionType):
            args = get_args(type_hint)
            return type(None) not in args  # Optional handled separately
        return False

    def extract(self, type_hint: Any, extractor: 'CallableSchemaExtractor') -> Dict:
        args = get_args(type_hint)
        return {
            "anyOf": [extractor.get_type_schema(arg) for arg in args]
        }


class ListSchemaExtractor(TypeSchemaExtractor):
    """Handler for List[T], Sequence[T], etc."""

    def can_extract(self, type_hint: Any) -> bool:
        origin = get_origin(type_hint)
        return origin in (list, List)

    def extract(self, type_hint: Any, extractor: 'CallableSchemaExtractor') -> Dict:
        args = get_args(type_hint)
        if args:
            return {
                "type": "array",
                "items": extractor.get_type_schema(args[0])
            }
        return {"type": "array"}


class TupleSchemaExtractor(TypeSchemaExtractor):
    """Handler for Tuple types"""

    def can_extract(self, type_hint: Any) -> bool:
        origin = get_origin(type_hint)
        return origin is tuple

    def extract(self, type_hint: Any, extractor: 'CallableSchemaExtractor') -> Dict:
        args = get_args(type_hint)
        if args:
            if len(args) == 2 and args[1] is ...:
                # Tuple[T, ...] means array of T
                return {
                    "type": "array",
                    "items": extractor.get_type_schema(args[0])
                }
            else:
                # Fixed length tuple
                return {
                    "type": "array",
                    "items": {
                        "anyOf": [extractor.get_type_schema(arg) for arg in args]
                    },
                    "minItems": len(args),
                    "maxItems": len(args)
                }
        return {"type": "array"}


class DictSchemaExtractor(TypeSchemaExtractor):
    """Handler for Dict[K, V], Mapping[K, V]"""

    def can_extract(self, type_hint: Any) -> bool:
        origin = get_origin(type_hint)
        return origin in (dict, Dict)

    def extract(self, type_hint: Any, extractor: 'CallableSchemaExtractor') -> Dict:
        args = get_args(type_hint)
        if len(args) == 2:
            return {
                "type": "object",
                "additionalProperties": extractor.get_type_schema(args[1])
            }
        return {"type": "object"}


class SetSchemaExtractor(TypeSchemaExtractor):
    """Handler for Set[T]"""

    def can_extract(self, type_hint: Any) -> bool:
        origin = get_origin(type_hint)
        return origin is set

    def extract(self, type_hint: Any, extractor: 'CallableSchemaExtractor') -> Dict:
        args = get_args(type_hint)
        if args:
            return {
                "type": "array",
                "items": extractor.get_type_schema(args[0]),
                "uniqueItems": True
            }
        return {"type": "array", "uniqueItems": True}


class BaseModelSchemaExtractor(TypeSchemaExtractor):
    """Handler for Pydantic BaseModel"""

    def can_extract(self, type_hint: Any) -> bool:
        return inspect.isclass(type_hint) and issubclass(type_hint, BaseModel)

    def extract(self, type_hint: Any, extractor: 'CallableSchemaExtractor') -> Dict:
        return extractor.get_base_model_schema(type_hint)


class EnumSchemaExtractor(TypeSchemaExtractor):
    """Handler for Enum types"""

    def can_extract(self, type_hint: Any) -> bool:
        return inspect.isclass(type_hint) and issubclass(type_hint, Enum)

    def extract(self, type_hint: Any, extractor: 'CallableSchemaExtractor') -> Dict:
        return extractor.get_enum_schema(type_hint)


class TypeVarSchemaExtractor(TypeSchemaExtractor):
    """Handler for TypeVar (generic parameters)"""

    def can_extract(self, type_hint: Any) -> bool:
        return isinstance(type_hint, TypeVar)

    def extract(self, type_hint: Any, extractor: 'CallableSchemaExtractor') -> Dict:
        return {"type": "object", "description": "Generic type parameter"}


class ForwardRefSchemaExtractor(TypeSchemaExtractor):
    """Handler for ForwardRef (string type annotations)"""

    def can_extract(self, type_hint: Any) -> bool:
        return isinstance(type_hint, ForwardRef)

    def extract(self, type_hint: Any, extractor: 'CallableSchemaExtractor') -> Dict:
        return {"type": "object", "description": "Type reference"}


class LiteralSchemaExtractor(TypeSchemaExtractor):
    """Handler for Literal[T] types"""

    def can_extract(self, type_hint: Any) -> bool:
        origin = get_origin(type_hint)
        return origin is Literal

    def extract(self, type_hint: Any, extractor: 'CallableSchemaExtractor') -> Dict:
        args = get_args(type_hint)
        if args:
            # Get the type of the first literal value
            literal_type = type(args[0])
            # Map Python type to JSON Schema type
            type_mapping = {
                str: "string",
                int: "integer",
                float: "number",
                bool: "boolean"
            }
            json_type = type_mapping.get(literal_type, "string")
            
            return {
                "type": json_type,
                "enum": list(args),
            }
        return {"type": "string"}


class TypeSchemaExtractorRegistry:
    """Singleton registry for type handlers"""

    _instance = None
    _handlers: List[TypeSchemaExtractor] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._handlers = []
            cls._instance._register_extractors()
        return cls._instance

    def _register_extractors(self):
        """Register all default type handlers in priority order"""
        # Higher priority handlers first
        self.register(OptionalSchemaExtractor())
        self.register(UnionSchemaExtractor())
        self.register(LiteralSchemaExtractor())
        self.register(ListSchemaExtractor())
        self.register(TupleSchemaExtractor())
        self.register(DictSchemaExtractor())
        self.register(SetSchemaExtractor())
        self.register(BaseModelSchemaExtractor())
        self.register(EnumSchemaExtractor())
        self.register(SimpleTypeSchemaExtractor())
        self.register(TypeVarSchemaExtractor())
        self.register(ForwardRefSchemaExtractor())

    def register(self, handler: TypeSchemaExtractor):
        """Register a new type handler"""
        self._handlers.append(handler)

    def get_extractors(self) -> List[TypeSchemaExtractor]:
        """Get all registered handlers"""
        return self._handlers.copy()
