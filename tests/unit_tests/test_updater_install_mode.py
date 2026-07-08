import sys

from jiuwenswarm.common import updater


def test_desktop_env_forces_desktop_install_mode(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setenv("JIUWENSWARM_DESKTOP", "1")

    assert updater._detect_install_mode() == "desktop"


def test_desktop_env_keeps_gitcode_release_source(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setenv("JIUWENSWARM_DESKTOP", "1")
    monkeypatch.setattr(
        updater,
        "get_config_raw",
        lambda: {
            "updater": {
                "enabled": True,
                "desktop_release_api_type": "gitcode",
                "repo_owner": "openJiuwen",
                "repo_name": "jiuwenswarm",
                "release_api_url": "",
                "pypi_mirror": "https://mirrors.aliyun.com/pypi",
            }
        },
    )

    config = updater.UpdaterService._load_config()

    assert config["install_mode"] == "desktop"
    assert config["release_api_type"] == "gitcode"
    assert config["release_api_url"] == (
        "https://api.gitcode.com/api/v5/repos/openJiuwen/jiuwenswarm/releases/latest"
    )
