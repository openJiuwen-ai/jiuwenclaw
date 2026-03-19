# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import asyncio
from copy import deepcopy
from typing import AsyncIterator, ClassVar, Dict, Any, Literal, Set

import aiohttp
from oauthlib.common import urlencode
from pydantic import Field, field_validator

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.security.ssl_utils import SslUtils
from openjiuwen.core.common.security.url_utils import UrlUtils
from openjiuwen.core.common.utils.schema_utils import SchemaUtils
from openjiuwen.core.foundation.tool import Tool
from openjiuwen.core.foundation.tool.base import Input, Output, ToolCard
from openjiuwen.core.foundation.tool.service_api.api_param_mapper import APIParamLocation, APIParamMapper
from openjiuwen.core.foundation.tool.service_api.response_parser import ParserRegistry
from openjiuwen.core.runner.callback import trigger
from openjiuwen.core.runner.callback.events import ToolCallEvents


class RestfulApiCard(ToolCard):
    """RESTful API tool card with HTTP method validation."""
    SUPPORTED_METHODS: ClassVar[Set[str]] = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
    url: str = Field(..., description="Restful API path, such as: /api/v1/users")
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"] = Field(
        default="POST",
        description="HTTP method (GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS)"
    )
    headers: Dict[str, Any] = Field(default_factory=dict, description="Request headers")
    queries: Dict[str, Any] = Field(default_factory=dict, description="Request query parameters")
    paths: Dict[str, Any] = Field(default_factory=dict, description="Path parameters for URL placeholders")
    timeout: float = Field(default=60.0, ge=1.0, le=300.0, description="Request timeout in seconds")
    max_response_byte_size: int = Field(default=10 * 1024 * 1024, description="Response max size in bytes")

    @field_validator('method')
    @classmethod
    def validate_method(cls, v: str) -> str:
        v_upper = v.upper()
        if v_upper not in cls.SUPPORTED_METHODS:
            raise build_error(StatusCode.TOOL_RESTFUL_API_CARD_CONFIG_INVALID,
                              reason=f"support invalid method, method={v}, only accepts: {cls.SUPPORTED_METHODS}.")
        return v_upper

    @field_validator('url')
    @classmethod
    def validate_url(cls, v: str) -> str:
        try:
            UrlUtils.check_url_is_valid(v)
        except Exception as e:
            raise build_error(StatusCode.TOOL_RESTFUL_API_CARD_CONFIG_INVALID, cause=e,
                              reason=f"support invalid url, url={v}.")

        return v

    def model_post_init(self, __context):
        """Validate that URL path parameters are properly defined in input_params schema."""
        import re

        # Extract path parameter names from URL (e.g., {id}, {userId})
        url_path_params = set(re.findall(r'\{(\w+)\}', self.url))

        if not url_path_params:
            return  # No path parameters in URL, nothing to validate

        # Check if input_params schema is defined
        if not self.input_params:
            raise build_error(
                StatusCode.TOOL_RESTFUL_API_CARD_CONFIG_INVALID,
                reason=f"URL contains path parameters {url_path_params} but input_params schema is not defined. "
                       f"You must define input_params with 'location': 'path' for each path parameter. "
                       f"Example: {{'type': 'object', 'properties': {{'id': {{'type': 'integer', 'location': "
                       f"'path'}}}}}}"
            )

        # Get schema properties
        schema = self.input_params if isinstance(self.input_params, dict) else {}
        properties = schema.get("properties", {})

        # Find which parameters are marked as path parameters
        schema_path_params = set()
        for param_name, param_def in properties.items():
            if param_def.get("location") == "path":
                schema_path_params.add(param_name)

        # Check if all URL path parameters are defined in schema
        missing_in_schema = url_path_params - schema_path_params
        if missing_in_schema:
            raise build_error(
                StatusCode.TOOL_RESTFUL_API_CARD_CONFIG_INVALID,
                reason=f"URL contains path parameters {missing_in_schema} that are not defined in input_params schema "
                       f"with 'location': 'path'. Please add them to your schema. "
                       f"Example: '{list(missing_in_schema)[0]}': {{'type': 'string', "
                       f"'description': 'Parameter description', 'location': 'path'}}"
            )

        # Warn if schema has path parameters not in URL (not an error, just informational)
        extra_in_schema = schema_path_params - url_path_params
        if extra_in_schema:
            logger.warn(
                f"Schema defines path parameters {extra_in_schema} that are not used in URL {self.url}",
                UserWarning
            )


class RestfulApi(Tool):
    _RESTFUL_SSL_VERIFY = "RESTFUL_SSL_VERIFY"
    _RESTFUL_SSL_CERT = "RESTFUL_SSL_CERT"

    def __init__(self, card: RestfulApiCard):
        super().__init__(card)
        self._url = card.url
        self._method = card.method
        self._timeout = card.timeout
        self._max_response_byte_size = card.max_response_byte_size
        self._api_param_mapper = APIParamMapper(self._card.input_params,
                                                default_queries=card.queries,
                                                default_paths=card.paths,
                                                default_headers=card.headers)

    @staticmethod
    def get_parameters_by_location(card: RestfulApiCard) -> Dict[str, list]:
        """
        Helper method for GUI: Extract parameters organized by location.

        This is useful for GUI tools that need to display different input sections
        for path parameters, query parameters, headers, and body parameters.

        Args:
            card: RestfulApiCard configuration

        Returns:
            Dictionary with keys: 'path', 'query', 'header', 'body'
            Each value is a list of parameter definitions with: name, type, description, required

        Example:
            >>> card = RestfulApiCard(
            ...     url="http://api.example.com/api/v1/Activities/{id}",
            ...     method="PUT",
            ...     input_params={
            ...         "type": "object",
            ...         "properties": {
            ...             "id": {"type": "integer", "description": "Activity ID", "location": "path"},
            ...             "name": {"type": "string", "description": "Activity name", "location": "body"}
            ...         },
            ...         "required": ["id"]
            ...     }
            ... )
            >>> params = RestfulApi.get_parameters_by_location(card)
            >>> params['path']
            [{'name': 'id', 'type': 'integer', 'description': 'Activity ID', 'required': True}]
            >>> params['body']
            [{'name': 'name', 'type': 'string', 'description': 'Activity name', 'required': False}]
        """
        result = {
            "path": [],
            "query": [],
            "header": [],
            "body": []
        }

        if not card.input_params:
            return result

        schema = card.input_params if isinstance(card.input_params, dict) else {}
        properties = schema.get("properties", {})
        required_fields = schema.get("required", [])

        for param_name, param_def in properties.items():
            location = param_def.get("location", "body")  # Default to body if not specified

            param_info = {
                "name": param_name,
                "type": param_def.get("type", "string"),
                "description": param_def.get("description", ""),
                "required": param_name in required_fields,
                "default": param_def.get("default")
            }

            result.setdefault(location, []).append(param_info)

        return result

    async def _async_request(self, map_results: dict, timeout: float, max_response_byte_size: int,
                             raise_for_status: True, request_args: dict = None):
        request_arg = deepcopy(request_args) if request_args and isinstance(request_args, dict) else {}
        # Methods that typically don't send body as JSON (send as query params instead)
        # GET, HEAD, OPTIONS, DELETE use params; POST, PUT, PATCH use json body
        # Note: While DELETE CAN have a body per HTTP spec, it's uncommon in REST APIs
        # If you need DELETE with body, explicitly mark parameters with "location": "body" in schema
        if self._method in ["GET", "HEAD", "OPTIONS", "DELETE"]:
            request_arg["params"] = map_results.get(APIParamLocation.BODY)
        else:
            # POST, PUT, PATCH send data as JSON in request body
            request_arg["json"] = map_results.get(APIParamLocation.BODY)
        ssl_verify, ssl_cert = SslUtils.get_ssl_config(self._RESTFUL_SSL_VERIFY, self._RESTFUL_SSL_CERT, ["false"])
        if ssl_verify:
            ssl_context = SslUtils.create_strict_ssl_context(ssl_cert)
            connector = aiohttp.TCPConnector(ssl=ssl_context)
        else:
            connector = aiohttp.TCPConnector(ssl=False)
        url = self._url
        path_params = {k: str(v) for k, v in map_results.get(APIParamLocation.PATH).items()}
        if path_params:
            url = url.format(**path_params)
        query_params = [(k, v) for k, v in map_results.get(APIParamLocation.QUERY, {}).items()]
        if query_params:
            url = f'{url}?{urlencode(query_params)}'
        proxy = UrlUtils.get_global_proxy_url(url)
        async with aiohttp.ClientSession(proxy=proxy, connector=connector) as session:
            async with session.request(
                    self._method,
                    url,
                    headers=map_results.get(APIParamLocation.HEADER),
                    allow_redirects=False,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                    **request_arg,
            ) as response:
                if raise_for_status is not False:
                    response.raise_for_status()
                response_data = await self._format_response(response, max_response_byte_size)
        return response_data

    async def invoke(self, inputs: Input, **kwargs) -> Output:
        final_timeout = self._timeout
        try:
            if self._card.input_params is not None:
                await trigger(
                    ToolCallEvents.TOOL_PARSE_STARTED,
                    tool_name=self.card.name, tool_id=self.card.id,
                    raw_inputs=inputs, schema=self._card.input_params)
                inputs = SchemaUtils.format_with_schema(inputs, self._card.input_params,
                                                        skip_none_value=kwargs.get("skip_none_value", False),
                                                        skip_validate=kwargs.get("skip_inputs_validate", False))
                await trigger(
                    ToolCallEvents.TOOL_PARSE_FINISHED,
                    tool_name=self.card.name, tool_id=self.card.id,
                    formatted_inputs=inputs)
            map_results = self._api_param_mapper.map(inputs, default_location=APIParamLocation.BODY)
            final_timeout = kwargs.get("timeout", self._timeout)
            return await self._async_request(map_results,
                                             final_timeout,
                                             kwargs.get("max_response_byte_size", self._max_response_byte_size),
                                             kwargs.get("raise_for_status", True),
                                             kwargs.get("request_args", {}))
        except (aiohttp.ConnectionTimeoutError, asyncio.TimeoutError) as e:
            raise build_error(StatusCode.TOOL_RESTFUL_API_EXECUTION_TIMEOUT, cause=e,
                              method="invoke", timeout=final_timeout, card=self.card)
        except aiohttp.ClientResponseError as e:
            raise build_error(StatusCode.TOOL_RESTFUL_API_RESPONSE_ERROR, cause=e,
                              method="invoke", code=e.status, reason=e.message,
                              card=self.card)
        except BaseError as e:
            raise e
        except Exception as e:
            raise build_error(StatusCode.TOOL_RESTFUL_API_EXECUTION_ERROR, cause=e,
                              method="invoke", reason=str(e),
                              card=self.card)

    async def stream(self, inputs: Input, **kwargs) -> AsyncIterator[Output]:
        raise build_error(StatusCode.TOOL_STREAM_NOT_SUPPORTED, card=self._card)

    async def _format_response(self, response: aiohttp.ClientResponse, response_bytes_size_limit):
        """Format response using parser registry"""
        content = bytearray()
        response_headers = dict(response.headers)
        async for chunk in response.content.iter_chunked(1024):
            content.extend(chunk)
            if len(content) > response_bytes_size_limit:
                raise build_error(StatusCode.TOOL_RESTFUL_API_RESPONSE_SIZE_EXCEED_LIMIT,
                                  method="invoke", max_length=response_bytes_size_limit, actual_length=len(content),
                                  card=self._card)

        status_code = response.status
        try:
            parsed_response = ParserRegistry().parse(
                response_headers=response_headers,
                response_data=content,
                status_code=status_code
            )
            results = dict(code=status_code,
                           data=parsed_response,
                           url=str(response.url),
                           headers=response_headers,
                           reason=response.reason)
            if 200 <= status_code < 300:
                results["message"] = "success"
            else:
                results["message"] = response.reason
            return results
        except Exception as e:
            raise build_error(
                StatusCode.TOOL_RESTFUL_API_RESPONSE_PROCESS_ERROR,
                cause=e,
                card=self._card,
                reason=e
            )

    def get_method(self):
        return self._method
