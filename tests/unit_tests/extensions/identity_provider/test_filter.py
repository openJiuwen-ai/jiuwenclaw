# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""IdentityFieldFilter 测试。"""

import logging
import pytest

from jiuwenclaw.extensions.identity_provider.types import IdentityInfo
from jiuwenclaw.extensions.identity_provider.store import IdentityStore
from jiuwenclaw.utils import IdentityFieldFilter


class TestIdentityFieldFilter:
    """测试 IdentityFieldFilter。"""

    @staticmethod
    def setup_method() -> None:
        """每个测试前重置单例。"""
        IdentityStore.reset_instance()

    @staticmethod
    def teardown_method() -> None:
        """每个测试后清理单例。"""
        IdentityStore.reset_instance()

    @staticmethod
    def test_filter_adds_identity_fields() -> None:
        """测试 filter 添加身份字段到 LogRecord。"""
        store = IdentityStore.get_instance()
        identity = IdentityInfo(user_id="user-123", domain_id="domain-abc", app_id="app-xyz")
        store.set_test_state(identity=identity, fetched=True)

        filter_obj = IdentityFieldFilter()
        record = logging.LogRecord(
            "test.logger", logging.INFO, "", 1, "test message", (), None
        )

        result = filter_obj.filter(record)

        assert result is True
        assert record.user_id == "user-123"
        assert record.domain_id == "domain-abc"
        assert record.app_id == "app-xyz"

    @staticmethod
    def test_filter_handles_none_identity() -> None:
        """测试 filter 处理 None 身份。"""
        store = IdentityStore.get_instance()
        store.set_test_state(identity=None, fetched=True)

        filter_obj = IdentityFieldFilter()
        record = logging.LogRecord(
            "test.logger", logging.INFO, "", 1, "test message", (), None
        )

        result = filter_obj.filter(record)

        assert result is True
        assert record.user_id is None
        assert record.domain_id is None
        assert record.app_id is None

    @staticmethod
    def test_filter_handles_partial_identity() -> None:
        """测试 filter 处理部分身份字段。"""
        store = IdentityStore.get_instance()
        identity = IdentityInfo(user_id="user-123", domain_id=None, app_id="app-xyz")
        store.set_test_state(identity=identity, fetched=True)

        filter_obj = IdentityFieldFilter()
        record = logging.LogRecord(
            "test.logger", logging.INFO, "", 1, "test message", (), None
        )

        result = filter_obj.filter(record)

        assert result is True
        assert record.user_id == "user-123"
        assert record.domain_id is None
        assert record.app_id == "app-xyz"

    @staticmethod
    def test_filter_always_returns_true() -> None:
        """测试 filter 总是返回 True（不过滤）。"""
        filter_obj = IdentityFieldFilter()
        record = logging.LogRecord(
            "test.logger", logging.INFO, "", 1, "test message", (), None
        )

        result = filter_obj.filter(record)

        assert result is True