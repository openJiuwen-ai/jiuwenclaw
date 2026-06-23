# -*- mode: python ; coding: utf-8 -*-
r"""JiuwenSwarm PyInstaller 打包配置。

构建前请先：
1. 安装依赖: uv sync --extra dev
2. 构建前端: cd jiuwenswarm/channels/web/frontend && npm run build
3. 执行打包: .\scripts\build-exe.ps1  或  uv run pyinstaller scripts/jiuwenswarm.spec
"""

import glob
import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

block_cipher = None

SPEC_DIR = os.path.abspath(globals().get("SPECPATH", os.getcwd()))
project_root = os.path.abspath(os.path.join(SPEC_DIR, os.pardir))
symphony_root = os.path.join(project_root, "jiuwenswarm", "symphony")
if symphony_root not in sys.path:
    sys.path.insert(0, symphony_root)

DATA_FILE_PATTERNS = ["**/*.yaml", "**/*.yml", "**/*.json", "**/*.md"]
EXTENSION_DATA_FILE_PATTERNS = ["**/*.py", *DATA_FILE_PATTERNS]
DISPATCH_PACKAGE_ROOTS = ("indexing", "models", "orchestration", "retrieval", "shared")


def collect_tree_data_files(source_dir, target_dir, patterns):
    data_files = []
    for pattern in patterns:
        full_pattern = os.path.join(source_dir, pattern)
        for path in glob.glob(full_pattern, recursive=True):
            if not os.path.isfile(path):
                continue
            rel_dir = os.path.dirname(os.path.relpath(path, source_dir))
            dest_dir = os.path.normpath(os.path.join(target_dir, rel_dir))
            data_files.append((path, dest_dir))
    return data_files


def collect_tree_python_modules(source_dir, package_roots):
    modules = []
    for package_root in package_roots:
        package_dir = os.path.join(source_dir, *package_root.split("."))
        for path in glob.glob(os.path.join(package_dir, "**", "*.py"), recursive=True):
            rel_path = os.path.relpath(path, source_dir)
            module_name = os.path.splitext(rel_path)[0].replace(os.sep, ".")
            if module_name.endswith(".__init__"):
                module_name = module_name[: -len(".__init__")]
            modules.append(module_name)
    return sorted(set(modules))

try:
    webview_datas = collect_data_files("webview")
except Exception as exc:
    raise SystemExit(
        "错误: 当前虚拟环境缺少 pywebview，请先安装后再打包。"
        "例如: pip install pywebview 或 uv sync --extra dev"
    ) from exc

# 只显式打包当前平台会用到的 pywebview 模块，
# 避免 collect_submodules("webview") 把 Android/Kivy 等后端也扫描进来。
webview_hiddenimports = [
    "webview",
    "webview.guilib",
    "webview.http",
    "webview.errors",
    "webview.event",
    "webview.localization",
    "webview.menu",
    "webview.screen",
    "webview.util",
    "webview.window",
]
if sys.platform == "win32":
    webview_hiddenimports.extend([
        "webview.platforms.edgechromium",
        "webview.platforms.winforms",
    ])
elif sys.platform == "darwin":
    webview_hiddenimports.extend([
        "webview.platforms.cocoa",
    ])

# 检查前端是否已构建
web_dist = os.path.join(project_root, "jiuwenswarm", "channels", "web", "frontend", "dist")
if not os.path.isdir(web_dist) or not os.listdir(web_dist):
    raise SystemExit(
        "错误: 请先构建前端。执行: cd jiuwenswarm/channels/web/frontend && npm install && npm run build"
    )

# 数据文件：resources（含 agent 模板）、前端构建产物
datas = webview_datas + [
    (os.path.join(project_root, "jiuwenswarm", "resources"), "jiuwenswarm/resources"),
    (os.path.join(project_root, "jiuwenswarm", "channels", "web", "frontend", "dist"), "jiuwenswarm/channels/web/frontend/dist"),
]
datas += copy_metadata("fastmcp", recursive=True)
datas += copy_metadata("mcp", recursive=True)
datas += copy_metadata("openjiuwen", recursive=True)
datas += collect_data_files("openjiuwen", include_py_files=False)
datas += collect_data_files(
    "jiuwenswarm.extensions",
    include_py_files=True,
    includes=EXTENSION_DATA_FILE_PATTERNS,
)
datas += collect_data_files(
    "jiuwenswarm.symphony",
    include_py_files=False,
    includes=DATA_FILE_PATTERNS,
)
for package_root in DISPATCH_PACKAGE_ROOTS:
    datas += collect_tree_data_files(
        os.path.join(symphony_root, package_root),
        package_root,
        DATA_FILE_PATTERNS,
    )

# openjiuwen 使用动态导入，需要收集全部子模块
openjiuwen_submodules = collect_submodules("openjiuwen")
symphony_submodules = collect_submodules("jiuwenswarm.symphony")
dispatch_submodules = collect_tree_python_modules(symphony_root, DISPATCH_PACKAGE_ROOTS)

# 部分包需要显式声明隐藏导入
hiddenimports = webview_hiddenimports + [
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
    "webview",
    "jiuwenswarm.channels.web.app_web",  # 静态文件服务
    "jiuwenswarm.channels.web.desktop_app",  # 桌面入口
] + openjiuwen_submodules + symphony_submodules + dispatch_submodules

# 排除不需要的模块以减小体积（pandas 为 pymilvus/openjiuwen 所需，不可排除）
excludes = [
    "tkinter",
    "matplotlib",
    "scipy",
    "numpy.tests",
    # 测试框架
    "pytest",
    "pytest-asyncio",
    "_pytest",
    "py",
    "tox",
    "hypothesis",
    "mock",
    "coverage",
    "pytest-cov",
]

# 入口脚本位于 scripts 目录
entry_script = os.path.join(project_root, "scripts", "jiuwenswarm_exe_entry.py")

# 图标路径（Windows 用 .ico，macOS 用 .icns）
icon_path = os.path.join(
    project_root, "jiuwenswarm", "channels", "web", "frontend", "public",
    "logo.ico" if sys.platform == "win32" else "logo.icns",
)

a = Analysis(
    [entry_script],
    pathex=[project_root, symphony_root],
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
    [],
    name="jiuwenswarm",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    exclude_binaries=True,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_path,
    uac_admin=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="jiuwenswarm",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="JiuwenSwarm.app",
        icon=icon_path,
        bundle_identifier="com.jiuwenswarm.desktop",
        info_plist={
            "CFBundleName": "JiuwenSwarm",
            "CFBundleDisplayName": "JiuwenSwarm",
            "CFBundleShortVersionString": "0.2.2",
            "CFBundleVersion": "0.2.2",
            "NSHighResolutionCapable": "True",
        },
    )
