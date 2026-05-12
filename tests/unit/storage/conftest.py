# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Storage module pytest configuration and shared fixtures."""

import tempfile
from pathlib import Path
from typing import Generator

import pytest


@pytest.fixture
def temp_storage_dir(tmp_path: Path) -> Generator[Path, None, None]:
    """Create a temporary storage directory for testing.

    Args:
        tmp_path: pytest's built-in tmp_path fixture

    Yields:
        Path to temporary storage directory
    """
    storage_dir = tmp_path / "storage"
    storage_dir.mkdir(parents=True, exist_ok=True)
    yield storage_dir


@pytest.fixture
def temp_upload_dir(tmp_path: Path) -> Generator[Path, None, None]:
    """Create a temporary upload directory for testing.

    Args:
        tmp_path: pytest's built-in tmp_path fixture

    Yields:
        Path to temporary upload directory
    """
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    yield upload_dir


@pytest.fixture
def sample_file_config() -> dict:
    """Provide sample storage configuration for testing.

    Returns:
        Dictionary with sample storage configuration
    """
    return {
        "type": "local",
        "local": {
            "base_dir": "/tmp/jiuwenclaw/storage",
            "upload_dir": "/tmp/jiuwenclaw/uploads"
        }
    }
