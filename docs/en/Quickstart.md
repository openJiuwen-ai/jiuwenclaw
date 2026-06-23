# Quick Start

> **⚠️ Version Sync**: This document should be kept in sync with [`docs/zh/Quickstart.md`](../zh/Quickstart.md). When updating one, please update the other.

## Installation

### Prerequisites

Before installing JiuwenSwarm, ensure your system meets the following requirements:

| Dependency | Version | Description |
|------------|---------|-------------|
| Operating System | Windows 10/11, macOS 10.15+, Linux | Supports mainstream operating systems |
| Python | ≥3.11, <3.14 | Python 3.11 recommended |
| Node.js | 18.x or higher | For frontend interface |
| Git | Latest | For source code installation |

Check Node.js version:

```bash
node --version
# Expected output: v18.x.x or higher
```

### pip Install

```bash
# Create virtual environment
python -m venv jiuwenswarm

# Activate virtual environment
# Windows:
jiuwenswarm\Scripts\activate
# Linux/Mac:
source jiuwenswarm/bin/activate

# Install JiuwenSwarm
pip install jiuwenswarm
```

## Start Service

```bash
# Initialize (first run)
jiuwenswarm-init

# Start service
jiuwenswarm-start
```

After successful startup, the terminal will display backend service status:

```
[INFO] Starting JiuwenSwarm server...
[INFO] API server running at http://localhost:8000
[INFO] Web server running at http://localhost:5173
```

When you see similar output, the service is ready. Open `http://localhost:5173` in your browser to use.

### Terminal CLI

You can also chat with JiuwenSwarm directly from the terminal:

```bash
jiuwenswarm chat "Hello, introduce yourself"
```

For details, see [CLI / Terminal Chat](CLI.md#terminal-cli-jiuwenswarm-chat).

### Remote Access (Optional)

For remote access, run the following commands:

```bash
# Start web service
jiuwenswarm-web --host 0.0.0.0 --port <custom-port>

# Start backend service
jiuwenswarm-app
```

## Configure Model

In the left sidebar of the Web UI, find "Configuration" and enter the configuration page:

![](../assets/images/jiuwenswarm_configuration_Info.png)

Complete the following basic configuration, then click "Save" in the top right:

![](../assets/images/jiuwenswarm_config_api.png)

**Configuration Items:**

| Field | Environment variable | Description | Required |
|--------|------------------------|-------------|----------|
| `model_name` | `MODEL_NAME` | Model name, e.g., `deepseek-chat`, `gpt-4o` | ✅ Required |
| `api_base` | `API_BASE` | Model API base URL, e.g., `https://api.deepseek.com` | ✅ Required |
| `api_key` | `API_KEY` | Model API key | ✅ Required |
| `model_provider` | `MODEL_PROVIDER` | Model provider, e.g., `OpenAI`, `DeepSeek`, `Anthropic` | ✅ Required |

**Test After Configuration:**

After filling in the configuration, click the "Test" button to verify model availability. A successful test shows ✅, if failed check:
- Whether API Key is correct
- Whether API Base URL is accessible
- Whether model name and Provider match

**Notes:**

- **Auto-restart after save**: Backend automatically restarts to load new configuration
- **Required fields**: The four fields above are basic configuration required for normal operation
- **Model Providers**: `OpenAI`, `DashScope`, `SiliconFlow`, `InferenceAffinity`

## Start Conversation

In the left sidebar of the Web UI, find "Chat" and enter your question to start:

![](../assets/images/jiuwenswarm_example.png)

## Session Management

Click the "+" button below to clear the current session and start a new one:

![](../assets/images/jiuwenswarm_new_session.png)

Page display after clearing:

![](../assets/images/jiuwenswarm_clear_session.png)

**When to clear a session?**

| Scenario | Description |
|----------|-------------|
| **Topic Switch** | Current conversation is complete, want to start a completely new topic |
| **Context Confusion** | Too much content in current session, model understanding deviates |
| **Repeated/Wrong Response** | Model falls into loop response or gives irrelevant answers |
| **Privacy/Sensitive Info** | Current session contains temporary sensitive information that needs immediate clearing |

**Comparison: Clear vs Not Clear Session:**

| Comparison | Not Clear (Continue Session) | Clear (New Session) |
|------------|------------------------------|---------------------|
| **Context Retention** | ✅ Keep all history, model knows full context | ❌ No history retained, model starts from scratch |
| **Token Consumption** | ⚠️ Grows with conversation, consumes more tokens | ✅ Initial tokens minimal, cost controllable |
| **Answer Relevance** | Early topics may interfere with current understanding | Each question processed independently, no interference |
| **Privacy Security** | History persists in current session | Sensitive info not carried to new session |

## Clear Memory

When you need JiuwenSwarm to forget all conversation history and user information, you can clear memory files.

> **⚠️ Risk Warning:** Clearing memory is **permanent**, deleted memory files **cannot be recovered**. Before proceeding, confirm:
> - Whether important memories are backed up
> - Whether you really need to delete (or just want to start a new session)

**Difference from Session Clearing:**

| Operation | Scope | Impact | Use Case |
|-----------|-------|--------|----------|
| **New Session** | Current chat window | Does not delete any memory, only starts new thread | Want to switch topics but keep historical memory for reference |
| **Clear Memory** | All memory files | Permanently deletes all history, user info, project memory | Completely clear all history, protect privacy, or reset to initial state |

**Use Cases:**
- **Privacy Protection**: Clear history containing sensitive information
- **Fresh Start**: Start a completely different project or topic, avoid historical interference
- **Debug Troubleshooting**: Reset when memory files are corrupted or content is abnormal
- **User Switching**: Clear previous user info in multi-user environments

**Steps to Clear Memory:**

Memory files are stored in `{workspace_dir}/memory/` directory:

**Method 1: Delete via Agent**
Tell JiuwenSwarm: "Please delete all memory files" or "Clear my memory", Agent will call file tools to delete files in the memory directory.
![](../assets/images/jiuwenswarm_delete_memory.png)

**Method 2: Manual Delete**
Stop JiuwenSwarm service, then directly delete all Markdown files in the `memory/` directory.
![](../assets/images/jiuwenswarm_memory.png)

> ⚠️ **Note**: Memory cannot be recovered after clearing, proceed with caution. Regularly backup important memory files.
