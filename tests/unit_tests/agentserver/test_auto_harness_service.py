# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Unit tests for AutoHarnessService export/import methods."""

import json
import shutil
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from openjiuwen.auto_harness import AutoHarnessConfig

from jiuwenclaw.agentserver.deep_agent.auto_harness_service import AutoHarnessService, _HARNESS_PACKAGES_FILE


class TestBuildAutoHarnessConfig:
    """Tests for per-request AutoHarnessConfig overrides."""

    def test_build_config_pins_workspace_to_local_repo(self, tmp_path: Path):
        """Assess agents must resolve relative paths against the repo checkout."""
        stale_workspace = tmp_path / ".jiuwenclaw"
        local_repo = tmp_path / "agent-core"
        local_repo.mkdir()

        with patch.object(AutoHarnessService, '__init__', lambda self, rail, agent: None):
            with patch.object(AutoHarnessService, '_build_model_from_env', return_value=None):
                service = AutoHarnessService(rail=None, agent=None)
                service.data_dir = tmp_path / "auto-harness"
                service.experience_dir = service.data_dir / "experience"
                service.config_path = service.data_dir / "config.yaml"
                service._base_config = AutoHarnessConfig(
                    data_dir=str(tmp_path / "old-data"),
                    local_repo=str(tmp_path / "old-repo"),
                    workspace=str(stale_workspace),
                    repo_url="https://example.com/old.git",
                )

                config = service.build_auto_harness_config(
                    "https://gitcode.com/openJiuwen/agent-core.git",
                    local_repo,
                    model=None,
                    optimization_goal="评估 Runtime Extension 能力缺口",
                )

        assert config.local_repo == str(local_repo.resolve())
        assert config.workspace == str(local_repo.resolve())
        assert config.optimization_goal == "评估 Runtime Extension 能力缺口"
        assert config.experience_dir == str(service.experience_dir)
        assert service._base_config.workspace == str(stale_workspace)


class TestExportPackage:
    """Tests for export_package method."""

    def test_export_package_creates_zip_file(self, tmp_path: Path):
        """Test that export_package creates a zip archive."""
        # Setup: Create a mock runtime_extensions directory
        runtime_root = tmp_path / "auto-harness" / "runtime_extensions"
        hash_dir = runtime_root / "abc12345"
        ext_dir = hash_dir / "test_extension"
        ext_dir.mkdir(parents=True)

        # Create harness_config.yaml
        config_file = ext_dir / "harness_config.yaml"
        config_file.write_text("extension_name: test_extension\n")

        # Create some files
        prompts_dir = ext_dir / "prompts"
        prompts_dir.mkdir()
        prompts_dir.joinpath("system.md").write_text("Test prompt")

        # Setup packages metadata
        packages_data = {
            "packages": [
                {
                    "id": "pkg_abc12345_test_extension_20260507120000",
                    "extension_name": "test_extension",
                    "runtime_path": str(ext_dir),
                    "config_path": str(config_file),
                    "created_at": "2026-05-07T12:00:00",
                    "is_active": False,
                    "version_label": "",
                    "description": "",
                }
            ],
            "native_version": {"id": "native", "extension_name": "Native Agent", "is_active": True},
            "active_package_id": None,
        }

        # Create temp directory for exports
        temp_dir = tmp_path / "auto-harness" / "temp" / "exports"
        temp_dir.mkdir(parents=True)

        # Mock the service
        with patch.object(AutoHarnessService, '__init__', lambda self, rail, agent: None):
            service = AutoHarnessService(rail=None, agent=None)
            service.data_dir = tmp_path / "auto-harness"
            service._ensure_data_dirs = lambda: None

            # Mock load_packages to return test data
            service.load_packages = MagicMock(return_value=packages_data)

            # Execute
            package_id = "pkg_abc12345_test_extension_20260507120000"
            zip_path = service.export_package(package_id)

            # Verify
            assert zip_path.exists()
            assert zip_path.suffix == ".zip"
            assert "test_extension" in zip_path.name

            # Verify zip contents
            import zipfile
            with zipfile.ZipFile(zip_path, 'r') as zf:
                names = zf.namelist()
                assert any("harness_config.yaml" in n for n in names)
                assert any("prompts/system.md" in n for n in names)

    def test_export_package_not_found_raises_error(self, tmp_path: Path):
        """Test that export_package raises ValueError for non-existent package."""
        packages_data = {
            "packages": [],
            "native_version": {"id": "native", "extension_name": "Native Agent", "is_active": True},
            "active_package_id": None,
        }

        with patch.object(AutoHarnessService, '__init__', lambda self, rail, agent: None):
            service = AutoHarnessService(rail=None, agent=None)
            service.data_dir = tmp_path / "auto-harness"
            service.load_packages = MagicMock(return_value=packages_data)

            with pytest.raises(ValueError, match="Package not found"):
                service.export_package("pkg_nonexistent")

    def test_export_native_raises_error(self, tmp_path: Path):
        """Test that export_package raises ValueError for native version."""
        packages_data = {
            "packages": [],
            "native_version": {"id": "native", "extension_name": "Native Agent", "is_active": True},
            "active_package_id": None,
        }

        with patch.object(AutoHarnessService, '__init__', lambda self, rail, agent: None):
            service = AutoHarnessService(rail=None, agent=None)
            service.data_dir = tmp_path / "auto-harness"
            service.load_packages = MagicMock(return_value=packages_data)

            with pytest.raises(ValueError, match="Cannot export native version"):
                service.export_package("native")


class TestImportPackage:
    """Tests for import_package method."""

    def test_import_package_creates_new_package(self, tmp_path: Path):
        """Test that import_package extracts zip and registers package."""
        # Setup: Create a valid zip file
        runtime_root = tmp_path / "auto-harness" / "runtime_extensions"
        runtime_root.mkdir(parents=True)

        # Create temp directory with package structure
        source_ext = tmp_path / "source_ext"
        source_ext.mkdir()
        config_file = source_ext / "harness_config.yaml"
        config_file.write_text("extension_name: imported_extension\nversion: 1.0\n")
        prompts_dir = source_ext / "prompts"
        prompts_dir.mkdir()
        prompts_dir.joinpath("system.md").write_text("Imported prompt")

        # Create zip from source
        zip_path = tmp_path / "imported_extension.zip"
        shutil.make_archive(str(zip_path.with_suffix("")), "zip", source_ext.parent, source_ext.name)

        # Setup existing packages metadata
        packages_file = tmp_path / "auto-harness" / "harness-packages.json"
        packages_file.parent.mkdir(parents=True, exist_ok=True)
        packages_data = {
            "packages": [],
            "native_version": {"id": "native", "extension_name": "Native Agent", "is_active": True},
            "active_package_id": None,
        }
        packages_file.write_text(json.dumps(packages_data))

        # Create temp uploads directory
        uploads_dir = tmp_path / "auto-harness" / "temp" / "uploads"
        uploads_dir.mkdir(parents=True)

        with patch.object(AutoHarnessService, '__init__', lambda self, rail, agent: None):
            with patch("jiuwenclaw.agentserver.deep_agent.auto_harness_service._HARNESS_PACKAGES_FILE", packages_file):
                service = AutoHarnessService(rail=None, agent=None)
                service.data_dir = tmp_path / "auto-harness"
                service._ensure_data_dirs = lambda: None

                result = service.import_package(zip_path)

                # Verify result
                assert result["extension_name"] == "imported_extension"
                assert "id" in result
                assert result["id"].startswith("pkg_")

                # Verify directory created
                new_runtime_path = Path(result["runtime_path"])
                assert new_runtime_path.exists()
                assert (new_runtime_path / "harness_config.yaml").exists()

                # Verify package registered
                new_packages_data = json.loads(packages_file.read_text())
                assert len(new_packages_data["packages"]) == 1
                assert new_packages_data["packages"][0]["extension_name"] == "imported_extension"

    def test_import_package_missing_config_raises_error(self, tmp_path: Path):
        """Test that import_package raises ValueError for zip without harness_config.yaml."""
        # Create invalid zip (no harness_config.yaml)
        source_dir = tmp_path / "invalid_ext"
        source_dir.mkdir()
        source_dir.joinpath("random_file.txt").write_text("No config here")

        zip_path = tmp_path / "invalid.zip"
        shutil.make_archive(str(zip_path.with_suffix("")), "zip", source_dir)

        packages_file = tmp_path / "auto-harness" / "harness-packages.json"
        packages_file.parent.mkdir(parents=True, exist_ok=True)
        packages_file.write_text(json.dumps({"packages": [], "native_version": {}, "active_package_id": None}))

        with patch.object(AutoHarnessService, '__init__', lambda self, rail, agent: None):
            with patch("jiuwenclaw.agentserver.deep_agent.auto_harness_service._HARNESS_PACKAGES_FILE", packages_file):
                service = AutoHarnessService(rail=None, agent=None)
                service.data_dir = tmp_path / "auto-harness"

                with pytest.raises(ValueError, match="Zip must contain harness_config.yaml"):
                    service.import_package(zip_path)

    def test_import_package_name_conflict_raises_error(self, tmp_path: Path):
        """Test that import_package raises ValueError when extension_name already exists."""
        # Setup existing package with same name
        runtime_root = tmp_path / "auto-harness" / "runtime_extensions"
        existing_hash_dir = runtime_root / "existing123"
        existing_ext = existing_hash_dir / "duplicate_name"
        existing_ext.mkdir(parents=True)
        existing_ext.joinpath("harness_config.yaml").write_text("extension_name: duplicate_name")

        packages_file = tmp_path / "auto-harness" / "harness-packages.json"
        packages_file.parent.mkdir(parents=True, exist_ok=True)
        packages_data = {
            "packages": [
                {
                    "id": "pkg_existing_duplicate_name",
                    "extension_name": "duplicate_name",
                    "runtime_path": str(existing_ext),
                }
            ],
            "native_version": {},
            "active_package_id": None,
        }
        packages_file.write_text(json.dumps(packages_data))

        # Create zip with same extension_name
        source_dir = tmp_path / "new_source"
        source_dir.mkdir()
        source_dir.joinpath("harness_config.yaml").write_text("extension_name: duplicate_name")

        zip_path = tmp_path / "duplicate_name.zip"
        shutil.make_archive(str(zip_path.with_suffix("")), "zip", source_dir)

        with patch.object(AutoHarnessService, '__init__', lambda self, rail, agent: None):
            with patch("jiuwenclaw.agentserver.deep_agent.auto_harness_service._HARNESS_PACKAGES_FILE", packages_file):
                service = AutoHarnessService(rail=None, agent=None)
                service.data_dir = tmp_path / "auto-harness"

                with pytest.raises(ValueError, match="already exists"):
                    service.import_package(zip_path)

    def test_import_package_invalid_zip_raises_error(self, tmp_path: Path):
        """Test that import_package raises ValueError for invalid zip file."""
        # Create invalid "zip" file (not actually a zip)
        zip_path = tmp_path / "invalid.zip"
        zip_path.write_text("This is not a zip file")

        packages_file = tmp_path / "auto-harness" / "harness-packages.json"
        packages_file.parent.mkdir(parents=True, exist_ok=True)
        packages_file.write_text(json.dumps({"packages": [], "native_version": {}, "active_package_id": None}))

        with patch.object(AutoHarnessService, '__init__', lambda self, rail, agent: None):
            with patch("jiuwenclaw.agentserver.deep_agent.auto_harness_service._HARNESS_PACKAGES_FILE", packages_file):
                service = AutoHarnessService(rail=None, agent=None)
                service.data_dir = tmp_path / "auto-harness"

                with pytest.raises(ValueError, match="Invalid zip file"):
                    service.import_package(zip_path)
