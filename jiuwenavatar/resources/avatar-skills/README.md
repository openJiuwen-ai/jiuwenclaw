# 分身预置 Skill

本目录存放九问数字分身（JiuwenAvatar）**内置默认 Skill**，随仓库一起分发，无需从外部项目拷贝。

## 与 `resources/agent/workspace/` 的区别

| 目录 | 作用 |
|------|------|
| `resources/avatar-skills/`（本目录） | 内置 Skill 源码，纳入版本库，运行时优先从这里解析 |
| `resources/agent/workspace/` | 用户 Agent 工作区初始化模板（`AGENT.md`、`HEARTBEAT.md` 等），不含内置 Skill |
| `resources/agent/workspace/skills/` | 历史兼容/开发生成路径，已被 gitignore，不是权威技能源 |

`jiuwenavatar-init` 只从 `resources/agent/workspace/` 复制工作区基础模板；内置 Skill 由运行时从本目录加载，或在创建分身时安装到用户 `~/.jiuwenavatar/agent/workspace/skills/`。

## 来源

技能内容改编自 [CodeReviewAvatar](https://github.com/) AIDLC 流水线技能集，按 Persona 模板绑定。各 Persona 默认运行模式：

| Persona | 默认模式 | Skills |
|---------|----------|--------|
| committer | dev-reviewer **standalone**（cron/webhook 检视 + 自行提交行评） | dev-reviewer, gitcode-repo, aidlc-common, user-interact |
| developer | **dev-coder standalone**（实现 + 验证 + 提 PR） | dev-coder, gitcode-repo, aidlc-common, env-setup, user-interact |
| tester | **dev-tester standalone**（module / pr-gate）；Aidlc 委派时 G5 流水线 | dev-tester, aidlc-common, bench-runner, bench-creator, env-setup, user-interact |
| one-person-company | 默认 **dev-leader 全流程**；轻量任务可走各 dev-* standalone | dev-leader, dev-analyzer, dev-designer, dev-planner, dev-coder, dev-tester, dev-reviewer, gitcode-repo, … |

`brainstorming` 为通用辅助技能，可按需安装。

## 加载优先级

运行时 `get_builtin_skills_dirs()` 按以下顺序扫描：

1. `resources/avatar-skills/`（本目录，纳入版本库）
2. `resources/agent/workspace/skills/`（本地开发/构建产物，通常被 gitignore）
3. `resources/agent/skills/`（旧版回退路径）

创建分身时会自动将 Persona 关联技能安装到用户工作区 `~/.jiuwenavatar/`。

## 编码引擎集成（jiuwen / claude-code / codex）

分身的 **编码能力** 由统一的「编码引擎」抽象提供（`jiuwenavatar/server/runtime/coding/`）：

| 引擎 | 说明 | 是否需要外部 CLI |
|------|------|------------------|
| `jiuwen-coding` | 原生 DeepAgent，Leader 直接用 Skill + bash 执行 | 否 |
| `claude-code` | 调用本机 `claude -p` CLI | 是（`claude`） |
| `codex` | 调用本机 `codex exec` CLI | 是（`codex`） |

当分身选择外部 CLI 引擎时，运行时会：

1. 准备引擎工作区 `~/.jiuwenavatar/agent/workspace/aidlc-<cc|codex>/`，将 `skills/` 软链进去；
   Claude Code 额外同步 `.claude/agents/`（来自本目录旁 `claude-agents/*.md`）与 `.claude/settings.json`；
   Codex 额外写入 `AGENTS.md`。
2. 若 CLI 缺失，自动调用仓库根安装脚本（Windows: `setup_coding_cli.ps1`，Unix: `setup_coding_cli.sh`；国内自动走淘宝镜像）。
3. 为 Leader 注册统一的 **`coding_task` 工具**——Leader 不感知具体后端，
   运行时按分身的 `coding_engine` 自动路由到 `claude -p` / `codex exec`。

需在配置页填写：`ANTHROPIC_API_KEY`（claude-code，或本机 `claude login`）/ `OPENAI_API_KEY`（codex）、
以及 `GITCODE_TOKEN`（提交检视意见等）。原生 `jiuwen-coding` 无需安装任何外部 CLI。

## 维护

- 每个技能一个子目录，根文件为 `SKILL.md`
- 修改后重启后端即可；已安装到用户目录的副本需重新安装或手动同步
