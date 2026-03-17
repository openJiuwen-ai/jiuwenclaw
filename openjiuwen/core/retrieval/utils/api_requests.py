# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
API Requests Helper Methods

Unified api requests functions, supporting embedding & reranker tasks.
"""

import asyncio
import random
import time
from types import MappingProxyType
from typing import Any, Callable, Dict, Literal, Mapping, Never, Optional

import httpx

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger

ERROR_TEMPLATE = "%s Service Error%s: %s"
SUPPORTED_TASKS = ["Reranker", "Embedding"]


def default_error_handling(resp: Optional[httpx.Response], attempt: int, should_retry: bool) -> tuple[int, bool, Any]:
    """Default error handling routine"""
    should_retry = True
    return attempt, should_retry, None


CallbackFunction = Callable[[Optional[httpx.Response], int, bool], tuple[int, bool, Optional[dict]]]
HANDLE_ERR_CODE: MappingProxyType[int, CallbackFunction] = MappingProxyType(
    {
        429: default_error_handling,
        500: default_error_handling,
        503: default_error_handling,
    }
)


def sync_request_with_retry(
    client: httpx.Client,
    max_retries: int = 3,
    retry_wait: float = 0.1,
    custom_callback: Mapping[int, CallbackFunction] = HANDLE_ERR_CODE,
    task: Literal["Reranker", "Embedding"] = "Reranker",
    **kwargs,
) -> Optional[dict]:
    """Send api requests with retries (sync)"""
    _validate_task(task)
    attempt, should_retry = 0, False
    resp_str = "No request sent"
    response = last_error = None

    for backoff in range(1, max_retries + 1):
        if should_retry:
            time.sleep(random.random() * retry_wait * backoff)
            should_retry = False
        try:
            response = client.post(**kwargs)
            resp_json, resp_str = _handle_response(response)
        except Exception as e:
            resp_str = str(e)
            last_error = e
            should_retry = True
            continue
        attempt, should_retry, result = _handle_response_by_status(
            response, attempt, should_retry, resp_json, resp_str, task, custom_callback
        )
        if result is not None:
            return result

    _raise_errors(task, max_retries, resp_str=resp_str, response=response, last_error=last_error)


async def async_request_with_retry(
    client: httpx.AsyncClient,
    max_retries: int = 3,
    retry_wait: float = 0.1,
    custom_callback: Mapping[int, CallbackFunction] = HANDLE_ERR_CODE,
    task: Literal["Reranker", "Embedding"] = "Reranker",
    **kwargs,
) -> Optional[dict]:
    """Send api requests with retries (async)"""
    _validate_task(task)
    attempt, should_retry = 0, False
    resp_str = "No request sent"
    response = last_error = None

    for backoff in range(1, max_retries + 1):
        if should_retry:
            await asyncio.sleep(random.random() * retry_wait * backoff)
            should_retry = False
        try:
            response = await client.post(**kwargs)
            resp_json, resp_str = _handle_response(response)
        except Exception as e:
            resp_str = str(e)
            last_error = e
            should_retry = True
            continue
        attempt, should_retry, result = _handle_response_by_status(
            response, attempt, should_retry, resp_json, resp_str, task, custom_callback
        )
        if result is not None:
            return result

    _raise_errors(task, max_retries, resp_str=resp_str, response=response, last_error=last_error)


def _handle_response(response: Optional[httpx.Response]) -> tuple[dict | list, str]:
    if not response.text.startswith("{") and response.text.endswith("}"):
        raise ValueError("Empty response")
    resp_json = response.json()
    resp_str = f"{response.text=}"
    return resp_json, resp_str


def _handle_response_by_status(
    response: httpx.Response,
    attempt: int,
    should_retry: bool,
    resp_json: Dict,
    resp_str: str,
    task: str,
    custom_callback: Mapping[int, CallbackFunction],
) -> tuple[int, bool, Optional[dict]]:
    match response.status_code:
        case 200:
            return attempt, should_retry, resp_json
        case 400:
            attempt += 1
            attempt_str = f" ({attempt=})"
            logger.error(ERROR_TEMPLATE, task, attempt_str, resp_str)
            resp_json = resp_json.get("error", resp_json)
            error_code = str(resp_json.get("code", "")) + resp_json.get("message", "") + resp_json.get("content", "")
            error_code = error_code.casefold()
            if any(k in error_code for k in ["safety", "violation", "policy", "inspection", "appropriate"]):
                logger.warning("Reranker request may contain censored content")
        case _:
            callback = custom_callback.get(response.status_code, default_error_handling)
            attempt, should_retry, result = callback(response, attempt, should_retry)
            if result is not None:
                return attempt, should_retry, result
    return attempt, should_retry, None


def _raise_errors(
    task: str, max_retries: int, resp_str: str, response: Optional[httpx.Response], last_error: Optional[Exception]
) -> Never:
    logger.error(ERROR_TEMPLATE, task, "", resp_str)
    if response is not None:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            last_error = e
    else:
        last_error = build_error(
            getattr(StatusCode, f"RETRIEVAL_{task.upper()}_UNREACHABLE_CALL_FAILED"),
            error_msg=f"Failed to get {task} after {max_retries} attempts",
        )

    raise build_error(
        getattr(StatusCode, f"RETRIEVAL_{task.upper()}_REQUEST_CALL_FAILED"),
        error_msg=f"Failed to get {task} after {max_retries} attempts",
        cause=last_error,
    ) from last_error


def _validate_task(task: str):
    if task not in SUPPORTED_TASKS:
        raise build_error(
            StatusCode.RETRIEVAL_UTILS_CONFIG_NOT_FOUND,
            error_msg=f"Unsupported task in retrieval_api_requests: {task}, {SUPPORTED_TASKS=}",
        )
