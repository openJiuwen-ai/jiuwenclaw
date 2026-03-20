# mcp_exec_command -> FileSystemRail 替换

## 背景

JiuwenClaw 早期通过 `jiuwenclaw/agentserver/tools/command_tools.py` 中的 `mcp_exec_command` 工具，为 Agent 提供统一的命令执行入口。该工具具备以下特点：

- 单工具入口，负责执行命令行命令
- 支持 `shell_type=auto|cmd|powershell|bash|sh`
- 支持 `background=True` 非阻塞启动后台进程
- 将 `workdir` 强约束在 `workspace` 目录内
- 内置危险命令黑名单拦截
- 对 `stdout/stderr` 做长度裁剪，避免输出过长

在 DeepAgents 集成后，JiuwenClaw 改为在 Agent 初始化阶段挂载 `FileSystemRail`，由 Rail 统一注册文件系统、Shell 和代码执行工具，替代原先单一的 `mcp_exec_command`。

## 修改目标

将原有的单点命令执行工具替换为 DeepAgents 原生工具体系，使 Agent 具备更细粒度的本地操作能力，包括：

- `read_file`
- `write_file`
- `edit_file`
- `glob`
- `list_files`
- `grep`
- `bash`
- `code`

本次替换的目标不是对 `mcp_exec_command` 做 1:1 兼容复刻，而是将“命令执行”扩展为“文件系统 + Shell + 代码执行”的工具集合。

## 涉及文件

| 文件 | 修改类型 |
|------|----------|
| `jiuwenclaw_wxl/jiuwenclaw/agentserver/interface.py` | 接入 `FileSystemRail`，并调整 rail 热更新策略 |
| `jiuwenclaw_wxl/openjiuwen/deepagents/rails/filesystem_rail.py` | Rail 注册文件系统 / shell / code 工具 |
| `jiuwenclaw_wxl/openjiuwen/deepagents/rails/skill_rail.py` | `skill_mode` 所在 rail |
| `jiuwenclaw_wxl/openjiuwen/deepagents/tools/shell.py` | `bash` 工具 |
| `jiuwenclaw_wxl/openjiuwen/deepagents/tools/code.py` | `code` 工具 |
| `jiuwenclaw_wxl/openjiuwen/deepagents/tools/filesystem.py` | 文件系统工具集合 |
| `jiuwenclaw_wxl/jiuwenclaw/agentserver/tools/command_tools.py` | 历史工具，已被替换 |

## 具体变更

### 1. interface.py

在构建 DeepAgent rails 时，新增 `FileSystemRail` 初始化逻辑，同时保留 `SkillRail`。

关键变化：

- 当 `sys_operation` 可用时，创建并挂载 `FileSystemRail`
- 不再向 Agent 注入历史的 `mcp_exec_command`
- Agent 的本地操作能力转由 Rail 统一提供
- `_build_agent_rails()` 不再因为 `sys_operation` 为空而整体提前返回，只跳过依赖 `sys_operation` 的 rail

### 2. FileSystemRail

`FileSystemRail` 在 `init()` 时注册以下工具：

```python
read_file
write_file
edit_file
glob
list_files
grep
bash
code
```

关键变化：

- 能力从“单命令工具”变为“多工具协作”
- 文件读取、编辑、搜索不再需要绕一层 shell 命令
- 代码执行从 shell 侧拆出，变为独立的 `code` 工具

### 3. bash / code 工具

#### `bash`

`bash` 工具底层调用 `SysOperation.shell().execute_cmd()`，提供：

- `command`
- `timeout`
- 结构化返回 `stdout/stderr/exit_code`

#### `code`

`code` 工具底层调用 `SysOperation.code().execute_code()`，提供：

- `code`
- `language=python|javascript`
- `timeout`
- 结构化返回 `stdout/stderr/exit_code`

### 4. rail 热更新策略

本次又对热更新逻辑做了调整。

#### 初始化阶段

首次创建 Agent 时，仍通过 `_build_agent_rails(config)` 构建 rail 实例：

- `FileSystemRail`
- `SkillRail`

并将它们缓存到实例属性中：

- `self._filesystem_rail`
- `self._skill_rail`

#### reload 阶段

`reload_agent_config()` 不再重新调用 `_build_agent_rails(config)`。

当前策略是：

- 直接复用当前已经保存的 rail 实例
- 仅同步更新 `self._skill_rail.skill_mode`
- 然后通过 `_get_current_agent_rails()` 把现有 rail 重新传给新的 `DeepAgent`

关键变化：

- 热更新时不重新构建 rail
- `FileSystemRail` 不因普通配置变更被重新创建
- `SkillRail` 也不重新实例化，只同步 `skill_mode`

## 能力对照

| 能力项 | 旧 `mcp_exec_command` | 新 `FileSystemRail` |
|------|----------------------|--------------------|
| 命令执行 | 支持 | 通过 `bash` 支持 |
| 代码执行 | 不独立 | 通过 `code` 独立支持 |
| 文件读写 | 需借助命令 | 原生支持 |
| grep / glob / list | 需借助命令 | 原生支持 |
| `shell_type` 选择 | 支持 | 不支持 |
| `background` 后台执行 | 支持 | 不支持 |
| 危险命令黑名单 | 支持 | 不直接支持 |
| 工作目录限制到 `workspace` | 显式支持 | 依赖 `SysOperation.work_dir` 配置 |
| 输出裁剪 | 支持 | 不直接支持 |

## 架构说明

### 旧路径

```text
Agent
  -> mcp_exec_command
      -> 子进程执行命令
```

特点：

- 所有本地操作都收敛到一个命令工具
- 能力边界清晰，但扩展能力弱
- prompt 更依赖模型自行拼接 shell 命令

### 新路径

```text
Agent
  -> FileSystemRail
      -> read_file / write_file / edit_file / glob / list_files / grep / bash / code
```

特点：

- 工具职责更细
- 文件系统操作优先走专用工具
- 只有确实需要命令行时才调用 `bash`
- 代码执行与命令执行分离

### 当前热更新路径

```text
create_instance
  -> _build_agent_rails(config)
  -> 缓存 self._filesystem_rail / self._skill_rail

reload_agent_config
  -> 不再 _build_agent_rails(config)
  -> 仅更新 self._skill_rail.skill_mode
  -> 复用当前 rail 实例
```

设计意图：

- 热配置更新本身不应无意义重建所有 rail
- `skill_mode` 是当前确认会影响 rail 行为的配置项
- 其他 rail 在配置未改变其功能时应尽量复用

## 已知遗漏

### 1. 后台执行能力缺失

旧 `mcp_exec_command` 支持 `background=True`，可用于启动服务、守护进程或长时间运行任务后立即返回。

替换后：

- `bash` 仅提供阻塞式命令执行
- `FileSystemRail` 中没有等价的后台启动工具

影响：

- 启动本地 server、watcher、dev 进程等场景会退化
- Agent 可能只能同步等待，直至超时或任务结束

### 2. `shell_type` 兼容层缺失

旧工具会根据平台和命令内容，在 `cmd`、`powershell`、`bash`、`sh` 之间选择执行方式，`auto` 模式还会识别 PowerShell 风格命令。

替换后：

- `bash` 没有 `shell_type` 参数
- Windows 下 PowerShell 风格命令的兼容体验可能下降

影响：

- 以前可执行的 PowerShell 片段，迁移后不一定稳定
- prompt 或工具调用如果隐含依赖 `shell_type=auto`，可能出现回归

### 3. 危险命令黑名单缺失

旧工具显式拦截了多类危险命令，例如：

- `rm -rf`
- `rd /s /q`
- `shutdown`
- `format`
- `Remove-Item -Recurse -Force`

替换后：

- 新方案主要依赖 `shell_allowlist`
- 如果 allowlist 为空，则不会自动做上述黑名单拦截

影响：

- 安全模型发生变化
- 若上层未额外配置限制，风险边界可能比旧方案更宽

### 4. 工作目录边界收口不如旧实现显式

旧工具强制要求 `workdir` 位于 `workspace` 根目录内，越界直接报错。

替换后：

- `ShellOperation` 会参考 `work_dir`
- 但路径收敛依赖 `SysOperation` 配置，而不是工具层固定写死

影响：

- 若配置不严，运行目录边界可能弱于旧工具
- 这是“配置约束”替代“工具内建约束”的变化

### 5. 输出裁剪能力缺失

旧工具支持 `max_output_chars`，会裁剪超长输出。

替换后：

- `bash` 和 `code` 返回完整 `stdout/stderr`

影响：

- 构建日志、测试日志、批量 grep 结果可能过长
- 容易增加上下文压力和前端展示压力

### 6. sandbox 模式尚未实现

当前 `shell` 和 `code` 的 sandbox operation 仍为 `NotImplementedError`。

影响：

- 当前方案默认依赖 LOCAL 模式
- 若未来切换到 sandbox，`FileSystemRail` 中的 `bash` / `code` 可能在运行时报错

### 7. 热更新只同步了 `skill_mode`

当前 `reload_agent_config()` 在 rail 层只同步 `SkillRail.skill_mode`，并复用现有 rail 实例。

影响：

- 这符合“配置修改本身不影响其他 rail 功能”的当前设计判断
- 但如果后续出现新的 rail 配置项，或 `SkillRail` 内部新增依赖其他配置字段，则需要重新评估这套热更新策略
- 换言之，当前实现是针对现阶段配置影响面的最小更新方案，不是通用 rail reconfigure 框架

## 结论

`FileSystemRail` 替换 `mcp_exec_command` 后，整体能力更强，架构也更符合 DeepAgents 的工具化设计，但这次替换属于“能力重组”，不是“完全等价迁移”。

收益主要体现在：

- 文件系统操作不再依赖 shell 命令拼接
- `code` 成为独立工具，能力边界更清晰
- Rail 方式更容易与 DeepAgents 生命周期集成
- 热更新阶段减少了 rail 的无意义重建

当前需要重点关注的回归点是：

1. 后台执行
2. `shell_type` 兼容
3. 危险命令拦截
4. 工作目录边界
5. 长输出裁剪
6. sandbox 可用性
7. 后续新增 rail 配置项时的热更新扩展性

## 验证建议

1. 验证 Agent 是否能正常使用 `read_file` / `edit_file` / `grep` / `bash` / `code`
2. 验证历史依赖 `mcp_exec_command` 的 prompt 或样例是否已全部清理
3. 在 Windows 环境下验证 PowerShell 风格命令是否仍可稳定执行
4. 验证启动本地服务类任务是否存在因缺少后台执行而失败的情况
5. 验证 `SysOperation.work_dir` 与 `shell_allowlist` 配置是否满足原有安全边界
6. 验证大输出命令是否会对上下文或前端展示造成影响
7. 验证 reload 配置后，`FileSystemRail` 不会重复构建，`SkillRail.skill_mode` 能正确生效
