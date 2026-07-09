# Agent Studio 取证补充

> 与 `SKILL.md` 第三步配合使用；版本级路径与「按意图快速定位」仍以 `references/<version>.md` 为准。

## 仓库形态

| 项 | 说明 |
|----|------|
| 形态 | **Monorepo**：`backend/`、`frontend/`、`plugin_server/`、`sandbox_server/`、`scripts/`、`docker/`、`helm/` |
| 产品线锚点 | Git tag（如 `v0.1.7`）与 `references/<version>.md` |
| 版本字段 | **前后端/子包版本可能不一致**——以各 `package.json` / `pyproject.toml` 为准；索引文末常有「版本矩阵」 |
| 运行时依赖 | 后端 `backend/pyproject.toml` 声明 `openjiuwen[...]` SDK 版本（执行引擎在 SDK 内） |
| 测试 | 主要在 `backend/.../core/dsl_converter/tests/`；**无**仓库顶层统一 `tests/` |

## 顶层目录（速览）

| 目录 | 包名（示例） | 职责 |
|------|-------------|------|
| `backend/openjiuwen_studio/` | `openjiuwen-studio` | FastAPI 主服务：CRUD、执行、DSL 转换 |
| `frontend/` | `jiuwen-agent-studio` | Vite + React 主应用 |
| `frontend/packages/workflow-canvas/` | `workflow-canvas` | **FlowGram 画布**编辑器 |
| `frontend/packages/api-client/` | `@test-agentstudio/api-client` | 前端 API 封装 |
| `plugin_server/` | `openjiuwen-plugin-server` | 独立插件 REST 服务 |
| `sandbox_server/` | sandbox-server / gateway | 代码组件沙箱执行 |
| `scripts/`、`docker/`、`helm/studio/` | — | 部署、升级、K8s |

**分层记忆**：`routers/`（HTTP）→ `core/manager/`（业务/转换）→ `core/executor/`（执行）；前端 `src/pages/` → `api-client` → 画布问题下钻 `workflow-canvas/`。

## 文档路径注意

- 中英双语，有 **`docs/zh/SUMMARY.md`** / **`docs/en/SUMMARY.md`**
- 英文目录名常含**空格**（如 `2.Installation Guide`、`4.Development Guide`），以磁盘实际文件夹名为准
- 安装：`docs/zh/2.安装指导/`；开发指南：`docs/zh/4.开发指南/`；教程：`docs/zh/5.实践教程/`
- 用户未指定语言时中文优先；英文路径对照 `docs/en/SUMMARY.md`

## 易混概念（搜代码前先对表）

| 概念 | Studio 首选位置 | 勿混淆 |
|------|-----------------|--------|
| 工作流画布 DSL | `core/common/dsl.py`、`core/manager/convertor/` | agent-core 的 `core/workflow/workflow.py`（SDK 引擎） |
| 工作流执行 | `core/executor/workflow/workflow_runner.py` | `lowcode/runtime_workflow_runner.py`（低代码路径） |
| DSL 导入导出 | `core/dsl_converter/` | `routers/workflows.py` 仅为 API 层 |
| 智能体（平台） | `core/manager/agent.py`、`routers/agents.py` | agent-core 的 `ReActAgent` / `WorkflowAgent` |
| 插件（Studio） | `plugin_server/` + `routers/plugin.py` + `marketplace/` | agent-core 的 `core/foundation/tool/` |
| 代码组件沙箱 | `sandbox_server/` + `code_runner/remote.py` | agent-core 的 `core/sys_operation/sandbox/` |
| 提示词（Ops） | `ops/modules/prompt/` | agent-core 的 `dev_tools/prompt_builder/` |
| 前端画布 | `packages/workflow-canvas/` | `src/components/Workflow/` 为页面壳层 |
| 智能体发布 / Runtime | `routers/runtimes.py`、`core/manager/runtime.py` | **`openjiuwen-agent-runtime`** 的 DeploymentManager |

完整对照见 `references/<version>.md`「类型与概念对照」。

## 与 SDK / Runtime 的边界

| 维度 | Agent Studio（本 skill） | agent-core（SDK） | agent-runtime |
|------|--------------------------|-------------------|---------------|
| 核心问题 | 低代码平台、画布、安装、前后端二次开发 | Agent / Workflow API、Runner | 生产部署 REST、`AgentApp` |
| 典型入口 | FlowGram 画布、`/dashboard/workflows` | `openjiuwen.core.runner.Runner` | `/api/v1/agents/deploy` |
| 执行实现 | 后端 `import openjiuwen` 调用 SDK | SDK 源码与 API 文档 | Runtime 进程内 `service` |

用户问「画布节点怎么配置 / Studio Helm 安装」→ **本 skill**；问「ReActAgent 怎么写 / Session API」→ **`openjiuwen-agent-core`**；问「部署到 k8s Runtime Server」→ **`openjiuwen-agent-runtime`**。

## 与其它产品线的边界

| 用户问 | 本 skill | 应转 |
|--------|----------|------|
| Studio 安装、画布、前后端、插件/沙箱、Helm | ✓ | — |
| `openjiuwen` SDK、workflow 引擎源码、MCP | | `openjiuwen-agent-core` |
| `DeploymentManager`、runtime-server、独立部署 API | | `openjiuwen-agent-runtime` |
| DeepSearch 独立服务（非 Studio 内嵌） | | `openjiuwen-deepsearch` |
| Java SDK | | `openjiuwen-agent-core-java` |
| jiuwenclaw、Swarm | | `openjiuwen-jiuwenswarm` |

跨产品线消歧详见 `openjiuwen-qa-guideline/references/product-routing.md`。
