# DeepSearch 取证补充

> 与 `SKILL.md` 第三步配合使用；版本级路径与「按意图快速定位」仍以 `references/<version>.md` 为准。

## 仓库形态

| 项 | 说明 |
|----|------|
| PyPI 包名 | `openjiuwen-deepsearch`（快照根 `pyproject.toml`） |
| 主 Python 包 | **`openjiuwen_deepsearch`**（命名空间包，`algorithm/` 与 `framework/` 可独立子树） |
| 运行时依赖 | `pyproject.toml` 锁定 **`openjiuwen`** SDK 版本（工作流执行依赖 agent-core） |
| 交付形态 | **SDK**（`main.py`、算法 + 工作流）与 **完整版后端**（`server/` FastAPI）并存 |
| 测试入口 | `pytest`（配置见 `pyproject.toml` `[tool.pytest.ini_options]`）；`tests/conftest.py` |
| 启动 | SDK：`main.py`；后端：`start_backend.py` / `server/main.py`；模板 `.env.example` |

## 顶层目录（速览）

| 目录 | 职责 |
|------|------|
| `openjiuwen_deepsearch/algorithm/` | 核心算法：查询理解、收集、报告、模板、溯源、反馈 |
| `openjiuwen_deepsearch/framework/openjiuwen/` | 工作流节点、Agent 工厂、搜索工具（基于 openjiuwen） |
| `openjiuwen_deepsearch/config/`、`common/`、`utils/` | 配置、异常、日志与工具 |
| `server/` | **完整版** FastAPI：运行记录、报告、知识库、搜索引擎配置 |
| `main.py`、`start_backend.py` | SDK / 后端入口 |
| `docker/` | 容器构建 |
| `tests/` | 按模块镜像的单测与集成测 |

**分层记忆**：`algorithm/`（研究 pipeline）← 被 `framework/.../agent/workflow.py` 编排 ← 可选 `server/` 暴露 REST；勿把 `server/routers/` 与 SDK 内 `deepsearch_agent.py` 混为同一入口。

## 文档路径注意

- 中英双语，有 **`docs/zh/SUMMARY.md`** / **`docs/en/SUMMARY.md`**
- 安装：`docs/zh/2.安装指导/`（**完整版** vs **SDK** 分支不同）
- 开发指南：`docs/zh/4.开发指南/`；目录结构：`directory_structure.md`
- API 文档：`docs/zh/4.开发指南/API文档/`（`deepsearch_agent.md`、`workflow.md`、`deepsearch_rest_api.md` 等）
- 产品说明：`README.md`、`README-en.md`
- 用户未指定语言时中文优先

## 易混概念（搜代码前先对表）

| 概念 | DeepSearch 首选位置 | 勿混淆 |
|------|---------------------|--------|
| `DeepSearchAgent` / 主工作流 | `algorithm/search_agent/deepsearch_agent.py`、`framework/.../agent/workflow.py` | agent-core 通用 `WorkflowAgent` / `ReActAgent` |
| 主图节点 | `framework/.../agent/main_graph_nodes.py` | agent-core 工作流 **组件**目录 |
| 信息收集子图 | `framework/.../agent/collector_graph/` | `algorithm/research_collector/` 为算法实现 |
| 报告 / 模板 | `algorithm/report/`、`algorithm/report_template/` | `server/deepsearch/core/manager/report_manager/` 为后端管理 |
| 溯源 / 引用 | `algorithm/source_trace/`、`source_tracer_infer/` | 普通 RAG citation 非同一套 API |
| 联网搜索 | `framework/.../tools/web_search.py`、`tools/search_api/` | agent-core `retrieval/` 知识库链路 |
| REST API | `server/routers/`、`docs/.../deepsearch_rest_api.md` | SDK 侧 `main.py` 无完整 REST 面 |
| Studio 内 DeepSearch | Studio `routers/deepsearch*.py`（另一仓库） | 本 skill 快照为 **独立 deepsearch 仓** |

完整模块对照见 `references/<version>.md` 第三节「代码索引」与 `directory_structure.md`。

## 与 agent-core / Studio 的边界

| 维度 | DeepSearch（本 skill） | agent-core（SDK） | agent-studio |
|------|------------------------|-------------------|--------------|
| 核心问题 | 深度研究、报告、溯源、搜索 pipeline | 通用 Agent / Workflow / Session | 低代码画布、Studio 安装 |
| 典型入口 | `DeepSearchAgent`、`deepsearch_rest_api` | `openjiuwen.core.runner.Runner` | Apps 对话、知识库 UI |
| 工作流 | `framework/openjiuwen/agent/` 专用节点 | `openjiuwen/core/workflow/` | Studio 画布 DSL |

用户问「深度检索报告模板 / 溯源 / Tavily 配置」→ **本 skill**；问「Session 中断 / MCP 工具注册」→ **`openjiuwen-agent-core`**；问「Studio 知识库页面 / 画布」→ **`openjiuwen-agent-studio`**。

## 与其它产品线的边界

| 用户问 | 本 skill | 应转 |
|--------|----------|------|
| DeepSearch 安装、Agent、报告、溯源、REST、`openjiuwen_deepsearch` | ✓ | — |
| 通用 `openjiuwen` SDK、Runner、MCP（非 DeepSearch 包） | | `openjiuwen-agent-core` |
| Studio 画布、Helm 装 Studio、前后端二次开发 | | `openjiuwen-agent-studio` |
| Runtime Server、`DeploymentManager` | | `openjiuwen-agent-runtime` |
| Java SDK | | `openjiuwen-agent-core-java` |
| jiuwenclaw、Swarm | | `openjiuwen-jiuwenswarm` |

跨产品线消歧详见 `openjiuwen-qa-guideline/references/product-routing.md`。
