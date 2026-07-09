# Quick Start

> **⚠️ Version Sync**: This document should be kept in sync with [`docs/zh/Quickstart_tui.md`](../zh/Quickstart_tui.md). When updating one, please update the other.

JiuwenAvatar provides two installation methods: `pip install` or `install from source`.

## Prerequisites

- Download JiuwenAvatar code:
  ```bash
  git clone https://gitcode.com/openjiuwen/jiuwenavatar.git
  ```
- Environment dependencies:
  - Python: >=3.11, <3.14
  - Node.js: >=18.0.0 (only needed for building frontend from source or for browser-use functionality; 20 LTS recommended)

**Note: Users can choose any of the following installation methods based on their needs.**

## Method 1: pip Install

Suitable for users who manage their own Python environment. Follow these steps:

- Create a virtual environment & install JiuwenAvatar

  ```bash
  # Create a virtual environment named jiuwenavatar
  python -m venv jiuwenavatar

  # Activate the jiuwenavatar virtual environment on Windows
  jiuwenavatar\Scripts\activate

  # Activate the jiuwenavatar virtual environment on Mac
  source .venv/bin/activate

  # Install JiuwenAvatar
  pip install jiuwenavatar

  # Install JiuwenAvatar-tui
  pip install jiuwenavatar-tui
  ```

- Initialize & start JiuwenAvatar

  ```bash
  # Initialize JiuwenAvatar (first time setup)
  jiuwenavatar-init

  # Start JiuwenAvatar
  jiuwenavatar-start
  ```

- start JiuwenAvatar-tui

  ```bash
  # Start JiuwenAvatar
  jiuwenavatar-tui
  ```

## Method 2: Install from Source

Suitable for users who perform custom development or adaptation based on JiuwenAvatar.

### uv Installation

- Create a virtual environment with `uv`
  ```bash
  # Create a virtual environment with uv (supports any of 3.11, 3.12, 3.13)
  uv venv --python=3.11
  # or: uv venv --python=3.12
  # or: uv venv --python=3.13
  ```

- 激活 jiuwenavatar 虚拟环境
  ```bash
  # Activate the jiuwenavatar virtual environment on Windows
  jiuwenavatar\Scripts\activate

  # Activate the jiuwenavatar virtual environment on Mac
  source .venv/bin/activate
  ```

- Run uv sync

  Navigate to the project root directory `jiuwenavatar/` and run:
  ```bash
  uv sync
  ```

- Install frontend dependencies

  Navigate to the frontend directory `jiuwenavatar/channels/web/frontend` and install dependencies:
  ```bash
  cd jiuwenavatar/channels/web/frontend
  npm install
  ```

- Run frontend service

  Two methods are available for running the frontend service:

  - Static frontend service (suitable for production deployment)
    ```bash
    npm run build
    cd ../../
    uv run jiuwenavatar-init
    uv run jiuwenavatar-start
    ```

  - Dynamic frontend service (suitable for development and debugging)
    ```bash
    cd ../../
    uv run jiuwenavatar-init
    uv run jiuwenavatar-start dev
    ```

  After running, you can access the JiuwenAvatar web UI.

- Install TUI dependencies
  Open one new erminal，navigate to the TUI directory `jiuwenavatar/channels/tui/frontend` and install dependencies:
  ```bash
  cd jiuwenavatar/channels/tui/frontend
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
  conda create -n JiuwenAvatar python=3.11
  # or: conda create -n JiuwenAvatar python=3.12
  # or: conda create -n JiuwenAvatar python=3.13
  ```

- Install Python dependencies

  Navigate to the project root directory `jiuwenavatar/` and run:
  ```bash
  # Mode 1: Development installation (recommended, facilitates code modification)
  pip install -e .

  # Mode 2: Regular installation
  pip install .
  ```
  **Note:** This installation method relies on the project's installable package (pyproject.toml) and will install `jiuwenavatar` itself by default.

- Install frontend dependencies

  Navigate to the frontend directory `jiuwenavatar/channels/web/frontend` and install dependencies:
  ```bash
  cd jiuwenavatar/channels/web/frontend
  npm install
  ```

- Run frontend service

  Two methods are available for running the frontend service:

  - Static frontend service (suitable for production deployment)
    ```bash
    npm run build
    cd ../../
    jiuwenavatar-init
    jiuwenavatar-start
    ```

  - Dynamic frontend service (suitable for development and debugging)
    ```bash
    cd ../../
    # Start directly (without using uv run)
    jiuwenavatar-init
    jiuwenavatar-start dev
    ```

  After running, you can access the JiuwenAvatar web UI.

- Install TUI dependencies
  Open one new erminal，navigate to the TUI directory `jiuwenavatar/channels/tui/frontend` and install dependencies:
  ```bash
  cd jiuwenavatar/channels/tui/frontend
  npm install
  ```

- Start TUI

  ```bash
  npm run dev
  ```