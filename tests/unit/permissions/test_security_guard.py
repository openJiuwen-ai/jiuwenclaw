# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for ``security_guard`` (KIA + RMS checks).

Covers:
  * RMS byte detection (non-Office / ZIP OOXML / OLE2+DataSpaces / OLE2 no
    DataSpaces / unreadable).
  * KIA detection via ICPM — mocked HTTPConnection: disabled flag, no-KIA dir,
    match, no-match, connection failure (degrade-to-allow), non-200, and the
    request payload shape sent to ICPM.
  * Event-loop safety: the blocking HTTP call runs in a worker thread, not on
    the loop thread (regression test for the sync-in-async blocker).
  * Path helpers: ``_resolve_path`` (``..`` / relative), ``_get_parent_directory``,
    ``_normalize_for_kia_compare``, ``_is_file_in_kia_list``, ``_icpm_endpoint``,
    ``extract_file_path_from_tool_args``.
"""

from __future__ import annotations

import json
import os
import threading

import pytest

from jiuwenclaw.agentserver.permissions import security_guard as sg

# ── RMS test fixtures ──

_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ZIP_MAGIC = b"PK\x03\x04"
_DATASPACES_UTF16LE = "DataSpaces".encode("utf-16-le")


def _write_file(path, data: bytes) -> None:
    with open(path, "wb") as f:
        f.write(data)


# ── Fake ICPM HTTP connection ──


class _FakeResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body


class _FakeHTTPConnection:
    """Stand-in for ``http.client.HTTPConnection``.

    Class-level state so tests can configure the canned response / forced
    exception before invoking ``check_kia_file``; instances are recorded for
    post-call assertions (request payload, thread id).
    """

    response: _FakeResponse = _FakeResponse(
        200, b'{"status":"success","isExistKia":false}'
    )
    raise_on_request: Exception | None = None
    instances: list["_FakeHTTPConnection"] = []

    def __init__(self, host: str, port: int, timeout: float | None = None) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.request_method: str | None = None
        self.request_path: str | None = None
        self.request_body: str | None = None
        self.request_headers: dict | None = None
        self.thread_id: int | None = None
        self.closed = False
        _FakeHTTPConnection.instances.append(self)

    def request(self, method, path, body=None, headers=None) -> None:
        if _FakeHTTPConnection.raise_on_request is not None:
            raise _FakeHTTPConnection.raise_on_request
        self.thread_id = threading.get_ident()
        self.request_method = method
        self.request_path = path
        self.request_body = body
        self.request_headers = headers

    def getresponse(self) -> _FakeResponse:
        return _FakeHTTPConnection.response

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_icpm(monkeypatch: pytest.MonkeyPatch) -> type[_FakeHTTPConnection]:
    """Patch ``http.client.HTTPConnection`` with the fake and reset state."""
    monkeypatch.setattr(sg.http.client, "HTTPConnection", _FakeHTTPConnection)
    _FakeHTTPConnection.instances = []
    _FakeHTTPConnection.response = _FakeResponse(
        200, b'{"status":"success","isExistKia":false}'
    )
    _FakeHTTPConnection.raise_on_request = None
    return _FakeHTTPConnection


@pytest.fixture
def kia_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KIA_GUARD_ENABLED", "true")


# ────────────────────────── RMS detection ──────────────────────────


def test_detect_rms_non_office_extension_returns_none(tmp_path) -> None:
    f = tmp_path / "notes.txt"
    _write_file(f, _OLE2_MAGIC + _DATASPACES_UTF16LE)
    assert sg.detect_rms_file(str(f)) is None


def test_detect_rms_zip_ooxml_returns_none(tmp_path) -> None:
    # Normal .docx is a ZIP container (PK header) — not OLE2, not RMS.
    f = tmp_path / "normal.docx"
    _write_file(f, _ZIP_MAGIC + b"\x00" * 64)
    assert sg.detect_rms_file(str(f)) is None


def test_detect_rms_ole2_with_dataspaces_returns_reason(tmp_path) -> None:
    f = tmp_path / "encrypted.docx"
    _write_file(f, _OLE2_MAGIC + b"\x00" * 32 + _DATASPACES_UTF16LE)
    reason = sg.detect_rms_file(str(f))
    assert reason is not None
    assert "RMS-encrypted Office file" in reason
    assert ".docx" in reason


def test_detect_rms_ole2_without_dataspaces_returns_none(tmp_path) -> None:
    # Password-encrypted Office file has OLE2 header but no DataSpaces storage.
    f = tmp_path / "password.docx"
    _write_file(f, _OLE2_MAGIC + b"\x00" * 512)
    assert sg.detect_rms_file(str(f)) is None


def test_detect_rms_binary_office_with_dataspaces(tmp_path) -> None:
    f = tmp_path / "secret.doc"
    _write_file(f, _OLE2_MAGIC + _DATASPACES_UTF16LE + b"\x00" * 32)
    reason = sg.detect_rms_file(str(f))
    assert reason is not None
    assert ".doc" in reason


def test_detect_rms_unreadable_file_returns_none(tmp_path) -> None:
    missing = tmp_path / "nope.docx"
    assert sg.detect_rms_file(str(missing)) is None


# ────────────────────────── KIA detection ──────────────────────────


async def test_check_kia_disabled_returns_false_without_http(
    monkeypatch: pytest.MonkeyPatch, fake_icpm
) -> None:
    monkeypatch.delenv("KIA_GUARD_ENABLED", raising=False)
    assert await sg.check_kia_file("C:\\projects\\a.md") is False
    # Guard short-circuits before any ICPM call.
    assert _FakeHTTPConnection.instances == []


async def test_check_kia_dir_has_no_kia_returns_false(
    kia_enabled, fake_icpm
) -> None:
    _FakeHTTPConnection.response = _FakeResponse(
        200, b'{"status":"success","isExistKia":false}'
    )
    assert await sg.check_kia_file("C:\\projects\\normal.md") is False


async def test_check_kia_match_returns_true(kia_enabled, fake_icpm) -> None:
    _FakeHTTPConnection.response = _FakeResponse(
        200,
        b'{"status":"success","isExistKia":true,"kiaPaths":'
        b'["C:\\\\secret\\\\kia.md"]}',
    )
    assert await sg.check_kia_file("C:\\secret\\kia.md") is True


async def test_check_kia_no_match_returns_false(kia_enabled, fake_icpm) -> None:
    _FakeHTTPConnection.response = _FakeResponse(
        200,
        b'{"status":"success","isExistKia":true,"kiaPaths":'
        b'["C:\\\\secret\\\\other.md"]}',
    )
    assert await sg.check_kia_file("C:\\secret\\kia.md") is False


async def test_check_kia_path_case_and_sep_normalised(kia_enabled, fake_icpm) -> None:
    # ICPM lists backslash/lowercase; caller passes mixed case + forward slash.
    _FakeHTTPConnection.response = _FakeResponse(
        200,
        b'{"status":"success","isExistKia":true,"kiaPaths":'
        b'["C:\\\\Secret\\\\KIA.MD"]}',
    )
    assert await sg.check_kia_file("c:/secret/kia.md") is True


async def test_check_kia_icpm_connection_refused_degrades(
    kia_enabled, fake_icpm
) -> None:
    _FakeHTTPConnection.raise_on_request = ConnectionRefusedError("no ICPM")
    # Must not raise — degrade-to-allow.
    assert await sg.check_kia_file("C:\\secret\\kia.md") is False


async def test_check_kia_icpm_non_200_degrades(kia_enabled, fake_icpm) -> None:
    _FakeHTTPConnection.response = _FakeResponse(500, b"server error")
    assert await sg.check_kia_file("C:\\secret\\kia.md") is False


async def test_check_kia_icpm_malformed_body_degrades(
    kia_enabled, fake_icpm
) -> None:
    _FakeHTTPConnection.response = _FakeResponse(200, b"not-json{{")
    assert await sg.check_kia_file("C:\\secret\\kia.md") is False


async def test_check_kia_sends_expected_request(kia_enabled, fake_icpm) -> None:
    _FakeHTTPConnection.response = _FakeResponse(
        200, b'{"status":"success","isExistKia":false}'
    )
    await sg.check_kia_file("C:\\projects\\sub\\file.md")

    assert len(_FakeHTTPConnection.instances) == 1
    conn = _FakeHTTPConnection.instances[0]
    assert conn.request_method == "POST"
    assert conn.request_path == "/api/queryDirKiaPaths"
    assert conn.request_headers == {"Content-Type": "application/json"}
    payload = json.loads(conn.request_body)
    assert payload["pageNo"] == 1
    assert payload["pageSize"] == 500
    # Parent directory with trailing backslash separator.
    assert payload["filePath"] == "C:\\projects\\sub\\"


async def test_check_kia_runs_http_off_event_loop(kia_enabled, fake_icpm) -> None:
    """Regression: blocking HTTP must run in a worker thread, not on the loop."""
    _FakeHTTPConnection.response = _FakeResponse(
        200, b'{"status":"success","isExistKia":false}'
    )
    await sg.check_kia_file("C:\\projects\\a.md")
    assert _FakeHTTPConnection.instances, "no connection was created"
    worker_tid = _FakeHTTPConnection.instances[0].thread_id
    assert worker_tid is not None
    assert worker_tid != threading.get_ident(), (
        "blocking HTTP call ran on the event-loop thread — stalls the loop"
    )


# ────────────────────────── path helpers ──────────────────────────


def test_resolve_path_resolves_dotdot(tmp_path) -> None:
    base = tmp_path / "secret"
    base.mkdir()
    (base / "kia.md").write_text("x")
    rel = os.path.join(str(tmp_path), "projects", "..", "secret", "kia.md")
    resolved = sg._resolve_path(rel)
    assert os.path.normcase(resolved) == os.path.normcase(str(base / "kia.md"))


def test_resolve_path_relative_path(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "f.txt").write_text("x")
    assert os.path.normcase(sg._resolve_path("f.txt")) == os.path.normcase(
        str(tmp_path / "f.txt")
    )


def test_get_parent_directory_backslash() -> None:
    assert sg._get_parent_directory("C:\\projects\\sub\\file.md") == "C:\\projects\\sub\\"


def test_get_parent_directory_no_parent() -> None:
    # Root-level path has no parent — return as-is.
    assert sg._get_parent_directory("file.md") == ""


def test_resolve_kia_path_dotdot_windows_cross_platform() -> None:
    # ntpath resolves `..` regardless of host OS (regression: os.path on Linux
    # mangles Windows paths). Drive-letter + backslash stays Windows-style.
    assert sg._resolve_kia_path("C:\\projects\\..\\secret\\kia.md").lower() == "c:\\secret\\kia.md"


def test_resolve_kia_path_forward_slash_normalised() -> None:
    # Forward slashes are accepted by ntpath and normalised to backslash.
    assert sg._resolve_kia_path("C:/projects/sub/file.md").lower() == "c:\\projects\\sub\\file.md"


def test_normalize_for_kia_compare() -> None:
    assert sg._normalize_for_kia_compare("C:/Secret/KIA.MD") == "c:\\secret\\kia.md"


def test_is_file_in_kia_list_match() -> None:
    assert sg._is_file_in_kia_list(
        "C:\\secret\\kia.md", ["C:\\Secret\\KIA.MD"]
    ) is True


def test_is_file_in_kia_list_no_match() -> None:
    assert sg._is_file_in_kia_list(
        "C:\\secret\\kia.md", ["C:\\secret\\other.md"]
    ) is False


def test_is_file_in_kia_list_empty() -> None:
    assert sg._is_file_in_kia_list("C:\\x.md", []) is False


def test_icpm_endpoint_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ICPM_BASE_URL", raising=False)
    assert sg._icpm_endpoint() == ("127.0.0.1", 32200)


def test_icpm_endpoint_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ICPM_BASE_URL", "http://127.0.0.1:39999")
    assert sg._icpm_endpoint() == ("127.0.0.1", 39999)


# ────────────────────── extract_file_path_from_tool_args ──────────────────────


def test_extract_path_read_file_file_path_key() -> None:
    assert sg.extract_file_path_from_tool_args(
        "read_file", {"file_path": "C:\\a.md"}
    ) == "C:\\a.md"


def test_extract_path_read_file_path_key() -> None:
    assert sg.extract_file_path_from_tool_args(
        "read_file", {"path": "C:\\b.md"}
    ) == "C:\\b.md"


def test_extract_path_read_text_file() -> None:
    assert sg.extract_file_path_from_tool_args(
        "read_text_file", {"path": "C:\\c.md"}
    ) == "C:\\c.md"


def test_extract_path_non_file_tool_returns_none() -> None:
    assert sg.extract_file_path_from_tool_args("bash", {"command": "ls"}) is None


def test_extract_path_non_dict_args_returns_none() -> None:
    assert sg.extract_file_path_from_tool_args("read_file", "not-a-dict") is None


def test_extract_path_empty_path_returns_none() -> None:
    assert sg.extract_file_path_from_tool_args("read_file", {"path": "   "}) is None
