<div align="center">

# JiuwenSwarm

> 随叫随到的智能管家，让AI触手可及

[![Python Version](https://img.shields.io/badge/python-3.11%2C3.12%2C3.13-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-Apache--2.0-green)](LICENSE)
[![华为云MaaS](https://img.shields.io/badge/华为云-MaaS-red)](https://www.huaweicloud.com/)

</div>

## 🌟 项目简介

**JiuwenSwarm** 是一款基于Python开发的智能AI Agent，正如其名——"Swarm"象征着让多个智能体像蜂群⼀样⾼效协作、⾃主演进，正式按下"群体智能"的加速键!

### ✨ 核心特色

- **生态兼容**：完美支持**华为云MaaS**等主流模型平台
- **无缝对接**：与**小艺开放平台**无缝接入，华为手机用户可通过小艺直接唤醒
- **灵活部署**：支持自托管部署，数据完全自主可控
- **多端接入**：支持Web端、聊天软件等多种交互方式

## 🎯 核心理念

> **懂你所想，自主演进**

### 🤝 贴身任务管家
面对复杂的输入场景——任务追加、指令打断、需求修改，JiuwenSwarm都能精准理解，为你智能排期，有条不紊地完成任务。

### 🔄 自主演进
当你表达不满或运行出错时，它会根据你的反馈自动调整相应技能，持续演进，全心全意为你服务。

<p align="center">
  <strong>⚡ 一个始终在线、数据自主的专属AI助理 ⚡</strong>
</p>

## ⚠️ 版本升级提醒

如果您从旧版本升级，请查看更新日志确认是否有重大变更。如有重大变更，升级后**必须**重新初始化 JiuwenSwarm，否则服务将无法启动。

### 升级前备份数据

| 数据类型 | 原路径 | 说明 |
|---------|--------|------|
| 记忆数据 | `.jiuwenswarm/workspace/agent/memory` | 所有对话记忆 |
| 自定义技能 | `.jiuwenswarm/workspace/agent/skills` | 您的自定义技能 |
| 配置文件 | `.jiuwenswarm/config` | 应用设置 |

### 数据迁移步骤

升级并运行 `jiuwenswarm-init` 后，请手动迁移数据：

1. **迁移记忆数据**：将原目录下的 `.jiuwenswarm/workspace/agent/memory` 复制到 `.jiuwenswarm/agent/memory`

2. **迁移技能数据**：将原目录下的 `.jiuwenswarm/workspace/agent/skills` 复制到 `.jiuwenswarm/agent/skills`

## 🚀 快速上手

### 📦 安装

```bash
# 安装 JiuwenSwarm
pip install jiuwenswarm

# 初始化 JiuwenSwarm (首次启动)
jiuwenswarm-init

# 启动 JiuwenSwarm
jiuwenswarm-start

# 安装 JiuwenSwarm-tui
pip install jiuwenswarm-tui

# 启动 JiuwenSwarm-tui
jiuwenswarm-tui
```

### 💬 使用方式

📖 详细的快速上手指南，请参阅 [📖 快速开始文档](docs/zh/Quickstart.md)，包含：

- **配置模型**：模型配置与API设置
- **开始对话**：如何与JiuwenSwarm对话
- **会话管理**：清空会话的场景与效果对比
- **清空记忆**：风险提示与操作步骤

快速启动命令：

```bash
# 安装并启动 JiuwenSwarm
pip install jiuwenswarm
jiuwenswarm-init    # 首次运行或升级后执行
jiuwenswarm-start   # 启动服务，访问 http://localhost:5173

# 或使用 TUI 版本
pip install jiuwenswarm-tui
jiuwenswarm-tui
```

### 💬 对话模式

| 方式 | 说明                                        |
|------|-------------------------------------------|
| **Web前端** | 启动服务后访问 `http://localhost:5173`，通过浏览器直接对话 |
| **小艺频道** | 华为手机用户可直接唤醒小艺，与JiuwenSwarm对话               |
| **飞书频道** | 完成渠道配置后，在飞书中与JiuwenSwarm畅聊                 |

### ⏰ 定时任务

设置心跳任务，填写待办事项，JiuwenSwarm即可定时被唤醒，自动执行预设任务。让你的日程管理更加智能高效！

#### 6️⃣ 清空记忆

当你需要让 JiuwenSwarm忘记之前的所有对话历史和用户信息时，可以清空记忆文件。

**适用场景：**
- **隐私保护**：清除包含敏感信息的历史记录
- **全新开始**：开始一个完全不同的项目或话题，避免历史信息干扰
- **调试排错**：记忆文件损坏或内容异常时重置
- **用户切换**：多用户共用环境时，清除上一个用户的信息

**清空记忆操作步骤：**

记忆文件存储在 `{workspace_dir}/memory/` 目录下：

**方式一：通过 Agent 删除**
直接告诉 JiuwenSwarm："请删除所有记忆文件" 或 "清空我的记忆"，Agent 会调用文件工具删除 memory 目录下的文件。
![](docs/assets/images/jiuwenswarm_delete_memory.png)

**方式二：手动删除**
停止 JiuwenSwarm服务后，直接删除 `memory/` 目录下的所有 Markdown 文件即可。
![](docs/assets/images/jiuwenswarm_memory.png)

> ⚠️ **注意**：清空记忆后无法恢复，请谨慎操作。建议定期备份重要的记忆文件。

## 📚 文档导航

| 文档 | 核心内容 |
|:-----|:---------|
| [📖 安装指南](docs/zh/安装指南.md) | 从零安装（pip、源码、conda、Docker 等） |
| [📖 快速开始](docs/zh/Quickstart.md) | 5分钟上手JiuwenSwarm |
| [📖 快速开始(TUI)](docs/zh/Quickstart_tui.md) | 5分钟上手JiuwenSwarm-tui |
| [📖 TUI 使用指南](docs/zh/TUI使用指南.md) | TUI 命令行参数、Slash 命令、工具与 Code 模式详解 |
| [⚙️ 配置与工作空间](docs/zh/配置信息.md) | 环境配置与工作区管理 |
| [📁 工作区结构](docs/zh/智能体.md) | workspace 目录说明，预置与动态生成内容 |
| [🔄 模式系统](docs/zh/模式系统.md) | PLAN / AGENT / CODE / TEAM 模式切换与配置 |
| [🛠️ 技能系统](docs/zh/技能.md) | 自定义技能开发指南 |
| [🔄 Skill自演进](docs/zh/Skill自演进.md) | Skill自演进机制 |
| [📱 频道配置](docs/zh/频道.md) | 飞书、小艺等频道接入 |
| [💬 Discord](docs/zh/Discord.md) | Discord频道配置与使用 |
| [💬 WhatsApp](docs/zh/whatsapp.md) | WhatsApp频道配置与使用 |
| [⌨️ 命令行指令](docs/zh/命令行指令.md) | 命令行工具使用指南 |
| [⏰ 定时任务](docs/zh/定时任务.md) | 定时任务管理 |
| [💓 心跳](docs/zh/心跳.md) | 心跳机制与配置 |
| [🧠 记忆功能](docs/zh/记忆.md) | 智能记忆与学习 |
| [💡 经验记忆](docs/zh/经验记忆.md) | 任务级经验检索与沉淀 |
| [📦 上下文压缩](docs/zh/上下文压缩卸载.md) | 上下文压缩与卸载 |
| [💻 编码记忆](docs/zh/编码记忆.md) | Code模式专属记忆系统 |
| [📋 任务规划](docs/zh/任务规划.md) | 任务规划与待办事项 |
| [🌐 浏览器相关](docs/zh/浏览器.md) | 自动化浏览功能 |
| [🔌 MCP配置](docs/zh/MCP配置.md) | MCP服务接入与配置 |
| [🔒 工具权限与安全](docs/zh/工具权限与安全防护.md) | 权限模型与安全配置 |
| [📝 Slash命令](docs/zh/Slash命令表.md) | Slash命令速查 |
| [🏗️ Slash命令架构](docs/zh/SLASH_COMMAND_ARCHITECTURE.md) | Slash命令内部机制与扩展 |
| [📨 E2A协议](docs/zh/E2A-protocol.md) | Gateway ↔ Agent 请求信封规范 |
| [🤝 A2A接入](docs/zh/A2A.md) | A2A协议接入说明 |
| [🔌 ACP插件配置](docs/zh/ACP插件使用.md) | ACP客户端插件配置 |
| [👥 分布式Team](docs/zh/分布式Team.md) | 多进程分布式团队模式 |
| [🔀 单机多实例](docs/zh/单机多实例运行.md) | 同一机器运行多个独立实例 |
| [📦 打包桌面应用](docs/zh/打包exe指南.md) | 打包独立桌面可执行文件 |
| [🚀 开发实践](docs/zh/开发实践/) | 开发实践与经验分享 |

## 🤝 参与贡献

我们热烈欢迎社区贡献！无论是提交Bug、提出新功能建议，还是完善文档，都是对项目的宝贵支持。

1. Fork 本仓库
2. 创建您的特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交您的改动 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 打开一个 Pull Request

## 📄 开源协议

本项目采用 **Apache License 2.0** 开源协议，详情请参阅 [LICENSE](LICENSE) 文件。

---

<p align="center">
  <strong>让智能触手可及，让生活更加简单</strong><br>
  <sub>✨ JiuwenSwarm—— 您的专属AI助理 ✨</sub>
</p>