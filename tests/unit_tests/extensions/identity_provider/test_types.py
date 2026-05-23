# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""IdentityInfo 数据结构测试。"""

import pytest

from jiuwenclaw.extensions.identity_provider.types import IdentityInfo


class TestIdentityInfo:
    """测试 IdentityInfo dataclass。"""

    @staticmethod
    def test_identity_info_default_fields() -> None:
        """测试默认字段值为 None。"""
        info = IdentityInfo()
        assert info.user_id is None
        assert info.domain_id is None
        assert info.app_id is None
        assert info.extra == {}

    @staticmethod
    def test_identity_info_with_fields() -> None:
        """测试设置字段值。"""
        info = IdentityInfo(
            user_id="user-123",
            domain_id="domain-abc",
            app_id="app-xyz",
        )
        assert info.user_id == "user-123"
        assert info.domain_id == "domain-abc"
        assert info.app_id == "app-xyz"

    @staticmethod
    def test_identity_info_with_extra() -> None:
        """测试 extra 扩展字段。"""
        info = IdentityInfo(
            user_id="user-123",
            extra={"tenant": "tenant-1", "region": "cn"},
        )
        assert info.extra == {"tenant": "tenant-1", "region": "cn"}

    @staticmethod
    def test_to_dict_with_all_fields() -> None:
        """测试 to_dict() 包含所有非 None 字段。"""
        info = IdentityInfo(
            user_id="user-123",
            domain_id="domain-abc",
            app_id="app-xyz",
            extra={"key": "value"},
        )
        result = info.to_dict()
        assert result == {
            "user_id": "user-123",
            "domain_id": "domain-abc",
            "app_id": "app-xyz",
            "extra": {"key": "value"},
        }

    @staticmethod
    def test_to_dict_with_partial_fields() -> None:
        """测试 to_dict() 仅包含非 None 字段。"""
        info = IdentityInfo(
            user_id="user-123",
            domain_id=None,
            app_id="app-xyz",
        )
        result = info.to_dict()
        assert "user_id" in result
        assert "domain_id" not in result
        assert "app_id" in result
        assert result["user_id"] == "user-123"
        assert result["app_id"] == "app-xyz"

    @staticmethod
    def test_to_dict_with_all_none() -> None:
        """测试 to_dict() 全为 None 时返回空字典。"""
        info = IdentityInfo()
        result = info.to_dict()
        assert result == {}

    @staticmethod
    def test_to_dict_empty_extra_not_included() -> None:
        """测试 to_dict() 不包含空 extra。"""
        info = IdentityInfo(
            user_id="user-123",
            extra={},
        )
        result = info.to_dict()
        assert "extra" not in result