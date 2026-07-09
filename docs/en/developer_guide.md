# Developer Guide

This document is intended for developers of the JiuwenAvatar project, covering how to set up a development environment from source and run tests.

## Prerequisites

| Dependency | Version Requirement | Description |
|------------|---------------------|-------------|
| Operating System | Windows 10/11, macOS 10.15+, Linux | Mainstream OS supported |
| Python | ≥3.11, <3.14 | Python 3.11 recommended |
| Git | Latest | For cloning the repository |
| Node.js | ≥18.x | For frontend interface |
| Bun | Latest | For building TUI frontend packages |

## 1. Clone the Repository

```bash
git clone <repository-url> jiuwenavatar
cd jiuwenavatar
```

## 2. Set Up Development Environment with `uv`

### Install `uv`

If you haven't installed `uv` yet, install it first:

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### Create a Virtual Environment

Use `uv` to create a new virtual environment (supports Python 3.11, 3.12, or 3.13):

```bash
uv venv --python=3.11
```

### Activate the Environment

```bash
# macOS / Linux
source .venv/bin/activate

# Windows
.venv\Scripts\activate
```

### Run uv Sync

Run the following command in the project root directory `jiuwenavatar/`:

```bash
uv sync
```

This command installs all dependencies based on `pyproject.toml` and `uv.lock`, including development dependencies (such as `pytest`, `pytest-cov`, `pytest-asyncio`, etc.).

## 3. Install Bun (For Building TUI Frontend Packages)

The TUI frontend packages require [Bun](https://bun.sh/) for compilation. Installation methods:

```bash
# macOS, Linux, and WSL
curl -fsSL https://bun.sh/install | bash

# Windows (via PowerShell)
powershell -c "irm bun.sh/install.ps1 | iex"

# Or install via npm
npm install -g bun
```

After installation, verify:

```bash
bun --version
```

## 4. Development

Once the environment is set up, you can start developing the source code and test cases.

### Project Structure Overview

```
jiuwenavatar/
├── jiuwenavatar/           # Project source code
├── tests/
│   ├── unit_tests/       # Unit tests
│   ├── system_tests/     # System tests
│   └── ...
├── docs/                 # Documentation
├── pyproject.toml        # Project configuration and dependency declarations
├── uv.lock               # Dependency lock file
└── pytest.ini            # pytest configuration
```

### Adding Dependencies

- **Runtime dependencies**: `uv add <package>`
- **Development dependencies** (testing, linting, etc.): `uv add --dev <package>`

For example:

```bash
uv add --dev pytest-cov pytest-asyncio
```

### Self-Verification Development Workflow

After modifying code, you need to build and verify according to the type of changes made:

#### Backend Code Changes

After modifying backend Python code, run the following commands to reinitialize and start the service:

```bash
uv run jiuwenavatar-init
uv run jiuwenavatar-start
```

#### Frontend Code Changes

After modifying frontend code, rebuild the frontend assets:

```bash
npm run build
```

#### TUI Changes

After modifying TUI (Terminal User Interface) related code, run the development mode for debugging:

```bash
npm run dev
```

## 5. Running Tests

> **Important**: Always use `uv run pytest` instead of running `pytest` directly to ensure that dependencies from the `uv`-managed virtual environment are used.

### Run All Unit Tests

```bash
uv run pytest tests/unit_tests/
```

### Run All System Tests

```bash
uv run pytest tests/system_tests/
```

### Run a Specific Test File

```bash
uv run pytest tests/unit_tests/agentserver/test_team_config_loader.py
```

### Troubleshooting

**Issue: `ModuleNotFoundError: No module named 'xxx'`**

Cause: The system `pytest` was run directly without using the `uv` environment. Solution:

```bash
# Wrong - uses system Python
pytest tests/

# Correct - runs through uv
uv run pytest tests/
```

**Issue: `error: unrecognized arguments: --cov=...`**

Cause: `pytest.ini` is configured with the `--cov` parameter, but `pytest-cov` is not installed in the current environment. Solution:

```bash
uv add --dev pytest-cov pytest-asyncio
```

**Issue: Tests fail unexpectedly due to state leakage between tests**

If a test passes when run individually but fails during a full test run, it is usually because a test file modifies `sys.modules` or global state at the module level, affecting the collection or execution of subsequent tests. You can use binary search to locate the issue:

```bash
# Runs successfully on its own
uv run pytest tests/unit_tests/xxx/test_foo.py

```

**Issue: How to install Node.js**

Frontend development requires Node.js. It is recommended to install via [nvm](https://github.com/nvm-sh/nvm) (Node Version Manager):

```bash
# Install nvm (macOS / Linux)
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash

# Reload shell configuration
source ~/.bashrc

# Install and use Node.js 18
nvm install 18
nvm use 18
```

Windows users can use [nvm-windows](https://github.com/coreybutler/nvm-windows) or download the installer directly from the [Node.js website](https://nodejs.org/).

After installation, verify the version:

```bash
node --version
# Expected output: v18.x.x or higher
```

## 6. Building Packages

The project supports two distribution formats: **Python wheel packages** (`.whl`) and **desktop executables** (`.exe` / `.dmg`).

### 6.1 Building Wheel Packages

The project contains two independent wheel packages:

| Package | Description | Config File |
|---------|-------------|-------------|
| `jiuwenavatar` | Backend service main package (includes Web frontend build artifacts) | `pyproject.toml` |
| `jiuwenavatar-tui` | TUI terminal interface sidecar package (includes Bun-compiled native binaries) | `packages/jiuwenavatar-tui/pyproject.toml` |

#### 6.1.1 Build All (Recommended)

Run from the project root directory:

```bash
# macOS / Linux
bash scripts/build.sh
```

The script will execute the following steps in order:
1. Build the Web frontend (runs `npm run build` in `jiuwenavatar/channels/web/frontend`)
2. Build the main package `jiuwenavatar.whl`
3. If `bun` is detected, continue to build the TUI native binary and `jiuwenavatar-tui.whl`

Artifacts are output to two directories:
- `./dist/jiuwenavatar-<version>-py3-none-any.whl` (main package)
- `./packages/jiuwenavatar-tui/dist/jiuwenavatar_tui-<version>-<platform>.whl` (TUI sidecar package)

### 6.2 Desktop EXE / DMG Packaging

The desktop version uses [PyInstaller](https://pyinstaller.org/) to package the Python application into a standalone executable.

#### 6.2.1 Prerequisites

- `uv` and `Node.js` installed
- Install development dependencies via `uv sync --extra dev` (includes PyInstaller)

#### 6.2.2 Windows Packaging (EXE)

**Using batch script (recommended):**

```cmd
scripts\build-exe.bat
```

**Using PowerShell script:**

```powershell
.\scripts\build-exe.ps1
```

Output: `dist\jiuwenavatar\jiuwenavatar.exe`

#### 6.2.3 macOS Packaging (DMG)

```bash
bash scripts/build-macos.sh
```

The script will execute the following steps in order:
1. Install Python dependencies (`uv sync --extra dev`)
2. Build the Web frontend (`npm run build`)
3. Package with PyInstaller to generate `JiuwenAvatar.app`
4. Create a DMG installer image using `hdiutil`

Output: `dist/JiuwenAvatar-<version>.dmg`
