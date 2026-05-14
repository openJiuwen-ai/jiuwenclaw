# JiuwenClaw installation guide

> **Important:** Finishing installation does not mean the app is ready to use. You must complete model configuration first. See [Configuration](#configuration) for model setup.

---

## Prerequisites

Before installing JiuwenClaw, make sure your system meets the following requirements:

| Dependency | Version | Notes |
|------------|---------|-------|
| OS | Windows 10/11, macOS 10.15+, Linux | Common desktop OS supported |
| Python | 3.10 – 3.12 | Python 3.11 recommended |
| Node.js | 18.x or newer | Used for the web UI |
| Git | Latest | Required for source install |

### Environment check

Run these commands in a terminal:

```bash
# Check Python
python --version

# Check Node.js
node --version

# Check Git
git --version
```

---

## First-time installation

### Option 1: Desktop installer (exe)

> To be filled in when a desktop installer is available. Prefer this method when offered.

---

### Option 2: pip install

#### 1. Installation steps

```bash
# Create a virtual environment (recommended)
python -m venv jiuwenclaw-env

# Activate the virtual environment
# Windows:
jiuwenclaw-env\Scripts\activate
# macOS/Linux:
source jiuwenclaw-env/bin/activate

# Install JiuwenClaw
pip install jiuwenclaw
```

#### 2. Use China mainland mirrors (recommended)

```bash
# Tsinghua mirror
pip install jiuwenclaw -i https://pypi.tuna.tsinghua.edu.cn/simple

# Aliyun mirror
pip install jiuwenclaw -i https://mirrors.aliyun.com/pypi/simple/
```

#### 3. First launch

```bash
# Initialize JiuwenClaw (first run)
jiuwenclaw-init
# Start JiuwenClaw
jiuwenclaw-start
```

After the first start, the app creates the config directory `~/.jiuwenclaw/`.

#### 4. Restarting the service

If you closed JiuwenClaw and want to run it again:

```bash
# Start again
jiuwenclaw-start
```

---

### Option 3: Install from source (uv)

#### 1. Install uv

```bash
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# macOS/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

#### 2. Clone and install

```bash
# Clone the repository
git clone https://gitcode.com/openJiuwen/jiuwenclaw.git

# Enter project directory
cd jiuwenclaw

# Create venv and install dependencies with uv
uv venv
uv pip install -e .
```

#### 3. Build the front end

> ⚠️ **Important:** With a source (editable) install you must build the front end manually, or startup will fail with `dist directory not found`.

```bash
# Enter front-end directory
cd jiuwenclaw/channels/web

# Install front-end dependencies
npm install

# Build
npm run build

# Copy build output into the user workspace
# Windows:
xcopy /E /I dist %USERPROFILE%\.jiuwenclaw\channels\web\frontend\dist
# macOS/Linux:
cp -r dist ~/.jiuwenclaw/channels/web/frontend/dist

# Back to repo root
cd ..
```

**Notes:**

- `pip install -e .` / `uv pip install -e .` is an editable install that points at your source tree.
- `web/dist` is ignored by `.gitignore` and is not shipped in the repo.
- You must build and copy artifacts to `~/.jiuwenclaw/channels/web/frontend/dist`.

#### 4. First launch

```bash
# Activate virtual environment
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

# Initialize JiuwenClaw (first run)
jiuwenclaw-init
# Start
jiuwenclaw-start
```

#### 5. Restarting the service

```bash
# After activating the virtual environment
jiuwenclaw-start
```

---

### Option 4: Install from source (conda)

#### 1. Check and install conda

**Check if conda is installed:**

```bash
conda --version
```

If a version is printed, conda is installed and you can skip installation.

**Install conda (if missing):**

Miniconda (lightweight) is recommended:

| System | How to install |
|--------|----------------|
| Windows | PowerShell commands below |
| macOS | `brew install --cask miniconda` or download the installer |
| Linux | Commands below |

**Install Miniconda on Windows (PowerShell):**

```powershell
# Download Miniconda installer
Invoke-WebRequest -Uri "https://repo.anaconda.com/miniconda/Miniconda3-latest-Windows-x86_64.exe" -OutFile "miniconda.exe"

# Silent install (default location)
start /wait "" .\Miniconda3-latest-Windows-x86_64.exe /InstallationType=[JustMe|AllUsers] /AddToPath=[0|1] /RegisterPython=[0|1] /S /D=<install_path>

# After install, open a new terminal and run conda --version. If it prints a version, install and PATH are OK.
```

**Install Miniconda on Linux:**

```bash
# Download and install Miniconda
curl -sSL https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -o miniconda.sh
bash miniconda.sh -b -p $HOME/miniconda3

# Add to PATH
export PATH="$HOME/miniconda3/bin:$PATH"

# Initialize conda (optional; takes effect after restarting the shell)
conda init
```

#### 2. Create the conda environment

```bash
# Create environment
conda create -n jiuwenclaw python=3.11

# Initialize conda (first time)
conda init
# After init, close the window and open a new session before activate

# Activate environment
conda activate jiuwenclaw
```

#### 3. Clone and install

```bash
# Clone the repository
git clone https://gitcode.com/openJiuwen/jiuwenclaw.git

# Enter project directory
cd jiuwenclaw

# Install dependencies
pip install -e .
```

#### 4. Build the front end

> ⚠️ **Important:** With a source (editable) install you must build the front end manually, or startup will fail with `dist directory not found`.

```bash
# Enter front-end directory
cd jiuwenclaw/channels/web

# Install front-end dependencies
npm install

# Build
npm run build

# Copy build output into the user workspace
# Windows:
xcopy /E /I dist %USERPROFILE%\.jiuwenclaw\channels\web\frontend\dist
# macOS/Linux:
cp -r dist ~/.jiuwenclaw/channels/web/frontend/dist

# Back to repo root
cd ..
```

**Notes:**

- `pip install -e .` is an editable install that points at your source tree.
- `web/dist` is ignored by `.gitignore` and is not shipped in the repo.
- You must build and copy artifacts to `~/.jiuwenclaw/channels/web/frontend/dist`.

#### 5. First launch

```bash
# Initialize JiuwenClaw (first run)
jiuwenclaw-init
# Start
jiuwenclaw-start
```

#### 6. Restarting the service

```bash
# Activate environment, then start
conda activate jiuwenclaw
jiuwenclaw-start
```

---

## Version upgrades

| Current range | Approach | Notes |
|---------------|----------|-------|
| Same major (e.g. 0.1.x → 0.1.y) | [Routine version upgrades](#routine-version-upgrades) | Upgrade directly; no backup required |
| Cross-major (e.g. 0.1.x → 0.2.x) | [Major version upgrades](#major-version-upgrades) | Back up data first |

---

### Routine version upgrades

#### pip install upgrade

```bash
# Activate your virtual environment
# Then upgrade
pip install --upgrade jiuwenclaw
```

#### Source install upgrade

```bash
# Enter project directory
cd jiuwenclaw

# Pull latest
git pull

# Reinstall
pip install -e .

# Rebuild the front end (when it changed)
cd jiuwenclaw/channels/web
npm install
npm run build

# Copy build output
# Windows:
xcopy /E /I dist %USERPROFILE%\.jiuwenclaw\channels\web\frontend\dist
# macOS/Linux:
cp -r dist ~/.jiuwenclaw/channels/web/frontend/dist

cd ..
```

---

### Major version upgrades

> ⚠️ Always back up your data before upgrading across major versions.

#### 1. Data backup

**Windows:**

```bash
# Back up the whole config and data directory
xcopy "%USERPROFILE%\.jiuwenclaw" "%USERPROFILE%\.jiuwenclaw_backup" /E /I

# Or with PowerShell (recommended)
Copy-Item -Path "$env:USERPROFILE\.jiuwenclaw" -Destination "$env:USERPROFILE\.jiuwenclaw_backup" -Recurse
```

**macOS/Linux:**

```bash
# Back up the whole config and data directory
cp -r ~/.jiuwenclaw ~/.jiuwenclaw_backup

# Or with rsync (recommended; preserves permissions)
rsync -av ~/.jiuwenclaw ~/.jiuwenclaw_backup
```

**What to back up:**

| Path | Description |
|------|-------------|
| `config/config.yaml` | Main config (models, API keys, etc.) |
| `config/.env` | Environment variables |
| `agent/memory/` | User memory data |
| `agent/home/` | Identity and task data |
| `agent/skills/` | Skills library (custom skills and config) |
| `agent/workspace/` | Workspace files |

#### 2. Perform the upgrade

Pick the steps that match how you installed JiuwenClaw:

##### pip install upgrade

Same as [Routine version upgrade – pip install upgrade](#pip-install-upgrade):

```bash
# Activate virtual environment
# Windows:
jiuwenclaw-env\Scripts\activate
# macOS/Linux:
source jiuwenclaw-env/bin/activate

# Upgrade
pip install --upgrade jiuwenclaw
```

##### Source install upgrade

Same as [Routine version upgrade – source install upgrade](#source-install-upgrade):

```bash
# Enter project directory
cd jiuwenclaw

# Pull latest
git pull

# Reinstall
pip install -e .
```

#### 3. Data migration

After upgrading, migrate data so config and stores match the new version.

##### Step 1: Review config changes

```bash
# View the new config template (source install)
cat docs/config_template.yaml

# Or read the changelog
# https://gitcode.com/openJiuwen/jiuwenclaw/blob/develop/docs/CHANGELOG.md
```

##### Step 2: Migrate configuration

1. **Compare old and new config shape**

   New releases may add or remove options. Check:
   - New required keys in `config.yaml`
   - New variables in `.env`
   - Deprecated or renamed keys

2. **Migrate by hand**

   ```bash
   # Back up the new default config
   cp ~/.jiuwenclaw/config/config.yaml ~/.jiuwenclaw/config/config.yaml.new

   # Restore from backup (use with care)
   # Prefer diff/merge in an editor instead of blind overwrite
   ```

3. **Common migration cases**

   | Case | What to do |
   |------|------------|
   | New model support | Add the new model block in `config.yaml` |
   | API endpoint change | Update URLs in `.env` |
   | Renamed keys | Map old → new using the changelog |
   | Removed keys | Delete obsolete entries |

##### Step 3: Migrate memory data

Memory is usually backward compatible; still verify:

```bash
# Inspect memory layout
ls ~/.jiuwenclaw/agent/memory/

# If something looks wrong, restore from backup
cp -r ~/.jiuwenclaw_backup/agent/memory/* ~/.jiuwenclaw/agent/memory/
```

##### Step 4: Verify migration

```bash
# Start the service
jiuwenclaw-start

# Watch logs for config errors
# Logs: ~/.jiuwenclaw/logs/
```

**Migration checklist:**

- [ ] Service starts cleanly
- [ ] Model config works (chat OK)
- [ ] Historical memory is readable
- [ ] Custom settings carried over
- [ ] No serious errors or warnings

---

## Configuration

After installation, configure models before normal use. Files live under:

- **Config directory:** `~/.jiuwenclaw/config/`
- **Main file:** `config.yaml`
- **Environment:** `.env`

For details see the [configuration guide](https://gitcode.com/openJiuwen/jiuwenclaw/blob/develop/docs/en/Configuration.md) ([Chinese](https://gitcode.com/openJiuwen/jiuwenclaw/blob/develop/docs/zh/%E9%85%8D%E7%BD%AE%E4%BF%A1%E6%81%AF.md)).


## FAQ

### Q: On start I see "Python version not supported"

Use Python 3.10, 3.11, or 3.12.

### Q: On start I see "Node.js not found"

Install Node.js 18.x or newer.

### Q: How do I check the installed version?

```bash
jiuwenclaw --version
```

### Q: How do I uninstall?

```bash
pip uninstall jiuwenclaw
```

---

## Related links

- **Web UI (page overview):** [Page-Overview.md](Page-Overview.md)（[简体中文版](../zh/页面概览.md)）
- **Repository:** https://gitcode.com/openJiuwen/jiuwenclaw
- **Issues:** https://gitcode.com/openJiuwen/jiuwenclaw/issues
- **Docs:** https://gitcode.com/openJiuwen/jiuwenclaw/tree/develop/docs

---

*Last updated: 2026-05-09*
