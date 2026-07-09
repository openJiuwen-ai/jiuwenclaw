# Agent Runtime 取证补充

> 与 `SKILL.md` 第三步配合使用；版本级路径与「按意图快速定位」仍以 `references/<version>.md` 为准。

## 仓库形态

| 项 | 说明 |
|----|------|
| 形态 | **Monorepo**：`server/`、`management/`、`service/`、`foundation/`、`applications/`、`cli/` 等子包 |
| 根目录 | **无**统一 `pyproject.toml`；各子目录独立 `[project]` |
| Python 命名空间 | 均为 **`openjiuwen_runtime`**（按子包分别安装） |
| 版本真相源 | 各子目录 `pyproject.toml` 的 `[project].version`（如 `server` → `agent-runtime-server`） |
| 测试入口 | 各模块内 `tests/` 或 `test/`；无仓库级统一 `Makefile` |
| 启动 | `scripts/run-server.sh` / `run-server.ps1`；配置模板 `server/.env.example` |

## 顶层模块（速览）

| 目录 | PyPI 名（示例） | 职责 |
|------|-----------------|------|
| `server/` | `agent-runtime-server` | FastAPI **管理面**：部署 / 查询 / 删除 Agent |
| `management/` | `openjiuwen-runtime-management` | `DeploymentManager`、subprocess / docker / k8s 策略 |
| `service/` | `openjiuwen-runtime-service` | **对话面**：`AgentApp`、`BaseApp`、SSE `/query` |
| `foundation/` | `openjiuwen-runtime-foundation` | DB、配置、端口、Docker、venv、日志 |
| `applications/` | 各应用子包 | 低码 IR、`workflow_agent`、`llm_agent`、`ir_execution_service` |
| `cli/` | `agent-runtime-cli` | 命令行工具 |
| `docker/`、`scripts/` | — | 镜像构建与运维脚本 |

**分层记忆**：`server` 暴露 REST → 调用 `management` 部署 → 被部署进程内跑 `service` + `applications` 适配具体 Agent 类型。

## 文档路径注意

- 中英双语，**按编号文件名**导航（无 `SUMMARY.md`）：
  - 中文：`docs/zh/0. 项目介绍.md` … `4. Agent接入.md`
  - 英文：`docs/en/0. Project Overview.md` … `4. Agent Integration.md`
- 产品说明：`README.md`（中文）、`README_en.md`（英文）
- 配置对照：`server/.env.example` + `docs/zh/2. 配置说明.md`（英文 `2. Configuration.md`）
- 用户未指定语言时中文优先；英文文档与中文序号对应，文件名不同

## 易混概念（搜代码前先对表）

| 概念 | 首选位置 | 勿混淆 |
|------|----------|--------|
| Runtime Server（管理面） | `server/openjiuwen_runtime/server/main.py` | Agent 进程内的 `service` 对话 API |
| `/api/v1/agents/deploy` | `server/.../main.py` | agent-core SDK 的 `Runner` / 本地调试 |
| `DeploymentManager` | `management/.../manager.py` | `server` 仅 HTTP 转发与校验 |
| 部署策略 | `management/deployments/subprocess|docker|k8s/` | Studio 前端「发布」是另一产品链路 |
| `AgentApp` / `/query` | `service/.../app/agent_app.py` | Runtime Server 的部署 REST |
| 低码 IR 部署 | `applications/lowcode_agent/`、`ir_execution_service/` | agent-core 工作流引擎在 **SDK 依赖** 中 |
| `DEPLOY_TYPE` / `mode=docker` | `foundation/.../config.py`、`docs/zh/3. Agent部署.md` | 通用 Docker Compose 运维问题 |

完整对照见 `references/<version>.md` 第三节「概念对照」。

## 与 agent-core / Studio 的边界

| 维度 | Agent Runtime（本 skill） | agent-core（SDK） | agent-studio |
|------|---------------------------|-------------------|--------------|
| 核心问题 | 生产部署、多租户、REST 部署 API | Agent / Workflow API、Session、Runner | 低代码画布、Studio 安装 |
| 典型入口 | `/api/v1/agents/deploy`、`AgentApp` | `openjiuwen.core.runner.Runner` | FlowGram、Helm 装 Studio |
| 低码 IR | `applications/` 侧适配与执行 | SDK 内工作流 / Agent 实现 | Studio 导出 IR |

用户问「怎么写 ReActAgent」→ 通常 **`openjiuwen-agent-core`**；问「怎么把 Agent 部署到 k8s / Runtime Server」→ **本 skill**。

## 与其它产品线的边界

| 用户问 | 本 skill | 应转 |
|--------|----------|------|
| Runtime Server、DeploymentManager、docker/k8s 部署、`AgentApp` | ✓ | — |
| `openjiuwen` SDK、workflow、Session、MCP | | `openjiuwen-agent-core` |
| Studio 画布、前后端二次开发、Helm 装 Studio | | `openjiuwen-agent-studio` |
| DeepSearch 报告、溯源、`deepsearch_agent` | | `openjiuwen-deepsearch` |
| Java SDK、`com.openjiuwen` Maven | | `openjiuwen-agent-core-java` |
| jiuwenclaw、IM 机器人、Swarm Team | | `openjiuwen-jiuwenswarm` |

跨产品线消歧详见 `openjiuwen-qa-guideline/references/product-routing.md`。
