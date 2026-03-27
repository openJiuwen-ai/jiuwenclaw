# DeepAgents 集成优化 JiuwenClaw 开发指南

本文档指导如何在本地开发、调试和验证 JiuwenClaw 的 DeepAgents SDK 集成。

## 目录

- [环境准备](#环境准备)
- [代码下载与配置](#代码下载与配置)
- [本地 openjiuwen 代码同步](#本地-openjiuwen-代码同步)
- [启动服务验证](#启动服务验证)
- [修改 interface_deep.py 并调试](#修改-interface_deeppy-并调试)
- [常见问题](#常见问题)

---

## 环境准备

### 前置要求

- **Python**: >=3.11, <3.14
- **Node.js**: >=18.0.0 (仅用于构建前端或 browser-use；推荐 20 LTS)
- **uv**: Python 包管理器

### 安装 uv

#### 方式一：官方脚本安装（推荐）

```bash
# Windows (PowerShell)
irm https://astral.sh/uv/install.ps1 | iex

# macOS/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

#### 方式二：通过 pip 安装（网络受限时）

```bash
pip install uv --upgrade -i https://mirrors.aliyun.com/pypi/simple/
```

> ⚠️ 注意：通过 pip 安装的 uv 可能不是最新版，且启动稍慢。

#### 方式三：从 GitHub Releases 手动下载（网络受限时）

1. 访问 [uv Releases](https://github.com/astral-sh/uv/releases) 页面
2. 下载对应系统的安装包：
   - Windows: `uv-x86_64-pc-windows-msvc.zip`
   - Linux: `uv-x86_64-unknown-linux-gnu.tar.gz`
   - macOS: `uv-x86_64-apple-darwin.tar.gz`
3. 解压后将 `uv` 和 `uvx` 可执行文件放到 PATH 目录：
   - Windows: 放到 `%USERPROFILE%\.local\bin\` 并添加到 PATH
   - Linux/macOS: 放到 `~/.local/bin/` 或 `/usr/local/bin/`

#### 验证安装

```bash
uv --version
```

### 创建虚拟环境

```bash
# 使用 uv 创建虚拟环境
uv venv --python=3.11

# 或使用 Anaconda
conda create -n JiuwenClaw python=3.11
```

---

## 代码下载与配置

### 1. 下载 JiuwenClaw 代码

```bash
git clone https://gitcode.com/wangxiaolong100/jiuwenclaw.git
cd jiuwenclaw
```

### 2. 同步项目依赖

```bash
# 同步所有依赖
uv sync

# 如果需要开发依赖
uv sync --extra dev
```

### 3. 安装前端依赖

```bash
cd jiuwenclaw/web
npm install
npm run build
cd ../..
```

> 💡 **提示**：`npm run build` 用于构建前端资源，构建产物会输出到 `jiuwenclaw/web/dist` 目录。如果跳过此步骤，启动服务时会报 `dist directory not found` 错误。

---

## 本地 openjiuwen 代码同步

JiuwenClaw 依赖 `openjiuwen` 包，开发时需要同步本地 openjiuwen 代码。

### 方式一：使用 uv 同步本地 agent-core（推荐）

> ⚠️ **前提条件**：`agent-core` 目录必须包含 `pyproject.toml` 文件。如果缺失，请先在 `agent-core` 目录下创建：
>
> ```toml
> [project]
> name = "openjiuwen"
> version = "0.1.0"
> description = "OpenJiuwen Agent Framework"
> requires-python = ">=3.11"
> dependencies = []
>
> [tool.setuptools.packages.find]
> include = ["openjiuwen*"]
> ```
>
> > 💡 **提示**：如果 `agent-core` 已有 `pyproject.toml` 但仍报错，只需在文件末尾添加 `[tool.setuptools.packages.find]` 配置即可。
>
> > ⚠️ **注意**：此修改仅用于本地开发调试，**请勿将修改后的 `pyproject.toml` 提交到版本库**，以免影响其他开发者。建议在 `.gitignore` 或 `git update-index --assume-unchanged` 中忽略此变更。

在 `pyproject.toml` 中配置 openjiuwen 指向本地 agent-core 路径：

```toml
[tool.uv.sources]
openjiuwen = { path = "../agent-core", editable = true }
```

然后同步：

```bash
uv sync --upgrade-package openjiuwen
```

> **路径修改样例**：根据你的实际目录结构调整 `path` 值：
>
> | 目录结构 | path 配置 |
> |----------|----------|
> | 同级目录 | `{ path = "../agent-core", editable = true }` |
> | 上两级目录 | `{ path = "../../DeepAgents/agent-core", editable = true }` |
> | 绝对路径（Windows） | `{ path = "D:/Code/Projects/DeepAgents/agent-core", editable = true }` |
> | 绝对路径（macOS/Linux） | `{ path = "/home/user/projects/DeepAgents/agent-core", editable = true }` |

### 方式二：使用环境变量（临时调试）

```bash
# Windows (PowerShell)
$env:PYTHONPATH = "D:\Code\Projects\DeepAgents\agent-core"

# macOS/Linux
export PYTHONPATH="/path/to/agent-core"
```

### 方式三：使用远程个人 agent-core 仓库（可选）

直接修改 `pyproject.toml` 中 `dependencies` 的 openjiuwen 配置，将 git 地址改为你的个人仓库：

```toml
dependencies = [
    ...
    # 原配置（注释掉）
    # "openjiuwen @ git+https://gitcode.com/openJiuwen/agent-core.git@develop",
    # 个人仓库配置
    "openjiuwen @ git+https://gitcode.com/your-username/agent-core.git@your-branch",
    ...
]
```

然后同步依赖：

```bash
uv sync --upgrade-package openjiuwen
```

> **仓库配置样例**：
>
> | 平台 | 配置示例 |
> |------|----------|
> | GitCode | `"openjiuwen @ git+https://gitcode.com/your-username/agent-core.git@main"` |
> | GitHub | `"openjiuwen @ git+https://github.com/your-username/agent-core.git@main"` |
> | Gitee | `"openjiuwen @ git+https://gitee.com/your-username/agent-core.git@develop"` |
> | 私有仓库（SSH） | `"openjiuwen @ git+ssh://git@gitcode.com/your-username/agent-core.git@feature-branch"` |
> | 指定标签/版本 | `"openjiuwen @ git+https://gitcode.com/your-username/agent-core.git@v0.1.0"` |

> ⚠️ 注意：此方式会修改 `pyproject.toml`，提交代码时需注意不要误提交个人仓库配置，以免影响其他开发者。

### 各方式对比

| 方式 | 优点 | 缺点 | 适用场景 |
|------|------|------|----------|
| 方式一：`[tool.uv.sources]` | 不影响他人，灵活切换路径 | 仅 uv 支持，需 pyproject.toml | 本地开发调试（推荐） |
| 方式二：环境变量 | 临时生效，不修改文件 | 每次需设置 | 快速临时调试 |
| 方式三：个人远程仓库 | 便于分享和协作 | 需维护仓库，注意提交 | 多人协作或远程开发 |

### 验证 openjiuwen 是否正确加载

```python
import openjiuwen
print(openjiuwen.__file__)
# 应该输出本地 openjiuwen 路径
```

---

## 启动服务验证

### 方式一：前后端一起启动（推荐）

使用 `start_services` 脚本一键启动所有服务：

```bash
# 启动所有服务（前端 + 后端）
uv run python -m jiuwenclaw.start_services

# 或指定模式
uv run python -m jiuwenclaw.start_services all   # 前端 + 后端
uv run python -m jiuwenclaw.start_services app   # 仅后端
uv run python -m jiuwenclaw.start_services web   # 仅前端
uv run python -m jiuwenclaw.start_services dev   # 开发模式（热重载前端）
```

### 方式二：分别启动前后端

```bash
# 终端 1：启动后端
uv run python -m jiuwenclaw.app

# 终端 2：启动前端
uv run python -m jiuwenclaw.app_web
```

### 方式三：开发模式（前端热重载）

```bash
# 先构建前端
cd jiuwenclaw/web
npm run build
cd ../..

# 启动开发模式
uv run python -m jiuwenclaw.start_services dev
```

### 方式四：PyCharm 调试配置

在 PyCharm 中配置调试运行，方便断点调试：

#### 步骤 1：创建运行配置

1. 点击 PyCharm 右上角 **Add Configuration...**
2. 点击 **+** → 选择 **Python**
3. 配置如下：

| 配置项 | 值 |
|--------|-----|
| Name | JiuwenClaw: Start Services |
| Script path | 选择项目根目录，或留空使用 Module |
| Module name | `jiuwenclaw.start_services` |
| Parameters | `all` |
| Python interpreter | 选择项目的虚拟环境 |
| Working directory | `D:\Code\Projects\JiuwenClaw\jiuwenclaw` |
| Environment variables | `JIUWENCLAW_AGENT_SDK=deepagents`（可选） |

#### 步骤 2：配置截图参考

```
┌─────────────────────────────────────────────────────────────┐
│ Run/Debug Configurations                                    │
├─────────────────────────────────────────────────────────────┤
│ Name: JiuwenClaw: Start Services                            │
│                                                             │
│ Configuration:                                              │
│ ┌─────────────────────────────────────────────────────────┐ │
│ │ Script path: [留空]                                      │ │
│ │ Module name: jiuwenclaw.start_services                   │ │
│ │ Parameters: all                                          │ │
│ │ Python interpreter: Python 3.11 (jiuwenclaw)            │ │
│ │ Working directory: D:\Code\Projects\JiuwenClaw\jiuwenclaw│ │
│ │ Environment variables:                                   │ │
│ │   JIUWENCLAW_AGENT_SDK=deepagents                        │ │
│ └─────────────────────────────────────────────────────────┘ │
│                                                             │
│ [OK]  [Cancel]  [Apply]                                     │
└─────────────────────────────────────────────────────────────┘
```

#### 步骤 3：启动调试

1. 在代码中设置断点（如 `interface_deep.py`）
2. 点击 **Debug** 按钮（虫子图标）启动调试
3. 访问 http://localhost:5173 触发请求，断点会自动命中

#### 常用调试配置模板

可根据需要创建多个配置：

| 配置名称 | Module | Parameters | 用途 |
|----------|--------|------------|------|
| JiuwenClaw: All | `jiuwenclaw.start_services` | `all` | 前后端一起启动 |
| JiuwenClaw: Backend | `jiuwenclaw.app` | - | 仅后端 |
| JiuwenClaw: Frontend | `jiuwenclaw.app_web` | - | 仅前端 |
| JiuwenClaw: Dev | `jiuwenclaw.start_services` | `dev` | 开发模式 |

### 访问服务

启动成功后，打开浏览器访问：

- **Web UI**: http://localhost:5173

---

## 修改 interface_deep.py 并调试

> ⚠️ **重要原则**：
> 1. 不要修改 `jiuwenclaw/agentserver/` 下原有的公共代码（如 `interface.py`、`session_manager.py` 等）。所有 DeepAgents SDK 相关的修改都应在 `deep_agent/` 目录内进行，保持适配器模式的隔离性。
> 2. 如果修改代码逻辑，需要将修改逻辑相关的文档归档在 `jiuwenclaw/docs/JiuwenClaw集成DeepAgents SDK/` 目录下，便于后续维护和知识沉淀。

### 文件位置

```
jiuwenclaw/
└── jiuwenclaw/
    └── agentserver/
        ├── interface.py              # Facade 层 - 统一入口（不要修改）
        ├── agent_adapters.py         # Protocol 层 - 适配器协议（不要修改）
        ├── session_manager.py        # Session 管理器（不要修改）
        ├── skill_manager.py          # Skills 管理器（不要修改）
        ├── interface_react.py        # ReAct 适配器
        └── deep_agent/               # ⭐ DeepAgents 专属目录
            ├── __init__.py
            ├── interface_deep.py     # DeepAgents 适配器 - 主要修改文件
            └── ...                   # 其他 DeepAgents 相关模块
```

### 架构说明

根据 [multi-sdk-adapter-design.md](./multi-sdk-adapter-design.md)，JiuwenClaw 采用多 SDK 适配架构：

```
┌─────────────────────────────────────────────────────────┐
│                    interface.py (Facade)                │
│  - 统一入口 API                                          │
│  - SDK 工厂路由                                          │
│  - skill_manager (创建并管理)                            │
│  - session_manager (创建并管理)                          │
│  - 公共编排（Skills 路由、heartbeat、流式包装）           │
└───────────────────────┬─────────────────────────────────┘
                        │
          ┌─────────────┼─────────────┐
          │             │             │
          ▼             ▼             ▼
┌───────────────┐ ┌───────────┐ ┌─────────────────────┐
│session_manager│ │skill_mgr  │ │     Adapters        │
│ - session队列 │ │ - skills  │ ├─────────────────────┤
│ - 任务提交    │ │ - hooks   │ │ interface_react.py  │
│ - 任务取消    │ │           │ │ interface_deep.py   │
└───────────────┘ └───────────┘ └─────────────────────┘
```

### 切换到 DeepAgents SDK

默认使用 DeepAgents SDK。如需确认：

```bash
# 设置环境变量（可选）
# Windows (PowerShell)
$env:JIUWENCLAW_AGENT_SDK = "deepagents"

# macOS/Linux
export JIUWENCLAW_AGENT_SDK=deepagents
```

### 调试步骤

#### 1. 修改代码

编辑 `jiuwenclaw/agentserver/deep_agent/interface_deep.py`：

```python
class JiuWenClawDeepAdapter:
    """Deep SDK 适配器，实现 AgentAdapter 协议."""

    async def create_instance(self, config: dict[str, Any] | None = None) -> None:
        """初始化 DeepAgent 实例."""
        # 添加调试日志
        logger.info("[JiuWenClawDeepAdapter] 开始初始化...")
        
        # 你的修改...
        
        logger.info("[JiuWenClawDeepAdapter] 初始化完成")
```

#### 2. 添加断点

在 PyCharm 中添加断点：

1. 打开 `jiuwenclaw/agentserver/deep_agent/interface_deep.py` 文件
2. 在需要调试的代码行号左侧**点击**，出现**红色圆点**表示断点已设置
3. 或使用快捷键：将光标移到目标行，按 `Ctrl + F8` 切换断点

```
  1  class JiuWenClawDeepAdapter:
  2      """Deep SDK 适配器，实现 AgentAdapter 协议."""
  3  
  4      async def create_instance(self, config: dict[str, Any] | None = None) -> None:
  5          """初始化 DeepAgent 实例."""
  6🔴        logger.info("[JiuWenClawDeepAdapter] 开始初始化...")  # ← 点击行号设置断点
  7          
  8          # 你的修改...
  9  
 10🔴        logger.info("[JiuWenClawDeepAdapter] 初始化完成")  # ← 另一个断点
```

#### 3. 启动调试

1. 选择之前配置的 **JiuwenClaw: Start Services** 运行配置
2. 点击 **Debug** 按钮（虫子图标 🐛）或按 `Shift + F9`
3. 服务启动后，访问 http://localhost:5173 触发请求
4. 程序会在断点处暂停，可以查看变量、调用栈等

#### 4. 重启服务验证

修改代码后需要重启服务：

```bash
# 方式一：命令行重启
# 停止当前服务 (Ctrl+C)
# 重新启动
uv run python -m jiuwenclaw.start_services
```

或在 PyCharm 中：

1. 点击 **Stop** 按钮（红色方块 ⏹）停止当前调试
2. 重新点击 **Debug** 按钮（虫子图标 🐛）启动

#### 5. 查看日志

日志会输出到 PyCharm 的 **Run/Debug** 控制台，关注以下关键日志：

```
[JiuWenClaw] Initialized adapter: sdk=deepagents
[JiuWenClawDeepAdapter] 初始化完成: agent_name=main_agent
[JiuWenClaw] 处理请求: request_id=xxx channel_id=web session_id=default sdk=deepagents
```

---

## 关键代码位置

> ⚠️ **注意**：当前正在适配中，后续结构可能会有变化，请以实际代码为准。

### 1. Facade 层 (`interface.py`)

统一入口，负责 SDK 路由和公共编排：

```python
class JiuWenClaw:
    def __init__(self):
        self._adapter: AgentAdapter | None = None
        self._sdk_name: str | None = None
        self._skill_manager = SkillManager()
        self._session_manager = SessionManager()

    async def process_message(self, request: AgentRequest) -> AgentResponse:
        adapter = self._ensure_adapter()
        # ... 路由到 adapter.process_message_impl()
```

### 2. DeepAgents 适配器 (`interface_deep.py`)

Deep SDK 专属逻辑：

```python
class JiuWenClawDeepAdapter:
    async def create_instance(self, config):
        # 初始化 DeepAgent
        self._instance = create_deep_agent(...)
        
    async def process_message_impl(self, request, inputs):
        # 执行 DeepAgent
        result = await Runner.run_agent(agent=self._instance, inputs=inputs)
```

### 3. Rail 管理

Rail 是 DeepAgents 的核心扩展机制，用于在 Agent 运行的不同阶段注入自定义逻辑。

#### Rail 类型

| Rail 类型 | 文件位置 | 用途 | 注册方式 |
|-----------|----------|------|----------|
| `FileSystemRail` | openjiuwen | 文件系统操作能力 | 静态 |
| `SkillUseRail` | openjiuwen | Skills 技能调用 | 静态 |
| `JiuClawStreamEventRail` | `rails/stream_event_rail.py` | 流式事件处理 | 静态 |
| `ToolPromptRail` | `rails/tool_prompt_rail.py` | 工具提示注入 | 静态 |
| `TaskPlanningRail` | openjiuwen | 任务规划 | 动态 |

#### 静态 Rail 注册

静态 Rail 在 Agent 初始化时构建并注册：

```python
def _build_agent_rails(self, config: dict[str, Any]) -> list[Any]:
    """构建静态 Rails 列表"""
    rail_infos = [
        _RailBuildInfo("_filesystem_rail", self._build_filesystem_rail),
        _RailBuildInfo("_skill_rail", self._build_skill_rail,
                       {"config": config, "include_tools": self._filesystem_rail is None}),
        _RailBuildInfo("_stream_event_rail", self._build_stream_event_rail),
        _RailBuildInfo("_tool_prompt_rail", self._build_tool_prompt_rail),
    ]
    
    rails_list = []
    for info in rail_infos:
        rail_instance = info.build_func(**info.params)
        if rail_instance is not None:
            setattr(self, info.attr_name, rail_instance)
            rails_list.append(rail_instance)
    return rails_list
```

#### 动态 Rail 注册

动态 Rail 根据运行时模式按需注册/去注册：

```python
async def _register_runtime_tools(self, session_id, mode="plan"):
    if mode == "plan":
        # 注册 TaskPlanningRail
        if self._task_planning_rail is None:
            self._task_planning_rail = self._build_task_planning_rail()
            await self._instance.register_rail(self._task_planning_rail)
    else:
        # 去注册 TaskPlanningRail
        if self._task_planning_rail is not None:
            await self._instance.unregister_rail(self._task_planning_rail)
            self._task_planning_rail = None
```

#### 添加自定义 Rail

在 `deep_agent/rails/` 目录下创建新的 Rail：

```python
# deep_agent/rails/my_custom_rail.py
from openjiuwen.deepagents.rails.base import DeepAgentRail

class MyCustomRail(DeepAgentRail):
    """自定义 Rail 示例"""
    
    async def before_model_call(self, context, agent_config):
        # 在模型调用前执行
        pass
    
    async def after_model_call(self, context, agent_config, result):
        # 在模型调用后执行
        pass
```

然后在 `interface_deep.py` 中注册：

```python
def _build_my_custom_rail(self) -> MyCustomRail | None:
    """Build MyCustomRail."""
    try:
        rail = MyCustomRail()
        logger.info("[JiuWenClawDeepAdapter] MyCustomRail create success")
        return rail
    except Exception as exc:
        logger.warning("[JiuWenClawDeepAdapter] MyCustomRail create failed: %s", exc)
        return None
```

### 4. 工具注册

工具（Tools）通过 `Runner.resource_mgr.add_tool()` 注册。

#### 工具类型

| 工具类型 | 注册时机 | 用途 |
|----------|----------|------|
| Memory Tools | Agent 初始化时 | 记忆/存储相关工具 |
| Web Tools | Agent 初始化时 | Web 浏览相关工具 |
| Todo Tools | 会话创建时 | 待办事项工具 |
| Browser Tools | 按需注册 | 浏览器自动化工具 |

#### 工具注册示例

```python
# 注册 Memory 工具
for tool in memory_tools:
    Runner.resource_mgr.add_tool(tool)
self._memory_tools_registered = True

# 注册 Web 工具
for tool_instance in web_tools:
    Runner.resource_mgr.add_tool(tool_instance)
self._web_tools_registered = True
```

#### 添加自定义工具

工具来源有两种方式：

1. **使用 openjiuwen 内置工具**：openjiuwen 包提供了丰富的内置工具，可直接导入使用，无需自行实现。常用的内置工具包括：
   - 文件操作工具
   - 网络请求工具
   - 代码执行工具
   - 搜索工具
   - 等等

2. **创建自定义工具**：在 `deep_agent/tools/` 目录下创建新的工具类，继承 `openjiuwen.core.foundation.tool.base.Tool` 基类，实现 `invoke()/stream()` 方法。

工具注册方式：在 `interface_deep.py` 中通过 `Runner.resource_mgr.add_tool()` 注册工具实例。

### 5. 配置管理

> ⚠️ **注意**：配置结构正在适配中，后续可能会有变化，请以实际代码为准。

JiuwenClaw 使用 YAML 配置文件管理运行时配置。

#### 配置文件位置

```
# Windows
C:\Users\<用户名>\.jiuwenclaw\config\config.yaml

# macOS/Linux
~/.jiuwenclaw/config/config.yaml
```

> 配置目录结构可能根据版本变化，具体请参考 `jiuwenclaw/utils.py` 中的 `prepare_workspace()` 函数。

#### 读取配置

使用 `get_config()` 函数读取配置：

```python
from jiuwenclaw.config import get_config

config_base = get_config()
```

#### 配置热更新

- 模型配置等热更新：在 `reload_agent_config()` 方法中实现
- 语言或模式热更新：在 `_register_runtime_tools()` 方法中实现

---

## 常见问题

### 1. ModuleNotFoundError: No module named 'openjiuwen'

**原因**: openjiuwen 未正确加载。

**解决方案**:

```bash
# 检查 openjiuwen 是否安装
uv pip list | grep openjiuwen

# 重新同步
uv sync --upgrade-package openjiuwen

# 或设置 PYTHONPATH
$env:PYTHONPATH = "D:\Code\Projects\DeepAgents\agent-core\openjiuwen"
```

### 2. 前端页面空白或无法访问

**原因**: 前端未构建。

**解决方案**:

```bash
cd jiuwenclaw/web
npm install
npm run build
cd ../..
```

### 3. 端口被占用

**原因**: 5173 或 8000 端口已被占用。

**解决方案**:

```bash
# Windows: 查找并结束占用端口的进程
netstat -ano | findstr :5173
taskkill /PID <PID> /F

# 或修改端口
uv run python -m jiuwenclaw.app_web --port 5174
```

### 4. 热更新不生效

**原因**: 使用了生产模式而非开发模式。

**解决方案**:

```bash
# 使用开发模式启动
uv run python -m jiuwenclaw.start_services dev
```

### 5. DeepAgent 初始化失败

**原因**: 模型配置不正确。

**解决方案**:

1. 检查 `~/.jiuwenclaw/config/.env` 文件
2. 确保配置了 `API_KEY` 和 `MODEL_PROVIDER`
3. 查看日志中的错误信息

```bash
# 初始化工作区（首次使用）
uv run jiuwenclaw-init

# 编辑配置
# Windows: %USERPROFILE%\.jiuwenclaw\.env
# macOS/Linux: ~/.jiuwenclaw/.env
```

### 6. 修改代码后未生效

**原因**: 未重启服务。

**解决方案**:

```bash
# 停止服务 (Ctrl+C)
# 重新启动
uv run python -m jiuwenclaw.start_services
```

---

## 开发流程总结

1. **下载代码** → `git clone https://gitcode.com/wangxiaolong100/jiuwenclaw.git`
2. **同步依赖** → `uv sync`
3. **同步 openjiuwen** → `uv sync --upgrade-package openjiuwen`
4. **构建前端** → `cd jiuwenclaw/web && npm install && npm run build`
5. **启动服务** → 命令行 `uv run python -m jiuwenclaw.start_services` 或 PyCharm 调试模式
6. **修改代码** → 编辑 `interface_deep.py`（在 `deep_agent/` 目录内）
7. **重启验证** → 使用自己习惯的模式重启并验证
8. **查看日志** → 关注控制台或 PyCharm Run/Debug 输出
9. **归档文档** → 将修改逻辑相关文档归档到 `jiuwenclaw/docs/JiuwenClaw集成DeepAgents SDK/`

---

## 相关文档

- [多 SDK 适配架构设计](./multi-sdk-adapter-design.md)
- [快速开始](../en/Quickstart.md)
- [桌面打包指南](../zh/打包exe指南.md)