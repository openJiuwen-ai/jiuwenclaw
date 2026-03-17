# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class ParamType(Enum):
    """Parameter type enumeration"""
    String = "string"
    Boolean = "boolean"
    Integer = "integer"
    Number = "number"
    Array = "array"
    Object = "object"


class Param(BaseModel):
    """
    Parameter definition model with nested structure support
    
    Used to describe input parameters for agents and workflows.
    Supports both basic types and complex nested types.
    
    Design principle:
        - `items` field is ONLY used when type is Array
        - `properties` field is ONLY used when type is Object
        - For other types, these fields must be None
        - Validation ensures data consistency
    
    Attributes:
        name: Parameter name
        description: Parameter description
        type: Parameter type
        required: Whether the parameter is required
        default: Default value (optional)
        items: Array element type definition (ONLY for Array type)
        properties: Object property list (ONLY for Object type)
    
    Examples:
        Simple string parameter:
        >>> Param.string(name="username", description="Username", required=True)
        
        Array parameter (array of strings):
        >>> Param.array(
        ...     name="tags",
        ...     description="Tag list",
        ...     required=False,
        ...     items=Param.string(name="tag", description="Tag", required=True)
        ... )
        
        Object parameter:
        >>> Param.object(
        ...     name="user",
        ...     description="User information",
        ...     required=True,
        ...     properties=[
        ...         Param.string(name="name", description="Name", required=True),
        ...         Param.integer(name="age", description="Age", required=False)
        ...     ]
        ... )
    """
    name: str = Field(..., description="Parameter name")
    description: str = Field(..., description="Parameter description")
    type: ParamType = Field(..., description="Parameter type")
    required: bool = Field(..., description="Whether the parameter is required")
    default: Optional[Any] = Field(None, description="Default value")
    
    # These fields are ONLY meaningful for specific types
    items: Optional["Param"] = Field(
        None,
        description="[ONLY for Array] Type definition of array elements"
    )
    properties: Optional[list["Param"]] = Field(
        None,
        description="[ONLY for Object] Property list of the object"
    )
    
    @model_validator(mode='after')
    def validate_type_specific_fields(self) -> 'Param':
        """
        Validate that type-specific fields are used correctly
        
        Rules:
            - Array type MUST have items, MUST NOT have properties
            - Object type MUST have properties, MUST NOT have items
            - Other types MUST NOT have items or properties
        """
        if self.type == ParamType.Array:
            if self.items is None:
                raise ValueError(
                    f"Param '{self.name}': Array type requires 'items' field"
                )
            if self.properties is not None:
                raise ValueError(
                    f"Param '{self.name}': Array type should not have 'properties' field"
                )
        
        elif self.type == ParamType.Object:
            if self.properties is None:
                raise ValueError(
                    f"Param '{self.name}': Object type requires 'properties' field"
                )
            if self.items is not None:
                raise ValueError(
                    f"Param '{self.name}': Object type should not have 'items' field"
                )
        
        else:
            # Simple types: String, Boolean, Integer, Number
            if self.items is not None:
                raise ValueError(
                    f"Param '{self.name}': {self.type.value} type should not have 'items' field"
                )
            if self.properties is not None:
                raise ValueError(
                    f"Param '{self.name}': {self.type.value} type should not have 'properties' field"
                )
        
        return self
    
    # Factory methods for better readability and ease of use
    
    @classmethod
    def string(
        cls,
        name: str,
        description: str,
        required: bool,
        default: Optional[str] = None
    ) -> "Param":
        """Create a string type parameter"""
        return cls(
            name=name,
            description=description,
            type=ParamType.String,
            required=required,
            default=default
        )
    
    @classmethod
    def boolean(
        cls,
        name: str,
        description: str,
        required: bool,
        default: Optional[bool] = None
    ) -> "Param":
        """Create a boolean type parameter"""
        return cls(
            name=name,
            description=description,
            type=ParamType.Boolean,
            required=required,
            default=default
        )
    
    @classmethod
    def integer(
        cls,
        name: str,
        description: str,
        required: bool,
        default: Optional[int] = None
    ) -> "Param":
        """Create an integer type parameter"""
        return cls(
            name=name,
            description=description,
            type=ParamType.Integer,
            required=required,
            default=default
        )
    
    @classmethod
    def number(
        cls,
        name: str,
        description: str,
        required: bool,
        default: Optional[float] = None
    ) -> "Param":
        """Create a number (float) type parameter"""
        return cls(
            name=name,
            description=description,
            type=ParamType.Number,
            required=required,
            default=default
        )
    
    @classmethod
    def array(
        cls,
        name: str,
        description: str,
        required: bool,
        items: "Param",
        default: Optional[list] = None
    ) -> "Param":
        """
        Create an array type parameter
        
        Args:
            name: Parameter name
            description: Parameter description
            required: Whether the parameter is required
            items: Type definition of array elements (required)
            default: Default value
        """
        return cls(
            name=name,
            description=description,
            type=ParamType.Array,
            required=required,
            items=items,
            default=default
        )
    
    @classmethod
    def object(
        cls,
        name: str,
        description: str,
        required: bool,
        properties: list["Param"],
        default: Optional[dict] = None
    ) -> "Param":
        """
        Create an object type parameter
        
        Args:
            name: Parameter name
            description: Parameter description
            required: Whether the parameter is required
            properties: Property list of the object (required)
            default: Default value
        """
        return cls(
            name=name,
            description=description,
            type=ParamType.Object,
            required=required,
            properties=properties,
            default=default
        )
