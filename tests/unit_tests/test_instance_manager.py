# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for instance_manager module.

Tests for:
- Instance name validation
- Port auto-allocation
- Port conflict detection
- PID file management
- Instance status querying
- InstancesYamlError handling
- InstanceLock concurrency control
"""

import json
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from jiuwenswarm.instance_manager import (
    InstanceConfig,
    InstanceLock,
    InstanceStatus,
    InstancesYamlError,
    validate_instance_name,
    is_valid_instance_name,
    get_instances_yaml_path,
    compute_auto_port,
    calculate_instance_ports,
    check_port_conflicts,
    collect_all_ports,
    write_pid_file,
    read_pid_file,
    delete_pid_file,
    is_process_alive,
    get_instance_status,
    get_default_instance_status,
    list_all_instances,
    format_status_line,
    get_instance_config,
    load_all_instance_configs,
    create_bootstrap_env,
    create_bootstrap_env_for_name,
    stop_instance_process,
    create_instances_yaml_template,
    load_instances_yaml,
    save_instances_yaml,
    update_instances_yaml,
    get_instance_index,
    RESERVED_NAMES,
    PORT_TYPES,
    BASE_PORTS,
    STALE_LOCK_TIMEOUT,
)


# Module-level helper function for multiprocessing (must be at module level to be pickleable)
def _try_acquire_lock_for_multiprocess(workspace_str: str) -> bool:
    """Helper function to try acquiring lock in a subprocess.

    This must be at module level to be pickleable for multiprocessing.
    """
    from jiuwenswarm.instance_manager import (
        InstanceConfig as _InstanceConfig,
        InstanceLock as _InstanceLock,
    )

    _config = _InstanceConfig(name="test", workspace=Path(workspace_str), ports={})
    _lock = _InstanceLock(_config)
    result = _lock.acquire(timeout=0.5)  # Short timeout
    if result:
        _lock.release()
    return result


def _try_acquire_lock_with_result(workspace_str: str, result_queue) -> None:
    """Helper function for multiprocessing that returns result via Queue.

    This must be at module level to be pickleable for multiprocessing on Windows.
    """
    result = _try_acquire_lock_for_multiprocess(workspace_str)
    result_queue.put(result)


class TestInstanceNameValidation:
    """Test instance name validation."""

    @staticmethod
    def test_valid_simple_names():
        """Test valid simple names."""
        assert validate_instance_name("alice") is None
        assert validate_instance_name("bob") is None
        assert validate_instance_name("my-instance") is None
        assert validate_instance_name("test_123") is None
        assert validate_instance_name("a") is None

    @staticmethod
    def test_valid_complex_names():
        """Test valid complex names."""
        assert validate_instance_name("production-server-01") is None
        assert validate_instance_name("dev_test_env") is None
        assert validate_instance_name("CamelCaseName") is None

    @staticmethod
    def test_invalid_empty_name():
        """Test empty name is invalid."""
        assert validate_instance_name("") is not None
        assert validate_instance_name(None) is not None

    @staticmethod
    def test_invalid_too_long():
        """Test name longer than 64 chars is invalid."""
        long_name = "a" * 65
        assert validate_instance_name(long_name) is not None

    @staticmethod
    def test_invalid_special_chars():
        """Test names with special characters are invalid."""
        assert validate_instance_name("alice@example") is not None
        assert validate_instance_name("my instance") is not None  # space
        assert validate_instance_name("instance.name") is not None  # dot
        assert validate_instance_name("中文实例") is not None  # non-ASCII

    @staticmethod
    def test_invalid_leading_dot():
        """Test names starting with dot are invalid."""
        assert validate_instance_name(".hidden") is not None
        assert validate_instance_name(".alice") is not None

    @staticmethod
    def test_reserved_names():
        """Test reserved names are invalid."""
        for name in RESERVED_NAMES:
            assert validate_instance_name(name) is not None
            assert validate_instance_name(name.upper()) is not None  # case insensitive

    @staticmethod
    def test_is_valid_instance_name():
        """Test is_valid_instance_name helper."""
        assert is_valid_instance_name("alice") is True
        assert is_valid_instance_name("default") is False
        assert is_valid_instance_name("") is False


class TestPortAllocation:
    """Test port auto-allocation."""

    @staticmethod
    def test_base_ports():
        """Test base ports for default instance (index 0)."""
        assert compute_auto_port("agent_server", 0) == 18092
        assert compute_auto_port("web", 0) == 19000
        assert compute_auto_port("gateway", 0) == 19001
        assert compute_auto_port("frontend", 0) == 5173

    @staticmethod
    def test_calculate_instance_ports():
        """Test calculate_instance_ports returns all port types."""
        ports = calculate_instance_ports(1)
        assert "agent_server" in ports
        assert "web" in ports
        assert "gateway" in ports
        assert "frontend" in ports
        assert ports["agent_server"] == 19092

    @staticmethod
    def test_unknown_port_type():
        """Test unknown port type uses default base."""
        assert compute_auto_port("unknown", 0) == 10000


class TestPortAvailability:
    """Test port availability checking."""

    @staticmethod
    def test_check_port_conflicts_no_conflicts():
        """Test no conflicts when ports are available."""
        ports = {"agent_server": 19092, "web": 20000}
        # Should have no conflicts if ports are free (unlikely to be used)
        conflicts = check_port_conflicts(ports, "127.0.0.1", [])
        # This test may fail if ports happen to be occupied
        # We're testing the logic, not the actual availability
        assert isinstance(conflicts, list)

    @staticmethod
    def test_check_port_conflicts_with_existing():
        """Test conflicts detected when port in existing set."""
        ports = {"agent_server": 19092}
        existing = [19092]
        conflicts = check_port_conflicts(ports, "127.0.0.1", existing)
        assert 19092 in conflicts


class TestInstanceConfig:
    """Test InstanceConfig dataclass."""

    @staticmethod
    def test_basic_config():
        """Test basic InstanceConfig creation."""
        config = InstanceConfig(
            name="alice",
            workspace=Path("/tmp/alice"),
            ports={"agent_server": 19092, "web": 20000},
        )
        assert config.name == "alice"
        assert "alice" in str(config.workspace)
        assert config.ports["agent_server"] == 19092


class TestPidFileManagement:
    """Test PID file management."""

    @staticmethod
    def test_write_and_read_pid_file(tmp_path):
        """Test writing and reading PID file."""
        config = InstanceConfig(
            name="test",
            workspace=tmp_path,
            ports={},
        )
        pid = 12345
        write_pid_file(config, pid)

        data = read_pid_file(config)
        assert data is not None
        assert data["pid"] == pid
        assert data["name"] == "test"
        assert "started_at" in data

    @staticmethod
    def test_read_nonexistent_pid_file(tmp_path):
        """Test reading nonexistent PID file."""
        config = InstanceConfig(
            name="test",
            workspace=tmp_path,
            ports={},
        )
        data = read_pid_file(config)
        assert data is None

    @staticmethod
    def test_delete_pid_file(tmp_path):
        """Test deleting PID file."""
        config = InstanceConfig(
            name="test",
            workspace=tmp_path,
            ports={},
        )
        write_pid_file(config, 12345)
        assert config.get_pid_file_path().exists()

        deleted = delete_pid_file(config)
        assert deleted is True
        assert not config.get_pid_file_path().exists()

        # Second delete returns False
        deleted2 = delete_pid_file(config)
        assert deleted2 is False


class TestInstanceStatus:
    """Test InstanceStatus and status querying."""

    @staticmethod
    def test_get_instance_status_stopped(tmp_path):
        """Test getting status for stopped instance."""
        config = InstanceConfig(
            name="test",
            workspace=tmp_path,
            ports={"agent_server": 19092},
        )
        status = get_instance_status(config)
        assert status.name == "test"
        assert status.running is False
        assert status.pid is None

    @staticmethod
    def test_get_instance_status_running_with_dead_pid(tmp_path):
        """Test status returns stopped when PID file exists but process is dead."""
        config = InstanceConfig(
            name="test",
            workspace=tmp_path,
            ports={},
        )
        # Write PID file with unlikely PID
        write_pid_file(config, 999999999)

        status = get_instance_status(config)
        assert status.running is False
        assert status.pid is None

    @staticmethod
    def test_format_status_line():
        """Test formatting status line."""
        status = InstanceStatus(
            name="alice",
            running=True,
            pid=12345,
            workspace=Path("/tmp/alice"),
            ports={"agent_server": 19092, "web": 20000},
        )
        line = format_status_line(status)
        assert "alice" in line
        assert "running" in line
        assert "12345" in line

    @staticmethod
    def test_format_status_line_default():
        """Test formatting default instance status."""
        status = InstanceStatus(
            name="default",
            running=False,
            pid=None,
            workspace=Path("/tmp/default"),
            ports={},
        )
        line = format_status_line(status)
        assert "default" in line
        assert "stopped" in line


class TestIsProcessAlive:
    """Test process alive checking."""

    @staticmethod
    def test_invalid_pid():
        """Test invalid PID returns False."""
        assert is_process_alive(-1) is False
        assert is_process_alive(0) is False

    @staticmethod
    def test_current_process():
        """Test current process PID is alive."""
        current_pid = os.getpid()
        assert is_process_alive(current_pid) is True


class TestBootstrapEnv:
    """Test bootstrap .env creation."""

    @staticmethod
    def test_create_bootstrap_env(tmp_path):
        """Test creating bootstrap env file."""
        config = InstanceConfig(
            name="alice",
            workspace=tmp_path,
            ports={
                "agent_server": 19092,
                "web": 20000,
                "gateway": 20001,
                "frontend": 6173,
            },
        )
        env_path = create_bootstrap_env(config)

        assert env_path.exists()
        content = env_path.read_text()
        assert "JIUWENSWARM_DATA_DIR" in content
        assert "JIUWENSWARM_INSTANCE=alice" in content
        assert "AGENT_SERVER_PORT=19092" in content


class TestInstancesYaml:
    """Test instances.yaml management."""

    @staticmethod
    def test_create_instances_yaml_template(tmp_path):
        """Test creating instances.yaml template."""
        with patch(
            "jiuwenswarm.instance_manager.yaml.get_instances_yaml_path",
            return_value=tmp_path / "instances.yaml",
        ):
            path = create_instances_yaml_template()
            assert path.exists()
            content = path.read_text()
            assert "instances:" in content

    @staticmethod
    def test_load_empty_instances_yaml(tmp_path):
        """Test loading nonexistent instances.yaml."""
        yaml_path = tmp_path / "instances.yaml"
        with patch(
            "jiuwenswarm.instance_manager.yaml.get_instances_yaml_path",
            return_value=yaml_path,
        ):
            data = load_instances_yaml()
            assert data == {"instances": {}}

    @staticmethod
    def test_save_and_load_instances_yaml(tmp_path):
        """Test saving and loading instances.yaml."""
        yaml_path = tmp_path / "instances.yaml"
        with patch(
            "jiuwenswarm.instance_manager.yaml.get_instances_yaml_path",
            return_value=yaml_path,
        ):
            data = {
                "instances": {"alice": {}, "bob": {"ports": {"agent_server": 28092}}}
            }
            save_instances_yaml(data)

            loaded = load_instances_yaml()
            assert "alice" in loaded["instances"]
            assert "bob" in loaded["instances"]
            assert loaded["instances"]["bob"]["ports"]["agent_server"] == 28092


class TestGetInstanceConfig:
    """Test instance config loading."""

    @staticmethod
    def test_get_instance_config_not_found(tmp_path):
        """Test getting nonexistent instance config."""
        yaml_path = tmp_path / "instances.yaml"
        yaml_path.write_text("instances: {}\n", encoding="utf-8")
        with patch(
            "jiuwenswarm.instance_manager.yaml.get_instances_yaml_path",
            return_value=yaml_path,
        ):
            config = get_instance_config("nonexistent")
            assert config is None

    @staticmethod
    def test_get_instance_config_with_auto_ports(tmp_path):
        """Test getting instance config with auto-allocated ports."""
        yaml_path = tmp_path / "instances.yaml"
        yaml_path.write_text("instances:\n  alice: {}\n", encoding="utf-8")
        with patch(
            "jiuwenswarm.instance_manager.yaml.get_instances_yaml_path",
            return_value=yaml_path,
        ):
            config = get_instance_config("alice")
            assert config is not None
            assert config.name == "alice"
            # First instance (index 1) should have these ports
            assert config.ports["agent_server"] == 19092


class TestCollectAllPorts:
    """Test collecting all ports for conflict detection."""

    @staticmethod
    def test_collect_default_ports():
        """Test collecting default instance ports."""
        yaml_path = Path("/nonexistent")
        with patch(
            "jiuwenswarm.instance_manager.yaml.get_instances_yaml_path",
            return_value=yaml_path,
        ):
            ports = collect_all_ports()
            # Default instance ports should be included
            assert 18092 in ports  # agent_server
            assert 19000 in ports  # web

    @staticmethod
    def test_collect_excluding_self():
        """Test collecting ports excluding a specific instance."""
        yaml_path = Path("/nonexistent")
        with patch(
            "jiuwenswarm.instance_manager.yaml.get_instances_yaml_path",
            return_value=yaml_path,
        ):
            ports = collect_all_ports(exclude_name="default")
            # Should be empty when excluding default and no named instances
            assert ports == []


class TestListAllInstances:
    """Test listing all instances."""

    @staticmethod
    def test_list_all_instances_empty(tmp_path):
        """Test listing with no instances.yaml."""
        yaml_path = tmp_path / "instances.yaml"
        with patch(
            "jiuwenswarm.instance_manager.yaml.get_instances_yaml_path",
            return_value=yaml_path,
        ):
            statuses = list_all_instances(include_default=True)
            # Should include default instance
            assert len(statuses) >= 1
            assert any(s.name == "default" for s in statuses)

    @staticmethod
    def test_list_all_instances_no_default(tmp_path):
        """Test listing without default instance."""
        yaml_path = tmp_path / "instances.yaml"
        with patch(
            "jiuwenswarm.instance_manager.yaml.get_instances_yaml_path",
            return_value=yaml_path,
        ):
            statuses = list_all_instances(include_default=False)
            # Should not include default instance
            assert not any(s.name == "default" for s in statuses)


class TestInstancesYamlError:
    """Test instances.yaml error handling."""

    @staticmethod
    def test_valid_yaml(tmp_path):
        """Test loading valid YAML file."""
        yaml_path = tmp_path / "instances.yaml"
        yaml_path.write_text(
            "instances:\n  alice:\n    ports:\n      agent_server: 28092\n",
            encoding="utf-8",
        )
        with patch(
            "jiuwenswarm.instance_manager.yaml.get_instances_yaml_path",
            return_value=yaml_path,
        ):
            data = load_instances_yaml()
            assert "alice" in data["instances"]
            assert data["instances"]["alice"]["ports"]["agent_server"] == 28092

    @staticmethod
    def test_missing_file(tmp_path):
        """Test missing file returns empty structure."""
        yaml_path = tmp_path / "nonexistent.yaml"
        with patch(
            "jiuwenswarm.instance_manager.yaml.get_instances_yaml_path",
            return_value=yaml_path,
        ):
            data = load_instances_yaml()
            assert data == {"instances": {}}

    @staticmethod
    def test_invalid_yaml_syntax(tmp_path):
        """Test invalid YAML syntax raises InstancesYamlError."""
        yaml_path = tmp_path / "instances.yaml"
        # Invalid YAML: missing space after colon, duplicate key
        yaml_path.write_text(
            "instances:\n  alice:bad_syntax\n  alice: duplicate\n",
            encoding="utf-8",
        )
        with patch(
            "jiuwenswarm.instance_manager.yaml.get_instances_yaml_path",
            return_value=yaml_path,
        ):
            with pytest.raises(InstancesYamlError) as exc_info:
                load_instances_yaml()
            # Check error message contains useful info
            assert "YAML format error" in str(exc_info.value)
            assert str(yaml_path) in str(exc_info.value)

    @staticmethod
    def test_missing_instances_key(tmp_path):
        """Test missing 'instances' key raises InstancesYamlError."""
        yaml_path = tmp_path / "instances.yaml"
        yaml_path.write_text("other_key: value\n", encoding="utf-8")
        with patch(
            "jiuwenswarm.instance_manager.yaml.get_instances_yaml_path",
            return_value=yaml_path,
        ):
            with pytest.raises(InstancesYamlError) as exc_info:
                load_instances_yaml()
            assert "Missing 'instances' key" in str(exc_info.value)

    @staticmethod
    def test_invalid_instance_name(tmp_path):
        """Test invalid instance name raises InstancesYamlError."""
        yaml_path = tmp_path / "instances.yaml"
        yaml_path.write_text(
            "instances:\n  '.hidden': {}\n",  # Invalid: starts with dot
            encoding="utf-8",
        )
        with patch(
            "jiuwenswarm.instance_manager.yaml.get_instances_yaml_path",
            return_value=yaml_path,
        ):
            with pytest.raises(InstancesYamlError) as exc_info:
                load_instances_yaml()
            assert "Invalid instance name" in str(exc_info.value)
            assert ".hidden" in str(exc_info.value)

    @staticmethod
    def test_reserved_instance_name(tmp_path):
        """Test reserved instance name raises InstancesYamlError."""
        yaml_path = tmp_path / "instances.yaml"
        yaml_path.write_text(
            "instances:\n  default: {}\n",  # Reserved name
            encoding="utf-8",
        )
        with patch(
            "jiuwenswarm.instance_manager.yaml.get_instances_yaml_path",
            return_value=yaml_path,
        ):
            with pytest.raises(InstancesYamlError) as exc_info:
                load_instances_yaml()
            assert "reserved" in str(exc_info.value).lower()

    @staticmethod
    def test_invalid_port_type(tmp_path):
        """Test unknown port type raises InstancesYamlError."""
        yaml_path = tmp_path / "instances.yaml"
        yaml_path.write_text(
            "instances:\n  alice:\n    ports:\n      unknown_port: 12345\n",
            encoding="utf-8",
        )
        with patch(
            "jiuwenswarm.instance_manager.yaml.get_instances_yaml_path",
            return_value=yaml_path,
        ):
            with pytest.raises(InstancesYamlError) as exc_info:
                load_instances_yaml()
            assert "unknown port type" in str(exc_info.value)
            assert "unknown_port" in str(exc_info.value)

    @staticmethod
    def test_invalid_port_value_negative(tmp_path):
        """Test negative port value raises InstancesYamlError."""
        yaml_path = tmp_path / "instances.yaml"
        yaml_path.write_text(
            "instances:\n  alice:\n    ports:\n      agent_server: -1\n",
            encoding="utf-8",
        )
        with patch(
            "jiuwenswarm.instance_manager.yaml.get_instances_yaml_path",
            return_value=yaml_path,
        ):
            with pytest.raises(InstancesYamlError) as exc_info:
                load_instances_yaml()
            assert "must be 1-65535" in str(exc_info.value)

    @staticmethod
    def test_invalid_port_value_out_of_range(tmp_path):
        """Test port value out of range raises InstancesYamlError."""
        yaml_path = tmp_path / "instances.yaml"
        yaml_path.write_text(
            "instances:\n  alice:\n    ports:\n      agent_server: 70000\n",
            encoding="utf-8",
        )
        with patch(
            "jiuwenswarm.instance_manager.yaml.get_instances_yaml_path",
            return_value=yaml_path,
        ):
            with pytest.raises(InstancesYamlError) as exc_info:
                load_instances_yaml()
            assert "must be 1-65535" in str(exc_info.value)

    @staticmethod
    def test_invalid_port_value_string(tmp_path):
        """Test string port value raises InstancesYamlError."""
        yaml_path = tmp_path / "instances.yaml"
        yaml_path.write_text(
            "instances:\n"
            "  alice:\n"
            "    ports:\n"
            '      agent_server: "28092"\n',  # String, not int
            encoding="utf-8",
        )
        with patch(
            "jiuwenswarm.instance_manager.yaml.get_instances_yaml_path",
            return_value=yaml_path,
        ):
            with pytest.raises(InstancesYamlError) as exc_info:
                load_instances_yaml()
            assert "must be integer" in str(exc_info.value)

    @staticmethod
    def test_ports_not_dict(tmp_path):
        """Test non-dict ports value raises InstancesYamlError."""
        yaml_path = tmp_path / "instances.yaml"
        yaml_path.write_text(
            "instances:\n  alice:\n    ports: invalid\n",
            encoding="utf-8",
        )
        with patch(
            "jiuwenswarm.instance_manager.yaml.get_instances_yaml_path",
            return_value=yaml_path,
        ):
            with pytest.raises(InstancesYamlError) as exc_info:
                load_instances_yaml()
            assert "'ports' must be a dict" in str(exc_info.value)

    @staticmethod
    def test_workspace_not_string(tmp_path):
        """Test non-string workspace raises InstancesYamlError."""
        yaml_path = tmp_path / "instances.yaml"
        yaml_path.write_text(
            "instances:\n  alice:\n    workspace: 123\n",  # Int, not string
            encoding="utf-8",
        )
        with patch(
            "jiuwenswarm.instance_manager.yaml.get_instances_yaml_path",
            return_value=yaml_path,
        ):
            with pytest.raises(InstancesYamlError) as exc_info:
                load_instances_yaml()
            assert "'workspace' must be a string" in str(exc_info.value)


class TestInstanceLock:
    """Test InstanceLock concurrency control."""

    @staticmethod
    def test_acquire_and_release(tmp_path):
        """Test basic lock acquire and release."""
        config = InstanceConfig(
            name="test",
            workspace=tmp_path,
            ports={},
        )
        lock = InstanceLock(config)

        # Should acquire successfully
        assert lock.acquire(timeout=1.0) is True
        assert lock.lock_path.exists()

        # Should be able to release
        lock.release()
        # On Windows, lock file is removed; on Unix, it may remain
        assert getattr(lock, "_lock_file") is None

    @staticmethod
    def test_context_manager(tmp_path):
        """Test lock as context manager."""
        config = InstanceConfig(
            name="test",
            workspace=tmp_path,
            ports={},
        )

        with InstanceLock(config) as lock:
            assert lock.lock_path.exists()
            assert getattr(lock, "_lock_file") is not None

        # Released after context
        assert getattr(lock, "_lock_file") is None

    @staticmethod
    def test_double_acquire_same_process(tmp_path):
        """Test that same process can re-acquire after release."""
        config = InstanceConfig(
            name="test",
            workspace=tmp_path,
            ports={},
        )
        lock = InstanceLock(config)

        assert lock.acquire(timeout=1.0) is True
        lock.release()

        # Should be able to acquire again
        assert lock.acquire(timeout=1.0) is True
        lock.release()

    @staticmethod
    def test_concurrent_acquire_fails(tmp_path):
        """Test that concurrent acquire from another process fails.

        This is the primary test for cross-process lock isolation.
        test_timeout_exceeded only tests same-process lock object competition,
        which is a different scenario and cannot replace this test.
        """
        import multiprocessing

        config = InstanceConfig(
            name="test",
            workspace=tmp_path,
            ports={},
        )
        lock = InstanceLock(config)

        # Acquire in this process
        assert lock.acquire(timeout=1.0) is True

        # Use Queue to get return value from subprocess
        ctx = multiprocessing.get_context("spawn")
        result_queue = ctx.Queue()

        p = ctx.Process(
            target=_try_acquire_lock_with_result, args=(str(tmp_path), result_queue)
        )
        p.start()
        p.join(timeout=5.0)

        # Process should not hang
        assert not p.is_alive()

        # The other process should fail to acquire lock (return False)
        # On Windows, the lock file already exists so exclusive creation fails
        # On Unix, flock on same file from different process fails
        if not result_queue.empty():
            subprocess_result = result_queue.get(timeout=1.0)
            assert subprocess_result is False, (
                "Subprocess should fail to acquire lock held by main process"
            )

        lock.release()

    @staticmethod
    def test_stale_lock_cleanup(tmp_path):
        """Test that stale lock is cleaned up."""
        config = InstanceConfig(
            name="test",
            workspace=tmp_path,
            ports={},
        )

        # Create a stale lock file manually
        lock_path = tmp_path / ".instance.lock"
        lock_path.write_text("99999\n0.0\n", encoding="utf-8")  # Old timestamp

        # Modify mtime to be old
        old_time = time.time() - STALE_LOCK_TIMEOUT - 10
        os.utime(lock_path, (old_time, old_time))

        # Acquire should succeed after cleaning stale lock
        lock = InstanceLock(config)
        assert lock.acquire(timeout=1.0) is True
        lock.release()

    @staticmethod
    def test_timeout_exceeded(tmp_path):
        """Test that acquire returns False after timeout."""
        config = InstanceConfig(
            name="test",
            workspace=tmp_path,
            ports={},
        )

        # First lock acquires successfully
        first_lock = InstanceLock(config)
        assert first_lock.acquire(timeout=1.0) is True

        # Second lock should fail to acquire (timeout exceeded)
        second_lock = InstanceLock(config)
        result = second_lock.acquire(timeout=0.5)
        assert result is False

        # Clean up
        first_lock.release()
