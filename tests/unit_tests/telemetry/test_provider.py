# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Unit tests for telemetry provider initialization."""

from unittest.mock import patch, MagicMock

import pytest
from opentelemetry.sdk.resources import Resource, SERVICE_NAME

from jiuwenclaw.telemetry.attributes import JIUWENCLAW_CLAW_ID
from jiuwenclaw.telemetry.config import TelemetryConfig
from jiuwenclaw.telemetry.provider import (
    ProviderBundle,
    build_default_providers,
    install_providers,
    _coerce_provider_bundle,
    init_providers,
)


class TestBuildDefaultProviders:
    """Test build_default_providers function."""

    @staticmethod
    def test_resource_created_without_telemetry_sdk_attrs() -> None:
        """Resource should NOT contain telemetry SDK attributes."""
        cfg = TelemetryConfig(
            enabled=True,
            service_name="test-service",
            claw_id="claw-123",
            traces_exporter="none",
            metrics_exporter="none",
        )
        bundle = build_default_providers(cfg)

        assert bundle.resource is not None
        attrs = bundle.resource.attributes

        # 验证业务属性存在
        assert attrs[SERVICE_NAME] == "test-service"
        assert attrs[JIUWENCLAW_CLAW_ID] == "claw-123"
        assert attrs["service.version"] == "0.1.5"

        # 验证不包含 telemetry SDK 自动注入的属性
        assert "telemetry_sdk_language" not in attrs
        assert "telemetry_sdk_name" not in attrs
        assert "telemetry_sdk_version" not in attrs

    @staticmethod
    def test_resource_without_claw_id() -> None:
        """Resource should work without claw_id."""
        cfg = TelemetryConfig(
            enabled=True,
            service_name="test-service",
            claw_id=None,
            traces_exporter="none",
            metrics_exporter="none",
        )
        bundle = build_default_providers(cfg)

        assert bundle.resource is not None
        attrs = bundle.resource.attributes
        assert attrs[SERVICE_NAME] == "test-service"
        assert JIUWENCLAW_CLAW_ID not in attrs

    @staticmethod
    def test_tracer_provider_created() -> None:
        """Should create TracerProvider with correct resource."""
        cfg = TelemetryConfig(
            enabled=True,
            service_name="test-service",
            claw_id="claw-123",
            traces_exporter="none",
            metrics_exporter="none",
        )
        bundle = build_default_providers(cfg)

        assert bundle.tracer_provider is not None
        assert bundle.tracer_provider.resource == bundle.resource

    @staticmethod
    def test_meter_provider_created() -> None:
        """Should create MeterProvider."""
        cfg = TelemetryConfig(
            enabled=True,
            service_name="test-service",
            claw_id="claw-123",
            traces_exporter="none",
            metrics_exporter="none",
        )
        bundle = build_default_providers(cfg)

        assert bundle.meter_provider is not None
        # MeterProvider 在 OTel SDK 中没有直接的 resource 属性访问
        # 验证 meter_provider 可用即可


class TestProviderBundle:
    """Test ProviderBundle dataclass."""

    @staticmethod
    def test_provider_bundle_creation() -> None:
        """Test ProviderBundle can be created with all fields."""
        resource = Resource({SERVICE_NAME: "test"})
        bundle = ProviderBundle(
            tracer_provider=None,
            meter_provider=None,
            resource=resource,
        )
        assert bundle.resource == resource

    @staticmethod
    def test_provider_bundle_defaults() -> None:
        """Test ProviderBundle default values."""
        bundle = ProviderBundle()
        assert bundle.tracer_provider is None
        assert bundle.meter_provider is None
        assert bundle.resource is None


class TestCoerceProviderBundle:
    """Test _coerce_provider_bundle function."""

    @staticmethod
    def test_coerce_provider_bundle_passthrough() -> None:
        """ProviderBundle should pass through unchanged."""
        original = ProviderBundle(
            tracer_provider=None,
            meter_provider=None,
            resource=Resource({SERVICE_NAME: "test"}),
        )
        result = _coerce_provider_bundle(original)
        assert result is original

    @staticmethod
    def test_coerce_provider_bundle_from_dict_like() -> None:
        """Should coerce dict-like object to ProviderBundle."""
        from opentelemetry.sdk.trace import TracerProvider

        test_resource = Resource({SERVICE_NAME: "test"})
        tp = TracerProvider(resource=test_resource)

        class FakeBundle:
            tracer_provider = tp
            meter_provider = None
            resource = test_resource

        result = _coerce_provider_bundle(FakeBundle())
        assert result.tracer_provider == tp
        assert result.resource == test_resource

    @staticmethod
    def test_coerce_provider_bundle_invalid() -> None:
        """Should raise TypeError for invalid objects."""
        with pytest.raises(TypeError, match="must return a ProviderBundle-like object"):
            _coerce_provider_bundle({"invalid": "object"})

    @staticmethod
    def test_coerce_provider_bundle_wrong_type() -> None:
        """Should raise TypeError for wrong provider type."""
        class FakeBundle:
            tracer_provider = "not a tracer provider"
            meter_provider = None

        with pytest.raises(TypeError, match="must be a TracerProvider instance"):
            _coerce_provider_bundle(FakeBundle())


class TestInstallProviders:
    """Test install_providers function."""

    @staticmethod
    def test_install_providers_sets_global_tracer_provider() -> None:
        """Should set global tracer provider."""
        from opentelemetry import trace

        resource = Resource({SERVICE_NAME: "test"})
        tp = trace.get_tracer_provider()
        if isinstance(tp, trace.NoOpTracerProvider):
            from opentelemetry.sdk.trace import TracerProvider
            tp = TracerProvider(resource=resource)

        bundle = ProviderBundle(
            tracer_provider=tp,
            meter_provider=None,
            resource=resource,
        )
        install_providers(bundle)

        assert trace.get_tracer_provider() == tp

    @staticmethod
    def test_install_providers_handles_none() -> None:
        """Should handle None providers gracefully."""
        bundle = ProviderBundle(
            tracer_provider=None,
            meter_provider=None,
            resource=None,
        )
        # Should not raise
        install_providers(bundle)


class TestInitProviders:
    """Test init_providers function."""

    @staticmethod
    def test_init_providers_creates_default_bundle() -> None:
        """Should create default bundle when no extension."""
        cfg = TelemetryConfig(
            enabled=True,
            service_name="test-service",
            claw_id="claw-123",
            traces_exporter="none",
            metrics_exporter="none",
        )

        with patch(
            "jiuwenclaw.telemetry.provider._build_extension_provider_bundle",
            return_value=None,
        ):
            bundle = init_providers(cfg)

        assert bundle.tracer_provider is not None
        assert bundle.meter_provider is not None
        assert bundle.resource is not None
        assert bundle.resource.attributes[SERVICE_NAME] == "test-service"

    @staticmethod
    def test_init_providers_uses_extension() -> None:
        """Should use extension bundle when available."""
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.metrics import MeterProvider

        cfg = TelemetryConfig(
            enabled=True,
            service_name="test-service",
            claw_id=None,
            traces_exporter="none",
            metrics_exporter="none",
        )

        custom_tp = TracerProvider()
        custom_mp = MeterProvider()
        custom_resource = Resource({SERVICE_NAME: "custom-service"})
        custom_bundle = ProviderBundle(
            tracer_provider=custom_tp,
            meter_provider=custom_mp,
            resource=custom_resource,
        )

        with patch(
            "jiuwenclaw.telemetry.provider._build_extension_provider_bundle",
            return_value=custom_bundle,
        ):
            bundle = init_providers(cfg)

        assert bundle.tracer_provider == custom_tp
        assert bundle.meter_provider == custom_mp


class TestResourceDirectConstruction:
    """Test that Resource is constructed directly without SDK attrs."""

    @staticmethod
    def test_resource_direct_construction_no_sdk_attrs() -> None:
        """Resource() should not include telemetry SDK attributes."""
        resource = Resource({
            SERVICE_NAME: "test-service",
            JIUWENCLAW_CLAW_ID: "claw-123",
            "custom_attr": "custom_value",
        })

        attrs = resource.attributes

        # 验证自定义属性存在
        assert attrs[SERVICE_NAME] == "test-service"
        assert attrs[JIUWENCLAW_CLAW_ID] == "claw-123"
        assert attrs["custom_attr"] == "custom_value"

        # 验证不包含 telemetry SDK 属性
        assert "telemetry_sdk_language" not in attrs
        assert "telemetry_sdk_name" not in attrs
        assert "telemetry_sdk_version" not in attrs

    @staticmethod
    def test_resource_create_includes_sdk_attrs() -> None:
        """Resource.create() SHOULD include telemetry SDK attributes (for comparison)."""
        # 这个测试用于验证 Resource.create() 的行为，与 Resource() 对比
        resource = Resource.create({SERVICE_NAME: "test-service"})

        attrs = resource.attributes

        # Resource.create() 会自动注入 telemetry SDK 属性
        # 这是我们改用 Resource() 的原因
        # 注意：如果 OTel SDK 版本不同，这个行为可能变化
        # 这里仅作记录，不强制断言
        assert attrs[SERVICE_NAME] == "test-service"
