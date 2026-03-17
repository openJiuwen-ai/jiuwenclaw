# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Query Expression Registry
"""

from threading import Lock

from openjiuwen.core.common.logging import logger

from .base import QUERY_EXPR_FUNCTIONS, QueryLanguageDefinition, raise_query_error

__query_language_register_lock = Lock()


def register_database_query_language(name: str, definition: QueryLanguageDefinition, force: bool = False):
    """Register query language definition for a database"""
    with __query_language_register_lock:
        if name in QUERY_EXPR_FUNCTIONS and not force:
            raise_query_error(f"Database query language for {name=} already registered")
        QUERY_EXPR_FUNCTIONS[name] = definition
        logger.info("Registered query expression support for %s", name)
