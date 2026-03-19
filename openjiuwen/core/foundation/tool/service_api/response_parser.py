# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import json
import gzip
import zlib
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Union

from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.utils.singleton import Singleton


class BaseResponseParser(ABC):
    """Base class for response parsers"""

    @abstractmethod
    def can_parse(self, content_type: str, status_code: int, **kwargs) -> bool:
        """Check if this parser can handle the response"""
        pass

    @abstractmethod
    def parse(self, response_data: bytes, encoding: Optional[str] = None, **kwargs) -> Any:
        """Parse the response data"""
        pass

    def _decode_bytes(self, data: bytes, content_type: str) -> str:
        if not data:
            return ""
        encoding = self._extract_charset_from_content_type(content_type)
        if not encoding:
            encoding = 'utf-8'
        return data.decode(encoding)

    @staticmethod
    def _extract_charset_from_content_type(content_type: str) -> Optional[str]:
        if not content_type:
            return None
        parts = content_type.split(';')
        for part in parts[1:]:
            part = part.strip()
            if part.lower().startswith('charset='):
                charset = part.split('=', 1)[1].strip()
                charset = charset.strip('"\'')
                return charset
        return None


class BaseResponseDecompressor(ABC):
    """Base class for response decompressors"""

    @abstractmethod
    def can_decompress(self, encoding: str) -> bool:
        """Check if this decompressor supports the encoding"""
        pass

    @abstractmethod
    def decompress(self, response_data: bytes) -> bytes:
        """Decompress the response data"""
        pass


class JsonResponseParser(BaseResponseParser):
    """JSON response parser.
    
    Handles JSON responses with standard content types (application/json, text/json)
    and RFC 6839 structured syntax suffix types (e.g., application/video+json, application/hal+json).
    """

    def can_parse(self, content_type: str, status_code: int, **kwargs) -> bool:
        """Check if this is a JSON response.
        
        Supports:
        - Standard JSON types: application/json, text/json, text/x-json, application/javascript
        - RFC 6839 +json suffix types: application/video+json, application/hal+json, etc.
        - No Content-Type with JSON Accept header
        """
        if not content_type:
            # No Content-Type header, check Accept header as fallback
            if status_code == 200:
                accept = kwargs.get('Accept', '').lower()
                if 'application/json' in accept or 'json' in accept:
                    return True
            return False
        
        # Normalize content type for case-insensitive comparison
        content_type_lower = content_type.lower()
        
        # Standard JSON content types (exact match)
        json_content_types = [
            'application/json',
            'text/json',
            'text/x-json',
            'application/javascript',
        ]
        if content_type_lower in json_content_types:
            return True
        
        # RFC 6839 structured syntax suffix: +json (e.g., application/video+json)
        if content_type_lower.endswith('+json'):
            return True
        
        # Legacy check for 'application/json' or 'text/json' as substring
        if 'application/json' in content_type_lower or 'text/json' in content_type_lower:
            return True
        
        return False

    def parse(self, response_data: bytes, encoding: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        """Parse JSON response data.
        
        Args:
            response_data: Raw binary response data (bytes) containing JSON text
            encoding: Optional encoding hint (charset extracted from Content-Type)
            **kwargs: Response headers including Content-Type
            
        Returns:
            Parsed JSON as dictionary
            
        Raises:
            ValueError: If JSON parsing fails
        """
        content_type = kwargs.get('Content-Type', '')

        if not response_data:
            return {}

        # Decode bytes to text using charset from Content-Type header
        decoded_text = self._decode_bytes(response_data, content_type)

        # Parse JSON
        try:
            return json.loads(decoded_text)
        except json.JSONDecodeError as e:
            raise ValueError(f"JSON parsing failed: {str(e)}") from e


class TextResponseParser(BaseResponseParser):
    """Text response parser"""

    def can_parse(self, content_type: str, status_code: int, **kwargs) -> bool:
        """Check if this is a text response"""
        # Common text content types
        text_content_types = [
            'text/plain',
            'text/html',
            'text/xml',
            'text/css',
            'text/javascript',
            'text/csv',
            'application/xml',
            'application/xhtml+xml',
            'application/javascript',
            'application/x-www-form-urlencoded',
        ]

        # Exact match
        if content_type in text_content_types:
            return True

        # Generic text/* types
        if content_type.startswith('text/'):
            return True

        # XML types
        if 'xml' in content_type and 'json' not in content_type:
            return True

        # If no Content-Type but status is OK, check Accept header
        if not content_type and status_code == 200:
            accept = kwargs.get('Accept', '').lower()
            if 'text/' in accept or 'html' in accept or 'xml' in accept:
                return True

        return False

    def parse(self, response_data: bytes, encoding: Optional[str] = None, **kwargs) -> Union[str, Dict[str, Any]]:
        """Parse text response data"""
        content_type = kwargs.get('Content-Type', '')

        # Handle empty response
        if not response_data:
            return ""

        # Use EncodingUtils to decode
        try:
            decoded_text = self._decode_bytes(response_data, content_type)
            return decoded_text
        except ValueError as e:
            raise ValueError(f"Text decoding failed: {str(e)}") from e


class GzipDecompressor(BaseResponseDecompressor):
    """GZIP decompressor"""

    def can_decompress(self, encoding: str) -> bool:
        """Check if this decompressor supports the encoding"""
        encoding_lower = encoding.lower()
        return encoding_lower == 'gzip' or encoding_lower == 'x-gzip'

    def decompress(self, response_data: bytes) -> bytes:
        """Decompress GZIP data"""
        try:
            return gzip.decompress(response_data)
        except gzip.BadGzipFile:
            try:
                return zlib.decompress(response_data, 16 + zlib.MAX_WBITS)
            except zlib.error:
                try:
                    return zlib.decompress(response_data, -zlib.MAX_WBITS)
                except zlib.error as e:
                    raise ValueError(f"GZIP decompression failed: {str(e)}") from e
        except Exception as e:
            raise ValueError(f"GZIP decompression exception: {str(e)}") from e


class DeflateDecompressor(BaseResponseDecompressor):
    """Deflate decompressor"""

    def can_decompress(self, encoding: str) -> bool:
        """Check if this decompressor supports the encoding"""
        return encoding.lower() == 'deflate'

    def decompress(self, response_data: bytes) -> bytes:
        """Decompress Deflate data"""
        try:
            return zlib.decompress(response_data)
        except zlib.error:
            try:
                return zlib.decompress(response_data, -zlib.MAX_WBITS)
            except zlib.error as e:
                raise ValueError(f"Deflate decompression failed: {str(e)}") from e


class ParserRegistry(metaclass=Singleton):
    """Registry for response parsers and decompressors"""

    def __init__(self):
        self._parsers: List[BaseResponseParser] = []
        self._decompressors: Dict[str, BaseResponseDecompressor] = {}
        self._register_default_components()

    def _register_default_components(self):
        """Register default components"""
        # Register parsers (order matters - first matching parser will be used)
        self.register(JsonResponseParser())
        self.register(TextResponseParser())

        # Register decompressors
        self.register_decompressor('gzip', GzipDecompressor())
        self.register_decompressor('deflate', DeflateDecompressor())

    def register(self, parser: BaseResponseParser) -> None:
        self._parsers.append(parser)

    def register_decompressor(self, encoding: str, decompressor: BaseResponseDecompressor) -> None:
        self._decompressors[encoding.lower()] = decompressor

    def _apply_decompression(self, response_data: bytes, content_encoding: str) -> bytes:
        if not content_encoding or not response_data:
            return response_data

        encodings = [e.strip().lower() for e in content_encoding.split(',')]

        for encoding in encodings:
            if encoding in self._decompressors:
                decompressor = self._decompressors[encoding]
                if decompressor.can_decompress(encoding):
                    try:
                        response_data = decompressor.decompress(response_data)
                    except Exception as e:
                        logger.error(f"Decompression failed ({encoding}): {str(e)}")
                        break

        return response_data

    def parse(self, response_headers: Dict[str, str], response_data: bytes, status_code: int) -> Optional[Any]:
        """Parse the HTTP response"""
        lower_headers = {k.lower(): v for k, v in response_headers.items()}
        content_type = lower_headers.get('content-type', 'text/plain')
        content_encoding = lower_headers.get('content-encoding', '')

        # Find appropriate parser
        result = None
        parsed = False
        for parser in self._parsers:
            if parser.can_parse(content_type, status_code, **response_headers):
                result = parser.parse(response_data, content_encoding, **response_headers)
                parsed = True
                break  # Use first matching parser

        if not parsed:
            raise ValueError(f"not found response parser for {content_type}")

        return result
