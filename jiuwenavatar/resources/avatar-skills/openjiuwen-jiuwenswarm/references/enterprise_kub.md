<!-- git-ref: dev/enterprise_kub -->
# Reference — JiuwenSwarm 企业版（enterprise_kub）

本文件用于 **企业版 / 企业 Claw / 企业 Swarm / K8s 云化部署** 相关问答。所有路径均相对于 **`assets/enterprise_kub/`**。

| 属性 | 值 |
|------|-----|
| **索引名（assets 目录）** | `enterprise_kub` |
| **Git 分支** | `dev/enterprise_kub` |
| **仓库** | `openJiuwen/jiuwenswarm` |
| **主 Python 包目录** | **`jiuwenclaw/`**（本分支仍为 `jiuwenclaw`，勿与开源 `0.2.x` 的 `jiuwenswarm/` 包目录混淆） |
| **PyPI/工程名** | `jiuwenswarm`（`pyproject.toml` 中 `name`，版本约 **0.1.10**） |
| **CLI 前缀** | `jiuwenclaw-*`（如 `jiuwenclaw-init` / `jiuwenclaw-start` / `jiuwenclaw-gateway`） |
| **工作区目录** | `.jiuwenclaw/` |

**何时选用本索引（优先于 `0.2.x`）**：用户提到「企业版」「企业 claw」「企业 swarm」「enterprise」「enterprise_kub」「K8s / Kubernetes 部署」「Manager 管控台」「RuntimeManagement」「多租户云化」「deploy 一键部署工具」等。

**勿与**：开源最新版 `references/0.2.3.md`（分支 `dev_release_0.2.3`，包目录 `jiuwenswarm/`）混读。

---

## 一、按意图快速定位（任务 → 最小集合）

| 用户意图 / 关键词 | 先读文档 | 再读代码 / 部署 | 备注 |
|------------------|----------|-----------------|------|
| 企业版总览、与开源差异 | `README_CN.md`、`docs/zh/SUMMARY.md` | `pyproject.toml`、`packages/jiuwenclaw-ee/` | 包名为 jiuwenclaw |
| **K8s 一键部署 / 部署工具** | `deploy/README.md`、`deploy/.env.example` | `deploy/deploy.sh`、`*_handler.sh`、`deploy/templates/` | 企业交付主路径 |
| Gateway / Web / Manager 部署 | `deploy/README.md` | `deploy/gateway_handler.sh`、`web_handler.sh`、`manager_handler.sh` | 模板在 `deploy/templates/` |
| Redis / PG / MySQL / MinIO / NFS / RabbitMQ | `deploy/README.md` | 对应 `deploy/*_handler.sh`、`templates/*.template.yaml` | 可内置或外接 |
| 企业 Web / 登录 / 多租户 UI | `docs/zh/配置信息.md`、前端字段相关 | `jiuwenclaw/web_enterprise/`、`app_enterprise_web.py` | 区别于开源 `web/` |
| RuntimeManagement / AgentServer 池 | `deploy/README.md`、企业配置文档 | `jiuwenclaw/agentserver/`、`packages/jiuwenclaw-ee/`、`jiuwenclaw_ee` | 动态调度 |
| Manager 管控台 | `deploy/README.md` | `packages/jiuwenclaw-ee/claw_manager`、`jiuwenclaw-manager` 入口 | 企业扩展包 |
| 企业配置下发 / 加签验签 | `docs/zh/配置下发加签验签设计.md`、`配置下发字段级加解密设计.md` | `jiuwenclaw/agentserver/enterprise_config/`、`security/` | 企业特有 |
| 链路握手鉴权 | `docs/zh/链路握手鉴权.md` | `jiuwenclaw/security/`、gateway 鉴权相关 | |
| 权限 / 工具护栏 | `docs/zh/工具权限与安全防护.md`、`permissions_config_architecture.md` | `jiuwenclaw/agentserver/permissions/` | |
| 可观测性 | `docs/zh/OpenTelemetry可观测性.md` | `jiuwenclaw/telemetry/`、`deploy/log_handler.sh` | |
| 前端字段 / 扩展透传 | `docs/zh/前端字段配置.md`、`扩展字段透传.md` | `web_enterprise/`、handlers | |
| E2A / 频道 / 基础能力 | `docs/zh/E2A-protocol.md`、`频道.md` | `jiuwenclaw/e2a/`、`channel/`、`gateway/` | 与开源同源能力 |
| 安装初始化（企业单机/联调） | `docs/zh/Quickstart.md`、`配置信息.md` | `jiuwenclaw/init_workspace.py`、`start_services.py` | CLI 为 `jiuwenclaw-*` |
| Skill / 记忆 / 心跳 / 定时 | `docs/zh/` 对应专题 | `agentserver/`、`gateway/cron` 等 | 结构与开源不同代，勿套用 0.2.3 路径 |

---

## 二、文档索引

> 中文路径相对 `docs/zh/`（本分支 SUMMARY 以企业能力为主，条目少于开源 0.2.x）。

### 2.1 推荐入口

| 说明 | 路径 |
|------|------|
| 总目录 | `docs/zh/SUMMARY.md` |
| 仓库说明 | `README_CN.md` |
| **企业部署手册** | `deploy/README.md` |
| 部署环境变量样例 | `deploy/.env.example` |
| 快速开始 | `docs/zh/Quickstart.md` |
| 配置 | `docs/zh/配置信息.md` |
| E2A | `docs/zh/E2A-protocol.md` |

### 2.2 企业安全与配置

| 主题 | 路径 |
|------|------|
| 工具权限 | `工具权限与安全防护.md`、`permissions_config_architecture.md` |
| 配置下发加签验签 | `配置下发加签验签设计.md` |
| 配置字段加解密 | `配置下发字段级加解密设计.md` |
| 链路握手鉴权 | `链路握手鉴权.md` |
| 前端字段 | `前端字段配置.md` |
| 扩展字段透传 | `扩展字段透传.md` |
| Web 文件上传 | `Web文件上传.md` |
| OpenTelemetry | `OpenTelemetry可观测性.md` |

### 2.3 通用能力（本分支仍有）

频道、命令行、心跳、定时、任务规划、记忆、技能、Skill 自演进、智能体、浏览器、打包 exe、ACP、开发实践等，见 `SUMMARY.md`。

---

## 三、代码与部署索引

### 3.1 顶层结构

```
assets/enterprise_kub/
├── deploy/                 # K8s 一键部署工具（主路径）
├── docs/                   # 中英文档
├── jiuwenclaw/             # 主运行时（Gateway / AgentServer / Web 企业前端）
├── packages/
│   ├── jiuwenclaw-ee/      # 企业扩展（含 claw_manager）
│   └── jiuwenclaw-tui/     # TUI 可选包
├── jiuwenbox/              # 附属产品（若问题相关）
└── pyproject.toml
```

### 3.2 CLI 入口（`pyproject.toml` → `jiuwenclaw.*`）

| 命令 | 职责 |
|------|------|
| `jiuwenclaw-init` | 工作区初始化 |
| `jiuwenclaw-start` | 启动服务 |
| `jiuwenclaw-gateway` / `jiuwenclaw-agentserver` / `jiuwenclaw-web` | 分进程入口 |
| `jiuwenclaw-app` | 聚合入口 |
| `jiuwenclaw-manager` | Manager（企业包） |

### 3.3 `jiuwenclaw/` 关键目录

| 路径 | 职责 |
|------|------|
| `app_gateway.py` / `gateway/` | Gateway |
| `app_agentserver.py` / `agentserver/` | AgentServer（含 `enterprise_config/`、`permissions/`） |
| `web_enterprise/`、`app_enterprise_web.py` | 企业 Web |
| `web/`、`app_web.py` | 通用/开源风格 Web（若并存） |
| `channel/` | IM 频道适配 |
| `e2a/` | E2A 协议 |
| `security/` | 安全与鉴权 |
| `telemetry/` | 可观测性 |
| `extensions/redis/` | Redis 扩展（SessionMap 等） |
| `infrastructure/` | 基础设施辅助 |
| `deployment_mode.py` | 部署模式 |

### 3.4 `deploy/` 部署工具

| 文件 / 目录 | 职责 |
|-------------|------|
| `deploy.sh` | 入口 |
| `gateway_handler.sh` / `web_handler.sh` / `manager_handler.sh` | 业务组件 |
| `redis_handler.sh` / `postgresql_handler.sh` / `mysql_handler.sh` / `minio_handler.sh` / `nfs_handler.sh` / `rabbitmq_handler.sh` | 依赖组件 |
| `k8s_handler.sh` / `template_handler.sh` / `templates/` | K8s 资源渲染与操作 |
| `.env.example` / `.env.custom`（用户侧） | 部署参数 |

### 3.5 概念对照（避免搜错）

| 概念 | 本分支位置 | 勿混淆 |
|------|------------|--------|
| 企业部署 | `deploy/` | 开源 `docker/` 或单机 `jiuwenswarm-start` |
| 主包目录 | `jiuwenclaw/` | 开源 0.2.x 的 `jiuwenswarm/` |
| 企业前端 | `web_enterprise/` | 开源 Vite `channels/web/frontend` |
| Manager | `packages/jiuwenclaw-ee/claw_manager` | Avatar 单机无独立 Manager |
| Runtime / 多租户 | `agentserver/enterprise_config`、ee 包、deploy | 分布式 Team（A2X）不是本路径 |
| SDK 依赖 | `agent-core@enterprise-dev`、`agent-runtime` foundation | 开源固定 PyPI `openjiuwen==0.1.16` |

---

## 四、建议调查顺序

1. 确认问题确属 **企业版** → 使用本索引（不要用 `0.2.3`）。
2. 部署/运维类：先 `deploy/README.md` → 对应 `*_handler.sh` / `templates/`。
3. 安全/鉴权/配置下发：先读 `docs/zh/` 企业专题 → `security/` / `enterprise_config/`。
4. 运行时行为：`jiuwenclaw/gateway`、`agentserver`、`web_enterprise`。
5. 文档与代码冲突时，以 **`assets/enterprise_kub/` 内代码与 deploy 脚本** 为准。

---

## 五、关键词检索提示（在 `assets/enterprise_kub/` 内）

| 想找 | 建议 pattern / 路径 |
|------|---------------------|
| 部署入口 | `deploy.sh`、`DEPLOY` in `deploy/` |
| Manager | `manager_handler`、`claw_manager` |
| RuntimeManagement / 路由 | `RuntimeManagement`、`SessionMap`、`service_id` |
| 企业配置 | `enterprise_config` in `jiuwenclaw/agentserver/` |
| 加签验签 | `加签`、`验签` in `docs/zh/`、`security/` |
| Redis 会话 | `redis` in `jiuwenclaw/extensions/redis/` |
| 企业 Web | `web_enterprise` |
| CLI | `jiuwenclaw-` in `pyproject.toml` |

---

## 六、版本与拉取

| 项 | 值 |
|----|-----|
| assets 目录 | `assets/enterprise_kub/` |
| Git 分支 | **`dev/enterprise_kub`** |
| 工程 version（约） | `0.1.10`（以快照 `pyproject.toml` 为准） |

```bash
bash openjiuwen-jiuwenswarm/scripts/fetch.sh enterprise_kub
# 等价于:
git clone --branch 'dev/enterprise_kub' --depth 1 --single-branch \
  https://gitcode.com/openJiuwen/jiuwenswarm.git \
  openjiuwen-jiuwenswarm/assets/enterprise_kub
```

若目录尚不存在，问答前应先拉取或说明无法取证。
