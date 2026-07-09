# JiuwenSwarm 取证补充

> 与 `SKILL.md` 第三步配合使用；版本级路径与「按意图快速定位」仍以 `references/<version>.md` 为准。

## 仓库形态

| 项 | 说明 |
|----|------|
| PyPI 包名 | **`jiuwenswarm`**（对外安装名） |
| 主 Python 包 | **`jiuwenclaw`**（运行时源码树） |
| 版本 tag 习惯 | Git tag **无 `v` 前缀**（如 `0.2.0`）；索引文件 `references/0.2.0.md` |
| 版本真相源 | 快照根 `pyproject.toml`（`[project].version`、CLI `[project.scripts]`） |
| 运行时依赖 | `pyproject.toml` 锁定 **`openjiuwen`**（agent-core SDK，常为 Git 分支依赖） |
| 测试入口 | `run_tests.sh`、`pytest.ini`；说明见 `TESTING.md` |
| 工作区 | 用户目录 **`.jiuwenswarm/`**（由 `jiuwenswarm-init` 初始化） |

## 顶层目录（速览）

| 目录 | 职责 |
|------|------|
| `jiuwenclaw/gateway/` | 消息路由、IM 频道、Slash、心跳、定时任务 |
| `jiuwenclaw/server/` | **AgentServer**：runtime、session、skill、向 Gateway 推送 |
| `jiuwenclaw/agents/harness/` | Claw/Code DeepAgent、Team、tools、rails、memory |
| `jiuwenclaw/channels/` | Web、Desktop、ACP/TUI |
| `jiuwenclaw/common/` | E2A 协议、schema、security |
| `jiuwenclaw/instance_manager/` | 单机多实例 |
| `jiuwenbox/` | **JiuwenBox** 子产品（独立 `src/jiuwenbox`） |
| `packages/jiuwenswarm-tui/` | TUI 可选依赖包 |
| `docker/`、`scripts/` | 容器、exe 打包、NFS 等 |

**分层记忆**：IM/Web/TUI → **Gateway** → E2A → **AgentServer** → harness Agent；勿把 `server/gateway_push/`（Server→Gateway 推送）与 `gateway/` 整体混淆。

## CLI 入口（速查）

| 命令 | 模块 |
|------|------|
| `jiuwenswarm-init` | `jiuwenclaw/init_workspace.py` |
| `jiuwenswarm-start` | `jiuwenclaw/start_services.py` |
| `jiuwenswarm-gateway` | `jiuwenclaw/gateway/app_gateway.py` |
| `jiuwenswarm-agentserver` | `jiuwenclaw/server/app_agentserver.py` |
| `jiuwenswarm-web` | `jiuwenclaw/channels/web/app_web.py` |
| `jiuwenswarm-tui` / `jiuwenswarm-acp` | `jiuwenclaw/channels/acp/app_acp.py` |

## 文档路径注意

- 中英双语，有 **`docs/zh/SUMMARY.md`** / **`docs/en/SUMMARY.md`**
- 安装：`docs/zh/安装指南.md`；快速开始：`Quickstart.md`、`Quickstart_tui.md`
- 协议：E2A、A2A、Agent Team 等有独立专题文档
- 产品说明：`README_CN.md`、`README.md`（快照根）
- 用户未指定语言时中文优先

## 易混概念（搜代码前先对表）

| 概念 | JiuwenSwarm 首选位置 | 勿混淆 |
|------|----------------------|--------|
| Gateway | `jiuwenclaw/gateway/` | `server/gateway_push/` 为 Server→Gateway 推送 |
| AgentServer | `jiuwenclaw/server/` | 进程名 `agentserver`，非独立顶层包名 |
| Team / 分布式 | `agents/harness/team/` | openjiuwen `core/multi_agent` 在 **依赖 SDK** 内 |
| E2A | `common/e2a/` | Gateway↔Agent 内部协议 |
| A2A | `agents/harness/team/a2x/`、`docs/zh/A2A.md` | 与 E2A 不同协议层 |
| 工作区 | `init_workspace`、`.jiuwenswarm/` | Studio 工作区或 agent-core 本地项目 |
| JiuwenBox | `jiuwenbox/src/jiuwenbox/` | 主 Claw 运行时 `jiuwenclaw/` |
| 技能 | `server/runtime/skill/skill_manager.py` | agent-core `singleagent/skills/` 在 SDK 依赖中 |

完整对照见 `references/<version>.md` 第三节「概念对照」。

## 与 agent-core / Studio / Runtime 的边界

| 维度 | JiuwenSwarm（本 skill） | agent-core | agent-studio |
|------|-------------------------|------------|--------------|
| 核心问题 | IM 机器人、Gateway、Team、Claw Agent、工作区 | SDK API、Workflow、Runner | 低代码画布、Studio 安装 |
| 典型入口 | `jiuwenswarm-start`、频道接入、E2A | `openjiuwen.core.runner.Runner` | FlowGram、Helm 装 Studio |
| DeepAgent | `agents/harness/claw/`、`code/` | `openjiuwen.harness` | Studio 导出 Agent |

用户问「飞书/Discord 接入、Slash 命令、心跳」→ **本 skill**；问「Workflow 组件 API、Session 中断」→ **`openjiuwen-agent-core`**；问「画布发布、Studio Helm」→ **`openjiuwen-agent-studio`**。

## 与其它产品线的边界

| 用户问 | 本 skill | 应转 |
|--------|----------|------|
| jiuwenclaw、jiuwenswarm、频道、Team、Gateway、工作区 | ✓ | — |
| 通用 `openjiuwen` SDK、workflow 引擎源码 | | `openjiuwen-agent-core` |
| Studio 画布、前后端二次开发 | | `openjiuwen-agent-studio` |
| Runtime Server、`DeploymentManager` | | `openjiuwen-agent-runtime` |
| DeepSearch 报告、溯源 | | `openjiuwen-deepsearch` |
| Java SDK | | `openjiuwen-agent-core-java` |

跨产品线消歧详见 `openjiuwen-qa-guideline/references/product-routing.md`。
