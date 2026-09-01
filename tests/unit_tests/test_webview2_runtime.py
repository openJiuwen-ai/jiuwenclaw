# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""WebView2 installer prerequisite tests."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_installer_downloads_webview2_without_blocking_main_install():
    installer = (ROOT / "scripts" / "installer.iss").read_text(encoding="utf-8")
    build_script = (ROOT / "scripts" / "build-exe.ps1").read_text(encoding="utf-8")
    pyinstaller_spec = (ROOT / "scripts" / "jiuwenswarm.spec").read_text(encoding="utf-8")

    assert "BuildWebView2InstallerPath" not in installer
    assert "MicrosoftEdgeWebView2RuntimeInstallerX64.exe" in installer
    assert "https://go.microsoft.com/fwlink/?linkid=2124701" in installer
    assert "function PrepareToInstall" in installer
    assert "CreateDownloadPage" in installer
    assert "WebView2DownloadPage.Add" in installer
    assert "WebView2DownloadPage.Download" in installer
    assert "WebView2DownloadPage.AbortedByUser" in installer
    assert "WebView2DownloadPage.AbortButton.Caption := '取消下载'" in installer
    assert "请检查网络、代理或防火墙设置" in installer
    assert "CreateOutputMarqueeProgressPage" in installer
    assert "WebView2InstallPage.Show" in installer
    assert "WebView2InstallPage.Hide" in installer
    assert "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" in installer
    assert "GetWebView2RuntimeVersion" in installer
    assert "RegQueryStringValue(HKLM32, Key, 'pv', Version)" in installer
    assert "RegQueryStringValue(HKCU, Key, 'pv', Version)" in installer
    assert "InstallParameters := '/silent /install'" in installer
    assert "InstallParameters := '/install'" in installer
    assert "SW_SHOWNORMAL" in installer
    assert "Get-AuthenticodeSignature" in installer
    assert "O=Microsoft Corporation" in installer
    assert "Web 端仍可正常使用" in installer
    assert "将继续安装" in installer
    assert "WebView2InstallerPath" not in build_script
    assert "uv sync --extra dev" in build_script
    assert "--extra codex" not in build_script
    assert "--extra claude" not in build_script

    excludes = pyinstaller_spec.split("excludes = [", maxsplit=1)[1].split("]", maxsplit=1)[0]
    for optional_runtime in ("claude_agent_sdk", "openai_codex", "codex_cli_bin"):
        assert f'"{optional_runtime}"' in excludes
        assert f'collect_all("{optional_runtime}")' not in pyinstaller_spec
        assert f'collect_submodules("{optional_runtime}")' not in pyinstaller_spec
