from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace


def test_desktop_external_url_is_discoverable_static_api_and_callable(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("JIUWENSWARM_DATA_DIR", str(tmp_path))
    monkeypatch.setitem(sys.modules, "webview", SimpleNamespace())
    module = importlib.import_module("jiuwenswarm.channels.desktop.desktop_app")

    descriptor = module._WindowApi.__dict__["open_external_url"]
    assert isinstance(descriptor, staticmethod)

    opened: list[tuple[str, int, bool]] = []

    def record_open(url: str, *, new: int, autoraise: bool) -> bool:
        opened.append((url, new, autoraise))
        return True

    monkeypatch.setattr(module.webbrowser, "open", record_open)
    api = module._WindowApi(runtime=object())

    assert api.open_external_url("https://auth.openai.com/codex/device") is True
    assert opened == [("https://auth.openai.com/codex/device", 2, True)]
    assert api.open_external_url("https://example.test/not-allowed") is False
    assert len(opened) == 1
