<div align="center">

# JiuwenSwarm

> Your On-Call AI Butler — Bringing Intelligence to Your Fingertips

[![Python Version](https://img.shields.io/badge/python-3.11%2C3.12%2C3.13-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-Apache--2.0-green)](LICENSE)
[![Huawei Cloud MaaS](https://img.shields.io/badge/华为云-MaaS-red)](https://www.huaweicloud.com/)

</div>

## 🌟 Overview

**JiuwenSwarm** is an intelligent AI Agent built in Python. As its name suggests, “Swarm” symbolizes multiple intelligent agents working together as efficiently as a swarm of bees, evolving autonomously and accelerating the era of “collective intelligence” in earnest.

### ✨ Key Features

- **Ecosystem Compatible**: Full support for **Huawei Cloud MaaS** and other mainstream model platforms
- **Seamless Integration**: Native integration with the **Xiaoyi Open Platform**, enabling Huawei phone users to invoke JiuwenSwarm directly through the Xiaoyi assistant
- **Flexible Deployment**: Self-hosted deployment with full data sovereignty
- **Multi-Platform Access**: Interact via web interface, messaging apps, and more

## 🎯 Design Philosophy

> **Understands You. Evolves With You.**

### 🤝 Your Personal Task Butler

Whether dealing with task additions, mid-flow interruptions, or shifting requirements, JiuwenSwarm understands your intent precisely — intelligently scheduling and executing tasks in an orderly, stress-free manner.

### 🔄 Autonomous Evolution

When you express dissatisfaction or an error occurs, JiuwenSwarm automatically refines the relevant skills based on your feedback — continuously improving, always working in your best interest.


<p align="center">
  <strong>⚡ Your always-on, data-sovereign personal AI assistant ⚡</strong>
</p>

## ⚠️ Version Upgrade Notice

If you're upgrading from an older version, check the changelog for any breaking changes. You **must** reinitialize JiuwenSwarm if a breaking change is indicated. The service will fail to start without reinitialization.

### Backup Before Upgrading

| Data Type | Source Path | Description |
|-----------|-------------|-------------|
| Memory Data | `.jiuwenswarm/workspace/agent/memory` | All your conversation memories |
| Custom Skills | `.jiuwenswarm/workspace/agent/skills` | Your custom agent skills |
| Configuration | `.jiuwenswarm/config` | Your app settings |

### Migration Steps

After upgrading and running `jiuwenswarm-init`, manually migrate your data:

1. **Copy Memory:**
   ```bash
   cp -r .jiuwenswarm/workspace/agent/memory .jiuwenswarm/agent/memory
   ```

2. **Copy Skills:**
   ```bash
   cp -r .jiuwenswarm/workspace/agent/skills .jiuwenswarm/agent/skills
   ```

## 🚀 Getting Started

### 📦 Installation

```bash
# Install JiuwenSwarm
pip install jiuwenswarm

# Initialize JiuwenSwarm (first-time setup or after upgrading)
# ⚠️ Remember to backup your data before running this command
jiuwenswarm-init

# Start JiuwenSwarm
jiuwenswarm-start

# Install JiuwenSwarm-tui
pip install jiuwenswarm-tui

# Start JiuwenSwarm-tui
jiuwenswarm-tui
```

### 💬 How to Use

#### 1️⃣ Conversation Mode

| Method             | Description                                                  |
| ------------------ | ------------------------------------------------------------ |
| **Web Frontend**   | After starting the service, visit `http://localhost:5173` to chat directly in your browser |
| **Xiaoyi Channel** | Huawei phone users can invoke Xiaoyi to talk with JiuwenSwarm directly |
| **Lark Channel** | Once configured, chat with JiuwenSwarm seamlessly inside Lark |

#### 2️⃣ Scheduled Tasks

Set up heartbeat tasks with your to-do items, and JiuwenSwarm will wake up on schedule to execute them automatically — making your time management smarter and more effortless.

## 📚 Documentation

| Document | Description |
| :--------------------------------------------------- | :------------------------------------------------------- |
| [📖 Install guide](docs/en/InstallGuide.md)          | Full installation paths (pip, source, conda, Docker)     |
| [📖 Quick Start](docs/en/Quickstart.md) | Get up and running with JiuwenSwarm in 5 minutes |
| [📖 Quick Start (TUI)](docs/en/Quickstart_tui.md) | Get up and running with JiuwenSwarm-tui in 5 minutes |
| [⚙️ Configuration & Workspace](docs/en/Configuration.md) | Environment setup and workspace management |
| [📁 Workspace Structure](docs/en/Agent.md) | workspace directory layout, presets, and dynamic content |
| [🔄 Modes](docs/en/Modes.md) | PLAN / AGENT / CODE / TEAM mode switching and configuration |
| [🛠️ Skill System](docs/en/Skills.md) | Guide to developing custom skills |
| [🔄 Skill Self-Evolution](docs/en/SkillSelfEvolution.md) | Mechanism for automatic skill evolution |
| [📱 Channel Configuration](docs/en/Channels.md) | Integrating Feishu, Xiaoyi, and other channels |
| [💬 Discord](docs/en/Discord.md) | Discord channel setup and usage |
| [💬 WhatsApp](docs/en/WhatsApp.md) | WhatsApp channel setup and usage |
| [⌨️ CLI Commands](docs/en/CLI.md) | Command-line tool usage guide |
| [⏰ Scheduled Tasks](docs/en/ScheduledTasks.md) | Scheduled task management |
| [💓 Heartbeat](docs/en/Heartbeat.md) | Heartbeat mechanism and configuration |
| [🧠 Memory](docs/en/Memory.md) | Intelligent memory and learning capabilities |
| [💡 Task Memory](docs/en/TaskMemory.md) | Task-level experience retrieval and consolidation |
| [📦 Context Compression](docs/en/ContextCompression.md) | Context compression and unloading |
| [💻 Coding Memory](docs/en/CodingMemory.md) | Code-mode-specific memory system |
| [📋 Task Planning](docs/en/TaskPlanning.md) | Chat behavior and task flow |
| [🌐 Browser Automation](docs/en/Browser.md) | Web browsing and automation features |
| [🔌 MCP Configuration](docs/en/MCPConfiguration.md) | MCP server integration and configuration |
| [🔒 Tool Permissions & Security](docs/en/ToolPermissionsSecurity.md) | Permission model and security configuration |
| [📝 Slash Commands](docs/en/SlashCommands.md) | Slash command reference |
| [🏗️ Slash Command Architecture](docs/en/SlashCommandArchitecture.md) | Slash command internals and extension guide |
| [📨 E2A Protocol](docs/en/E2A-protocol.md) | Gateway ↔ Agent request envelope specification |
| [🤝 A2A Integration](docs/en/A2A.md) | A2A protocol integration guide |
| [🔌 ACP Client Config](docs/en/ACP_Client_Config.md) | ACP client plugin configuration |
| [👥 Distributed Team](docs/en/DistributedTeam.md) | Multi-process distributed team mode |
| [🔀 Multi-Instance Operation](docs/en/MultiInstance.md) | Running multiple independent instances on one machine |
| [📦 Packaging Desktop App](docs/en/PackExeGuide.md) | Build standalone desktop executables |
| [🚀 Development Practices](docs/en/development-practices/README.md) | Development practices and experience sharing |


## 🤝 Contributing

We warmly welcome community contributions — whether it's filing bug reports, suggesting new features, or improving documentation, every bit of support means the world to us.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

## 📄 License

This project is licensed under the **Apache License 2.0**. See the [LICENSE](LICENSE) file for details.


---

<p align="center">
  <strong>Making intelligence accessible. Making life simpler.</strong><br>
  <sub>✨ JiuwenSwarm — Your Personal AI Assistant ✨</sub>
</p>
