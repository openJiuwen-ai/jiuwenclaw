<div align="center">

# JiuwenAvatar

> 随叫随到的智能管家，让 AI 触手可及

[![Python Version](https://img.shields.io/badge/python-3.11%2C3.12%2C3.13-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-Apache--2.0-green)](LICENSE)
[![华为云MaaS](https://img.shields.io/badge/华为云-MaaS-red)](https://www.huaweicloud.com/)

</div>

**JiuwenAvatar** 是一款基于 Python 的智能 AI Agent，支持自托管部署、数据自主可控，可通过 Web、TUI、飞书 / 小艺等多端接入。

[English](README.md) ｜ 简体中文

---

## 🧑‍💻 源码部署

仓库根目录提供一键脚本：**macOS / Linux 用 `dev.sh`，Windows 用 `dev.ps1`**，流程一致：装后端依赖 → 装并构建前端 → 清理占用端口 → 等 Gateway 就绪 → 同时拉起后端(29000) 与前端 Vite(29173)。

### 0. 前置依赖（所有平台）

| 依赖 | 版本 | 说明 |
|------|------|------|
| [Python](https://www.python.org/) | 3.11 ~ 3.13 | 后端运行时 |
| [uv](https://docs.astral.sh/uv/) | 最新 | Python 依赖与虚拟环境管理（脚本用 `uv sync` / `uv run`） |
| [Node.js](https://nodejs.org/) | ≥ 18 LTS | 前端构建（自带 npm / npx） |
| Git | 最新 | 拉取源码 |

安装 uv：

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

```powershell
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### 1. 拉取源码并初始化工作区

```bash
# 已有源码可跳过 clone，直接进入项目根目录
git clone https://gitcode.com/zhengshangyi/jiuwen-avatar.git
cd jiuwen-avatar

uv sync                     # 安装后端依赖（首次较慢）
uv run jiuwenavatar-init    # 初始化工作区 ~/.jiuwenavatar（首次必做）
```

> `jiuwenavatar-init` 会在用户目录创建 `~/.jiuwenavatar`（Windows 为 `%USERPROFILE%\.jiuwenavatar`），含配置、技能、记忆等。**模型 API Key 等凭据**在该目录的配置文件中填写，详见 [配置信息](docs/zh/配置信息.md)。

### 2. 一键启动

**macOS / Linux：**

```bash
./dev.sh                 # 完整流程：装依赖 + 构建前端 + 启动后端&前端
./dev.sh --skip-install  # 跳过依赖安装
./dev.sh --skip-build    # 跳过前端构建（用 Vite HMR 调试更快）
./dev.sh --frontend-only # 只启动前端 Vite dev server
./dev.sh --backend-only  # 只启动后端
```

**Windows（PowerShell）：**

```powershell
# 首次可能需放开脚本执行策略（仅当前进程，安全）
powershell -ExecutionPolicy Bypass -File .\dev.ps1

# 或在已放开策略的 PowerShell 里直接：
.\dev.ps1
.\dev.ps1 -SkipInstall      # 跳过依赖安装
.\dev.ps1 -SkipBuild        # 跳过前端构建
.\dev.ps1 -FrontendOnly     # 只启动前端
.\dev.ps1 -BackendOnly      # 只启动后端
```

> Windows 用户也可使用 [WSL2](https://learn.microsoft.com/windows/wsl/install) 后直接运行 `./dev.sh`，体验与 Linux 一致。

启动成功后访问 **前端 http://localhost:29173**（开发热更新），后端 Gateway 在 **http://localhost:29000**。按 `Ctrl+C` 停止全部服务并清理端口。

### 3. 不想用脚本？手动启动

```bash
# 后端（Gateway + AgentServer）
uv run python -m jiuwenavatar.app

# 前端（另开一个终端）
cd jiuwenavatar/channels/web/frontend
npm install      # 首次
npm run dev      # Vite dev server，默认 http://localhost:29173
```

## 🔌 端口一览

| 端口 | 用途 |
|------|------|
| 29173 | 前端 Vite dev server（开发访问入口） |
| 29000 | Web Gateway（前端代理后端） |
| 29001 | ACP / TUI Gateway |
| 29002 | Webhook（GitCode 等，需 `WEBHOOK_ENABLED=true`） |
| 28092 | AgentServer |

> 启动脚本会在启动前自动清理上述端口上的残留进程，避免「端口被占用 / ECONNREFUSED」一类问题。

## ⚠️ 升级提醒

拉取新代码后重新执行 `uv sync`；若工作区结构有重大变更，需重新运行 `uv run jiuwenavatar-init`，否则服务可能无法启动。升级前请备份工作区目录 `~/.jiuwenavatar`（含配置、记忆、自定义技能）。

## 📚 更多文档

| 文档 | 核心内容 |
|:-----|:---------|
| [📖 安装指南](docs/zh/安装指南.md) | 从零安装（源码、conda、Docker 等） |
| [📖 快速开始](docs/zh/Quickstart.md) | 5 分钟上手 |
| [⚙️ 配置与工作空间](docs/zh/配置信息.md) | 模型 / 凭据配置与工作区管理 |
| [📱 频道配置](docs/zh/频道.md) | 飞书、小艺等频道接入 |
| [🛠️ 技能系统](docs/zh/技能.md) | 自定义技能开发 |
| [⏰ 定时任务](docs/zh/定时任务.md) | 定时任务与心跳 |
| [👥 分布式 Team](docs/zh/分布式Team.md) | 多进程分布式团队模式 |
| [🔀 单机多实例](docs/zh/单机多实例运行.md) | 同机运行多个独立实例 |

完整文档索引见 [`docs/zh/`](docs/zh/) 目录。

## 📄 开源协议

本项目采用 **Apache License 2.0** 开源协议，详情见 [LICENSE](LICENSE)。
