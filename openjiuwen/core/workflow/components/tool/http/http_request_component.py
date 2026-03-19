# coding=utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from dataclasses import dataclass, field
from typing import Dict, Any, Optional, Literal, List, AsyncIterator
from enum import Enum
import json
import base64
import asyncio
import aiohttp
from oauthlib.common import urlencode
from pydantic import Field, BaseModel, ValidationError

from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.security.ssl_utils import SslUtils
from openjiuwen.core.common.security.url_utils import UrlUtils
from openjiuwen.core.common.utils.schema_utils import SchemaUtils
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.graph.executable import Input, Output
from openjiuwen.core.session.node import Session
from openjiuwen.core.workflow.components.base import ComponentConfig, WorkflowComponentMetadata
from openjiuwen.core.workflow.components.component import ComponentComposable, ComponentExecutable
from openjiuwen.core.foundation.tool.service_api.response_parser import ParserRegistry


class HttpAuthType(str, Enum):
    NONE = "none"
    BASIC = "basic"
    BEARER = "bearer"
    API_KEY = "api_key"
    DIGEST = "digest"
    AWS = "aws"


class HttpContentType(str, Enum):
    JSON = "json"
    FORM = "form"
    MULTIPART_FORM = "multipart_form"
    BINARY = "binary"
    TEXT = "text"
    AUTO = "auto"


class HttpResponseFormat(str, Enum):
    AUTODETECT = "autodetect"
    JSON = "json"
    TEXT = "text"
    BINARY = "binary"
    BUFFER = "buffer"


class HttpAuthConfig(BaseModel):
    type: HttpAuthType = HttpAuthType.NONE
    # Basic/Digest auth
    username: Optional[str] = None
    password: Optional[str] = None
    # Bearer token
    token: Optional[str] = None
    # API Key
    api_key: Optional[str] = None
    in_location: Literal["header", "query", "body"] = "header"
    name: str = "Authorization"
    # AWS Signature
    access_key: Optional[str] = None
    secret_key: Optional[str] = None
    region: Optional[str] = None


class HttpRequestBodyConfig(BaseModel):
    content_type: HttpContentType = HttpContentType.JSON
    json_data: Optional[Dict[str, Any]] = None
    form_data: Optional[Dict[str, Any]] = None
    multipart_form: Optional[Dict[str, Any]] = None  # For file uploads
    binary_data: Optional[str] = None  # Reference to binary data
    text_data: Optional[str] = None


class HttpResponseHandlingConfig(BaseModel):
    response_format: HttpResponseFormat = HttpResponseFormat.AUTODETECT
    response_code_success_codes: List[int] = Field(default_factory=lambda: [200, 201, 202, 204])
    response_code_failure_codes: List[int] = Field(default_factory=list)
    response_mode: Literal["full", "on-success", "on-error"] = "full"
    response_data_property: Optional[str] = None  # For extracting specific property from response
    max_redirects: int = 21
    throw_on_http_error: bool = True


class HttpAdvancedOptionsConfig(BaseModel):
    follow_redirect: bool = True
    ignore_ssl_issues: bool = False
    proxy: Optional[str] = None
    timeout: int = 10000  # in milliseconds
    disable_compression: bool = False
    disable_follow_track_redirect: bool = False
    max_body_length: int = 1048576  # 1MB default
    use_stream: bool = False
    proxy_header: Optional[Dict[str, str]] = None


class HttpRetryConfig(BaseModel):
    enabled: bool = False
    max_retries: int = 3
    retry_on_status_codes: List[int] = Field(default_factory=lambda: [429, 500, 502, 503, 504])
    retry_delay: int = 1000  # in milliseconds
    backoff_type: Literal["fixed", "exponential", "linear"] = "exponential"


class HttpRateLimitConfig(BaseModel):
    enabled: bool = False
    requests_per_unit: int = 1
    unit: Literal["second", "minute", "hour"] = "second"


class HttpRequestParamConfig(BaseModel):
    url: str = Field(..., description="The URL to make the request to")
    method: str = Field(default="GET", description="HTTP method to use")
    headers: Dict[str, str] = Field(default_factory=dict, description="Request headers")
    query_parameters: Dict[str, Any] = Field(default_factory=dict, description="Query parameters")
    body: Optional[HttpRequestBodyConfig] = None
    authentication: Optional[HttpAuthConfig] = None
    response_handling: HttpResponseHandlingConfig = Field(default_factory=HttpResponseHandlingConfig)
    advanced_options: HttpAdvancedOptionsConfig = Field(default_factory=HttpAdvancedOptionsConfig)
    retry_config: HttpRetryConfig = Field(default_factory=HttpRetryConfig)
    rate_limit_config: HttpRateLimitConfig = Field(default_factory=HttpRateLimitConfig)
    timeout: float = Field(default=60.0, ge=1.0, le=300.0, description="Request timeout in seconds")
    max_response_byte_size: int = Field(default=10 * 1024 * 1024, description="Response max size in bytes")


class HttpComponentConfig(BaseModel):
    request_params: HttpRequestParamConfig = Field(default_factory=HttpRequestParamConfig)
    metadata: Optional[WorkflowComponentMetadata] = Field(default=None)


class HTTPRequestExecutable(ComponentExecutable):
    TEXT_CONTENT_TYPES = ['text/', 'application/json', 'application/xml', 
                          'application/javascript', 'application/xhtml+xml']
    
    def __init__(self, config: HttpComponentConfig):
        super().__init__()
        self.config = config
        self.request_params = config.request_params
        
    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        # Process inputs and merge with component configuration
        processed_inputs = await self.process_inputs(inputs)
        
        # Perform the HTTP request
        response = await self.make_request(processed_inputs)
        
        # Process and return the response
        return await self.process_response(response)
    
    async def stream(self, inputs: Input, session: Session, context: ModelContext) -> AsyncIterator[Output]:
        # For now, just yield the result of invoke
        result = await self.invoke(inputs, session, context)
        yield result
    
    async def collect(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        # Process streaming inputs and return batch output
        result = await self.invoke(inputs, session, context)
        return result
    
    async def transform(self, inputs: Input, session: Session, context: ModelContext) -> AsyncIterator[Output]:
        # Transform streaming input to streaming output
        result = await self.invoke(inputs, session, context)
        yield result
    
    async def process_inputs(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process the input data and merge with component configuration
        """
        # Merge inputs with component configuration
        # Handle dynamic values in URL, headers, query parameters, etc.
        processed = {}
        
        # Process URL - allow for dynamic values from inputs
        url = self.request_params.url
        for key, value in inputs.items():
            placeholder = f"{{{{{key}}}}}"  # {{key}} format
            if isinstance(url, str):
                url = url.replace(placeholder, str(value))
        processed['url'] = url
        
        # Process method
        method = inputs.get('method', self.request_params.method)
        processed['method'] = method.upper()
        
        # Process headers
        headers = self.request_params.headers.copy()
        headers.update(inputs.get('headers', {}))
        processed['headers'] = headers
        
        # Process query parameters
        query_params = self.request_params.query_parameters.copy()
        query_params.update(inputs.get('query_parameters', {}))
        processed['query_parameters'] = query_params
        
        # Process body
        body_config = self.request_params.body
        if body_config:
            processed['body'] = body_config
        else:
            processed['body'] = inputs.get('body', {})
            
        # Process authentication
        auth_config = self.request_params.authentication
        if auth_config:
            processed['authentication'] = auth_config
        else:
            processed['authentication'] = inputs.get('authentication', {})
        
        return processed
    
    async def make_request(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Perform the actual HTTP request with all configurations
        """
        url = params['url']
        method = params['method']
        headers = params.get('headers', {})
        query_params = params.get('query_parameters', {})
        body_config = params.get('body', {})
        auth_config = params.get('authentication', {})
        
        # Apply authentication to headers
        headers = await self.apply_authentication(headers, auth_config)
        
        # Prepare request body
        request_body, content_type = await self.prepare_request_body(body_config)
        if content_type:
            headers['Content-Type'] = content_type
        
        # Prepare SSL context
        ssl_verify, ssl_cert = SslUtils.get_ssl_config("HTTP_SSL_VERIFY", "HTTP_SSL_CERT", ["false"])
        if ssl_verify and not self.request_params.advanced_options.ignore_ssl_issues:
            ssl_context = SslUtils.create_strict_ssl_context(ssl_cert)
            connector = aiohttp.TCPConnector(ssl=ssl_context)
        else:
            connector = aiohttp.TCPConnector(ssl=False)
        
        # Prepare proxy
        proxy = self.request_params.advanced_options.proxy or UrlUtils.get_global_proxy_url(url)
        
        # Prepare timeout
        timeout_seconds = self.request_params.timeout
        
        # Prepare URL with query parameters
        if query_params:
            query_string = urlencode([(k, v) for k, v in query_params.items()])
            url = f'{url}?{query_string}'

        # Handle retries
        max_retries = self.request_params.retry_config.max_retries if self.request_params.retry_config.enabled else 0
        retry_count = 0

        while retry_count <= max_retries:
            try:
                async with aiohttp.ClientSession(connector=connector) as session:
                    async with session.request(
                        method=method,
                        url=url,
                        headers=headers,
                        data=request_body if method in ['POST', 'PUT', 'PATCH'] else None,
                        params=query_params if method == 'GET' else None,  # For GET requests
                        allow_redirects=self.request_params.advanced_options.follow_redirect,
                        timeout=aiohttp.ClientTimeout(total=timeout_seconds),
                        proxy=proxy
                    ) as response:
                        # Read response content with size check
                        content = bytearray()
                        async for chunk in response.content.iter_chunked(8192):  # Read in chunks of 8KB
                            content.extend(chunk)
                            
                            # Check if content exceeds max size
                            if len(content) > self.request_params.max_response_byte_size:
                                raise build_error(
                                    StatusCode.COMPONENT_TOOL_EXECUTION_ERROR,
                                    error_msg=f"Response size ({len(content)} bytes) exceeds maximum allowed size \
                                        ({self.request_params.max_response_byte_size} bytes)"
                                )

                        # Determine if content should be treated as text or binary based on content-type header
                        content_type = response.headers.get('content-type', '').lower()
                        
                        if any(text_type in content_type for text_type in self.TEXT_CONTENT_TYPES):
                            # Decode as text for text-based content types
                            content_result = bytes(content).decode('utf-8')
                        else:
                            # Keep as bytes for binary content types
                            content_result = bytes(content)

                        result = {
                            'status_code': response.status,
                            'headers': dict(response.headers),
                            'content': content_result,
                            'url': str(response.url),
                            'reason': response.reason
                        }
                        
                        # Check if we need to retry based on status code
                        if (self.request_params.retry_config.enabled and 
                            response.status in self.request_params.retry_config.retry_on_status_codes and
                            retry_count < max_retries):
                            
                            # Wait before retry based on backoff strategy
                            delay = self.calculate_retry_delay(retry_count)
                            await asyncio.sleep(delay)
                            retry_count += 1
                            continue
                        else:
                            # Successful response or no more retries
                            break
            
            except Exception as e:
                if (self.request_params.retry_config.enabled and 
                    retry_count < max_retries):
                    
                    # Wait before retry
                    delay = self.calculate_retry_delay(retry_count)
                    await asyncio.sleep(delay)
                    retry_count += 1
                    continue
                else:
                    # No more retries, raise the error
                    raise build_error(
                        StatusCode.COMPONENT_TOOL_EXECUTION_ERROR,
                        error_msg=f"HTTP request failed: {str(e)}",
                        cause=e
                    ) from e
        
        return result
    
    async def process_response(self, response: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process the response according to response handling configuration
        """
        status_code = response['status_code']
        content = response['content']
        headers = response['headers']
        
        # Validate response code
        success_codes = self.request_params.response_handling.response_code_success_codes
        failure_codes = self.request_params.response_handling.response_code_failure_codes
        
        is_success = status_code in success_codes or (not failure_codes and 200 <= status_code < 300)
        is_failure = status_code in failure_codes or status_code >= 400
        
        # Determine response format
        response_format = self.request_params.response_handling.response_format
        if response_format == HttpResponseFormat.AUTODETECT:
            content_type = headers.get('content-type', '').lower()
            if 'application/json' in content_type:
                response_format = HttpResponseFormat.JSON
            elif 'text/' in content_type:
                response_format = HttpResponseFormat.TEXT
            else:
                response_format = HttpResponseFormat.BINARY
        
        # Parse response based on format
        parsed_content = content
        if response_format == HttpResponseFormat.JSON:
            try:
                parsed_content = json.loads(content.decode('utf-8'))
            except json.JSONDecodeError:
                parsed_content = content
        elif response_format == HttpResponseFormat.TEXT:
            parsed_content = content.decode('utf-8')
        elif response_format == HttpResponseFormat.BINARY:
            # Already in binary format
            pass
        
        # Extract specific property if configured
        if self.request_params.response_handling.response_data_property:
            prop_path = self.request_params.response_handling.response_data_property
            if isinstance(parsed_content, dict):
                # Simple property access for now
                parsed_content = parsed_content.get(prop_path, parsed_content)
        
        # Format response based on response_mode
        response_mode = self.request_params.response_handling.response_mode
        result = {
            'statusCode': status_code,
            'headers': headers,
            'body': parsed_content,
            'url': response.get('url', ''),
            'ok': is_success and not is_failure
        }
        
        if response_mode == "on-success" and not is_success:
            result = {}
        elif response_mode == "on-error" and is_success:
            result = {}
        
        return result
    
    async def apply_authentication(self, headers: Dict[str, str], auth_config: HttpAuthConfig) -> Dict[str, str]:
        """
        Apply authentication to request headers
        """
        if not auth_config or auth_config.type == HttpAuthType.NONE:
            return headers
        
        if auth_config.type == HttpAuthType.BASIC:
            if auth_config.username and auth_config.password:
                credentials = f"{auth_config.username}:{auth_config.password}"
                encoded_credentials = base64.b64encode(credentials.encode()).decode()
                headers["Authorization"] = f"Basic {encoded_credentials}"
                
        elif auth_config.type == HttpAuthType.BEARER:
            if auth_config.token:
                headers["Authorization"] = f"Bearer {auth_config.token}"
                
        elif auth_config.type == HttpAuthType.API_KEY:
            if auth_config.api_key:
                if auth_config.in_location == "header":
                    headers[auth_config.name] = auth_config.api_key
                # For query and body, we'd need to handle separately in the request
        
        # Note: Digest and AWS authentication would require additional implementation
        # These are more complex and typically require additional libraries
        
        return headers
    
    async def prepare_request_body(self, body_config: HttpRequestBodyConfig) -> tuple:
        """
        Prepare request body based on content type
        """
        if not body_config:
            return None, None
        
        content_type = body_config.content_type
        if content_type == HttpContentType.JSON and body_config.json_data:
            return json.dumps(body_config.json_data), 'application/json'
        
        elif content_type == HttpContentType.FORM and body_config.form_data:
            return aiohttp.FormData(body_config.form_data), 'application/x-www-form-urlencoded'
        
        elif content_type == HttpContentType.MULTIPART_FORM and body_config.multipart_form:
            form_data = aiohttp.FormData()
            for key, value in body_config.multipart_form.items():
                form_data.add_field(key, value)
            return form_data, 'multipart/form-data'
        
        elif content_type == HttpContentType.TEXT and body_config.text_data:
            return body_config.text_data, 'text/plain'
        
        elif content_type == HttpContentType.BINARY and body_config.binary_data:
            # Assuming binary_data is base64 encoded string
            binary_bytes = base64.b64decode(body_config.binary_data)
            return binary_bytes, 'application/octet-stream'
        
        return None, None
    
    def calculate_retry_delay(self, retry_count: int) -> float:
        """
        Calculate delay before retry based on backoff strategy
        """
        base_delay = self.request_params.retry_config.retry_delay / 1000.0  # Convert ms to seconds
        
        if self.request_params.retry_config.backoff_type == "fixed":
            return base_delay
        elif self.request_params.retry_config.backoff_type == "linear":
            return base_delay * (retry_count + 1)
        elif self.request_params.retry_config.backoff_type == "exponential":
            return base_delay * (2 ** retry_count)
        
        return base_delay


class HTTPRequestComponent(ComponentComposable):
    def __init__(self, config: HttpComponentConfig):
        super().__init__()
        self.__config = config  # Private attribute to comply with rule G.CLS.11
        self._executable = None

    @property
    def config(self) -> HttpComponentConfig:
        """Public getter for the component configuration."""
        return self.__config

    @property
    def executable(self) -> HTTPRequestExecutable:
        if self._executable is None:
            self._executable = self.to_executable()
        return self._executable

    def to_executable(self) -> HTTPRequestExecutable:
        return HTTPRequestExecutable(self.config)