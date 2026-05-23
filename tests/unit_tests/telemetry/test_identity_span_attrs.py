# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for _identity_span_attrs helper function."""

import pytest

from jiuwenclaw.extensions.identity_provider import IdentityInfo, IdentityStore
from jiuwenclaw.telemetry.metrics import _identity_span_attrs


class TestIdentitySpanAttrs:
    """Test _identity_span_attrs returns correct attributes."""

    @staticmethod
    def teardown_method() -> None:
        """Reset global state after each test."""
        IdentityStore.reset_instance()

    @staticmethod
    def test_with_complete_identity() -> None:
        """Complete identity should return all three attributes."""
        store = IdentityStore.get_instance()
        store.set_test_state(identity=IdentityInfo(
            user_id="user-123",
            domain_id="domain-abc",
            app_id="app-xyz",
        ))

        result = _identity_span_attrs()

        assert result == {
            "user.id": "user-123",
            "domain.id": "domain-abc",
            "app.id": "app-xyz",
        }

    @staticmethod
    def test_with_partial_identity_only_user_id() -> None:
        """Partial identity with only user_id should return only user_id."""
        store = IdentityStore.get_instance()
        store.set_test_state(identity=IdentityInfo(user_id="user-123"))

        result = _identity_span_attrs()

        assert result == {"user.id": "user-123"}
        assert "domain.id" not in result
        assert "app.id" not in result

    @staticmethod
    def test_with_no_identity() -> None:
        """No identity should return empty dict."""
        IdentityStore.reset_instance()
        store = IdentityStore.get_instance()
        store.set_test_state(identity=None)

        result = _identity_span_attrs()

        assert result == {}

    @staticmethod
    def test_with_identity_all_none_fields() -> None:
        """Identity with all None fields should return empty dict."""
        store = IdentityStore.get_instance()
        store.set_test_state(identity=IdentityInfo(
            user_id=None,
            domain_id=None,
            app_id=None,
        ))

        result = _identity_span_attrs()

        assert result == {}

    @staticmethod
    def test_with_user_and_app_no_domain() -> None:
        """Identity with user_id and app_id (no domain_id) should return two attributes."""
        store = IdentityStore.get_instance()
        store.set_test_state(identity=IdentityInfo(
            user_id="user-123",
            app_id="app-xyz",
        ))

        result = _identity_span_attrs()

        assert result == {
            "user.id": "user-123",
            "app.id": "app-xyz",
        }
        assert "domain.id" not in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])