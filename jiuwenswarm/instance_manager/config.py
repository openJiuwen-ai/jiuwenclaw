# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Instance configuration, constants, name validation, and port management.

This module provides:
- Data classes: InstanceConfig, InstanceStatus, InstancesYamlError
- Constants: BASE_PORTS, PORT_TYPES, INSTANCE_NAME_PATTERN, RESERVED_NAMES
- Name validation: validate_instance_name, is_valid_instance_name
- Port management: compute_auto_port, calculate_instance_ports, is_port_available, etc.
"""

from __future__ import annotations

import logging
import os
import platform
import re
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)


class InstancesYamlError(Exception):
    """Custom exception for instances.yaml parsing/validation errors.

    Provides user-friendly error messages with actionable fix suggestions.
    """

    def __init__(self, message: str):
        # Prepend consistent header for CLI display
        super().__init__(f"[instances.yaml] {message}")


# Instance name validation rules
INSTANCE_NAME_PATTERN = re.compile(r'^[a-zA-Z0-9_-]+$')
RESERVED_NAMES = frozenset({'default', 'config', 'tmp', 'jiuwenswarm', 'all'})

# Base ports for default instance (index=0)
BASE_PORTS = {
    "agent_server": 18092,
    "web": 19000,
    "gateway": 19001,
    "frontend": 5173,
}

# Port types that must be unique across instances
PORT_TYPES = ("agent_server", "web", "gateway", "frontend")

# PID file name in workspace directory
PID_FILENAME = ".instance.pid"


@dataclass
class InstanceConfig:
    """Configuration for a named instance.

    Attributes:
        name: Instance name (unique identifier)
        workspace: Path to instance workspace directory
        ports: Dict of port assignments for each service type
    """
    name: str
    workspace: Path
    ports: Dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Expand and resolve workspace path."""
        self.workspace = Path(self.workspace).expanduser().resolve()

    def get_pid_file_path(self) -> Path:
        """Get the PID file path for this instance."""
        return self.workspace / PID_FILENAME

    def get_bootstrap_env_path(self) -> Path:
        """Get the bootstrap .env file path for this instance."""
        return self.workspace / ".env"


@dataclass
class InstanceStatus:
    """Runtime status of an instance.

    Attributes:
        name: Instance name
        running: Whether the instance is currently running
        pid: Process ID if running, None otherwise
        workspace: Path to instance workspace
        ports: Dict of port assignments
        started_at: Startup timestamp if running, None otherwise
    """
    name: str
    running: bool
    pid: Optional[int]
    workspace: Path
    ports: Dict[str, int]
    started_at: Optional[float] = None


def validate_instance_name(name: str) -> Optional[str]:
    """Validate instance name, return error message or None if valid.

    Rules:
    - Length: 1-64 characters
    - Pattern: letters, digits, underscore, hyphen only
    - No leading dot
    - Not a reserved name
    """
    if not name or not isinstance(name, str):
        return "Instance name must be a non-empty string"
    if len(name) < 1 or len(name) > 64:
        return "Instance name must be 1-64 characters"
    if not INSTANCE_NAME_PATTERN.match(name):
        return "Instance name must contain only letters, digits, underscore, hyphen"
    if name.startswith('.'):
        return "Instance name cannot start with dot"
    if name.lower() in RESERVED_NAMES:
        return f"Instance name '{name}' is reserved"
    return None


def is_valid_instance_name(name: str) -> bool:
    """Check if instance name is valid (returns bool)."""
    return validate_instance_name(name) is None


def compute_auto_port(port_type: str, index: int) -> int:
    """Compute auto-allocated port for an instance.

    Algorithm: base_port + index * 1000
    - index 0 reserved for default instance
    - Named instances start from index 1

    Args:
        port_type: Service type (agent_server, web, gateway, frontend)
        index: Instance index (0 for default, 1+ for named)

    Returns:
        Computed port number
    """
    base = BASE_PORTS.get(port_type, 10000)
    return base + index * 1000


def calculate_instance_ports(index: int) -> Dict[str, int]:
    """Calculate ports for an instance: base_port + index * 1000."""
    return {k: v + index * 1000 for k, v in BASE_PORTS.items()}


def is_port_available(host: str, port: int) -> bool:
    """Check if a port is available on the given host.

    Args:
        host: Host address to check
        port: Port number to check

    Returns:
        True if port is available, False if occupied
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        try:
            sock.connect((host, port))
            return False  # Port is occupied
        except OSError:
            return True  # Port is available


def check_port_conflicts(
    ports: Dict[str, int],
    host: str = "127.0.0.1",
    existing_ports: Optional[Sequence[int]] = None,
) -> List[int]:
    """Check for port conflicts.

    Args:
        ports: Dict of port assignments to check
        host: Host address to check
        existing_ports: Ports already used by other instances

    Returns:
        List of conflicting port numbers
    """
    conflicts: List[int] = []
    check_set = set(existing_ports or [])

    for port_type, port in ports.items():
        if port in check_set:
            conflicts.append(port)
            continue
        if not is_port_available(host, port):
            conflicts.append(port)

    return conflicts


def collect_all_ports(exclude_name: Optional[str] = None) -> List[int]:
    """Collect all ports used by all instances for conflict detection.

    Args:
        exclude_name: Instance name to exclude from collection

    Returns:
        List of port numbers used by other instances
    """
    # Import locally to avoid circular dependency
    from jiuwenswarm.instance_manager.yaml import load_instances_yaml

    ports: List[int] = []

    # Default instance ports
    if exclude_name != "default":
        for port_type in PORT_TYPES:
            ports.append(compute_auto_port(port_type, 0))

    # Named instance ports from instances.yaml
    try:
        data = load_instances_yaml()
        instances = data.get("instances", {})
        for name, inst_data in instances.items():
            if name == exclude_name:
                continue
            index = list(instances.keys()).index(name) + 1
            ports_config = (inst_data or {}).get("ports", {})
            for port_type in PORT_TYPES:
                if port_type in ports_config:
                    ports.append(ports_config[port_type])
                else:
                    ports.append(compute_auto_port(port_type, index))
    except Exception as exc:
        logger.debug(
            "Failed to load instance ports from yaml (ignored): %s", exc
        )

    return ports


def _get_system_executable(name: str) -> str:
    """Get absolute path for system executable.

    Uses System32 path on Windows for common system utilities.
    Falls back to shutil.which() for cross-platform resolution.

    Args:
        name: Executable name (e.g., 'tasklist', 'netstat', 'lsof')

    Returns:
        Absolute path to executable, or name if not found
    """
    import shutil

    system = platform.system().lower()
    if system == "windows":
        # Windows system executables are in System32
        system32 = os.path.join(
            os.environ.get("SystemRoot", "C:\\Windows"), "System32"
        )
        exe_path = os.path.join(system32, f"{name}.exe")
        if os.path.exists(exe_path):
            return exe_path
    # Fallback to shutil.which for cross-platform resolution
    resolved = shutil.which(name)
    return resolved or name