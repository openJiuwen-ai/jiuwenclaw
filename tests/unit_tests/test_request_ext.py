# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import os

import pytest

from jiuwenswarm.common.request_ext import (
    METADATA_KEY,
    attach_to_metadata,
    build_ext_from_source,
    get_ext,
    lift_from_metadata,
    reset_ext,
    set_current,
    set_forward_headers,
)


@pytest.fixture(autouse=True)
def _reset_forward_headers():
    set_forward_headers(None)
    yield
    set_forward_headers(None)


def test_build_ext_from_query_lists(monkeypatch):
    monkeypatch.setenv("JIUWENSWARM_REQUEST_EXT_FORWARD_HEADERS", "user_id,group_id,bot_id")
    ext = build_ext_from_source({
        "user_id": ["u1"],
        "group_id": ["g1"],
        "other": ["x"],
    })
    assert ext == {"user_id": "u1", "group_id": "g1"}


def test_build_ext_legacy_env_name(monkeypatch):
    monkeypatch.delenv("JIUWENSWARM_REQUEST_EXT_FORWARD_HEADERS", raising=False)
    monkeypatch.setenv("JIUWENCLAW_REQUEST_EXT_FORWARD_HEADERS", "bot_id")
    ext = build_ext_from_source({"bot_id": "b1"})
    assert ext == {"bot_id": "b1"}


def test_attach_and_lift_roundtrip():
    meta = attach_to_metadata({"method": "chat.send"}, ext={"user_id": "u1"})
    assert meta[METADATA_KEY] == {"user_id": "u1"}
    token = lift_from_metadata(meta)
    try:
        assert get_ext() == {"user_id": "u1"}
    finally:
        reset_ext(token)
    assert get_ext() == {}


def test_set_current_context():
    token = set_current({"group_id": "g"})
    try:
        meta = attach_to_metadata({"method": "x"})
        assert meta[METADATA_KEY] == {"group_id": "g"}
    finally:
        reset_ext(token)
