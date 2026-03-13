# -*- mode: python ; coding: utf-8 -*-
"""JiuwenClaw PyInstaller 打包配置。

构建前请先：
1. 安装依赖: uv sync --extra dev
2. 构建前端: cd jiuwenclaw/web && npm run build
3. 执行打包: .\Scripts\build-exe.ps1  或  uv run pyinstaller Scripts/jiuwenclaw.spec
"""

import os

block_cipher = None

# 项目根目录（PyInstaller 在项目根目录执行）
project_root = os.path.abspath(".")

# 检查前端是否已构建
web_dist = os.path.join(project_root, "jiuwenclaw", "web", "dist")
if not os.path.isdir(web_dist) or not os.listdir(web_dist):
    raise SystemExit(
        "错误: 请先构建前端。执行: cd jiuwenclaw/web && npm install && npm run build"
    )

# 数据文件：resources、workspace 模板、前端构建产物
datas = [
    (os.path.join(project_root, "jiuwenclaw", "resources"), "jiuwenclaw/resources"),
    (os.path.join(project_root, "jiuwenclaw", "web", "dist"), "jiuwenclaw/web/dist"),
    (os.path.join(project_root, "workspace"), "workspace"),
]

# 部分包需要显式声明隐藏导入
hiddenimports = [
    "pandas",  # pymilvus 依赖
    "tiktoken_ext",  # tiktoken 编码插件（cl100k_base 等）
    "tiktoken_ext.openai_public",
    "ruamel.yaml",
    "ruamel.yaml.reader",
    "ruamel.yaml.representer",
    "ruamel.yaml.nodes",
    "chromadb",
    "chromadb.config",
    "chromadb.telemetry",
    "openjiuwen",
    "psutil",
    "aiosqlite",
    "croniter",
    "websockets",
    "loguru",
    "dotenv",
    "jiuwenclaw.app_web",  # 静态文件服务
]

# 排除不需要的模块以减小体积（pandas 为 pymilvus/openjiuwen 所需，不可排除）
excludes = [
    "tkinter",
    "matplotlib",
    "scipy",
    "numpy.tests",
]

# 入口脚本位于 Scripts 目录
entry_script = os.path.join(project_root, "Scripts", "jiuwenclaw_exe_entry.py")

a = Analysis(
    [entry_script],
    pathex=[project_root],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="jiuwenclaw",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,  # 保留控制台便于查看日志
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
