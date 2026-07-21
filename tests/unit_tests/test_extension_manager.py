# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from unittest.mock import AsyncMock, MagicMock

import pytest

from jiuwenswarm.extensions.manager import (
    ExtensionManager,
    _extension_dir_paths_from_config,
)


def test_extension_dirs_default_to_builtin_when_empty_string() -> None:
    paths = _extension_dir_paths_from_config({"extensions": {"extension_dirs": ""}})

    assert paths == ["jiuwenswarm/extensions"]


def test_extension_dirs_default_to_builtin_when_missing() -> None:
    assert _extension_dir_paths_from_config({}) == ["jiuwenswarm/extensions"]
    assert _extension_dir_paths_from_config({"extensions": {}}) == [
        "jiuwenswarm/extensions"
    ]


def test_extension_dirs_append_builtin_after_custom_paths() -> None:
    paths = _extension_dir_paths_from_config(
        {"extensions": {"extension_dirs": "custom/a; custom/b "}}
    )

    assert paths == ["custom/a", "custom/b", "jiuwenswarm/extensions"]


def test_extension_dirs_do_not_duplicate_builtin_path() -> None:
    paths = _extension_dir_paths_from_config(
        {"extensions": {"extension_dirs": "custom/a;jiuwenswarm/extensions;custom/a"}}
    )

    assert paths == ["custom/a", "jiuwenswarm/extensions"]


@pytest.mark.asyncio
async def test_required_extension_load_failure_is_propagated(tmp_path) -> None:
    root = tmp_path / "required_extension"
    root.mkdir()
    root.joinpath("extension.yaml").write_text(
        "name: required-extension\nrequired: true\n",
        encoding="utf-8",
    )
    manager = ExtensionManager.__new__(ExtensionManager)
    manager.loader = MagicMock()
    manager.loader.discover_extension_roots.return_value = [root]
    manager.loader.load_extension = AsyncMock(
        side_effect=RuntimeError("required load failed")
    )
    manager._loaded_extensions = []

    with pytest.raises(RuntimeError, match="required load failed"):
        await manager.load_all_extensions()


@pytest.mark.asyncio
async def test_optional_extension_load_failure_remains_isolated(tmp_path) -> None:
    failed_root = tmp_path / "optional_extension"
    healthy_root = tmp_path / "healthy_extension"
    failed_root.mkdir()
    healthy_root.mkdir()
    failed_root.joinpath("extension.yaml").write_text(
        "name: optional-extension\nrequired: false\n",
        encoding="utf-8",
    )
    loaded_extension = object()
    manager = ExtensionManager.__new__(ExtensionManager)
    manager.loader = MagicMock()
    manager.loader.discover_extension_roots.return_value = [failed_root, healthy_root]
    manager.loader.load_extension = AsyncMock(
        side_effect=[RuntimeError("optional load failed"), loaded_extension]
    )
    manager._loaded_extensions = []

    await manager.load_all_extensions()

    assert manager.loader.load_extension.await_count == 2
    assert manager._loaded_extensions == [loaded_extension]


@pytest.mark.asyncio
async def test_invalid_required_marker_fails_startup(tmp_path) -> None:
    root = tmp_path / "invalid_manifest_extension"
    root.mkdir()
    root.joinpath("extension.yaml").write_text(
        "name: invalid-extension\nrequired: mandatory\n",
        encoding="utf-8",
    )
    manager = ExtensionManager.__new__(ExtensionManager)
    manager.loader = MagicMock()
    manager.loader.discover_extension_roots.return_value = [root]
    manager.loader.load_extension = AsyncMock()
    manager._loaded_extensions = []

    with pytest.raises(TypeError, match="required.*true or false"):
        await manager.load_all_extensions()

    manager.loader.load_extension.assert_not_awaited()
