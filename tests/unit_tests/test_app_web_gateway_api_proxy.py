from __future__ import annotations

from pathlib import Path

import pytest

from jiuwenswarm.channels.web.app_web import (
    _SpaStaticHandler,
    _inject_user_web_runtime_config,
)


def _handler(path: str) -> _SpaStaticHandler:
    handler = object.__new__(_SpaStaticHandler)
    handler.path = path
    handler.web_http_target = "http://gateway:19002"
    return handler


def test_gateway_api_is_classified_as_gateway_web_http_route() -> None:
    assert _handler("/gateway-api/v1/chat/completions")._is_web_http_route()


def test_gateway_api_prefix_is_rewritten_for_upstream(monkeypatch) -> None:
    handler = _handler("/gateway-api/v1/chat/completions?mode=work")
    captured: dict[str, str] = {}

    def proxy_http() -> None:
        captured["path"] = handler.path
        captured["target"] = handler.api_target

    monkeypatch.setattr(handler, "_proxy_http", proxy_http)
    handler._proxy_web_http()

    assert captured == {
        "path": "/api/v1/chat/completions?mode=work",
        "target": "http://gateway:19002",
    }
    assert handler.path == "/gateway-api/v1/chat/completions?mode=work"


@pytest.mark.parametrize(
    ("mode", "embedding"),
    [("personal", "false"), ("enterprise", "true")],
)
def test_user_web_runtime_mode_injection_preserves_property_names(
    mode: str,
    embedding: str,
) -> None:
    frontend_index = (
        Path(__file__).resolve().parents[2]
        / "jiuwenswarm"
        / "channels"
        / "web"
        / "frontend"
        / "index.html"
    ).read_text(encoding="utf-8")

    rendered = _inject_user_web_runtime_config(frontend_index, mode)

    assert f"window.__JIUWEN_USER_WEB_MODE__ = '{mode}'" in rendered
    assert "window.__JIUWEN_USER_WEB_EMBEDDING__" in rendered
    assert f"'{embedding}' === 'true'" in rendered
    assert "__JIUWEN_USER_WEB_MODE_VALUE__" not in rendered
    assert "__JIUWEN_USER_WEB_EMBEDDING_VALUE__" not in rendered
