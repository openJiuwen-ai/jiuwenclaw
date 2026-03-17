# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import typing
from typing import Any, Optional, Type, Union, Dict, List, get_type_hints
from copy import deepcopy
from jsonschema import validate as jsonschema_validate, ValidationError as JsonSchemaValidationError
from pydantic import BaseModel, create_model, Field, ConfigDict
from pydantic.fields import FieldInfo

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import ValidationError, build_error


class SchemaUtils:
    """
    Schema utility class for handling JSON Schema and Pydantic model conversions,
    data formatting, and validation.

    This class provides bidirectional conversion between JSON Schema dictionaries
    and Pydantic models, along with data formatting and validation capabilities.
    """

    @staticmethod
    def format_with_schema(data: Any,
                           schema: Union[Dict[str, Any], Type[BaseModel]],
                           *,
                           skip_none_value: bool = False,
                           skip_validate: bool = False) -> Any:
        """
        Format data according to the provided schema, filling in default values.

        Args:
            data: The data to be formatted
            schema: Either a JSON Schema dictionary or a Pydantic BaseModel class
            skip_none_value: Skip none value of data
            skip_validate: Skip validate of data

        Returns:
            Formatted data with default values populated

        Raises:
            ValidationError: If data cannot be formatted according to the schema
        """
        try:
            new_data = SchemaUtils.remove_none_values(data) if skip_none_value else data

            # First validate the data
            if not skip_validate:
                SchemaUtils.validate_with_schema(new_data, schema)

            # Get the appropriate model
            if isinstance(schema, dict):
                model = SchemaUtils.get_schema_class(schema)
            else:
                model = schema

            # Format the data using the model
            return SchemaUtils._format_data(new_data, model)
        except ValidationError as e:
            raise e
        except Exception as e:
            # Wrap the exception in a custom business exception
            raise build_error(StatusCode.SCHEMA_FORMAT_INVALID, cause=e, reason=str(e), data=data) from e

    @staticmethod
    def remove_none_values(data: Any) -> Any:
        """
            Recursively remove None values from a data structure.

            This method traverses through dictionaries and lists, removing any None values
            while preserving the structure for non-None values. This is useful when
            validating data against schemas where None values should be treated as
            "not provided" rather than explicit null values.

            Args:
                data: The input data to clean. Can be of any type including dict, list,
                      str, int, float, bool, or None.

            Returns:
                The cleaned data structure with None values removed. The return types are:
                - None if input is None
                - Dict without None values (or None if all values were None)
                - List without None values (or None if all items were None)
                - Original value for basic types (str, int, float, bool)
                - String representation for other types, or None if conversion fails
        """
        if data is None:
            return None

        if isinstance(data, dict):
            cleaned_dict = {}
            for key, value in data.items():
                cleaned_value = SchemaUtils.remove_none_values(value)
                if cleaned_value is not None:
                    cleaned_dict[key] = cleaned_value
            return cleaned_dict if cleaned_dict else None

        elif isinstance(data, list):
            cleaned_list = []
            for item in data:
                cleaned_item = SchemaUtils.remove_none_values(item)
                if cleaned_item is not None:
                    cleaned_list.append(cleaned_item)
            return cleaned_list if cleaned_list else None

        elif isinstance(data, (str, int, float, bool)):
            return data

        else:
            try:
                return str(data)
            except:
                return None

    @staticmethod
    def validate_with_schema(data: Any, schema: Union[Dict[str, Any], Type[BaseModel]]) -> None:
        """
        Validate data against the provided schema.

        Args:
            data: The data to be validated
            schema: Either a JSON Schema dictionary or a Pydantic BaseModel class

        Raises:
            ValidationError: If data fails pydantic validation or jsonschema validation
        """
        try:
            if isinstance(schema, dict):
                try:
                    # Try jsonschema validation first
                    jsonschema_validate(data, schema)
                except JsonSchemaValidationError:
                    # If jsonschema fails, try pydantic validation
                    model = SchemaUtils.get_schema_class(schema)
                    model.model_validate(data)
            else:
                # Use pydantic validation directly
                schema.model_validate(data)
        except Exception as e:
            # Wrap the exception in a custom business exception
            raise build_error(StatusCode.SCHEMA_VALIDATE_INVALID, cause=e, reason=str(e), data=data)

    @staticmethod
    def get_schema_dict(schema: Type[BaseModel]) -> Optional[Dict[str, Any]]:
        """
        Convert a Pydantic model to a JSON Schema dictionary.

        Args:
            schema: A Pydantic BaseModel class

        Returns:
            JSON Schema dictionary representation of the model
        """
        if schema is None:
            return None
        # Get the basic JSON schema from pydantic
        schema_dict = schema.model_json_schema()

        # Enhance with additional information
        schema_dict = SchemaUtils._enhance_schema_dict(schema_dict, schema)

        return schema_dict

    @staticmethod
    def get_schema_class(schema_dict: Dict[str, Any]) -> Optional[Type[BaseModel]]:
        """
        Convert a JSON Schema dictionary to a Pydantic model.

        Args:
            schema_dict: JSON Schema dictionary

        Returns:
            Pydantic BaseModel class generated from the schema
        """
        if schema_dict is None:
            return None
        return SchemaUtils._create_model_from_schema(schema_dict)

    @staticmethod
    def _format_data(data: Any, model: Type[BaseModel]) -> Any:
        """
        Internal method to format data using a Pydantic model.

        Args:
            data: The data to format
            model: Pydantic model to use for formatting

        Returns:
            Formatted data with defaults filled in
        """
        result = deepcopy(data)

        # Handle None data
        if result is None:
            instance = model()
            return instance.model_dump(exclude_unset=False)

        # Validate and format using the model
        instance = model.model_validate(result)
        final_result = instance.model_dump(exclude_unset=False)

        return final_result

    @staticmethod
    def _create_model_from_schema(schema_dict: Dict[str, Any]) -> Type[BaseModel]:
        """
        Create a Pydantic model from a JSON Schema dictionary.

        Args:
            schema_dict: JSON Schema dictionary

        Returns:
            Dynamic Pydantic model class
        """
        schema_type = schema_dict.get("type", "object")

        # Handle non-object types by creating a wrapper model
        if schema_type != "object":
            return SchemaUtils._create_wrapper_model(schema_dict)

        # Extract properties and required fields
        properties = schema_dict.get("properties", {})
        required = schema_dict.get("required", [])
        additional_properties = schema_dict.get("additionalProperties", False)
        extra_config = "ignore"
        if additional_properties is True:
            extra_config = "allow"
        elif additional_properties is False:
            extra_config = "forbid"
        else:
            extra_config = 'ignore'

        # Build field definitions
        field_definitions = {}

        for field_name, field_schema in properties.items():
            field_type = SchemaUtils._convert_schema_to_type(field_schema)
            # For optional fields and don't have default values
            if field_name not in required:
                field_type = typing.Optional[field_type]
            field_config = SchemaUtils._convert_schema_to_field(field_schema, field_name in required)
            field_definitions[field_name] = (field_type, field_config)

        # Configure model behavior
        config = ConfigDict(
            extra=extra_config,
            validate_default=True,  # Validate default values
            json_schema_extra={"schema": schema_dict}  # Preserve original schema
        )

        # Create the dynamic model
        model_name = schema_dict.get("title", "DynamicModel")
        model = create_model(
            model_name,
            __config__=config,
            **field_definitions
        )

        return model

    @staticmethod
    def _create_wrapper_model(schema_dict: Dict[str, Any]) -> Type[BaseModel]:
        """
        Create a wrapper model for non-object schema types.

        Args:
            schema_dict: JSON Schema dictionary for a non-object type

        Returns:
            Pydantic model that wraps the value in a 'value' field
        """
        value_type = SchemaUtils._convert_schema_to_type(schema_dict)
        default_value = schema_dict.get("default")

        return create_model(
            "WrapperModel",
            value=(value_type, Field(default=default_value)),
            __config__=ConfigDict(extra="ignore")
        )

    @staticmethod
    def _convert_schema_to_type(schema: Dict[str, Any]) -> Any:
        """
        Convert JSON Schema type definition to Python type.

        Args:
            schema: JSON Schema fragment containing type information

        Returns:
            Python type or typing construct
        """
        schema_type = schema.get("type")

        # Infer type if not explicitly specified
        if not schema_type:
            if "properties" in schema:
                return Dict[str, Any]
            elif "items" in schema:
                return List[Any]
            else:
                return Any

        # Handle union types (multiple allowed types)
        if isinstance(schema_type, list):
            types = []
            has_null = False

            # Process each type in the union
            for t in schema_type:
                if t == "null":
                    has_null = True
                else:
                    types.append(SchemaUtils._single_type_to_python(t))

            if not types:
                return Any

            # Create union type
            union_type = typing.Union[tuple(types)] if len(types) > 1 else types[0]

            # Handle optional types (union with None)
            if has_null:
                return typing.Optional[union_type]
            else:
                return union_type

        # Single type
        return SchemaUtils._single_type_to_python(schema_type)

    @staticmethod
    def _single_type_to_python(schema_type: str) -> Any:
        """
        Convert a single JSON Schema type to Python type.

        Args:
            schema_type: JSON Schema type string

        Returns:
            Corresponding Python type
        """
        type_mapping = {
            "string": str,
            "integer": int,
            "number": float,
            "boolean": bool,
            "array": List,
            "object": Dict,
            "null": type(None),
            "any": Any
        }

        return type_mapping.get(schema_type, Any)

    @staticmethod
    def _convert_schema_to_field(schema: Dict[str, Any], required: bool = False) -> FieldInfo:
        """
        Convert JSON Schema field definition to Pydantic Field configuration.

        Args:
            schema: JSON Schema field definition
            required: Whether the field is required

        Returns:
            Pydantic FieldInfo configuration
        """
        field_kwargs = {}

        # Basic field properties
        if "default" in schema:
            field_kwargs["default"] = schema["default"]
        elif not required:
            field_kwargs["default"] = None

        if "title" in schema:
            field_kwargs["title"] = schema["title"]
        if "description" in schema:
            field_kwargs["description"] = schema["description"]

        # String-specific constraints
        if schema.get("type") == "string":
            if "minLength" in schema:
                field_kwargs["min_length"] = schema["minLength"]
            if "maxLength" in schema:
                field_kwargs["max_length"] = schema["maxLength"]
            if "pattern" in schema:
                field_kwargs["pattern"] = schema["pattern"]
            if "format" in schema:
                if schema["format"] == "email":
                    return Field(**field_kwargs, email=True)
                elif schema["format"] == "uri":
                    return Field(**field_kwargs, url=True)

        # Numeric-specific constraints
        elif schema.get("type") in ["integer", "number"]:
            if "minimum" in schema:
                field_kwargs["ge"] = schema["minimum"]
            if "maximum" in schema:
                field_kwargs["le"] = schema["maximum"]
            if "exclusiveMinimum" in schema:
                field_kwargs["gt"] = schema["exclusiveMinimum"]
            if "exclusiveMaximum" in schema:
                field_kwargs["lt"] = schema["exclusiveMaximum"]
            if "multipleOf" in schema:
                field_kwargs["multiple_of"] = schema["multipleOf"]

        # Array-specific constraints
        elif schema.get("type") == "array":
            if "minItems" in schema:
                field_kwargs["min_length"] = schema["minItems"]
            if "maxItems" in schema:
                field_kwargs["max_length"] = schema["maxItems"]
            if "uniqueItems" in schema:
                field_kwargs["unique_items"] = schema["uniqueItems"]

            # Handle array item type
            if "items" in schema:
                items_schema = schema["items"]
                if isinstance(items_schema, dict):
                    items_type = SchemaUtils._convert_schema_to_type(items_schema)
                    field_kwargs["annotation"] = List[items_type]

        # Enum support
        if "enum" in schema:
            field_kwargs["enum"] = schema["enum"]

        return Field(**field_kwargs)

    @staticmethod
    def _enhance_schema_dict(schema_dict: Dict[str, Any], model: Type[BaseModel]) -> Dict[str, Any]:
        """
        Enhance JSON Schema dictionary with additional information from Pydantic model.

        Args:
            schema_dict: Base JSON schema from pydantic
            model: Source Pydantic model

        Returns:
            Enhanced JSON schema with default values and type information
        """
        enhanced = deepcopy(schema_dict)

        # Enhance field definitions with model information
        if "properties" in enhanced:
            model_fields = model.model_fields
            for field_name, field_schema in enhanced["properties"].items():
                if field_name in model_fields:
                    field_info = model_fields[field_name]

                    # Add default value if available
                    if field_info.default is not None:
                        field_schema["default"] = field_info.default

                    # Add type information if missing
                    if "type" not in field_schema:
                        field_schema["type"] = SchemaUtils._python_type_to_json_type(field_info.annotation)

        return enhanced

    @staticmethod
    def _python_type_to_json_type(python_type: Any) -> Union[str, List[str]]:
        """
        Convert Python type to JSON Schema type string.

        Args:
            python_type: Python type or typing construct

        Returns:
            JSON Schema type string or list of type strings
        """
        type_str = str(python_type)

        # Map Python types to JSON Schema types
        if "str" in type_str:
            return "string"
        elif "int" in type_str:
            return "integer"
        elif "float" in type_str or "Decimal" in type_str:
            return "number"
        elif "bool" in type_str:
            return "boolean"
        elif "list" in type_str or "List" in type_str:
            return "array"
        elif "dict" in type_str or "Dict" in type_str:
            return "object"
        elif "None" in type_str or "NoneType" in type_str:
            return "null"
        elif "Any" in type_str:
            # Any type accepts all JSON types
            return ["string", "integer", "number", "boolean", "array", "object", "null"]
        else:
            # Default to string for unknown types
            return "string"
