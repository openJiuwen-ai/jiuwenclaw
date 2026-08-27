# 合并记录：origin/xiaoyi_0.2.4.beta3 → 本地（2026-08-28）

基点 `23b0ed548`；本地 22 提交（桌面集成/密钥包/权限档位），远程 10 提交（mcp/run 直连、权限增强、沙箱默认关、cron 修复等）。

## 冲突解决

### 1. useraccess_runtime.py（语义冲突，已决策：密钥包投递凭证）
- 取**远程版**为底：云插件直连 mcp/run，握手 `businessCredential`，本地中转（relay/localAuth）路径整体移除。
- 在远程版上补：`resolve_business_credential()` 增加密钥包兜底 —— env `CLAW_BUSINESS_CREDENTIAL` 优先（实验室/旧形态），为空时读 `secrets_bootstrap.get_secret("businessCredential")`（桌面 stdin 密钥包形态，env 已剔密）。
- **桌面端需配套**：密钥包需下发 `businessCredential` 键（顶层、camleCase，与 proxyKey/e2aToken 同规）。
- 本地 `build_local_relay_headers` 随远程删除（远程已无任何引用）；relay 相关测试随远程重写一并移除。

### 2. interface.py（行尾冲突 + 双向小补丁）
- 根因：基点 CRLF，本地已转 LF → 整文件冲突。取本地 LF 版为底。
- 手工合入远程 21 行语义补丁：member_name 成员帧落盘守卫（delta 不进主应答 buffer、成员帧不触发冲刷、通用路径跳过防双写；chunk/dict 两分支同规）。
- 本地 +10 行（skills.toggle 扇出刷新）保留。
- 合并后与远程 `-w` 语义 diff 仅剩本地 10 行，验证通过。
- `.gitattributes` 新增 `interface.py text eol=lf` 钉住行尾，防再次全文件冲突。

### 3. agent_ws_server.py（import 并集）
- 本地 `e2a_transports`（命名管道传输）与远程 `wire_trace.trace_inbound`（E2A 落盘）两个 import 均保留。

## 自动合并无冲突（已抽查语义正确）
- ws_send.py：远程 trace_outbound + 本地 _transport_send 重构，顺序正确。
- app_agentserver.py：远程 should_prepare_workspace 与本地改动区域不重叠。
- test_invoke_meta.py：远程重写版生效，relay 断言为"忽略 relay"语义，与直连方向一致。

## 测试结果
- 通过：test_invoke_meta / test_team_helpers / test_secrets_bootstrap（134）、e2a_transports / xiaoyi_client_variables / xiaoyi_invocation_adapter / session_permissions_overlay / stream_event_rail_todo / cron_runtime 等（183+51）。
- **5 个失败均为远程分支固有**（在纯远程 worktree 复现，非合并引入）：
  - test_interrupt_helpers 4 个：远程代码调用 `ToolPermissionHost(persist_session_allow_rule=...)`，需 registry 版 openjiuwen 0.1.16.post2；本地 venv 为 git 钉版 commit 5297c25a 无此参数。uv.lock 双方钉版口径不同（本地 git hotfix / 远程 registry），**保持本地 git 钉版不变**，待 openjiuwen hotfix 分支合入该接口后自然恢复。
  - test_cron_scheduler::test_agent_wake_uses_unary_cron_channel 1 个：远程测试断言自相矛盾（session_id 既等于 "cron_agentserver_allocated" 又要求 endswith job.id），远程分支自身即失败，待上游修复。

## 合入后回归清单
1. 云插件调用全链路（依赖桌面密钥包下发 businessCredential）。
2. 桌面三档位权限切换（远程 permissions.enabled 默认已改 true + 会话 overlay）。
3. 团队会话成员归因/防双写。
4. frozen exe 启动 + 沙箱默认关闭行为。
