# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Enterprise Gateway + Runtime Management end-to-end system test."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest
import websockets
from dotenv import load_dotenv

pytestmark = [pytest.mark.integration, pytest.mark.system]

ENTERPRISE_DIR = Path(__file__).resolve().parent
REPO_ROOT = ENTERPRISE_DIR.parents[2]
ENV_FILE = ENTERPRISE_DIR / ".env"
MOCK_LLM_SCRIPT = ENTERPRISE_DIR / "mock_llm_server.py"
MONOREPO_ROOT = REPO_ROOT.parent
RUNTIME_FOUNDATION = MONOREPO_ROOT / "agent-runtime" / "foundation"
RUNTIME_MANAGEMENT = MONOREPO_ROOT / "agent-runtime" / "management"

CHAT_ID = "chat_test"
BOT_ID = "bot_test"
SERVICE_ID = hashlib.md5(f"{CHAT_ID}::{BOT_ID}".encode("utf-8")).hexdigest()
SESSION_ID = "enterprise_sess_001"
ENTERPRISE_E2E_JIUWENCLAW_ID = "enterprise_e2e_001"
GATEWAY_DB_FILENAME = "jiuwenswarm.db"


def _ut_log(stage: str, /, **fields: object) -> None:
    """Print structured test diagnostics (visible with pytest -s / log_cli)."""
    detail = " ".join(f"{key}={value!r}" for key, value in fields.items())
    print(f"[E2E][{stage}] {detail}", flush=True)


def _pick_free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _start_process(cmd: list[str], *, env: dict[str, str], log_path: Path) -> subprocess.Popen:
    log_file = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        cmd,
        cwd=str(REPO_ROOT),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
    )
    log_file.close()
    _ut_log(
        "process.start",
        pid=proc.pid,
        cmd=" ".join(cmd),
        log_path=log_path,
    )
    return proc


def _stop_process(proc: subprocess.Popen | None) -> None:
    if proc is None:
        return
    exit_code = proc.poll()
    if exit_code is not None:
        _ut_log("process.already_exited", pid=proc.pid, exit_code=exit_code)
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass
        return

    _ut_log("process.stop", pid=proc.pid)
    proc.terminate()
    try:
        proc.wait(timeout=15)
        return
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass


async def _wait_for_log(log_path: Path, needle: str, timeout: float = 120.0) -> None:
    _ut_log("wait.log.begin", needle=needle, log_path=log_path, timeout=timeout)
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if log_path.exists():
            text = log_path.read_text(encoding="utf-8", errors="ignore")
            if needle in text:
                _ut_log("wait.log.ok", needle=needle, log_path=log_path)
                return
        await asyncio.sleep(0.3)
    log_text = (
        log_path.read_text(encoding="utf-8", errors="ignore")
        if log_path.exists()
        else ""
    )
    raise AssertionError(
        f"Timed out waiting for log line: {needle}\nlog={log_text[-4000:]}"
    )


async def _wait_for_http(url: str, timeout: float = 30.0) -> None:
    import urllib.error
    import urllib.request

    _ut_log("wait.http.begin", url=url, timeout=timeout)
    deadline = asyncio.get_running_loop().time() + timeout
    last_error: Exception | None = None
    while asyncio.get_running_loop().time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:  # noqa: S310
                if resp.status == 200:
                    _ut_log("wait.http.ok", url=url, status=resp.status)
                    return
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            await asyncio.sleep(0.2)
    raise AssertionError(f"Timed out waiting for HTTP {url} last_error={last_error}")


async def _wait_for_websocket_ready(url: str, timeout: float = 60.0) -> None:
    _ut_log("wait.ws.begin", url=url, timeout=timeout)
    deadline = asyncio.get_running_loop().time() + timeout
    last_error: Exception | None = None
    while asyncio.get_running_loop().time() < deadline:
        try:
            async with websockets.connect(url):
                _ut_log("wait.ws.ok", url=url)
                return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            await asyncio.sleep(0.2)
    raise AssertionError(
        f"Timed out waiting for websocket: {url} last_error={last_error}"
    )


def _subprocess_bootstrap_env() -> dict[str, str]:
    keys = (
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "TEMP",
        "TMP",
        "PYTHONHOME",
        "VIRTUAL_ENV",
        "PATHEXT",
        "PROCESSOR_ARCHITECTURE",
    )
    return {key: value for key, value in os.environ.items() if key in keys and value}


def _load_gateway_dotenv() -> None:
    """enterprise/.env 仅供 Gateway 进程使用。"""
    load_dotenv(dotenv_path=ENV_FILE, override=False)


def _shared_gateway_db_path(run_home: Path) -> Path:
    """Gateway 与 AgentServer 共用的 SQLite 文件（位于本次 run 时间戳目录下）。"""
    return (run_home / GATEWAY_DB_FILENAME).resolve()


def _apply_shared_gateway_db_env(env: dict[str, str], run_home: Path) -> None:
    env["GATEWAY_DB_TYPE"] = "sqlite"
    env["GATEWAY_SQLITE_PATH"] = str(_shared_gateway_db_path(run_home))
    env["JIUWENCLAW_ID"] = ENTERPRISE_E2E_JIUWENCLAW_ID


def _build_gateway_env(
    run_home: Path,
    gateway_home: Path,
    server_home: Path,
    mock_llm_port: int,
    web_port: int,
    gateway_port: int,
) -> dict[str, str]:
    _load_gateway_dotenv()
    env = os.environ.copy()
    env["HOME"] = str(gateway_home)
    env["AGENT_SERVER_HOME"] = str(server_home)
    env["API_BASE"] = f"http://127.0.0.1:{mock_llm_port}/v1"
    env["WEB_HOST"] = "127.0.0.1"
    env["WEB_PORT"] = str(web_port)
    env["GATEWAY_HOST"] = "127.0.0.1"
    env["GATEWAY_PORT"] = str(gateway_port)
    env["EXTENSION_DIRS"] = str(REPO_ROOT / "packages" / "jiuwenclaw-ee" / "gateway" / "extensions")
    env["GATEWAY_MANAGER_WS_CLIENT_ENABLED"] = "false"
    env["AGENT_SERVER_DEPLOY_MODE"] = "process"
    env["LLM_SSL_VERIFY"] = "false"
    env["IP"] = "127.0.0.1"
    env["DEPLOY_TYPE"] = "subprocess"
    env["LOWCODE_IMAGE"] = "local-process"
    env["PYTHONPATH"] = os.pathsep.join(
        [
            str(REPO_ROOT),
            str(RUNTIME_FOUNDATION),
            str(RUNTIME_MANAGEMENT),
        ]
    )
    env["AGENT_SERVER_LAUNCHER_SCRIPT"] = str(ENTERPRISE_DIR / "agentserver_launcher.py")
    env["AGENT_SERVER_LOG_FILE"] = str(server_home / "agentserver.log")
    env["VIBESKILL_ENABLED"] = "false"
    _apply_shared_gateway_db_env(env, run_home)
    _ut_log(
        "env.gateway.ready",
        gateway_home=gateway_home,
        server_home=server_home,
        gateway_log=gateway_home / "gateway.log",
        agentserver_log=Path(env["AGENT_SERVER_LOG_FILE"]),
        api_base=env["API_BASE"],
        web_port=web_port,
        gateway_port=gateway_port,
        deploy_mode=env["AGENT_SERVER_DEPLOY_MODE"],
        extension_dirs=env["EXTENSION_DIRS"],
        launcher_script=env["AGENT_SERVER_LAUNCHER_SCRIPT"],
        gateway_db_type=env["GATEWAY_DB_TYPE"],
        gateway_sqlite_path=env["GATEWAY_SQLITE_PATH"],
        jiuwenclaw_id=env["JIUWENCLAW_ID"],
    )
    return env


def _build_mock_llm_env() -> dict[str, str]:
    """Mock LLM 不加载 enterprise/.env，仅保留系统 PATH 等。"""
    return _subprocess_bootstrap_env()


def _workspace_config_dir(home: Path) -> Path:
    """Agent/Gateway 工作区配置目录：``<HOME>/.jiuwenclaw/config``。"""
    return home / ".jiuwenclaw" / "config"


def _init_gateway_home(home: Path, mock_llm_port: int) -> None:
    """初始化 Gateway HOME，并写入 workspace config/.env（供 Gateway 读取）。"""
    from jiuwenclaw.utils import prepare_workspace, set_user_home

    set_user_home(home)
    prepare_workspace(overwrite=False)
    _write_workspace_env(home, mock_llm_port)
    _ut_log(
        "setup.gateway_home",
        home=home,
        workspace=home / ".jiuwenclaw",
        config_env=_workspace_config_dir(home) / ".env",
    )


def _init_server_home(home: Path) -> None:
    """初始化 AgentServer HOME 目录结构；LLM 配置由 Runtime _agent_env_vars 注入，不写 workspace .env。"""
    from jiuwenclaw.utils import prepare_workspace, set_user_home

    set_user_home(home)
    prepare_workspace(overwrite=False)
    _ut_log(
        "setup.server_home",
        home=home,
        workspace=home / ".jiuwenclaw",
    )


def _web_channel_ws_url(web_port: int, web_path: str = "/ws") -> str:
    return f"ws://127.0.0.1:{web_port}{web_path}"


def _write_workspace_env(home: Path, mock_llm_port: int) -> None:
    config_dir = _workspace_config_dir(home)
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / ".env").write_text(
        "\n".join(
            [
                f"API_BASE=http://127.0.0.1:{mock_llm_port}/v1",
                "API_KEY=mock-key",
                "MODEL_PROVIDER=OpenAI",
                "MODEL_NAME=mock-model",
                "LLM_SSL_VERIFY=false",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


async def _recv_json(ws, timeout: float = 180.0) -> dict:
    raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
    return json.loads(raw)


async def _run_web_channel_user_request(ws_url: str) -> dict:
    """WebSocket client: connect to Gateway WebChannel, send one user request, wait for response."""
    frames: list[dict] = []
    req_id = "req-enterprise-e2e"
    request = {
        "type": "req",
        "id": req_id,
        "method": "chat.send",
        "params": {
            "session_id": SESSION_ID,
            "content": "say hello",
            "mode": "agent.fast",
            "service_id": SERVICE_ID,
        },
    }

    _ut_log(
        "web_channel.begin",
        ws_url=ws_url,
        req_id=req_id,
        session_id=SESSION_ID,
        service_id=SERVICE_ID,
    )

    async with websockets.connect(ws_url, max_size=10_000_000) as ws:
        ack = await _recv_json(ws, timeout=30)
        if ack.get("event") != "connection.ack":
            raise AssertionError(f"Expected connection.ack, got: {ack}")
        _ut_log("web_channel.connected", event=ack.get("event"))

        await ws.send(json.dumps(request, ensure_ascii=False))
        _ut_log("web_channel.sent", req_id=req_id, params=request["params"])

        accepted = False
        completed = False
        response_events: list[str] = []
        deadline = asyncio.get_running_loop().time() + 120.0

        while asyncio.get_running_loop().time() < deadline:
            remaining = max(5.0, deadline - asyncio.get_running_loop().time())
            msg = await _recv_json(ws, timeout=remaining)
            _ut_log(
                "web.recv_msg",
                msg=msg,
            )
            frames.append(msg)

            if msg.get("type") == "res" and msg.get("id") == req_id:
                assert msg.get("ok") is True, f"chat.send rejected: {msg}"
                accepted = True
                _ut_log("web_channel.accepted", req_id=req_id, payload=msg.get("payload"))
                continue

            if msg.get("type") != "event":
                continue

            event_name = str(msg.get("event") or "")
            payload = msg.get("payload") or {}
            if event_name:
                response_events.append(event_name)
            _ut_log(
                "web_channel.event",
                event=event_name,
                is_complete=payload.get("is_complete"),
            )

            if event_name == "chat.error":
                raise AssertionError(f"chat.error: {payload}")
            if event_name == "chat.final":
                completed = True
                break
            if event_name == "chat.processing_status" and payload.get("is_complete"):
                completed = True
                break

        assert accepted, "chat.send was not accepted by Gateway WebChannel"
        assert completed, f"no terminal chat response, last_frames={frames[-5:]}"

    result = {
        "accepted": accepted,
        "completed": completed,
        "response_events": response_events,
        "frame_count": len(frames),
    }
    _ut_log("web_channel.done", **result)
    return result


@pytest.mark.asyncio
@pytest.mark.skip(reason="skip ci")
async def test_gateway_runtime_process_deploy_and_chat(
    enterprise_run_dirs: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
):
    run_home, gateway_home, server_home = enterprise_run_dirs
    mock_llm_port = _pick_free_port()
    web_port = _pick_free_port()
    gateway_port = _pick_free_port()
    _ut_log(
        "setup.ports",
        run_home=run_home,
        gateway_home=gateway_home,
        server_home=server_home,
        mock_llm_port=mock_llm_port,
        web_port=web_port,
        gateway_port=gateway_port,
        service_id=SERVICE_ID,
        session_id=SESSION_ID,
    )

    monkeypatch.setenv("HOME", str(gateway_home))
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    _init_gateway_home(gateway_home, mock_llm_port)
    _init_server_home(server_home)

    from jiuwenclaw.utils import set_user_home

    set_user_home(gateway_home)

    gateway_env = _build_gateway_env(
        run_home, gateway_home, server_home, mock_llm_port, web_port, gateway_port
    )
    mock_llm_env = _build_mock_llm_env()

    mock_log = run_home / "mock_llm.log"
    gateway_log = gateway_home / "gateway.log"
    agentserver_log = server_home / "agentserver.log"

    mock_proc = _start_process(
        [
            sys.executable,
            str(MOCK_LLM_SCRIPT),
            "--port",
            str(mock_llm_port),
            "--stream-token-count",
            "5",
            "--stream-token-interval",
            "0.05",
        ],
        env=mock_llm_env,
        log_path=mock_log,
    )
    gateway_proc = None
    try:
        await _wait_for_http(f"http://127.0.0.1:{mock_llm_port}/health", timeout=30)
        _ut_log("mock_llm.ready", port=mock_llm_port, log_path=mock_log)

        gateway_proc = _start_process(
            [sys.executable, "-m", "jiuwenclaw.app_gateway", "--port", str(web_port)],
            env=gateway_env,
            log_path=gateway_log,
        )
        _ut_log(
            "gateway.started",
            web_port=web_port,
            web_channel_ws=_web_channel_ws_url(web_port),
            log_path=gateway_log,
        )

        await _wait_for_log(gateway_log, "using extension AgentServerClient", timeout=180)
        await _wait_for_log(gateway_log, "WebChannel 已启动", timeout=180)
        _ut_log("gateway.ready", gateway_log=gateway_log)

        web_ws_url = _web_channel_ws_url(web_port)
        chat_result = await _run_web_channel_user_request(web_ws_url)

        gateway_text = gateway_log.read_text(encoding="utf-8", errors="ignore")
        mock_text = mock_log.read_text(encoding="utf-8", errors="ignore")
        process_deploy_ok = "Process deploy 成功" in gateway_text or "Process deploy" in gateway_text
        mock_llm_called = "POST /v1/chat/completions" in mock_text
        mock_llm_responded = (
            "mock token1" in mock_text
            or "Non-stream response content:" in mock_text
            or "Streamed token: mock token1" in mock_text
        )
        _ut_log(
            "assert.summary",
            process_deploy_ok=process_deploy_ok,
            mock_llm_called=mock_llm_called,
            mock_llm_responded=mock_llm_responded,
            chat_result=chat_result,
            mock_log=mock_log,
            gateway_log=gateway_log,
        )
        assert process_deploy_ok
        assert mock_llm_called
        assert mock_llm_responded
        assert chat_result["accepted"] is True
        assert chat_result["completed"] is True
        assert agentserver_log.exists(), f"AgentServer log not found: {agentserver_log}"
        agent_text = agentserver_log.read_text(encoding="utf-8", errors="ignore")
        assert "Streamed token: mock token1" in mock_text, "Mock LLM should stream SSE tokens"
        assert "[LLM] <<< response: content_len=0" not in agent_text, (
            "Agent LLM response was empty; mock likely returned non-SSE JSON for a stream request"
        )
        shared_db = _shared_gateway_db_path(run_home)
        assert shared_db.exists(), f"Shared gateway sqlite not created: {shared_db}"
        server_env_file = server_home / ".jiuwenclaw" / "config" / ".env"
        if server_env_file.exists():
            assert f"127.0.0.1:{mock_llm_port}" not in server_env_file.read_text(encoding="utf-8")
        _ut_log(
            "test.pass",
            session_id=SESSION_ID,
            service_id=SERVICE_ID,
            run_home=run_home,
            gateway_home=gateway_home,
            server_home=server_home,
            agentserver_log=agentserver_log,
        )
    finally:
        _stop_process(gateway_proc)
        _stop_process(mock_proc)
        _ut_log(
            "teardown.keep",
            run_home=run_home,
            gateway_home=gateway_home,
            server_home=server_home,
            mock_log=mock_log,
            gateway_log=gateway_log,
            agentserver_log=agentserver_log,
        )
