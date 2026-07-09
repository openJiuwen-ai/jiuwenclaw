# Java SDK 取证补充

> 与 `SKILL.md` 第三步配合使用；版本级路径与「按意图快速定位」仍以 `references/<version>.md` 为准。

## Maven 与构建

| 项 | 说明 |
|----|------|
| Maven 坐标 | `com.openjiuwen:agent-core-java` |
| 版本真相源 | `assets/<version>/pom.xml` 的 `<version>` |
| Java 版本 | 以 `pom.xml` 的 `<maven.compiler.release>` / `<java.version>` 为准（当前多为 **Java 21**） |
| 测试入口 | `mvn test`（模块根目录即快照根 `assets/<version>/`） |
| 示例运行 | 各 `examples/<topic>/` 子目录 README；共享工具见 `examples/utils/` |

## `com.openjiuwen` 顶层包（速览）

| 包 | 职责 |
|----|------|
| `core/` | SDK 运行时：工作流、单智能体、Session、Runner、检索、记忆、Controller 等 |
| `agent_evolving/` | 智能体演进：轨迹、Trainer、Optimizer |
| `deepagents/` | DeepAgent 工厂与中间件（Java 侧轻量封装） |
| `dev_tools/` | tune、prompt_builder、skill_creator |
| `extensions/` | checkpointer、store、context_evolver、厂商适配 |
| `spi/` | Store 等 SPI 扩展点 |

公开 API 以 **`documents/zh/`** 下 API 文档与源码为准；**`legacy/` 包仅兼容，勿新功能依赖**。

## 文档路径注意

- 中文 API / 模块导航：`documents/zh/SUMMARY.md`
- 开发指南：`documents/zh/2.开发指南/...`
- API 文档根：`documents/zh/2.开发指南/API文档/com.openjiuwen.core/`
- 产品介绍：`README.zh.md`（中文）、`README.md`（英文）
- 本版本通常**没有**与 Python 仓对等的 `documents/en/` 全量树；深度 API 叙述以 **`documents/zh/`** 为准。用户未指定语言时中文优先；英文语境可对照 `README.md` 与 Java 类名，并注明详细叙述来自的中文文档路径。

## 与 Python SDK 的边界（勿混用 API）

| 维度 | Java（本 skill） | Python（`openjiuwen-agent-core`） |
|------|------------------|-----------------------------------|
| 安装 | Maven `com.openjiuwen:agent-core-java` | `pip install openjiuwen` |
| 源码根 | `src/main/java/com/openjiuwen/` | `openjiuwen/` |
| 文档树 | `documents/zh/` | `docs/zh/`、`docs/en/` |
| DeepAgent | `deepagents/`（轻量） | `harness/`（完整 DeepAgent 框架） |
| 团队编排 | `core/multiagent/` | `agent_teams/` + `core/multi_agent/team_runtime/` |
| 包管理器 | Maven / `pom.xml` | uv/pip / `pyproject.toml` |

概念名相近（如 `ReActAgent`、`WorkflowAgent`、`Runner`）时，**以当前语言 SDK 快照为准**，勿跨语言照搬 import 或构造方式。

## 易混概念（搜代码前先对表）

| 概念 | 首选位置 | 勿混淆 |
|------|----------|--------|
| `ReActAgent` | `core/singleagent/agents/ReActAgent.java` | `application/schema/ReActAgentConfig.java` 仅为配置；`workflow/components/llm/` 为**工作流组件** |
| `LlmAgent` | `core/application/llm/LlmAgent.java` | 基于 Controller 的 LLM 应用 Agent |
| `WorkflowAgent` | `core/application/workflow/WorkflowAgent.java` | `core/workflow/Workflow.java` 为流程引擎 |
| `Runner` | `core/runner/Runner.java` | 资源见 `runner/resourcemanager/ResourceMgr.java` |
| `BaseGroup` | `core/multiagent/BaseGroup.java` | `multiagent/legacy/` 为旧版组 API |
| `ContextEvolvingReActAgent` | `extensions/context_evolver/ContextEvolvingReActAgent.java` | 继承 `core/singleagent/agents/ReActAgent.java` |
| Checkpointer | `core/session/checkpointer/` | `extensions/checkpointer/redis/` 为 Redis 实现 |
| MCP 工具 | `core/foundation/tool/mcp/McpTool.java` | 无独立顶层 `mcp/` 包，见各示例中的工具注册 |

**源码结构提示**：工作流同时存在 `workflow/components/`（内置组件类）与 `workflow/component/`（执行辅助），读实现时注意包名，勿混目录。

完整对照见 `references/<version>.md` 第三节「类型与概念对照」。

## 与其它产品线的边界

| 用户问 | 本 skill | 应转 |
|--------|----------|------|
| `com.openjiuwen` Maven、Java Agent / Workflow / Session API | ✓ | — |
| `pip install openjiuwen`、Python workflow / MCP / DeepAgent harness | | `openjiuwen-agent-core` |
| Studio 画布、Helm 装 Studio、前后端二次开发 | | `openjiuwen-agent-studio` |
| `DeploymentManager`、runtime-server、k8s 部署 Agent | | `openjiuwen-agent-runtime` |
| DeepSearch 报告模板、溯源、`deepsearch_agent` | | `openjiuwen-deepsearch` |
| jiuwenclaw、IM 机器人、Swarm Team | | `openjiuwen-jiuwenswarm` |

跨产品线消歧详见 `openjiuwen-qa-guideline/references/product-routing.md`。
