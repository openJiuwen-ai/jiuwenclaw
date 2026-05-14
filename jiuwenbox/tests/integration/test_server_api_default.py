# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Integration tests for box-server API endpoints."""

import copy
import logging
import socket
import textwrap
import time
from pathlib import Path

import httpx
import pytest
import yaml

from jiuwenbox.models.policy import SecurityPolicy
from jiuwenbox.supervisor import network as network_module
from jiuwenbox.supervisor.bwrap import BwrapConfig

_DEFAULT_POLICY = yaml.safe_load(
    (Path(__file__).resolve().parents[2] / "configs" / "default-policy.yaml").read_text(encoding="utf-8")
)
_DEFAULT_FILESYSTEM_POLICY = _DEFAULT_POLICY["filesystem_policy"]

SYSTEM_BIND_MOUNTS = copy.deepcopy(_DEFAULT_FILESYSTEM_POLICY["bind_mounts"])
DEVICE_MOUNTS = copy.deepcopy(_DEFAULT_FILESYSTEM_POLICY["device"])
DEFAULT_FILES = copy.deepcopy(_DEFAULT_FILESYSTEM_POLICY.get("files", []))
SANDBOX_WORKSPACE = "/root/.jiuwenbox"
DIRECTORIES = copy.deepcopy(_DEFAULT_FILESYSTEM_POLICY["directories"])

logger = logging.getLogger(__name__)


class SandboxTrackingClient:
    """Track sandboxes created during a test and clean them up afterwards."""

    def __init__(self, client):
        self._client = client
        self._created_ids: list[str] = []

    def __getattr__(self, name: str):
        return getattr(self._client, name)

    def post(self, url, *args, **kwargs):
        response = self._client.post(url, *args, **kwargs)
        if str(url).rstrip("/") == "/api/v1/sandboxes" and response.status_code == 201:
            try:
                sandbox_id = response.json().get("id")
            except Exception:
                sandbox_id = None
            if sandbox_id:
                self._created_ids.append(sandbox_id)
        return response

    def delete(self, url, *args, **kwargs):
        response = self._client.delete(url, *args, **kwargs)
        sandbox_id = self._sandbox_id_from_delete_url(url)
        if sandbox_id and response.status_code in (200, 202, 204, 404):
            self._created_ids = [item for item in self._created_ids if item != sandbox_id]
        return response

    def cleanup_sandboxes(self) -> None:
        for sandbox_id in reversed(self._created_ids):
            try:
                self._client.delete(f"/api/v1/sandboxes/{sandbox_id}")
            except Exception as exc:
                logger.warning("Failed to cleanup sandbox %s: %s", sandbox_id, exc)
        self._created_ids.clear()

    @staticmethod
    def _sandbox_id_from_delete_url(url) -> str | None:
        path = str(url).split("?", 1)[0].rstrip("/")
        prefix = "/api/v1/sandboxes/"
        if not path.startswith(prefix):
            return None
        suffix = path[len(prefix):]
        if "/" in suffix:
            return None
        return suffix or None


def _normalize_endpoint(endpoint: str) -> str:
    return endpoint if "://" in endpoint else f"http://{endpoint}"


def _sandbox_health_url(server_endpoint: str) -> str:
    return f"{_normalize_endpoint(server_endpoint).rstrip('/')}/health"


def _host_network_ip_from_sandbox(client, sandbox_id: str) -> str:
    script = textwrap.dedent(
        """
        import re
        import subprocess
        import sys

        def run_ip(args):
            try:
                return subprocess.check_output(
                    ["ip", *args],
                    stderr=subprocess.DEVNULL,
                    text=True,
                )
            except (FileNotFoundError, subprocess.CalledProcessError):
                return ""

        route = run_ip(["-4", "route", "get", "1.1.1.1"])
        match = re.search(r"\\bsrc\\s+(\\d+\\.\\d+\\.\\d+\\.\\d+)", route)
        if match and not match.group(1).startswith("127."):
            print(match.group(1))
            sys.exit(0)

        addresses = run_ip(["-4", "-o", "addr", "show", "scope", "global"])
        for address in re.findall(r"\\binet\\s+(\\d+\\.\\d+\\.\\d+\\.\\d+)/", addresses):
            if not address.startswith("127."):
                print(address)
                sys.exit(0)

        print("failed to resolve host-network IPv4 address from sandbox", file=sys.stderr)
        sys.exit(1)
        """
    ).strip()
    response = client.post(f"/api/v1/sandboxes/{sandbox_id}/exec", json={
        "command": ["python3", "-c", script],
        "timeout_seconds": 5,
    })
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["exit_code"] == 0, data
    return data["stdout"].strip()


def _unused_host_network_tcp_port_from_sandbox(client, sandbox_id: str) -> int:
    script = textwrap.dedent(
        """
        import socket

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("0.0.0.0", 0))
            print(sock.getsockname()[1])
        """
    ).strip()
    response = client.post(f"/api/v1/sandboxes/{sandbox_id}/exec", json={
        "command": ["python3", "-c", script],
        "timeout_seconds": 5,
    })
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["exit_code"] == 0, data
    return int(data["stdout"].strip())


def _capability_check_script(cap_bit: int) -> str:
    return textwrap.dedent(
        f"""
        cap_eff = 0
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("CapEff:"):
                    cap_eff = int(line.split()[1], 16)
                    break
        print("yes" if cap_eff & (1 << {cap_bit}) else "no")
        """
    ).strip()


def _loopback_ingress_script(expect_success: bool) -> str:
    connect_block = textwrap.dedent(
        """
        sock = socket.create_connection(("127.0.0.1", port), timeout=1)
        conn, _ = srv.accept()
        conn.sendall(b"ingress-ok")
        conn.close()
        print(sock.recv(64).decode())
        sock.close()
        """
    ).strip()
    if not expect_success:
        connect_block = textwrap.dedent(
            """
            try:
                sock = socket.create_connection(("127.0.0.1", port), timeout=1)
                conn, _ = srv.accept()
                conn.sendall(b"ingress-ok")
                conn.close()
                print(sock.recv(64).decode())
                sock.close()
                print("unexpected-success")
                sys.exit(0)
            except Exception as exc:
                print(type(exc).__name__)
                sys.exit(7)
            """
        ).strip()

    return "\n".join([
        "import socket",
        "import sys",
        "",
        "port = int(sys.argv[1])",
        "",
        "srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)",
        "srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)",
        'srv.bind(("127.0.0.1", port))',
        "srv.listen(1)",
        connect_block,
        "srv.close()",
        "",
    ])


def _has_directory(directories: list, path: str) -> bool:
    for directory in directories:
        if isinstance(directory, str) and directory == path:
            return True
        if isinstance(directory, dict) and directory.get("path") == path:
            return True
    return False


def _has_bind_mount(bind_mounts: list, sandbox_path: str) -> bool:
    return any(mount.get("sandbox_path") == sandbox_path for mount in bind_mounts)


def _with_runtime_support(policy: dict) -> dict:
    runtime_policy = copy.deepcopy(policy)
    filesystem_policy = runtime_policy.setdefault("filesystem_policy", {})
    bind_mounts = filesystem_policy.setdefault("bind_mounts", [])
    for mount in SYSTEM_BIND_MOUNTS:
        if mount not in bind_mounts:
            bind_mounts.append(mount.copy())

    directories = filesystem_policy.setdefault("directories", [])
    for directory_entry in DIRECTORIES:
        directories.append(directory_entry.copy())

    return runtime_policy


def _has_mount(args: list[str], flag: str, source: str, target: str) -> bool:
    for index, value in enumerate(args[:-2]):
        if value == flag and args[index + 1] == source and args[index + 2] == target:
            return True
    return False


def _has_arg_pair(args: list[str], flag: str, value: str) -> bool:
    for index, item in enumerate(args[:-1]):
        if item == flag and args[index + 1] == value:
            return True
    return False


@pytest.fixture
def client(server_endpoint):
    with httpx.Client(base_url=_normalize_endpoint(server_endpoint), timeout=30.0) as external:
        tracking = SandboxTrackingClient(external)
        try:
            yield tracking
        finally:
            tracking.cleanup_sandboxes()


@pytest.fixture
def create_sandbox_with_policy(client):
    def factory(
        *,
        name_prefix: str,
        policy: dict,
        policy_mode: str = "override",
    ) -> dict:
        response = client.post("/api/v1/sandboxes", json={
            "policy_mode": policy_mode,
            "policy": _with_runtime_support(policy),
        })
        assert response.status_code == 201, response.text
        sandbox = response.json()
        assert sandbox["phase"] == "ready", sandbox
        return sandbox

    return factory


class TestHealthEndpoint:
    @staticmethod
    def test_health(client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "version" in data
        assert "landlock_supported" in data
        assert "sandboxes_active" in data


class TestSandboxCRUD:
    @staticmethod
    def test_list_sandboxes_empty(client):
        resp = client.get("/api/v1/sandboxes")
        assert resp.status_code == 200
        assert resp.json() == []

    @staticmethod
    def test_create_sandbox(client):
        resp = client.post("/api/v1/sandboxes", json={})
        assert resp.status_code == 201
        data = resp.json()
        assert "id" in data
        assert "name" not in data
        assert "command" not in data
        assert "workdir" not in data
        assert data["phase"] in ("provisioning", "ready", "error")

    @staticmethod
    def test_list_sandboxes_after_create(client):
        create_resp = client.post("/api/v1/sandboxes", json={})
        sandbox_id = create_resp.json()["id"]
        resp = client.get("/api/v1/sandboxes")
        assert resp.status_code == 200
        data = resp.json()
        assert any(item["id"] == sandbox_id for item in data)
        assert len(data) == 1

    @staticmethod
    def test_get_sandbox(client):
        create_resp = client.post("/api/v1/sandboxes", json={})
        sandbox_id = create_resp.json()["id"]

        resp = client.get(f"/api/v1/sandboxes/{sandbox_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == sandbox_id
        assert "name" not in data

    @staticmethod
    def test_get_nonexistent_sandbox(client):
        resp = client.get("/api/v1/sandboxes/nonexistent")
        assert resp.status_code == 404

    @staticmethod
    def test_delete_sandbox(client):
        create_resp = client.post("/api/v1/sandboxes", json={})
        sandbox_id = create_resp.json()["id"]

        resp = client.delete(f"/api/v1/sandboxes/{sandbox_id}")
        assert resp.status_code == 204

        resp = client.get(f"/api/v1/sandboxes/{sandbox_id}")
        assert resp.status_code == 404


class TestSandboxLifecycle:
    @staticmethod
    def test_start_stopped_sandbox(client):
        create_resp = client.post("/api/v1/sandboxes", json={})
        sandbox_id = create_resp.json()["id"]

        stop_resp = client.post(f"/api/v1/sandboxes/{sandbox_id}/stop")
        assert stop_resp.status_code == 200
        assert stop_resp.json()["phase"] == "stopped"

        start_resp = client.post(f"/api/v1/sandboxes/{sandbox_id}/start")
        assert start_resp.status_code == 200
        assert start_resp.json()["phase"] == "ready", start_resp.json()

    @staticmethod
    def test_stop_sandbox(client):
        create_resp = client.post("/api/v1/sandboxes", json={})
        sandbox_id = create_resp.json()["id"]

        resp = client.post(f"/api/v1/sandboxes/{sandbox_id}/stop")
        assert resp.status_code == 200
        assert resp.json()["phase"] == "stopped"

    @staticmethod
    def test_restart_sandbox(client):
        create_resp = client.post("/api/v1/sandboxes", json={})
        sandbox_id = create_resp.json()["id"]

        resp = client.post(f"/api/v1/sandboxes/{sandbox_id}/restart")
        assert resp.status_code == 200
        assert resp.json()["phase"] == "ready"

    @staticmethod
    def test_sandbox_process_cannot_target_sandbox_daemon(client):
        # The long-running daemon shares the sandbox PID namespace with
        # user-spawned children (the daemon is PID 1 in that namespace).
        # The kernel protects PID 1 of a namespace from in-namespace senders
        # for any signal that PID 1 has not registered a handler for; the
        # daemon registers no handlers, so SIGTERM/SIGINT/SIGKILL from user
        # code must all be silently dropped and the daemon must continue to
        # service requests.
        create_resp = client.post("/api/v1/sandboxes", json={})
        assert create_resp.status_code == 201
        sandbox = create_resp.json()
        assert sandbox["phase"] == "ready", sandbox
        sandbox_id = sandbox["id"]

        kill_resp = client.post(f"/api/v1/sandboxes/{sandbox_id}/exec", json={
            "command": [
                "python3",
                "-c",
                textwrap.dedent(
                    """
                    import os
                    import signal
                    import sys
                    import time

                    targets = [signal.SIGTERM, signal.SIGINT, signal.SIGKILL,
                               signal.SIGHUP, signal.SIGUSR1, signal.SIGUSR2]
                    delivered = []
                    for sig in targets:
                        try:
                            os.kill(1, sig)
                        except ProcessLookupError:
                            delivered.append(f"missing:{sig}")
                        except PermissionError:
                            continue
                        except OSError as exc:
                            delivered.append(f"error:{sig}:{exc.errno}")
                    time.sleep(0.5)

                    try:
                        os.kill(1, 0)
                    except ProcessLookupError:
                        print("daemon-killed")
                        sys.exit(1)
                    except PermissionError:
                        pass

                    if delivered:
                        print(f"unexpected:{','.join(delivered)}")
                        sys.exit(2)
                    print("daemon-survived")
                    """
                ).strip(),
            ],
            "timeout_seconds": 10,
        })
        assert kill_resp.status_code == 200
        kill_data = kill_resp.json()
        assert kill_data["exit_code"] == 0, kill_data
        assert kill_data["stdout"].strip() == "daemon-survived"

        status_resp = client.get(f"/api/v1/sandboxes/{sandbox_id}")
        assert status_resp.status_code == 200
        assert status_resp.json()["phase"] == "ready", status_resp.json()

        exec_resp = client.post(f"/api/v1/sandboxes/{sandbox_id}/exec", json={
            "command": ["echo", "daemon-alive"],
            "timeout_seconds": 5,
        })
        assert exec_resp.status_code == 200
        exec_data = exec_resp.json()
        assert exec_data["exit_code"] == 0, exec_data
        assert exec_data["stdout"].strip() == "daemon-alive"

    @staticmethod
    def test_sandbox_process_cannot_inspect_sandbox_daemon_memory(client):
        # PID 1 of the sandbox PID namespace is the long-running daemon.
        # Reading the daemon's address space (``/proc/1/mem``) requires
        # CAP_SYS_PTRACE (stripped by the default policy) and
        # ``ptrace(PTRACE_ATTACH, 1)`` is blocked by seccomp. Together
        # these prevent a sandboxed process from extracting secrets from
        # or hijacking the long-running daemon.
        create_resp = client.post("/api/v1/sandboxes", json={})
        assert create_resp.status_code == 201
        sandbox = create_resp.json()
        assert sandbox["phase"] == "ready", sandbox
        sandbox_id = sandbox["id"]

        script = textwrap.dedent(
            """
            import ctypes
            import os
            import sys

            try:
                fd = os.open('/proc/1/mem', os.O_RDONLY)
            except PermissionError:
                pass
            except FileNotFoundError:
                pass
            else:
                try:
                    os.read(fd, 16)
                except PermissionError:
                    pass
                except OSError:
                    pass
                else:
                    os.close(fd)
                    print('daemon-memory-readable')
                    sys.exit(2)
                os.close(fd)

            try:
                libc = ctypes.CDLL('libc.so.6', use_errno=True)
                PTRACE_ATTACH = 16
                rc = libc.ptrace(PTRACE_ATTACH, 1, 0, 0)
                if rc == 0:
                    print('daemon-ptraceable')
                    sys.exit(3)
            except OSError:
                pass

            print('daemon-protected')
            """
        ).strip()
        response = client.post(f"/api/v1/sandboxes/{sandbox_id}/exec", json={
            "command": ["python3", "-c", script],
            "timeout_seconds": 5,
        })
        assert response.status_code == 200
        data = response.json()
        assert data["exit_code"] == 0, data
        assert data["stdout"].strip() == "daemon-protected"

    @staticmethod
    def test_get_logs(client):
        create_resp = client.post("/api/v1/sandboxes", json={})
        sandbox_id = create_resp.json()["id"]

        resp = client.get(f"/api/v1/sandboxes/{sandbox_id}/logs")
        assert resp.status_code == 200


class TestPolicyAPI:
    @staticmethod
    def test_get_sandbox_policy(client):
        create_resp = client.post("/api/v1/sandboxes", json={})
        sandbox_id = create_resp.json()["id"]

        resp = client.get(f"/api/v1/policies/{sandbox_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["version"] == 1
        assert data["name"] == "server-default"
        assert data["environment"] == {}
        assert "sandbox_workspace" not in data
        assert "resources" not in data
        assert data["filesystem_policy"]["directories"] == [{'path': '/home', 'permissions': '0777'},
                                                            {'path': '/tmp', 'permissions': '1777'}]
        assert data["filesystem_policy"]["read_only"] == [
            "/",
            "/bin",
            "/sbin",
            "/usr",
            "/lib",
            "/lib64",
            "/etc",
            "/opt",
        ]
        assert data["filesystem_policy"]["read_write"] == ["/home", "/tmp"]
        assert data["filesystem_policy"]["bind_mounts"] == SYSTEM_BIND_MOUNTS
        assert data["filesystem_policy"]["device"] == DEVICE_MOUNTS
        assert data["filesystem_policy"]["files"] == DEFAULT_FILES
        assert data["process"]["run_as_user"] == "sandbox"
        assert data["process"]["run_as_group"] == "sandbox"
        assert data["namespace"] == {
            "user": True,
            "pid": True,
            "ipc": True,
            "cgroup": True,
            "uts": True,
        }
        assert data["capabilities"] == {"add": [], "drop": []}
        assert data["landlock"]["compatibility"] == "best_effort"
        assert data["network"]["mode"] == "host"
        assert data["network"]["egress"]["allowed_domains"] == ["baidu.com"]
        assert data["network"]["egress"]["allowed_ips"] == ["127.0.0.1/32", "::1/128"]
        assert data["network"]["egress"]["blocked_ips"] == ["169.254.169.254/32"]
        assert data["network"]["egress"]["blocked_ports"] == [22]
        assert data["network"]["egress"]["default"] == "allow"
        assert data["network"]["egress"]["blocked_domains"] == ["ip.me"]
        assert data["network"]["egress"]["allowed_ports"] == [443, 80]
        assert data["network"]["ingress"]["default"] == "allow"
        assert data["network"]["ingress"]["allowed_domains"] == ["localhost"]
        assert data["network"]["ingress"]["allowed_ips"] == ["127.0.0.1/32", "::1/128"]
        assert data["network"]["ingress"]["blocked_ips"] == []
        assert data["network"]["ingress"]["allowed_ports"] == [8080]
        assert data["network"]["ingress"]["blocked_ports"] == []
        assert "profile" not in data["syscall"]
        assert "blocked" not in data["syscall"]
        assert "mount" in data["syscall"]["x86_64"]["blocked"]
        assert "kexec_file_load" in data["syscall"]["x86_64"]["blocked"]
        assert "mount" in data["syscall"]["arm64"]["blocked"]
        assert "kexec_file_load" in data["syscall"]["arm64"]["blocked"]

    @staticmethod
    def test_append_policy_merges_with_server_default(client):
        create_resp = client.post("/api/v1/sandboxes", json={
            "policy_mode": "append",
            "policy": {
                "name": "appended-policy",
                "environment": {
                    "JIUWENBOX_APPEND_ENV": "append-ok",
                },
                "filesystem_policy": {
                    "directories": [{"path": "/tmp/appended-dir", "permissions": "0700"}],
                    "read_only": ["/var/log"],
                    "read_write": ["/var/tmp"],
                    "bind_mounts": [{
                        "host_path": "/tmp",
                        "sandbox_path": "/tmp",
                        "mode": "rw",
                    }],
                },
                "network": {
                    "egress": {
                        "allowed_domains": ["extra.example.com"],
                        "allowed_ips": ["203.0.113.10/32"],
                    },
                    "ingress": {
                        "allowed_ips": ["10.0.0.0/8"],
                        "allowed_ports": [9090],
                    },
                },
                "process": {
                    "run_as_user": "root",
                    "run_as_group": "root",
                },
                "namespace": {
                    "pid": False,
                    "uts": False,
                },
                "capabilities": {
                    "add": ["CAP_NET_RAW"],
                    "drop": [],
                },
                "landlock": {
                    "compatibility": "disabled",
                },
                "syscall": {
                    "x86_64": {"blocked": ["getpid"]},
                    "arm64": {"blocked": ["getpid"]},
                },
            },
        })
        sandbox_id = create_resp.json()["id"]

        resp = client.get(f"/api/v1/policies/{sandbox_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "appended-policy"
        assert data["environment"] == {"JIUWENBOX_APPEND_ENV": "append-ok"}
        assert "sandbox_workspace" not in data
        assert data["network"]["egress"]["allowed_domains"] == [
            "baidu.com",
            "extra.example.com",
        ]
        assert data["network"]["egress"]["allowed_ips"] == [
            "127.0.0.1/32",
            "::1/128",
            "203.0.113.10/32",
        ]
        assert data["network"]["egress"]["blocked_ips"] == ["169.254.169.254/32"]
        assert data["network"]["egress"]["blocked_ports"] == [22]
        assert data["network"]["ingress"]["allowed_domains"] == ["localhost"]
        assert data["network"]["ingress"]["allowed_ips"] == [
            "127.0.0.1/32",
            "::1/128",
            "10.0.0.0/8",
        ]
        assert data["network"]["ingress"]["allowed_ports"] == [8080, 9090]
        assert data["filesystem_policy"]["read_only"] == [
            "/",
            "/bin",
            "/sbin",
            "/usr",
            "/lib",
            "/lib64",
            "/etc",
            "/opt",
            "/var/log",
        ]
        assert data["filesystem_policy"]["read_write"] == ["/home", "/tmp", "/var/tmp"]
        assert data["filesystem_policy"]["directories"] == [{'path': '/home', 'permissions': '0777'},
                                                            {'path': '/tmp', 'permissions': '1777'},
                                                            {"path": "/tmp/appended-dir", "permissions": "0700"}]
        assert data["filesystem_policy"]["bind_mounts"] == SYSTEM_BIND_MOUNTS + [{
            "host_path": "/tmp",
            "sandbox_path": "/tmp",
            "mode": "rw",
        }]
        assert data["filesystem_policy"]["device"] == DEVICE_MOUNTS
        assert data["filesystem_policy"]["files"] == DEFAULT_FILES
        assert data["process"]["run_as_user"] == "root"
        assert data["process"]["run_as_group"] == "root"
        assert data["namespace"] == {
            "user": True,
            "pid": False,
            "ipc": True,
            "cgroup": True,
            "uts": False,
        }
        assert data["capabilities"]["add"] == ["CAP_NET_RAW"]
        assert data["capabilities"]["drop"] == []
        assert data["landlock"]["compatibility"] == "disabled"
        assert "getpid" in data["syscall"]["x86_64"]["blocked"]
        assert "mount" in data["syscall"]["x86_64"]["blocked"]
        assert "getpid" in data["syscall"]["arm64"]["blocked"]
        assert "mount" in data["syscall"]["arm64"]["blocked"]

    @staticmethod
    def test_override_policy_replaces_server_default(client):
        create_resp = client.post("/api/v1/sandboxes", json={
            "policy_mode": "override",
            "policy": {
                "name": "override-policy",
                "environment": {
                    "JIUWENBOX_OVERRIDE_ENV": "override-ok",
                },
                "filesystem_policy": {
                    "directories": [{
                        "path": "/tmp/override-dir",
                        "permissions": "0700",
                    }],
                    "read_only": ["/usr"],
                    "read_write": ["/var/tmp"],
                    "bind_mounts": SYSTEM_BIND_MOUNTS,
                },
                "network": {
                    "mode": "host",
                    "egress": {
                        "default": "deny",
                        "allowed_domains": ["override.example.com"],
                        "allowed_ips": ["198.51.100.10/32"],
                        "blocked_ips": ["198.51.100.11/32"],
                        "allowed_ports": [80],
                        "blocked_ports": [25],
                    },
                    "ingress": {
                        "default": "allow",
                        "allowed_ips": ["10.0.0.0/8"],
                        "blocked_ips": ["10.0.5.0/24"],
                        "allowed_ports": [9090],
                        "blocked_ports": [22],
                    },
                },
                "process": {
                    "run_as_user": "root",
                    "run_as_group": "root",
                },
                "namespace": {
                    "user": True,
                    "pid": False,
                    "ipc": False,
                    "cgroup": False,
                    "uts": False,
                },
                "capabilities": {
                    "add": ["CAP_NET_RAW"],
                    "drop": [],
                },
                "landlock": {
                    "compatibility": "disabled",
                },
                "syscall": {
                    "x86_64": {"blocked": ["getppid"]},
                    "arm64": {"blocked": ["getppid"]},
                },
            },
        })
        sandbox_id = create_resp.json()["id"]

        resp = client.get(f"/api/v1/policies/{sandbox_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "override-policy"
        assert data["environment"] == {"JIUWENBOX_OVERRIDE_ENV": "override-ok"}
        assert "sandbox_workspace" not in data
        assert data["network"]["mode"] == "host"
        assert data["network"]["egress"]["allowed_domains"] == ["override.example.com"]
        assert data["network"]["egress"]["allowed_ips"] == ["198.51.100.10/32"]
        assert data["network"]["egress"]["blocked_ips"] == ["198.51.100.11/32"]
        assert data["network"]["egress"]["blocked_ports"] == [25]
        assert data["network"]["ingress"]["default"] == "allow"
        assert data["network"]["ingress"]["allowed_ips"] == ["10.0.0.0/8"]
        assert data["network"]["ingress"]["blocked_ips"] == ["10.0.5.0/24"]
        assert data["network"]["ingress"]["allowed_ports"] == [9090]
        assert data["network"]["ingress"]["blocked_ports"] == [22]
        assert data["filesystem_policy"]["read_only"] == ["/usr"]
        assert data["filesystem_policy"]["read_write"] == ["/var/tmp"]
        assert data["filesystem_policy"]["bind_mounts"] == SYSTEM_BIND_MOUNTS
        assert data["filesystem_policy"]["device"] == []
        assert data["filesystem_policy"]["files"] == []
        assert data["filesystem_policy"]["directories"] == [{
            "path": "/tmp/override-dir",
            "permissions": "0700",
        }]
        assert data["process"]["run_as_user"] == "root"
        assert data["process"]["run_as_group"] == "root"
        assert data["namespace"] == {
            "user": True,
            "pid": False,
            "ipc": False,
            "cgroup": False,
            "uts": False,
        }
        assert data["capabilities"] == {"add": ["CAP_NET_RAW"], "drop": []}
        assert data["landlock"]["compatibility"] == "disabled"
        assert data["syscall"]["x86_64"]["blocked"] == ["getppid"]
        assert data["syscall"]["arm64"]["blocked"] == ["getppid"]

    @staticmethod
    def test_get_nonexistent_policy(client):
        resp = client.get("/api/v1/policies/nonexistent")
        assert resp.status_code == 404

    @staticmethod
    def test_create_sandbox_rejects_direct_sandbox_bind_mount(client):
        resp = client.post("/api/v1/sandboxes", json={
            "policy": {
                "name": "bad-sandbox-mount-policy",
                "filesystem_policy": {
                    "bind_mounts": [{
                        "host_path": f"{SANDBOX_WORKSPACE}/manual",
                        "sandbox_path": "/tmp/manual",
                        "mode": "rw",
                    }],
                },
                "network": {
                    "mode": "host",
                },
            },
        })

        assert resp.status_code == 400
        assert SANDBOX_WORKSPACE in resp.json()["error"]

    @staticmethod
    def test_create_sandbox_rejects_direct_sandbox_device_mount(client):
        resp = client.post("/api/v1/sandboxes", json={
            "policy": {
                "name": "bad-sandbox-device-policy",
                "filesystem_policy": {
                    "device": [{
                        "host_path": f"{SANDBOX_WORKSPACE}/manual-device",
                        "sandbox_path": "/dev/manual-device",
                    }],
                },
                "network": {
                    "mode": "host",
                },
            },
        })

        assert resp.status_code == 400
        assert SANDBOX_WORKSPACE in resp.json()["error"]

    @staticmethod
    def test_create_sandbox_rejects_direct_sandbox_path(client):
        resp = client.post("/api/v1/sandboxes", json={
            "policy": {
                "name": "bad-sandbox-path-policy",
                "filesystem_policy": {
                    "read_write": [f"{SANDBOX_WORKSPACE}/manual"],
                },
                "network": {
                    "mode": "host",
                },
            },
        })

        assert resp.status_code == 400
        assert SANDBOX_WORKSPACE in resp.json()["error"]

    @staticmethod
    def test_create_sandbox_rejects_direct_sandbox_directory(client):
        resp = client.post("/api/v1/sandboxes", json={
            "policy": {
                "name": "bad-sandbox-dir-policy",
                "filesystem_policy": {
                    "directories": [{
                        "path": f"{SANDBOX_WORKSPACE}/manual",
                        "permissions": "0700",
                    }],
                },
                "network": {
                    "mode": "host",
                },
            },
        })

        assert resp.status_code == 400
        assert SANDBOX_WORKSPACE in resp.json()["error"]

    @staticmethod
    def test_create_sandbox_rejects_direct_sandbox_file(client):
        resp = client.post("/api/v1/sandboxes", json={
            "policy": {
                "name": "bad-sandbox-file-policy",
                "filesystem_policy": {
                    "files": [{
                        "path": f"{SANDBOX_WORKSPACE}/manual-file",
                        "permissions": "0600",
                    }],
                },
                "network": {
                    "mode": "host",
                },
            },
        })

        assert resp.status_code == 400
        assert SANDBOX_WORKSPACE in resp.json()["error"]


class TestPolicyEnforcement:
    @staticmethod
    def test_filesystem_read_write_rule_allows_upload_and_download(
        client,
        create_sandbox_with_policy,
    ):
        sandbox = create_sandbox_with_policy(
            name_prefix="fs-rw",
            policy={
                "name": "fs-rw-policy",
                "filesystem_policy": {
                    "read_only": ["/usr", "/lib", "/lib64", "/etc", "/opt"],
                    "read_write": ["/tmp"],
                },
                "network": {
                    "mode": "host",
                    "egress": {"default": "allow"},
                },
            },
        )

        upload = client.post(
            f"/api/v1/sandboxes/{sandbox['id']}/upload",
            params={"sandbox_path": "/tmp/policy-ok.txt"},
            files={"file": ("policy-ok.txt", b"hello-policy", "text/plain")},
        )
        assert upload.status_code == 204

        download = client.get(
            f"/api/v1/sandboxes/{sandbox['id']}/download",
            params={"sandbox_path": "/tmp/policy-ok.txt"},
        )
        assert download.status_code == 200
        assert download.content == b"hello-policy"

    @staticmethod
    def test_filesystem_read_only_rule_rejects_upload(
        client,
        create_sandbox_with_policy,
    ):
        sandbox = create_sandbox_with_policy(
            name_prefix="fs-ro",
            policy={
                "name": "fs-ro-policy",
                "filesystem_policy": {
                    "read_only": ["/usr", "/lib", "/lib64", "/etc", "/opt"],
                    "read_write": ["/tmp"],
                },
                "network": {
                    "mode": "host",
                    "egress": {"default": "allow"},
                },
            },
        )

        upload = client.post(
            f"/api/v1/sandboxes/{sandbox['id']}/upload",
            params={"sandbox_path": "/etc/policy-denied.txt"},
            files={"file": ("policy-denied.txt", b"nope", "text/plain")},
        )
        assert upload.status_code == 409

    @staticmethod
    def test_filesystem_directories_rule_creates_directory(
        client,
        create_sandbox_with_policy,
    ):
        sandbox = create_sandbox_with_policy(
            name_prefix="fs-dir",
            policy={
                "name": "fs-dir-policy",
                "filesystem_policy": {
                    "directories": [{
                        "path": "/policy-created",
                        "permissions": 700,
                    }],
                    "read_only": ["/usr", "/lib", "/lib64", "/etc", "/opt"],
                    "read_write": ["/tmp"],
                },
                "landlock": {
                    "compatibility": "disabled",
                },
                "network": {
                    "mode": "host",
                },
            },
        )

        response = client.post(f"/api/v1/sandboxes/{sandbox['id']}/exec", json={
            "command": [
                "python",
                "-c",
                (
                    "import os, stat; "
                    "from pathlib import Path; "
                    "path = Path('/policy-created'); "
                    "print(path.is_dir()); "
                    "print(oct(stat.S_IMODE(os.stat(path).st_mode)))"
                ),
            ],
            "timeout_seconds": 5,
        })
        assert response.status_code == 200
        data = response.json()
        assert data["exit_code"] == 0, data
        assert data["stdout"].splitlines() == ["True", "0o700"]

    @staticmethod
    def test_filesystem_directories_rule_creates_nested_directory(
        client,
        create_sandbox_with_policy,
    ):
        sandbox = create_sandbox_with_policy(
            name_prefix="fs-nested-dir",
            policy={
                "name": "fs-nested-dir-policy",
                "filesystem_policy": {
                    "directories": [{
                        "path": "/policy-created/level1/level2",
                        "permissions": "0711",
                    }],
                    "read_only": ["/usr", "/lib", "/lib64", "/etc", "/opt"],
                    "read_write": ["/tmp"],
                },
                "landlock": {
                    "compatibility": "disabled",
                },
                "network": {
                    "mode": "host",
                },
            },
        )

        response = client.post(f"/api/v1/sandboxes/{sandbox['id']}/exec", json={
            "command": [
                "python3",
                "-c",
                (
                    "import os, stat; "
                    "from pathlib import Path; "
                    "parent = Path('/policy-created/level1'); "
                    "path = parent / 'level2'; "
                    "print(Path('/policy-created').is_dir()); "
                    "print(parent.is_dir()); "
                    "print(path.is_dir()); "
                    "print(oct(stat.S_IMODE(os.stat(path).st_mode)))"
                ),
            ],
            "timeout_seconds": 5,
        })
        assert response.status_code == 200
        data = response.json()
        assert data["exit_code"] == 0, data
        assert data["stdout"].splitlines() == ["True", "True", "True", "0o711"]

    @staticmethod
    def test_filesystem_files_rule_creates_nested_empty_file(
        client,
        create_sandbox_with_policy,
    ):
        sandbox = create_sandbox_with_policy(
            name_prefix="fs-file",
            policy={
                "name": "fs-file-policy",
                "filesystem_policy": {
                    "files": [{
                        "path": "/policy-created/level1/marker.txt",
                        "permissions": "0640",
                    }],
                    "read_only": ["/usr", "/lib", "/lib64", "/etc", "/opt"],
                    "read_write": ["/tmp"],
                },
                "landlock": {
                    "compatibility": "disabled",
                },
                "network": {
                    "mode": "host",
                },
            },
        )

        response = client.post(f"/api/v1/sandboxes/{sandbox['id']}/exec", json={
            "command": [
                "python3",
                "-c",
                (
                    "import os, stat; "
                    "from pathlib import Path; "
                    "parent = Path('/policy-created/level1'); "
                    "path = parent / 'marker.txt'; "
                    "print(Path('/policy-created').is_dir()); "
                    "print(parent.is_dir()); "
                    "print(path.is_file()); "
                    "print(path.read_text()); "
                    "print(oct(stat.S_IMODE(os.stat(path).st_mode)))"
                ),
            ],
            "timeout_seconds": 5,
        })
        assert response.status_code == 200
        data = response.json()
        assert data["exit_code"] == 0, data
        lines = data["stdout"].splitlines()
        assert lines[:4] == ["True", "True", "True", ""]
        assert lines[4] in {"0o640", "0o646"}, lines

    @staticmethod
    def test_filesystem_directories_rule_creates_directory_under_home(
        client,
        create_sandbox_with_policy,
    ):
        sandbox = create_sandbox_with_policy(
            name_prefix="fs-home-dir",
            policy={
                "name": "fs-home-dir-policy",
                "filesystem_policy": {
                    "directories": [{
                        "path": "/home",
                        "permissions": "0755",
                    }],
                    "read_only": ["/usr", "/lib", "/lib64", "/etc", "/opt"],
                    "read_write": ["/tmp"],
                },
                "landlock": {
                    "compatibility": "disabled",
                },
                "network": {
                    "mode": "host",
                },
            },
        )

        upload = client.post(
            f"/api/v1/sandboxes/{sandbox['id']}/upload",
            params={"sandbox_path": "/home/upload-created/file.txt"},
            files={"file": ("file.txt", b"hello-home-upload", "text/plain")},
        )
        assert upload.status_code == 204, upload.text

        response = client.post(f"/api/v1/sandboxes/{sandbox['id']}/exec", json={
            "command": [
                "python3",
                "-c",
                (
                    "import os; "
                    "from pathlib import Path; "
                    "home = Path('/home'); "
                    "exec_path = home / 'exec-created'; "
                    "exec_path.mkdir(); "
                    "(exec_path / 'marker.txt').write_text('hello-home-exec'); "
                    "print(home.is_dir()); "
                    "print((home / 'upload-created').is_dir()); "
                    "print((home / 'upload-created/file.txt').read_text()); "
                    "print(exec_path.is_dir())"
                ),
            ],
            "timeout_seconds": 5,
        })
        assert response.status_code == 200
        data = response.json()
        assert data["exit_code"] == 0, data
        assert data["stdout"].splitlines() == ["True", "True", "hello-home-upload", "True"]

        download = client.get(
            f"/api/v1/sandboxes/{sandbox['id']}/download",
            params={"sandbox_path": "/home/exec-created/marker.txt"},
        )
        assert download.status_code == 200, download.text
        assert download.content == b"hello-home-exec"

    @staticmethod
    def test_exec_applies_workdir_env_and_stdin(
        client,
        create_sandbox_with_policy,
    ):
        sandbox = create_sandbox_with_policy(
            name_prefix="exec-options",
            policy={
                "name": "exec-options-policy",
                "filesystem_policy": {
                    "read_only": ["/usr", "/lib", "/lib64", "/etc", "/opt"],
                    "read_write": ["/tmp"],
                },
                "network": {
                    "mode": "host",
                },
            },
        )

        script = (
            "import os, pathlib, sys; "
            "print(os.environ['BOX_TEST']); "
            "print(pathlib.Path.cwd()); "
            "print(sys.stdin.read())"
        )
        response = client.post(f"/api/v1/sandboxes/{sandbox['id']}/exec", json={
            "command": ["python3", "-c", script],
            "workdir": "/tmp",
            "env": {"BOX_TEST": "env-ok"},
            "stdin": "stdin-ok",
            "timeout_seconds": 5,
        })
        assert response.status_code == 200
        data = response.json()
        assert data["exit_code"] == 0, data
        assert data["stdout"].splitlines() == ["env-ok", "/tmp", "stdin-ok"]

    @staticmethod
    def test_policy_environment_applies_to_all_exec_processes(
        client,
        create_sandbox_with_policy,
    ):
        sandbox = create_sandbox_with_policy(
            name_prefix="policy-env",
            policy={
                "name": "policy-env-policy",
                "environment": {
                    "JIUWENBOX_POLICY_ENV": "policy-env-ok",
                    "JIUWENBOX_SHARED_ENV": "from-policy",
                },
                "filesystem_policy": {
                    "read_only": ["/usr", "/lib", "/lib64", "/etc", "/opt"],
                    "read_write": ["/tmp"],
                },
                "network": {
                    "mode": "host",
                },
            },
        )

        script = (
            "import os; "
            "print(os.environ['JIUWENBOX_POLICY_ENV']); "
            "print(os.environ['JIUWENBOX_SHARED_ENV'])"
        )
        response = client.post(f"/api/v1/sandboxes/{sandbox['id']}/exec", json={
            "command": ["python3", "-c", script],
            "timeout_seconds": 5,
        })
        assert response.status_code == 200
        data = response.json()
        assert data["exit_code"] == 0, data
        assert data["stdout"].splitlines() == ["policy-env-ok", "from-policy"]

        response = client.post(f"/api/v1/sandboxes/{sandbox['id']}/exec", json={
            "command": ["python3", "-c", script],
            "env": {"JIUWENBOX_SHARED_ENV": "from-exec"},
            "timeout_seconds": 5,
        })
        assert response.status_code == 200
        data = response.json()
        assert data["exit_code"] == 0, data
        assert data["stdout"].splitlines() == ["policy-env-ok", "from-exec"]

    @staticmethod
    def test_exec_runs_javascript_code(
        client,
        create_sandbox_with_policy,
    ):
        sandbox = create_sandbox_with_policy(
            name_prefix="exec-js",
            policy={
                "name": "exec-js-policy",
                "filesystem_policy": {
                    "read_only": ["/usr", "/lib", "/lib64", "/etc", "/opt"],
                    "read_write": ["/tmp"],
                },
                "network": {
                    "mode": "host",
                },
            },
        )

        js_code = (
            "const label = process.env.BOX_JS_TEST || 'missing'; "
            "const sum = [1, 2, 3, 4].reduce((total, value) => total + value, 0); "
            "console.log(label); "
            "console.log(`sum=${sum}`);"
        )
        response = client.post(f"/api/v1/sandboxes/{sandbox['id']}/exec", json={
            "command": ["node", "-e", js_code],
            "env": {"BOX_JS_TEST": "js-ok"},
            "timeout_seconds": 5,
        })
        assert response.status_code == 200
        data = response.json()
        assert data["exit_code"] == 0, data
        assert data["stdout"].splitlines() == ["js-ok", "sum=10"]
        assert data["stderr"] == ""

    @staticmethod
    def test_download_missing_file_returns_404(
        client,
        create_sandbox_with_policy,
    ):
        sandbox = create_sandbox_with_policy(
            name_prefix="download-missing",
            policy={
                "name": "download-missing-policy",
                "filesystem_policy": {
                    "read_only": ["/usr", "/lib", "/lib64", "/etc", "/opt"],
                    "read_write": ["/tmp"],
                },
                "network": {
                    "mode": "host",
                },
            },
        )

        response = client.get(
            f"/api/v1/sandboxes/{sandbox['id']}/download",
            params={"sandbox_path": "/tmp/not-found.txt"},
        )
        assert response.status_code == 404

    @staticmethod
    def test_download_directory_returns_409(
        client,
        create_sandbox_with_policy,
    ):
        sandbox = create_sandbox_with_policy(
            name_prefix="download-dir",
            policy={
                "name": "download-dir-policy",
                "filesystem_policy": {
                    "read_only": ["/usr", "/lib", "/lib64", "/etc", "/opt"],
                    "read_write": ["/tmp"],
                },
                "network": {
                    "mode": "host",
                },
            },
        )

        response = client.get(
            f"/api/v1/sandboxes/{sandbox['id']}/download",
            params={"sandbox_path": "/tmp"},
        )
        assert response.status_code == 409

    @staticmethod
    def test_list_files_endpoint_returns_files_and_directories(
        client,
        create_sandbox_with_policy,
    ):
        sandbox = create_sandbox_with_policy(
            name_prefix="list-files",
            policy={
                "name": "list-files-policy",
                "filesystem_policy": {
                    "read_only": ["/usr", "/lib", "/lib64", "/etc", "/opt"],
                    "read_write": ["/tmp"],
                },
                "network": {
                    "mode": "host",
                },
            },
        )

        setup = client.post(f"/api/v1/sandboxes/{sandbox['id']}/exec", json={
            "command": [
                "python3",
                "-c",
                (
                    "from pathlib import Path; "
                    "Path('/tmp/list-api/sub').mkdir(parents=True, exist_ok=True); "
                    "Path('/tmp/list-api/a.txt').write_text('a'); "
                    "Path('/tmp/list-api/sub/b.log').write_text('b')"
                ),
            ],
            "timeout_seconds": 5,
        })
        assert setup.status_code == 200
        assert setup.json()["exit_code"] == 0

        response = client.get(
            f"/api/v1/sandboxes/{sandbox['id']}/files",
            params={"sandbox_path": "/tmp/list-api", "recursive": True},
        )
        assert response.status_code == 200
        items = response.json()["items"]
        paths = {item["path"] for item in items}
        assert "/tmp/list-api/a.txt" in paths
        assert "/tmp/list-api/sub" in paths
        assert "/tmp/list-api/sub/b.log" in paths
        assert any(item["name"] == "sub" and item["is_directory"] for item in items)

        files_only = client.get(
            f"/api/v1/sandboxes/{sandbox['id']}/files",
            params={
                "sandbox_path": "/tmp/list-api",
                "recursive": True,
                "include_dirs": False,
            },
        )
        assert files_only.status_code == 200
        assert all(not item["is_directory"] for item in files_only.json()["items"])

    @staticmethod
    def test_search_files_endpoint_filters_matches(
        client,
        create_sandbox_with_policy,
    ):
        sandbox = create_sandbox_with_policy(
            name_prefix="search-files",
            policy={
                "name": "search-files-policy",
                "filesystem_policy": {
                    "read_only": ["/usr", "/lib", "/lib64", "/etc", "/opt"],
                    "read_write": ["/tmp"],
                },
                "network": {
                    "mode": "host",
                },
            },
        )

        setup = client.post(f"/api/v1/sandboxes/{sandbox['id']}/exec", json={
            "command": [
                "python3",
                "-c",
                (
                    "from pathlib import Path; "
                    "Path('/tmp/search-api').mkdir(parents=True, exist_ok=True); "
                    "Path('/tmp/search-api/keep.py').write_text('print(1)'); "
                    "Path('/tmp/search-api/drop.py').write_text('print(2)'); "
                    "Path('/tmp/search-api/readme.md').write_text('# hi')"
                ),
            ],
            "timeout_seconds": 5,
        })
        assert setup.status_code == 200
        assert setup.json()["exit_code"] == 0

        response = client.get(
            f"/api/v1/sandboxes/{sandbox['id']}/search",
            params=[
                ("sandbox_path", "/tmp/search-api"),
                ("pattern", "*.py"),
                ("exclude_patterns", "drop.py"),
            ],
        )
        assert response.status_code == 200
        items = response.json()["items"]
        assert [item["name"] for item in items] == ["keep.py"]

    @staticmethod
    def test_process_user_and_group_policy_is_applied(
        client,
        create_sandbox_with_policy,
    ):
        sandbox = create_sandbox_with_policy(
            name_prefix="process-root",
            policy={
                "name": "process-root-policy",
                "filesystem_policy": {
                    "read_only": ["/usr", "/lib", "/lib64", "/etc", "/opt"],
                    "read_write": ["/tmp"],
                },
                "process": {
                    "run_as_user": "root",
                    "run_as_group": "root",
                },
                "network": {
                    "mode": "host",
                },
            },
        )

        response = client.post(f"/api/v1/sandboxes/{sandbox['id']}/exec", json={
            "command": ["id", "-u"],
            "timeout_seconds": 5,
        })
        assert response.status_code == 200
        uid_data = response.json()
        assert uid_data["exit_code"] == 0, uid_data
        assert uid_data["stdout"].strip() == "0"

        response = client.post(f"/api/v1/sandboxes/{sandbox['id']}/exec", json={
            "command": ["id", "-g"],
            "timeout_seconds": 5,
        })
        assert response.status_code == 200
        gid_data = response.json()
        assert gid_data["exit_code"] == 0, gid_data
        assert gid_data["stdout"].strip() == "0"

    @staticmethod
    def test_syscall_blocked_rule_is_applied(
        client,
        create_sandbox_with_policy,
    ):
        sandbox = create_sandbox_with_policy(
            name_prefix="syscall-block",
            policy={
                "name": "syscall-block-policy",
                "filesystem_policy": {
                    "read_only": ["/usr", "/lib", "/lib64", "/etc", "/opt"],
                    "read_write": ["/tmp"],
                },
                "syscall": {
                    "x86_64": {"blocked": ["getpid"]},
                    "arm64": {"blocked": ["getpid"]},
                },
                "network": {
                    "mode": "host",
                },
            },
        )

        response = client.post(f"/api/v1/sandboxes/{sandbox['id']}/exec", json={
            "command": [
                "python3",
                "-c",
                textwrap.dedent(
                    """
                    import ctypes
                    import errno
                    import platform
                    import sys

                    syscall_numbers = {
                        "x86_64": 39,
                        "AMD64": 39,
                        "aarch64": 172,
                    }
                    nr = syscall_numbers.get(platform.machine())
                    if nr is None:
                        print(f"unsupported-arch:{platform.machine()}")
                        sys.exit(2)

                    libc = ctypes.CDLL("libc.so.6", use_errno=True)
                    libc.syscall.restype = ctypes.c_long
                    ctypes.set_errno(0)
                    result = libc.syscall(nr)
                    err = ctypes.get_errno()
                    if result == -1 and err == errno.EPERM:
                        print("syscall-blocked")
                        sys.exit(7)

                    print(f"unexpected-success:{result}:{err}")
                    """
                ).strip(),
            ],
            "timeout_seconds": 5,
        })
        assert response.status_code == 200
        data = response.json()
        assert data["exit_code"] == 7, data
        assert "syscall-blocked" in data["stdout"]
        assert "unexpected-success" not in data["stdout"]

    @staticmethod
    def test_pid_namespace_policy_is_applied(
        client,
        create_sandbox_with_policy,
    ):
        sandbox = create_sandbox_with_policy(
            name_prefix="pid-ns",
            policy={
                "name": "pid-ns-policy",
                "filesystem_policy": {
                    "read_only": ["/usr", "/lib", "/lib64", "/etc", "/opt"],
                    "read_write": ["/tmp"],
                },
                "namespace": {
                    "pid": True,
                },
                "landlock": {
                    "compatibility": "disabled",
                },
                "network": {
                    "mode": "host",
                },
            },
        )

        response = client.post(f"/api/v1/sandboxes/{sandbox['id']}/exec", json={
            "command": ["python3", "-c", "import os; print(os.getpid())"],
            "timeout_seconds": 5,
        })
        assert response.status_code == 200
        data = response.json()
        assert data["exit_code"] == 0, data
        assert int(data["stdout"].strip()) <= 5

    @staticmethod
    def test_capability_drop_removes_net_raw(
        client,
        create_sandbox_with_policy,
    ):
        sandbox = create_sandbox_with_policy(
            name_prefix="cap-drop",
            policy={
                "name": "cap-drop-policy",
                "filesystem_policy": {
                    "read_only": ["/usr", "/lib", "/lib64", "/etc", "/opt"],
                    "read_write": ["/tmp"],
                },
                "process": {
                    "run_as_user": "root",
                    "run_as_group": "root",
                },
                "capabilities": {
                    "add": ["CAP_NET_RAW"],
                    "drop": ["CAP_NET_RAW"],
                },
                "landlock": {
                    "compatibility": "disabled",
                },
                "network": {
                    "mode": "host",
                },
            },
        )

        response = client.post(f"/api/v1/sandboxes/{sandbox['id']}/exec", json={
            "command": ["python", "-c", _capability_check_script(13)],
            "timeout_seconds": 5,
        })
        assert response.status_code == 200
        data = response.json()
        assert data["exit_code"] == 0, data
        assert data["stdout"].strip() == "no"

    @staticmethod
    def test_capability_add_net_raw_sets_effective_capability(
        client,
        create_sandbox_with_policy,
    ):
        sandbox = create_sandbox_with_policy(
            name_prefix="cap-add",
            policy={
                "name": "cap-add-policy",
                "filesystem_policy": {
                    "read_only": ["/usr", "/lib", "/lib64", "/etc", "/opt"],
                    "read_write": ["/tmp"],
                },
                "process": {
                    "run_as_user": "root",
                    "run_as_group": "root",
                },
                "capabilities": {
                    "add": ["CAP_NET_RAW"],
                    "drop": [],
                },
                "landlock": {
                    "compatibility": "disabled",
                },
                "network": {
                    "mode": "host",
                },
            },
        )

        response = client.post(f"/api/v1/sandboxes/{sandbox['id']}/exec", json={
            "command": ["python", "-c", _capability_check_script(13)],
            "timeout_seconds": 5,
        })
        assert response.status_code == 200
        data = response.json()
        assert data["exit_code"] == 0, data
        assert data["stdout"].strip() == "yes"

    @staticmethod
    def test_landlock_hard_requirement_policy_is_enforced(
        client,
    ):
        create_resp = client.post("/api/v1/sandboxes", json={
            "policy": _with_runtime_support({
                "name": "landlock-hard-policy",
                "filesystem_policy": {
                    "read_only": ["/usr", "/lib", "/lib64", "/etc", "/opt"],
                    "read_write": ["/tmp"],
                },
                "landlock": {
                    "compatibility": "hard_requirement",
                },
                "network": {
                    "mode": "host",
                },
            }),
        })
        assert create_resp.status_code == 201
        data = create_resp.json()
        if data["phase"] == "ready":
            assert data["phase"] == "ready", data
        else:
            assert data["phase"] == "error", data
            assert "landlock" in (data.get("error_message") or "").lower()

    @staticmethod
    def test_landlock_rules_allow_policy_paths_and_deny_other_mounted_paths(
        client,
    ):
        create_resp = client.post("/api/v1/sandboxes", json={
            "name": "landlock-rules",
            "policy": _with_runtime_support({
                "name": "landlock-rules-policy",
                "filesystem_policy": {
                    "read_only": ["/usr", "/lib", "/lib64", "/etc", "/opt"],
                    "read_write": ["/tmp"],
                },
                "landlock": {
                    "compatibility": "hard_requirement",
                },
                "network": {
                    "mode": "host",
                },
            }),
        })
        assert create_resp.status_code == 201
        sandbox = create_resp.json()
        if sandbox["phase"] == "error":
            assert "landlock" in (sandbox.get("error_message") or "").lower()
            return
        assert sandbox["phase"] == "ready", sandbox

        script = textwrap.dedent(
            """
            from pathlib import Path
            import sys

            allowed = Path("/tmp/landlock-allowed.txt")
            allowed.write_text("landlock-allowed")
            assert allowed.read_text() == "landlock-allowed"

            try:
                Path("/jiuwenbox/landlock-launcher.py").read_text()
            except PermissionError:
                print("landlock-denied")
                sys.exit(7)

            print("unexpected-success")
            """
        ).strip()
        response = client.post(f"/api/v1/sandboxes/{sandbox['id']}/exec", json={
            "command": ["python", "-c", script],
            "timeout_seconds": 5,
        })
        assert response.status_code == 200
        data = response.json()
        assert data["exit_code"] == 7, data
        assert "landlock-denied" in data["stdout"]
        assert "unexpected-success" not in data["stdout"]

    @staticmethod
    def test_network_mode_isolated_blocks_http_requests(
        client,
        create_sandbox_with_policy,
        server_endpoint,
    ):
        sandbox = create_sandbox_with_policy(
            name_prefix="net-isolated",
            policy={
                "name": "net-isolated-policy",
                "filesystem_policy": {
                    "read_only": ["/usr", "/lib", "/lib64", "/etc", "/opt"],
                    "read_write": ["/tmp"],
                },
                "network": {
                    "mode": "isolated",
                    "egress": {"default": "allow"},
                },
            },
        )

        script = (
            "import sys, urllib.request; "
            "urllib.request.urlopen(sys.argv[1], timeout=2).read(); "
            "print('unexpected-success')"
        )
        response = client.post(f"/api/v1/sandboxes/{sandbox['id']}/exec", json={
            "command": ["python", "-c", script, _sandbox_health_url(server_endpoint)],
            "timeout_seconds": 5,
        })
        assert response.status_code == 200
        data = response.json()
        assert data["exit_code"] != 0
        assert "unexpected-success" not in data["stdout"]

    @staticmethod
    def test_network_mode_host_allows_http_requests(
        client,
        create_sandbox_with_policy,
    ):
        sandbox = create_sandbox_with_policy(
            name_prefix="net-host",
            policy={
                "name": "net-host-policy",
                "filesystem_policy": {
                    "read_only": ["/usr", "/lib", "/lib64", "/etc", "/opt"],
                    "read_write": ["/tmp"],
                },
                "network": {
                    "mode": "host",
                    "egress": {"default": "allow"},
                },
            },
        )

        script = textwrap.dedent(
            """
            import sys
            import urllib.request

            request = urllib.request.Request(
                sys.argv[1],
                headers={"User-Agent": "jiuwenbox-integration-test"},
            )
            with urllib.request.urlopen(request, timeout=10) as response:
                print(response.status)
                print(response.geturl())
            """
        ).strip()
        response = client.post(f"/api/v1/sandboxes/{sandbox['id']}/exec", json={
            "command": ["python3", "-c", script, "https://www.huawei.com/"],
            "timeout_seconds": 15,
        })
        assert response.status_code == 200
        data = response.json()
        assert data["exit_code"] == 0
        assert "huawei.com" in data["stdout"].lower()

    @staticmethod
    def test_host_network_allows_external_tcp_connection_to_sandbox_process(
        client,
        create_sandbox_with_policy,
    ):
        sandbox = create_sandbox_with_policy(
            name_prefix="net-host-listener",
            policy={
                "name": "net-host-listener-policy",
                "filesystem_policy": {
                    "read_only": ["/usr", "/lib", "/lib64", "/etc", "/opt"],
                    "read_write": ["/tmp"],
                },
                "network": {
                    "mode": "host",
                    "egress": {"default": "allow"},
                    "ingress": {"default": "allow"},
                },
            },
        )
        host = _host_network_ip_from_sandbox(client, sandbox["id"])
        port = _unused_host_network_tcp_port_from_sandbox(client, sandbox["id"])
        script = textwrap.dedent(
            """
            import socket
            import sys

            port = int(sys.argv[1])
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(("0.0.0.0", port))
            server.listen(1)
            print("server-listening", flush=True)
            while True:
                conn, _ = server.accept()
                data = conn.recv(64)
                print("received:" + data.decode(), flush=True)
                conn.sendall(b"pong-from-sandbox")
                conn.close()
            """
        ).strip()
        response = client.post(f"/api/v1/sandboxes/{sandbox['id']}/exec_background", json={
            "command": ["python3", "-c", script, str(port)],
            "timeout_seconds": 10,
        })
        assert response.status_code == 200, response.text
        background = response.json()
        assert background["started"] is True, background
        assert isinstance(background["pid"], int), background
        assert background["error_message"] is None

        deadline = time.monotonic() + 8
        last_error = None
        payload = b""
        while time.monotonic() < deadline:
            try:
                with socket.create_connection((host, port), timeout=0.5) as sock:
                    sock.sendall(b"ping-from-host")
                    payload = sock.recv(64)
                    break
            except OSError as exc:
                last_error = exc
                time.sleep(0.1)
        else:
            raise AssertionError(
                f"sandbox tcp server did not accept connections: {last_error}; "
                f"background={background}"
            )

        assert payload == b"pong-from-sandbox"
        for index in range(5):
            with socket.create_connection((host, port), timeout=0.5) as sock:
                sock.sendall(f"ping-{index}".encode())
                assert sock.recv(64) == b"pong-from-sandbox"
            time.sleep(0.5)

    @staticmethod
    def test_default_policy_allows_network_https(client):
        response = client.post("/api/v1/sandboxes", json={})
        assert response.status_code == 201, response.text
        sandbox = response.json()
        assert sandbox["phase"] == "ready", sandbox

        script = textwrap.dedent(
            """
            import sys
            import urllib.request

            request = urllib.request.Request(
                sys.argv[1],
                headers={"User-Agent": "jiuwenbox-integration-test"},
            )
            with urllib.request.urlopen(request, timeout=10) as response:
                print(response.status)
                print(response.geturl())
            """
        ).strip()
        response = client.post(f"/api/v1/sandboxes/{sandbox['id']}/exec", json={
            "command": ["python3", "-c", script, "https://www.huawei.com/"],
            "timeout_seconds": 15,
        })
        assert response.status_code == 200
        data = response.json()
        assert data["exit_code"] == 0, data
        assert "huawei.com" in data["stdout"].lower()

    @staticmethod
    def test_default_policy_blocks_access_to_box_server_health(client, server_endpoint):
        response = client.post("/api/v1/sandboxes", json={})
        assert response.status_code == 201, response.text
        sandbox = response.json()
        assert sandbox["phase"] == "ready", sandbox

        script = textwrap.dedent(
            """
            import sys
            import urllib.request

            try:
                urllib.request.urlopen(sys.argv[1], timeout=3).read()
            except Exception as exc:
                print(type(exc).__name__)
                sys.exit(7)

            print("unexpected-success")
            """
        ).strip()
        response = client.post(f"/api/v1/sandboxes/{sandbox['id']}/exec", json={
            "command": ["python3", "-c", script, _sandbox_health_url(server_endpoint)],
            "timeout_seconds": 10,
        })
        assert response.status_code == 200
        data = response.json()
        assert data["exit_code"] == 7, data
        assert "unexpected-success" not in data["stdout"]

    @staticmethod
    def test_default_policy_does_not_expose_sensitive_etc_files(client):
        response = client.post("/api/v1/sandboxes", json={})
        assert response.status_code == 201, response.text
        sandbox = response.json()
        assert sandbox["phase"] == "ready", sandbox

        script = textwrap.dedent(
            """
            from pathlib import Path
            import sys

            sensitive_paths = [
                "/etc/passwd",
                "/etc/shadow",
                "/etc/group",
                "/etc/gshadow",
            ]

            for sensitive_path in sensitive_paths:
                try:
                    content = Path(sensitive_path).read_text()
                except (FileNotFoundError, PermissionError):
                    print(f"denied:{sensitive_path}")
                    continue

                print(f"leaked:{sensitive_path}:{content[:80]!r}")
                sys.exit(1)
            """
        ).strip()
        response = client.post(f"/api/v1/sandboxes/{sandbox['id']}/exec", json={
            "command": ["python3", "-c", script],
            "timeout_seconds": 5,
        })
        assert response.status_code == 200
        data = response.json()
        assert data["exit_code"] == 0, data
        assert "leaked:" not in data["stdout"]
        assert "root:" not in data["stdout"]
        assert data["stdout"].splitlines() == [
            "denied:/etc/passwd",
            "denied:/etc/shadow",
            "denied:/etc/group",
            "denied:/etc/gshadow",
        ]

    @staticmethod
    def test_ingress_allowed_port_accepts_loopback_connection(
        client,
        create_sandbox_with_policy,
    ):
        sandbox = create_sandbox_with_policy(
            name_prefix="ingress-allow",
            policy={
                "name": "ingress-allow-policy",
                "filesystem_policy": {
                    "read_only": ["/usr", "/lib", "/lib64", "/etc", "/opt"],
                    "read_write": ["/tmp"],
                },
                "network": {
                    "mode": "isolated",
                    "egress": {"default": "allow"},
                    "ingress": {
                        "default": "deny",
                        "allowed_ips": ["127.0.0.1/32"],
                        "allowed_ports": [18081],
                    },
                },
            },
        )

        response = client.post(f"/api/v1/sandboxes/{sandbox['id']}/exec", json={
            "command": ["python3", "-c", _loopback_ingress_script(True), "18081"],
            "timeout_seconds": 5,
        })
        assert response.status_code == 200
        data = response.json()
        assert data["exit_code"] == 0, data
        assert "ingress-ok" in data["stdout"]

    @staticmethod
    def test_ingress_blocked_port_rejects_loopback_connection(
        client,
        create_sandbox_with_policy,
    ):
        sandbox = create_sandbox_with_policy(
            name_prefix="ingress-block",
            policy={
                "name": "ingress-block-policy",
                "filesystem_policy": {
                    "read_only": ["/usr", "/lib", "/lib64", "/etc", "/opt"],
                    "read_write": ["/tmp"],
                },
                "network": {
                    "mode": "isolated",
                    "egress": {"default": "allow"},
                    "ingress": {
                        "default": "deny",
                        "allowed_ips": ["127.0.0.1/32"],
                        "allowed_ports": [18081],
                        "blocked_ports": [18082],
                    },
                },
            },
        )

        response = client.post(f"/api/v1/sandboxes/{sandbox['id']}/exec", json={
            "command": ["python3", "-c", _loopback_ingress_script(False), "18082"],
            "timeout_seconds": 5,
        })
        assert response.status_code == 200
        data = response.json()
        assert data["exit_code"] != 0
        assert "unexpected-success" not in data["stdout"]

    @staticmethod
    def test_isolated_sandbox_policy_persists_after_restart(
        client,
        create_sandbox_with_policy,
    ):
        sandbox = create_sandbox_with_policy(
            name_prefix="netns-persist",
            policy={
                "name": "netns-persist-policy",
                "filesystem_policy": {
                    "read_only": ["/usr", "/lib", "/lib64", "/etc", "/opt"],
                    "read_write": ["/tmp"],
                },
                "network": {
                    "mode": "isolated",
                    "egress": {"default": "allow"},
                    "ingress": {
                        "default": "deny",
                        "allowed_ips": ["127.0.0.1/32"],
                        "allowed_ports": [18083],
                    },
                },
            },
        )

        first_exec = client.post(f"/api/v1/sandboxes/{sandbox['id']}/exec", json={
            "command": ["python3", "-c", _loopback_ingress_script(True), "18083"],
            "timeout_seconds": 5,
        })
        assert first_exec.status_code == 200
        first_data = first_exec.json()
        assert first_data["exit_code"] == 0, first_data

        stop_response = client.post(f"/api/v1/sandboxes/{sandbox['id']}/stop")
        assert stop_response.status_code == 200

        start_response = client.post(f"/api/v1/sandboxes/{sandbox['id']}/start")
        assert start_response.status_code == 200

        second_exec = client.post(f"/api/v1/sandboxes/{sandbox['id']}/exec", json={
            "command": ["python3", "-c", _loopback_ingress_script(True), "18083"],
            "timeout_seconds": 5,
        })
        assert second_exec.status_code == 200
        second_data = second_exec.json()
        assert second_data["exit_code"] == 0, second_data

        delete_response = client.delete(f"/api/v1/sandboxes/{sandbox['id']}")
        assert delete_response.status_code == 204

    # ------------------------------------------------------------------
    # ``/jiuwenbox`` runtime-script integrity
    #
    # jiuwenbox places two trusted scripts on a tmpfs at ``/jiuwenbox``
    # inside every sandbox:
    #
    #   * ``/jiuwenbox/landlock-launcher.py`` - applies the Landlock
    #     ruleset, then either ``compile``/``exec``s the daemon
    #     in-process or ``execvp``s a one-shot user command.
    #   * ``/jiuwenbox/sandbox-daemon.py``    - long-running daemon that
    #     fronts ``exec`` / ``write_file`` / ``read_file`` / ``list_dir``
    #     IPC requests with the policy uid/gid, mount layout, seccomp
    #     filter, and Landlock ruleset already applied.
    #
    # If user code inside the sandbox could read, modify, replace, or
    # unlink either script, the entire trust model collapses: a hostile
    # payload could rewrite the daemon and gain a foothold for every
    # *subsequent* exec the box-server dispatches into the sandbox.
    #
    # The launcher pre-reads both scripts into memory **before** Landlock
    # is installed, so user code (which always runs strictly after
    # Landlock is in force) is supposed to see ``/jiuwenbox`` as
    # completely off-limits. The two cases below pin that contract; the
    # complementary ``TestReservedSandboxPaths`` class below proves that
    # user policies cannot widen the Landlock allowlist to expose
    # ``/jiuwenbox`` from the outside.
    # ------------------------------------------------------------------

    _RESERVED_SCRIPT_DAEMON = "/jiuwenbox/sandbox-daemon.py"
    _RESERVED_SCRIPT_LAUNCHER = "/jiuwenbox/landlock-launcher.py"

    _RESERVED_INTEGRITY_POLICY = {
        "name": "reserved-script-integrity-policy",
        "filesystem_policy": {
            # /jiuwenbox is intentionally NOT listed below - the runtime
            # artifacts there must remain inaccessible to user code, and
            # PolicyEngine would reject the policy outright if it tried.
            "read_only": ["/usr", "/lib", "/lib64", "/etc", "/opt"],
            "read_write": ["/tmp"],
        },
        "landlock": {
            "compatibility": "hard_requirement",
        },
        "network": {
            "mode": "host",
        },
    }

    @staticmethod
    def test_reserved_dir_scripts_cannot_be_tampered_with(
        create_sandbox_with_policy,
        client,
    ):
        """User code must hit ``PermissionError`` on every flavour of
        access against ``/jiuwenbox`` and the trusted scripts inside it.

        The script returns exit code 0 only when *every* attempted attack
        was rejected; any successful read/write/delete/symlink/listdir
        would surface as a non-zero exit code with a label in stderr, so
        the happy path here is the locked-down path.
        """
        sandbox = create_sandbox_with_policy(
            name_prefix="reserved-script-integrity",
            policy=TestPolicyEnforcement._RESERVED_INTEGRITY_POLICY,
        )

        daemon_path = TestPolicyEnforcement._RESERVED_SCRIPT_DAEMON
        launcher_path = TestPolicyEnforcement._RESERVED_SCRIPT_LAUNCHER

        attack_script = textwrap.dedent(
            f"""
            import errno
            import os
            import sys

            DAEMON = {daemon_path!r}
            LAUNCHER = {launcher_path!r}

            failures = []

            # Errnos that mean the attack was rejected. The first three
            # come straight from Landlock (EACCES on read/write/exec,
            # EPERM on operations like unlink, EROFS on tmpfs that we
            # remount read-only). The last two come from the filesystem
            # layer doing its own job *before* Landlock even gets to
            # rule:
            #   * EEXIST - ``os.symlink(target, link)`` refuses to clobber
            #     an existing file at ``link``. The daemon script is
            #     therefore not replaced, which is exactly the
            #     containment guarantee we are pinning here.
            #   * EXDEV - ``os.rename(src, dst)`` cannot move a file
            #     across different mounts. ``/jiuwenbox`` is its own
            #     tmpfs and ``/tmp`` is another one, so the
            #     rename-shadow attack cannot complete regardless of
            #     Landlock. Treating this as containment success
            #     documents the additional defence-in-depth that the
            #     runtime relies on.
            BLOCKED_ERRNOS = (
                errno.EACCES,
                errno.EPERM,
                errno.EROFS,
                errno.EEXIST,
                errno.EXDEV,
            )

            def expect_blocked(label, fn):
                try:
                    fn()
                except PermissionError:
                    return
                except FileNotFoundError:
                    # Landlock can mask the path so it appears not to
                    # exist; that is also a containment success.
                    return
                except FileExistsError:
                    # See BLOCKED_ERRNOS comment above re: EEXIST.
                    return
                except OSError as exc:
                    if exc.errno in BLOCKED_ERRNOS:
                        return
                    failures.append(
                        f"{{label}}: unexpected OSError errno={{exc.errno}} {{exc!r}}"
                    )
                    return
                failures.append(f"{{label}}: did not raise")

            # 1. Direct read of the trusted scripts must be denied.
            expect_blocked("read-daemon",   lambda: open(DAEMON, "rb").close())
            expect_blocked("read-launcher", lambda: open(LAUNCHER, "rb").close())

            # 2. Truncating / overwriting either script must be denied.
            expect_blocked("write-truncate-daemon",   lambda: open(DAEMON, "wb").close())
            expect_blocked("write-append-daemon",     lambda: open(DAEMON, "ab").close())
            expect_blocked("write-truncate-launcher", lambda: open(LAUNCHER, "wb").close())

            # 3. Unlinking the scripts must be denied.
            expect_blocked("unlink-daemon",   lambda: os.unlink(DAEMON))
            expect_blocked("unlink-launcher", lambda: os.unlink(LAUNCHER))

            # 4. Replacing them via symlink (atomic shadow attack) must
            #    be denied. We try both ``symlink`` over the existing
            #    path and ``rename`` of an attacker-controlled file.
            expect_blocked(
                "symlink-shadow-daemon",
                lambda: os.symlink("/tmp/evil", DAEMON),
            )
            attacker = "/tmp/jiuwenbox-attacker.py"
            try:
                with open(attacker, "wb") as fh:
                    fh.write(b"# planted by user code\\n")
            except OSError as exc:
                failures.append(
                    f"setup-attacker-failed: {{exc!r}}"
                )
            else:
                expect_blocked(
                    "rename-shadow-daemon",
                    lambda: os.rename(attacker, DAEMON),
                )

            # 5. Creating *new* files inside ``/jiuwenbox`` must also
            #    be denied so an attacker cannot drop a co-resident
            #    decoy that the runtime might later mistakenly load.
            expect_blocked(
                "create-new-file-in-reserved-dir",
                lambda: open("/jiuwenbox/evil.py", "wb").close(),
            )

            # 6. Even directory enumeration must be denied; otherwise an
            #    attacker could probe what exists before mounting another
            #    technique.
            expect_blocked("listdir-reserved", lambda: os.listdir("/jiuwenbox"))
            expect_blocked("scandir-reserved", lambda: list(os.scandir("/jiuwenbox")))

            # 7. Permission bits must not be mutable from user code.
            expect_blocked("chmod-daemon", lambda: os.chmod(DAEMON, 0o777))

            if failures:
                for fail in failures:
                    print(fail, file=sys.stderr)
                sys.exit(1)
            print("all-blocked")
            """
        ).strip()

        response = client.post(
            f"/api/v1/sandboxes/{sandbox['id']}/exec",
            json={
                "command": ["python3", "-c", attack_script],
                "timeout_seconds": 10,
            },
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["exit_code"] == 0, data
        assert data["stdout"].strip() == "all-blocked", data

    @staticmethod
    def test_sandbox_remains_functional_after_attempted_reserved_dir_tampering(
        create_sandbox_with_policy,
        client,
    ):
        """Attempting to tamper with ``/jiuwenbox`` scripts must not
        damage the IPC daemon.

        The daemon is loaded into memory before Landlock applies, so its
        on-disk artifact is consumed only at sandbox-creation time. This
        test guards against a regression where a future change makes
        the daemon reload from ``/jiuwenbox`` on later requests - which
        would mean an attacker who *did* break in could bend subsequent
        execs to their will. We pound the attack endpoint, then verify
        that two different IPC code paths (exec and read_file via
        download) still produce the expected results.
        """
        sandbox = create_sandbox_with_policy(
            name_prefix="reserved-script-survival",
            policy=TestPolicyEnforcement._RESERVED_INTEGRITY_POLICY,
        )
        sandbox_id = sandbox["id"]

        daemon_path = TestPolicyEnforcement._RESERVED_SCRIPT_DAEMON
        launcher_path = TestPolicyEnforcement._RESERVED_SCRIPT_LAUNCHER

        attack_script = textwrap.dedent(
            f"""
            import os
            DAEMON = {daemon_path!r}
            LAUNCHER = {launcher_path!r}
            for path in (DAEMON, LAUNCHER):
                for opener in (
                    lambda p=path: open(p, 'rb').close(),
                    lambda p=path: open(p, 'wb').close(),
                    lambda p=path: os.unlink(p),
                    lambda p=path: os.symlink('/tmp/evil', p),
                    lambda p=path: os.chmod(p, 0o777),
                ):
                    try:
                        opener()
                    except OSError:
                        pass
            print('attempted')
            """
        ).strip()

        attack_resp = client.post(
            f"/api/v1/sandboxes/{sandbox_id}/exec",
            json={
                "command": ["python3", "-c", attack_script],
                "timeout_seconds": 10,
            },
        )
        assert attack_resp.status_code == 200, attack_resp.text
        # The script swallows every error on purpose; what matters is
        # that the daemon survives the volley of attempted mutations.
        assert attack_resp.json()["exit_code"] == 0, attack_resp.json()

        # IPC-exec must still work end-to-end after the attack.
        followup_resp = client.post(
            f"/api/v1/sandboxes/{sandbox_id}/exec",
            json={
                "command": [
                    "python3",
                    "-c",
                    "print('post-attack-exec-ok')",
                ],
                "timeout_seconds": 10,
            },
        )
        assert followup_resp.status_code == 200, followup_resp.text
        followup = followup_resp.json()
        assert followup["exit_code"] == 0, followup
        assert "post-attack-exec-ok" in followup["stdout"]

        # IPC file-op fast paths must also still work. Round-trip a
        # payload through upload + download to confirm the daemon is
        # still serving non-exec requests too.
        target = "/tmp/post-attack-marker.txt"
        marker = b"post-attack-file-op-ok"
        upload_resp = client.post(
            f"/api/v1/sandboxes/{sandbox_id}/upload",
            params={"sandbox_path": target},
            files={"file": ("marker.txt", marker, "text/plain")},
        )
        assert upload_resp.status_code == 204, upload_resp.text

        download_resp = client.get(
            f"/api/v1/sandboxes/{sandbox_id}/download",
            params={"sandbox_path": target},
        )
        assert download_resp.status_code == 200, download_resp.text
        assert download_resp.content == marker


# ----------------------------------------------------------------------
# Reserved-sandbox-path validation
#
# ``PolicyEngine`` reserves a small set of in-sandbox paths
# (``_RESERVED_SANDBOX_PATHS``, currently ``("/jiuwenbox",)``) for the
# trusted launcher and daemon scripts. Any user policy that names that
# subtree must be rejected at sandbox-creation time, otherwise:
#
#   * ``read_only`` / ``read_write`` / ``directories`` / ``files``
#     entries would punch ``/jiuwenbox`` into the Landlock allowlist
#     (see ``jiuwenbox/supervisor/landlock.py``), letting user code read
#     the launcher and daemon scripts and bypassing the
#     ``test_reserved_dir_scripts_cannot_be_tampered_with`` guarantee;
#   * ``bind_mounts`` with ``sandbox_path`` under ``/jiuwenbox`` would
#     either shadow our launcher mount or bind a user-controlled host
#     directory under the reserved name, which would also leak into the
#     Landlock allowlist;
#   * ``device`` mounts behave the same way as ``bind_mounts``, with
#     the additional risk of granting ``--dev-bind`` privileges inside
#     a path the runtime treats as trusted.
#
# The cases below assert that every flavour of policy-supplied path
# referencing the reserved subtree is rejected with a 400 response and a
# message that names the offending path.
# ----------------------------------------------------------------------


class TestReservedSandboxPaths:
    """Server must reject user policies that target ``/jiuwenbox``."""

    _RESERVED_DIR = "/jiuwenbox"
    _RESERVED_NESTED = "/jiuwenbox/landlock-launcher.py"
    _RESERVED_DEEP_NESTED = "/jiuwenbox/sub/dir/file.py"

    @staticmethod
    def _post_policy(client, policy: dict):
        """Submit a policy and return ``(status_code, body)`` pairs.

        ``create_sandbox_with_policy`` asserts ``phase == "ready"`` so it
        is unsuitable for negative tests; we hit the endpoint directly.
        """
        return client.post(
            "/api/v1/sandboxes",
            json={
                "policy_mode": "override",
                "policy": _with_runtime_support(policy),
            },
        )

    @staticmethod
    def _assert_rejected_with_reserved_message(response, expected_path: str):
        assert response.status_code == 400, response.text
        body = response.json()
        assert "error" in body, body
        message = body["error"]
        assert expected_path in message, message
        assert "reserved" in message.lower(), message

    @staticmethod
    @pytest.mark.parametrize(
        "field, sandbox_path",
        [
            ("read_only", _RESERVED_DIR),
            ("read_only", _RESERVED_NESTED),
            ("read_only", _RESERVED_DEEP_NESTED),
            ("read_write", _RESERVED_DIR),
            ("read_write", _RESERVED_NESTED),
            ("read_write", _RESERVED_DEEP_NESTED),
        ],
    )
    def test_read_lists_cannot_reference_reserved_subtree(
        client,
        field: str,
        sandbox_path: str,
    ):
        """``read_only`` / ``read_write`` are pushed straight into the
        Landlock allowlist by ``encode_landlock_payload``. Letting a user
        sneak ``/jiuwenbox`` in there would expose the launcher / daemon
        scripts the moment Landlock applies, so the policy engine has to
        bounce these inputs before any sandbox is created.
        """
        policy = {
            "name": f"reserved-{field}-policy",
            "filesystem_policy": {field: [sandbox_path]},
        }
        response = TestReservedSandboxPaths._post_policy(client, policy)
        TestReservedSandboxPaths._assert_rejected_with_reserved_message(
            response, sandbox_path,
        )

    @staticmethod
    @pytest.mark.parametrize(
        "sandbox_path",
        [_RESERVED_DIR, _RESERVED_NESTED, _RESERVED_DEEP_NESTED],
    )
    def test_directories_field_cannot_reference_reserved_subtree(
        client,
        sandbox_path: str,
    ):
        """``filesystem_policy.directories`` ultimately becomes a
        ``--dir`` mount + Landlock read_write entry; both behaviours
        would clobber our reserved tmpfs.
        """
        policy = {
            "name": "reserved-directories-policy",
            "filesystem_policy": {"directories": [sandbox_path]},
        }
        response = TestReservedSandboxPaths._post_policy(client, policy)
        TestReservedSandboxPaths._assert_rejected_with_reserved_message(
            response, sandbox_path,
        )

    @staticmethod
    @pytest.mark.parametrize(
        "sandbox_path",
        [_RESERVED_DIR, _RESERVED_NESTED, _RESERVED_DEEP_NESTED],
    )
    def test_files_field_cannot_reference_reserved_subtree(
        client,
        sandbox_path: str,
    ):
        """A single user-controlled file inside ``/jiuwenbox`` would
        still leak that path into the Landlock allowlist via
        ``encode_landlock_payload``; reject the whole subtree, not just
        the canonical script names.
        """
        policy = {
            "name": "reserved-files-policy",
            "filesystem_policy": {"files": [sandbox_path]},
        }
        response = TestReservedSandboxPaths._post_policy(client, policy)
        TestReservedSandboxPaths._assert_rejected_with_reserved_message(
            response, sandbox_path,
        )

    @staticmethod
    @pytest.mark.parametrize("mode", ["ro", "rw"])
    @pytest.mark.parametrize(
        "sandbox_path",
        [_RESERVED_DIR, _RESERVED_NESTED, _RESERVED_DEEP_NESTED],
    )
    def test_bind_mounts_cannot_target_reserved_subtree(
        client,
        sandbox_path: str,
        mode: str,
    ):
        """``bind_mounts.sandbox_path`` is the most dangerous knob: it
        mounts a host-controlled directory under the reserved subtree
        and tells Landlock to allow it. ``host_path`` is irrelevant for
        the policy reservation (it lives in the operator's filesystem)
        but we need to supply *something* that exists on host so the
        request reaches validation.
        """
        policy = {
            "name": "reserved-bind-mounts-policy",
            "filesystem_policy": {
                "bind_mounts": [
                    {
                        "host_path": "/tmp",
                        "sandbox_path": sandbox_path,
                        "mode": mode,
                    },
                ],
            },
        }
        response = TestReservedSandboxPaths._post_policy(client, policy)
        TestReservedSandboxPaths._assert_rejected_with_reserved_message(
            response, sandbox_path,
        )

    @staticmethod
    @pytest.mark.parametrize(
        "sandbox_path",
        [_RESERVED_DIR, _RESERVED_NESTED, _RESERVED_DEEP_NESTED],
    )
    def test_device_mounts_cannot_target_reserved_subtree(
        client,
        sandbox_path: str,
    ):
        """``device`` mounts go through ``--dev-bind`` and the Landlock
        allowlist; if a user can pin ``/jiuwenbox/foo`` here, they
        smuggle the reserved subtree back into Landlock the same way
        ``bind_mounts`` would.
        """
        policy = {
            "name": "reserved-device-policy",
            "filesystem_policy": {
                "device": [
                    {
                        "host_path": "/dev/null",
                        "sandbox_path": sandbox_path,
                    },
                ],
            },
        }
        response = TestReservedSandboxPaths._post_policy(client, policy)
        TestReservedSandboxPaths._assert_rejected_with_reserved_message(
            response, sandbox_path,
        )

    @staticmethod
    def test_unrelated_sandbox_paths_are_not_rejected(
        client, create_sandbox_with_policy,
    ):
        """Sanity check: paths that merely resemble the reserved name
        (substring matches, suffix collisions, ``/run`` left over from
        the previous design) must still be allowed. This guards against
        an over-broad ``startswith`` style implementation.
        """
        policy = {
            "name": "non-reserved-policy",
            "filesystem_policy": {
                "read_only": [
                    "/usr",
                    "/lib",
                    "/lib64",
                    "/etc",
                    "/opt",
                    # ``/jiuwenbox-public`` is *not* under
                    # ``/jiuwenbox`` because PurePosixPath compares full
                    # path components, not raw string prefixes.
                    "/jiuwenbox-public",
                    # Legacy directory we used to host the launcher in -
                    # plain ``/run`` must remain a normal user-policy
                    # path now that the reserved subtree has moved.
                    "/run",
                ],
                "read_write": ["/tmp"],
            },
        }
        sandbox = create_sandbox_with_policy(
            name_prefix="non-reserved",
            policy=policy,
        )
        assert sandbox["phase"] == "ready", sandbox

    @staticmethod
    def test_reserved_subtree_rejection_runs_before_sandbox_creation(
        client,
    ):
        """The launcher / daemon scripts must never be touched by an
        invalid policy. We assert the failure path returns 400 *and*
        does not surface a non-zero phase, which would imply the runtime
        partially started a sandbox before bouncing.
        """
        policy = {
            "name": "reserved-pre-creation-policy",
            "filesystem_policy": {
                "read_only": [TestReservedSandboxPaths._RESERVED_DIR],
            },
        }
        response = TestReservedSandboxPaths._post_policy(client, policy)
        TestReservedSandboxPaths._assert_rejected_with_reserved_message(
            response, TestReservedSandboxPaths._RESERVED_DIR,
        )
        # The /sandboxes endpoint only returns a created body on 201.
        # 400 responses must not contain phase information.
        assert "phase" not in response.json(), response.json()


class TestBwrapFilesystem:
    @staticmethod
    def test_read_rules_do_not_mount_host_paths():
        policy = SecurityPolicy.model_validate({
            "filesystem_policy": {
                "read_only": ["/host-read-only"],
                "read_write": ["/host-read-write"],
                "bind_mounts": [
                    {
                        "host_path": "/host-source-ro",
                        "sandbox_path": "/sandbox-target-ro",
                        "mode": "ro",
                    },
                    {
                        "host_path": "/host-source-rw",
                        "sandbox_path": "/sandbox-target-rw",
                        "mode": "rw",
                    },
                ],
            },
        })

        args = BwrapConfig.from_policy(policy, ["true"]).to_args()

        assert not _has_mount(args, "--ro-bind", "/host-read-only", "/host-read-only")
        assert not _has_mount(args, "--bind", "/host-read-write", "/host-read-write")
        assert _has_mount(args, "--ro-bind", "/host-source-ro", "/sandbox-target-ro")
        assert _has_mount(args, "--bind", "/host-source-rw", "/sandbox-target-rw")

    @staticmethod
    def test_nested_bind_targets_create_parent_directories():
        policy = SecurityPolicy.model_validate({
            "filesystem_policy": {
                "bind_mounts": [{
                    "host_path": "/etc/resolv.conf",
                    "sandbox_path": "/etc/resolv.conf",
                    "mode": "ro",
                }],
            },
        })

        args = BwrapConfig.from_policy(policy, ["true"]).to_args()

        assert _has_arg_pair(args, "--dir", "/etc")
        assert _has_mount(args, "--ro-bind", "/etc/resolv.conf", "/etc/resolv.conf")

    @staticmethod
    def test_device_mounts_use_dev_bind_and_create_parent_directories():
        policy = SecurityPolicy.model_validate({
            "filesystem_policy": {
                "device": [{
                    "host_path": "/dev/dri/renderD128",
                    "sandbox_path": "/dev/dri/renderD128",
                }],
            },
        })

        args = BwrapConfig.from_policy(policy, ["true"]).to_args()

        assert not _has_arg_pair(args, "--dir", "/dev")
        assert _has_arg_pair(args, "--dir", "/dev/dri")
        assert _has_mount(args, "--dev-bind", "/dev/dri/renderD128", "/dev/dri/renderD128")

    @staticmethod
    def test_read_only_parent_of_nested_bind_is_remounted_read_only():
        policy = SecurityPolicy.model_validate({
            "filesystem_policy": {
                "read_only": ["/etc"],
                "read_write": ["/tmp"],
                "bind_mounts": [{
                    "host_path": "/etc/resolv.conf",
                    "sandbox_path": "/etc/resolv.conf",
                    "mode": "ro",
                }],
            },
        })

        args = BwrapConfig.from_policy(policy, ["true"]).to_args()

        assert not _has_arg_pair(args, "--dir", "/etc")
        assert _has_arg_pair(args, "--tmpfs", "/etc")
        assert _has_mount(args, "--ro-bind", "/etc/resolv.conf", "/etc/resolv.conf")
        assert _has_arg_pair(args, "--remount-ro", "/etc")


class TestNetworkIptables:
    @staticmethod
    def test_iptables_backend_falls_back_to_legacy(monkeypatch):
        select_iptables_binary = getattr(network_module, "_select_iptables_binary")
        select_iptables_binary.cache_clear()

        def fake_candidates(ip_version):
            assert ip_version == 4
            return [
                network_module.IPTABLES_BINARY,
                network_module.IPTABLES_LEGACY_BINARY,
            ]

        def fake_run(binary, args, *, check=True, namespace=None):
            if binary == network_module.IPTABLES_BINARY:
                return network_module.subprocess.CompletedProcess(
                    args=[binary, *args],
                    returncode=3,
                    stdout="",
                    stderr="iptables-nft failed",
                )
            return network_module.subprocess.CompletedProcess(
                args=[binary, *args],
                returncode=0,
                stdout="",
                stderr="",
            )

        monkeypatch.setattr(network_module, "_iptables_candidates", fake_candidates)
        monkeypatch.setattr(network_module, "_run_iptables_binary", fake_run)

        assert select_iptables_binary(4, "test-netns") == (
            network_module.IPTABLES_LEGACY_BINARY
        )

    @staticmethod
    def test_iptables_backend_error_includes_stderr(monkeypatch):
        select_iptables_binary = getattr(network_module, "_select_iptables_binary")
        select_iptables_binary.cache_clear()

        def fake_candidates(ip_version):
            assert ip_version == 4
            return [network_module.IPTABLES_BINARY]

        def fake_run(binary, args, *, check=True, namespace=None):
            return network_module.subprocess.CompletedProcess(
                args=[binary, *args],
                returncode=3,
                stdout="",
                stderr="kernel/userspace mismatch",
            )

        monkeypatch.setattr(network_module, "_iptables_candidates", fake_candidates)
        monkeypatch.setattr(network_module, "_run_iptables_binary", fake_run)

        with pytest.raises(network_module.NetworkSetupError) as exc_info:
            select_iptables_binary(4, "test-netns")

        assert "kernel/userspace mismatch" in str(exc_info.value)


class TestSandboxExec:
    @staticmethod
    def test_exec_requires_running_sandbox(client):
        create_resp = client.post("/api/v1/sandboxes", json={})
        sandbox_id = create_resp.json()["id"]

        # Stop it first
        client.post(f"/api/v1/sandboxes/{sandbox_id}/stop")

        resp = client.post(f"/api/v1/sandboxes/{sandbox_id}/exec", json={
            "command": ["echo", "hello"],
        })
        assert resp.status_code == 409


class TestSandboxListing:
    @staticmethod
    def test_list_returns_all_sandboxes(client):
        for i in range(3):
            client.post("/api/v1/sandboxes", json={})

        resp = client.get("/api/v1/sandboxes")
        assert resp.status_code == 200
        assert len(resp.json()) == 3
