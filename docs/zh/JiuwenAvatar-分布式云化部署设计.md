# JiuwenAvatar 分布式云化部署整体设计

## 1. 文档目标

本文描述 JiuwenAvatar 从**单机/联调级部署**演进到**分布式云化 SaaS 部署**的整体设计方案，重点回答：

1. 业务上要实现什么（多租户、每人创建分身、分身绑定 AgentServer 执行）。
2. 技术上如何复用 JiuwenSwarm `dev/enterprise_kub` 分支的企业级能力。
3. 控制面、执行面、管理面如何分工与协作。
4. 与现有 Avatar 自动任务闭环（Trigger / Mission / Report）如何衔接。
5. 分阶段落地路径与风险。

### 1.1 关联文档

| 文档 | 关系 |
|------|------|
| [JiuwenAvatar 自动任务闭环详细设计](JiuwenAvatar-详细设计.md) | Avatar 业务域模型与单机闭环，本文在其上扩展云化部署 |
| [分布式 Team](分布式Team.md) | A2X + pyzmq 多机 Team 联调，**不是**本文云化主路径 |
| [E2A 协议](E2A-protocol.md) | Gateway ↔ AgentServer 通信协议 |
| [单机多实例运行](单机多实例运行.md) | 同机多进程隔离，与 K8s 多租户互补 |
| JiuwenSwarm `dev/enterprise_kub` | 企业级 K8s 部署、RuntimeManagement、Manager 等上游参考实现 |

### 1.2 不在本文范围

- 不重复描述 JiuwenSwarm 基础 Gateway / MessageHandler / Skill / Memory 等通用能力。
- 不展开 A2X 分布式 Team leader/teammate bootstrap 细节（见 [分布式 Team](分布式Team.md)）。
- 不替代 K8s 运维手册（部署操作细节参考 enterprise_kub `deploy/README.md`）。

---

## 2. 0 层设计：业务目标

### 2.1 原始诉求

```text
分布式云化部署
  → 服务可部署在 Kubernetes 集群，支持水平扩展与高可用

每人可在云服务上创建分身实例
  → 多租户、多用户；用户从 Persona 模板创建自己的 Avatar

分身关联对应 AgentServer 实例执行
  → 执行请求按稳定路由键落到指定 AgentServer 进程/Pod
  → AgentServer 按 avatar_id 注入 Persona / Skill / CodingEngine 上下文
```

### 2.2 目标能力闭环

在现有 Avatar 闭环基础上扩展为：

```text
租户 / 用户
  → 创建 Avatar（Persona 实例化）
  → 绑定运行时路由（service_id / agent_id）
  → 配置 Trigger（自动唤醒）
  → Mission 执行（Gateway 调度 + AgentServer 执行）
  → Report 沉淀 + 前端展示
```

与单机版闭环对比：

| 维度 | 单机版（现状） | 云化版（目标） |
|------|----------------|----------------|
| 部署 | 单 Gateway + 单 AgentServer | K8s 多副本 Gateway + AgentServer 池 |
| 用户 | 单用户工作区 | 多租户 / 多用户 IAM |
| 分身 | 本地 `PersonaManager` | 云端持久化 + 按用户隔离 |
| 路由 | 固定 WebSocket 连一个 AgentServer | SessionMap + RuntimeManagement 按 service_id 调度 |
| Trigger 调度 | 单 Gateway 进程 | 主备 Gateway + 分布式锁 |
| 存储 | JSON 文件 | PostgreSQL / Redis |

### 2.3 路径选择：enterprise_kub vs 分布式 Team

| 路径 | 调度模型 | 适用场景 | 是否采用 |
|------|----------|----------|----------|
| **enterprise_kub** | RuntimeManagement + K8s Pod 池 + SessionMap | 多租户 SaaS、生产云 | **主路径** |
| **A2X 分布式 Team** | A2X 注册中心 + pyzmq bootstrap | 自管多机 Team 联调 | 不用于本次云化 |
| **yuanrong serverless** | 云函数 URN 调用 | Serverless 备选 | 可选备选 |

**结论**：云化部署应移植 `dev/enterprise_kub` 的运行时底座，**不应**在 K8s 生产环境中混用 `react.a2x_registry` 来调度 AgentServer——二者调度语义不同。

---

## 3. 1 层设计：系统分工

### 3.1 三层架构

```text
管理面（Management Plane）
  Identity（OAuth2 / JWT）
  Claw Manager（实例、模板、策略、Bot/Avatar 元数据、config.push）
  WebUI（登录 / 管理 / 用户面 / 聊天）

控制面（Control Plane）— Gateway 进程
  渠道接入、MessageHandler、SessionMap
  TriggerEngine、MissionManager（Mission / Report）
  RuntimeManagementAgentClient（AgentServer 池调度）
  Leader Election（主备选主）

执行面（Execution Plane）— AgentServer 进程/Pod
  TenantAgentPool（按 service_id + agent_id 多租户隔离）
  AgentAdapter + PersonaAvatarChatRail（avatar_id 上下文注入）
  Skill / CodingEngine / Team（进程内）
```

### 3.2 总体架构图

```mermaid
flowchart TB
    subgraph users [用户与入口]
        U[用户浏览器]
        IM[IM 渠道 可选]
    end

    subgraph mgmt [管理面]
        IDP[Identity 认证 OAuth2/JWT]
        MGR[Claw Manager REST/WS]
        WEBUI[WebUI nginx 统一入口]
    end

    subgraph ctrl [控制面 Gateway 可水平扩展]
        GW1[Gateway 副本 1]
        GW2[Gateway 副本 2]
        REDIS[(Redis 选主 + SessionMap)]
        TE[TriggerEngine]
        RM[MissionManager]
        RMC[RuntimeManagementAgentClient]
    end

    subgraph exec [执行面 AgentServer 池]
        AS1[AgentServer Pod 1]
        AS2[AgentServer Pod N]
        TAP1[TenantAgentPool]
        TAP2[TenantAgentPool]
    end

    subgraph data [持久化]
        PG[(PostgreSQL)]
        NFS[NFS / 对象存储]
    end

    U --> WEBUI
    IM --> GW1
    WEBUI --> IDP
    WEBUI --> MGR
    WEBUI --> GW1
    MGR -->|config.push WS| GW1
    MGR -->|config.push WS| GW2
    GW1 <-->|active-standby| REDIS
    GW2 <-->|STANDBY| REDIS
    GW1 --> TE
    GW1 --> RM
    TE --> RMC
    GW1 --> RMC
    RMC -->|按 service_id| AS1
    RMC --> AS2
    AS1 --> TAP1
    AS2 --> TAP2
    RM --> PG
    MGR --> PG
    TE --> PG
    AS1 --> NFS
    AS2 --> NFS
```

### 3.3 关键边界

| 边界 | 规则 |
|------|------|
| Gateway ↔ AgentServer | 仅通过 E2A（WebSocket 或 RuntimeManagement 封装），携带 `service_id`、`agent_id`、`avatar_id` |
| Gateway 不执行 Agent | 不理解工具链内部，只控制 Mission 生命周期与路由 |
| AgentServer 不调度 Trigger | 不持久化 Mission/Report 主账本（云化后可选只读缓存） |
| `avatar_id` | 业务分身标识，决定 Persona / Skill / CodingEngine 注入 |
| `service_id` | 运行时路由键，决定落到哪个 AgentServer 服务实例 |
| `agent_id` | 租户隔离键（通常 = `user_id`），决定 Pod 内 AgentManager / workspace |
| `session_id` | Agent 对话会话，可 rotate，与 Mission 取消（CHAT_CANCEL）关联 |

---

## 4. 2 层设计：领域模型

### 4.1 概念映射

| JiuwenAvatar 概念 | enterprise_kub 概念 | 云化设计约定 |
|-------------------|----------------------|--------------|
| Persona | `service_config_template` / 策略模板 | Manager 下发或 Gateway 内置 |
| Avatar | `bot`（建议扩展为 `avatar` 资源） | 1 用户可创建 N 个 Avatar |
| Trigger | 无（Avatar 新增） | 绑定 `avatar_id`，存 Gateway |
| Mission | 无（Avatar 新增） | 一次自动任务账本 |
| MissionReport | 无（Avatar 新增） | Mission 结构化输出 |
| 租户 | `group_id` / 组织 | IAM 组织 |
| 用户 | `user_id` | JWT `sub` |
| 执行实例 | `service_id` + `agent_id` | 见 §4.3 |

### 4.2 核心对象关系（云化扩展）

```mermaid
classDiagram
    direction LR

    class Tenant {
        +group_id
        +name
    }

    class User {
        +user_id
        +group_id
    }

    class Persona {
        +id
        +trigger_templates
        +skills
        +coding_engines
    }

    class Avatar {
        +id
        +owner_user_id
        +group_id
        +persona_id
        +service_id
        +trigger_ids
        +status
    }

    class TriggerConfig {
        +id
        +avatar_id
        +type
    }

    class Mission {
        +id
        +avatar_id
        +session_id
        +status
    }

    class RuntimeBinding {
        +service_id
        +agent_id
        +agentserver_ref
    }

    Tenant --> User : 包含
    User --> Avatar : 创建
    Persona --> Avatar : 实例化
    Avatar --> TriggerConfig : 1:N
    Avatar --> RuntimeBinding : 绑定
    TriggerConfig --> Mission : 触发
    Avatar --> Mission : 执行主体
```

### 4.3 路由绑定模型（推荐）

**默认策略**：每个 Avatar 独立路由到 AgentServer 服务实例；Pod 内按用户隔离 workspace。

```text
service_id = md5(group_id + "::" + avatar_id)
agent_id   = user_id
session_id = SessionMap 按 (provider, chat, bot, user) 派生，可 rotate
```

这几个 ID 的职责必须拆开理解：

| 字段 | 业务含义 | 运行态作用 | 默认来源 |
|------|----------|------------|----------|
| `group_id` | 租户 / 组织，例如一个公司、团队或部门 | 数据隔离与计费统计的最外层边界 | Identity / 管理台登录上下文 |
| `user_id` / `owner_user_id` | 某个租户下的用户 | Avatar 归属、权限 owner、用户级记忆归属 | JWT `sub` 或本地管理台用户 |
| `avatar_id` | 用户创建的某个数字分身实例 | 决定 Persona、系统提示词、技能、CodingEngine、Trigger、Mission、Report | Avatar 创建时生成 |
| `service_id` | 运行时服务路由键 | Gateway 用它选择或创建哪个 AgentServer Pod / 服务实例 | 默认 `md5(group_id + "::" + avatar_id)` |
| `agent_id` | Pod 内运行时隔离键 | AgentServer 用它选择 Pod 内哪个 `AgentManager` / 用户隔离槽 | 默认 `user_id` |
| `session_id` | 一次对话上下文 | Agent 历史、取消任务、会话切换 | SessionMap 派生 |

一次请求的运行路径是：

```text
Gateway 收到 avatar_id
  → 查 Avatar 得到 group_id / owner_user_id / service_id / agent_id
  → 按 service_id 把请求发到对应 AgentServer
  → AgentServer 按 agent_id 选择 Pod 内隔离的 AgentManager
  → AgentManager / AgentAdapter 按 avatar_id 注入分身身份、技能和工作区上下文
```

因此：

```text
service_id  决定“跑在哪个 AgentServer / Pod”
agent_id    决定“在这个 Pod 里用哪个用户运行时”
avatar_id   决定“这次以哪个数字分身身份工作”
group_id    决定“属于哪个租户/组织”
```

| 字段 | 稳定性 | 用途 |
|------|--------|------|
| `service_id` | Avatar 创建后不变 | RuntimeManagement 选择 AgentServer Pod |
| `agent_id` | = 所属用户 | TenantAgentPool LRU 键；workspace 隔离 |
| `session_id` | 可 `\new_session` 轮换 | Agent 对话上下文；Mission 取消用 |

**配置项**：`gateway.session_map_scope = per_chat_bot_user`（与 enterprise_kub 一致）。

**备选策略**（资源紧张时）：

```text
service_id = md5(group_id + "::" + user_id)   # 同一用户多个 Avatar 共享一个 Pod
agent_id   = user_id
# 依赖 avatar_id 在 AgentServer 内切换上下文（现有 _apply_avatar_chat_context 已支持）
```

两种策略的取舍：

| 策略 | 路由粒度 | 优点 | 代价 |
|------|----------|------|------|
| `service_id = group_id + avatar_id` | 分身级 | 每个分身有稳定运行时，隔离强，适合数字分身实例长期运行 | 同一用户多个分身可能在不同 Pod，用户级记忆/缓存不能靠进程内共享 |
| `service_id = group_id + user_id` | 用户级 | 同一用户多个分身更容易共享进程内 AgentManager / memory / workspace | 用户热点明显，多分身并发互相影响，分身隔离较弱 |

当前实现采用第一种作为默认，因为它更符合“分身实例独立部署”的诉求。但这也意味着：**`agent_id` 相同不等于跨 Pod 共享内存状态**。如果同一用户的多个 Avatar 被路由到不同 Pod，用户级记忆必须外部化到共享存储，或改用用户级 `service_id` 策略。

### 4.4 Avatar 云化扩展字段

在现有 `AvatarConfig` 基础上增加：

```text
owner_user_id      创建者
group_id           租户 / 组织
service_id         运行时路由键（创建时计算并持久化）
runtime_status     unbound / bound / error
agentserver_ref    可选，当前绑定的 Pod/实例标识（运维用）
created_at
updated_at
```

持久化建议：Phase 1 可仍在 Gateway 侧 PostgreSQL；与 Manager 元数据可同库不同表。

### 4.5 工作区与记忆分层

当前默认路由策略允许同一用户的不同 Avatar 落到不同 AgentServer Pod：

```text
user_id = zhangsan
avatar-a → service_id-a → Pod A
avatar-b → service_id-b → Pod B
```

此时 Pod A 和 Pod B 的进程内 `AgentManager`、session cache、临时内存不会自动共享。`agent_id = user_id` 只是 Pod 内隔离键，不是分布式共享机制。

推荐把工作区和记忆拆成三层：

```text
tenant-workspace-root/
  group-a/
    shared/                  # 租户共享资料、模板、公共策略
    users/
      zhangsan/
        shared/              # 用户共享记忆、长期偏好、常用资料
        avatars/
          avatar-a/          # 分身私有工作区、任务产物、临时状态
          avatar-b/
```

分层规则：

| 层级 | 隔离键 | 用途 | 是否跨 Pod 共享 |
|------|--------|------|----------------|
| 租户共享层 | `group_id` | 组织模板、公共知识、企业策略 | 是，必须使用 DB / 对象存储 / NFS |
| 用户共享层 | `group_id + user_id` | 用户长期记忆、偏好、凭据引用、常用资料 | 是，必须使用 DB / 向量库 / 对象存储 |
| 分身私有层 | `group_id + user_id + avatar_id` | 分身技能状态、任务产物、CodingEngine 工作区 | 按需共享；默认私有 |
| 进程内缓存 | `service_id + agent_id` | 当前 Pod 内 AgentManager、会话运行时 | 否，Pod 重启或跨 Pod 不共享 |

后续生产化时，记忆系统应支持：

```text
读取顺序：租户共享记忆 → 用户共享记忆 → Avatar 私有记忆 → 当前 session 上下文
写入策略：用户偏好写用户层；分身任务经验写 Avatar 层；企业规则写租户层
```

---

## 5. 3 层设计：端到端链路

### 5.1 用户创建 Avatar

```mermaid
sequenceDiagram
    participant Web as Web / Manager API
    participant PM as PersonaManager
    participant SM as SessionMap
    participant TE as TriggerEngine
    participant RMC as RuntimeManagement
    participant DB as PostgreSQL

    Web->>PM: avatars.create(user_id, group_id, persona_id, ...)
    PM->>PM: build AvatarConfig
    PM->>PM: service_id = md5(group_id::avatar_id)
    PM->>TE: provision triggers from trigger_templates
    PM->>DB: save Avatar + trigger_ids
    PM->>SM: register binding (service_id, agent_id=user_id)
  opt 预热
    PM->>RMC: ensure_service(service_id)
    RMC-->>PM: agentserver ready
  end
    PM-->>Web: avatar
```

### 5.2 Web 对话（带 avatar_id）

```mermaid
sequenceDiagram
    participant Web as Web Frontend
    participant GW as Gateway
    participant SM as SessionMap
    participant RMC as RuntimeManagement
    participant AS as AgentServer
    participant TAP as TenantAgentPool

    Web->>GW: chat(user_id, group_id, avatar_id, message)
    GW->>SM: get_session(provider, chat, bot=avatar_id, user_id)
    SM-->>GW: session_id, service_id, agent_id
    GW->>RMC: E2A chat.send(service_id, agent_id, params.avatar_id, prompt)
    RMC->>AS: route to Pod
    AS->>TAP: get AgentManager(service_id, agent_id)
    AS->>AS: _apply_avatar_chat_context(avatar_id)
    AS-->>GW: response
    GW-->>Web: stream / result
```

### 5.3 Trigger 自动任务（与详细设计衔接）

在 [JiuwenAvatar 详细设计](JiuwenAvatar-详细设计.md) §5.2 基础上，`_dispatch_fire` 增加路由步骤：

```text
ITrigger.fire
  → TriggerEngine._dispatch_fire
  → 读取 TriggerConfig.avatar_id
  → 查 Avatar.service_id / owner user_id → agent_id
  → MissionManager.create_mission()
  → RuntimeManagementAgentClient.send_request(E2A chat.send)
       params: { avatar_id, content, mode: agent }
       routing: { service_id, agent_id }
  → Mission COMPLETED/FAILED → create_report → publish
```

Mission 取消链路不变：Gateway 用 `mission.session_id` 发 `CHAT_CANCEL` 到**同一 service_id** 对应的 AgentServer。

### 5.4 Gateway 主备切换

```text
PRIMARY Gateway
  → 运行 TriggerEngine 调度
  → 消费 Cron / Heartbeat
  → 写 SessionMap / Mission 到 Redis + PostgreSQL

STANDBY Gateway
  → 不调度 Trigger
  → 可接收只读 API
  → 晋升 PRIMARY 后 reload SessionMap + 恢复调度
```

实现参考：enterprise_kub `jiuwenclaw/gateway/leader_election.py`。

---

## 6. 4 层设计：模块职责

### 6.1 控制面（Gateway）

| 模块 | 现状 | 云化改造 |
|------|------|----------|
| **SessionMap** | 本地 JSON，仅 `session_id` | 增加 `service_id`/`agent_id`；Redis 后端 |
| **MessageHandler** | 渠道消息路由 | 填充 `ChannelControlState.service_id/agent_id` |
| **TriggerEngine** | 单进程调度 | PRIMARY 调度 + DB 存储 + 分布式锁 |
| **MissionManager** | JSON 文件 | PostgreSQL 按 `group_id` 隔离 |
| **AgentClient** | 固定 WebSocket | **RuntimeManagementAgentClient**（K8s 池） |
| **LeaderElection** | 无 | 从 enterprise_kub 移植 |

### 6.2 执行面（AgentServer）

| 模块 | 现状 | 云化改造 |
|------|------|----------|
| **TenantAgentPool** | 单 `AgentManager` | 多租户 LRU（`service_id`+`agent_id`） |
| **workspace 路径** | 单用户目录 | `get_multi_tenant_user_workspace_dir` |
| **PersonaAvatarChatRail** | per-request 注入 | 不变，仍靠 `avatar_id` |
| **CodingEngine** | 按 avatar 隔离 CLI 工作区 | 路径纳入多租户根目录 |
| **AgentAdapter** | `_apply_avatar_chat_context` | 不变 |

### 6.3 管理面（可选，Phase 3）

| 模块 | 职责 |
|------|------|
| **Identity** | OAuth2 密码流 / JWT；组织与用户 |
| **Claw Manager** | 实例注册、K8s kubeconfig、模板、策略、Bot 可见性 |
| **manager_ws_client** | Gateway 接收 `config.push`，落库 GDB |
| **WebUI** | `/auth` `/manager` `/user` `/chat` 统一入口 |

### 6.4 需从 enterprise_kub 移植的组件

| 源路径（enterprise_kub） | 目标路径（jiuwen-avatar） | 阶段 |
|--------------------------|---------------------------|------|
| `packages/jiuwenclaw-ee/gateway/extensions/runtime_management_extension/` | `jiuwenavatar/extensions/runtime_management/` | P0 |
| `jiuwenclaw/gateway/session_map.py` + `session_storage.py` | `jiuwenavatar/gateway/routing/` | P0 |
| `jiuwenclaw/gateway/leader_election.py` | `jiuwenavatar/gateway/` | P2 |
| `jiuwenclaw/agentserver/tenant_agent_pool.py` | 替换 `server/runtime/tenant_agent_pool.py` | P0 |
| `deploy/` K8s 模板 | `deploy/enterprise/` | P0 |
| `packages/jiuwenclaw-ee/.../manager_ws_client/` | `jiuwenavatar/extensions/manager_ws_client/` | P3 |
| `identity_service/` + `claw_manager/` | 独立服务或子目录 | P3 |

**保留不覆盖的 Avatar 独有模块**：

```text
gateway/trigger/*
gateway/report/*
server/runtime/persona/*
server/runtime/coding/*
server/runtime/persona/persona_avatar_chat_rail.py
```

### 6.5 当前仓库已落地的企业模式骨架

截至当前实现，`jiuwen-avatar` 已经具备一套**可本地烟测、可继续替换为真实 RuntimeManagement 的企业模式骨架**。它不是完整 SaaS 成品，但已经把路由、隔离、存储后端开关和管理台入口接进主链路。

| 能力 | 当前实现 | 位置 | 说明 |
|------|----------|------|------|
| 企业模式开关 | `DEPLOYMENT_MODE=enterprise`、`AGENT_SERVER_DEPLOY_MODE=k8s`、`JIUWENAVATAR_ENTERPRISE_MODE=true` | `jiuwenavatar/common/enterprise.py` | 未启用时单机版路径与行为保持原样 |
| 路由上下文 | `TenantRuntimeContext(service_id, agent_id, avatar_id, group_id, user_id)` | `jiuwenavatar/common/enterprise.py` | 请求级 ContextVar，用于 AgentServer 内部工作区隔离 |
| RuntimeManagement 风格 AgentClient | `RuntimeManagementAgentClient` | `jiuwenavatar/gateway/routing/agent_client.py` | 当前支持静态 `service_id -> ws endpoint` 映射；后续可替换成 K8s 动态创建/发现 |
| Gateway 客户端选择 | 企业模式使用 `RuntimeManagementAgentClient`，单机模式继续 `WebSocketAgentServerClient` | `jiuwenavatar/gateway/app_gateway.py` | 保持 `AgentServerClient` 抽象不变 |
| SessionMap 扩展 | `SessionBinding(session_id, service_id, agent_id, avatar_id, group_id, user_id)` | `jiuwenavatar/gateway/routing/session_map.py` | 兼容旧 `key -> session_id` 字符串格式 |
| Redis SessionMap | 企业模式或 `GATEWAY_SESSION_MAP_BACKEND=redis` 时优先 Redis | `jiuwenavatar/gateway/routing/session_map.py` | Redis 不可用时回退本地 `session_map.json` |
| 多租户 AgentManager 池 | 按 `service_id + agent_id` 缓存多个 `AgentManager` | `jiuwenavatar/server/runtime/tenant_agent_pool.py` | `agent_id` 相同但不同 Pod 仍不共享内存 |
| AgentServer 接线 | unary / stream / cancel 均按请求路由选择 tenant manager | `jiuwenavatar/server/agent_ws_server.py` | `CHAT_CANCEL` 会落回同一租户 manager |
| Avatar 云化字段 | `owner_user_id`、`group_id`、`service_id`、`agent_id`、`runtime_status`、`agentserver_ref` | `jiuwenavatar/server/runtime/persona/models.py` | 创建 Avatar 时自动生成默认 `service_id` |
| 聊天路由注入 | 手动 Web chat 根据 `avatar_id` 注入 routing | `jiuwenavatar/gateway/message_handler/message_handler.py` | E2A params 中带 `routing` |
| Trigger / Mission 路由 | Trigger 派发、Mission 创建、Mission cancel 都记录/携带路由 | `jiuwenavatar/gateway/trigger/engine.py`、`gateway/report/*` | 支持跨 Gateway 找回同一 `service_id` |
| 可选 PostgreSQL JSON Store | Avatar / Trigger / Mission / Report 支持 `JIUWENAVATAR_STORE_BACKEND=postgres` | `jiuwenavatar/common/postgres_json_store.py` | 目前是 JSONB 通用表，后续应演进为正式 schema |
| 多租户工作区路径 | 企业模式下 `get_agent_root_dir()` 可解析到租户工作区 | `jiuwenavatar/common/utils.py` | 覆盖 CodingEngine / Skill 等沿用 path helper 的模块 |
| 管理台入口 | 前端新增“企业管理台”，后端新增 `auth.login`、`manager.status` | `channels/web/frontend/src/components/EnterprisePanel/`、`app_web_handlers.py` | 目前是本地 Identity shim，不是正式 OAuth2 |
| 部署模板 | 最小 K8s YAML 和说明 | `deploy/enterprise/` | 用于 P0/P1 烟测，不等同 Helm/生产部署 |

当前路由链路实际为：

```text
Web / Trigger
  → Gateway MessageHandler / TriggerEngine
  → 按 avatar_id 查 Avatar 路由字段
  → E2A params.routing = { service_id, agent_id, avatar_id, group_id, user_id }
  → RuntimeManagementAgentClient 按 service_id 选择 AgentServer endpoint
  → AgentWebSocketServer 按 service_id + agent_id 选择 TenantAgentPool 中的 AgentManager
  → AgentAdapter 按 avatar_id 注入 Persona / Skill / CodingEngine
```

当前仍是骨架或待生产化的部分：

| 项 | 当前状态 | 后续要求 |
|----|----------|----------|
| RuntimeManagement | 静态 endpoint map | 接真实 K8s Pod 创建、健康检查、回收、池化 |
| Identity | 本地 `auth.login` JSON token shim | 接 OAuth2 / JWT / 组织用户体系 |
| Manager | 只有管理台状态页和 Avatar CRUD 复用 | 接模板、策略、实例状态、config.push |
| PostgreSQL | 通用 JSONB Store | 拆正式表、索引、迁移脚本、行级租户过滤 |
| Redis 锁 | SessionMap 已支持 Redis | 还需 LeaderElection、Trigger lock、pub/sub reload |
| 文件传输 | 未完整实现 Gateway/AgentServer 分离传输 | 补 `file_transfer_handler` / `file_transfer_manager` |
| 用户共享记忆 | 工作区可隔离，但用户共享记忆未独立外部化 | 按 §4.5 拆用户共享层与 Avatar 私有层 |

---

## 7. 数据与存储设计

### 7.1 存储分层

| 数据类型 | 单机现状 | 云化目标 | 隔离键 |
|----------|----------|----------|--------|
| Avatar / Persona | 本地 YAML/内存 | PostgreSQL | `group_id`, `owner_user_id` |
| Trigger | `triggers.json` | PostgreSQL | `group_id`, `avatar_id` |
| Mission / Report | `missions.json` / `reports.json` | PostgreSQL | `group_id`, `avatar_id` |
| SessionMap | `session_map.json` | Redis | key 含 provider/chat/bot/user |
| Gateway 选主 | 无 | Redis SETNX | `gateway:leader` |
| Trigger 调度锁 | 无 | Redis 锁 | `trigger:schedule:{id}` |
| Agent workspace | 本地目录 | NFS 或对象存储 | `service_id` / `agent_id` |
| 编码 CLI 工作区 | `~/.jiuwenavatar/...` | 多租户子目录 | `avatar_id` |

### 7.2 PostgreSQL 逻辑表（草案）

```text
tenants           (group_id, name, ...)
users             (user_id, group_id, ...)          -- 或由 Identity 管理
personas          (id, group_id, config_json, ...)   -- 内置 + 租户自定义
avatars           (id, group_id, owner_user_id, persona_id, service_id, ...)
triggers          (id, group_id, avatar_id, type, config_json, ...)
missions          (id, group_id, avatar_id, trigger_id, status, session_id, ...)
mission_reports   (id, mission_id, ...)
read_state        (user_id, resource_type, resource_id, ...)
usage_stats       (group_id, user_id, metrics_json, ...)
```

### 7.3 Redis Key 约定（草案）

```text
sessionmap:{scope}:{provider}:{chat}:{bot}:{user}  → Session JSON
gateway:leader                                     → instance_id
trigger:lock:{trigger_id}                          → 调度互斥
mission:active:{mission_id}                        → 可选缓存
```

---

## 8. 部署设计

### 8.1 推荐 K8s 拓扑（最小生产集）

```text
Namespace: jiuwen-avatar-prod

基础设施（可共享或外置）:
  redis
  postgresql
  minio          （可选，报告附件 / 文件）
  nfs-server     （AgentServer workspace，按需）

管理面（Phase 3）:
  identity
  manager-server
  webui          （唯一对外 NodePort / Ingress）
  web-broker     （企业 Web WS）

运行时:
  gateway        （replicas=2, active-standby）
  agentserver    （由 RuntimeManagement 动态创建 Pod，非固定 Deployment）
```

### 8.2 关键环境变量

| 变量 | 说明 | 示例 |
|------|------|------|
| `DEPLOYMENT_MODE` | `standalone` / `enterprise` / `active-standby` | `enterprise` |
| `JIUWENAVATAR_ENTERPRISE_MODE` | 显式打开企业模式 | `true` |
| `GATEWAY_INSTANCE_ID` | 网关实例标识 | `gateway-jia-prod` |
| `AGENT_RUNTIME` | `jiuwen`（K8s）/ `yuanrong`（serverless） | `jiuwen` |
| `AGENT_SERVER_DEPLOY_MODE` | `k8s` / `process` | `k8s` |
| `AGENT_SERVICE_ENDPOINTS` | 静态路由映射，P0/P1 本地或测试环境使用 | `{"svc1":"ws://agentserver-a:28092"}` |
| `AGENT_SERVER_URL` | 未命中 `service_id` 时的默认 AgentServer | `ws://agentserver:28092` |
| `GATEWAY_SESSION_MAP_SCOPE` | 会话隔离粒度 | `per_chat_bot_user` |
| `GATEWAY_SESSION_MAP_BACKEND` | `auto` / `json` / `redis` | `redis` |
| `AGENT_SERVER_MIN_IDLE_SERVICES` | 池最小空闲实例 | `2` |
| `AGENT_SERVER_MAX_SERVICES` | 池上限 | `20` |
| `REDIS_HOST` / `REDIS_PORT` | SessionMap + 选主 | — |
| `JIUWENAVATAR_STORE_BACKEND` | `auto` / `json` / `postgres` | `postgres` |
| `DATABASE_URL` / `POSTGRES_DSN` | PostgreSQL JSON Store 连接串 | `postgresql://...` |
| `JIUWENAVATAR_TENANT_WORKSPACE_ROOT` | 企业模式租户工作区根目录 | NFS 挂载路径 |
| `JIUWEN_TEAM_WORKSPACE_ROOT` | 共享工作区（Team/编码按需） | NFS 挂载路径 |

### 8.3 Gateway RBAC

Gateway ServiceAccount 需具备创建/删除 AgentServer Pod 的权限（enterprise_kub `gateway.template.yaml` 已定义）。Avatar 镜像替换 `GATEWAY_IMAGE` / `AGENT_SERVER_IMAGE` 即可复用部署脚本。

### 8.4 与单机多实例的关系

[单机多实例运行](单机多实例运行.md) 通过 `JIUWENAVATAR_DATA_DIR` 隔离**同机多进程**，适用于开发机。云化部署通过 K8s Namespace + 多租户 DB/Redis 隔离，二者可并存（开发用多实例，生产用 K8s）。

### 8.5 企业版本构建

企业版本和单机版本使用同一套代码与前端产物，通过运行时环境变量切换模式。构建顺序为：

```powershell
cd E:\code\jiuwen-avatar\jiuwenavatar\channels\web\frontend
npm install
npm run build
```

前端 `dist` 会被 Python 包作为静态资源打入 `jiuwenavatar`。然后回到仓库根目录安装 Python 依赖：

```powershell
cd E:\code\jiuwen-avatar
uv sync
```

### 8.6 本地企业模式烟测

当前代码支持先用静态 endpoint 映射跑通分布式路由，不要求一开始就接完整 K8s RuntimeManagement。PowerShell 环境变量示例：

```powershell
$env:JIUWENAVATAR_ENTERPRISE_MODE="true"
$env:DEPLOYMENT_MODE="enterprise"
$env:AGENT_SERVER_DEPLOY_MODE="k8s"

$env:GATEWAY_SESSION_MAP_BACKEND="json"
$env:JIUWENAVATAR_STORE_BACKEND="json"
$env:JIUWENAVATAR_TENANT_WORKSPACE_ROOT="$env:USERPROFILE\.jiuwenavatar-enterprise\tenants"

$env:AGENT_SERVER_URL="ws://127.0.0.1:28092"
$env:AGENT_SERVICE_ENDPOINTS="{}"
```

分别打开三个终端启动：

```powershell
uv run jiuwenavatar-agentserver
```

```powershell
uv run jiuwenavatar-gateway
```

```powershell
uv run jiuwenavatar-web
```

前端使用方式：

1. 浏览器打开 Web UI。
2. 企业模式下首页会先进入“企业管理台”登录页；单机模式不会显示企业管理台入口。
3. 输入本地烟测用 `user_id`、`group_id`，例如 `local-user` / `default`。
4. 登录后进入“分身”页，创建 Avatar 时会自动携带当前租户上下文。
5. Web 对话、Trigger 派发、Mission cancel 会沿用 Avatar 上的 `service_id` 和 `agent_id`。

如果要验证 Redis / PostgreSQL 后端，把 JSON 后端替换为：

```powershell
$env:GATEWAY_SESSION_MAP_BACKEND="redis"
$env:REDIS_HOST="127.0.0.1"
$env:REDIS_PORT="6379"

$env:JIUWENAVATAR_STORE_BACKEND="postgres"
$env:DATABASE_URL="postgresql://jiuwen:jiuwen@127.0.0.1:5432/jiuwenavatar"
```

当前 PostgreSQL 适配层是 JSONB 通用存储骨架，用于联调 Avatar / Trigger / Mission / Report 的集中存储；正式生产还需要拆分 schema、索引、迁移和租户行级权限。

### 8.7 K8s 最小部署

K8s 烟测模板位于：

```text
deploy/enterprise/jiuwenavatar-enterprise.yaml
```

先构建镜像，并确保集群能拉取该镜像：

```powershell
cd E:\code\jiuwen-avatar
docker build -t jiuwenavatar:latest .
```

部署：

```powershell
kubectl apply -f deploy/enterprise/jiuwenavatar-enterprise.yaml
kubectl get pods -n jiuwen-avatar
kubectl get svc -n jiuwen-avatar
```

当前 YAML 是最小烟测版：`gateway` 使用 2 副本，`agentserver` 使用 1 个静态 Deployment，Redis/PostgreSQL 以配置位接入，工作区挂载到 `JIUWENAVATAR_TENANT_WORKSPACE_ROOT`。生产形态需要继续补：

| 项 | 当前模板 | 生产要求 |
|----|----------|----------|
| AgentServer | 静态 Deployment | RuntimeManagement 动态创建 / 回收 Pod |
| Gateway HA | 2 副本配置 | LeaderElection + PRIMARY 调度 |
| 数据库 | JSONB Store 连接位 | 正式表结构、迁移脚本、备份 |
| 工作区 | PVC `ReadWriteMany` | NFS / 对象存储 / 容量与权限规划 |
| 对外入口 | Service | Ingress / TLS / 认证网关 |
| Secret | ConfigMap 明文示例 | Secret / External Secrets |

这套方式适合 P0/P1 联调。生产部署还需要补齐 §11 中未完成项，尤其是真实 Identity、Manager、LeaderElection、K8s RuntimeManagement 和正式数据库 schema。

---

## 9. API 设计（草案）

### 9.1 Avatar 资源 API（Gateway 或 Manager）

```text
POST   /v1/avatars
       Body: { group_id, persona_id, name, coding_engine?, extra_skills? }
       → 创建 Avatar + provision Triggers + 计算 service_id

GET    /v1/me/avatars?group_id=
       → 当前用户分身列表

GET    /v1/avatars/{avatar_id}
PATCH  /v1/avatars/{avatar_id}
DELETE /v1/avatars/{avatar_id}
       → 删除 Triggers + purge Mission/Report

GET    /v1/avatars/{avatar_id}/triggers
POST   /v1/avatars/{avatar_id}/triggers
```

### 9.2 既有 API（保持不变，增加租户上下文）

```text
triggers.list / get / create / update / delete
missions.list / get / cancel / delete / stats
reports.list / get
report_read_state.get / set
report.unread_counts
```

请求需携带：`Authorization: Bearer <JWT>`，从中解析 `user_id`、`group_id`；Gateway 中间件注入租户上下文，存储层强制过滤。

### 9.3 E2A 扩展字段

`AgentRequest` / `params` 建议稳定包含：

```json
{
  "avatar_id": "avatar-xxxx",
  "content": "...",
  "mode": "agent",
  "routing": {
    "service_id": "md5...",
    "agent_id": "user-123"
  }
}
```

---

## 10. 安全设计

| 项 | 要求 |
|----|------|
| 对外入口 | 仅 WebUI Ingress；Gateway/AgentServer 集群内访问 |
| 认证 | OAuth2 + RS256 JWT（Identity） |
| 授权 | Manager 策略 + Bot/Avatar 可见性；API 按 `group_id` 强制隔离 |
| Gateway ↔ AgentServer | 内网 + link_auth / token（RuntimeManagement 内置） |
| Webhook Trigger | 生产强制 `webhook_secret` + HMAC 校验 |
| K8s | Gateway SA 最小 RBAC；AgentServer Pod securityContext |
| 配置下发 | enterprise config.push 字段级加解密（可选） |
| 多租户数据 | SQL 行级 `group_id` 过滤；禁止跨租户 `avatar_id` 访问 |

---

## 11. 分阶段实施计划

### Phase 0：运行时底座（当前已完成骨架）

**目标**：K8s 上 Gateway 能按 `service_id` 调度 AgentServer，E2A 对话打通。

| 任务 | 状态 | 当前产出 |
|------|------|----------|
| 企业模式开关 | 已完成 | `common/enterprise.py` |
| Gateway AgentClient 抽象切换 | 已完成 | 企业模式使用 `RuntimeManagementAgentClient` |
| RuntimeManagement Client | 已完成骨架 | 静态 endpoint map；待接真实 K8s runtime |
| SessionMap + Redis 后端 | 已完成骨架 | `SessionBinding` + Redis fallback |
| TenantAgentPool | 已完成 | 按 `service_id + agent_id` 管理多个 `AgentManager` |
| `deploy/enterprise/` 部署模板 | 已完成最小版 | Gateway / AgentServer / Redis / PostgreSQL 参考 YAML |
| 依赖 `openjiuwen_runtime.management` | 未完成 | 当前未强绑定外部 runtime SDK，先用静态映射替代 |

**当前验收**：单 Avatar 可携带 `service_id` 完成 Web 对话；静态映射可把不同 `service_id` 打到不同 AgentServer endpoint。

### Phase 1：Avatar 路由绑定（当前已完成主链路）

**目标**：用户创建 Avatar 自动绑定 `service_id`，Trigger/Mission 走路由。

| 任务 | 状态 | 当前产出 |
|------|------|----------|
| Avatar 扩展字段 | 已完成 | `owner_user_id`、`group_id`、`service_id`、`agent_id`、`runtime_status` |
| Avatar 持久化后端 | 已完成骨架 | JSON 默认，PostgreSQL JSON Store 可选 |
| 创建链路写路由字段 | 已完成 | `PersonaManager.create_avatar()` 默认生成 `service_id` |
| Web 对话路由 | 已完成 | `MessageHandler` 注入 routing |
| TriggerEngine `_dispatch_fire` 路由 | 已完成 | 派发前读取 Avatar 路由 |
| Mission / Report 路由字段 | 已完成 | Mission 保存 `service_id`、`agent_id`、`group_id`、`owner_user_id` |
| Mission cancel 定向取消 | 已完成 | 按 Mission 保存的 `service_id` 找回 AgentServer |
| workspace 多租户路径 | 已完成骨架 | 复用 `get_agent_root_dir()` 的模块已纳入租户路径 |
| 基础 API | 部分完成 | 复用 WebSocket RPC；REST `/v1` 仍是设计草案 |

**当前验收**：两用户各建 Avatar 后，Web 对话、Trigger、Mission cancel 都能携带各自路由；生产级跨 Pod 验收依赖真实 RuntimeManagement 和 K8s 环境。

### Phase 2：控制面云化（下一阶段）

**目标**：Gateway 水平扩展，控制数据上云。

| 任务 | 状态 | 产出 |
|------|------|------|
| Leader Election + 双副本 | 未完成 | `gateway:leader` Redis SETNX |
| Trigger/Mission/Report → PostgreSQL 正式表 | 部分完成 | 当前为 JSONB 通用 store，仍需 schema / migration |
| Trigger 调度分布式锁 | 未完成 | `trigger:lock:{trigger_id}` |
| Mission 跨副本一致性 | 部分完成 | 路由字段已保存；状态并发一致性仍需锁/事务 |
| Gateway 配置热更新 | 未完成 | Manager `config.push` / reload |

**验收**：Gateway 主备切换无重复触发；Mission 状态一致；DB/Redis 故障恢复后可重建调度状态。

### Phase 3：管理面与用户面（下一阶段）

**目标**：SaaS 自助创建分身。

| 任务 | 状态 | 产出 |
|------|------|------|
| 本地管理台入口 | 已完成骨架 | `EnterprisePanel` + `manager.status` |
| 本地登录上下文 | 已完成骨架 | `auth.login` 写入 `user_id`、`group_id` |
| Identity + Manager 引入 | 未完成 | OAuth2/JWT、组织用户、实例管理 |
| WebUI 用户面「我的分身」 | 部分完成 | Avatar 创建/列表可带租户上下文；专门实例状态页待完善 |
| config.push Avatar 段 | 未完成 | Persona 模板和策略下发 |
| 企业权限 GDB | 未完成 | 角色、资源可见性、审计 |

**验收**：端到端：正式登录 → 创建分身 → 分配/扩容 AgentServer → 对话 + 自动任务 + 报告 + 审计。

### Phase 4：生产加固（持续）

| 任务 | 状态 |
|------|------|
| AgentServer HPA / 池化参数调优 | 未完成 |
| jiuwenbox sidecar 沙箱 | 未完成 |
| 文件传输独立通道 | 未完成 |
| 观测性（Mission 审计、AgentServer 健康） | 未完成 |
| Webhook / 传输层安全加固 | 未完成 |
| 灾备与备份策略 | 未完成 |

---

## 12. 能力矩阵

| 能力 | 当前状态 | 已落地内容 | 后续计划 |
|------|----------|------------|----------|
| 单机版兼容 | 已完成 | 默认不开企业模式，继续走原 `WebSocketAgentServerClient`、本地 JSON/YAML、单工作区 | 持续回归测试 |
| Avatar 闭环 | 已完成 | Avatar / Persona / Trigger / Mission / Report 业务链路保留 | 与 Manager bot 资源统一建模 |
| 路由 ID 链路 | 已完成 | `group_id`、`user_id`、`avatar_id`、`agent_id`、`service_id` 进入 Avatar、Session、Mission、E2A | 接正式 Identity 后由 token 注入 |
| Web 对话路由 | 已完成 | `MessageHandler` 按 Avatar 注入 `routing` | 补 REST API / OpenAPI |
| Trigger 路由 | 已完成 | `_dispatch_fire` 查 Avatar 路由后派发 | 加分布式锁，避免多 Gateway 重复调度 |
| Mission cancel 路由 | 已完成 | Mission 保存 `service_id`，取消时发回同一 AgentServer | 增加跨副本状态一致性 |
| SessionMap | 已完成骨架 | `SessionBinding` + Redis 可选 + JSON fallback | Redis pub/sub、TTL、迁移工具 |
| TenantAgentPool | 已完成 | AgentServer 内按 `service_id + agent_id` 管理多 `AgentManager` | 增加 metrics、空闲清理策略 |
| RuntimeManagement | 部分完成 | `RuntimeManagementAgentClient` 静态 endpoint map | 接 K8s Pod 生命周期、健康检查、池化 |
| PostgreSQL 存储 | 部分完成 | 通用 JSONB Store 覆盖 Avatar / Trigger / Mission / Report | 正式表结构、索引、迁移、租户 RLS |
| 工作区隔离 | 已完成骨架 | 企业模式下 agent root 按租户上下文解析 | 用户共享记忆外部化，文件产物对象存储化 |
| 管理台 | 部分完成 | 前端“管理台”、本地 login、运行状态页 | 正式 Identity、Manager、实例状态、模板策略 |
| Gateway HA | 未完成 | 文档设计已给出 | LeaderElection、PRIMARY 调度、DB/Redis 恢复 |
| K8s 部署 | 部分完成 | `deploy/enterprise/` 最小 YAML | Helm、HPA、RBAC 加固、Ingress、Secret 管理 |
| 文件传输 | 未完成 | 仍沿用单机/现有路径 | Gateway/AgentServer 分离后的上传下载通道 |
| A2X 分布式 Team | 已有但不作为本方案核心 | 保留原能力 | 云化 Avatar 默认不依赖 Team |
| yuanrong serverless | 可作为备选 | 原扩展能力保留 | 后续可作为 RuntimeManagement 后端之一 |

---

## 13. 风险与演进

### 13.1 当前风险

| 风险 | 影响 | 缓解 |
|------|------|------|
| enterprise_kub 与 avatar 代码分叉 | 移植成本高 | 只移植扩展包，避免整仓 merge |
| Trigger JSON 多 Gateway 写冲突 | 重复执行 | Phase 2 必须上 DB + 锁 |
| CodingEngine 依赖本机 CLI | 容器内不可用 | sidecar 或限制引擎类型 |
| 单活 Team session（若启用 Team） | 与多 Avatar 并发冲突 | 云化场景默认 inprocess Team 或禁用 |
| 配置双轨（本地 yaml + config.push） | 行为不一致 | Phase 3 后以前者只读、后者为准 |

### 13.2 后续演进

- Avatar 与 Manager `bot` 资源统一建模
- Mission 重试 / 超时 / 失败归因
- Report 引入 Persona `report_template`
- 按租户计费与 UsageStats 上报
- 多区域部署与 AgentServer 就近调度
- Helm Chart 官方化（当前 enterprise 以 shell 模板为主）

---

## 14. 设计收益

1. **业务闭环完整**：在 Avatar Trigger → Mission → Report 之上，补齐多租户云化执行与路由。
2. **控制面 / 执行面解耦保持不变**：Gateway 调度与记账，AgentServer 按 `avatar_id` 执行。
3. **可复用 enterprise 生产实践**：K8s、主备、Agent 池、Session 亲和经过企业版验证。
4. **路径清晰**：不与 A2X Team 混用，降低架构歧义。
5. **可分阶段交付**：P0 即可在集群内跑通路由，P3 才依赖完整 IAM/Manager。

---

## 15. 附录

### 15.1 术语表

| 术语 | 含义 |
|------|------|
| Persona | 角色模板 |
| Avatar | Persona 的用户实例 |
| service_id | AgentServer 池路由键 |
| agent_id | Pod 内租户隔离键 |
| session_id | Agent 对话会话 ID |
| RuntimeManagement | openjiuwen 运行时编排 SDK，负责 K8s Pod 生命周期 |
| config.push | Manager → Gateway WebSocket 配置下发 |

### 15.2 参考仓库与分支

- JiuwenSwarm：`https://gitcode.com/openJiuwen/jiuwenswarm.git`
- 企业云化参考分支：`dev/enterprise_kub`
- 部署手册：`deploy/README.md`（enterprise_kub 仓库内）
- Web 架构：`docs/zh/Web服务重构设计.md`（enterprise_kub 仓库内）

### 15.3 文档版本

| 版本 | 日期 | 说明 |
|------|------|------|
| 0.1 | 2026-07-02 | 初稿：整体架构、映射、分阶段计划 |
| 0.2 | 2026-07-09 | 补充分布式多租户当前实现状态、路由 ID 关系、工作区/记忆分层、启用方式和后续计划 |
