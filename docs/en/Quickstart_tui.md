# Quick Start

> **⚠️ Version Sync**: This document should be kept in sync with [`docs/zh/Quickstart_tui.md`](../zh/Quickstart_tui.md). When updating one, please update the other.

JiuwenSwarm provides two installation methods: `pip install` or `install from source`.

## Prerequisites

- Download JiuwenSwarm code:
  ```bash
  git clone https://gitcode.com/openjiuwen/jiuwenswarm.git
  ```
- Environment dependencies:
  - Python: >=3.11, <3.14
  - Node.js: >=18.0.0 (only needed for building frontend from source or for browser-use functionality; 20 LTS recommended)

**Note: Users can choose any of the following installation methods based on their needs.**

## Method 1: pip Install

Suitable for users who manage their own Python environment. Follow these steps:

- Create a virtual environment & install JiuwenSwarm

  ```bash
  # Create a virtual environment named jiuwenswarm
  python -m venv jiuwenswarm

  # Activate the jiuwenswarm virtual environment on Windows
  jiuwenswarm\Scripts\activate

  # Activate the jiuwenswarm virtual environment on Mac
  source .venv/bin/activate

  # Install JiuwenSwarm
  pip install jiuwenswarm

  # Install JiuwenSwarm-tui
  pip install jiuwenswarm-tui
  ```

- Initialize & start JiuwenSwarm

  ```bash
  # Initialize JiuwenSwarm (first time setup)
  jiuwenswarm-init

  # Start JiuwenSwarm
  jiuwenswarm-start
  ```

- start JiuwenSwarm-tui

  ```bash
  # Start JiuwenSwarm
  jiuwenswarm-tui
  ```

  You can run the command above in **multiple terminals** against the same Gateway (default `ws://127.0.0.1:19001/tui`) for parallel sessions in separate TUI windows. See the **Multi-window TUI** section in [TUI Usage Guide (zh)](../zh/TUI使用指南.md#多窗口-tui).

## Method 2: Install from Source

Suitable for users who perform custom development or adaptation based on JiuwenSwarm.

### uv Installation

- Create a virtual environment with `uv`
  ```bash
  # Create a virtual environment with uv (supports any of 3.11, 3.12, 3.13)
  uv venv --python=3.11
  # or: uv venv --python=3.12
  # or: uv venv --python=3.13
  ```

- 激活 jiuwenswarm 虚拟环境
  ```bash
  # Activate the jiuwenswarm virtual environment on Windows
  jiuwenswarm\Scripts\activate

  # Activate the jiuwenswarm virtual environment on Mac
  source .venv/bin/activate
  ```

- Run uv sync

  Navigate to the project root directory `jiuwenswarm/` and run:
  ```bash
  uv sync
  ```

- Install frontend dependencies

  Navigate to the frontend directory `jiuwenswarm/channels/web/frontend` and install dependencies:
  ```bash
  cd jiuwenswarm/channels/web/frontend
  npm install
  ```

- Run frontend service

  Two methods are available for running the frontend service:

  - Static frontend service (suitable for production deployment)
    ```bash
    npm run build
    cd ../../
    uv run jiuwenswarm-init
    uv run jiuwenswarm-start
    ```

  - Dynamic frontend service (suitable for development and debugging)
    ```bash
    cd ../../
    uv run jiuwenswarm-init
    uv run jiuwenswarm-start dev
    ```

  After running, you can access the JiuwenSwarm web UI.

- Install TUI dependencies
  Open one new erminal，navigate to the TUI directory `jiuwenswarm/channels/tui/frontend` and install dependencies:
  ```bash
  cd jiuwenswarm/channels/tui/frontend
  npm install
  ```

- Start TUI

  ```bash
  npm run dev
  ```

### conda Installation

- Create a virtual environment with `conda`
  ```bash
  # Create a virtual environment with Anaconda (supports any of 3.11, 3.12, 3.13)
  conda create -n JiuwenSwarm python=3.11
  # or: conda create -n JiuwenSwarm python=3.12
  # or: conda create -n JiuwenSwarm python=3.13
  ```

- Install Python dependencies

  Navigate to the project root directory `jiuwenswarm/` and run:
  ```bash
  # Mode 1: Development installation (recommended, facilitates code modification)
  pip install -e .

  # Mode 2: Regular installation
  pip install .
  ```
  **Note:** This installation method relies on the project's installable package (pyproject.toml) and will install `jiuwenswarm` itself by default.

- Install frontend dependencies

  Navigate to the frontend directory `jiuwenswarm/channels/web/frontend` and install dependencies:
  ```bash
  cd jiuwenswarm/channels/web/frontend
  npm install
  ```

- Run frontend service

  Two methods are available for running the frontend service:

  - Static frontend service (suitable for production deployment)
    ```bash
    npm run build
    cd ../../
    jiuwenswarm-init
    jiuwenswarm-start
    ```

  - Dynamic frontend service (suitable for development and debugging)
    ```bash
    cd ../../
    # Start directly (without using uv run)
    jiuwenswarm-init
    jiuwenswarm-start dev
    ```

  After running, you can access the JiuwenSwarm web UI.

- Install TUI dependencies
  Open one new erminal，navigate to the TUI directory `jiuwenswarm/channels/tui/frontend` and install dependencies:
  ```bash
  cd jiuwenswarm/channels/tui/frontend
  npm install
  ```

- Start TUI

  ```bash
  npm run dev
  ```