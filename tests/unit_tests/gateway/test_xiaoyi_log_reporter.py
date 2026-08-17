"""OpenClaw-style standalone log_reporter (scripts/log_reporter.py)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_LOG_REPORTER_PATH = (
    Path(__file__).resolve().parents[3] / "scripts" / "log_reporter.py"
)


def _load_log_reporter():
    name = "xiaoyi_log_reporter_under_test"
    cached = sys.modules.get(name)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(name, _LOG_REPORTER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


lr = _load_log_reporter()


def test_monitors_include_jiuwen_and_sidecar_paths() -> None:
    types = {item["business_type"] for item in lr.MONITORS}
    paths = {item["path"] for item in lr.MONITORS}
    assert "jiuwenswarm-full" in types
    assert "jiuwenswarm-gateway" in types
    assert "openclaw-gateway" in types
    assert "skill-dynamization" in types
    assert "/home/sandbox/.jiuwenswarm/agent/.logs/full.log" in paths
    assert "/sidecar/proxyservice/proxy_policy_fault.log" in paths
    assert "/tmp/openclaw/skill-toggle.log" in paths
    assert not any("/opt/huawei/logs/sidecar" in p for p in paths)
    for item in lr.MONITORS:
        assert item["business_type"] == item["business_type"].lower()
        assert " " not in item["business_type"]


def test_read_env_file(tmp_path: Path) -> None:
    env_path = tmp_path / ".xiaoyienv"
    env_path.write_text(
        "SERVICE_URL=http://lfhagmirror.hwcloudtest.cn:80\n"
        "PERSONAL-API-KEY=sk-virtual\n"
        "PERSONAL-UID=uid-virtual\n",
        encoding="utf-8",
    )
    env = lr.read_env_file(str(env_path))
    assert env["service_url"] == "http://lfhagmirror.hwcloudtest.cn:80"
    assert env["api_key"] == "sk-virtual"
    assert env["uid"] == "uid-virtual"


def test_read_env_file_requires_keys(tmp_path: Path) -> None:
    env_path = tmp_path / ".xiaoyienv"
    env_path.write_text("SERVICE_URL=http://example\n", encoding="utf-8")
    with pytest.raises(ValueError, match="PERSONAL-API-KEY"):
        lr.read_env_file(str(env_path))


def test_resolve_log_files_skips_missing_directory() -> None:
    assert lr.resolve_log_files("/no/such/dir/full.log") == []


def test_resolve_log_files_wildcard_and_latest_only(tmp_path: Path) -> None:
    (tmp_path / "init_20260101_010101.log").write_text("a", encoding="utf-8")
    (tmp_path / "init_20260102_020202.log").write_text("b", encoding="utf-8")
    pattern = str(tmp_path / "init_{year}{month}{day}_{hour}{minute}{second}.log")
    all_files = lr.resolve_log_files(pattern)
    assert len(all_files) == 2
    latest = lr.resolve_log_files(pattern, latest_only=True)
    assert latest == [str(tmp_path / "init_20260102_020202.log")]


def test_scan_file_incremental_complete_lines(tmp_path: Path) -> None:
    log_path = tmp_path / "full.log"
    log_path.write_bytes(b"line1\nline2\n")
    store = {"files": {}}
    first = lr.scan_file(str(log_path), store)
    assert first is not None
    assert first["content"] == "line1\nline2\n"
    lr.set_cursor(store, str(log_path), first["new_cursor"])
    second = lr.scan_file(str(log_path), store)
    assert second is None
    with log_path.open("ab") as handle:
        handle.write(b"line3\n")
    third = lr.scan_file(str(log_path), store)
    assert third is not None
    assert third["content"] == "line3\n"


def test_scan_file_missing_returns_none() -> None:
    assert lr.scan_file("/no/such/file.log", {"files": {}}) is None


def test_filter_self_referencing_drops_reporter_noise() -> None:
    raw = "\n".join(
        [
            "keep me",
            "POST /fulfillment/v1/claw/log-file/sync",
            "log-reporter uploaded",
            "also keep",
        ]
    )
    cleaned = lr.filter_self_referencing(raw)
    assert "keep me" in cleaned
    assert "also keep" in cleaned
    assert "log-file/sync" not in cleaned
    assert "log-reporter" not in cleaned


def test_send_report_payload_and_headers() -> None:
    captured: dict = {}

    class FakeResp:
        status = 200

        def read(self):
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(req, timeout=30):
        captured["url"] = req.full_url
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return FakeResp()

    env = {
        "service_url": "http://lfhagmirror.hwcloudtest.cn:80",
        "api_key": "k1",
        "uid": "u1",
    }
    with patch.object(lr, "urlopen", fake_urlopen), patch.object(
        lr, "generate_instance_id", return_value="sandbox-1"
    ):
        lr.send_report(
            [{"businessType": "jiuwenswarm-full", "fileUrl": "http://file/1"}],
            env,
        )
    assert captured["url"] == (
        "http://lfhagmirror.hwcloudtest.cn:80/fulfillment/v1/claw/log-file/sync"
    )
    assert captured["headers"]["x-uid"] == "u1"
    assert captured["headers"]["x-api-key"] == "k1"
    assert captured["headers"]["x-request-from"] == "openclaw"
    assert "x-hag-trace-id" in captured["headers"]
    assert captured["body"]["instanceId"] == "sandbox-1"
    assert captured["body"]["logFiles"] == [
        {"businessType": "jiuwenswarm-full", "fileUrl": "http://file/1"}
    ]
    assert "targetFileName" not in captured["body"]["logFiles"][0]


def test_do_scan_uploads_and_syncs(tmp_path: Path, monkeypatch) -> None:
    log_path = tmp_path / "full.log"
    log_path.write_bytes(b"hello swarm\n")
    env = {
        "service_url": "http://upload.example",
        "api_key": "k1",
        "uid": "u1",
    }
    uploaded = {}

    def fake_upload(content, name, bak_dir, base_url, api_key, uid):
        uploaded["name"] = name
        uploaded["content"] = content
        return "http://osms/file"

    reports = {}

    def fake_report(log_files, report_env):
        reports["files"] = log_files
        reports["env"] = report_env

    monkeypatch.setattr(lr, "upload_content", fake_upload)
    monkeypatch.setattr(lr, "send_report", fake_report)
    lr.do_scan(
        cursor_path=str(tmp_path / "cursor.json"),
        bak_dir=str(tmp_path / "bak"),
        env=env,
        monitors=[
            {
                "path": str(log_path),
                "business_type": "jiuwenswarm-full",
                "json_parse": False,
            }
        ],
    )
    assert uploaded["name"] == "jiuwenswarm-full"
    assert "hello swarm" in uploaded["content"]
    assert reports["files"] == [
        {"businessType": "jiuwenswarm-full", "fileUrl": "http://osms/file"}
    ]
    saved = json.loads((tmp_path / "cursor.json").read_text(encoding="utf-8"))
    assert str(log_path) in saved["files"]
