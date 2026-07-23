# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from types import SimpleNamespace

import pytest

from jiuwenswarm.agents.harness.common.tools import browser_start_client


class _FakeProcess:
    def __init__(self, pid: int, ppid: int, executable: str, args: list[str]) -> None:
        self.pid = pid
        self.info = {"pid": pid, "ppid": ppid, "exe": executable, "cmdline": args}


def test_process_discovery_requires_matching_executable_port_and_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    chrome = str(tmp_path / "chrome.exe")
    profile = str(tmp_path / "profile")
    processes = [
        _FakeProcess(
            10,
            1,
            chrome,
            [chrome, "--remote-debugging-port=9222", f"--user-data-dir={profile}"],
        ),
        _FakeProcess(
            11,
            1,
            chrome,
            [
                chrome,
                "--remote-debugging-port=9222",
                f"--user-data-dir={tmp_path / 'other'}",
            ],
        ),
        _FakeProcess(
            12,
            1,
            chrome,
            [chrome, "--remote-debugging-port=9333", f"--user-data-dir={profile}"],
        ),
        _FakeProcess(
            13,
            1,
            str(tmp_path / "python.exe"),
            ["python.exe", "--remote-debugging-port=9222", f"--user-data-dir={profile}"],
        ),
    ]
    monkeypatch.setattr(browser_start_client.psutil, "process_iter", lambda _attrs: processes)

    result = browser_start_client._find_existing_browser_processes(
        chrome_exec=chrome,
        port=9222,
        user_data_dir=profile,
    )

    assert [process.pid for process in result] == [10]


@pytest.mark.parametrize(
    ("headless", "expected_headless_arg"),
    [(False, False), (True, True)],
)
def test_start_browser_restarts_with_current_headless_setting(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    headless: bool,
    expected_headless_arg: bool,
) -> None:
    chrome = str(tmp_path / "chrome.exe")
    profile = str(tmp_path / "profile")
    events: list[object] = []
    monkeypatch.setattr(
        browser_start_client,
        "_load_browser_config",
        lambda _path: {
            "chrome_path": chrome,
            "remote_debugging_address": "127.0.0.1",
            "remote_debugging_port": 9222,
            "user_data_dir": profile,
            "profile_directory": "Default",
            "headless": headless,
        },
    )
    monkeypatch.setattr(browser_start_client, "_os_key", lambda: "windows")
    monkeypatch.setattr(browser_start_client, "_normalize_chrome_executable", lambda *_: chrome)
    monkeypatch.setattr(browser_start_client, "_parse_cdp_from_env", lambda host, port: (host, port))
    monkeypatch.setattr(
        browser_start_client,
        "_stop_existing_browser_service",
        lambda **kwargs: events.append(("stop", kwargs)) or [123],
    )
    monkeypatch.setattr(browser_start_client, "_port_is_open", lambda *_, **__: False)
    monkeypatch.setattr(browser_start_client, "_persist_browser_profile", lambda **_: None)

    def fake_popen(args, **kwargs):
        events.append(("popen", args, kwargs))
        return SimpleNamespace(pid=456)

    monkeypatch.setattr(browser_start_client.subprocess, "Popen", fake_popen)

    assert browser_start_client.start_browser(config_file="config.yaml") == 0
    assert events[0][0] == "stop"
    assert ("--headless=new" in events[1][1]) is expected_headless_arg


def test_start_browser_refuses_unrecognized_port_owner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    chrome = str(tmp_path / "chrome.exe")
    monkeypatch.setattr(
        browser_start_client,
        "_load_browser_config",
        lambda _path: {"chrome_path": chrome, "headless": False},
    )
    monkeypatch.setattr(browser_start_client, "_os_key", lambda: "windows")
    monkeypatch.setattr(browser_start_client, "_normalize_chrome_executable", lambda *_: chrome)
    monkeypatch.setattr(browser_start_client, "_parse_cdp_from_env", lambda host, port: (host, port))
    monkeypatch.setattr(browser_start_client, "_stop_existing_browser_service", lambda **_: [])
    monkeypatch.setattr(browser_start_client, "_port_is_open", lambda *_, **__: True)
    monkeypatch.setattr(
        browser_start_client.subprocess,
        "Popen",
        lambda *_, **__: pytest.fail("Popen must not run for an unknown port owner"),
    )

    with pytest.raises(RuntimeError, match="occupied by an unrecognized process"):
        browser_start_client.start_browser(config_file="config.yaml")
