# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import datetime
import json
from enum import Enum
from typing import Any, Union

from openjiuwen.core.runner.drunner.dmessage_queue.message import (
    DmqRequestMessage, DmqResponseMessage
)
from openjiuwen.core.session import InteractionOutput
from openjiuwen.core.session.stream import OutputSchema, CustomSchema, TraceSchema
from openjiuwen.core.workflow import WorkflowOutput

MAX_RECURSE_DEPTH = 10

# Classes that can be deserialized in JSON, call .model_validate(payload) for deserialization
TYPE_REGISTRY = {
    "OutputSchema": OutputSchema,
    "CustomSchema": CustomSchema,
    "TraceSchema": TraceSchema,
    "InteractionOutput": InteractionOutput,
    "WorkflowOutput": WorkflowOutput,
    "DmqRequestMessage": DmqRequestMessage,
    "DmqResponseMessage": DmqResponseMessage,
}


def serialize_message(msg: Union[DmqRequestMessage, DmqResponseMessage]) -> bytes:
    """Serialize message to JSON string"""
    data = _serialize_payload(msg, depth=0)
    return json.dumps(data, default=_json_serializer).encode("utf-8")


def _json_serializer(obj):
    """Custom JSON serializer for datetime objects"""
    if isinstance(obj, datetime.datetime):
        return {
            "__type__": "datetime",
            "value": obj.isoformat()
        }
    raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")


def _serialize_payload(payload: Any, depth: int) -> Any:
    """Serialize payload recursively with depth limit."""
    if depth > MAX_RECURSE_DEPTH:
        raise RecursionError(f"Payload nested too deep (> {MAX_RECURSE_DEPTH})")

    # Enum
    if isinstance(payload, Enum):
        return payload.value

    # BaseModel objects
    if hasattr(payload, "model_dump"):
        result = {"__class__": payload.__class__.__name__}

        # custom schema
        for field_name in payload.model_fields.keys():
            field_value = getattr(payload, field_name)
            result[field_name] = _serialize_payload(field_value, depth + 1)

        if hasattr(payload, '__pydantic_extra__') and payload.__pydantic_extra__:
            for field_name, field_value in payload.__pydantic_extra__.items():
                result[field_name] = _serialize_payload(field_value, depth + 1)

        return result

    # List
    if isinstance(payload, list):
        return [_serialize_payload(v, depth + 1) for v in payload]

    # Dict
    if isinstance(payload, dict):
        new_dict = {}
        for k, v in payload.items():
            new_dict[k] = _serialize_payload(v, depth + 1)
        return new_dict

    # Primitives (str, int, float, bool, None, etc.)
    return payload


def deserialize_message(data: bytes) -> Union[DmqRequestMessage, DmqResponseMessage]:
    """Deserialize JSON string to message object"""
    obj = json.loads(data.decode("utf-8"), object_hook=_json_deserializer)
    return _deserialize_payload(obj, depth=0)


def _json_deserializer(obj):
    """Custom JSON deserializer for datetime objects"""
    if isinstance(obj, dict) and obj.get("__type__") == "datetime":
        return datetime.datetime.fromisoformat(obj["value"])
    return obj


def _deserialize_payload(payload: Any, depth: int) -> Any:
    """Deserialize payload recursively with depth limit."""
    if depth > MAX_RECURSE_DEPTH:
        raise RecursionError(f"Payload nested too deep (> {MAX_RECURSE_DEPTH})")

    # Dict with __class__ marker - BaseModel object
    if isinstance(payload, dict) and "__class__" in payload:
        class_name = payload.pop("__class__")
        if class_name not in TYPE_REGISTRY:
            raise ValueError(f"Unknown payload class: {class_name}")

        payload_class = TYPE_REGISTRY[class_name]

        deserialized_fields = {}
        for key, value in payload.items():
            deserialized_fields[key] = _deserialize_payload(value, depth + 1)

        return payload_class.model_validate(deserialized_fields)

    # List
    if isinstance(payload, list):
        return [_deserialize_payload(v, depth + 1) for v in payload]

    # Plain dict (no __class__ marker)
    if isinstance(payload, dict):
        new_dict = {}
        for k, v in payload.items():
            new_dict[k] = _deserialize_payload(v, depth + 1)
        return new_dict

    # Primitives
    return payload
