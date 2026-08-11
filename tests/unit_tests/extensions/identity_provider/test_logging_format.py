# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""日志格式含身份字段测试。"""
import json
import logging
import pytest
from jiuwenswarm.extensions.identity_provider.types import IdentityInfo
from jiuwenswarm.extensions.identity_provider.store import IdentityStore
from jiuwenswarm.common.utils import IdentityFieldFilter, IdentityTextFormatter, JsonUserVisibleFormatter


@pytest.fixture(autouse=True)
def _reset():
    IdentityStore.set_test_state(None, False)
    yield
    IdentityStore.set_test_state(None, False)


def test_json_output_with_identity():
    IdentityStore.set_test_state(IdentityInfo(user_id="u1", domain_id="d1", app_id="a1"))
    r = logging.LogRecord("test.logger", logging.INFO, "", 42, "msg", (), None)
    IdentityFieldFilter().filter(r)
    obj = json.loads(JsonUserVisibleFormatter().format(r))
    assert obj["user_id"] == "u1" and obj["domain_id"] == "d1" and obj["app_id"] == "a1"
    assert obj["lineno"] == 42


def test_json_output_with_null_identity():
    IdentityStore.set_test_state(None)
    r = logging.LogRecord("test.logger", logging.INFO, "", 42, "msg", (), None)
    IdentityFieldFilter().filter(r)
    obj = json.loads(JsonUserVisibleFormatter().format(r))
    assert obj["user_id"] is None and obj["domain_id"] is None and obj["app_id"] is None


def test_text_output_with_identity():
    IdentityStore.set_test_state(IdentityInfo(user_id="u1", domain_id="d1", app_id="a1"))
    fmt = IdentityTextFormatter(fmt="%(levelname)s %(identity)s%(name)s:%(lineno)d: %(message)s")
    r = logging.LogRecord("test.logger", logging.INFO, "", 42, "msg", (), None)
    IdentityFieldFilter().filter(r)
    out = fmt.format(r)
    assert "user_id=u1" in out and "domain_id=d1" in out and "app_id=a1" in out


def test_text_output_null_identity():
    IdentityStore.set_test_state(None)
    fmt = IdentityTextFormatter(fmt="%(levelname)s %(identity)s%(name)s: %(message)s")
    r = logging.LogRecord("t", logging.INFO, "", 0, "msg", (), None)
    IdentityFieldFilter().filter(r)
    out = fmt.format(r)
    assert "user_id=null" in out and "domain_id=null" in out and "app_id=null" in out
