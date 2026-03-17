# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import json

from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.logging import logger


class JsonUtils:
    @staticmethod
    def safe_json_loads(json_string, default=None, **kwargs):
        if default is None:
            try:
                return json.loads(json_string, **kwargs)
            except json.JSONDecodeError as e:
                raise build_error(StatusCode.COMMON_JSON_INPUT_PROCESS_ERROR, error_msg="JSON decode error") from e
            except TypeError as e:
                raise build_error(StatusCode.COMMON_JSON_INPUT_PROCESS_ERROR, error_msg="JSON type error") from e
            except ValueError as e:
                raise build_error(StatusCode.COMMON_JSON_INPUT_PROCESS_ERROR, error_msg="JSON value error") from e
            except Exception as e:
                raise build_error(StatusCode.COMMON_JSON_INPUT_PROCESS_ERROR, error_msg="JSON operation error") from e
        else:
            result = default
            try:
                result = json.loads(json_string, **kwargs)
            except json.JSONDecodeError as e:
                logger.error(f"JSON decode error: {e}")
            except TypeError as e:
                logger.error(f"JSON type error: {e}")
            except ValueError as e:
                logger.error(f"JSON value error: {e}")
            except Exception as e:
                logger.error(f"JSON operation error: {e}")
            return result


    @staticmethod
    def safe_json_dumps(obj, default=None, **kwargs):
        if default is None:
            try:
                return json.dumps(obj, **kwargs)
            except TypeError as e:
                raise build_error(
                    StatusCode.COMMON_JSON_EXECUTION_PROCESS_ERROR,
                    error_msg="JSON serialization type error"
                ) from e
            except ValueError as e:
                raise build_error(
                    StatusCode.COMMON_JSON_EXECUTION_PROCESS_ERROR,
                    error_msg="JSON serialization value error",
                ) from e
            except Exception as e:
                raise build_error(
                    StatusCode.COMMON_JSON_EXECUTION_PROCESS_ERROR,
                    error_msg="JSON serialization error",
                ) from e
        else:
            result = default
            try:
                result = json.dumps(obj, **kwargs)
            except TypeError:
                logger.error("JSON serialization type error")
            except ValueError:
                logger.error("JSON serialization value error")
            except Exception:
                logger.error("JSON serialization error")
            return result
