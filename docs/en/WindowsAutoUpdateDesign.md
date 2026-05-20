# Windows Auto-Update Design

This document describes the minimum viable auto-update solution for JiuwenSwarm on Windows desktop. The goal is to prioritize stability over seamless upgrades.

## Scope

- Windows desktop edition only
- Automatic update check on startup
- Manual update check via the sidebar "Update" page
- Update source: GitHub Releases
- Download artifact: Inno Setup installer `jiuwenswarm-setup-<version>.exe`
- After download, an external helper script performs a silent install and restarts the application

## Out of Scope

- No incremental/delta updates
- No in-process self-replacement
- No macOS auto-install
- No version-skip, canary releases, or multi-channel distribution
- No forced updates

## Core Flow

1. After app launch, the frontend asynchronously calls `updater.check`
2. The backend requests the GitHub Releases API for the latest release
3. If a new version is found, it records the latest version, publish date, release notes, and installer download URL
4. The user clicks "Download Update" on the Update page
5. The backend downloads the installer to `%USERPROFILE%\\.jiuwenswarm\\.updates` in the background
6. After download completes, the frontend calls the pywebview API to trigger installation
7. The desktop process writes a temporary `cmd` helper script that waits for the current process to exit
8. The helper runs the Inno Setup installer silently, then restarts the application

## Update Source

Default GitHub Releases API:

```text
https://api.github.com/repos/{owner}/{repo}/releases/latest
```

Fields read from the release:

- `tag_name` — latest version number
- `body` — release notes
- `published_at` — publish date
- `assets[]` — the installer and optional sha256 file

## Configuration

Update settings are in the `updater` section of `config.yaml`:

```yaml
updater:
  enabled: true
  repo_owner: CharlieZhao95
  repo_name: jiuwenswarm
  release_api_url: ""
  asset_name_pattern: "jiuwenswarm-setup-{version}.exe"
  sha256_name_pattern: "jiuwenswarm-setup-{version}.exe.sha256"
  timeout_seconds: 20
```

## Backend API

Three WebSocket RPC methods are registered:

- `updater.get_status`
- `updater.check`
- `updater.download`

Status values:

- `idle`
- `checking`
- `up_to_date`
- `update_available`
- `downloading`
- `downloaded`
- `installing`
- `error`
- `unsupported`

## Installation Execution

To avoid replacing files while the main process is running, installation is not performed within the current process.

When the desktop process receives an install request from the frontend:

1. It generates a temporary `cmd` helper script
2. The helper waits for the desktop main process to exit
3. The helper runs the installer with:

```text
/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /CLOSEAPPLICATIONS
```

4. After installation completes, it restarts `jiuwenswarm.exe`