# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Guard unit tests against importing JiuwenSwarm from another checkout."""

from __future__ import annotations

from pathlib import Path

import jiuwenswarm


def test_unit_tests_import_jiuwenswarm_from_current_checkout() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    package_file = Path(jiuwenswarm.__file__).resolve()

    assert package_file.is_relative_to(repo_root / "jiuwenswarm"), (
        f"unit tests imported jiuwenswarm from another checkout: {package_file}"
    )
