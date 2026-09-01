# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""WebView2 Evergreen Runtime prerequisite tests."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from jiuwenswarm.channels.desktop import webview2_runtime

ROOT = Path(__file__).resolve().parents[2]


class _RegistryKey:
    def __init__(self, value: object):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


class _FakeRegistry:
    HKEY_LOCAL_MACHINE = "machine"
    HKEY_CURRENT_USER = "user"

    def __init__(self, values: dict[tuple[str, str], object]):
        self.values = values

    def OpenKey(self, root: str, subkey: str):
        try:
            return _RegistryKey(self.values[(root, subkey)])
        except KeyError as exc:
            raise FileNotFoundError(subkey) from exc

    @staticmethod
    def QueryValueEx(key: _RegistryKey, name: str):
        if name != "pv":
            raise FileNotFoundError(name)
        return key.value, 1


@pytest.mark.parametrize("value", [None, "", "  ", "0.0.0.0"])
def test_empty_or_zero_version_is_not_registered(value: object):
    assert webview2_runtime._is_registered_version(value) is False


def test_machine_runtime_short_circuits_user_lookup():
    machine_location = webview2_runtime._WEBVIEW2_REGISTRY_LOCATIONS[0][1]
    registry = _FakeRegistry({("machine", machine_location): "152.0.4191.53"})

    assert webview2_runtime.is_webview2_runtime_registered(registry) is True


def test_user_runtime_is_used_when_machine_runtime_is_missing():
    user_location = webview2_runtime._WEBVIEW2_REGISTRY_LOCATIONS[1][1]
    registry = _FakeRegistry({("user", user_location): "152.0.4191.53"})

    assert webview2_runtime.is_webview2_runtime_registered(registry) is True


def test_runtime_is_missing_when_neither_registration_is_valid():
    machine_location = webview2_runtime._WEBVIEW2_REGISTRY_LOCATIONS[0][1]
    registry = _FakeRegistry({("machine", machine_location): "0.0.0.0"})

    assert webview2_runtime.is_webview2_runtime_registered(registry) is False


@pytest.fixture
def desktop_app(monkeypatch):
    if "webview" not in sys.modules:
        monkeypatch.setitem(sys.modules, "webview", types.ModuleType("webview"))
    sys.modules.pop("jiuwenswarm.channels.desktop.desktop_app", None)
    from jiuwenswarm.channels.desktop import desktop_app as module

    return module


def test_missing_runtime_stops_before_port_or_service_setup(desktop_app, monkeypatch):
    args = types.SimpleNamespace(desktop_install_update=False)
    monkeypatch.setattr(desktop_app, "_parse_args", lambda: args)
    monkeypatch.setattr(desktop_app, "_cleanup_stale_update_artifacts", lambda: None)
    monkeypatch.setattr(desktop_app, "_setup_tui_path", lambda: None)
    monkeypatch.setattr(
        desktop_app,
        "_ensure_webview2_runtime_before_services",
        lambda: False,
    )

    def unexpected_port_resolution():
        raise AssertionError("port and service setup must not start")

    monkeypatch.setattr(desktop_app, "resolve_desktop_ports", unexpected_port_resolution)

    with pytest.raises(SystemExit) as exc_info:
        desktop_app.main()

    assert exc_info.value.code == 1


def test_installer_embeds_and_verifies_webview2_prerequisite():
    installer = (ROOT / "scripts" / "installer.iss").read_text(encoding="utf-8")
    build_script = (ROOT / "scripts" / "build-exe.ps1").read_text(encoding="utf-8")
    pyinstaller_spec = (ROOT / "scripts" / "jiuwenswarm.spec").read_text(encoding="utf-8")
    runtime_check = (ROOT / "jiuwenswarm" / "channels" / "desktop" / "webview2_runtime.py").read_text(
        encoding="utf-8"
    )

    assert "BuildWebView2InstallerPath" in installer
    assert "Flags: dontcopy solidbreak nocompression" in installer
    assert "function PrepareToInstall" in installer
    assert "CreateOutputMarqueeProgressPage" in installer
    assert "WebView2ProgressPage.Show" in installer
    assert "WebView2ProgressPage.Hide" in installer
    assert "finally" in installer
    assert webview2_runtime.WEBVIEW2_RUNTIME_ID in installer
    assert "GetWebView2RuntimeVersion" in installer
    assert "RegQueryStringValue(HKLM32, Key, 'pv', Version)" in installer
    assert "RegQueryStringValue(HKCU, Key, 'pv', Version)" in installer
    assert "'/silent /install'" in installer
    assert "Get-AuthenticodeSignature" in build_script
    assert "O=Microsoft Corporation" in build_script
    assert "uv sync --extra dev" in build_script
    assert "--extra codex" not in build_script
    assert "--extra claude" not in build_script
    excludes = pyinstaller_spec.split("excludes = [", maxsplit=1)[1].split("]", maxsplit=1)[0]
    for optional_runtime in ("claude_agent_sdk", "openai_codex", "codex_cli_bin"):
        assert f'"{optional_runtime}"' in excludes
        assert f'collect_all("{optional_runtime}")' not in pyinstaller_spec
        assert f'collect_submodules("{optional_runtime}")' not in pyinstaller_spec
    assert "webbrowser" not in runtime_check
