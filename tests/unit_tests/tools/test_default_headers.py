# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import os

import pytest

from jiuwenclaw.local_env_config import (
    ENV_CONFIG_DICT,
    bind_task_env_overlay,
    clear_staged_env,
    parse_default_headers,
    read_default_headers,
    read_default_headers_raw,
    reset_local_env_state_for_tests,
    reset_task_env_overlay,
)


@pytest.fixture(autouse=True)
def _reset_env_state():
    saved_environ = dict(os.environ)
    reset_local_env_state_for_tests()
    ENV_CONFIG_DICT.clear()
    clear_staged_env()
    yield
    reset_local_env_state_for_tests()
    os.environ.clear()
    os.environ.update(saved_environ)


class TestParseDefaultHeaders:
    @staticmethod
    def test_empty_returns_none():
        assert parse_default_headers("") is None
        assert parse_default_headers("   ") is None

    @staticmethod
    def test_valid_json_object():
        parsed = parse_default_headers('{"Authorization":"Basic abc"}')
        assert parsed == {"Authorization": "Basic abc"}

    @staticmethod
    def test_invalid_json_raises():
        with pytest.raises(ValueError, match="not valid JSON"):
            parse_default_headers("{bad json")

    @staticmethod
    def test_non_object_raises():
        with pytest.raises(ValueError, match="must be a JSON object"):
            parse_default_headers('["a"]')


class TestReadDefaultHeaders:
    @staticmethod
    def test_reads_primary_env_key():
        ENV_CONFIG_DICT["default_headers"] = '{"Authorization":"Basic x"}'
        assert read_default_headers() == {"Authorization": "Basic x"}

    @staticmethod
    def test_alias_default_headers_uppercase():
        ENV_CONFIG_DICT["DEFAULT_HEADERS"] = '{"Authorization":"Basic y"}'
        assert read_default_headers_raw() == '{"Authorization":"Basic y"}'

    @staticmethod
    def test_alias_openai_default_headers():
        ENV_CONFIG_DICT["OPENAI_DEFAULT_HEADERS"] = '{"Authorization":"Basic z"}'
        assert read_default_headers_raw() == '{"Authorization":"Basic z"}'

    @staticmethod
    def test_staged_overlay_priority():
        ENV_CONFIG_DICT["default_headers"] = '{"Authorization":"Basic active"}'
        token = bind_task_env_overlay({"default_headers": '{"Authorization":"Basic overlay"}'})
        try:
            assert read_default_headers() == {"Authorization": "Basic overlay"}
        finally:
            reset_task_env_overlay(token)
        assert read_default_headers() == {"Authorization": "Basic active"}

    @staticmethod
    def test_missing_returns_none():
        assert read_default_headers() is None
        assert read_default_headers_raw() == ""


class TestPaidDefaultHeadersContract:
    """paid._load_llm_default_headers delegates to read_default_headers()."""

    @staticmethod
    def test_wrapper_contract_missing():
        assert read_default_headers() is None

    @staticmethod
    def test_wrapper_contract_present():
        token = bind_task_env_overlay({"default_headers": '{"Authorization":"Basic petal"}'})
        try:
            assert read_default_headers() == {"Authorization": "Basic petal"}
        finally:
            reset_task_env_overlay(token)
