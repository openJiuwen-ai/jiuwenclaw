# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Dict, List, Optional, Union, TYPE_CHECKING

from pydantic import BaseModel, Field, field_validator

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
if TYPE_CHECKING:
    from openjiuwen.core.memory.migration.operation.base_operation import BaseOperation


class VectorDataType(str, Enum):
    """Supported data types for vector store fields."""

    VARCHAR = "VARCHAR"
    FLOAT_VECTOR = "FLOAT_VECTOR"
    INT64 = "INT64"
    INT32 = "INT32"
    INT16 = "INT16"
    INT8 = "INT8"
    FLOAT = "FLOAT"
    DOUBLE = "DOUBLE"
    BOOL = "BOOL"
    JSON = "JSON"
    ARRAY = "ARRAY"


class FieldSchema(BaseModel):
    """
    Schema definition for a single field in a collection.

    Similar to Milvus FieldSchema, supports various data types and field properties.
    """

    name: str = Field(..., description="Field name")
    dtype: VectorDataType = Field(..., description="Field data type")
    is_primary: bool = Field(default=False, description="Whether this is the primary key field")
    auto_id: bool = Field(default=False, description="Whether to auto-generate IDs for this field")
    max_length: Optional[int] = Field(default=65535, description="Max length for VARCHAR fields")
    dim: Optional[int] = Field(default=None, description="Vector dimension for FLOAT_VECTOR fields")
    element_type: Optional[VectorDataType] = Field(default=None, description="Element type for ARRAY fields")
    max_capacity: Optional[int] = Field(default=None, description="Max capacity for ARRAY fields")
    description: Optional[str] = Field(default=None, description="Field description")
    default_value: Optional[Any] = Field(default=None, description="Default value for the field")

    @field_validator("dim")
    @classmethod
    def validate_dim(cls, v: Optional[int], info) -> Optional[int]:
        if v is not None and v <= 0:
            raise build_error(
                StatusCode.STORE_VECTOR_SCHEMA_INVALID,
                error_msg=f"dim of vector field is invalid, field={info.data.get('name')}, dim={v}"
            )
        dtype = info.data.get("dtype")
        if dtype == VectorDataType.FLOAT_VECTOR and v is None:
            raise build_error(
                StatusCode.STORE_VECTOR_SCHEMA_INVALID,
                error_msg=f"dim of vector field is missing, field={info.data.get('name')}, dim={v}"
            )
        return v

    def to_dict(self) -> Dict[str, Any]:
        """Convert field schema to dictionary format."""
        result: Dict[str, Any] = {
            "name": self.name,
            "type": self.dtype.value,
        }
        if self.is_primary:
            result["is_primary"] = True
        if self.auto_id:
            result["auto_id"] = True
        if self.max_length is not None:
            result["max_length"] = self.max_length
        if self.dim is not None:
            result["dim"] = self.dim
        if self.element_type is not None:
            result["element_type"] = self.element_type.value
        if self.max_capacity is not None:
            result["max_capacity"] = self.max_capacity
        if self.default_value is not None:
            result["default_value"] = self.default_value
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FieldSchema":
        """Create field schema from dictionary format."""
        dtype_str = data.get("type", data.get("dtype", "VARCHAR")) or "VARCHAR"
        dtype = VectorDataType(dtype_str.upper())

        return cls(
            name=data["name"],
            dtype=dtype,
            is_primary=data.get("is_primary", False),
            auto_id=data.get("auto_id", False),
            max_length=data.get("max_length"),
            dim=data.get("dim"),
            element_type=VectorDataType(data["element_type"]) if data.get("element_type") else None,
            max_capacity=data.get("max_capacity"),
            description=data.get("description"),
            default_value=data.get("default_value"),
        )


class CollectionSchema(BaseModel):
    """
    Schema definition for a vector collection.

    Similar to Milvus CollectionSchema, supports dynamic field addition.
    """

    fields: List[FieldSchema] = Field(default_factory=list, description="List of field definitions")
    description: Optional[str] = Field(default=None, description="Collection description")
    enable_dynamic_field: bool = Field(
        default=False, description="Whether to enable dynamic field (allows fields not in schema)"
    )

    def __init__(self, **data: Any):
        super().__init__(**data)
        self._validate_primary_key()

    def _validate_primary_key(self) -> None:
        """Validate that there is at most one primary key field."""
        primary_fields = [f for f in self.fields if f.is_primary]
        if len(primary_fields) > 1:
            raise build_error(
                StatusCode.STORE_VECTOR_SCHEMA_INVALID,
                error_msg=f"collection can have at most one primary key field,"
                          f" primary_field={primary_fields[0].name}, field={primary_fields[1].name}"
            )

    def add_field(self, field: Union[FieldSchema, Dict[str, Any]]) -> "CollectionSchema":
        """
        Add a field to the schema.

        Args:
            field: FieldSchema instance or field dictionary

        Returns:
            Self for method chaining
        """
        if isinstance(field, dict):
            field = FieldSchema.from_dict(field)

        # Check for duplicate field names
        if any(f.name == field.name for f in self.fields):
            raise build_error(
                StatusCode.STORE_VECTOR_SCHEMA_INVALID,
                error_msg=f"field name already exists, field={field.name}"
            )

        # Check for duplicate primary key
        if field.is_primary:
            primary_fields = [f for f in self.fields if f.is_primary]
            if primary_fields:
                raise build_error(
                    StatusCode.STORE_VECTOR_SCHEMA_INVALID,
                    error_msg=f"collection can have at most one primary key field,"
                              f" primary_field={primary_fields[0].name}, field={field.name}"
                )

        self.fields.append(field)
        return self

    def remove_field(self, field_name: str) -> "CollectionSchema":
        """
        Remove a field from the schema by name.

        Args:
            field_name: Name of the field to remove

        Returns:
            Self for method chaining
        """
        self.fields = [f for f in self.fields if f.name != field_name]
        return self

    def get_field(self, field_name: str) -> Optional[FieldSchema]:
        """Get a field by name."""
        for field in self.fields:
            if field.name == field_name:
                return field
        return None

    def has_field(self, field_name: str) -> bool:
        """Check if a field exists in the schema."""
        return self.get_field(field_name) is not None

    def get_primary_key_field(self) -> Optional[FieldSchema]:
        """Get the primary key field if exists."""
        for field in self.fields:
            if field.is_primary:
                return field
        return None

    def get_vector_fields(self) -> List[FieldSchema]:
        """Get all vector fields in the schema."""
        return [f for f in self.fields if f.dtype == VectorDataType.FLOAT_VECTOR]

    def to_dict(self) -> Dict[str, Any]:
        """Convert schema to dictionary format."""
        return {
            "fields": [f.to_dict() for f in self.fields],
            "description": self.description,
            "enable_dynamic_field": self.enable_dynamic_field,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CollectionSchema":
        """Create schema from dictionary format."""
        fields = []
        for field_data in data.get("fields", []):
            fields.append(FieldSchema.from_dict(field_data))

        return cls(
            fields=fields,
            description=data.get("description"),
            enable_dynamic_field=data.get("enable_dynamic_field", False),
        )

    @classmethod
    def from_fields(cls, fields: List[Union[FieldSchema, Dict[str, Any]]], **kwargs: Any) -> "CollectionSchema":
        """
        Create schema from a list of field definitions.

        Args:
            fields: List of FieldSchema instances or field dictionaries
            **kwargs: Additional schema parameters (description, enable_dynamic_field)

        Returns:
            CollectionSchema instance
        """
        schema = cls(**kwargs)
        for field in fields:
            schema.add_field(field)
        return schema


class VectorSearchResult(BaseModel):
    """
    Result of a vector search operation.

    Contains the relevance score and all field values from the matched document.
    """

    score: float = Field(..., description="Relevance score (higher is more relevant)")
    fields: Dict[str, Any] = Field(
        default_factory=dict,
        description="All field values from the matched document, including id, text, metadata, etc.",
    )



class BaseVectorStore(ABC):
    """
    Abstract base class defining a unified interface for vector storage.

    This class provides a common interface for vector database operations,
    supporting collection management, document insertion, vector search,
    and document deletion.
    """

    @abstractmethod
    async def create_collection(
        self,
        collection_name: str,
        schema: Union[CollectionSchema, Dict[str, Any]],
        **kwargs: Any,
    ) -> None:
        """
        Create a new collection with specified schema.

        Args:
            collection_name: Name of the collection to create
            schema: CollectionSchema instance or schema dictionary containing:
                - fields (List[Dict]): List of field definitions
                    - name (str): Field name
                    - type (str): Field type (e.g., "VARCHAR", "FLOAT_VECTOR", "INT64")
                    - Optional parameters based on field type:
                        - For VARCHAR: max_length (int)
                        - For FLOAT_VECTOR: dim (int) - vector dimension
                        - For primary key: is_primary (bool), auto_id (bool)
                        - Other field-specific parameters
                - description (str, optional): Collection description
                - enable_dynamic_field (bool, optional): Enable dynamic fields
            **kwargs: Additional parameters for collection creation
                - distance_metric (str, optional): Distance metric for vector search

        Example:
            # Using CollectionSchema
            from openjiuwen.core.foundation.store.base_vector_store import CollectionSchema, FieldSchema, VectorDataType

            schema = CollectionSchema(
                description="My document collection",
                enable_dynamic_field=False,
            )
            schema.add_field(FieldSchema(
                name="id",
                dtype=VectorDataType.VARCHAR,
                max_length=256,
                is_primary=True,
            ))
            schema.add_field(FieldSchema(
                name="embedding",
                dtype=VectorDataType.FLOAT_VECTOR,
                dim=768,
            ))
            schema.add_field(FieldSchema(
                name="text",
                dtype=VectorDataType.VARCHAR,
                max_length=65535,
            ))
            schema.add_field(FieldSchema(
                name="metadata",
                dtype=VectorDataType.JSON,
            ))
            await store.create_collection("my_collection", schema)

            # Using dictionary (backward compatible)
            schema_dict = {
                "fields": [
                    {
                        "name": "id",
                        "type": "VARCHAR",
                        "max_length": 256,
                        "is_primary": True,
                    },
                    {
                        "name": "embedding",
                        "type": "FLOAT_VECTOR",
                        "dim": 768,
                    },
                    {
                        "name": "text",
                        "type": "VARCHAR",
                        "max_length": 65535,
                    },
                    {
                        "name": "metadata",
                        "type": "JSON",
                    },
                ],
                "description": "My document collection",
                "enable_dynamic_field": False,
            }
            await store.create_collection("my_collection", schema_dict)
        """
        pass

    @abstractmethod
    async def delete_collection(
        self,
        collection_name: str,
        **kwargs: Any,
    ) -> None:
        """
        Delete a collection by name.

        Args:
            collection_name: Name of the collection to delete
            **kwargs: Additional parameters for collection deletion

        Raises:
            Exception: If the collection does not exist or deletion fails
        """
        pass

    @abstractmethod
    async def collection_exists(
        self,
        collection_name: str,
        **kwargs: Any,
    ) -> bool:
        """
        Check if a collection exists.

        Args:
            collection_name: Name of the collection to check
            **kwargs: Additional parameters for collection existence check

        Returns:
            bool: True if the collection exists, False otherwise

        Example:
            exists = await store.collection_exists("my_collection")
            if exists:
                print("Collection exists")
            else:
                print("Collection does not exist")
        """
        pass

    @abstractmethod
    async def get_schema(
        self,
        collection_name: str,
        **kwargs: Any,
    ) -> CollectionSchema:
        """
        Get the schema of a collection.

        Args:
            collection_name: Name of the collection
            **kwargs: Additional parameters for getting schema

        Returns:
            CollectionSchema: The schema of the collection

        Raises:
            ValueError: If the collection does not exist
            Exception: If getting schema fails

        Example:
            schema = await store.get_schema("my_collection")
            print(f"Schema has {len(schema.fields)} fields")
            for field in schema.fields:
                print(f"  {field.name}: {field.dtype}")
        """
        pass

    @abstractmethod
    async def add_docs(
        self,
        collection_name: str,
        docs: List[Dict[str, Any]],
        **kwargs: Any,
    ) -> None:
        """
        Add documents to a collection.

        Args:
            collection_name: Name of the target collection
            docs: List of documents to add. Each document is a dictionary containing:
                - id (str, optional): Document ID. If not provided, may be auto-generated
                - embedding (List[float]): Vector embedding of the document
                - text (str): Text content of the document
                - metadata (Dict[str, Any], optional): Additional metadata
                - Other fields as defined in the collection schema
            **kwargs: Additional parameters for document insertion
                - batch_size (int, optional): Batch size for bulk insertion

        Example:
            docs = [
                {
                    "id": "doc_1",
                    "embedding": [0.1, 0.2, 0.3, ...],
                    "text": "Document content",
                    "metadata": {"source": "file1", "page": 1},
                },
                ...
            ]
        """
        pass

    @abstractmethod
    async def search(
        self,
        collection_name: str,
        query_vector: List[float],
        vector_field: str,
        top_k: int = 5,
        filters: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> List[VectorSearchResult]:
        """
        Search for the most relevant documents by vector similarity.

        Args:
            collection_name: Name of the collection to search
            query_vector: Query vector for similarity search
            vector_field: Name of the vector field to search against (e.g., "embedding")
            top_k: Number of most relevant documents to return
            filters: Optional dictionary of scalar field filters for filtering results.
                Keys are field names, values are the field values to match (equality filter only).
                Example: {"category": "tech", "status": "active"}
            **kwargs: Additional search parameters
                - metric_type (str, optional): Distance metric (e.g., "COSINE", "L2", "IP")
                - output_fields (List[str], optional): Fields to return in results

        Returns:
            List of VectorSearchResult objects, each containing:
                - score (float): Relevance score (higher is more relevant)
                - fields (Dict[str, Any]): All field values from the matched document,
                  including id, text, metadata, and other fields as defined in the collection schema

        Example:
            results = await store.search(
                collection_name="my_collection",
                query_vector=[0.1, 0.2, 0.3, ...],
                vector_field="embedding",
                top_k=10,
                filters={"category": "tech", "status": "active"},
            )
            # Access results
            for result in results:
                print(f"Score: {result.score}")
                print(f"ID: {result.fields.get('id')}")
                print(f"Text: {result.fields.get('text')}")
                print(f"Metadata: {result.fields.get('metadata')}")
        """
        pass

    @abstractmethod
    async def delete_docs_by_ids(
        self,
        collection_name: str,
        ids: List[str],
        **kwargs: Any,
    ) -> None:
        """
        Delete documents by their IDs.

        Args:
            collection_name: Name of the collection
            ids: List of document IDs to delete
            **kwargs: Additional parameters for deletion

        Raises:
            Exception: If deletion fails
        """
        pass

    @abstractmethod
    async def delete_docs_by_filters(
        self,
        collection_name: str,
        filters: Dict[str, Any],
        **kwargs: Any,
    ) -> None:
        """
        Delete documents by scalar field filters.

        Args:
            collection_name: Name of the collection
            filters: Dictionary of scalar field filters for matching documents to delete.
                Keys are field names, values are the field values to match (equality filter only).
                Example: {"category": "tech", "status": "inactive"}
            **kwargs: Additional parameters for deletion

        Raises:
            Exception: If deletion fails

        Example:
            await store.delete_docs_by_filters(
                collection_name="my_collection",
                filters={"category": "tech", "status": "inactive"},
            )
        """
        pass

    @abstractmethod
    async def list_collection_names(self) -> List[str]:
        """
        List all collection names in the vector store.

        Returns:
            List[str]: A list of collection names.
        """
        pass

    @abstractmethod
    async def update_schema(self, collection_name: str, operations: List["BaseOperation"]) -> None:
        """
        Upgrade the schema field for vector data migration.

        """
        pass

    @abstractmethod
    async def update_collection_metadata(self, collection_name: str, metadata: Dict[str, Any]) -> None:
        """
        Update the schema version of a collection.
        """
        pass

    @abstractmethod
    async def get_collection_metadata(self, collection_name: str) -> Dict[str, Any]:
        """
        Get the schema version of a collection.
        """
        pass