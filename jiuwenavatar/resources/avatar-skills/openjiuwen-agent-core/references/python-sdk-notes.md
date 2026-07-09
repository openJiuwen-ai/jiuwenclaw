# Python SDK 取证补充

> 与 `SKILL.md` 第三步配合使用；版本级路径与「按意图快速定位」仍以 `references/<version>.md` 为准。

## 包与安装

| 项 | 说明 |
|----|------|
| PyPI 包名 | `openjiuwen`（`pip install openjiuwen`） |
| 版本真相源 | `assets/<version>/pyproject.toml` 的 `[project].version` |
| Python 版本 | 以 `pyproject.toml` 的 `requires-python` 为准 |
| 测试入口 | `Makefile`（`make test`、`make test TESTFLAGS=...`） |

## `openjiuwen/` 顶层包（速览）

| 包 | 职责 |
|----|------|
| `core/` | SDK 运行时：工作流、单智能体、Session、Runner、检索、记忆等 |
| `harness/` | DeepAgent 编码框架：factory、rails、tools、workspace、cli |
| `agent_teams/` | 团队编排、DeepAgentSpec、TeamRuntime 池 |
| `agent_evolving/` | 演进、轨迹、RL、optimizer |
| `dev_tools/` | tune、prompt_builder、agent_builder |
| `extensions/` | checkpointer、store、a2a、context_evolver 等 |
| `auto_harness/` | 自动化评测 / CI gate 流水线 |

公开 API 以各包 `__init__.py` 导出、`AGENTS.md` 与 API 文档为准；**`legacy/` 仅兼容，勿新功能依赖**。

## 文档路径注意

- 中文开发指南：`docs/zh/2.开发指南/...`
- 英文对应：`docs/en/2.Development Guide/...`（目录名含空格，以磁盘实际文件夹名为准）
- API 文档：中文 `2.开发指南/API文档/`，英文 `2.Development Guide/API Docs/`
- 英文路径映射见各 `references/<version>.md` 第一节表格脚注

## 易混概念（搜代码前先对表）

| 概念 | 首选位置 | 勿混淆 |
|------|----------|--------|
| `ReActAgent` | `core/single_agent/agents/react_agent.py` | `workflow/components/llm/react/` 为**工作流组件** |
| `WorkflowAgent` | `core/application/workflow_agent/` | `core/workflow/workflow.py` 为流程引擎 |
| `DeepAgent` | `harness/deep_agent.py` | `agent_teams/schema/deep_agent_spec.py` 为团队规格 |
| `TeamRuntime` | `core/multi_agent/team_runtime/` | `agent_teams/runtime/` 为团队池管理 |
| `Runner` | `core/runner/runner.py` | 全局 `resource_mgr` 共享 |

完整对照见 `references/<version>.md` 第三节「类型与概念对照」。

## 与其它产品线的边界

| 用户问 | 本 skill | 应转 |
|--------|----------|------|
| `openjiuwen.Agent` / workflow / MCP / Session API | ✓ | — |
| Studio 画布、Helm 装 Studio、前后端二次开发 | | `openjiuwen-agent-studio` |
| `DeploymentManager`、runtime-server、k8s 部署 Agent | | `openjiuwen-agent-runtime` |
| DeepSearch 报告模板、溯源、`deepsearch_agent` | | `openjiuwen-deepsearch` |
| Java SDK、`com.openjiuwen` Maven | | `openjiuwen-agent-core-java` |
| jiuwenclaw、IM 机器人、Swarm Team | | `openjiuwen-jiuwenswarm` |

跨产品线消歧详见 `openjiuwen-qa-guideline/references/product-routing.md`。
