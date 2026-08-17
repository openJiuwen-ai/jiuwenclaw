# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""端到端 CLI proxy 集成测试.

仅做端到端: 通过 ``subprocess.run`` 真实拉起已安装的 ``jiuwenbox`` 可执行脚本
(由 ``pyproject.toml`` 的 ``[project.scripts]`` 声明, 类似 ``uvicorn``),
断言 stdout / stderr / 退出码 / HTTP 副作用; 不写单元测试、不 mock httpx。

运行测试前请先在仓库根 (``code_agent/jiuwenbox/``) 安装本包::

    pip install -e .

若 ``jiuwenbox`` 不在 PATH 上, 测试会自动到 ``sys.executable`` 同级目录寻找;
均找不到则在 collect 阶段直接报错并提示安装方式。

假设 jiuwenbox server 已在运行 (与既有 ``test_server_api_default.py`` 一致),
由 ``--server-endpoint`` 或 ``JIUWENBOX_TEST_SERVER`` 指定。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def _resolve_jiuwenbox_bin() -> str:
    """定位已安装的 ``jiuwenbox`` 可执行脚本。

    查找顺序:
    1. ``PATH`` 上的 ``jiuwenbox`` / ``jiuwenbox.exe``;
    2. 当前 Python 解释器同级目录 (venv ``bin/`` 或 ``Scripts/``);
    3. 找不到则报错, 提示先 ``pip install -e .``。
    """
    found = shutil.which("jiuwenbox")
    if found:
        return found
    py_dir = Path(sys.executable).resolve().parent
    candidates = [py_dir / "jiuwenbox", py_dir / "jiuwenbox.exe"]
    # venv 上 ``python`` 可能在 ``bin/``, scripts 也在 ``bin/``; Windows venv
    # 则 ``python.exe`` 与 ``jiuwenbox.exe`` 同在 ``Scripts/``。
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    raise RuntimeError(
        "jiuwenbox CLI is not installed. Run `pip install -e .` from "
        "`code_agent/jiuwenbox/` before running the CLI integration tests.",
    )


_JIUWENBOX_BIN = _resolve_jiuwenbox_bin()


_PROXY_ENV_VARS = (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
    "http_proxy", "https_proxy", "all_proxy",
)


def _subprocess_env(extra_env: dict[str, str] | None = None) -> dict[str, str]:
    """为子进程构造环境变量。

    本测试运行已安装的脚本, 无需再注入 ``PYTHONPATH``;
    保留 ``NO_COLOR`` 以确保 stderr 不含 ANSI 转义码, 便于断言。
    """
    env = os.environ.copy()
    for key in _PROXY_ENV_VARS:
        env.pop(key, None)
    env["NO_PROXY"] = "*"
    env["no_proxy"] = "*"
    env.setdefault("NO_COLOR", "1")
    if extra_env:
        env.update(extra_env)
    return env


def _run_cli(
    args: list[str],
    *,
    base_url: str | None = None,
    extra_env: dict[str, str] | None = None,
    input_bytes: bytes | None = None,
    timeout: float = 60.0,
    check: bool = False,
) -> subprocess.CompletedProcess:
    """运行 CLI 并返回 ``CompletedProcess`` (stdout/stderr 为 bytes)。"""
    cmd: list[str] = [_JIUWENBOX_BIN]
    if base_url is not None:
        cmd += ["--base-url", base_url]
    cmd += args
    return subprocess.run(
        cmd,
        input=input_bytes,
        capture_output=True,
        timeout=timeout,
        check=check,
        env=_subprocess_env(extra_env),
    )


def _run_cli_json(args: list[str], *, base_url: str, **kwargs) -> tuple[subprocess.CompletedProcess, object]:
    """运行 CLI, 期待 stdout 是合法 JSON, 返回 (proc, parsed_json)。"""
    proc = _run_cli(args, base_url=base_url, **kwargs)
    assert proc.returncode == 0, (
        f"CLI exited {proc.returncode}\n"
        f"args={args!r}\nstdout={proc.stdout!r}\nstderr={proc.stderr!r}"
    )
    return proc, json.loads(proc.stdout.decode("utf-8"))


@pytest.fixture
def tracking_proxy_routes(client):
    """Register proxy route names created via CLI; best-effort delete on teardown."""
    names: list[str] = []
    yield names
    for name in reversed(names):
        try:
            client.post(f"/api/v1/proxies/{name}/stop")
            client.delete(f"/api/v1/proxies/{name}")
        except Exception:  # noqa: BLE001
            pass


class TestCliProxyBasicAuth:
    """CLI ``proxy create``/``update`` Basic auth options (P2)."""

    @staticmethod
    def _unique_prefix() -> str:
        return f"/cli-basic-{uuid.uuid4().hex[:8]}"

    def test_cli_proxy_create_basic_password_redacted(
        self, server_url, tracking_proxy_routes
    ):
        """``--password`` create: detail/list output is redacted (no plaintext)."""
        secret = "cli-secret-pw-AAAA"
        prefix = self._unique_prefix()
        name = prefix.lstrip("/").replace("/", "-")
        tracking_proxy_routes.append(name)

        proc, data = _run_cli_json(
            ["proxy", "create", "--prefix", prefix, "--target", "http://upstream:7474",
             "--username", "neo4j", "--password", secret],
            base_url=server_url,
        )
        assert data["name"] == name
        assert secret not in proc.stdout.decode("utf-8")
        assert secret not in proc.stderr.decode("utf-8")

        proc, detail = _run_cli_json(["proxy", "get", name], base_url=server_url)
        assert detail["route"]["auth_type"] == "basic"
        assert detail["route"]["basic_auth"]["username"] == "neo4j"
        assert detail["route"]["basic_auth"]["password_configured"] is True
        assert "password" not in detail["route"]["basic_auth"]
        out = proc.stdout.decode("utf-8")
        assert secret not in out

        proc, listing = _run_cli_json(["proxy", "ls"], base_url=server_url)
        entry = next(r for r in listing if r["name"] == name)
        assert entry["route"]["auth_type"] == "basic"
        assert "password" not in entry["route"]["basic_auth"]
        assert secret not in proc.stdout.decode("utf-8")

    def test_cli_proxy_create_basic_password_stdin(
        self, server_url, tracking_proxy_routes
    ):
        """``--password-stdin`` reads stdin, sends via REST; secret not in argv."""
        secret = "stdin-secret-pw-BBBB"
        prefix = self._unique_prefix()
        name = prefix.lstrip("/").replace("/", "-")
        tracking_proxy_routes.append(name)

        proc, data = _run_cli_json(
            ["proxy", "create", "--prefix", prefix, "--target", "http://upstream:7474",
             "--username", "neo4j", "--password-stdin"],
            base_url=server_url,
            input_bytes=secret.encode("utf-8") + b"\n",
        )
        assert data["name"] == name
        assert secret not in proc.stdout.decode("utf-8")
        assert secret not in proc.stderr.decode("utf-8")

        proc, detail = _run_cli_json(["proxy", "get", name], base_url=server_url)
        assert detail["route"]["auth_type"] == "basic"
        assert detail["route"]["basic_auth"]["password_configured"] is True
        assert "password" not in detail["route"]["basic_auth"]

    def test_cli_proxy_create_basic_password_file(
        self, server_url, tracking_proxy_routes, tmp_path
    ):
        """``--password-file`` passes a server-side path; CLI never reads it."""
        secret = "file-secret-pw-CCCC"
        pw_file = tmp_path / "neo4j_pw"
        pw_file.write_text(secret + "\n")
        prefix = self._unique_prefix()
        name = prefix.lstrip("/").replace("/", "-")
        tracking_proxy_routes.append(name)

        proc, data = _run_cli_json(
            ["proxy", "create", "--prefix", prefix, "--target", "http://upstream:7474",
             "--username", "neo4j", "--password-file", str(pw_file)],
            base_url=server_url,
        )
        assert data["name"] == name
        assert secret not in proc.stdout.decode("utf-8")
        assert secret not in proc.stderr.decode("utf-8")

        proc, detail = _run_cli_json(["proxy", "get", name], base_url=server_url)
        assert detail["route"]["auth_type"] == "basic"
        assert detail["route"]["basic_auth"]["password_file"] == str(pw_file)
        assert "password" not in detail["route"]["basic_auth"]
        assert secret not in proc.stdout.decode("utf-8")

    @staticmethod
    def test_cli_proxy_basic_password_sources_mutually_exclusive(server_url):
        secret = "leakcheck-value-XYZ"
        proc = _run_cli(
            ["proxy", "create", "--prefix", "/mux1", "--target", "http://up:7474",
             "--username", "u", "--password", secret, "--password-file", "/tmp/x"],
            base_url=server_url,
        )
        assert proc.returncode != 0
        err = proc.stderr.decode("utf-8")
        assert "mutually exclusive" in err
        assert secret not in err  # rejected password value is not echoed

    @staticmethod
    def test_cli_proxy_basic_password_and_stdin_mutually_exclusive(server_url):
        proc = _run_cli(
            ["proxy", "create", "--prefix", "/mux2", "--target", "http://up:7474",
             "--username", "u", "--password", "p", "--password-stdin"],
            base_url=server_url,
            input_bytes=b"q\n",
        )
        assert proc.returncode != 0
        assert "mutually exclusive" in proc.stderr.decode("utf-8")

    @staticmethod
    def test_cli_proxy_basic_username_required(server_url):
        proc = _run_cli(
            ["proxy", "create", "--prefix", "/nouser", "--target", "http://up:7474",
             "--password", "p"],
            base_url=server_url,
        )
        assert proc.returncode != 0
        assert "username" in proc.stderr.decode("utf-8").lower()

    @staticmethod
    def test_cli_proxy_basic_password_source_required(server_url):
        proc = _run_cli(
            ["proxy", "create", "--prefix", "/nosrc", "--target", "http://up:7474",
             "--username", "u"],
            base_url=server_url,
        )
        assert proc.returncode != 0
        assert "one of --password, --password-file or --password-stdin" in proc.stderr.decode("utf-8")

    @staticmethod
    def test_cli_proxy_basic_api_key_mutex(server_url):
        proc = _run_cli(
            ["proxy", "create", "--prefix", "/keymux", "--target", "http://up:7474",
             "--api-key", "sk", "--username", "u", "--password", "p"],
            base_url=server_url,
        )
        assert proc.returncode != 0
        assert "mutually exclusive" in proc.stderr.decode("utf-8")

    def test_cli_proxy_update_basic_redacted(
        self, server_url, tracking_proxy_routes
    ):
        """``proxy update`` accepts Basic options; output stays redacted."""
        prefix = self._unique_prefix()
        name = prefix.lstrip("/").replace("/", "-")
        tracking_proxy_routes.append(name)

        _run_cli_json(
            ["proxy", "create", "--prefix", prefix, "--target", "http://up:7474"],
            base_url=server_url,
        )
        secret = "upd-secret-pw-DDDD"
        proc, _ = _run_cli_json(
            ["proxy", "update", name, "--prefix", prefix, "--target", "http://up:7474",
             "--username", "neo4j", "--password", secret],
            base_url=server_url,
        )
        assert secret not in proc.stdout.decode("utf-8")
        assert secret not in proc.stderr.decode("utf-8")

        proc, detail = _run_cli_json(["proxy", "get", name], base_url=server_url)
        assert detail["route"]["auth_type"] == "basic"
        assert detail["route"]["basic_auth"]["password_configured"] is True
        assert "password" not in detail["route"]["basic_auth"]
        assert secret not in proc.stdout.decode("utf-8")

    @staticmethod
    def test_cli_proxy_help_documents_basic_options():
        """Help lists Basic options and the --password dev/test warning."""
        proc = _run_cli(["proxy", "create", "--help"], base_url="http://127.0.0.1:8321")
        assert proc.returncode == 0, proc.stderr
        text = proc.stdout.decode("utf-8")
        assert "--username" in text
        assert "--password" in text
        assert "--password-file" in text
        assert "--password-stdin" in text
        assert "DEV/TEST" in text or "dev/test" in text.lower()
