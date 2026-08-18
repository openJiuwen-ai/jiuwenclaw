# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Desktop startup state and per-session diagnostic behavior."""

from __future__ import annotations

import sys
import time
import types
from pathlib import Path

import pytest

from jiuwenswarm.common.startup_diagnostics import write_startup_failure
from jiuwenswarm.instance_manager.config import calculate_instance_ports


@pytest.fixture
def desktop_app(monkeypatch):
    if "webview" not in sys.modules:
        monkeypatch.setitem(sys.modules, "webview", types.ModuleType("webview"))
    sys.modules.pop("jiuwenswarm.channels.desktop.desktop_app", None)
    from jiuwenswarm.channels.desktop import desktop_app as mod

    return mod


def _runtime(desktop_app, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(desktop_app, "get_logs_dir", lambda: tmp_path / "logs")
    return desktop_app.DesktopRuntime(
        frontend_host="127.0.0.1",
        ports=calculate_instance_ports(0),
    )


def test_child_env_is_scoped_to_current_startup_session(
    desktop_app, tmp_path: Path
) -> None:
    session_dir = tmp_path / "session"
    env = desktop_app._build_child_env(
        "app",
        calculate_instance_ports(0),
        startup_diagnostics_dir=session_dir,
    )

    assert env[desktop_app.STARTUP_DIAGNOSTICS_DIR_ENV] == str(session_dir)


def test_failed_status_reports_correlated_system_dependency(
    desktop_app, tmp_path: Path, monkeypatch
) -> None:
    runtime = _runtime(desktop_app, tmp_path, monkeypatch)
    try:
        raise ImportError("DLL load failed while importing cygrpc")
    except ImportError as exc:
        write_startup_failure(
            exc,
            argv=["jiuwenswarm.exe", "--desktop-run-agent"],
            diagnostics_dir=runtime._startup_diagnostics_dir,
        )
    doctor_result = {
        "status": "environment_error",
        "checks": [
            {
                "name": "dbghelp.dll",
                "display_name": "Windows 系统库 dbghelp.dll",
                "status": "failed",
                "message": "OSError: missing",
            },
            {
                "name": "grpc._cython.cygrpc",
                "display_name": "gRPC Native 扩展 cygrpc",
                "kind": "native_import",
                "status": "failed",
                "message": "ImportError: DLL load failed",
            },
        ],
    }

    status = runtime._build_failed_status(RuntimeError("app exited 1"), doctor_result)

    assert status["title"] == "运行环境缺少必要组件"
    assert status["component"] == "dbghelp.dll"
    assert "missing" in status["message"]


def test_unrelated_doctor_failure_does_not_replace_known_child_failure(
    desktop_app, tmp_path: Path, monkeypatch
) -> None:
    runtime = _runtime(desktop_app, tmp_path, monkeypatch)
    try:
        raise RuntimeError("port 7860 is already in use")
    except RuntimeError as exc:
        write_startup_failure(
            exc,
            argv=["jiuwenswarm.exe", "--desktop-run-web"],
            diagnostics_dir=runtime._startup_diagnostics_dir,
        )
    doctor_result = {
        "status": "environment_error",
        "checks": [
            {
                "name": "faiss",
                "display_name": "FAISS Native 扩展",
                "kind": "native_import",
                "status": "failed",
                "message": "ImportError: unrelated",
            }
        ],
    }

    status = runtime._build_failed_status(RuntimeError("wrapper"), doctor_result)

    assert status["component"] == "web"
    assert "port 7860" in status["message"]
    assert "FAISS" not in status["message"]


def test_unrelated_doctor_failure_does_not_replace_native_child_failure(
    desktop_app, tmp_path: Path, monkeypatch
) -> None:
    runtime = _runtime(desktop_app, tmp_path, monkeypatch)
    try:
        raise ImportError("DLL load failed while importing cygrpc")
    except ImportError as exc:
        write_startup_failure(
            exc,
            argv=["jiuwenswarm.exe", "--desktop-run-agent"],
            diagnostics_dir=runtime._startup_diagnostics_dir,
        )
    doctor_result = {
        "status": "environment_error",
        "checks": [
            {
                "name": "faiss",
                "display_name": "FAISS Native 扩展",
                "kind": "native_import",
                "status": "failed",
                "message": "ImportError: unrelated",
            }
        ],
    }

    status = runtime._build_failed_status(RuntimeError("wrapper"), doctor_result)

    assert status["component"] == "agent"
    assert "cygrpc" in status["message"]
    assert "FAISS" not in status["message"]


def test_runtime_doctor_trigger_requires_native_evidence_or_unexplained_exit(
    desktop_app, tmp_path: Path, monkeypatch
) -> None:
    runtime = _runtime(desktop_app, tmp_path, monkeypatch)
    monkeypatch.setattr(desktop_app.sys, "frozen", True, raising=False)

    assert runtime._should_run_startup_doctor(
        RuntimeError("app exited early with code 1"),
        None,
    )
    assert runtime._should_run_startup_doctor(
        RuntimeError("wrapper"),
        {
            "error_type": "ImportError",
            "message": "DLL load failed while importing cygrpc",
        },
    )
    assert not runtime._should_run_startup_doctor(
        RuntimeError("Timed out waiting for http://127.0.0.1:7860"),
        {
            "error_type": "RuntimeError",
            "message": "port 7860 is already in use",
        },
    )


def test_failed_status_reads_only_runtime_session(
    desktop_app, tmp_path: Path, monkeypatch
) -> None:
    runtime = _runtime(desktop_app, tmp_path, monkeypatch)
    stale_dir = runtime._startup_diagnostics_dir.parent / "stale-session"
    try:
        raise ImportError("stale cygrpc error")
    except ImportError as exc:
        write_startup_failure(
            exc,
            argv=["jiuwenswarm.exe", "--desktop-run-agent"],
            diagnostics_dir=stale_dir,
        )
    try:
        raise RuntimeError("current web error")
    except RuntimeError as exc:
        write_startup_failure(
            exc,
            argv=["jiuwenswarm.exe", "--desktop-run-web"],
            diagnostics_dir=runtime._startup_diagnostics_dir,
        )

    status = runtime._build_failed_status(RuntimeError("wrapper"), None)

    assert status["component"] == "web"
    assert "current web error" in status["message"]
    assert "stale cygrpc" not in status["message"]


def test_startup_status_is_terminal_after_failure(
    desktop_app, tmp_path: Path, monkeypatch
) -> None:
    runtime = _runtime(desktop_app, tmp_path, monkeypatch)
    runtime._set_startup_status("diagnosing", message="checking")
    runtime._set_startup_status("failed", message="failed")
    runtime._set_startup_status("ready", message="late ready")

    status = runtime.get_startup_status()
    assert status["state"] == "failed"
    assert status["message"] == "failed"


def test_process_started_during_shutdown_is_terminated(
    desktop_app, tmp_path: Path, monkeypatch
) -> None:
    runtime = _runtime(desktop_app, tmp_path, monkeypatch)
    fake_process = object()
    terminated = []
    monkeypatch.setattr(desktop_app, "_start_process", lambda *_args, **_kwargs: fake_process)
    monkeypatch.setattr(desktop_app, "_terminate_process_tree", terminated.append)
    runtime._is_shutting_down = True

    with pytest.raises(RuntimeError, match="startup cancelled"):
        runtime._start_managed_process("web", ["fake-web"])

    assert terminated == [fake_process]
    assert "web" not in runtime.processes


def test_loading_page_polls_state_and_has_bridge_watchdog(desktop_app) -> None:
    html = desktop_app.DesktopRuntime._build_loading_html()

    assert "get_startup_status" in html
    assert "status.state==='failed'" in html
    assert "desktop-webview-bridge" in html
    assert "desktop-navigation" in html
    assert "window.location.replace(status.frontend_url)" in html


def test_desktop_doctor_timeout_leaves_supervisor_cleanup_margin(desktop_app) -> None:
    assert (
        desktop_app.STARTUP_DOCTOR_TIMEOUT_SECONDS
        > desktop_app.DOCTOR_TIMEOUT_SECONDS
    )


def test_service_failure_transitions_loading_state_without_worker_window_calls(
    desktop_app, tmp_path: Path, monkeypatch
) -> None:
    runtime = _runtime(desktop_app, tmp_path, monkeypatch)

    class FakeEvent:
        def __iadd__(self, _handler):
            return self

    class FakeWindow:
        def __init__(self) -> None:
            self.events = types.SimpleNamespace(loaded=FakeEvent(), closed=FakeEvent())

    fake_window = FakeWindow()
    monkeypatch.setattr(desktop_app, "get_user_workspace_dir", lambda: tmp_path)
    monkeypatch.setattr(
        desktop_app.webview,
        "create_window",
        lambda *_args, **_kwargs: fake_window,
        raising=False,
    )

    def start_webview(**_kwargs) -> None:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if runtime.get_startup_status()["state"] == "failed":
                return
            time.sleep(0.01)
        raise AssertionError("startup failure did not reach the UI state")

    def fail_services() -> None:
        raise RuntimeError("child exited early")

    monkeypatch.setattr(desktop_app.webview, "start", start_webview, raising=False)
    monkeypatch.setattr(runtime, "_clear_wkwebview_system_cache", lambda: None)
    monkeypatch.setattr(runtime, "start_services", fail_services)
    doctor_calls = []
    monkeypatch.setattr(
        runtime,
        "_run_doctor_after_failure",
        lambda: doctor_calls.append(True),
    )
    monkeypatch.setattr(runtime, "shutdown", lambda: None)

    runtime.run(window_title="test", width=1200, height=800, debug=False)

    status = runtime.get_startup_status()
    assert status["state"] == "failed"
    assert "child exited early" in status["message"]
    assert doctor_calls == []


def test_installer_runs_doctor_before_offering_launch() -> None:
    installer = (
        Path(__file__).resolve().parents[2] / "scripts" / "installer.iss"
    ).read_text(encoding="utf-8")

    assert "ExecAsOriginalUser" in installer
    assert "ewWaitUntilTerminated" in installer
    assert "--doctor --doctor-output" in installer
    assert "Check: DoctorPassed" in installer
