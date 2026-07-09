<div align="center">

# JiuwenAvatar

> Your On-Call AI Butler — Bringing Intelligence to Your Fingertips

[![Python Version](https://img.shields.io/badge/python-3.11%2C3.12%2C3.13-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-Apache--2.0-green)](LICENSE)
[![Huawei Cloud MaaS](https://img.shields.io/badge/华为云-MaaS-red)](https://www.huaweicloud.com/)

</div>

**JiuwenAvatar** is a self-hosted, data-sovereign AI Agent built in Python. Access it via the web UI, TUI, or messaging channels (Lark, Xiaoyi, and more).

English ｜ [简体中文](README_CN.md)

---

## 🧑‍💻 Deploy from Source

The repo root ships one-click launchers: **`dev.sh` for macOS / Linux, `dev.ps1` for Windows** — both do the same thing: install backend deps → install & build frontend → free busy ports → wait for the Gateway → start backend (29000) and the Vite frontend (29173) together.

### 0. Prerequisites (all platforms)

| Tool | Version | Purpose |
|------|---------|---------|
| [Python](https://www.python.org/) | 3.11 – 3.13 | Backend runtime |
| [uv](https://docs.astral.sh/uv/) | latest | Python deps & venv (`uv sync` / `uv run`) |
| [Node.js](https://nodejs.org/) | ≥ 18 LTS | Frontend build (bundles npm / npx) |
| Git | latest | Clone source |

Install uv:

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

```powershell
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### 1. Clone and initialize the workspace

```bash
# Already have the source? Skip the clone and cd into the project root.
git clone https://gitcode.com/openJiuwen/jiuwen-avatar.git
cd jiuwen-avatar

uv sync                     # install backend deps (slow on first run)
uv run jiuwenavatar-init    # initialize the ~/.jiuwenavatar workspace (first time)
```

> `jiuwenavatar-init` creates `~/.jiuwenavatar` (`%USERPROFILE%\.jiuwenavatar` on Windows) with config, skills and memory. Put your **model API keys / credentials** in the config files there — see [Configuration](docs/en/Configuration.md).

### 2. One-click start

**macOS / Linux:**

```bash
./dev.sh                 # full: install deps + build frontend + start backend & frontend
./dev.sh --skip-install  # skip dependency install
./dev.sh --skip-build    # skip frontend build (faster with Vite HMR)
./dev.sh --frontend-only # only the Vite dev server
./dev.sh --backend-only  # only the backend
```

**Windows (PowerShell):**

```powershell
# First run may need to relax the execution policy (current process only, safe)
powershell -ExecutionPolicy Bypass -File .\dev.ps1

# Or, in a PowerShell where the policy is already relaxed:
.\dev.ps1
.\dev.ps1 -SkipInstall      # skip dependency install
.\dev.ps1 -SkipBuild        # skip frontend build
.\dev.ps1 -FrontendOnly     # only the frontend
.\dev.ps1 -BackendOnly      # only the backend
```

> Windows users can also install [WSL2](https://learn.microsoft.com/windows/wsl/install) and run `./dev.sh` for a Linux-identical experience.

Once started, open the **frontend at http://localhost:29173** (hot reload); the backend Gateway runs at **http://localhost:29000**. Press `Ctrl+C` to stop all services and free the ports.

### 3. Prefer manual startup?

```bash
# Backend (Gateway + AgentServer)
uv run python -m jiuwenavatar.app

# Frontend (in a separate terminal)
cd jiuwenavatar/channels/web/frontend
npm install      # first time
npm run dev      # Vite dev server at http://localhost:29173
```

## 🔌 Ports

| Port | Purpose |
|------|---------|
| 29173 | Frontend Vite dev server (entry point) |
| 29000 | Web Gateway (proxies the backend) |
| 29001 | ACP / TUI Gateway |
| 29002 | Webhook (GitCode etc., requires `WEBHOOK_ENABLED=true`) |
| 28092 | AgentServer |

> The launchers automatically clear any stale processes on these ports before starting, avoiding "port in use / ECONNREFUSED" issues.

## ⚠️ Upgrade Notice

After pulling new code, re-run `uv sync`. If the workspace layout changed significantly, also re-run `uv run jiuwenavatar-init` or the service may fail to start. Back up the `~/.jiuwenavatar` workspace (config, memory, custom skills) before upgrading.

## 📚 More Docs

| Document | Description |
|:---------|:------------|
| [📖 Install Guide](docs/en/InstallGuide.md) | Full installation paths (source, conda, Docker) |
| [📖 Quick Start](docs/en/Quickstart.md) | Up and running in 5 minutes |
| [⚙️ Configuration](docs/en/Configuration.md) | Model / credential setup and workspace management |
| [📱 Channels](docs/en/Channels.md) | Lark, Xiaoyi and other channel integration |
| [🛠️ Skill System](docs/en/Skills.md) | Developing custom skills |
| [⏰ Scheduled Tasks](docs/en/ScheduledTasks.md) | Scheduled tasks and heartbeat |
| [👥 Distributed Team](docs/en/DistributedTeam.md) | Multi-process distributed team mode |
| [🔀 Multi-Instance](docs/en/MultiInstance.md) | Running multiple independent instances |

See the [`docs/en/`](docs/en/) directory for the full index.

## 📄 License

Licensed under the **Apache License 2.0**. See [LICENSE](LICENSE) for details.
