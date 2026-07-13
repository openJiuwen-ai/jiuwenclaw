"""Reliability tests for sandbox service."""

from __future__ import annotations

import os
import time

import pytest

from tests.system_tests.test_utils import ChaosInjector


def _docker_restart_policy(container_name: str) -> str | None:
    """Query docker container restart policy without using subprocess."""
    cmd = ["/usr/bin/docker", "inspect", "--format", "{{.HostConfig.RestartPolicy.Name}}", container_name]
    if not os.path.exists(cmd[0]):
        return None
    r_fd, w_fd = os.pipe()
    child_pid = os.fork()
    if child_pid == 0:
        os.close(r_fd)
        os.dup2(w_fd, 1)
        os.dup2(w_fd, 2)
        os.close(w_fd)
        try:
            os.execv(cmd[0], cmd)
        except OSError:
            os._exit(127)
    else:
        os.close(w_fd)
        chunks = []
        while True:
            data = os.read(r_fd, 4096)
            if not data:
                break
            chunks.append(data.decode("utf-8", errors="replace"))
        os.close(r_fd)
        _pid, status = os.waitpid(child_pid, 0)
        if os.WEXITSTATUS(status) != 0:
            return None
        return "".join(chunks).strip() or None


@pytest.mark.system
@pytest.mark.slow
class TestReliability:
    """Reliability tests."""

    @staticmethod
    def test_rel_001_sandbox_process_auto_restart(client, wait_for_sandbox_ready, exec_command):
        resp = client.post("/api/v1/sandboxes", json={})
        assert resp.status_code == 201, f"Failed to create sandbox: {resp.text}"
        sandbox_id = resp.json()["id"]
        wait_for_sandbox_ready(sandbox_id)

        chaos = ChaosInjector(client)
        chaos.kill_sandbox_daemon(sandbox_id)

        time.sleep(10)
        resp = exec_command(sandbox_id, ["echo", "restarted"], timeout_seconds=30)
        assert resp.status_code == 200
        data = resp.json()
        assert "exit_code" in data, f"Missing exit_code in response: {data}"
        assert data["exit_code"] == 0

    @staticmethod
    def test_rel_002_container_process_auto_restart(client, wait_for_sandbox_ready, exec_command):
        resp = client.post("/api/v1/sandboxes", json={})
        assert resp.status_code == 201, f"Failed to create sandbox: {resp.text}"
        sandbox_id = resp.json()["id"]
        wait_for_sandbox_ready(sandbox_id)

        restart_policy = _docker_restart_policy("jiuwenbox-server")
        if restart_policy is None:
            pytest.skip("Docker or jiuwenbox-server container not available")
        if restart_policy not in ("always", "unless-stopped"):
            pytest.skip(
                f"jiuwenbox-server restart policy is {restart_policy!r}; "
                "need 'always' or 'unless-stopped' for this test"
            )

        try:
            ret = os.spawnl(os.P_WAIT, "/usr/bin/docker", "/usr/bin/docker", "kill", "jiuwenbox-server")
            if ret != 0:
                pytest.skip("Docker container not running")
        except OSError:
            pytest.skip("Docker not available")

        time.sleep(30)

        health_resp = client.get("/health")
        assert health_resp.status_code == 200

    @staticmethod
    def test_rel_003_exec_timeout_kill(client, wait_for_sandbox_ready):
        resp = client.post("/api/v1/sandboxes", json={})
        assert resp.status_code == 201, f"Failed to create sandbox: {resp.text}"
        sandbox_id = resp.json()["id"]
        wait_for_sandbox_ready(sandbox_id)

        resp = client.post(
            f"/api/v1/sandboxes/{sandbox_id}/exec",
            json={"command": ["sleep", "120"], "timeout_seconds": 10},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "exit_code" in data, f"Missing exit_code in response: {data}"
        assert data["exit_code"] != 0

        status = client.get(f"/api/v1/sandboxes/{sandbox_id}")
        assert status.status_code == 200, f"Failed to get sandbox status: {status.text}"
        status_data = status.json()
        assert "phase" in status_data, f"Missing phase in response: {status_data}"
        assert status_data["phase"] == "ready"

    @staticmethod
    def test_rel_004_scenario1_url_valid_instance_invalid(client, wait_for_sandbox_ready, exec_command):
        resp = client.post("/api/v1/sandboxes", json={})
        assert resp.status_code == 201, f"Failed to create sandbox: {resp.text}"
        sandbox_id = resp.json()["id"]
        wait_for_sandbox_ready(sandbox_id)

        chaos = ChaosInjector(client)
        for _ in range(3):
            chaos.kill_sandbox_daemon(sandbox_id)
            time.sleep(2)

        time.sleep(10)
        resp = exec_command(sandbox_id, ["echo", "retry-success"], timeout_seconds=30)
        assert resp.status_code == 200
        data = resp.json()
        assert "exit_code" in data, f"Missing exit_code in response: {data}"
        assert data["exit_code"] == 0

    @staticmethod
    def test_rel_005_scenario2_url_valid_instance_abnormal(client, wait_for_sandbox_ready, exec_command):
        resp = client.post("/api/v1/sandboxes", json={})
        assert resp.status_code == 201, f"Failed to create sandbox: {resp.text}"
        sandbox_id = resp.json()["id"]
        wait_for_sandbox_ready(sandbox_id)

        chaos = ChaosInjector(client)
        chaos.stop_sandbox_daemon(sandbox_id)

        time.sleep(5)
        resp = exec_command(sandbox_id, ["echo", "fallback-test"], timeout_seconds=30)
        assert resp.status_code == 200

        chaos.restart_sandbox_daemon(sandbox_id)

    @staticmethod
    def test_rel_006_scenario3_url_unreachable(client, wait_for_sandbox_ready, exec_command):
        resp = client.post("/api/v1/sandboxes", json={})
        assert resp.status_code == 201, f"Failed to create sandbox: {resp.text}"
        sandbox_id = resp.json()["id"]
        wait_for_sandbox_ready(sandbox_id)

        chaos = ChaosInjector(client)
        chaos.disconnect_network()

        try:
            resp = exec_command(sandbox_id, ["echo", "unreachable-test"], timeout_seconds=30)
            assert resp.status_code in (200, 408, 503)
        finally:
            chaos.reconnect_network()

        time.sleep(5)
        status = client.get(f"/api/v1/sandboxes/{sandbox_id}")
        assert status.status_code == 200, f"Failed to get sandbox status: {status.text}"
        status_data = status.json()
        assert "phase" in status_data, f"Missing phase in response: {status_data}"
        assert status_data["phase"] == "ready"

    @staticmethod
    def test_rel_007_scenario4_container_service_crash(client, wait_for_sandbox_ready, exec_command):
        resp = client.post("/api/v1/sandboxes", json={})
        assert resp.status_code == 201, f"Failed to create sandbox: {resp.text}"
        sandbox_id = resp.json()["id"]
        wait_for_sandbox_ready(sandbox_id)

        from tests.system_tests.test_utils import ResourceDegradationManager

        rdm = ResourceDegradationManager()
        try:
            rdm.degrade_memory(vm_bytes="22G", duration=60)
            chaos = ChaosInjector(client)
            chaos.trigger_oom(sandbox_id, memory_gb=22)
        finally:
            rdm.cleanup()

        time.sleep(30)
        health_resp = client.get("/health")
        assert health_resp.status_code == 200

    @staticmethod
    def test_rel_008_resource_cleanup_on_stop(client, wait_for_sandbox_ready):
        resp = client.post("/api/v1/sandboxes", json={})
        assert resp.status_code == 201, f"Failed to create sandbox: {resp.text}"
        sandbox_id = resp.json()["id"]
        wait_for_sandbox_ready(sandbox_id)

        stop_resp = client.post(f"/api/v1/sandboxes/{sandbox_id}/stop")
        assert stop_resp.status_code == 200, f"Failed to stop sandbox: {stop_resp.text}"
        stop_data = stop_resp.json()
        assert "phase" in stop_data, f"Missing phase in response: {stop_data}"
        assert stop_data["phase"] == "stopped"

        get_resp = client.get(f"/api/v1/sandboxes/{sandbox_id}")
        assert get_resp.status_code == 200, f"Failed to get sandbox status: {get_resp.text}"
        get_data = get_resp.json()
        assert "phase" in get_data, f"Missing phase in response: {get_data}"
        assert get_data["phase"] == "stopped"

        delete_resp = client.delete(f"/api/v1/sandboxes/{sandbox_id}")
        assert delete_resp.status_code == 204

        get_resp = client.get(f"/api/v1/sandboxes/{sandbox_id}")
        assert get_resp.status_code == 404

    @staticmethod
    def test_rel_009_idle_reaper_auto_cleanup(client, restore_timeout):
        client.put("/api/v1/timeout", json={"idle_timeout": 10, "idle_check_interval": 5})

        resp = client.post("/api/v1/sandboxes", json={})
        assert resp.status_code == 201, f"Failed to create sandbox: {resp.text}"
        sandbox_id = resp.json()["id"]

        time.sleep(15)

        get_resp = client.get(f"/api/v1/sandboxes/{sandbox_id}")
        assert get_resp.status_code == 404

    @staticmethod
    def test_rel_010_zombie_reaper_cleanup(client, wait_for_sandbox_ready, exec_command):
        resp = client.post("/api/v1/sandboxes", json={})
        assert resp.status_code == 201, f"Failed to create sandbox: {resp.text}"
        sandbox_id = resp.json()["id"]
        wait_for_sandbox_ready(sandbox_id)

        resp = exec_command(
            sandbox_id,
            ["python3", "-c", "import os; os.fork(); os._exit(0)"],
            timeout_seconds=10,
        )
        assert resp.status_code == 200

        time.sleep(5)
        resp = exec_command(
            sandbox_id,
            ["ps", "aux"],
            timeout_seconds=10,
        )
        assert resp.status_code == 200
        output = resp.json().get("stdout") or ""
        zombie_count = sum(1 for line in output.splitlines() if "<defunct>" in line)
        assert zombie_count == 0