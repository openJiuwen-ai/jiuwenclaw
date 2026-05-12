# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Basic tests for storage module to ensure framework is working."""

import pytest

from jiuwenclaw.storage import StorageBackend, StorageService
from jiuwenclaw.storage.exceptions import (
    StorageError,
    StorageFileNotFoundError,
    StoragePermissionError,
    UploadError,
    DownloadError,
    ConfigError,
)


class TestStorageModuleImport:
    """Test that storage module can be imported."""

    @staticmethod
    def test_storage_backend_is_abstract():
        """StorageBackend should be an abstract class that cannot be instantiated."""
        with pytest.raises(TypeError):
            StorageBackend()

    @staticmethod
    def test_storage_exceptions_exist():
        """Test that all storage exceptions are defined."""
        assert StorageError is not None
        assert StorageFileNotFoundError is not None
        assert StoragePermissionError is not None
        assert UploadError is not None
        assert DownloadError is not None
        assert ConfigError is not None

    @staticmethod
    def test_storage_service_class_exists():
        """Test that StorageService class exists."""
        assert StorageService is not None
        assert hasattr(StorageService, "get_instance")


@pytest.mark.asyncio
async def test_storage_service_get_instance(sample_file_config):
    """Test StorageService.get_instance() method (basic smoke test)."""
    # This is a basic smoke test to ensure the factory pattern structure works
    service = await StorageService.get_instance()
    # Service should be callable and return a backend instance
    assert service is not None
