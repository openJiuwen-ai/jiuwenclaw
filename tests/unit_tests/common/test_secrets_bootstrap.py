# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""secrets_bootstrap 单测：stdin 首帧密钥包读取 + 内存 vault 读取 + 失败回退。"""

from __future__ import annotations

import io
import json

import pytest

import jiuwenswarm.common.secrets_bootstrap as sb
from jiuwenswarm.common.np_transport import encode_frame


@pytest.fixture(autouse=True)
def _reset_module_state(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sb, "_SECRETS", {})
    monkeypatch.setattr(sb, "_LOADED", False)
    monkeypatch.setattr(sb, "_STDIN_BIN", None)
    yield


def _feed_stdin(monkeypatch: pytest.MonkeyPatch, data: bytes) -> None:
    monkeypatch.setattr(sb, "stdin_binary_stream", lambda: io.BytesIO(data))


class TestBootstrap:
    def test_reads_secrets_frame(self, monkeypatch: pytest.MonkeyPatch) -> None:
        secrets = {
            "e2aToken": "tok-1",
            "proxyKey": "mpk_x",
            "localAuth": {"ak": "ak1", "sk": "sk1", "agentId": "ag1"},
            "pipes": {"model": "\\\\.\\pipe\\claw-u-model"},
        }
        _feed_stdin(monkeypatch, encode_frame({"type": "secrets", "version": 1, "secrets": secrets}))

        got = sb.bootstrap_secrets_from_stdin(timeout=5)
        assert got == secrets
        assert sb.secrets_loaded() is True
        assert sb.get_secret("e2aToken") == "tok-1"
        assert sb.get_secret("localAuth.sk") == "sk1"
        assert sb.get_secret("pipes.model") == "\\\\.\\pipe\\claw-u-model"
        assert sb.get_secret("missing", "fallback") == "fallback"
        assert sb.get_secret("localAuth.missing") is None

    def test_repeated_bootstrap_is_idempotent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _feed_stdin(monkeypatch, encode_frame({"type": "secrets", "secrets": {"k": 1}}))
        first = sb.bootstrap_secrets_from_stdin(timeout=5)
        # 第二次不再读 stdin（即使底层流已空）
        second = sb.bootstrap_secrets_from_stdin(timeout=5)
        assert first == second == {"k": 1}

    def test_non_secrets_frame_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _feed_stdin(monkeypatch, encode_frame({"type": "other", "x": 1}))
        got = sb.bootstrap_secrets_from_stdin(timeout=5)
        assert got == {}
        assert sb.secrets_loaded() is False

    def test_invalid_json_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        body = b"not-json"
        _feed_stdin(monkeypatch, len(body).to_bytes(4, "little") + body)
        assert sb.bootstrap_secrets_from_stdin(timeout=5) == {}

    def test_oversize_frame_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _feed_stdin(monkeypatch, (FRAME_HUGE := (64 * 2**20)).to_bytes(4, "little"))
        assert sb.bootstrap_secrets_from_stdin(timeout=5) == {}

    def test_eof_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _feed_stdin(monkeypatch, b"")
        assert sb.bootstrap_secrets_from_stdin(timeout=5) == {}

    def test_frame_format_matches_desktop_codec(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """帧格式与桌面侧 length-prefix.ts 手工对齐：4B LE 前缀 + JSON。"""
        body = json.dumps({"type": "secrets", "secrets": {"a": "b"}}, separators=(",", ":")).encode()
        _feed_stdin(monkeypatch, len(body).to_bytes(4, "little") + body)
        assert sb.bootstrap_secrets_from_stdin(timeout=5) == {"a": "b"}
