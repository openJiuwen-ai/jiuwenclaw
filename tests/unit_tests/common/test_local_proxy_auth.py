# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""local_proxy_auth 单测：本地代理判定 + Bearer 令牌注入（令牌缺失时缺省不带）。"""

from __future__ import annotations

import pytest

import jiuwenswarm.common.secrets_bootstrap as secrets_bootstrap
from jiuwenswarm.common.local_proxy_auth import is_local_proxy_url, with_local_proxy_bearer


@pytest.fixture(autouse=True)
def _clean_secrets(monkeypatch):
    monkeypatch.setattr(secrets_bootstrap, "_SECRETS", {})
    yield


class TestIsLocalProxyUrl:
    @pytest.mark.parametrize(
        "url",
        [
            "np://claw-upload",
            "np://claw-upload/osms/v1/file/manager/prepare",
            "NP://Claw-Upload/x",
            "http://127.0.0.1:19692",
            "http://127.0.0.1:19692/osms/v1/file/manager/prepare",
            "http://localhost:19692/x",
            "https://localhost:19692/x",
            "http://[::1]:19692/x",
        ],
    )
    def test_local_forms(self, url: str) -> None:
        assert is_local_proxy_url(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            None,
            "",
            "https://lfhagmirror.hwcloudtest.cn:8450/osms/v1/file/manager/prepare",
            "http://10.33.87.20:8450/x",
            "not a url",
        ],
    )
    def test_non_local_forms(self, url) -> None:
        assert is_local_proxy_url(url) is False


class TestWithLocalProxyBearer:
    def test_named_pipe_with_token(self, monkeypatch) -> None:
        monkeypatch.setattr(secrets_bootstrap, "_SECRETS", {"uploadToken": "tok-1"})
        out = with_local_proxy_bearer({"x-uid": "1"}, "np://claw-upload/osms/x")
        assert out["Authorization"] == "Bearer tok-1"
        assert out["x-uid"] == "1"

    def test_loopback_http_with_token(self, monkeypatch) -> None:
        monkeypatch.setattr(secrets_bootstrap, "_SECRETS", {"uploadToken": "tok-2"})
        out = with_local_proxy_bearer({}, "http://127.0.0.1:19692/osms/x")
        assert out["Authorization"] == "Bearer tok-2"

    def test_external_url_never_gets_token(self, monkeypatch) -> None:
        monkeypatch.setattr(secrets_bootstrap, "_SECRETS", {"uploadToken": "tok-3"})
        base = {"x-uid": "1"}
        out = with_local_proxy_bearer(base, "https://obs.example.com/sig-put")
        assert "Authorization" not in out
        assert out == base

    def test_missing_token_omits_header(self) -> None:
        out = with_local_proxy_bearer({"x-uid": "1"}, "np://claw-upload/osms/x")
        assert "Authorization" not in out
        assert out == {"x-uid": "1"}

    def test_input_headers_not_mutated(self, monkeypatch) -> None:
        monkeypatch.setattr(secrets_bootstrap, "_SECRETS", {"uploadToken": "tok-4"})
        base = {"x-uid": "1"}
        with_local_proxy_bearer(base, "np://claw-upload")
        assert base == {"x-uid": "1"}
