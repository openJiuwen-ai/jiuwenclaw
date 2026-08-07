"""Declarative channel registration metadata."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from jiuwenswarm.gateway.channel_manager.base import BaseChannel


class ChannelConfigError(ValueError):
    """Raised when a channel configuration cannot start a usable runtime."""


@dataclass(frozen=True)
class ChannelCapabilities:
    streaming: bool = False
    images: bool = False
    files: bool = False
    rich_text: bool = False


ChannelFactory = Callable[[dict[str, Any]], BaseChannel]
ChannelValidator = Callable[[Mapping[str, Any]], None]
ChannelHealthcheck = Callable[[BaseChannel], bool]


@dataclass(frozen=True)
class ChannelSpec:
    """Everything ChannelManager needs to validate and construct one channel."""

    channel_id: str
    config_model: type[Any]
    factory: ChannelFactory
    capabilities: ChannelCapabilities = field(default_factory=ChannelCapabilities)
    validator: ChannelValidator | None = None
    startup_grace_seconds: float = 1.0
    healthcheck: ChannelHealthcheck | None = None

    def validate(self, config: Mapping[str, Any]) -> None:
        if self.validator is not None:
            self.validator(config)

    def create(self, config: Mapping[str, Any]) -> BaseChannel:
        self.validate(config)
        return self.factory(dict(config))


def require_fields(*field_names: str) -> ChannelValidator:
    """Build an enabled-aware required-field validator."""

    def _validate(config: Mapping[str, Any]) -> None:
        if not bool(config.get("enabled", False)):
            return
        missing = [name for name in field_names if not str(config.get(name) or "").strip()]
        if missing:
            raise ChannelConfigError(
                f"channel enabled but required fields are missing: {', '.join(missing)}"
            )

    return _validate
