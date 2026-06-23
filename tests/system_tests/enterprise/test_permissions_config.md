# test_permissions_config

用例文件：`test_permissions_config_process_e2e.py`  
用例函数：`test_permissions_config_process_hot_reload_gdb_and_cold_start`

在真实进程栈（**Mock LLM + Claw Manager + Gateway + Process deploy AgentServer**）上验证 `permissions_config` 的完整生命周期：热更新、GDB 持久化、Agent/Gateway 冷启动读库、删除恢复。

> 整体架构说明见 [permissions_config_architecture.md](../../docs/zh/permissions_config_architecture.md)

## 进程栈

```
Mock LLM ──HTTP──► AgentServer（子进程）
                        ▲
                        │ Runtime Process deploy
Claw Manager ◄──WS──► Gateway ◄──WS──► E2E 测试客户端
     │                    │
     │ REST               │ 共用 SQLite
     ▼                    ▼
 manager.db          jiuwenswarm.db (GDB)
```

**前置**（`PermissionsConfigProcessStack.start()`）：

1. Mock LLM：`mock_llm_server.py`
2. Claw Manager：`uvicorn jiuwenclaw_manager.app:app`
3. Gateway：`python -m jiuwenclaw.app_gateway`（`manager_ws_client` 已连接并注册）

> 需设置 `AGENT_RUNTIME`（`enterprise/.env` 默认 `local`），否则不会走 GDB 冷加载路径。

## 五阶段总览

```mermaid
flowchart LR
    P1[① PUT 热更新] --> P2[② 读 GDB]
    P2 --> P3[③ chat.send 拉 Agent]
    P3 --> P4[④ 重启 Gateway]
    P4 --> P5[⑤ DELETE 恢复]
```

| 阶段 | 触发动作 | 主要断言 |
|------|----------|----------|
| ① 热更新 | `PUT /permissions` | `structured_gateway.log` → `hot-reload` |
| ② GDB 持久化 | 读 `jiuwenswarm.db` | `body.tools.bash == deny` 等 |
| ③ Agent 冷启动 | `chat.send` | Agent 冷加载日志 + `Process deploy` |
| ④ Gateway 冷启动 | `restart_gateway()` | `gateway/gateway.log` → `App permissions config loaded` |
| ⑤ 删除恢复 | `DELETE /permissions` | 删行 + `GET` → 404 |

### PUT 请求体（阶段 ①）

```json
{
  "body": {
    "enabled": true,
    "defaults": "ask",
    "tools": {
      "bash": "deny",
      "todo_list": "allow"
    },
    "rules": [
      { "id": "e2e_allow_echo", "pattern": "echo *", "action": "allow" }
    ],
    "approval_overrides": [],
    "file_guard": {
      "workspace": { "rw_enabled": true },
      "global": {},
      "trusted_exec_directory": [],
      "tool_bindings": {}
    }
  }
}
```

### 日志标记常量

| 常量 | 含义 | 典型位置 |
|------|------|----------|
| `HOT_RELOAD_MARKER` | `[ManagerWsClient] permissions_config hot-reload` | `.jiuwenclaw/agent/.logs/gateway.log` |
| `GATEWAY_COLD_LOAD_MARKER` | `[App] permissions config loaded from Gateway DB` | `gateway/gateway.log`（stdout） |
| `AGENT_COLD_LOAD_MARKER` | `[AgentServer] permissions config loaded from Gateway DB` | `C:/home/app/.logs/gateway.log`（Windows） |
| `DELETE_MARKER` | `[ManagerWsClient] permissions_config deleted, reverted to yaml fallback` | `.jiuwenclaw/agent/.logs/gateway.log` |

---

## 阶段 ① 热更新（Hot Reload）

**目标**：Manager REST 写入后，经 WS 推送到 Gateway，**即时生效**（无需重启）。

**测试代码**：`http.put(stack.permissions_api(), json=...)` → `wait_for_log(structured_gateway_log, HOT_RELOAD_MARKER)`

**关键文件**

| 环节 | 路径 |
|------|------|
| REST 入口 | `packages/jiuwenclaw-ee/claw_manager/.../application_config_routers.py` → `upsert_permissions_config` |
| Manager 写库+推送 | `packages/jiuwenclaw-ee/claw_manager/.../permissions_config.py` → `PermissionsConfigService.upsert` |
| WS 路由 | `packages/jiuwenclaw-ee/gateway/.../manager_ws_client_router.py` → `apply_config_push` |
| Gateway 热更新 | `packages/jiuwenclaw-ee/gateway/.../permissions_config.py` → `apply_permissions_config` |
| 引擎生效 | `jiuwenclaw/agentserver/permissions/config_loader.py` → `apply_permissions_config_payload` |

---

## 阶段 ② GDB 持久化

**测试代码**：`read_gdb_permissions_row(stack.gdb_path, jiuwenclaw_id)` → 断言 `body.tools.bash == "deny"`

**Helper**：`e2e_helpers.read_gdb_permissions_row`

---

## 阶段 ③ Agent 冷启动

**测试代码**：`run_web_channel_user_request(...)` → `wait_for_log(structured_agent_process_gateway_log, AGENT_COLD_LOAD_MARKER)`

Agent 子进程启动时 `app_agentserver.py` 调用 `reload_permissions_from_gateway_db()`。

---

## 阶段 ④ Gateway 冷启动

**测试代码**：`stack.restart_gateway()` → `wait_for_log(gateway.log, GATEWAY_COLD_LOAD_MARKER)`

Gateway 重启时 `app_gateway.py` 调用 `reload_permissions_from_gateway_db()`。

---

## 阶段 ⑤ 删除恢复

**测试代码**：

1. `http.delete(stack.permissions_api())`
2. `wait_for_log(..., DELETE_MARKER)`
3. `read_gdb_permissions_row` → `None`
4. `http.get(stack.permissions_api())` → `404`

删除 GDB 行后，运行时回退到 `config.yaml::permissions`（yaml fallback）。

---

## 本地运行

```bash
cd tests/system_tests/enterprise
cp .env.example .env   # 首次
pytest test_permissions_config_process_e2e.py -s -m system
```

默认带 `@pytest.mark.skip(reason="skip ci")`，与 `test_logging_config_process_e2e.py` 一致；本地去掉 skip 或显式 `-k` 运行。
