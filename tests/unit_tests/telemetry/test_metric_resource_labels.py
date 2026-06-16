# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Unit tests for metric common labels injection."""

import pytest
from opentelemetry.sdk.resources import Resource, SERVICE_NAME

from jiuwenclaw.extensions.identity_provider import IdentityInfo, IdentityStore
from jiuwenclaw.telemetry.attributes import JIUWENCLAW_CLAW_ID
from jiuwenclaw.telemetry.metrics import (
    _observe_session_active,
    _with_resource_labels,
    set_resource,
    set_session_active_observer,
)


class TestResourceLabelsInjection:
    """Test _with_resource_labels and set_resource."""

    @staticmethod
    def teardown_method() -> None:
        """Reset global state changed by metric label tests."""
        set_resource(None)
        set_session_active_observer(None)
        IdentityStore.reset_instance()

    @staticmethod
    def test_with_resource_labels_basic() -> None:
        """Basic label injection without claw_id."""
        resource = Resource({SERVICE_NAME: "test-service"})
        set_resource(resource)
        result = _with_resource_labels({"key": "value"})
        assert result["key"] == "value"
        # SERVICE_NAME is NOT injected into metric attributes (only claw_id is)
        assert SERVICE_NAME not in result

    @staticmethod
    def test_with_resource_labels_with_claw_id() -> None:
        """Label injection with claw_id (not SERVICE_NAME)."""
        resource = Resource({
            SERVICE_NAME: "test-service",
            JIUWENCLAW_CLAW_ID: "claw-123",
        })
        set_resource(resource)
        result = _with_resource_labels({"key": "value"})
        assert result[JIUWENCLAW_CLAW_ID] == "claw-123"
        assert result["key"] == "value"
        # SERVICE_NAME is NOT injected into metric attributes
        assert SERVICE_NAME not in result

    @staticmethod
    def test_with_resource_labels_preserves_existing_attrs() -> None:
        """Existing attributes should be preserved."""
        resource = Resource({
            SERVICE_NAME: "test-service",
            JIUWENCLAW_CLAW_ID: "claw-123",
        })
        set_resource(resource)
        result = _with_resource_labels({
            "existing_key": "existing_value",
            "another_key": "another_value",
        })
        assert result["existing_key"] == "existing_value"
        assert result["another_key"] == "another_value"
        assert result[JIUWENCLAW_CLAW_ID] == "claw-123"

    @staticmethod
    def test_with_resource_labels_empty_attrs() -> None:
        """Empty attrs dict should only have claw_id (not SERVICE_NAME)."""
        resource = Resource({
            SERVICE_NAME: "test-service",
            JIUWENCLAW_CLAW_ID: "claw-123",
        })
        set_resource(resource)
        result = _with_resource_labels({})
        assert result[JIUWENCLAW_CLAW_ID] == "claw-123"
        # SERVICE_NAME is NOT injected into metric attributes
        assert SERVICE_NAME not in result
        assert len(result) == 1

    @staticmethod
    def test_set_resource_updates_globals() -> None:
        """set_resource should update global state."""
        resource_a = Resource({
            SERVICE_NAME: "service-a",
            JIUWENCLAW_CLAW_ID: "claw-a",
        })
        set_resource(resource_a)
        result_a = _with_resource_labels({})
        assert result_a[JIUWENCLAW_CLAW_ID] == "claw-a"

        resource_b = Resource({SERVICE_NAME: "service-b"})
        set_resource(resource_b)
        result_b = _with_resource_labels({})
        assert JIUWENCLAW_CLAW_ID not in result_b

    @staticmethod
    def test_with_resource_labels_none_resource() -> None:
        """None resource should not add any resource labels."""
        set_resource(None)
        result = _with_resource_labels({"key": "value"})
        assert JIUWENCLAW_CLAW_ID not in result
        assert result["key"] == "value"

    @staticmethod
    def test_with_resource_labels_injects_complete_identity() -> None:
        """Complete identity should be added to common metric labels."""
        store = IdentityStore.get_instance()
        store.set_test_state(identity=IdentityInfo(
            user_id="user-123",
            domain_id="domain-abc",
            app_id="app-xyz",
        ))

        result = _with_resource_labels({"key": "value"})

        assert result["key"] == "value"
        assert result["user_id"] == "user-123"
        assert result["domain_id"] == "domain-abc"
        assert result["app_id"] == "app-xyz"

    @staticmethod
    def test_with_resource_labels_omits_none_identity_fields() -> None:
        """Partial identity should only add non-None metric labels."""
        store = IdentityStore.get_instance()
        store.set_test_state(identity=IdentityInfo(user_id="user-123", app_id="app-xyz"))

        result = _with_resource_labels({})

        assert result["user_id"] == "user-123"
        assert "domain_id" not in result
        assert result["app_id"] == "app-xyz"

    @staticmethod
    def test_with_resource_labels_omits_identity_when_missing() -> None:
        """Missing identity should not add identity metric labels."""
        result = _with_resource_labels({})

        assert "user_id" not in result
        assert "domain_id" not in result
        assert "app_id" not in result


class TestSessionActiveObserver:
    """Test _observe_session_active with resource labels."""

    @staticmethod
    def teardown_method() -> None:
        """Reset global state changed by metric label tests."""
        set_resource(None)
        set_session_active_observer(None)
        IdentityStore.reset_instance()

    @staticmethod
    def test_observe_session_active_with_resource() -> None:
        """Session active observation should include claw_id (not SERVICE_NAME)."""
        resource = Resource({
            SERVICE_NAME: "test-service",
            JIUWENCLAW_CLAW_ID: "claw-123",
        })
        set_resource(resource)
        set_session_active_observer(lambda: 5)

        observations = list(_observe_session_active(None))
        assert len(observations) == 1
        obs = observations[0]
        assert obs.value == 5
        assert obs.attributes[JIUWENCLAW_CLAW_ID] == "claw-123"
        # SERVICE_NAME is NOT injected into metric attributes
        assert SERVICE_NAME not in obs.attributes

    @staticmethod
    def test_observe_session_active_no_resource() -> None:
        """Session active observation without resource should have empty attrs."""
        set_resource(None)
        set_session_active_observer(lambda: 3)

        observations = list(_observe_session_active(None))
        assert len(observations) == 1
        obs = observations[0]
        assert obs.value == 3
        assert obs.attributes == {}

    @staticmethod
    def test_observe_session_active_observer_exception() -> None:
        """Observer exception should return empty list."""
        resource = Resource({SERVICE_NAME: "test-service"})
        set_resource(resource)
        set_session_active_observer(lambda: int("invalid"))  # type: ignore

        observations = list(_observe_session_active(None))
        assert observations == []

    @staticmethod
    def test_observe_session_active_injects_identity_labels() -> None:
        """Session active gauge observations should include identity labels."""
        store = IdentityStore.get_instance()
        store.set_test_state(identity=IdentityInfo(
            user_id="user-123",
            domain_id="domain-abc",
            app_id="app-xyz",
        ))
        set_session_active_observer(lambda: 2)

        result = list(_observe_session_active(None))

        assert len(result) == 1
        assert result[0].value == 2
        assert result[0].attributes["user_id"] == "user-123"
        assert result[0].attributes["domain_id"] == "domain-abc"
        assert result[0].attributes["app_id"] == "app-xyz"
