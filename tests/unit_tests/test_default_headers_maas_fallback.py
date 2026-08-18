"""Tip default_headers + MaaS fallback aliases for LLM auth."""

from __future__ import annotations

from jiuwenswarm.common.local_env_config import (
    bind_task_env_overlay,
    parse_default_headers,
    read_default_headers,
    read_default_headers_raw,
    reset_task_env_overlay,
)


def test_parse_default_headers_requires_object() -> None:
    assert parse_default_headers("") is None
    assert parse_default_headers('{"Authorization":"Basic abc"}') == {
        "Authorization": "Basic abc"
    }


def test_read_default_headers_prefers_primary_key() -> None:
    token = bind_task_env_overlay(
        {
            "default_headers": '{"Authorization":"Basic primary"}',
            "PETAL_API_KEY": '{"Authorization":"Basic petal"}',
        }
    )
    try:
        assert read_default_headers_raw() == '{"Authorization":"Basic primary"}'
        assert read_default_headers() == {"Authorization": "Basic primary"}
    finally:
        reset_task_env_overlay(token)


def test_read_default_headers_does_not_fall_back_to_petal_headers() -> None:
    token = bind_task_env_overlay(
        {
            "PETAL_SEARCH_HEADERS": '{"Authorization":"Basic search"}',
            "PETAL_API_KEY": '{"Authorization":"Basic petal"}',
        }
    )
    try:
        assert read_default_headers() is None
    finally:
        reset_task_env_overlay(token)


def test_read_default_headers_falls_back_to_huawei_maas_headers() -> None:
    token = bind_task_env_overlay(
        {
            "OFFICE_CLAW_HUAWEI_MAAS_HEADERS_JSON": (
                '{"Authorization":"Basic maas"}'
            )
        }
    )
    try:
        assert read_default_headers() == {"Authorization": "Basic maas"}
    finally:
        reset_task_env_overlay(token)


def test_read_default_headers_does_not_reuse_petal_auth_for_custom_openai() -> None:
    token = bind_task_env_overlay(
        {
            "API_KEY": "sk-custom-openai",
            "PETAL_SEARCH_HEADERS": '{"Authorization":"Basic petal"}',
        }
    )
    try:
        assert read_default_headers() is None
    finally:
        reset_task_env_overlay(token)


def test_read_default_headers_ignores_non_json_petal_key() -> None:
    token = bind_task_env_overlay({"PETAL_API_KEY": "sk-not-json"})
    try:
        assert read_default_headers() is None
    finally:
        reset_task_env_overlay(token)
