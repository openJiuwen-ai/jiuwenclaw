# 分布式 Team

本文面向 **开发与联调**：说明分布式 Team（`team.runtime.mode=distributed` + `pyzmq`）在现有 AgentServer / TeamManager 中的配置入口、代码落点，以及如何在本机或双目录起 leader/teammate 做闭环验证。不要求单独的运行时二进制；业务入口仍为统一 AgentServer。

配置主文件一般为 `~/.jiuwenclaw/config/config.yaml`；可通过环境变量 `JIUWENCLAW_CONFIG_DIR` 指向其它目录（与 [配置说明](配置信息.md) 一致）。

English: [Distributed Team](../en/DistributedTeam.md)

---

## 1. 总览

| 项目 | 说明 |
|------|------|
| **模式开关** | `team.runtime.mode`: `local \| distributed` |
| **进程角色** | `team.runtime.role`: `leader \| teammate` |
| **传输** | `team.transport.type`: `inprocess \| pyzmq`；分布式联调通常用 `pyzmq` |
| **入口类** | `TeamManager`（`jiuwenclaw/agentserver/team/team_manager.py`）：构建 `TeamAgentSpec` 前会做 transport/身份字段归一化 |
| **配置装载** | `load_team_spec_dict()`（`jiuwenclaw/agentserver/team/config_loader.py`）：leader / `predefined_members` 的 name 与 display_name 兼容 |
| **样例** | 仓库内 `jiuwenclaw/resources/config.team.distributed.leader.yaml` / `config.team.distributed.teammate.yaml`（当前分角色模板） |

**会话语义**：与原 Team 一致倾向 **单活 session**——新建 session 的 Team 前会清理其它 session 的 Team 资源；分布式下不在本文档引入多 session 并发路由层。

---

## 2. 你需要关心的配置键

以下为分布式联调最常改动的键（模板见仓库 `config.team.distributed.leader.yaml` / `config.team.distributed.teammate.yaml`）。

| 键 | 含义 |
|----|------|
| `team.runtime.mode` | `distributed` 启用分布式语义 |
| `team.runtime.role` | 本进程是 `leader` 还是 `teammate` |
| `team.runtime.member_name` | teammate 侧默认身份；被 bootstrap 后会接管为 leader 动态请求的成员名 |
| `team.transport.type` | `pyzmq` |
| `react.a2x_registry` | teammate 启动时注册空闲节点；leader 组队时从注册中心预约空闲 teammate。**注册中心不是 jiuwenclaw 内置组件**，须从上游 [agent-protocol（feature/Agentregistry）](https://gitcode.com/openJiuwen/agent-protocol/tree/feature/Agentregistry) 单独拉取并按该仓说明独立部署 |
| `team.transport.params` | 本进程的 `direct_addr` / `bootstrap_direct_addr`、`pubsub_*` 等；leader 不需要预置 teammate 的 `known_peers` |
| `team.predefined_members` | 兼容旧静态成员声明；当前 blank teammate 联调不要求 leader 配置该项 |
| `team.storage` | 多进程场景下 `connection_string` 需指向 **各方可见的同一 DB**（如共享路径下的 sqlite） |

---

## 3. pyzmq Transport 字段归一化

当 `transport.type == pyzmq` 且 **`pubsub_publish_addr` / `pubsub_subscribe_addr` 尚未同时存在** 时，`TeamManager` 会根据 `params.leader` / `params.teammate` 等拓扑信息自动补全：

| 字段 | 说明 |
|------|------|
| `direct_addr` | 本进程直接通信地址 |
| `pubsub_publish_addr` | 发布地址 |
| `pubsub_subscribe_addr` | 订阅地址 |
| `known_peers` / `bootstrap_peers` | 节点发现列表 |
| `metadata.pubsub_bind` | 是否绑定 pubsub（leader=True，teammate=False） |

默认端口：
- Leader: `direct_port=18555`, `pub_port=18556`, `sub_port=18557`
- Teammate: `direct_port=18600`

---

## 4. PostgreSQL Bootstrap（Leader 角色）

当 `team.storage.type=postgresql` 且当前进程角色为 `leader` 时，启动时会自动检测 PostgreSQL 可用性：

1. 检查 `pg_isready -h <host> -p <port>`
2. 若不可达，尝试启动本地 PostgreSQL 集群：
   - 先尝试 `pg_ctlcluster <version> <cluster> start`
   - 失败则尝试 `systemctl start postgresql` 或 `service postgresql start`
3. 等待最多 30 秒确认服务就绪

配置示例：

```yaml
team:
  storage:
    type: postgresql
    params:
      connection_string: postgresql+asyncpg://user:pass@host:5432/teamdb
```

---

## 5. teammate_mode 与 spawn_mode

| 配置 | 值 | 说明 |
|------|-----|------|
| `teammate_mode` | `build_mode`（默认） | teammate 通过 build 流程构建 |
| `spawn_mode` | `inprocess`（默认） | 指成员 runtime 在**各自进程内部**以内嵌方式创建（不是额外 fork 子进程）；分布式场景下 leader 与 teammate 仍是独立进程/节点，通过 pyzmq + bootstrap 协作 |

---

## 6. 代码落点（改 bug / 跟逻辑时从这里进）

### 3.1 `TeamManager._load_team_spec`

流程：`load_team_spec_dict(session_id)` → **`_normalize_team_identity_fields`** → 若判定为分布式则 **`_normalize_distributed_transport_fields`** → `TeamAgentSpec.model_validate`。

分布式判定：见 **`_is_distributed_mode`**（`runtime.mode == distributed` 或 `transport.type == pyzmq`）。

### 3.2 pyzmq 字段归一化（bootstrap 语义）

当 `transport.type == pyzmq` 且 **`pubsub_publish_addr` / `pubsub_subscribe_addr` 尚未同时存在** 时，会根据 `params.leader` / `params.teammate` 等拓扑信息补全 **`direct_addr`、`pubsub_*`、`metadata.pubsub_bind`**。当前推荐分角色模板已直接给出运行时字段；teammate 发现由 A2X 注册中心完成，不要求 leader 配置静态 peer。

### 3.3 `config_loader`

- **`_build_leader_spec`**：补齐 `name` 与 `display_name` 的兼容。
- **`_build_predefined_members`**：必须有 `member_name`，且必须有 **`name` 或 `display_name`**，否则跳过并打日志。

### 3.4 当前分支的控制面 / 数据面实现（重点）

当前实现已按“控制面建连、数据面跑业务”分层：

- **控制面（Control Plane）**：
  - teammate 启动后将自己的 `bootstrap_direct_addr` 注册为 A2X 空闲节点。
  - leader 配置中不包含具体 teammate 名称或地址；只配置 A2X 注册中心地址和 dataset。
  - leader 在组队/`spawn_member` 时调用 `reserve_blank_agents` 预约空闲 teammate，并使用注册中心返回的 `service_id` / `endpoint` 发送 bootstrap。
  - leader 在 `spawn_member` 后通过 direct ZMQ 发送 `jiuwen.remote_teammate_bootstrap.direct`。
  - teammate 监听 `bootstrap_direct_addr` 接收 bootstrap，应用 leader 路由并完成接管。
  - bootstrap 成功后，teammate 会使用本进程持有的 A2X `service_id` 调用 `replace_agent_card`，把自己的注册中心 card 从 blank/idle 替换为 busy/member，避免 reservation TTL 过期后再次被 leader 当作空闲 teammate 预约。
  - ACK 使用 direct 传输层确认（不再依赖 DB ACK 消息链路）。
  - reservation 生命周期：bootstrap 失败时 leader 立即 release；bootstrap 成功后 leader 不再主动 release 该 reservation。
  - Team 解散时，leader 会通过 direct ZMQ 向已预约 teammate 发送 `jiuwen.remote_team_destroy.direct`。teammate 收到后清理本地 session/team runtime，并通过 A2X `replace_agent_card` 将自己的 agent card 重置为空闲 teammate；`bootstrap_direct_addr` 监听端口保持常驻，用于接收下一次 bootstrap。
  - teammate 侧 bootstrap 会临时构建一个辅助 `TeamAgent` 来读取共享 DB/context，但该 helper 不应长期缓存到 `TeamManager._team_agents`，构建完 context 后必须停止 runtime/messager 并移出 cache。
  - 真正执行任务的 dynamic teammate runtime 会重新分配本进程内可用的 loopback `direct_addr`，避免复用 agent-core 默认的 `tcp://127.0.0.1:16000` 导致 publish/event 端口冲突。
- **数据面（Data Plane）**：
  - 任务创建、认领、完成、普通团队消息仍走 team 业务链路（共享存储 + team runtime）。
  - `team.storage` 共享的是任务、成员状态、消息等业务状态；默认 `team-workspace` 文件目录仍按各进程自己的 HOME 生成，不等同于跨进程物理共享目录。
- **兜底策略（当前）**：
  - leader 侧 direct bootstrap 发送失败后，**不再 fallback 到 `team_message`**。
  - teammate 侧 bootstrap 接收也**不再使用 DB 轮询兜底**。
- **local 模式隔离**：
  - `TeamManager` 只在分布式配置下 attach remote bootstrap hooks；local / inprocess Team 不会执行 A2X 注册、预约或远端 bootstrap 逻辑。

---

## 4. 当前推荐配置方式（完整模板）

仓库内分角色模板已经是**完整 `config.yaml`**，包含基础 agent/model 配置、A2X 注册中心配置、顶层 `team` 运行时标记，以及 `modes.team.jiuwen_team` 的实际 TeamAgentSpec 配置。部署时可以直接复制为各自 HOME 下的配置文件，不需要再和默认 `config.yaml` 手工合并。

- `jiuwenclaw/resources/config.team.distributed.leader.yaml`
- `jiuwenclaw/resources/config.team.distributed.teammate.yaml`

用法（建议）：

1. 复制对应完整模板到各自配置目录（如 `<LEADER_HOME>/.jiuwenclaw/config/config.yaml` 和 `<TEAMMATE_HOME>/.jiuwenclaw/config/config.yaml`）。
2. 按环境替换以下字段：
   - `react.a2x_registry.base_url` / `dataset`（leader 和 teammate 指向同一注册中心数据集）。
   - teammate 的 `team.transport.params.bootstrap_direct_addr` 或 `react.a2x_registry.endpoint`（用于向注册中心发布可连接地址）。
   - `team.storage.params.connection_string`（leader 与 teammate 必须一致）。
   - teammate 的 `team.runtime.member_name`（仅标识本进程默认身份；leader 不再靠它定位地址）。
   - `team.transport.params.*` 与 `modes.team.jiuwen_team.transport.params.*` 中的端口/IP（多机部署不要使用只在本机可达的 `127.0.0.1`）。
3. 启动前准备模型相关环境变量，例如 `API_BASE` / `API_KEY` / `MODEL_PROVIDER` / `MODEL_NAME`；模板中的敏感值均保持为环境变量占位符或空字符串。

最小可用示例（复制完整模板到当前运行目录）：

```bash
# leader
mkdir -p "<LEADER_HOME>/.jiuwenclaw/config"
cp "<REPO_ROOT>/jiuwenclaw/resources/config.team.distributed.leader.yaml" \
  "<LEADER_HOME>/.jiuwenclaw/config/config.yaml"

# teammate
mkdir -p "<TEAMMATE_HOME>/.jiuwenclaw/config"
cp "<REPO_ROOT>/jiuwenclaw/resources/config.team.distributed.teammate.yaml" \
  "<TEAMMATE_HOME>/.jiuwenclaw/config/config.yaml"
```

---

## 5. 本地双目录联调（推荐布局）

用 **两个独立 HOME**（或两套 `JIUWENCLAW_CONFIG_DIR`）分别模拟 leader 与 teammate，避免配置互相覆盖。

下文用占位符：

- **Leader 配置目录**：`<LEADER_HOME>/.jiuwenclaw/config`
- **Teammate 配置目录**：`<TEAMMATE_HOME>/.jiuwenclaw/config`

两侧需约定一致：

- `team.runtime.mode=distributed`
- `team.runtime.role` 分别为 `leader` / `teammate`
- `react.a2x_registry` 指向 **同一注册中心数据集**
- teammate 发布自己的 bootstrap endpoint，leader 不需要知道 teammate 地址
- `team.storage.params.connection_string` 指向 **同一数据库**（如 PostgreSQL，或各节点都能访问的 sqlite 文件）

注意：分布式模板已经显式配置了 team workspace root：

```yaml
team:
  workspace:
    enabled: true
    root_path: ${JIUWEN_TEAM_WORKSPACE_ROOT:-/tmp/jiuwenclaw/shared_workspace/jiuwen_team}
    version_control: false
```

部署时应在所有节点使用同一个 `JIUWEN_TEAM_WORKSPACE_ROOT`，并只把该目录作为 NFS 共享目录。不要共享 `.agent_teams`，该目录保存 team.db、成员 workspace、symlink 等本地运行状态，多节点共享会破坏 kickoff 和 workspace 初始化。

NFS server/client 脚本、连通性检查、取消挂载：`scripts/nfs/README.md`。

未显式配置共同可见的 workspace root 时，leader 和 teammate 会分别在各自 HOME 下创建：

- `<LEADER_HOME>/.jiuwenclaw/.agent_teams/<team_name>/team-workspace`
- `<TEAMMATE_HOME>/.jiuwenclaw/.agent_teams/<team_name>/team-workspace`

这两个路径名字相同但不是同一个物理目录。需要让成员写出的文件被 leader 直接读取时，应配置双方都可访问的 `team.workspace.root_path`，或通过消息 / DB / 文件传输工具回传结果。

端口与防火墙需保证 leader/teammate 机器互通（多机时把示例中的 `127.0.0.1` 换成真实 IP）。

---

## 6. 启动命令示例（四个终端）

以下路径请替换为你的本机 `<REPO_ROOT>`、`<LEADER_HOME>`、`<TEAMMATE_HOME>`。

### 6.1 A2X 注册中心

注册中心进程与 Leader/Teammate **分离部署**：请从上游代码仓拉取并在独立环境中运行。

参考 [agent-protocol 的 Agent Team 快速启动说明](https://gitcode.com/openJiuwen/agent-protocol/blob/feature/Agentregistry/README_forAgentTeam.md)。`0.1.6` 起默认安装就是 Agent Team 精简版，只包含 SDK、FastAPI、uvicorn 等轻量依赖；注册中心服务端无需预置数据，也不需要配置 LLM。teammate 注册、leader 查询/预约、reservation lease 都由 `jiuwenclaw` 客户端逻辑完成。

安装（要求 Python >= 3.10）：

```bash
git clone -b feature/Agentregistry https://gitcode.com/openJiuwen/agent-protocol.git
cd agent-protocol
pip install -e .
```

单机部署（注册中心、leader、teammate 在同一台机器）：

```bash
a2x-registry
```

默认监听 `127.0.0.1:8000`，leader 和 teammate 配置：

```yaml
react:
  a2x_registry:
    base_url: http://127.0.0.1:8000
```

多机部署时，让注册中心监听可被其它机器访问的地址，并放行防火墙/安全组端口：

```bash
a2x-registry --host 0.0.0.0
a2x-registry --host 0.0.0.0 --port 8080
```

此时 leader 和 teammate 的 `react.a2x_registry.base_url` 应填写注册中心机器的 IP、域名或 HTTPS 反向代理地址，例如 `http://192.168.1.10:8000` 或 `https://registry.example.com`。

### 6.2 Teammate（仅 AgentServer）

```bash
HOME="<TEAMMATE_HOME>" \
GIT_AUTHOR_NAME="teambot" \
GIT_AUTHOR_EMAIL="teambot@example.com" \
GIT_COMMITTER_NAME="teambot" \
GIT_COMMITTER_EMAIL="teambot@example.com" \
AGENT_SERVER_PORT=28193 \
uv run python -m jiuwenclaw.app_agentserver
```

启动成功后，teammate 会把自己的 `bootstrap_direct_addr` 注册为 blank agent，例如 `endpoint=tcp://127.0.0.1:28610`。

### 6.3 Leader（Gateway + AgentServer）

```bash
HOME="<LEADER_HOME>" \
GIT_AUTHOR_NAME="teambot" \
GIT_AUTHOR_EMAIL="teambot@example.com" \
GIT_COMMITTER_NAME="teambot" \
GIT_COMMITTER_EMAIL="teambot@example.com" \
AGENT_SERVER_PORT=28192 \
GATEWAY_PORT=29101 \
WEB_PORT=29100 \
uv run python -m jiuwenclaw.app
```

Leader 不需要配置 teammate 的静态 endpoint；`spawn_member` 时会从注册中心 `reserve_blank_agents` 取得空闲 teammate。

### 6.4 Web 前端（可选）

```bash
cd "<REPO_ROOT>/jiuwenclaw/channels/web/frontend"
VITE_WS_BASE="ws://localhost:29100" npm run dev -- --host 0.0.0.0 --port 5173
```

若 workspace 未配置 Git 用户信息，启动前建议带上 `GIT_AUTHOR_*`，否则涉及 git 的工具链可能报错。

---

## 7. 验证 Prompt（团队协作闭环）

在前端（或等价通道）可用下列指令做强约束联调（可按环境改写）：

```text
【分布式Team联调验证指令】
你必须以 team 模式执行，并严格按顺序完成以下步骤，不允许跳过，不允许直接给最终答案：
1. 调用 team.build_team 创建团队（leader + teammate_1）。
2. 调用 team.create_task 创建任务，标题为“计算1+1”，并将 assignee 指定为 teammate_1。
3. 调用 team.send_message 给 teammate_1，要求其返回“1+1”的计算结果与一句简短说明。
4. 等待 teammate_1 完成任务并回传消息。
5. 调用 team.view_task 查看该任务状态，确认是 completed（或等效完成态）。
6. 最后由 leader 汇总输出最终答案。
输出格式必须为：
- STEP1: <执行结果>
- STEP2: <执行结果>
- STEP3: <执行结果>
- STEP4: <执行结果>
- STEP5: <执行结果>
- FINAL: <最终答案>
如果任一步失败，请明确输出 FAILED_AT_STEP=<步骤号> 和错误原因。
```

### 成功判定（简要）

- 前端能持续收到 `chat.delta`，并最终出现 `chat.final`。
- Leader 日志：可见 Team 创建、`team.*` 工具调用等。
- Teammate 日志：可见参与会话与任务协同。

---

## 8. 常见问题排查

| 现象 | 处理方向 |
|------|----------|
| `Address already in use (tcp://0.0.0.0:18555)` | pyzmq 绑定端口被占用；释放端口或改配置中的 `direct_port` / 拓扑端口。 |
| `git commit failed ... Author identity unknown` | 为启动命令补充 `GIT_AUTHOR_*` / `GIT_COMMITTER_*`。 |
| 前端无响应但后端已启动 | 确认前端使用 `VITE_WS_BASE`（而不是误用 `VITE_WS_URL`）。 |
| teammate 连不上 leader | 检查防火墙、leader 在 bootstrap 中下发的地址是否仍为 `127.0.0.1`（多机需使用真实地址）。 |
| leader 没有从注册中心拿到 teammate | 检查注册中心日志是否有 `POST /api/datasets/<dataset>/reservations 200 OK`；检查 teammate 是否已成功注册 blank agent。 |
| teammate 被重复抢占 | 检查 bootstrap 成功后 teammate 日志是否出现 `teammate agent card replaced ... member_name=...` / `teammate registry card replace ... replaced=True`；若缺失，注册中心仍认为它是 blank/idle，reservation TTL 过期后会再次被预约。也需确认 leader 没有在 bootstrap 成功后误 release reservation。 |
| Team 解散后 teammate 无法再次 bootstrap | 检查 teammate 日志是否出现 `teammate applied team destroy notification ... cleaned=True`；若为 `cleaned=False` 或 `cleanup failed`，旧 team runtime / messager 可能未释放干净。 |
| `Address already in use (tcp://127.0.0.1:16000)` | teammate 进程内可能存在未清理的辅助 `TeamAgent` 或旧 dynamic runtime；确认 bootstrap helper 构建 context 后已从 `TeamManager` cache 移除并 stop messager，dynamic runtime 已 retarget 到新 `direct_addr`。 |
| leader 与 teammate 都有 `team-workspace/result.txt` 但内容不同 | 默认 workspace 是各进程 HOME 下的本地目录，不是共享文件系统；需要显式使用共同可见路径或让 teammate 通过消息/存储回传结果。 |

---

## 9. 附录：与原单机 Team 的差异速查

| 维度 | 单机 / inprocess 倾向 | 分布式（本指南范围） |
|------|------------------------|----------------------|
| 入口 | 同一 `TeamManager` | 同一入口，按配置分流 |
| 传输 | `inprocess` 为主 | `pyzmq`，需地址与端口可达 |
| 部署 | 单进程 | leader / teammate 可分进程、可多机 |
| 配置 | 本地 `team` 即可 | 需 `runtime` + `transport` + 共享 storage 约定 |

更完整的拓扑与演进若单独成文，可与本指南并列维护；日常开发以 **第 2～7 节** 为准。
