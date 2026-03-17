# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Auto File Parser

Uses plugin architecture, supports registering new file format parsers via decorators.
"""

import os
from typing import Callable, Dict, List, Optional, Type

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.retrieval.common.document import Document
from openjiuwen.core.retrieval.indexing.processor.parser.base import Parser

# Global parser registry
_PARSER_REGISTRY: Dict[str, Callable[[], Parser]] = {}


def register_parser(file_extensions: List[str]):
    """
    Decorator: Register file format parser

    Args:
        file_extensions: List of supported file extensions, e.g. [".pdf", ".PDF"]

    Returns:
        Decorator function
    """

    def decorator(parser_class: Type[Parser]) -> Type[Parser]:
        def _create_parser_instance(cls=parser_class):
            return cls()

        for ext in file_extensions:
            normalized_ext = ext.lower()
            if normalized_ext not in _PARSER_REGISTRY:
                _PARSER_REGISTRY[normalized_ext] = _create_parser_instance
                logger.info(f"Registered parser {parser_class.__name__} for {normalized_ext}")
        return parser_class

    return decorator


class AutoFileParser(Parser):
    """Auto file parser

    Uses plugin architecture to automatically select appropriate parser based on file format.
    Supports registering new parsers via @register_parser decorator.
    """

    def __init__(self, **kwargs):
        # Import all parsers to trigger registration
        self._ensure_parsers_loaded()
        super().__init__(**kwargs)

    def _ensure_parsers_loaded(self):
        """Ensure all parsers are loaded and registered"""
        # Dynamically import all parser modules to trigger decorator execution
        try:
            from openjiuwen.core.retrieval.indexing.processor.parser.excel_parser import ExcelParser
            from openjiuwen.core.retrieval.indexing.processor.parser.json_parser import JSONParser
            from openjiuwen.core.retrieval.indexing.processor.parser.pdf_parser import PDFParser
            from openjiuwen.core.retrieval.indexing.processor.parser.txt_md_parser import (
                TxtMdParser,
            )
            from openjiuwen.core.retrieval.indexing.processor.parser.word_parser import WordParser
            from openjiuwen.core.retrieval.indexing.processor.parser.image_parser import ImageParser
        except ImportError as e:
            logger.warning(f"Failed to import some parser modules: {e}")

    async def parse(self, doc: str, doc_id: str = "", llm_client: Optional[Model] = None, **kwargs) -> List[Document]:
        """
        Automatically select appropriate parser based on file format

        Args:
            doc: File path
            doc_id: Document ID
            **kwargs: Additional parameters

        Returns:
            List of documents

        Raises:
            FileNotFoundError: File does not exist
            ValueError: Unsupported file format
        """
        if not os.path.exists(doc):
            raise build_error(
                StatusCode.RETRIEVAL_INDEXING_FILE_NOT_FOUND, error_msg=f"File {doc} does not exist"
            )

        # Get file extension
        file_ext = os.path.splitext(doc)[-1].lower()

        # Check if format is supported
        if file_ext not in _PARSER_REGISTRY:
            raise build_error(
                StatusCode.RETRIEVAL_INDEXING_FORMAT_NOT_SUPPORT,
                error_msg=f"Unsupported format: {file_ext}, only {list(_PARSER_REGISTRY.keys())} are supported",
            )

        # Get corresponding parser instance
        parser = _PARSER_REGISTRY[file_ext]()
        logger.info(f"Using {parser.__class__.__name__} to parse {doc}")

        # Use corresponding parser to parse and get document object list
        documents = await parser.parse(doc, doc_id, llm_client=llm_client, **kwargs)

        if not documents:
            return []

        # Get file info from kwargs or use default values
        file_name = kwargs.get("file_name", os.path.basename(doc))

        # Enhance document metadata
        for document in documents:
            document.metadata.update(
                {
                    "doc_id": doc_id,
                    "title": file_name,
                    "file_path": doc,
                    "file_ext": file_ext,
                }
            )

        return documents

    def supports(self, doc: str) -> bool:
        """
        Check if document is supported

        Args:
            doc: File path

        Returns:
            Whether supported
        """
        if not os.path.exists(doc):
            return False

        file_ext = os.path.splitext(doc)[-1].lower()
        return file_ext in _PARSER_REGISTRY

    @classmethod
    def register_new_parser(cls, file_extension: str, parser_factory: Callable[[], Parser]):
        """
        Dynamically register new parser at runtime

        Args:
            file_extension: File extension, e.g. ".pdf"
            parser_factory: Parser factory function that returns parser instance when called
        """
        normalized_ext = file_extension.lower()
        _PARSER_REGISTRY[normalized_ext] = parser_factory
        logger.info(f"Dynamically registered parser for {normalized_ext}")

    @classmethod
    def get_supported_formats(cls) -> List[str]:
        """
        Get all supported file formats

        Returns:
            List of supported file formats
        """
        return list(_PARSER_REGISTRY.keys())
