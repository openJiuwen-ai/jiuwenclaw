# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Conversion functions for Vector Store distance / similarity scores to normalized similarity [0, 1].
"""
from typing import List, Dict, Any, Callable
from openjiuwen.core.foundation.store.base_vector_store import VectorDataType
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.foundation.store.base_vector_store import CollectionSchema, FieldSchema
from openjiuwen.core.memory.migration.operation.base_operation import BaseOperation
from openjiuwen.core.memory.migration.operation.operations import (
    AddScalarFieldOperation,
    RenameScalarFieldOperation,
    UpdateScalarFieldTypeOperation,
    UpdateEmbeddingDimensionOperation
)


def convert_l2_squared(raw_score: float, max_dist: float = 4.0) -> float:
    """
    Convert squared L2 distance to normalized similarity in [0, 1].
    Works for both Milvus and Chroma.

    Args:
        raw_score: Raw L2 distance score
        max_dist: Maximum distance (defaults to 4 for unit vectors)

    Returns:
        Normalized similarity score in [0, 1]
    """
    return max(0.0, (max_dist - raw_score) / max_dist)


def convert_cosine_similarity(raw_score: float) -> float:
    """
    Convert cosine similarity to normalized similarity in [0, 1].
    Works for Milvus.

    Args:
        raw_score: Raw cosine similarity (range [-1, 1])

    Returns:
        Normalized similarity score in [0, 1]
    """
    return (raw_score + 1.0) / 2.0


def convert_cosine_distance(raw_score: float) -> float:
    """
    Convert cosine distance to normalized cosine similarity in [0, 1].
    Works for Chroma.

    Args:
        raw_score: Raw cosine distance (range [0, 2])

    Returns:
        Normalized similarity score in [0, 1]
    """
    return (2.0 - raw_score) / 2.0


def convert_ip_similarity(raw_score: float) -> float:
    """
    Convert raw inner product to normalized similarity in [0, 1].
    Works for Milvus.

    Args:
        raw_score: Raw inner product

    Returns:
        Normalized similarity score in [0, 1]
    """
    return max(0.0, min(1.0, (raw_score + 1.0) / 2.0))


def convert_ip_distance(raw_score: float) -> float:
    """
    Convert inner product in distance form to normalized similarity in [0, 1].
    Works for Chroma, whose IP is a distance: d = 1 - dot (range [0, 2]).

    Args:
        raw_score: IP distance from Chroma (range [0, 2])

    Returns:
        Normalized similarity score in [0, 1]
    """
    return max(0.0, min(1.0, (2.0 - raw_score) / 2.0))


def _map_string_to_vector_data_type(type_str: str) -> VectorDataType:
    """Map a string type name to VectorDataType.

    Args:
        type_str: The string representation of the type (e.g., "int", "float", "string").

    Returns:
        VectorDataType: The corresponding VectorDataType enum value.

    Raises:
        Error: If the type string cannot be mapped.
    """
    type_mapping = {
        # String types
        "string": VectorDataType.VARCHAR,
        "str": VectorDataType.VARCHAR,
        "varchar": VectorDataType.VARCHAR,
        # Integer types
        "int": VectorDataType.INT32,
        "integer": VectorDataType.INT32,
        "int32": VectorDataType.INT32,
        "int64": VectorDataType.INT64,
        "long": VectorDataType.INT64,
        # Float types
        "float": VectorDataType.FLOAT,
        "float32": VectorDataType.FLOAT,
        "double": VectorDataType.DOUBLE,
        "float64": VectorDataType.DOUBLE,
        # Boolean type
        "bool": VectorDataType.BOOL,
        "boolean": VectorDataType.BOOL,
        # JSON type
        "json": VectorDataType.JSON,
        # Vector type
        "vector": VectorDataType.FLOAT_VECTOR,
        "float_vector": VectorDataType.FLOAT_VECTOR,
    }

    normalized_type = type_str.lower().strip()
    if normalized_type not in type_mapping:
        raise build_error(
            StatusCode.STORE_VECTOR_SCHEMA_INVALID,
            error_msg=f"Unknown type string: '{type_str}'. "
                      f"Supported types: {list(type_mapping.keys())}"
        )
    return type_mapping[normalized_type]


def compute_new_schema(old_schema: CollectionSchema, operations: List[BaseOperation]) -> CollectionSchema:
    """
    Compute the final schema after applying all operations.

    This method sequentially applies each operation to compute the resulting schema
    without actually modifying any data.

    Args:
        old_schema: The original schema.
        operations: List of operations to apply.

    Returns:
        CollectionSchema: The resulting schema after all operations.
    """
    # Start with a copy of the old schema
    schema_dict = old_schema.to_dict()
    new_schema = CollectionSchema.from_dict(schema_dict)

    for operation in operations:
        if isinstance(operation, AddScalarFieldOperation):
            new_schema = _compute_schema_add_field(new_schema, operation)
        elif isinstance(operation, RenameScalarFieldOperation):
            new_schema = _compute_schema_rename_field(new_schema, operation)
        elif isinstance(operation, UpdateScalarFieldTypeOperation):
            new_schema = _compute_schema_update_field_type(new_schema, operation)
        elif isinstance(operation, UpdateEmbeddingDimensionOperation):
            new_schema = _compute_schema_update_vector_dim(new_schema, operation)
        else:
            raise build_error(
                StatusCode.STORE_VECTOR_SCHEMA_INVALID,
                error_msg=f"Unsupported operation type: {type(operation).__name__}"
            )

    return new_schema


def _compute_schema_add_field(schema: CollectionSchema, operation: AddScalarFieldOperation) -> CollectionSchema:
    """Compute schema after adding a scalar field."""
    schema_dict = schema.to_dict()
    new_schema = CollectionSchema.from_dict(schema_dict)

    field_schema = FieldSchema(
        name=operation.field_name,
        dtype=_map_string_to_vector_data_type(operation.field_type),
        default_value=operation.default_value,
    )
    new_schema.add_field(field_schema)
    return new_schema


def _compute_schema_rename_field(
        schema: CollectionSchema,
        operation: RenameScalarFieldOperation
) -> CollectionSchema:
    """Compute schema after renaming a scalar field."""
    if operation.old_field_name == operation.new_field_name:
        return schema

    schema_dict = schema.to_dict()

    # Check if old_field_name exists
    old_field_exists = False
    new_field_exists = False
    for field in schema_dict["fields"]:
        if field["name"] == operation.old_field_name:
            old_field_exists = True
        elif field["name"] == operation.new_field_name:
            new_field_exists = True

    if not old_field_exists:
        raise build_error(
            StatusCode.STORE_VECTOR_SCHEMA_INVALID,
            error_msg=f"Old field '{operation.old_field_name}' does not exist"
        )

    if new_field_exists:
        raise build_error(
            StatusCode.STORE_VECTOR_SCHEMA_INVALID,
            error_msg=f"New field '{operation.new_field_name}' already exists"
        )

    # Rename the field
    for field in schema_dict["fields"]:
        if field["name"] == operation.old_field_name:
            field["name"] = operation.new_field_name
            break

    return CollectionSchema.from_dict(schema_dict)


def _compute_schema_update_field_type(
        schema: CollectionSchema,
        operation: UpdateScalarFieldTypeOperation
) -> CollectionSchema:
    """Compute schema after updating a field's data type."""
    schema_dict = schema.to_dict()

    # Check if field_name exists and its type
    field_found = False
    for field in schema_dict["fields"]:
        if field["name"] == operation.field_name:
            field_found = True
            if field["type"] == VectorDataType.FLOAT_VECTOR.value:
                raise build_error(
                    StatusCode.STORE_VECTOR_SCHEMA_INVALID,
                    error_msg=f"Cannot update type of vector field '{operation.field_name}'"
                )
            break

    if not field_found:
        raise build_error(
            StatusCode.STORE_VECTOR_SCHEMA_INVALID,
            error_msg=f"Field '{operation.field_name}' does not exist"
        )

    # Update the field's type
    for field in schema_dict["fields"]:
        if field["name"] == operation.field_name:
            new_dtype = _map_string_to_vector_data_type(operation.new_field_type)
            field["type"] = new_dtype.value
            break

    return CollectionSchema.from_dict(schema_dict)


def _compute_schema_update_vector_dim(
        schema: CollectionSchema,
        operation: UpdateEmbeddingDimensionOperation
) -> CollectionSchema:
    """Compute schema after updating vector embedding dimension."""
    schema_dict = schema.to_dict()

    # Check if field exists and is a vector field
    field_found = False
    is_vector_field = False
    for field in schema_dict["fields"]:
        if field["name"] == operation.field_name:
            field_found = True
            if field["type"] == VectorDataType.FLOAT_VECTOR.value:
                is_vector_field = True
            break

    if not field_found:
        raise build_error(
            StatusCode.STORE_VECTOR_SCHEMA_INVALID,
            error_msg=f"Field '{operation.field_name}' does not exist"
        )

    if not is_vector_field:
        raise build_error(
            StatusCode.STORE_VECTOR_SCHEMA_INVALID,
            error_msg=f"Field '{operation.field_name}' is not a vector field"
        )

    # Update the vector field's dimension
    for field in schema_dict["fields"]:
        if field["name"] == operation.field_name:
            field["dim"] = operation.new_dimension
            break

    return CollectionSchema.from_dict(schema_dict)


def build_transform_func_for_operations(operations: List[BaseOperation])\
        -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """
    Build a unified transform function that applies all operations to a document.

    This function creates a single transform function that sequentially applies
    all the changes specified by the operations to each document during migration.

    Args:
        operations: List of operations to apply.

    Returns:
        A transform function that applies all operations to a document.
    """
    def transform_func(doc: Dict[str, Any]) -> Dict[str, Any]:
        # Apply each operation in sequence to the document
        for operation in operations:
            doc = _apply_operation_to_doc(doc, operation)
        return doc

    return transform_func


def _apply_operation_to_doc(doc: Dict[str, Any], operation: BaseOperation) -> Dict[str, Any]:
    """
    Apply a single operation to a document.

    Args:
        doc: The document to modify.
        operation: The operation to apply.

    Returns:
        The modified document.
    """
    if isinstance(operation, AddScalarFieldOperation):
        # Add the new field with its default value if not already present
        if operation.field_name not in doc and operation.default_value is not None:
            doc[operation.field_name] = operation.default_value

    elif isinstance(operation, RenameScalarFieldOperation):
        # Rename the field
        if operation.old_field_name in doc:
            doc[operation.new_field_name] = doc.pop(operation.old_field_name)

    elif isinstance(operation, UpdateScalarFieldTypeOperation):
        # For type updates, we keep the value as-is and let Milvus handle the conversion
        # If a custom transform is needed, it should be provided via the operation
        pass

    elif isinstance(operation, UpdateEmbeddingDimensionOperation):
        # Re-compute the embedding with the new dimension
        re_embedding_func = operation.recompute_embedding_func
        if re_embedding_func is None:
            # Default to zero vector if no re-embedding function provided
            def default_re_embedding_func(doc: dict) -> list:
                return [0.0] * operation.new_dimension
            re_embedding_func = default_re_embedding_func

        new_vector = re_embedding_func(doc)
        if len(new_vector) != operation.new_dimension:
            raise build_error(
                StatusCode.STORE_VECTOR_SCHEMA_INVALID,
                error_msg=f"Generated vector length {len(new_vector)} "
                          f"does not match new_dim {operation.new_dimension}"
            )
        doc[operation.field_name] = new_vector

    return doc