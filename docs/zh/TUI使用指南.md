# JiuwenSwarm TUI 使用指南

> 本文档面向 **JiuwenSwarm 终端界面（`jiuwenswarm-tui` / `jiuwenswarm-cli`）** 用户，结构与 [Claude Code CLI 参考](https://code.claude.com/docs/zh-CN/cli-reference) 类似：先列 **CLI 启动参数**，再列 **Slash 命令**，接着是 **工具参考** 与 **交互模式**，并对 **Code 模式** 做重点说明。  
> 行为以仓库代码为准：`jiuwenswarm/cli/src/index.ts`、`jiuwenswarm/cli/src/core/commands/registry.ts` 及各 `builtins/*.ts`。

---

## 启动前提

- TUI 通过 **WebSocket** 连接本机 Gateway 的 TUI 端点（默认 `ws://127.0.0.1:19001/tui`）。请先按 [快速开始(TUI)](Quickstart_tui.md) 启动后端服务，再打开 TUI。
- TUI 需要 **交互式 TTY**；在管道或非 TTY 环境下会报错退出。

---

## CLI 参考

### 启动方式

| 方式 | 说明 |
|------|------|
| `jiuwenswarm-tui` | 通过 `jiuwenswarm-tui` PyPI 包启动时，由包装器拉起对应平台的二进制（见 `packages/jiuwenswarm-tui`）。 |
| `jiuwenswarm-cli` | 源码/开发路径下，在 `jiuwenswarm/cli` 执行 `npm run dev` 或 `npm run start` 后使用 `jiuwenswarm-cli`（见 `package.json` 的 `bin`）。 |

以下 **命令行标志** 由 `jiuwenswarm/cli/src/index.ts` 中的 `parseArgs` 定义：

| 标志 | 说明 | 默认值 | 示例 |
|------|------|--------|------|
| `--url <url>` | Gateway 的 CLI WebSocket 地址 | `ws://127.0.0.1:19001/tui` | `jiuwenswarm-cli --url ws://192.168.1.10:19001/tui` |
| `--session <id>` | 启动时恢复指定会话 ID | 无 | `jiuwenswarm-cli --session abc-123` |
| `--token <token>` | 鉴权令牌（若 Gateway 需要） | 空字符串 | `jiuwenswarm-cli --token YOUR_TOKEN` |
| `-h`, `--help` | 打印帮助并退出 | - | `jiuwenswarm-cli -h` |

### 启动后界面

- **欢迎区**：ASCII 标题、版本、当前 Provider / Model / Mode；窄终端下为精简布局（`jiuwenswarm/cli/src/ui/welcome.ts`）。
- **连接提示**：未连上后端时会提示检查 Gateway 或 `--url`；鉴权失败时提示检查 `--token`。
- **ripgrep 提示**：若本机未安装 `rg`，会提示安装以优化文件搜索。

---

## Slash 命令参考

### 一览：按解析位置区分

| 类型 | 说明 |
|------|------|
| **TUI 本地** | 在 `CommandService` 中注册，输入后由 TUI 直接执行，不当作普通聊天发给 Agent。 |
| **经 TUI 调后端 RPC** | 仍为本地解析的命令，但内部通过 `ctx.request(...)` 或 `sendMessage` 调用 AgentServer / Gateway 能力。 |

当前 **已注册** 的顶层命令来自 `createBuiltinCommands()`（`registry.ts`），按名称排序如下表。

> **与文档 [Slash命令表.md](Slash命令表.md) 的差异**：`jiuwenswarm/cli/src/core/commands/builtins/` 下另有 **`switch.ts`（`/switch`）**、**`cancel.ts`（`/cancel`）**、**`new.ts`（`/new` 独立建会话）**、**`sessions.ts`（会话列表 RPC）** 等实现，但 **当前 `registry.ts` 未注册**这些顶层命令，输入后会得到 `Unknown command`。中断任务请优先使用 **`Ctrl+C`**（第一次中断，连按两次退出）。Gateway 侧受控指令仍以 `jiuwenswarm/gateway/slash_command.py` 与 Slash命令表为准。

### 命令总表

| 命令 | 别名 | 用途 | 示例 | 适用模式 |
|------|------|------|------|----------|
| `/help` | - | 列出已注册 Slash 命令 | `/help` | 全部 |
| `/hotkey` | - | 显示快捷键与 `/help` 提示 | `/hotkey` | 全部 |
| `/exit` | `/quit` | 退出 TUI | `/exit` | 全部 |
| `/clear` | `/reset`, `/new` | 新建会话 ID、清空当前 transcript（忙时拒绝） | `/clear` | 全部 |
| `/copy` | - | 复制最近第 N 条助手回复到剪贴板 | `/copy` 或 `/copy 2` | 全部 |
| `/theme` | - | 切换深/浅色主题 | `/theme dark` | 全部 |
| `/color` | - | 设置提示条强调色 | `/color blue` | 全部 |
| `/compact` | - | 压缩上下文，保留摘要 | `/compact` | 全部 |
| `/config` | `/settings`, `/setting` | 查看/设置后端配置 | `/config`、`/config get`、`/config set key value` | 全部 |
| `/context` | - | 查看上下文窗口占用与 Token 用量明细 | `/context` | 全部 |
| `/diff` | - | 查看本会话按轮次的文件改动 | `/diff` | 全部 |
| `/evolve` | - | 触发技能演进 | `/evolve myskill 修正错误处理` | `agent.plan` / `team`（见下） |
| `/evolve_list` | - | 列出某技能的演进条目 | `/evolve_list myskill --sort score` | `agent.plan` / `team` |
| `/evolve_rebuild` | - | 从归档与演进记录重建 SKILL.md | `/evolve_rebuild myskill 强化错误处理` | `agent.plan` / `team` |
| `/evolve_simplify` | - | 整理、合并某技能的演进经验 | `/evolve_simplify myskill 合并重复经验` | `agent.plan` / `team` |
| `/init` | - | 在 **Code 模式** 下初始化 `JIUWENSWARM.md` / `JIUWENSWARM.local.md` | `/init` | **仅 `code.*`** |
| `/mcp` | - | 管理 MCP 服务 | `/mcp list`、`/mcp add ...` | 全部 |
| `/mode` | - | 切换或查看模式 | `/mode`、`/mode code`、`/mode team` | 全部 |
| `/permissions` | - | 设置 `permissions.tools` 中单工具的 allow/ask/deny | `/permissions ask write_file` | 全部 |
| `/plan` | - | 进入 Agent 规划模式，或发送规划请求 | `/plan`、`/plan open`、`/plan 迁移步骤` | 非 `team` |
| `/rename` | - | 查看/重命名/清空当前会话标题 | `/rename`、`/rename 标题`、`/rename clear` | 全部 |
| `/resume` | `/continue` | 列出或恢复历史会话；无参 `/resume` 与 `/continue` 在 TUI 中可打开交互列表（见下） | `/resume list`、`/resume <id>` | 全部 |
| `/skills` | - | 技能与市场源管理 | `/skills`、`/skills install ...` | 全部 |
| `/teamskills` | - | TeamSkills Hub（初始化、校验、打包、搜索、安装等） | `/teamskills list` | 全部 |
| `/model` | - | 查看/新增/切换模型 | `/model`、`/model add name k=v` | 全部 |
| `/workspace` | `/workspace_dir`, `/workspace-dir` | 管理文件操作可信目录 | `/workspace add .` | 全部 |
| `/export` | - | 导出当前会话到文件或剪贴板 | `/export`、`/export my-chat` | 全部 |
| `/status` | - | 查看运行状态概览、用量、配置 | `/status`、`/status usage` | 全部 |
| `/branch` | `/fork` | 从当前对话点创建分支会话 | `/branch fix-login-bug` | 全部 |
| `/rewind` | `/checkpoint` | 回退对话到指定轮次之前 | `/rewind 2` | 全部 |
| `/memory` | `/mem` | 记忆管理（状态、文件、开关、目录） | `/memory status` | 全部 |
| `/sandbox` | - | 进出沙箱模式 / 管理 excluded_commands / files | `/sandbox enable`、`/sandbox status`、`/sandbox files allow ./tmp/` | 全部 |

#### `/resume` 与 `/continue` 在 TUI 中的特殊行为

- 输入 **`/resume`** 或 **`/continue`** 且 **无其它参数** 时，TUI 会打开 **会话列表** 供选择恢复，而不走纯文本 `session.list` 展示（`app-screen.ts`）。
- 带参数时仍走命令实现：`/resume list`、`/resume <conversation_id>` 等。

---

### 重点命令说明

#### `/mode` 与子模式切换

- **`/mode`**（`mode.ts`）  
  - 无参数：显示当前模式。  
  - TUI 当前接受：`agent`、`plan`、`agent.plan`、`agent.fast`、`code`、`code.normal`、`code.team`、`team`、`team.normal`。
  - 实际映射：`agent` / `plan` → `agent.plan`；`code` → `code.normal`；`team.normal` → `team`；其它直达值保持不变。
  - 切换时会尝试调用 `mode.set` RPC，同时更新本地 UI 状态；如果后端不支持 `mode.set`，TUI 仍会在后续发送消息时通过当前模式传递。
  - 从 `team` / `code.team` 离开 Team 族且当前有 Team 任务运行时，TUI 会先弹出确认，确认后发送 `chat.interrupt` 再切换。
- **与 Gateway 受控通道的差异**：受控通道只接受 `/mode agent|code|team|agent.plan|agent.fast|code.normal|code.team`；`/mode plan`、`/mode team.normal` 是 TUI 本地命令能力，不属于 Gateway slash 白名单。
- **同族子模式**：可用 **`/mode agent.fast`**、**`/mode code.normal`**、**`/mode code.team`** 等直达；**`/switch plan|fast|normal|team`** 在 `switch.ts` 中实现，但 **默认 TUI 注册表未注册**。

#### `/workspace`（可信目录）

- 系统默认工作空间：`~/.jiuwenswarm/agent/jiuwenswarm_workspace`（始终可用）。
- `add`：默认路径为当前工作目录；成功后会 `command.add_dir` 同步到服务端并 `remember: true`。
- `set`：重置为单个可信目录；若已有列表会二次确认。
- 详见 [Slash命令表.md](Slash命令表.md) 的 `/workspace` 小节。

#### `/init`（仅 Code 模式）

- 必须在 `code.*` 下执行；否则提示先 `/mode code`。
- 需能解析工作目录：优先 `trustedDirs[0]`，否则 `process.cwd()`；无法解析时提示先 `/workspace set <path>`。
- 交互选择范围：团队共享 `JIUWENSWARM.md`、个人 `JIUWENSWARM.local.md` 或两者；然后向后端发送编排提示（`logAsUser: false`）。
- 详见 [Slash命令表.md](Slash命令表.md) 与源码 `init.ts`。

#### `/diff` 与 `/compact`

- **`/diff`**：调用 `command.diff`，展示本会话内有文件变更的轮次（非完整 `git diff` 替代）。
- **`/compact`**：调用 `command.compact`，返回 `busy` | `compressed` | `noop`；成功时展示 token 节省比例（`compact.ts`）。

#### `/context`（上下文窗口用量）

- **`/context`**（`context.ts`）
  - 无参数，无子命令。
  - 调用 `command.context` RPC，携带当前 `mode`，获取上下文窗口占用与 Token 用量明细。
  - 展示分为多个面板：
    - **概览面板**：进度条 + 占用百分比；`context_window`（已用/上限 tokens）、`occupancy`（占用率）、`messages`（消息数）。
    - **Token 拆分面板**：按 `system_prompt`、`messages`、`tools`、`total` 展示。
    - **DeepAgent 占用明细**（如有数据）：`context_occupancy` 键值列表。
    - **DeepAgent 用量明细**（如有数据）：`deepagent_usage` 键值列表。
  - 阈值提示：占用率 >= 90% 时，提示 `Context window 90% full — consider /compact`。
  - 错误处理：请求失败时显示 `context failed: <错误信息>`。
  - 与 `/status usage` 的区别：`/context` 侧重**实时上下文窗口占用**，含进度条和阈值告警；`/status usage` 侧重**会话累计用量统计**与**按模型拆分**。

#### `/config`

- 子命令：`get`、`set`、`list`、`edit`、`reset`；无参数时展示分组概览。
- `set` 会校验 schema 中的 key；`toggle` 类型可省略 value 表示翻转。
- 敏感字段展示会掩码。

#### `/model`

- `/model add <name> key=value ...` 新增模型配置。
- `video` / `audio` / `vision` 不能作为默认聊天模型切换，需用 `/config`。
- 详见 [Slash命令表.md](Slash命令表.md)。

#### `/mcp`

- 子命令：`list`、`show`、`add`、`update`、`enable`、`disable`、`remove`（`delete` 同 `remove`）。
- `stdio`：需 `--command`，可选 `--args`、`--cwd`、`--env`。
- `sse`：需 `--url`，可选 `--headers`、`--timeout_s`。
- 详见 [Slash命令表.md](Slash命令表.md) 与 `mcp.ts`。

#### `/skills` 与 `/teamskills`

- **`/skills`**：默认等价 `list`；子命令含 `install`、`uninstall`、`marketplace`、`use` 等；部分长操作有 120s 超时（见 `skills.ts`）。
- **`/teamskills`**：无子命令时打印用法提示；支持 `init`、`validate`、`pack`、`info`、`search`、`list`、`install`、`uninstall`、`config`、`publish`、`delete`（见 `teamskills.ts` 与 Slash命令表）。

#### `/evolve*`（Skill 自演进）

这组命令在 TUI 本地注册（`evolve.ts`），但业务逻辑不在前端执行：TUI 只做必要参数校验，然后通过 `sendMessage(...)` 把原始 slash 文本发给后端。后端在 Agent / Team 流程中拦截并调用 SkillEvolutionRail / TeamSkillEvolutionRail。

| 命令 | 用途 | 行为要点 |
|------|------|----------|
| `/evolve <skill_name> [user_query]` | 为单个 Skill 生成演进记录 | `agent.plan` 下会先扫描当前会话中的工具失败和用户纠错信号；若没有信号且未给 `user_query`，返回“未发现明确演进信号”。Team 模式必须提供 `<user_query>`。 |
| `/evolve_list <skill_name> [--sort score]` | 查看某 Skill 的经验库 | 展示记录数、平均分、使用/反馈统计、目标 section 与内容预览；当前实现按 score 获取记录。 |
| `/evolve_simplify <skill_name> [user_intent]` | 智能整理经验库 | 生成可审批的整理方案，用于合并、拆分或清理演进经验。尾随文本会作为整理意图传给后端，不是独立 CLI flag。 |
| `/evolve_rebuild <skill_name> [user_intent]` | 重建 SKILL.md | 由后端生成 follow-up prompt，并继续作为普通 Agent / Team 任务执行，用归档历史与演进记录重建 Skill 文档。 |

适用条件：

- `agent.plan`：用于单 Agent Skill 自演进；其它 Agent / Code 子模式不处理这组命令。
- `team`：使用团队技能演进 rail；`/evolve <skill_name> <user_query>`、`/evolve_list`、`/evolve_simplify`、`/evolve_rebuild` 可用。
- 无参数 `/evolve` 仅在 `agent.plan` 下返回待处理演进记录摘要；Team 模式会要求补充 Skill 名称和演进意图。

审批与状态：

- `/evolve` 和 `/evolve_simplify` 生成变更后不会静默写入，会推送 `chat.ask_user_question`，TUI 进入确认态，用户确认后才由后端接受或丢弃记录。
- Team 技能演进确认后会同步团队技能；拒绝则丢弃本次生成内容。
- 后端推送 `chat.evolution_status` 时，TUI 会把演进状态标记为 running / idle；演进或审批未完成时补充输入会先排队，等待演进完成后再发送。

更多机制说明见 [Skill 自演进](Skill自演进.md)。

#### `/plan`

- 在 `team` 模式下不可用。
- 无参数：进入 `agent.plan`。
- `open`：仅提示已进入规划模式。
- 其它文本：在切换到 plan 后作为规划请求发送。

#### `/permissions`

- 用法：`/permissions <allow|ask|deny> <tool_name>`
- 调用 `permissions.tools.update`，写入配置中的 per-tool 策略。

#### `/export`

- 无参数：复制当前对话到剪贴板；剪贴板不可用时提示指定文件名。
- `/export <filename>`：将对话写入工作空间目录下的 `.txt` 文件（自动追加 `.txt` 后缀）。
- 输出格式为纯文本，每条消息按 `[User]`、`[Assistant]`、`[Thinking]`、`[Tools]` 等角色前缀与时间戳逐条渲染。
- 支持 Tab 补全：自动生成 `<时间戳>-<首条提示>.txt` 和 `conversation-<时间戳>.txt` 建议。
- 详见 [Slash命令表.md](Slash命令表.md) 的 `/export` 小节。

#### `/status`

- `/status`：显示完整状态概览（版本、会话、模型、连接、MCP 服务、配置来源）。
- `/status usage`：显示当前会话 token 用量统计（含按模型拆分）。
- `/status config`：进入交互式配置编辑器。
- 若 TUI 提供 StatusView，会打开带标签页的交互界面；否则回退为内联键值展示。
- 详见 [Slash命令表.md](Slash命令表.md) 的 `/status` 小节。

#### `/branch`（分支会话）

- 别名：`/fork`。
- 约束：当前会话忙时或无对话记录时拒绝执行。
- 行为：生成新 `session_id` 并调用 `session.fork`；TUI 自动切换到新分支会话，清空 transcript 并恢复分支历史。提示用户可用 `/resume <原会话ID>` 返回原会话。
- 示例：`/branch`、`/branch fix-login-bug`。

#### `/rewind`（回退对话）

- 别名：`/checkpoint`。
- 约束：当前会话忙时或无对话轮次时拒绝执行。
- 交互流程：
  1. 无参数时先展示轮次列表（含时间、文件变更统计），供用户选择目标轮次。
  2. 选择后展示恢复选项：
     - **Restore conversation and code** — 截断对话并恢复文件；
     - **Restore conversation only** — 仅截断对话；
     - **Restore code only** — 仅恢复文件（仅当目标轮次有文件变更时显示）；
     - **Cancel** — 取消。
  3. 对应调用 `session.rewind_and_restore`、`session.rewind` 或 `session.restore_files`。
- 回退后：TUI 清空 transcript 并重新加载历史；若回退内容包含用户输入，会自动填入输入框。
- 局限：回退不影响通过 bash 命令或手动编辑的文件。
- 示例：`/rewind`（交互式）、`/rewind 2`（直接回退到第 2 轮前）。

#### `/memory`（记忆管理）

- 别名：`/mem`。
- 子命令：
  - `list` — 列出所有记忆文件（大小、行数、修改时间）。
  - `edit [path]` — 编辑记忆文件；无参数时交互式选择。
  - `status` — 显示记忆系统详细状态（引擎、索引、Project/Coding/Auto/External Memory 统计）。
  - `toggle [key]` — 切换记忆开关；无参数时列出可切换项（`memory_enabled`、`memory_proactive`、`memory_forbidden_enabled`）。
  - `open` — 显示记忆系统各目录路径。
- 示例：`/memory status`、`/memory toggle memory_enabled`、`/memory edit memory/MEMORY.md`。

#### `/sandbox`（沙箱模式管理）

- 平台限制：仅在 Linux 上的 agent-server 可用；Windows / macOS 的 agent-server 收到 `/sandbox` 命令会直接返回错误。TUI 本身跑在哪个平台不影响——只要 agent-server 在 Linux 上即可。
- 子命令：`status`（默认）/ `enable` / `disable` / `exclude add|remove|list` / `files allow|deny|remove|list` / `help`。
- `enable` 行为：必要时启动 jiuwenbox（已有 endpoint 则复用），随后触发 agent 重建；响应面板会显示 `rebuilt_modes` 与 jiuwenbox 端点。
- `disable` 行为：重建 agent；只有 jiuwenswarm 自己启动的 jiuwenbox 才会被停掉，外部 endpoint 会显式保留。
- 状态面板字段：
  - `enabled` — 当前开关。
  - `excluded_commands` — 命中后穿透到本地执行的 shell glob 列表。
  - `files.allow_write` / `files.deny_write` — 生效（auto-managed ∪ user-configured，去重）的写入策略。
- 自动配置路径：文件 `AGENT.md`、`HEARTBEAT.md`、`IDENTITY.md`、`SOUL.md`、`USER.md`，目录 `memory/daily_memory/`，以及 `project_dir`（allow_write）与 `project_dir/config/config.yaml`（deny_write）。`preserve_file_sharing_mode` 仅支持 `mount`。
- `excluded_commands` 的匹配：按完整命令字符串匹配，不仅看 `argv[0]`；写 glob 时要把参数也覆盖进去（例如 `"git *"` 而不是 `git`）。本质等同于沙箱穿透口，不要对 `rm -rf` / `curl` 这类高风险命令使用。
- add / remove 严格校验：`exclude add` 已存在 pattern、`exclude remove` 不存在 pattern 都会报错；`files allow|deny` 在同 bucket 已有 path 或对侧 bucket 已有 path（allow/deny 冲突）会报错，先 `files remove` 再 add；`files remove` 没匹配到也会报错。避免"看起来执行了实际什么也没改"。
- 示例：`/sandbox enable`、`/sandbox status`、`/sandbox files allow ./tmp/ 0777`、`/sandbox exclude add "git *"`。

#### `/clear` 与忙状态

- 若 `session is busy`（正在处理），`/clear` 会拒绝执行，需先中断任务（**`Ctrl+C`** 第一次中断；默认构建无 `/cancel` 命令）。

---

## 工具参考（Tools）

本节描述 **Code 模式** 下与 Agent 模式差异最大的部分；Agent 模式通常为全量工具集，以服务端配置为准（见 [模式系统.md](模式系统.md)）。

### Code 模式配置中的动态工具（`modes.code.tools`）

默认示例（以你环境 `config.yaml` 为准）：

| 工具 | 说明 |
|------|------|
| `web_free_search` | 免费网页检索 |
| `web_fetch_webpage` | 抓取网页正文 |
| `web_paid_search` | 付费检索（若已配置） |
| `user_todos` | 用户待办相关能力 |

文档约定：`coding_memory_*` 与 `send_file_to_user` 由运行时自动注册，**不必**写在 `modes.code.tools` 列表中（见 [模式系统.md](模式系统.md)）。

### Code 模式专属：编码记忆工具

| 工具 | 参数要点 | 说明 |
|------|-----------|------|
| `coding_memory_read` | `query` | 按关键词检索编码上下文 |
| `coding_memory_write` | `content`, `section` | 写入片段或经验，`section` 如 `architecture`、`bugfix` |
| `coding_memory_edit` | `memory_id`, `content`, `section?` | 更新已有条目 |

存储与更多说明见 [编码记忆.md](编码记忆.md)。

### Code 模式安全 Rails（`modes.code.rails`）

| Rail | 作用 |
|------|------|
| `FileSystemRail` | 文件系统访问约束 |
| `SkillUseRail` | 技能调用约束 |
| `LspRail` | LSP 相关约束 |

### 调整工具权限

- 在 TUI 中使用 **`/permissions`** 将某一工具设为 `allow` / `ask` / `deny`，写入 `permissions.tools`。
- 全局安全模型见 [工具权限与安全防护.md](工具权限与安全防护.md)。

---

## 交互模式（Interactive Mode）

### 快捷键

| 按键 | 行为 |
|------|------|
| `Ctrl+C` | **第一次**：中断当前任务（`chat.interrupt`）；**1 秒内第二次**：退出 TUI |
| `Ctrl+L` | 重绘整屏 |
| `Ctrl+T` | 显示/隐藏 Todos 面板 |
| `Ctrl+G` | 显示/隐藏 Team 面板 |
| `Ctrl+O` | 在 transcript **紧凑 / 详细** 视图间切换 |
| `Esc` | 无待处理问题时，若存在可取消的工作，则发送取消 |

权限类问答中，`y` / `n` 可快速选择允许/拒绝类选项（`app-screen.ts`）。

### 输入、附件与 `@` 引用

- **文件/图片附件**：在输入中使用 `@路径` 或 `@"含空格路径"`；解析规则见 `attachments.ts`。
- **图片扩展名**：`.png`、`.jpg`、`.jpeg`、`.gif`、`.webp`。
- **常见作为附件的文件扩展名**：含源码、文档、配置、压缩包、Office 文档等一大类扩展名（见 `SUPPORTED_FILE_EXTENSIONS`）；未在列表中的扩展名可能不会生成附件。
- **粘贴路径**：支持引号路径、`file://`、Windows 盘符路径等，由 `extractFilePathsFromPaste` 解析。

### 命令与路径补全

- 输入 **`/`** 后可通过补全选择已注册命令（`CommandService.getSuggestions`）。
- 部分命令注册了 `completion`（如 `/mode`、`/theme`、`/color`、`/config`）。

### 启动时「是否信任当前文件夹」

- 首次启动可能询问是否信任当前目录；选择信任会将该目录加入可信列表（与 `/workspace` 逻辑一致，见 `app-screen.ts`）。

---

## 模式速览

| 模式 | 代号 | 摘要 |
|------|------|------|
| Agent（规划） | `agent.plan` | 全工具 + 主动记忆，偏规划 |
| Agent（快速） | `agent.fast` | 全工具 + 被动记忆，偏快 |
| Code（常规） | `code.normal` | 编码 + 编码记忆，偏执行 |
| Team | `team` | 多 Agent 协作 |

切换示例与 `config.yaml` 中 `modes` 段说明见 [模式系统.md](模式系统.md)。

---

## Code 模式专题（重点）

### 为什么需要 Code 模式

- **更贴合仓库工作流**：在工具白名单 + 安全 Rails 下读写项目文件，并用 **编码记忆** 持久化代码上下文。
- **与 Agent 模式的区别**：Code 模式 **不是**「全工具 + 无额外 Rails」；默认启用 `FileSystemRail`、`SkillUseRail`、`LspRail`，工具集为配置项 + 自动注册的编码记忆等（见上文工具参考）。

### 推荐工作流

1. **进入 Code 模式**  
   ` /mode code` → 默认 `code.normal`。
2. **划定可信目录**  
   ` /workspace add .` 或 ` /workspace set /path/to/repo`，确保 Agent 文件工具允许访问你的工程树。
3. **（可选）项目级说明文件**  
   在 `code.*` 下执行 ` /init`，按提示选择团队/个人/两者，生成 `JIUWENSWARM.md` 等。
4. **日常编码对话**  
   直接描述需求；模型会通过白名单工具与编码记忆完成任务。
5. **查看本轮改动**  
   ` /diff` 查看会话内按轮次文件变更轨迹。
6. **上下文过长**  
   ` /compact` 压缩历史，保留摘要。
7. **收紧危险工具**  
   例如 ` /permissions ask write_file`，在写入前要求确认。

### 编码记忆使用提示

- 长任务中可让助手「把本次接口约定写入编码记忆，`section=architecture`」等，便于后续 `coding_memory_read` 检索。
- 与对话记忆、经验记忆的关系见 [编码记忆.md](编码记忆.md) 中的对照表。

### 与 `/skills`、`/mcp` 的配合

- Code 模式下仍可通过 **`/skills`**、**`/mcp`** 管理扩展能力；具体工具是否出现在当前会话取决于服务端模式配置与权限。
- MCP 详细配置见 [MCP配置.md](MCP配置.md)。

### 常见问题

| 现象 | 处理 |
|------|------|
| `/init` 提示需要 Code 模式 | 先执行 `/mode code` |
| 无法写文件或总被要求确认 | 确认处于 `code.normal` |
| 文件路径不在允许范围 | 使用 `/workspace add` 将仓库根或子目录加入可信列表 |
| 连接失败 | 检查 Gateway 是否监听、`--url` 是否正确、防火墙 |
| 未安装 `rg` | 安装 ripgrep 以改善搜索体验（欢迎屏提示） |

---

## 故障排查

| 问题 | 建议 |
|------|------|
| `jiuwenswarm-cli requires an interactive TTY` | 在真实终端中运行，勿用管道代替 |
| `Authentication failed` | 检查 `--token` |
| `Backend unavailable` | 启动 `jiuwenswarm-gateway` 或修正 `--url` |
| `Unknown command: /xxx` | 该构建未注册该命令；用 `/help` 查看当前可用列表，或对照本文「命令总表」与 `registry.ts` |
| `/copy` 失败 | 当前系统无剪贴板集成；Linux 需常见剪贴板工具 |

---

## 另请参阅

- [快速开始(TUI)](Quickstart_tui.md)
- [模式系统](模式系统.md)
- [Slash命令表](Slash命令表.md)
- [编码记忆](编码记忆.md)
- [工具权限与安全防护](工具权限与安全防护.md)
- [MCP配置](MCP配置.md)
- [配置信息](配置信息.md)
- [Claude Code CLI 参考（结构参考）](https://code.claude.com/docs/zh-CN/cli-reference)
