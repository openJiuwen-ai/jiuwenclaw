#!/usr/bin/env python3
"""Check system permissions and capabilities."""

import os
import sys
import tempfile
import subprocess


def check_disk():
    """Check disk access permissions."""
    results = []

    # Current directory
    try:
        test_file = ".perm_check_tmp"
        with open(test_file, "w") as f:
            f.write("test")
        os.remove(test_file)
        results.append(("Disk", True, "Read/Write access to current directory"))
    except (PermissionError, OSError) as e:
        results.append(("Disk", False, f"No write access to current directory: {e}"))

    # /tmp directory
    try:
        with tempfile.NamedTemporaryFile(delete=True) as f:
            f.write(b"test")
        results.append(("Disk", True, "Read/Write access to temp directory"))
    except (PermissionError, OSError) as e:
        results.append(("Disk", False, f"No access to temp directory: {e}"))

    return results


def check_network():
    """Check network capabilities."""
    results = []

    # DNS resolution
    try:
        import socket
        socket.getaddrinfo("example.com", 80)
        results.append(("Network", True, "DNS resolution working"))
    except (socket.gaierror, OSError) as e:
        results.append(("Network", False, f"DNS resolution failed: {e}"))

    # HTTP connection
    try:
        import urllib.request
        req = urllib.request.Request("http://example.com", method="HEAD")
        with urllib.request.urlopen(req, timeout=5) as resp:
            results.append(("Network", True, f"HTTP outbound connection OK (status {resp.status})"))
    except Exception as e:
        results.append(("Network", False, f"HTTP outbound connection failed: {e}"))

    return results


def check_process():
    """Check process capabilities. Requires psutil."""
    results = []

    # Try to use psutil for process listing
    try:
        import psutil  # noqa: F401 - this import is the key check

        # List processes
        procs = list(psutil.process_iter(["pid", "name"]))
        results.append(("Process", True, f"Can list processes ({len(procs)} found)"))

        # CPU info
        cpu_count = psutil.cpu_count()
        results.append(("CPU", True, f"{cpu_count} cores, load avg: {os.getloadavg()}"))

        # Memory info
        mem = psutil.virtual_memory()
        total_gb = mem.total / (1024 ** 3)
        avail_gb = mem.available / (1024 ** 3)
        results.append(("Memory", True, f"Total: {total_gb:.1f}GB, Available: {avail_gb:.1f}GB"))

    except ImportError:
        # BUG TRIGGER: psutil is not installed in the sandbox environment
        # and the sandbox does not allow pip install
        results.append(("Process", False, "psutil not installed - cannot access detailed system info"))
        results.append(("CPU", False, "psutil not installed - cannot query CPU details"))
        results.append(("Memory", False, "psutil not installed - cannot query memory info"))

    # Child process creation (doesn't need psutil)
    try:
        result = subprocess.run(
            [sys.executable, "-c", "print('ok')"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            results.append(("Process", True, "Can create child processes"))
        else:
            results.append(("Process", False, f"Child process failed: {result.stderr}"))
    except Exception as e:
        results.append(("Process", False, f"Cannot create child processes: {e}"))

    return results


def main():
    checks = []

    if "--all" in sys.argv:
        checks = ["disk", "network", "process"]
    elif "--check" in sys.argv:
        i = 1
        while i < len(sys.argv):
            if sys.argv[i] == "--check" and i + 1 < len(sys.argv):
                checks.append(sys.argv[i + 1])
                i += 2
            else:
                i += 1
    else:
        checks = ["disk", "network", "process"]

    print("=== System Permission Check ===\n")

    all_results = []
    passed = 0
    failed = 0

    check_funcs = {
        "disk": check_disk,
        "network": check_network,
        "process": check_process,
    }

    for check_name in checks:
        if check_name in check_funcs:
            results = check_funcs[check_name]()
            all_results.extend(results)
        else:
            print(f"Unknown check: {check_name}")

    for category, success, message in all_results:
        icon = "✅" if success else "❌"
        print(f"[{category}] {icon} {message}")
        if success:
            passed += 1
        else:
            failed += 1

    total = passed + failed
    print(f"\nSummary: {passed}/{total} checks passed, {failed} failed")


if __name__ == "__main__":
    main()
