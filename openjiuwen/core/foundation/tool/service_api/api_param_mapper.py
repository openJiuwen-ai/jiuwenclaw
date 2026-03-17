# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from enum import Enum
from typing import Dict, Any, Optional, Type

from openai import BaseModel

from openjiuwen.core.common.utils.schema_utils import SchemaUtils


class APIParamLocation(Enum):
    """API parameter locations based on OpenAPI specification."""
    QUERY = "query"  # Query parameters in URL (e.g., ?key=value)
    PATH = "path"  # Path parameters in URL (e.g., /users/{id})
    BODY = "body"  # Request body parameters
    HEADER = "header"  # HTTP header parameters


class APIParamMapper:
    """Maps input parameters to their corresponding API locations (query, path, body, header).

    This class handles parameter distribution based on schema definitions and provides
    default value merging for query, path, and header parameters.
    """
    _LOCATION = "location"  # Key name for location specification in schema

    def __init__(
            self,
            schema: Dict[str, Any] | Type[BaseModel],
            default_queries: Optional[Dict[str, Any]] = None,
            default_headers: Optional[Dict[str, Any]] = None,
            default_paths: Optional[Dict[str, Any]] = None
    ):
        """Initialize the parameter mapper with schema and default values.

        Args:
            schema: OpenAPI schema defining parameter locations and properties
            default_queries: Default query parameters to merge with inputs
            default_headers: Default header parameters to merge with inputs
            default_paths: Default path parameters to merge with inputs
        """
        self.schema = schema if isinstance(schema, dict) else SchemaUtils.get_schema_dict(schema)
        # Store defaults for different parameter locations
        self.defaults = {
            APIParamLocation.QUERY: default_queries or {},
            APIParamLocation.HEADER: default_headers or {},
            APIParamLocation.PATH: default_paths or {},
        }

    def map(self,
            inputs: Dict[str, Any],
            default_location: APIParamLocation = APIParamLocation.BODY) -> Dict[APIParamLocation, Any]:
        """Map input parameters to their respective API locations.

        Args:
            inputs: Dictionary of input parameters to be mapped
            default_location: Default location for parameters without explicit location in schema

        Returns:
            Dictionary mapping APIParamLocation to dictionary of parameters for that location
        """
        if self.schema is None:
            result = {default_location: inputs}
        else:
            result = {location: {} for location in APIParamLocation}
            for param_name, param_schema in self.schema.get("properties", {}).items():
                if param_name in inputs:
                    location_str = param_schema.get(self._LOCATION, default_location)
                    if location_str:
                        location = APIParamLocation(location_str.lower()) if isinstance(location_str,
                                                                                        str) else location_str
                        result.get(location, {}).update({param_name: inputs.get(param_name)})
        for location in [APIParamLocation.PATH, APIParamLocation.QUERY, APIParamLocation.HEADER]:
            # Input values override default values (dictionary unpacking order matters)
            if location not in result:
                result[location] = {}
            result[location] = {**self.defaults.get(location, {}), **result[location]}

        return result
