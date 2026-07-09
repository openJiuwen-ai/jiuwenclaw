# JiuwenAvatar 架构设计文档

> 九问数字分身 — 让 AI 成为你的分身，替你完成重复性工作

本文件描述 **当前最新架构**：双进程运行时、Persona/Avatar 身份体系、统一编码引擎抽象（jiuwen / claude-code / codex），以及"创建分身 → 指派任务"的完整流程。

---

## 一、产品定位

**JiuwenAvatar（九问数字分身）** 是一个数字分身平台：用户基于预置 **Persona（身份模板）** 创建与自己角色对应的数字分身（Committer、Developer、Tester…），分身在 **Trigger（触发器）** 驱动下自主执行任务（**Mission**），并产出结构化 **执行报告**。

### 核心场景：Committer 分身

1. 定时 / Webhook 触发：检查 GitCode 上分配给我的待检视 PR
2. 按绑定的 Skill（`dev-reviewer`）执行代码检视——可由 **编码引擎**（原生 / Claude Code / Codex）实际跑检视
3. 编码引擎返回检视结论后，分身用 `gitcode-repo` Skill 提交检视意见 / 建 PR
4. 生成检视报告并推送到配置渠道

---

## 二、核心概念

| 概念 | 说明 |
|------|------|
| **Avatar（数字分身）** | 用户创建的角色化实例 = Persona 实例化 + 绑定 Skill + 选定编码引擎 + 关联 Trigger |
| **Persona（身份模板）** | 预置模板（committer / developer / tester），定义系统提示、默认技能集、可选编码引擎、触发器模板、报告模板 |
| **Skill（技能）** | 分身的能力单元（AIDLC 流水线脚本），随分身创建自动安装到用户工作区 |
| **Coding Engine（编码引擎）** | 分身执行"编码/检视"类重活的后端，可插拔：`jiuwen-coding`（原生）/ `claude-code` / `codex` |
| **Trigger（触发器）** | 驱动分身执行的机制：Cron / Heartbeat / Webhook / Event |
| **Mission（任务）** | 分身执行的一次完整任务（pending → running → completed/failed） |
| **Mission Report（执行报告）** | 任务完成后基于 Persona 报告模板生成的结构化报告 |

```
Persona（身份模板）
  ├── 默认 Skill 集合
  ├── coding_capable + 可选 coding_engines + 默认引擎
  ├── 系统提示 / 触发器模板 / 报告模板
  └── （只读，随仓库分发）

Avatar（数字分身）= Persona 实例化
  ├── 继承 Persona 的 Skill（创建时自动安装）
  ├── 选定 coding_engine（jiuwen / claude-code / codex）
  ├── 配置 Trigger
  └── 产生 Mission → Mission Report
```

**关键设计决策**：Skill / 编码引擎都是"身份的实现细节"，用户心智是"我要一个 Committer 分身"，而不是单独管理技能或后端。

---

## 三、系统架构：双进程分离

```
┌──────────────────────────────────────────────────────────────────────┐
│                          JiuwenAvatar 平台                            │
│                                                                       │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │                      Web 前端（React SPA）                       │  │
│  │  chat | avatars | triggers | reports | channels | config        │  │
│  │  AvatarCenter（分身中心：模板库 + 我的分身 + 编码引擎选择）       │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                              │ WebSocket                               │
│  ┌───────────────────────────┴────────────────────────────────────┐  │
│  │                        Gateway 进程  (HTTP :29000)               │  │
│  │   Trigger Engine（cron/heartbeat/webhook/event）                 │  │
│  │   消息路由 · 渠道管理（Web/IM/TUI/Desktop/ACP）· Report 服务      │  │
│  └───────────────────────────┬────────────────────────────────────┘  │
│                              │ E2A / WebSocket                         │
│  ┌───────────────────────────┴────────────────────────────────────┐  │
│  │                     AvatarServer 进程  (WS :29001)               │  │
│  │   Avatar Runtime（DeepAgent 适配器 interface_deep）              │  │
│  │   ├── PersonaManager（身份/分身 CRUD）                           │  │
│  │   ├── SkillManager（技能安装/加载）                              │  │
│  │   ├── Coding Engine（jiuwen / claude-code / codex）★            │  │
│  │   ├── PersonaAvatarChatRail（注入身份约束 + 引擎编排提示）        │  │
│  │   └── Memory / Session                                           │  │
│  └──────────────────────────────────────────────────────────────────┘ │
│                          jiuwenbox 沙箱                                 │
└──────────────────────────────────────────────────────────────────────┘
```

| 进程 | 职责 | 端口 |
|------|------|------|
| **AvatarServer** | 分身运行时、LLM 调用、工具执行、技能管理、编码引擎、记忆 | WS :29001 |
| **Gateway** | 消息路由、触发器引擎、渠道接入、报告服务、Web 服务 | HTTP :29000 |

两进程通过 E2A 协议（WebSocket）通信，由 `app.py` 统一拉起。

---

## 四、统一编码引擎抽象（核心设计）★

分身执行"代码检视 / 开发 / 测试"等重活时，背后的"编码后端"被抽象为 **可互换的 `CodingEngine`**。无论选哪个后端，**Leader（编排层）只看到同一个 `coding_task` 工具**，运行时按分身的 `coding_engine` 自动路由——这就是"架构合理、不拆太散"的落点。

### 4.1 模块结构

```
jiuwenavatar/server/runtime/coding/
├── __init__.py     # 公共 API：get_coding_engine / coding_task / set_active_coding_engine
├── engines.py      # CodingEngine 抽象 + 三个实现 + 注册表
├── tool.py         # 统一 coding_task 工具 + ContextVar 路由
└── bootstrap.py    # CLI 缺失即调用 scripts/setup_coding_cli.sh 安装
```

### 4.2 引擎契约

```python
class CodingEngine(ABC):
    kind: str                 # jiuwen-coding / claude-code / codex
    display_name: str
    is_cli: bool              # 是否外挂外部 CLI

    def is_available(self) -> bool: ...                 # CLI 是否就绪（原生恒 True）
    def provides_tool(self) -> bool: ...                # 是否需注册 coding_task（仅 CLI 引擎）
    def ensure_ready(self, skills_root) -> EngineStatus # 准备工作区 + 缺失即安装
    async def run_task(self, message, cwd=None) -> str  # 执行一次编码任务
    def prompt_section(self, skills_root, language)     # 注入给 Leader 的编排提示
```

| 引擎 | `is_cli` | 工作区 | 运行方式 | 注册 `coding_task` |
|------|----------|--------|----------|--------------------|
| `jiuwen-coding`（默认） | 否 | 直接用 `skills/` | Leader 直接用 Skill + bash | 否 |
| `claude-code` | 是 | `~/.jiuwenavatar/agent/workspace/aidlc-cc/` | `claude -p "<prompt>" --dangerously-skip-permissions` | 是 |
| `codex` | 是 | `~/.jiuwenavatar/agent/workspace/aidlc-codex/` | `codex exec "<prompt>"` | 是 |

- 所有 CLI 引擎共享 `CliCodingEngine` 基类：查找可执行文件 → 准备工作区（把分身 `skills/` 软链进去）→ 缺失即安装 → 跑 `<cli> ... <prompt>`。
- Claude Code 额外同步 `.claude/agents/` 与 `settings.json`；Codex 额外写入 `AGENTS.md`。
- `coding_task` 工具通过 `ContextVar` 拿到当前会话激活的引擎并转发；原生 `jiuwen-coding` 不注册该工具（Leader 直接干）。

### 4.3 缺失即安装

CLI 引擎在 `ensure_ready()` 时若发现 `claude` / `codex` 不在 PATH，会调用：

```
scripts/setup_coding_cli.sh <claude-code|codex|all>
```

该脚本自动识别国内网络（走 npm 淘宝镜像），海外走官方安装器 > Homebrew > npm；也可由用户手动运行。可用 `JIUWEN_AUTO_INSTALL_CODING_CLI=0` 关闭自动安装。

### 4.4 一次编码任务的时序

```
用户 / Trigger 发来 PR URL
      │
      ▼
interface_deep._apply_avatar_chat_context(avatar_id)
      │  resolve_avatar_chat_context → coding_engine / skills / system_prompt
      │  ensure_avatar_skills_installed（缺失的内置 Skill 自动装到 workspace）
      │  engine = get_coding_engine(coding_engine)
      │  engine.ensure_ready(skills_root)   # 工作区 + 缺失即装
      │  set_active_coding_engine(engine)   # ContextVar
      │  provides_tool() ? 注册 coding_task : 移除 coding_task
      │  PersonaAvatarChatRail 注入身份约束 + engine.prompt_section()
      ▼
Leader（九问 Agent）按提示编排：
   ├─ jiuwen-coding：直接用 dev-reviewer 等 Skill + bash 完成
   └─ claude-code / codex：coding_task(message="@dev-reviewer 审查此 PR: <URL>")
            → 运行时路由到 claude -p / codex exec（工作区已软链 skills）
            → 返回检视结论
      ▼
Leader 拿到结论 → 用 gitcode-repo Skill 提交检视意见 / 建 PR → 汇总报告
```

---

## 五、关键后端模块

### 5.1 Persona / Avatar — `server/runtime/persona/`

```
persona/
├── models.py                    # PersonaConfig / AvatarConfig（Pydantic）
├── loader.py                    # 加载 resources/personas/*.yaml
├── manager.py                   # PersonaManager：身份/分身 CRUD + WS handlers
├── avatar_factory.py            # Persona → Avatar 实例（含技能安装）
├── chat_context.py              # 解析分身对话上下文（编码引擎无关）
└── persona_avatar_chat_rail.py  # 注入身份约束 + 编码引擎编排提示
```

预置 Persona（`resources/personas/`）：`committer.yaml`、`developer.yaml`、`tester.yaml`，三者均 `coding_capable: true`、可选 `[jiuwen-coding, claude-code, codex]`、默认 `jiuwen-coding`。

### 5.2 Coding Engine — `server/runtime/coding/`

见第四章。

### 5.3 Trigger Engine — `gateway/trigger/`

```
trigger/
├── engine.py            # 统一调度入口（单例）
├── base.py              # ITrigger 基类
├── cron_trigger.py      # 定时
├── heartbeat_trigger.py # 心跳
├── webhook_trigger.py   # Webhook 回调
├── event_trigger.py     # 事件
├── models.py / store.py # 模型 + 持久化
```

创建分身时，`PersonaManager._provision_triggers_from_persona` 会按 Persona 的 `trigger_templates` 自动建好触发器。

### 5.4 Mission & Report — `gateway/report/`

```
report/
├── models.py   # Mission / MissionReport 数据模型
├── manager.py  # 任务生命周期 + 报告生成/推送
└── store.py    # SQLite 持久化
```

---

## 六、前端架构 — `channels/web/frontend/`

导航（6 个）：`chat | avatars | triggers | reports | channels | config`

| 面板 | 说明 |
|------|------|
| **AvatarCenter** | 分身中心：模板库 + 我的分身 + **CodingEngineSelect（编码引擎选择）** |
| **TriggerPanel** | 统一触发器管理（cron / heartbeat / webhook） |
| **MissionReportPanel** | 执行报告列表 / 详情 / 导出 / 推送 |
| **ConfigPanel** | 系统设置：GitCode Token、Anthropic / OpenAI API Key 等 |

Store：`avatarStore`（分身/Persona）、`reportStore`（报告）。

---

## 七、创建分身的完整流程

1. **进入「分身中心」** → 选择一个 Persona 模板（如 Committer）。
2. **（编码类分身）选择编码引擎**：`CodingEngineSelect` 展示该 Persona 的 `coding_engines`，默认选中 `default_coding_engine`（`jiuwen-coding`）。
   - 想指派给 Claude Code → 选 `claude-code`；想用 Codex → 选 `codex`。
3. **点击创建** → 前端调用 `avatars.create`：
   - 后端 `PersonaManager.create_avatar`：继承 Persona 的默认 Skill + 解析 `coding_engine`；
   - `_install_persona_skills`：把绑定的内置 Skill（`dev-reviewer` / `gitcode-repo` …）安装到 `~/.jiuwenavatar/agent/workspace/skills/`；
   - `_provision_triggers_from_persona`：按模板自动建触发器。
4. **配置凭据**（ConfigPanel）：`GITCODE_TOKEN`（提交检视意见）、`ANTHROPIC_API_KEY`（claude-code）/ `OPENAI_API_KEY`（codex）。
5. **指派任务**：
   - 在「对话」里选中该分身，直接发任务（如贴 PR URL）；或
   - 由触发器（定时 / Webhook）自动派发。
6. **运行时自动处理**：装好缺失 Skill → 准备编码引擎工作区 →（CLI 缺失则自动安装）→ 注入身份约束与引擎编排提示 → Leader 执行并产出报告。

> 切换引擎随时可改：在分身详情里重新选择 `coding_engine` 即可，技能与触发器不受影响。

---

## 八、Committer 分身端到端数据流（以 Claude Code 为例）

```
[定时触发 09:00 工作日]  或  [Webhook: gitcode.pr.assigned]
      │
      ▼
Trigger Engine 匹配 Committer Avatar 的触发器 → 创建 Mission (RUNNING)
      │
      ▼
AvatarServer 启动该分身会话（avatar_id 注入 runtime_config）
      │  ensure_avatar_skills_installed → dev-reviewer / gitcode-repo / aidlc-common / user-interact
      │  engine = ClaudeCodeEngine; ensure_ready()：
      │     aidlc-cc/skills → 软链 workspace/skills；.claude/agents 同步；claude 缺失则安装
      │  注册 coding_task；Rail 注入"用 coding_task 委派检视、拿结果后用 gitcode-repo 提交"
      ▼
Leader 编排：
   1. coding_task(message="@dev-reviewer 审查此 PR: <URL>")
        → claude -p 在 aidlc-cc 工作区执行检视（GITCODE_TOKEN 已注入子进程）
        → 返回检视结论
   2. 用 gitcode-repo Skill 把检视意见提交回 GitCode PR
   3. 汇总为结构化检视报告
      ▼
Mission (COMPLETED)
   ├── Report 存入 ReportStore
   ├── 推送到配置渠道（企业微信 / 飞书 / Web…）
   └── 前端 MissionReportPanel 可查看
```

切换为 `jiuwen-coding`：第 1 步不调用 `coding_task`，Leader 直接用 `dev-reviewer` Skill + bash 完成检视，其余一致。切换为 `codex`：第 1 步路由到 `codex exec`，其余一致。

---

## 九、目录结构速查

```
jiuwenavatar/
├── app.py                       # 双进程统一入口
├── server/runtime/
│   ├── persona/                 # 身份/分身体系 + 对话上下文 + Rail
│   ├── coding/                  # ★ 统一编码引擎抽象（jiuwen/claude-code/codex）
│   ├── skill/                   # 技能管理/安装
│   └── agent_adapter/
│       └── interface_deep.py    # DeepAgent 适配器（编码引擎在此装配）
├── gateway/
│   ├── trigger/                 # 触发器引擎
│   ├── report/                  # 任务 & 报告
│   ├── channel_manager/         # 渠道（Web / IM / TUI…）
│   └── routing / message_handler
├── channels/                    # Web 前端 / TUI / Desktop / ACP
└── resources/
    ├── personas/                # committer / developer / tester 模板
    └── avatar-skills/           # 内置 AIDLC Skill + claude-agents/ + claude-settings.json

scripts/setup_coding_cli.sh      # 编码引擎 CLI（claude / codex）安装脚本（缺失即装）
```
