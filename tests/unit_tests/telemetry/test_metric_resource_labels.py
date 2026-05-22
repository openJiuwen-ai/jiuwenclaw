# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Unit tests for metric resource labels injection."""

import pytest
from opentelemetry.metrics import Observation
from opentelemetry.sdk.resources import Resource, SERVICE_NAME

from jiuwenclaw.telemetry.attributes import JIUWENCLAW_CLAW_ID
from jiuwenclaw.telemetry.metrics import (
    _with_resource_labels,
    _observe_session_active,
    _observe_queue_depth,
    set_resource,
    set_session_active_observer,
    set_queue_depth_observer,
)


class TestResourceLabelsInjection:
    """Test _with_resource_labels and set_resource."""

    @staticmethod
    def test_with_resource_labels_basic() -> None:
        """Basic label injection with service_name only."""
        resource = Resource({SERVICE_NAME: "test-service"})
        set_resource(resource)
        result = _with_resource_labels({"key": "value"})
        assert result["key"] == "value"
        assert result[SERVICE_NAME] == "test-service"

    @staticmethod
    def test_with_resource_labels_with_claw_id() -> None:
        """Label injection with both service_name and claw_id."""
        resource = Resource({
            SERVICE_NAME: "test-service",
            JIUWENCLAW_CLAW_ID: "claw-123",
        })
        set_resource(resource)
        result = _with_resource_labels({"key": "value"})
        assert result[JIUWENCLAW_CLAW_ID] == "claw-123"
        assert result["key"] == "value"
        assert result[SERVICE_NAME] == "test-service"

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
        """Empty attrs dict should only have resource attributes."""
        resource = Resource({
            SERVICE_NAME: "test-service",
            JIUWENCLAW_CLAW_ID: "claw-123",
        })
        set_resource(resource)
        result = _with_resource_labels({})
        assert result[JIUWENCLAW_CLAW_ID] == "claw-123"
        assert result[SERVICE_NAME] == "test-service"
        assert len(result) == 2

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
        assert result["key"] == "value"

    @staticmethod
    def test_with_resource_labels_no_telemetry_sdk_attrs() -> None:
        """Resource should NOT contain telemetry SDK attributes."""
        resource = Resource({
            SERVICE_NAME: "test-service",
            JIUWENCLAW_CLAW_ID: "claw-123",
        })
        set_resource(resource)
        result = _with_resource_labels({})
        # 不应包含 telemetry SDK 自动注入的属性
        assert "telemetry_sdk_language" not in result
        assert "telemetry_sdk_name" not in result
        assert "telemetry_sdk_version" not in result

    @staticmethod
    def test_with_resource_labels_multiple_attrs() -> None:
        """Multiple resource attributes should all be injected."""
        resource = Resource({
            SERVICE_NAME: "test-service",
            JIUWENCLAW_CLAW_ID: "claw-123",
            "application_id": "app-id-123",
            "user_id": "user-id-123",
        })
        set_resource(resource)
        result = _with_resource_labels({"key": "value"})
        assert result[SERVICE_NAME] == "test-service"
        assert result[JIUWENCLAW_CLAW_ID] == "claw-123"
        assert result["application_id"] == "app-id-123"
        assert result["user_id"] == "user-id-123"


class TestSessionActiveObserver:
    """Test _observe_session_active with resource labels."""

    @staticmethod
    def test_observe_session_active_with_resource() -> None:
        """Session active observation should include resource labels."""
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
        assert obs.attributes[SERVICE_NAME] == "test-service"

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


class TestQueueDepthObserver:
    """Test _observe_queue_depth with resource labels."""

    @staticmethod
    def test_observe_queue_depth_with_resource() -> None:
        """Queue depth observation should include resource labels."""
        resource = Resource({
            SERVICE_NAME: "test-service",
            JIUWENCLAW_CLAW_ID: "claw-123",
        })
        set_resource(resource)
        set_queue_depth_observer(lambda: [Observation(10, {"queue": "main"})])

        observations = list(_observe_queue_depth(None))
        assert len(observations) == 1
        obs = observations[0]
        assert obs.value == 10
        assert obs.attributes["queue"] == "main"
        assert obs.attributes[JIUWENCLAW_CLAW_ID] == "claw-123"
        assert obs.attributes[SERVICE_NAME] == "test-service"

    @staticmethod
    def test_observe_queue_depth_no_resource() -> None:
        """Queue depth observation without resource should only have original attrs."""
        set_resource(None)
        set_queue_depth_observer(lambda: [Observation(5, {"queue": "default"})])

        observations = list(_observe_queue_depth(None))
        assert len(observations) == 1
        obs = observations[0]
        assert obs.value == 5
        assert obs.attributes == {"queue": "default"}

    @staticmethod
    def test_observe_queue_depth_no_observer() -> None:
        """No observer should return empty list."""
        set_queue_depth_observer(None)
        observations = list(_observe_queue_depth(None))
        assert observations == []

    @staticmethod
    def test_observe_queue_depth_observer_exception() -> None:
        """Observer exception should return empty list."""
        resource = Resource({SERVICE_NAME: "test-service"})
        set_resource(resource)
        set_queue_depth_observer(lambda: int("invalid"))  # type: ignore

        observations = list(_observe_queue_depth(None))
        assert observations == []
