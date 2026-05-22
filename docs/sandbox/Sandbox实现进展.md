# Sandbox 实现进展

本文记录当前代码中已经落地的 Sandbox 相关改造进展，便于和目标设计文档、SandboxClient 接口文档对照。

## 1. 当前结论

目前已经完成了不依赖沙箱反向建链细节的网关侧路由骨架：

- VibeSkill 创建会话时可以接收并保存 `user_id`。
- VibeSkill 后续转发给 Gateway / MessageHandler 的消息会携带 `user_id`。
- Gateway 在配置开启后直接使用 `SandboxRouterAgentClient` 作为 MessageHandler 的 agent client，不再创建默认 `WebSocketAgentServerClient`。
- Router 已按 `user_id` 维护 sandbox runtime、并实现并发创建保护、实例上限、FIFO 等待队列和 idle 回收。
- 真实“沙箱内 Agent 主动连回 Gateway”的连接协议尚未实现，目前在 Router 的创建/删除流程中通过内部方法保留扩展点。

因此，当前阶段可以验证“identity 透传、session 映射、router 调度逻辑、明确失败路径”；还不能验证真实 sandbox agent 与 Gateway 建链后的端到端执行。

## 2. 已实现内容

### 2.1 VibeSkill session 与 user_id

`POST /api/v1/session` 已支持在 body 中传入 `user_id` 或 `userId`。

- 传入 `user_id` 时，写入当前 `VibeSkillSession.metadata["user_id"]`。
- 未传入时，使用创建出的 `session_id` 作为 fallback user key。
- SkillCreate 和 Standard 两种模式都使用同一套规则。
- `VibeSkillSessionStore` 新增 `get_user_id(internal_id)`，用于后续消息补充路由身份。

### 2.2 VibeSkill session 映射

`VibeSkillSessionStore` 当前只维护进程内 session 状态，不做落盘。内存中维护的内容包括：

- session 列表
- external/internal session 映射
- session state
- mode
- metadata
- created_at / updated_at

`get_or_create`、`bind_external`、`set_state`、`set_metadata`、`delete_session` 保持内存 API 行为。Gateway 重启后不恢复 VibeSkill session 映射，调用方需要重新创建 session。

### 2.3 消息 identity 透传

VibeSkill 发往 MessageHandler 的 session 相关请求已经补充 `user_id`：

- SkillCreate：`skilldev.start`、`skilldev.parse_skill`、question reply、文件、版本、导出等请求。
- Standard：`chat.send` 以及创建 agent session 的路径。
- E2A normalize 路径保留并输出 `user_id`，Router 优先使用 `E2AEnvelope.user_id` 路由。

如果某条消息无法从 session store 找到 `user_id`，会 fallback 到 `session_id`，避免直接拒绝请求。

### 2.4 Sandbox Router 骨架

新增 `SandboxRouterAgentClient`，对 MessageHandler 暴露兼容 `AgentServerClient` 的接口：

- `send_request`
- `send_request_stream`
- `close`

Router 内部 runtime key 规则：

```text
有 user_id: vibeskill:user:{user_id}
无 user_id: vibeskill:session:{session_id}
```

Router 维护的 runtime 信息包括：

- `sandbox_id`
- `agent_client`
- `status`
- `task_count`
- `created_at`
- `last_active_at`
- `metadata`

当前 Router 负责：

- 同一 user key 复用同一个 sandbox runtime。
- 不同 user key 创建不同 sandbox runtime。
- per-key async lock，避免同一用户并发请求重复创建 sandbox。
- `max_sandboxes` 上限控制。
- FIFO 等待队列和 queue timeout。
- idle timeout 回收空闲 runtime。
- 当容量已满且存在 IDLE 的沙箱 runtime 时，新请求会先触发 IDLE runtime 清理，再创建新 runtime。
- 当 runtime 结束任务进入 IDLE 且等待队列非空时，会立即清理该 runtime 并唤醒等待者。

### 2.5 SandboxClient 职责收敛

当前 Gateway 侧只依赖 SandboxClient 的生命周期能力：

- `create_sandbox`
- `delete_sandbox`
- `close`

队列、上限、identity、runtime 复用等逻辑都放在 Router 中，不放进 SandboxClient。

### 2.6 未确定连接协议的扩展点

沙箱 agent 的真实反向建链协议尚未确定，Router 在创建和删除 runtime 时预留了内部扩展点：

```python
_wait_agent_connected(sandbox_id, routing_key, metadata) -> AgentServerClient
_disconnect_agent_client(sandbox_id, agent_client) -> None
```

当前实现状态：

- 测试中通过测试子类覆盖 `_wait_agent_connected` 来模拟 sandbox agent client。
- 生产路径如果开启 Router 但真实建链逻辑尚未实现，会在创建 sandbox 后返回明确错误：`sandbox agent connection is not configured`。
- 该错误是预期行为，表示不会静默退回共享 AgentClient。

## 3. 配置与运行状态

新增配置开关：

```yaml
gateway:
  sandbox_routing:
    enabled: false
```

默认关闭，因此不会影响现有单 AgentClient 行为。关闭时 Gateway 使用原有 `WebSocketAgentServerClient`；开启时 Gateway 使用 `SandboxRouterAgentClient`，不创建默认 `WebSocketAgentServerClient` 作为兜底。

开启 Router 时需要配置 sandbox client：

```yaml
gateway:
  sandbox_routing:
    enabled: true
    max_sandboxes: 4
    queue_enabled: true
    queue_max_size: 100
    queue_timeout_seconds: 60
    idle_timeout_seconds: 300
    idle_check_interval_seconds: 30

  sandbox_client:
    api_base: "http://127.0.0.1:8000"
    template_id: "your-template-id"
    duration_seconds: 900
    timeout_seconds: 120
    metadata: {}
```

如果 `gateway.sandbox_routing.enabled=true` 但未配置 `gateway.sandbox_client.api_base`，首次创建 sandbox runtime 时会返回配置缺失错误。

## 4. 当前未完成项

以下部分仍等待沙箱反向建链穿刺结果：

- 沙箱内 Agent 如何主动连接回 Gateway。
- Gateway 如何将 `sandbox_id` / `routing_key` 与反向连接绑定。
- Router 内部真实反向建链方法的生产实现。
- 真实 sandbox agent 建链后的端到端请求和流式响应验证。

## 5. 已覆盖测试

当前已有单元测试覆盖：

- VibeSkill session store 内存状态与 `user_id` 映射。
- 创建 session 时保存 `user_id`，未传时 fallback 到 `session_id`。
- 删除 session 后内存映射同步删除。
- `message_to_e2a` 保留 `user_id`。
- 同一 `user_id` 复用 runtime。
- 不同 `user_id` 创建不同 runtime。
- 同一 `user_id` 并发请求只创建一次 sandbox。
- 达到 `max_sandboxes` 后进入 FIFO 队列。
- runtime 删除后队列出队。
- 容量已满时优先清理 IDLE runtime。
- runtime 进入 IDLE 且等待队列非空时立即清理。
- `task_count` 在成功、异常、流式取消路径回落。
- idle timeout 只回收 `task_count == 0` 的 runtime。

可运行的目标测试：

```bash
uv run --no-sync pytest -o addopts='' \
  tests/unit_tests/channel/test_vibeskill_session_store.py \
  tests/unit_tests/gateway/test_sandbox_router.py \
  tests/unit_tests/e2a/test_gateway_normalize.py
```

## 6. 下一步

下一阶段应优先补齐真实反向建链逻辑：

1. 明确沙箱内 Agent 主动连回 Gateway 的协议与鉴权信息。
2. 实现 Router 内部 `_wait_agent_connected`，将反向连接绑定到 `sandbox_id` 和 `routing_key`。
3. 在 Router 开启模式下跑通 VibeSkill 到 sandbox agent 的端到端请求。
