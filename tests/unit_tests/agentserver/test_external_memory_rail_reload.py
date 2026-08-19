# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for ExternalMemoryRail fingerprint-driven reload behavior.

Mirrors external-memory rail reload register/unregister decisions without
importing ``interface_deep``.
"""

# pylint: disable=protected-access

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from jiuwenclaw.agentserver.memory.external_memory_config import (
    external_memory_fingerprint,
    is_external_memory_enabled,
)


class _ExternalMemoryRailHandlerHarness:
    """Minimal adapter surface for external-memory rail reload tests."""

    def __init__(self) -> None:
        self._instance = MagicMock()
        self._instance.register_rail = AsyncMock()
        self._instance.unregister_rail = AsyncMock()
        self._external_memory_rail = None
        self._external_memory_rail_registered = False
        self._external_memory_fingerprint: str | None = None
        self._build_calls = 0

    @property
    def instance(self):
        return self._instance

    @property
    def external_memory_rail(self):
        return self._external_memory_rail

    @property
    def external_memory_rail_registered(self) -> bool:
        return self._external_memory_rail_registered

    @property
    def external_memory_fingerprint(self) -> str | None:
        return self._external_memory_fingerprint

    @property
    def build_calls(self) -> int:
        return self._build_calls

    def set_registered_state(
        self,
        *,
        registered: bool,
        fingerprint: str | None,
        rail=None,
    ) -> None:
        self._external_memory_rail_registered = registered
        self._external_memory_fingerprint = fingerprint
        if rail is None:
            rail = MagicMock(name="external_rail")
            rail.provider = None
        self._external_memory_rail = rail

    def _build_external_memory_rail(self):
        self._build_calls += 1
        return MagicMock(name=f"built_rail_{self._build_calls}")

    async def _unregister_external_memory_rail(self) -> None:
        if self._external_memory_rail is None or not self._external_memory_rail_registered:
            self._external_memory_rail = None
            self._external_memory_rail_registered = False
            return
        rail = self._external_memory_rail
        provider = rail.__dict__.get("provider", rail.__dict__.get("_provider"))
        if provider is not None and hasattr(provider, "on_session_end"):
            await provider.on_session_end()
        await self._instance.unregister_rail(self._external_memory_rail)
        self._external_memory_rail = None
        self._external_memory_rail_registered = False

    async def _handle_external_memory_rail_by_config(self, config: dict) -> None:
        new_fp = external_memory_fingerprint(config)

        if is_external_memory_enabled(config):
            if (
                self._external_memory_rail_registered
                and self._external_memory_fingerprint == new_fp
            ):
                return

            if self._external_memory_rail_registered:
                await self._unregister_external_memory_rail()

            self._external_memory_rail = self._build_external_memory_rail()
            if self._external_memory_rail is None:
                self._external_memory_fingerprint = None
                return
            await self._instance.register_rail(self._external_memory_rail)
            self._external_memory_rail_registered = True
            self._external_memory_fingerprint = new_fp
        elif self._external_memory_rail_registered:
            await self._unregister_external_memory_rail()
            self._external_memory_fingerprint = None

    async def handle_external_memory_rail_by_config(self, config: dict) -> None:
        """Public test entry mirroring adapter reload hook."""
        await self._handle_external_memory_rail_by_config(config)


def _external_cfg(*, user_id: str = "alice") -> dict:
    return {
        "memory": {
            "engine": "external",
            "external": {
                "provider": "mem0",
                "user_id": user_id,
            },
        },
    }


def _make_harness(*, registered: bool, fingerprint: str | None, rail=None):
    harness = _ExternalMemoryRailHandlerHarness()
    harness.set_registered_state(registered=registered, fingerprint=fingerprint, rail=rail)
    return harness


@pytest.mark.asyncio
async def test_registered_same_fingerprint_skips_reregister():
    cfg = _external_cfg()
    fp = external_memory_fingerprint(cfg)
    harness = _make_harness(registered=True, fingerprint=fp)

    await harness.handle_external_memory_rail_by_config(cfg)

    harness.instance.unregister_rail.assert_not_awaited()
    harness.instance.register_rail.assert_not_awaited()
    assert harness.external_memory_fingerprint == fp
    assert harness.build_calls == 0


@pytest.mark.asyncio
async def test_registered_different_fingerprint_rebuilds_rail():
    old_cfg = _external_cfg(user_id="alice")
    new_cfg = _external_cfg(user_id="bob")
    old_fp = external_memory_fingerprint(old_cfg)
    new_fp = external_memory_fingerprint(new_cfg)
    assert old_fp != new_fp

    old_rail = MagicMock(name="old_rail")
    old_rail.provider = None
    harness = _make_harness(registered=True, fingerprint=old_fp, rail=old_rail)

    await harness.handle_external_memory_rail_by_config(new_cfg)

    harness.instance.unregister_rail.assert_awaited_once_with(old_rail)
    harness.instance.register_rail.assert_awaited_once()
    assert harness.external_memory_rail is not old_rail
    assert harness.external_memory_fingerprint == new_fp
    assert harness.external_memory_rail_registered is True
    assert harness.build_calls == 1


@pytest.mark.asyncio
async def test_external_disabled_unregisters_and_clears_fingerprint():
    cfg = {"memory": {"engine": "none", "external": {"provider": "mem0"}}}
    fp = external_memory_fingerprint(_external_cfg())
    rail = MagicMock(name="external_rail")
    rail.provider = None
    harness = _make_harness(registered=True, fingerprint=fp, rail=rail)

    await harness.handle_external_memory_rail_by_config(cfg)

    harness.instance.unregister_rail.assert_awaited_once_with(rail)
    harness.instance.register_rail.assert_not_awaited()
    assert harness.external_memory_rail is None
    assert harness.external_memory_rail_registered is False
    assert harness.external_memory_fingerprint is None
