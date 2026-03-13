# JiuwenClaw 打包为独立 exe 指南

本文档说明如何使用 uv + PyInstaller 将 JiuwenClaw 打包成**无需安装 Python 或 Node.js** 的独立 exe 应用。

## 前置要求

- **uv**：项目使用的 Python 包管理器
- **Node.js**：仅用于**构建时**编译前端，最终 exe 不依赖 Node.js
- **Windows**：当前 spec 针对 Windows，如需 Linux/macOS 需调整

## 打包相关文件位置

打包配置与入口脚本统一放在 `Scripts/` 目录，便于维护：

| 文件 | 说明 |
|------|------|
| `Scripts/jiuwenclaw.spec` | PyInstaller 打包配置 |
| `Scripts/jiuwenclaw_exe_entry.py` | exe 入口脚本（子命令分发） |
| `Scripts/build-exe.ps1` | 一键打包脚本（PowerShell） |
| `Scripts/build-exe.bat` | 一键打包脚本（批处理） |

## 打包步骤

### 方式一：使用脚本（推荐）

在项目根目录执行：

```powershell
# PowerShell
.\Scripts\build-exe.ps1
```

或双击运行 `Scripts\build-exe.bat`。

脚本会自动完成：安装依赖 → 构建前端 → 执行 PyInstaller 打包。

### 方式二：手动执行

#### 1. 安装 uv 和依赖

```bash
# 若未安装 uv
# Windows (PowerShell): irm https://astral.sh/uv/install.ps1 | iex

# 进入项目目录
cd e:\Projects\jiuwenclaw_9980

# 安装项目依赖（含 PyInstaller 开发依赖）
uv sync --extra dev
```

#### 2. 构建前端

前端为 React 应用，需先构建为静态文件，打包进 exe：

```bash
cd jiuwenclaw/web
npm install
npm run build
cd ../..
```

构建完成后，`jiuwenclaw/web/dist` 下会有静态文件。

#### 3. 执行打包

```bash
uv run pyinstaller Scripts/jiuwenclaw.spec
```

成功后，exe 位于 `dist/jiuwenclaw.exe`。

## 使用打包后的 exe

### 首次使用

1. **初始化工作区**（首次必须执行）：
   ```bash
   jiuwenclaw.exe init
   ```
   会在 `~/.jiuwenclaw` 创建配置和工作区。

2. **编辑配置**：
   - 打开 `%USERPROFILE%\.jiuwenclaw\.env`
   - 填写 `API_KEY`、`MODEL_PROVIDER` 等

3. **启动应用**：
   ```bash
   jiuwenclaw.exe
   ```

4. 浏览器访问 `http://localhost:5173`（静态前端）或 `http://127.0.0.1:19000`（WebSocket，以实际配置为准）。

### 子命令

| 命令 | 说明 |
|------|------|
| `jiuwenclaw.exe` | 启动主应用 |
| `jiuwenclaw.exe init` | 初始化工作区（首次使用） |

## 技术说明

- **Python 运行时**：PyInstaller 将 Python 解释器及依赖打包进 exe，目标机器无需安装 Python。
- **Node.js**：前端在构建阶段用 Node 编译，运行时只使用静态文件，exe 不依赖 Node。
- **工作区路径**：与 pip 安装一致，使用 `~/.jiuwenclaw` 作为配置与工作区根目录。

## 常见问题

### 1. 打包失败：找不到 web/dist

先执行 `cd jiuwenclaw/web && npm run build`，确保 `jiuwenclaw/web/dist` 存在。

### 2. 运行 exe 报错 ModuleNotFoundError

在 `Scripts/jiuwenclaw.spec` 的 `hiddenimports` 中补充缺失模块，然后重新打包。

### 3. exe 体积过大

可在 `Scripts/jiuwenclaw.spec` 的 `excludes` 中排除未用模块，或使用 `--onedir` 模式（目录分发）代替单文件。

### 4. 杀毒软件误报

PyInstaller 生成的 exe 可能被误报，可尝试：
- 添加排除规则
- 使用代码签名（若有证书）
