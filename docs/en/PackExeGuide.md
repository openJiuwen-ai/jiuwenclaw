# Desktop packaging (Windows & macOS)

This guide explains how to build a desktop app with **uv**, **PyInstaller**, and **pywebview**. Supported outputs: Windows **`onedir`** layout (for Inno Setup) and macOS **`.app` + `.dmg`**.

## Prerequisites

- **uv**: Python package manager used by the project
- **Node.js**: **Build-time only** for the web UI; the shipped app does not require Node at runtime
- **Windows**: `onedir` output for Inno Setup installers
- **macOS**: `.app` bundle and `.dmg`

## Files

| Path | Role |
|------|------|
| `scripts/jiuwenavatar.spec` | PyInstaller spec |
| `scripts/jiuwenavatar_exe_entry.py` | Exe entry (desktop mode + subcommands) |
| `jiuwenavatar/channels/desktop/desktop_app.py` | pywebview window and local server |
| `scripts/build-exe.ps1` | One-shot build (PowerShell) |
| `scripts/build-exe.bat` | One-shot build (batch) |
| `scripts/build-macos.sh` | macOS `.app` + `.dmg` |

## Windows

### Option A: scripts (recommended)

From the repo root:

```powershell
.\scripts\build-exe.ps1
```

Or double-click `scripts\build-exe.bat`.

The script installs deps, builds the frontend, and runs PyInstaller.

### Option B: manual

#### 1. uv and deps

```bash
# Install uv if needed (PowerShell):
# irm https://astral.sh/uv/install.ps1 | iex

cd <your-repo-root>
uv sync --extra dev
```

#### 2. Build the web UI

```bash
cd jiuwenavatar/channels/web/frontend
npm install
npm run build
cd ../..
```

Static files land in `jiuwenavatar/channels/web/frontend/dist`.

#### 3. PyInstaller

```bash
uv run pyinstaller scripts/jiuwenavatar.spec
```

Output: `dist/jiuwenavatar/`, main binary `dist/jiuwenavatar/jiuwenavatar.exe`.

## Using the Windows build

### First run

1. **Initialize** (required once):

   ```bash
   jiuwenavatar.exe init
   ```

   Creates `~/.jiuwenavatar` config and workspace.

2. **Configure**: edit `%USERPROFILE%\.jiuwenavatar\.env` (`API_KEY`, `MODEL_PROVIDER`, …).

3. **Start**:

   ```bash
   jiuwenavatar.exe
   ```

   Starts backend + static UI in a borderless pywebview window (default `http://127.0.0.1:29173`); you usually do not open a separate browser.

## Inno Setup notes

- Package the whole `dist/jiuwenavatar/` directory.
- Entry point: `dist/jiuwenavatar/jiuwenavatar.exe`.
- Run `jiuwenavatar.exe init` from the installer finish page if needed.
- User data lives under `%USERPROFILE%\.jiuwenavatar` — do not delete on uninstall by default.
- Share one `.ico` between `jiuwenavatar.spec` and Inno Setup if you add an icon.

### Subcommands

| Command | Role |
|---------|------|
| `jiuwenavatar.exe` | Start desktop app |
| `jiuwenavatar.exe init` | Initialize workspace |

## macOS

```bash
chmod +x scripts/build-macos.sh
./scripts/build-macos.sh
```

Produces `dist/JiuwenAvatar.app` and `dist/JiuwenAvatar-<version>.dmg`.

- Open the `.app` or mount the `.dmg` and drag to **Applications**.
- Not codesigned/notarized — fine for local testing; for distribution add `.icns`, signing, and notarization.
- First launch may require **Open** from the context menu (Gatekeeper).

## Technical notes

- **Python**: Bundled by PyInstaller; end users do not install Python.
- **pywebview**: Loads local `http://127.0.0.1:29173`.
- **Node**: Only for building the React app; runtime uses static files.
- **Workspace**: Same as pip install — `~/.jiuwenavatar`.
- **Inno**: Ship the full `dist/jiuwenavatar/` tree, not a single exe only.
- **DMG**: Script may include an **Applications** shortcut for drag install.

## Troubleshooting

### Missing `web/dist`

Run `cd jiuwenavatar/channels/web/frontend && npm run build`.

### `ModuleNotFoundError` at runtime

Add missing modules to `hiddenimports` in `scripts/jiuwenavatar.spec` and rebuild.

### Large bundle

`onedir` is intentional. Trim further via `excludes` in the spec.

### Antivirus false positives

Add exclusions or sign the binary if you have a certificate.
