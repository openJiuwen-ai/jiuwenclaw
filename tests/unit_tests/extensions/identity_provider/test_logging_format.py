# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""日志格式身份字段测试。"""

import json
import logging
import pytest

from jiuwenclaw.extensions.identity_provider.types import IdentityInfo
from jiuwenclaw.extensions.identity_provider.store import IdentityStore
from jiuwenclaw.utils import IdentityFieldFilter, IdentityTextFormatter


class TestJsonLogFormat:
    """测试 JSON 格式日志包含身份字段。"""

    @staticmethod
    def setup_method() -> None:
        """每个测试前重置单例。"""
        IdentityStore.reset_instance()

    @staticmethod
    def teardown_method() -> None:
        """每个测试后清理单例。"""
        IdentityStore.reset_instance()

    @staticmethod
    def test_json_output_with_identity() -> None:
        """测试 JSON 输出包含身份字段。"""
        from jiuwenclaw.utils import JsonUserVisibleFormatter

        store = IdentityStore.get_instance()
        identity = IdentityInfo(user_id="user-123", domain_id="domain-abc", app_id="app-xyz")
        store.set_test_state(identity=identity, fetched=True)

        formatter = JsonUserVisibleFormatter()
        filter_obj = IdentityFieldFilter()

        record = logging.LogRecord(
            "test.logger", logging.INFO, "", 42, "test message", (), None
        )
        record.user_visible = None

        # Apply filter to add identity fields
        filter_obj.filter(record)

        # Format and parse
        output = formatter.format(record)
        parsed = json.loads(output)

        assert parsed.get("user_id") == "user-123"
        assert parsed.get("domain_id") == "domain-abc"
        assert parsed.get("app_id") == "app-xyz"
        assert parsed.get("lineno") == 42

    @staticmethod
    def test_json_output_with_null_identity() -> None:
        """测试 JSON 输出包含 null 身份字段（便于日志聚合分析）。"""
        from jiuwenclaw.utils import JsonUserVisibleFormatter

        store = IdentityStore.get_instance()
        store.set_test_state(identity=None, fetched=True)

        formatter = JsonUserVisibleFormatter()
        filter_obj = IdentityFieldFilter()

        record = logging.LogRecord(
            "test.logger", logging.INFO, "", 42, "test message", (), None
        )
        record.user_visible = None

        filter_obj.filter(record)

        output = formatter.format(record)
        parsed = json.loads(output)

        # null values should be in JSON output (for log aggregation consistency)
        assert parsed.get("user_id") is None
        assert parsed.get("domain_id") is None
        assert parsed.get("app_id") is None


class TestTextLogFormat:
    """测试文本格式日志包含身份字段。"""

    @staticmethod
    def setup_method() -> None:
        """每个测试前重置单例。"""
        IdentityStore.reset_instance()

    @staticmethod
    def teardown_method() -> None:
        """每个测试后清理单例。"""
        IdentityStore.reset_instance()

    @staticmethod
    def test_text_output_with_identity() -> None:
        """测试文本输出包含身份字段。"""
        store = IdentityStore.get_instance()
        identity = IdentityInfo(user_id="user-123", domain_id="domain-abc", app_id="app-xyz")
        store.set_test_state(identity=identity, fetched=True)

        formatter = IdentityTextFormatter(
            fmt="%(levelname)s %(identity)s%(name)s:%(lineno)d: %(message)s"
        )
        filter_obj = IdentityFieldFilter()

        record = logging.LogRecord(
            "test.logger", logging.INFO, "", 42, "test message", (), None
        )

        filter_obj.filter(record)

        output = formatter.format(record)

        assert "user_id=user-123" in output
        assert "domain_id=domain-abc" in output
        assert "app_id=app-xyz" in output

    @staticmethod
    def test_text_output_with_null_identity() -> None:
        """测试文本输出在 null 身份时输出 null 值（便于日志聚合分析）。"""
        store = IdentityStore.get_instance()
        store.set_test_state(identity=None, fetched=True)

        formatter = IdentityTextFormatter(
            fmt="%(levelname)s %(identity)s%(name)s:%(lineno)d: %(message)s"
        )
        filter_obj = IdentityFieldFilter()

        record = logging.LogRecord(
            "test.logger", logging.INFO, "", 42, "test message", (), None
        )

        filter_obj.filter(record)

        output = formatter.format(record)

        # null 值应该输出为 "user_id=null" 等（便于日志聚合分析）
        assert "user_id=null" in output
        assert "domain_id=null" in output
        assert "app_id=null" in output