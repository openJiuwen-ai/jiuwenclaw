# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from jiuwenswarm.extensions.manager import (
    _bundled_ee_extension_dir,
    _extension_dir_paths_from_config,
)


def _with_bundled(paths: list[str]) -> list[str]:
    bundled = _bundled_ee_extension_dir()
    if bundled is None:
        return paths
    bundled_str = str(bundled)
    if bundled_str in paths:
        return paths
    return [*paths, bundled_str]


def test_extension_dirs_default_to_builtin_when_empty_string() -> None:
    paths = _extension_dir_paths_from_config(
        {"extensions": {"extension_dirs": ""}}
    )

    assert paths == _with_bundled(["jiuwenswarm/extensions"])


def test_extension_dirs_default_to_builtin_when_missing() -> None:
    assert _extension_dir_paths_from_config({}) == _with_bundled(
        ["jiuwenswarm/extensions"]
    )
    assert _extension_dir_paths_from_config({"extensions": {}}) == _with_bundled(
        ["jiuwenswarm/extensions"]
    )


def test_extension_dirs_append_builtin_after_custom_paths() -> None:
    paths = _extension_dir_paths_from_config(
        {"extensions": {"extension_dirs": "custom/a; custom/b "}}
    )

    assert paths == _with_bundled(
        ["custom/a", "custom/b", "jiuwenswarm/extensions"]
    )


def test_extension_dirs_do_not_duplicate_builtin_path() -> None:
    paths = _extension_dir_paths_from_config(
        {
            "extensions": {
                "extension_dirs": "custom/a;jiuwenswarm/extensions;custom/a"
            }
        }
    )

    assert paths == _with_bundled(["custom/a", "jiuwenswarm/extensions"])
