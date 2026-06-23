# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""permissions_config 真实进程 E2E：Manager REST + WS → Gateway 热更新 / GDB 持久化 → AgentServer 冷启动读库。"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest

from e2e_helpers import (
    DEFAULT_JIUWENCLAW_ID,
    MOCK_LLM_SCRIPT,
    agent_structured_logs_dir,
    build_gateway_env,
    build_manager_env,
    build_mock_llm_env,
    gateway_structured_logs_dir,
    init_gateway_home,
    init_server_home,
    init_ut_log,
    manager_permissions_api_url,
    pick_free_port,
    read_gdb_permissions_row,
    run_web_channel_user_request,
    shared_gateway_db_path,
    start_process,
    stop_gateway_gracefully,
    stop_process,
    ut_log,
    wait_for_http,
    wait_for_log,
    wait_for_manager_gateway_registered,
    web_channel_ws_url,
)

pytestmark = [pytest.mark.integration, pytest.mark.system]

HOT_RELOAD_MARKER = "[ManagerWsClient] permissions_config hot-reload"
GATEWAY_COLD_LOAD_MARKER = "[App] permissions config loaded from Gateway DB"
AGENT_COLD_LOAD_MARKER = "[AgentServer] permissions config loaded from Gateway DB"
DELETE_MARKER = "[ManagerWsClient] permissions_config deleted, reverted to yaml fallback"

E2E_PERMISSIONS_BODY = {
    "enabled": True,
    "defaults": "ask",
    "tools": {
        "bash": "deny",
        "todo_list": "allow",
    },
    "rules": [
        {
            "id": "e2e_allow_echo",
            "pattern": "echo *",
            "action": "allow",
        }
    ],
    "approval_overrides": [],
    "owner_scopes": {},
    "deny_guidance_message": "",
    "command_intent": {"enabled": False},
    "file_guard": {
        "workspace": {"rw_enabled": True, "description": "e2e"},
        "global": {},
        "trusted_exec_directory": [],
        "tool_bindings": {},
    },
}


@dataclass
class PermissionsConfigProcessStack:
    """Mock LLM + Claw Manager + Gateway 真实进程栈。"""

    run_home: Path
    gateway_home: Path
    server_home: Path
    jiuwenclaw_id: str = DEFAULT_JIUWENCLAW_ID
    mock_proc: object | None = field(default=None, repr=False)
    manager_proc: object | None = field(default=None, repr=False)
    gateway_proc: object | None = field(default=None, repr=False)
    mock_llm_port: int = 0
    manager_rest_port: int = 0
    manager_ws_port: int = 0
    web_port: int = 0
    gateway_port: int = 0

    @property
    def gateway_log(self) -> Path:
        return self.gateway_home / "gateway.log"

    @property
    def runtime_log(self) -> Path:
        return self.gateway_home / "runtime_sdk.log"

    @property
    def agentserver_log(self) -> Path:
        return self.server_home / "agentserver.log"

    @property
    def mock_log(self) -> Path:
        return self.run_home / "mock_llm.log"

    @property
    def manager_log(self) -> Path:
        return self.run_home / "manager.log"

    @property
    def structured_gateway_log(self) -> Path:
        return gateway_structured_logs_dir(self.gateway_home) / "gateway.log"

    @property
    def structured_agent_process_gateway_log(self) -> Path:
        return agent_structured_logs_dir() / "gateway.log"

    @property
    def gdb_path(self) -> Path:
        return shared_gateway_db_path(self.run_home)

    def permissions_api(self) -> str:
        return manager_permissions_api_url(self.manager_rest_port, self.jiuwenclaw_id)

    async def start(self) -> None:
        self.mock_llm_port = pick_free_port()
        self.manager_rest_port = pick_free_port()
        self.manager_ws_port = pick_free_port()
        self.web_port = pick_free_port()
        self.gateway_port = pick_free_port()

        init_gateway_home(self.gateway_home, self.mock_llm_port)
        init_server_home(self.server_home)

        mock_env = build_mock_llm_env()
        manager_env = build_manager_env(
            self.run_home,
            rest_port=self.manager_rest_port,
            ws_port=self.manager_ws_port,
        )

        self.mock_proc = start_process(
            [
                sys.executable,
                str(MOCK_LLM_SCRIPT),
                "--port",
                str(self.mock_llm_port),
                "--stream-token-count",
                "5",
                "--stream-token-interval",
                "0.05",
            ],
            env=mock_env,
            log_path=self.mock_log,
        )
        await wait_for_http(f"http://127.0.0.1:{self.mock_llm_port}/health", timeout=30)
        ut_log("mock_llm.ready", port=self.mock_llm_port, log_path=self.mock_log)

        self.manager_proc = start_process(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "jiuwenclaw_manager.app:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.manager_rest_port),
            ],
            env=manager_env,
            log_path=self.manager_log,
        )
        await wait_for_http(f"http://127.0.0.1:{self.manager_rest_port}/api/health", timeout=60)
        ut_log("manager.ready", rest_port=self.manager_rest_port, ws_port=self.manager_ws_port)

        await self.start_gateway()

    async def start_gateway(self) -> None:
        gateway_env = build_gateway_env(
            self.run_home,
            self.gateway_home,
            self.server_home,
            self.mock_llm_port,
            self.web_port,
            self.gateway_port,
            jiuwenclaw_id=self.jiuwenclaw_id,
            manager_ws_url=f"ws://127.0.0.1:{self.manager_ws_port}",
            manager_ws_client_enabled=True,
        )
        self.gateway_proc = start_process(
            [sys.executable, "-m", "jiuwenclaw.app_gateway", "--port", str(self.web_port)],
            env=gateway_env,
            log_path=self.gateway_log,
            new_process_group=True,
        )
        ut_log(
            "gateway.started",
            web_port=self.web_port,
            web_channel_ws=web_channel_ws_url(self.web_port),
            log_path=self.gateway_log,
        )
        await wait_for_log(self.gateway_log, "using extension AgentServerClient", timeout=180)
        await wait_for_log(self.gateway_log, "WebChannel 已启动", timeout=180)
        await wait_for_manager_gateway_registered(
            self.manager_rest_port,
            self.jiuwenclaw_id,
            timeout=120,
        )
        ut_log("gateway.ready", gateway_log=self.gateway_log)

    async def stop(self) -> None:
        stop_gateway_gracefully(
            self.gateway_proc,
            gateway_log=self.gateway_log,
            runtime_log=self.runtime_log,
        )
        stop_process(self.manager_proc)
        stop_process(self.mock_proc)
        self.gateway_proc = None
        self.manager_proc = None
        self.mock_proc = None
        ut_log(
            "teardown.keep",
            run_home=self.run_home,
            gateway_home=self.gateway_home,
            server_home=self.server_home,
            gateway_log=self.gateway_log,
            agentserver_log=self.agentserver_log,
            manager_log=self.manager_log,
            mock_log=self.mock_log,
        )

    async def restart_gateway(self) -> None:
        stop_gateway_gracefully(
            self.gateway_proc,
            gateway_log=self.gateway_log,
            runtime_log=self.runtime_log,
        )
        self.gateway_proc = None
        await self.start_gateway()


def _permissions_body_from_row(row: object) -> dict:
    body = getattr(row, "body", None)
    if isinstance(body, str):
        return json.loads(body)
    if isinstance(body, dict):
        return body
    raise AssertionError(f"permissions_config.body is not a dict: {type(body).__name__}")


@pytest.mark.asyncio
@pytest.mark.skip(reason="skip ci")
async def test_permissions_config_process_hot_reload_gdb_and_cold_start(
    enterprise_run_dirs: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
):
    """permissions_config 真实进程 E2E：热更新、GDB 持久化、冷启动读库、删除恢复。

    进程栈：Mock LLM + Claw Manager + Gateway（Manager WS 已连接）+ Process deploy AgentServer。

    链路：
        Manager REST PUT /permissions
            → Manager WS config.push
            → Gateway manager_ws_client 热更新
            → Gateway DB（jiuwenswarm.db）持久化 permissions_config

    验证阶段：
        ① 热更新：PUT 后 Gateway 结构化日志出现 HOT_RELOAD_MARKER
        ② GDB 持久化：共享 SQLite 中 permissions_config.body 字段正确
        ③ Agent 冷启动：chat.send 触发 Process deploy，Agent 从 GDB 加载 permissions
        ④ Gateway 冷启动：重启 Gateway 后从 GDB 加载 permissions
        ⑤ 删除恢复：DELETE /permissions 清除 GDB 行，GET 返回 404
    """
    run_home, gateway_home, server_home = enterprise_run_dirs
    init_ut_log(run_home)

    monkeypatch.setenv("HOME", str(gateway_home))

    stack = PermissionsConfigProcessStack(
        run_home=run_home,
        gateway_home=gateway_home,
        server_home=server_home,
    )
    await stack.start()

    try:
        # ① 热更新
        async with httpx.AsyncClient(timeout=60.0) as http:
            upsert_resp = await http.put(
                stack.permissions_api(),
                json={"body": E2E_PERMISSIONS_BODY},
            )
            assert upsert_resp.status_code == 200, upsert_resp.text
            upsert_body = upsert_resp.json()
            assert upsert_body["code"] == 200
            data = upsert_body["data"]
            assert data["jiuwenclaw_id"] == stack.jiuwenclaw_id
            assert data["body"]["tools"]["bash"] == "deny"

        await wait_for_log(stack.structured_gateway_log, HOT_RELOAD_MARKER, timeout=60)

        # ② GDB 持久化
        gdb_row = await read_gdb_permissions_row(stack.gdb_path, stack.jiuwenclaw_id)
        assert gdb_row is not None, f"permissions_config row missing in {stack.gdb_path}"
        gdb_body = _permissions_body_from_row(gdb_row)
        assert gdb_body["tools"]["bash"] == "deny"
        assert gdb_body["tools"]["todo_list"] == "allow"
        assert any(r.get("id") == "e2e_allow_echo" for r in gdb_body.get("rules") or [])
        ut_log(
            "assert.gdb.persisted",
            gdb_path=stack.gdb_path,
            bash=gdb_body["tools"]["bash"],
            todo_list=gdb_body["tools"]["todo_list"],
        )

        # ③ Agent 冷启动
        chat_result = await run_web_channel_user_request(web_channel_ws_url(stack.web_port))
        assert chat_result["accepted"] is True
        assert chat_result["completed"] is True
        assert stack.agentserver_log.exists(), f"AgentServer log not found: {stack.agentserver_log}"
        await wait_for_log(stack.structured_agent_process_gateway_log, AGENT_COLD_LOAD_MARKER, timeout=120)
        gateway_text = stack.gateway_log.read_text(encoding="utf-8", errors="ignore")
        assert "Process deploy 成功" in gateway_text or "Process deploy" in gateway_text
        ut_log(
            "assert.agent.cold_start",
            agentserver_log=stack.agentserver_log,
            structured_agent_process_gateway_log=stack.structured_agent_process_gateway_log,
        )

        # ④ Gateway 冷启动
        await stack.restart_gateway()
        await wait_for_log(stack.gateway_log, GATEWAY_COLD_LOAD_MARKER, timeout=120)
        ut_log("assert.gateway.cold_start", gateway_log=stack.gateway_log)

        # ⑤ 删除恢复
        async with httpx.AsyncClient(timeout=60.0) as http:
            delete_resp = await http.delete(stack.permissions_api())
            assert delete_resp.status_code == 200, delete_resp.text

        await wait_for_log(stack.structured_gateway_log, DELETE_MARKER, timeout=60)
        gdb_after_delete = await read_gdb_permissions_row(stack.gdb_path, stack.jiuwenclaw_id)
        assert gdb_after_delete is None, "permissions_config row should be removed from GDB"

        async with httpx.AsyncClient(timeout=60.0) as http:
            missing_resp = await http.get(stack.permissions_api())
            assert missing_resp.status_code == 404

        ut_log(
            "test.pass",
            run_home=run_home,
            jiuwenclaw_id=stack.jiuwenclaw_id,
            gdb_path=stack.gdb_path,
        )
    finally:
        await stack.stop()
