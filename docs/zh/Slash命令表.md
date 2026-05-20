# Slash 命令速查表

本文档按**解析位置**拆分：`TUI 本地解析` 与 `Gateway / Agent 侧解析`。  
用于快速查阅当前行为，最终实现以代码为准。

---

## 一览：按解析侧区分

### TUI 本地解析（CLI 内置）

在终端 UI 本地执行，不走 Gateway 受控命令管线。

| 命令 | 说明 |
|---|---|
| `/clear` | 清屏 |
| `/color` | 调整 TUI 配色 |
| `/copy` | 复制上一条消息 |
| `/exit` | 退出 |
| `/help` | 查看可用命令 |
| `/theme` | 切换主题 |
| `/config` | 修改配置（当前为本地实现，后续计划统一到 Gateway） |
| `/context` | 查看上下文窗口占用与 Token 用量明细（见下文） |
| `/workspace` | 管理可信目录（见下文） |
| `/teamskills` | TeamSkills 管理（`init/validate/pack/info/search/list/install/uninstall/config/publish/delete`） |
| `/export` | 导出当前会话到文件或剪贴板（见下文） |
| `/status` | 查看 jiuwenswarm 运行状态概览、用量统计、配置编辑（见下文） |
| `/statusline` | 配置 TUI 底部状态栏的自定义命令（见下文） |
| `/permissions` | 管理工具权限（`allow`/`ask`/`deny`） |
| `/evolve` | Skill 自演进入口：触发 Skill 演进（见下文） |
| `/evolve_list` | 查看某个 Skill 的演进经验库（见下文） |
| `/evolve_simplify` | 整理、合并某个 Skill 的演进经验（见下文） |
| `/evolve_rebuild` | 基于归档与演进记录重建 `SKILL.md`（见下文） |
| `/sandbox` | 设置沙箱模式（见下文） |

> 说明：`/mode` 的受控切换逻辑以 Gateway 侧行为为主，详见下文「`/mode` 与 `/switch`」。

### Gateway / Agent 侧解析（受控通道）

由 Gateway 识别并转发到 AgentServer 等后端能力。

| 命令 | 说明 |
|---|---|
| `/plan` | 切换规划子模式 |
| `/resume` | 历史会话恢复（见下文） |
| `/new_session` | 新建会话（仅 IM 生效） |
| `/mode` | 模式切换（支持一级入口与直达写法） |
| `/switch` | 在当前模式族内切换二级模式 |
| `/skills` | 技能管理（列表、安装、卸载、市场源） |
| `/model` | 模型查看、新增、切换（见下文） |
| `/mcp` | MCP 服务管理（见下文） |
| `/diff` | 查看当前会话按轮次改动（见下文） |
| `/compact` | 压缩当前上下文（见下文） |
| `/init` | 项目初始化（见下文） |
| `/branch` | 从当前对话点创建分支会话（见下文） |
| `/rewind` | 回退对话到指定轮次之前（见下文） |
| `/memory` | 记忆管理（见下文） |
| `/cron` | 定时任务管理（见下文） |

---

## 重点命令说明

### `/workspace`（TUI 可信目录管理）

管理 AI 可访问的目录范围，用于文件读取、编辑、执行等操作。

#### 子命令

| 命令 | 说明 |
|---|---|
| `/workspace` 或 `/workspace get` | 查看系统默认工作空间与当前可信目录列表 |
| `/workspace add [path]` | 添加可信目录（默认为当前目录，路径不存在时提示错误） |
| `/workspace set <path>` | 重置可信目录为单个路径（已有可信目录时需确认） |
| `/workspace remove <path>` | 移除指定可信目录 |
| `/workspace clear` | 清空所有可信目录（仅使用默认工作空间） |

#### 概念说明

- **系统默认工作空间（workspace）**：固定路径 `~/.jiuwenswarm/agent/jiuwenswarm_workspace`，始终可用
- **可信目录（trusted_dirs）**：用户授权的可访问目录，由 TUI 管理，传递给后端 Agent

#### 控制逻辑

1. **启动确认**：TUI 启动时询问用户是否信任当前目录
   - 选择「信任」：将当前目录添加为可信目录
   - 选择「不信任」：仅使用默认工作空间

2. **会话级管理**：可信目录会持久化到./jiuwenswarm-tui/config.json文件里

3. **后端传递**：TUI 通过请求参数 `trusted_dirs` 传递可信目录列表，Agent 据此限制文件操作范围

4. **路径限制**：Agent 收到可信目录后，文件操作需限制在可信目录范围内；超出范围需向用户确认

5. **路径校验**：`add` 和 `set` 操作会校验路径是否存在，不存在则提示错误

#### 兼容别名

`/workspace_dir`、`/workspace-dir`

### `/mode` 与 `/switch`（受控通道）

- 一级入口映射：
  - `/mode agent` -> `agent.plan`
  - `/mode code` -> `code.normal`
  - `/mode team` -> `team`
- 直达写法：
  - `/mode agent.plan` -> `agent.plan`
  - `/mode agent.fast` -> `agent.fast`
  - `/mode code.plan` -> `code.plan`
  - `/mode code.normal` -> `code.normal`
- 二级切换：
  - agent 族：`/switch plan` <-> `agent.plan`，`/switch fast` <-> `agent.fast`
  - code 族：`/switch plan` <-> `code.plan`，`/switch normal` <-> `code.normal`
- 非法组合（如在 `code.*` 下执行 `/switch fast`）返回：`非法指令`。
- 备注：独立 `/team` 命令已移除，请统一使用 `/mode team`。

### `/resume`

- `/resume list`：列出历史会话。
- `/resume <conversation_id>`：恢复指定会话。

### `/model`（查看 / 新增 / 切换模型）

- 用法：
  - `/model` 或 `/model list`：列出可切换模型（含当前模型标记）；
  - `/model <name>`：切换到指定模型；
  - `/model add <name> key=value ...`：新增模型配置（如 `model=...`、`provider=...`、`api_base=...`、`api_key=...`）。
- 限制：`video` / `audio` / `vision` 不能通过 `/model <name>` 设置为默认聊天模型，需改用 `/config edit` 或 `/config set`。
- 配置写入行为：
  - 新增模型会写入 `config.yaml` 的 `models.defaults`（兼容旧结构），并触发 Agent 配置重载；
  - 切换模型会校验配置与环境变量占位符，更新 `MODEL_NAME` / `MODEL_PROVIDER` / `API_BASE` / `API_KEY`，并回写 `.env`。
- 安全展示：涉及 `api_key`、`token` 等敏感字段会掩码显示。

### `/diff`（会话改动回顾）

- 用法：`/diff`（无子命令）。
- 数据来源：TUI 通过 `command.diff` 请求 Agent 侧 diff 服务，按当前 `session_id` 返回 `turns`（每轮改动集合）。
- 展示规则：
  - 有改动：显示 `Found N turn(s) with file changes` 并附结构化 `turns`；
  - 无改动：显示 `No file changes in this session`。
- 作用范围：用于查看当前会话内未提交的按轮次改动轨迹，不替代 `git diff` 的完整版本控制视角。

### `/compact`（上下文压缩）

- 用法：`/compact`（无参数）。
- 功能：主动触发上下文压缩，清理对话历史但保留摘要信息在上下文中。
- 数据来源：TUI 通过 `command.compact` 请求 Agent 侧压缩服务。
- 返回结果：
  - `busy`：压缩正在进行中，请稍后重试；
  - `compressed`：压缩成功，显示压缩前后 token 数及节省比例；
  - `noop`：无需压缩，上下文已处于最优状态。

### `/context`（上下文窗口用量）

- 用法：`/context`（无参数、无子命令）。
- 功能：查看当前会话的上下文窗口占用情况与 Token 用量明细。
- 数据来源：TUI 通过 `command.context` 请求 Agent 侧上下文统计服务，携带当前 `mode`。
- 展示内容：
  - **概览面板**：上下文窗口占用百分比 + 进度条；`context_window`（已用/上限 tokens）、`occupancy`（占用率）、`messages`（消息数）；
  - **Token 拆分面板**：按 `system_prompt`、`messages`、`tools`、`total` 展示 Token 用量；
  - **DeepAgent 占用明细**（如有数据）：以键值列表展示 `context_occupancy` 各字段；
  - **DeepAgent 用量明细**（如有数据）：以键值列表展示 `deepagent_usage` 各字段。
- 阈值提示：当占用率 >= 90% 时，概览标题显示 `Context window 90% full — consider /compact` 提示。
- 错误处理：请求失败时显示 `context failed: <错误信息>`。

### `/init`（项目初始化）

- 用法：`/init`（无参数）。
- 功能：初始化项目 AI 协作配置，生成 `JIUWENCLAW.md` 和可选的 `JIUWENCLAW.local.md`。
- 适用范围：仅在 `code` 模式下运行。
- 流程：
  1. 选择范围：`团队共享`（JIUWENCLAW.md）、`个人私有`（JIUWENCLAW.local.md）或 `都要`。
  2. 检测已有配置：自动检测 `CLAUDE.md`、`.cursorrules`、`copilot-instructions.md` 等文件。
  3. 生成配置：根据选择生成项目配置文件。
- 自动模式切换：若当前处于 `code.plan` 模式，会自动切换到 `code.normal` 以便写入文件。

### `/mcp`（MCP 服务管理）

- 用法：
  - `/mcp list`：列出全部 MCP 服务（名称、transport、启用状态）；
  - `/mcp show [name]`：查看 MCP 配置；不带参数时展示当前启用项，带 `name` 时展示单个服务详情；
  - `/mcp add --name <name> --transport <stdio|sse> ...`：新增 MCP 服务；
  - `/mcp update --name <name> ...`：更新指定 MCP 服务配置（支持更新 transport / 参数 / 启用状态）；
  - `/mcp enable <name>`：启用指定 MCP 服务；
  - `/mcp disable <name>`：禁用指定 MCP 服务；
  - `/mcp remove <name>`：删除指定 MCP 服务。
- 传输参数：
  - `stdio`：需提供 `--command`，可选 `--args`、`--cwd`、`--env`；
  - `sse`：需提供 `--url`，可选 `--headers`、`--timeout_s`。
- 示例：
  - `/mcp list`
  - `/mcp show`
  - `/mcp show playwright`
  - `/mcp add --name playwright --transport stdio --command python --args "server.py --transport stdio"`
  - `/mcp update --name playwright --transport sse --url http://127.0.0.1:9000/sse --headers "Authorization=Bearer xxx"`
  - `/mcp add --name local-sse --transport sse --url http://127.0.0.1:9000/sse`
  - `/mcp disable playwright`
  - `/mcp remove local-sse`
- 配置与生效：
  - 变更会写入 `config.yaml` 的 `mcp.servers`；
  - 写入后会触发 Agent 配置重载，运行时按配置同步 MCP server 绑定。

### `/teamskills`（TeamSkills 管理）

- 用法：
  - `/teamskills init <name> [--path <parent_dir>] [--type <teamskills|skill>] [--force]`
  - `/teamskills validate <path> [--type <teamskills|skill>]`
  - `/teamskills pack <path> [--output <dir>]`
  - `/teamskills info <asset_id> --version <x.y.z> [--market-url <url>]`
  - `/teamskills search <query> [--type <skill|teamskills>] [--author <name>] [--asset-id <id>] [--asset-type <type>] [--publisher-id <id>] [--page <n>] [--page-size <n>] [--order-by <field>] [--desc <bool>] [--market-url <url>]`
  - `/teamskills list`
  - `/teamskills install <asset_id> [--version <x.y.z>] [--output <dir>] [--force] [--market-url <url>]`
  - `/teamskills uninstall <name>`
  - `/teamskills config [--market-url <url>] [--token <user_token>] [--system-token <system_token>]`
  - `/teamskills publish <path> --version <x.y.z> [--id <skill_id>] [--file <zip>] (--token <t>|--system-token <t>) [--market-url <url>] [--force] [--version-desc <text>]`
  - `/teamskills delete <skill_id> [--version <x.y.z|all>] (--token <t>|--system-token <t>) [--market-url <url>]`
- 行为：
  - `list` 仅列出当前本地可见已安装技能（并展示 `type`，区分 `skill` 与 `teamskills`）；
  - `search` 仅用于 TeamSkills Hub 市场搜索；
  - `config` 用于持久化 TeamSkills Hub 地址与 token（写入配置并尽量即时生效）；
  - `publish` 走 TeamSkills Hub 原生发布接口 `POST /api/v1/plugins`；
  - `delete` 走 TeamSkills Hub 原生删除接口 `DELETE /api/v1/plugins/{skill_id}/versions/{version}`；
  - `--token` 与 `--system-token` 互斥，且必须二选一。

### `/evolve*`（Skill 自演进）

这组命令由 TUI 本地注册并解析，随后通过普通聊天通道把 slash 文本转发给后端。实际演进逻辑在 Agent / Team 侧完成：

- Agent 模式：由 `SkillEvolutionRail` 处理，仅 `agent.plan` 可用。
- Team 模式：由 `TeamSkillEvolutionRail` 处理，用于团队技能演进。
- Code 模式与 `agent.fast` 不支持这组命令。

#### 子命令

| 命令 | 说明 |
|---|---|
| `/evolve <skill_name> [user_query]` | 为指定 Skill 触发演进。`agent.plan` 会扫描当前会话中的工具失败、用户纠错等信号；Team 模式必须提供 `user_query`。 |
| `/evolve_list <skill_name> [--sort score]` | 按分数查看某个 Skill 的演进经验，展示记录数、平均分、使用/反馈统计、section 与内容预览。 |
| `/evolve_simplify <skill_name> [user_intent]` | 生成经验库整理方案，用于合并重复经验、拆分过长经验或清理低价值经验；尾随文本会作为整理意图传入后端。 |
| `/evolve_rebuild <skill_name> [user_intent]` | 生成重建 `SKILL.md` 的 follow-up prompt，并继续作为一次普通 Agent / Team 任务执行。 |

#### 审批流程

- `/evolve` 和 `/evolve_simplify` 不会直接落盘覆盖内容；后端会推送确认问题，TUI 进入等待确认状态。
- 接收后，后端接受本次演进记录并写入/固化；拒绝后丢弃本次生成内容。
- Team 技能演进接收后会同步团队技能目录。
- 演进或审批未完成时，用户补充的新输入会先排队，等待演进完成后再继续发送。

#### 示例

```bash
/evolve pptx 修复导出失败时的错误处理
/evolve_list pptx --sort score
/evolve_simplify pptx 合并重复的导出失败经验
/evolve_rebuild pptx 强化 Troubleshooting 和 Examples
```

### `/branch`（分支会话）

- 用法：`/branch [name]`。
- 别名：`/fork`。
- 功能：以当前会话的当前状态为起点，创建一个分支会话，复制当前对话历史。
- 约束：
  - 当前会话正在处理中（`session is busy`）时拒绝执行；
  - 当前会话无对话记录时拒绝执行。
- 行为：
  1. 生成新 `session_id`，向后端发送 `session.fork` RPC（携带 `source_session_id`、`target_session_id` 与可选标题）。
  2. TUI 自动切换到新分支会话，清空当前 transcript 并恢复分支的历史记录。
  3. 提示用户已在新分支，并告知可用 `/resume <原会话ID>` 返回原会话。
- 示例：
  - `/branch` — 创建无标题分支
  - `/branch fix-login-bug` — 创建名为 `fix-login-bug` 的分支

### `/rewind`（回退对话）

- 用法：`/rewind [turn_number]`。
- 别名：`/checkpoint`。
- 功能：将当前会话回退到指定轮次之前，支持仅回退对话、仅恢复文件、或两者同时恢复。
- 约束：
  - 当前会话正在处理中（`session is busy`）时拒绝执行；
  - 无对话轮次时拒绝执行。
- 交互流程：
  1. 无参数时，先展示当前会话所有轮次列表（含时间、文件变更统计），供用户选择目标轮次。
  2. 选择轮次后，展示恢复选项：
     - **Restore conversation and code** — 截断对话并恢复文件到该轮次之前的状态；
     - **Restore conversation only** — 仅截断对话，文件保持不变；
     - **Restore code only** — 仅恢复文件，对话保持不变（仅当目标轮次有文件变更时显示）；
     - **Cancel** — 取消操作。
  3. 根据选择调用对应后端 RPC：
     - `both` → `session.rewind_and_restore`
     - `conversation` → `session.rewind`
     - `code` → `session.restore_files`
- 回退后：TUI 清空 transcript 并重新加载历史；若回退内容包含用户输入，会自动填入输入框。
- 局限：回退不影响通过 bash 命令或手动编辑的文件。
- 示例：
  - `/rewind` — 交互式选择轮次并确认恢复方式
  - `/rewind 2` — 直接回退到第 2 轮之前

### `/memory`（记忆管理）

- 别名：`/mem`。
- 功能：查看与管理记忆系统状态、记忆文件、开关配置及目录路径。
- 子命令：

| 命令 | 说明 |
|---|---|
| `/memory` 或 `/memory edit` | 交互式选择并编辑记忆文件（无参数时列出可选文件） |
| `/memory list` | 列出所有记忆文件（含大小、行数、修改时间） |
| `/memory edit <path>` | 打开指定记忆文件进行编辑（通过 `$EDITOR`） |
| `/memory status` | 显示记忆系统详细状态 |
| `/memory toggle [key]` | 切换记忆系统开关（无参数时列出可切换项） |
| `/memory open` | 显示记忆系统各目录路径 |

- `status` 展示内容：
  - 当前模式、存储引擎、启用状态、Proactive 状态、Forbidden Filter 状态；
  - 索引状态（FTS5、Vector、Cache）、文件数、分块数；
  - Project Memory、Coding Memory、Auto Memory、External Memory 的统计。
- `toggle` 可切换项：
  - `memory_enabled` — 记忆总开关；
  - `memory_proactive` — 主动记忆开关；
  - `memory_forbidden_enabled` — Forbidden Filter 开关。
  - 切换后若需要重启会话生效，会给出提示。
- 示例：
  - `/memory` — 交互式编辑记忆文件
  - `/memory list` — 列出记忆文件
  - `/memory edit memory/MEMORY.md` — 编辑指定记忆文件
  - `/memory status` — 查看详细状态
  - `/memory toggle memory_enabled` — 切换记忆总开关
  - `/memory open` — 查看记忆目录路径

### `/cron`（定时任务管理）

管理定时任务（Cron Job），通过 RPC 调用后端 `CronController`，与 Web 端共用同一套后端逻辑和数据存储。

- 别名：`/crontab`
- 子命令：

| 命令 | 说明 |
|---|---|
| `/cron` 或 `/cron list` | 列出所有定时任务 |
| `/cron add name=<名称> cron_expr=<表达式> description=<描述> [其他参数]` | 新增定时任务 |
| `/cron update <job_id> key=value ...` | 更新指定任务的部分字段 |
| `/cron delete <job_id>` | 删除指定任务 |
| `/cron toggle <job_id> <on或off>` | 启用或禁用指定任务 |
| `/cron run <job_id>` | 立即执行指定任务 |
| `/cron preview <job_id>` | 预览任务接下来几次执行时间 |

- `add` 参数：

| 参数 | 必填 | 说明 |
|---|---|---|
| `name` | 是 | 任务名称 |
| `cron_expr` | 是 | Cron 表达式，支持两种格式：5 字段（分 时 日 月 周）或 7 字段 Quartz（秒 分 时 日 月 周 年）。5 字段会自动转换为 7 字段（补 second=0, year=*）。示例：每天 9 点 = `0 9 * * *`（5 字段）或 `0 0 9 * * ? *`（7 字段） |
| `description` | 是 | 任务描述，即 Agent 执行时收到的输入指令 |
| `targets` | 否 | 推送渠道，默认 `tui`；可选：`tui`、`web`、`feishu`、`whatsapp`、`wecom`、`xiaoyi`、`wechat` 或 `feishu_enterprise:<app_id>` |
| `timezone` | 否 | IANA 时区，默认 `Asia/Shanghai` |
| `mode` | 否 | 执行模式：`agent`（默认，适用于简单提醒类任务）或 `plan`（较复杂的推理任务，让Agent先规划步骤再执行） |
| `wake_offset_seconds` | 否 | 提前唤醒秒数，默认 300 |
| `delete_after_run` | 否 | 执行一次后自动删除，默认 false |

- `add` 示例：
  - `/cron add name=每分钟测试 cron_expr="0 * * * *" description="告诉我现在几点了" targets=tui`
  - `/cron add name=晨报 cron_expr="0 9 * * *" description="生成今日晨报摘要" targets=tui mode=plan`
  - `/cron add name=提醒 cron_expr="0 30 17 29 4 ? 2026" description="别忘了开会" targets=tui delete_after_run=true`
  - `/cron add name=每周一报 cron_expr="0 9 * * 1" description="生成本周周报" targets=web`

- `update` 用法：只需传入要修改的字段，如 `/cron update <id> name=新名称 enabled=false`
- `list` 显示内容：序号、完整 job ID、名称、cron 表达式、启用状态、描述摘要
- `preview` 显示内容：每次执行计划的唤醒时间和推送时间

### `/skills`（技能管理）

管理技能的完整生命周期：列表查看、安装、卸载以及市场源管理。

#### 子命令

| 命令 | 说明 |
|---|---|
| `/skills` 或 `/skills list` | 列出技能（分两组：已安装 / 可安装） |
| `/skills install <skill>` 或 `/skills install <skill@marketplace>` 或 `/skills install <path_or_url>` | 安装技能：内置技能可直接用名称，市场技能需用 `<名称>@<市场源>` 格式，本地路径或远程 URL 可直接传入路径 |
| `/skills uninstall <name>` | 按名称卸载技能 |
| `/skills marketplace` 或 `/skills marketplace list` | 列出市场源（名称、URL、启用状态、最后更新时间） |
| `/skills marketplace add <name> <url>` | 添加新的市场源 |
| `/skills marketplace remove <name>` | 移除市场源（同时清理缓存） |
| `/skills marketplace toggle <name> <on或off>` | 启用或禁用市场源（`on`/`true`/`1` 为启用，其余为禁用） |
| `/skills use <skill_name>, <query>` | 使用指定技能执行查询 |

#### 概念说明

- **技能（Skill）**：可从市场源、内置目录或本地路径安装的扩展能力，为 Agent 提供额外功能。
- **内置技能（Builtin skill）**：随软件打包发布的预置技能，安装时可直接使用技能名称（如 `/skills install advanced-daily-report`），无需指定市场源。
- **市场源（Marketplace source）**：托管可用技能的远程仓库（通常为 Git URL），每个源包含名称、URL 和启用/禁用状态。
- **规格标识（Spec）**：从市场源安装时使用的标识格式 `<技能名>@<市场源名>`；内置技能安装时可不带 `@`，自动识别为 `@builtin`。
- **本地安装（Local install）**：通过 `/skills install <path>` 将本地目录（需包含 `SKILL.md`）或远程归档 URL 安装为自定义技能；路径/URL 会自动识别并走本地导入流程。
- **安装位置（Install location）**：技能安装后的存储目录（`~/.jiuwenswarm/agent/jiuwenswarm_workspace/skills/`）。
- **来源标签（Source tag）**：列表中每项技能标注来源，`[builtin]` 表示内置、`[local]` 表示本地导入、`[project]` 或市场源名表示其他来源。

#### 列表分组展示

`/skills list` 返回的技能列表分为两组：

1. **已安装（Installed）**：已存在于用户 skills 目录的技能，可直接使用。
2. **可安装（Available to install）**：内置但尚未安装的技能，以及市场源中可安装的技能，需先执行 `/skills install` 才能使用。

#### IM 与 TUI 的差异

两端最终都会请求 `skills.list`，但触发方式和展示形态不同。

| 端 | 触发方式 | 行为 |
|---|---|---|
| IM（飞书等受控通道） | 整行精确匹配 `/skills list`（会先做空白规范化） | Gateway 拦截控制消息并请求 `skills.list`，结果以 IM 通知/卡片等形式展示；单独输入 `/skills` 不走该控制路径。 |
| TUI（CLI 内置） | 输入 `/skills` | 本地执行内置命令并调用 `skills.list`，在会话内以分组列表视图展示（标题 `Installed Skills` 与 `Available Skills`）；无数据时提示 `No installed skills`。 |

对于其他子命令（`/skills install`、`/skills uninstall`、`/skills marketplace add/remove/toggle`、`/skills use`），Gateway **不会拦截**——在 IM 侧输入时会被当作普通聊天消息发送给 Agent。这些子命令仅在 TUI（CLI 内置）和 Web UI 路径下可用，通过 RPC 直连 AgentServer。

#### 备注

- **超时**：`install`、`uninstall`、`marketplace toggle` 请求在 TUI 侧有 120 秒超时；其余子命令无显式超时设置。
- **内置技能自动识别**：使用 `/skills install <skill>` 安装时，若技能名称不带 `@`，系统会自动检查是否为内置技能并重定向到内置安装流程；若不是内置技能则返回格式提示。
- **路径/URL 自动识别**：使用 `/skills install <path>` 安装时，若参数为本地路径（如 `/path/to/skill`、`C:\skill`）或远程 URL（`https://...`），系统自动走本地导入流程。
- **缓存清理**：`marketplace remove` 发送 `{ name, remove_cache: true }` 以同时清理该源的本地缓存。
- **自动刷新**：`marketplace add`、`marketplace remove`、`marketplace toggle` 在操作成功后会自动重新列出市场源。
- **离线处理**：`/skills use` 会检查连接状态；离线时显示 `offline: waiting for reconnect before sending /skills use request`。

#### 示例

- `/skills` — 列出技能（分组：已安装 / 可安装）
- `/skills list` — 列出技能（显式子命令）
- `/skills install advanced-daily-report` — 安装内置技能（裸名自动识别）
- `/skills install advanced-daily-report@builtin` — 安装内置技能（显式指定）
- `/skills install my-skill@marketplace` — 从市场源安装技能
- `/skills install /path/to/my-skill` — 从本地目录安装技能
- `/skills install https://example.com/skill.zip` — 从远程 URL 安装技能
- `/skills uninstall my-skill` — 卸载技能
- `/skills marketplace list` — 列出市场源
- `/skills marketplace add community https://github.com/user/skills-repo` — 添加名为"community"的市场源
- `/skills marketplace remove community` — 移除"community"市场源
- `/skills marketplace toggle community on` — 启用"community"市场源
- `/skills marketplace toggle community off` — 禁用"community"市场源
- `/skills use my-skill, Code and execute a Hello World program.` — 使用技能执行查询

### `/export`（导出会话）

将当前对话导出到文件或剪贴板。

#### 子命令

| 命令 | 说明 |
|---|---|
| `/export` | 将整段对话复制到剪贴板；剪贴板不可用时提示指定文件名 |
| `/export <filename>` | 将对话写入工作空间目录下的 `filename.txt`；文件名不含 `.txt` 后缀时自动追加 |

#### 输出格式

导出的文本按时间戳与角色前缀逐条渲染：

- `[User] <时间戳>` — 用户输入
- `[Assistant] <时间戳>` — 助手回复
- `[Thinking] <时间戳>` — 内部推理过程
- `[Tools] <时间戳>` — 工具调用，含名称、摘要、截断结果（最多 500 字符）
- `[System] / [Error] / [Info] <时间戳>` — 系统消息
- `[Diff] <时间戳>` — 按轮次的文件变更摘要

#### Tab 补全

输入 `/export ` 后按 Tab，自动生成文件名建议：

- `<时间戳>-<净化后的首条提示>.txt` — 取首条用户消息（截断 50 字符，净化特殊字符）
- `conversation-<时间戳>.txt` — 通用时间戳名

时间戳格式：`YYYY-MM-DD-HHmmss`。

#### 行为细节

- **剪贴板回退**：未指定文件名且剪贴板不可用时，提示用户指定文件名导出到文件。
- **文件名规范化**：任何扩展名都会被替换为 `.txt`；例如 `/export my-chat.json` 变为 `my-chat.txt`。
- **写入位置**：文件保存到 `ctx.getWorkspaceDir()`（回退为 `process.cwd()`）。

#### 示例

- `/export` — 复制对话到剪贴板
- `/export my-chat` — 保存到工作空间下的 `my-chat.txt`
- `/export 2026-05-09-debug-session.txt` — 使用显式时间戳文件名保存

### `/sandbox`（沙箱模式管理）

进入/离开 jiuwenbox 沙箱模式，并调整其运行时策略。通过 `command.sandbox` 与 agent-server 交互。

#### 子命令

| 命令 | 说明 |
|---|---|
| `/sandbox` 或 `/sandbox status` | 显示当前 runtime（`enabled`、`excluded_commands`、`files.allow_write`、`files.deny_write`） |
| `/sandbox enable` | 进入沙箱模式（需要时启动 jiuwenbox，并重建 agent） |
| `/sandbox disable` | 离开沙箱模式（重建 agent；jiuwenbox 只在 jiuwenswarm 启动时才停掉） |
| `/sandbox exclude add <pattern>` | 加入一条 shell glob，命中后在本地而非沙箱内执行 |
| `/sandbox exclude remove <pattern>` | 移除一条 pattern |
| `/sandbox exclude list` | 列出当前 `excluded_commands` |
| `/sandbox files allow <path> [perm]` | 允许沙箱内写 `<path>` |
| `/sandbox files deny <path>` | 拒绝沙箱内写 `<path>` |
| `/sandbox files remove <path>` | 从 user-configured allow & deny 中移除该 path |
| `/sandbox files list` | 列出生效的 `allow_write` / `deny_write` |
| `/sandbox help` | 打印用法 |

#### 概念说明

- **平台限制**：`/sandbox` 仅支持 Linux 平台（jiuwenbox 依赖 bwrap / Landlock / Linux namespace 等内核能力）。 在 Windows / macOS 上运行的 agent-server 收到任何 `/sandbox` 子命令都会返回 `SANDBOX_BAD_REQUEST` 错误；如果 TUI 在 Mac/Windows 上、agent-server 在 Linux 主机上，是支持的（看 agent-server 所在主机的平台）。
- **生效写入策略**：状态面板里的 `files.allow_write` / `files.deny_write` 是 auto-managed 与 user-configured 合并后的视图。auto-managed 条目由服务端自动注入（intrinsic 文件 `AGENT.md`、`HEARTBEAT.md`、`IDENTITY.md`、`SOUL.md`、`USER.md`，`memory/daily_memory/` 目录，以及按 mode 决定的 `project_dir` 与 `config/config.yaml`），不能通过 `/sandbox files remove` 移除。
- **preserve_file_sharing_mode**：由 jiuwenswarm 配置决定，不通过 `/sandbox` 切换。仅支持 `mount`：intrinsic 文件与 `project_dir` 通过 bind mount 注入沙箱，`project_dir/config/config.yaml` 会显式加进 `deny_write`；yaml 里写入其它值会被服务端拒绝。
- **excluded_commands**：按完整命令字符串匹配（不是只看 `argv[0]`），命中后该次调用穿透到本地，相当于把对应命令的副作用授权给本地环境。
- **add / remove 的去重与冲突**：`exclude add` 在已存在同名 pattern 时报错；`exclude remove` 在不存在该 pattern 时报错。`files allow|deny` 在同一 bucket 已有同 path 时报错，在对侧 bucket（allow vs deny）已登记同 path 时也报错，需要先 `files remove` 再 add；`files remove` 在用户配置里找不到该 path 时报错。
- **enable / disable**：会触发 agent 重建，响应里会列出 `rebuilt_modes`（典型 `agent.*` / `code.*`）和 jiuwenbox 端点。

#### 示例

- `/sandbox enable` — 打开沙箱模式
- `/sandbox status` — 查看 runtime 与生效路径
- `/sandbox files allow ./tmp/ 0777` — 允许沙箱以 0777 写入 `./tmp/`
- `/sandbox exclude add "git *"` — 让 `git` 命令穿透到本地执行，不进沙箱

### `/status`（查看运行状态）

显示 jiuwenswarm 运行状态概览、用量统计或配置编辑界面。

#### 子命令

| 命令 | 说明 |
|---|---|
| `/status` | 显示完整状态概览（版本、会话、模型、连接、MCP 服务、配置来源） |
| `/status overview` | 与 `/status` 相同——显式概览子命令 |
| `/status usage` | 显示当前会话的 token 用量（输入、输出、总量、按模型拆分） |
| `/status config` | 进入交互式配置编辑器（与 `/config edit` 相同） |

#### 概览显示分区

运行 `/status` 时展示四个键值面板：

1. **核心信息**：版本号、会话 ID、会话名称（或提示 `/rename` 添加）、当前工作目录、当前模式
2. **模型与 API**：模型名称、提供商、API 基地址、连接状态
3. **MCP 服务**：每个服务的名称、传输类型、启用/禁用状态
4. **配置来源**：配置文件路径与所有设置来源路径

#### 用量显示

`/status usage` 显示当前会话的 token 消耗：

- 总输入 token、输出 token、总 token
- 按模型拆分：模型名称、token 总量、输入/输出细分

#### 交互模式

若 TUI 提供交互式 StatusView（`ctx.enterStatusView`），`/status` 会打开带标签页的完整状态 UI。子命令参数选择初始标签页：

- `/status` → 打开概览标签页
- `/status usage` → 打开用量标签页
- `/status config` → 打开配置标签页

若 StatusView 不可用，回退为内联键值展示。

#### 数据来源

- 概览数据：通过 `command.status` RPC 请求 AgentServer
- 用量数据：通过 `ctx.getUsageSummary()` 从本地会话追踪获取
- 配置数据：通过 `config.get` RPC 请求 AgentServer

#### 示例

- `/status` — 显示完整概览
- `/status overview` — 显示概览（显式）
- `/status usage` — 显示 token 用量
- `/status config` — 打开配置编辑器

### `/statusline`（TUI 状态栏配置）

配置 TUI 底部状态栏，通过自定义 shell 命令动态显示会话信息（模式、模型、工作目录等），仿照 Claude Code 的 `/statusline` 实现。

#### 子命令

| 命令 | 说明 |
|---|---|
| `/statusline` 或 `/statusline get` | 查看当前状态栏配置 |
| `/statusline set <shell-command>` | 设置状态栏命令（命令输出将显示在 TUI 底部） |
| `/statusline clear` | 清除状态栏配置（底部栏将不再显示） |
| `/statusline help` | 显示状态栏 JSON 输入字段参考 |

#### 概念说明

- **状态栏（StatusLine）**：TUI 底部的一行文字区域，实时显示用户自定义的动态信息。配置了自定义状态栏后，内置状态栏会自动隐藏，避免信息冗余。
- **Shell 命令**：用户配置的 shell 命令每 2 秒自动执行一次，其 stdout 输出渲染为状态栏文字。
- **JSON 输入**：每次执行时，系统将当前会话信息以 JSON 格式传入命令，用户可在命令中用 `jq` 等工具解析。POSIX（Linux/macOS）通过 stdin 管道传入；Windows 上因 MSYS2 管道继承限制，系统自动将 JSON 写入临时文件，并将命令中的 `$(cat)` 替换为 `$(cat "文件路径")`，用户无需修改命令格式。
- **前置依赖**：需要 `jq`（https://stedolan.github.io/jq/）用于解析 JSON；Windows 用户还需将 Git Bash 的 `usr\bin` 目录加入系统 PATH（如 `E:\Git\usr\bin`）。

#### JSON 输入字段

命令执行时接收如下 JSON 数据：

| 字段 | 说明 |
|---|---|
| `session_id` | 当前会话 ID |
| `session_name` | 会话标题（通过 `/rename` 设置） |
| `cwd` | 当前工作目录 |
| `mode` | 当前模式（`agent.plan` / `agent.fast` / `code.plan` / `code.normal` / `team`） |
| `model` | 当前模型名称 |
| `provider` | 模型提供商 |
| `version` | jiuwenswarm 版本号 |
| `connection` | 连接状态（`idle` / `connecting` / `connected` / `reconnecting` / `auth_failed`） |
| `theme` | 当前主题名 |
| `accent_color` | 当前强调色名 |
| `transcript_mode` | 对话显示模式（`compact` / `detailed`） |
| `transcript_fold_mode` | 折叠模式（`none` / `tools` / `thinking` / `all`） |
| `is_processing` | 是否正在处理（`true` / `false`） |
| `is_paused` | 是否暂停（`true` / `false`） |
| `is_interrupted` | 是否中断（`true` / `false`） |
| `cancellable_work` | 是否有可取消的工作（`true` / `false`） |
| `streaming_state` | 流式传输状态（`idle` / `streaming` / `tool_call` / `tool_result`） |
| `last_error` | 最近错误信息或 `null` |
| `evolution_status` | 演化状态（`idle` / `running`） |
| `active_subtask_count` | 活跃子任务数 |
| `todo_count` | 待办事项数 |
| `usage.total_input_tokens` | 会话总输入 token |
| `usage.total_output_tokens` | 会话总输出 token |
| `usage.total_tokens` | 会话总 token |

#### 命令编写模板

推荐使用以下模板编写命令。`input=$(cat)` 将 JSON 读入变量，后续用 `echo "$input" | jq -r .字段` 提取各字段。`// "默认值"` 是 jq 的备选语法，字段为空时使用默认值。

**通用公式**：

```
/statusline set 'input=$(cat); 字段1=$(echo "$input" | jq -r '.字段1 // "默认值"'); 字段2=$(echo "$input" | jq -r '.字段2 // "默认值"'); echo "格式化字符串"'
```

**推荐通用命令**（显示模式、模型、token、连接状态）：

```
/statusline set 'input=$(cat); mode=$(echo "$input" | jq -r '.mode // "?"'); model=$(echo "$input" | jq -r '.model // "?"'); tokens=$(echo "$input" | jq -r '.usage.total_tokens // 0'); conn=$(echo "$input" | jq -r '.connection // "?"'); echo "$mode | $model | tokens:$tokens | $conn"'
```

**各字段提取速查**：

| 要显示的字段 | jq 写法 |
|---|---|
| 会话名 | `jq -r '.session_name // ""'` |
| 工作目录 | `jq -r '.cwd // "?"'` |
| 模式 | `jq -r '.mode // "?"'` |
| 模型名 | `jq -r '.model // "?"'` |
| 提供商 | `jq -r '.provider // "?"'` |
| 版本号 | `jq -r '.version // "?"'` |
| 连接状态 | `jq -r '.connection // "?"'` |
| 是否在处理 | `jq -r '.is_processing // false'` |
| 是否暂停 | `jq -r '.is_paused // false'` |
| 流式状态 | `jq -r '.streaming_state // "idle"'` |
| 最近错误 | `jq -r '.last_error // ""'` |
| 演化状态 | `jq -r '.evolution_status // "idle"'` |
| 子任务数 | `jq -r '.active_subtask_count // 0'` |
| 待办数 | `jq -r '.todo_count // 0'` |
| 总输入 token | `jq -r '.usage.total_input_tokens // 0'` |
| 总输出 token | `jq -r '.usage.total_output_tokens // 0'` |
| 总 token | `jq -r '.usage.total_tokens // 0'` |

#### 更多示例

- `/statusline` — 查看当前配置
- `/statusline set 'input=$(cat); model=$(echo "$input" | jq -r .model); echo "$model"'` — 只显示模型名
- `/statusline set 'input=$(cat); proc=$(echo "$input" | jq -r .is_processing); model=$(echo "$input" | jq -r .model); echo "$proc | $model"'` — 显示是否在处理和模型名
- `/statusline set 'input=$(cat); err=$(echo "$input" | jq -r .last_error); if [ "$err" != "null" ] && [ "$err" != "" ]; then echo "error: $err"; else echo "ok"; fi'` — 有错误时显示错误信息，无错误时显示 ok
- `/statusline clear` — 清除状态栏配置
- `/statusline help` — 查看 JSON 输入字段参考

#### 行为细节

- **轮询频率**：每 2 秒自动执行一次配置的命令。
- **超时保护**：单次执行超时 3 秒后自动终止，不影响后续轮询。
- **输出限制**：命令输出超过 10KB 时截断；显示宽度自动适配 TUI 终端宽度。
- **故障静默**：命令执行失败时不显示错误，保持上一次成功输出或隐藏状态栏。
- **持久化**：配置保存在 `~/.jiuwenswarm-tui/config.json` 的 `statusLine` 字段，重启 TUI 后自动恢复。
- **别名**：`/sl`
- **Windows 适配**：系统自动将 `$(cat)` 替换为读取临时文件，用户命令格式不变；需确保 Git Bash 的 `usr\bin` 在系统 PATH 中。

#### 配置文件结构

```json
{
  "statusLine": {
    "type": "command",
    "command": "input=$(cat); mode=$(echo \"$input\" | jq -r '.mode // \"?\"'); model=$(echo \"$input\" | jq -r '.model // \"?\"'); tokens=$(echo \"$input\" | jq -r '.usage.total_tokens // 0'); echo \"$mode | $model | tokens:$tokens\"",
    "padding": 0
  }
}
```

---

## 待开发

| 命令             | 说明      |
|----------------|---------|
| `/btw`         | 提问      |
| `/export`      | 导出相关文件  |
| `/permissions` | 权限管理    |
